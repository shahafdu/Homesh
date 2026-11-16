"""The converter container: its app, and the emptiness of its compose service.

The app is tested with the function that runs LibreOffice replaced, since
LibreOffice is only in the converter's image and not in the one the suite runs
in. One test does run a real child process, to show that a conversion that
overruns is killed together with everything it started.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import converter, docformats

PDF = b"%PDF-1.4\n% a stand-in\n"


@pytest.fixture
def conv(monkeypatch):
    """The app, with soffice replaced by something that records what it was given."""
    seen: dict = {}

    async def fake(source: Path, out_dir: Path, profile: Path) -> str:
        seen["source"] = source
        seen["data"] = source.read_bytes()
        seen["work"] = source.parent
        outcome = seen.get("outcome", "ok")
        if outcome == "ok":
            (out_dir / "input.pdf").write_bytes(PDF)
        return outcome

    monkeypatch.setattr(converter, "run_soffice", fake)
    with TestClient(converter.app) as c:
        yield c, seen


def test_health(conv):
    client, _ = conv
    assert client.get("/health").json() == {"ok": True}


def test_a_good_conversion_returns_the_pdf(conv):
    client, seen = conv
    r = client.post("/convert", params={"ext": "docx"}, content=b"a document")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == PDF
    assert seen["data"] == b"a document"
    assert not seen["work"].exists(), "the temporary directory was left behind"


@pytest.mark.parametrize("ext", ["DOCX", "odt", "csv"])
def test_the_input_is_always_named_input_dot_ext(conv, ext):
    client, seen = conv
    assert client.post("/convert", params={"ext": ext}, content=b"x").status_code == 200
    assert seen["source"].name == f"input.{ext.lower()}"


@pytest.mark.parametrize("ext", ["exe", "pdf", "../docx", "docx/../../x", ""])
def test_an_unknown_extension_is_refused(conv, ext):
    client, seen = conv
    r = client.post("/convert", params={"ext": ext}, content=b"x")
    assert r.status_code in (415, 422)
    assert "source" not in seen, "something was written for a refused format"


def test_an_oversized_body_is_refused(conv, monkeypatch):
    client, seen = conv
    monkeypatch.setattr(converter, "MAX_INPUT", 10)
    r = client.post("/convert", params={"ext": "docx"}, content=b"x" * 11)
    assert r.status_code == 413
    assert "source" not in seen


def test_an_oversized_body_without_a_length_is_refused(conv, monkeypatch):
    """Counted while reading, not only taken from the header."""
    client, seen = conv
    monkeypatch.setattr(converter, "MAX_INPUT", 10)

    def chunks():
        for _ in range(5):
            yield b"xxxx"

    r = client.post("/convert", params={"ext": "docx"}, content=chunks())
    assert r.status_code == 413
    assert "source" not in seen


@pytest.mark.parametrize("outcome", ["timeout", "failed"])
def test_a_failed_conversion_is_422_and_leaves_nothing(conv, outcome):
    client, seen = conv
    seen["outcome"] = outcome
    r = client.post("/convert", params={"ext": "docx"}, content=b"x")
    assert r.status_code == 422
    assert r.json()["detail"] == "could not convert this document"
    assert not seen["work"].exists()


def test_a_conversion_that_produces_nothing_is_422(conv, monkeypatch):
    client, seen = conv

    async def nothing(source: Path, out_dir: Path, profile: Path) -> str:
        seen["work"] = source.parent
        return "ok"

    monkeypatch.setattr(converter, "run_soffice", nothing)
    assert client.post("/convert", params={"ext": "docx"}, content=b"x").status_code == 422
    assert not seen["work"].exists()


def test_a_waiter_gives_up_with_503(conv, monkeypatch):
    client, seen = conv
    monkeypatch.setattr(converter, "WAIT_FOR_TURN", 0.05)
    busy = asyncio.Lock()
    asyncio.run(busy.acquire())  # uncontended, so no loop is bound to it
    monkeypatch.setattr(converter, "_turn", busy)
    r = client.post("/convert", params={"ext": "docx"}, content=b"x")
    assert r.status_code == 503
    assert "source" not in seen


@pytest.mark.skipif(not hasattr(os, "killpg") or not Path("/proc").is_dir(),
                    reason="process groups as Linux has them")
async def test_an_overrun_kills_the_whole_process_group(tmp_path, monkeypatch):
    """A document that has taken LibreOffice over may have started something.

    So the group is killed, not only the process that was started: the
    background child here must not outlive the conversion.
    """
    pidfile = tmp_path / "child.pid"
    monkeypatch.setattr(converter, "CONVERT_TIMEOUT", 0.5)
    monkeypatch.setattr(
        converter, "_command",
        lambda source, out_dir, profile: [
            "sh", "-c", f'sleep 30 & echo $! > "{pidfile}"; wait',
        ],
    )
    started = time.monotonic()
    outcome = await converter.run_soffice(tmp_path / "input.docx", tmp_path, tmp_path)
    assert outcome == "timeout"
    assert time.monotonic() - started < 10

    child = int(pidfile.read_text().strip())
    stat = Path(f"/proc/{child}/stat")
    for _ in range(40):
        # Gone, or a zombie waiting to be reaped: either way, not running.
        if not stat.exists() or stat.read_text().split(") ", 1)[1].startswith("Z"):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("a process started by the conversion survived it")


async def test_the_child_gets_only_the_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MASTER_KEY", "must-not-reach-the-child")
    out = tmp_path / "env.txt"
    monkeypatch.setattr(
        converter, "_command",
        lambda source, out_dir, profile: ["sh", "-c", f'env > "{out}"'],
    )
    assert await converter.run_soffice(tmp_path / "input.docx", tmp_path, tmp_path) == "ok"
    text = out.read_text()
    assert "MASTER_KEY" not in text
    assert f"HOME={tmp_path}" in text


def test_the_server_and_the_converter_agree_on_the_formats():
    from app import documents

    assert documents.convertible("DOCX")
    assert not documents.convertible("exe")
    assert docformats.CONVERTIBLE is converter.CONVERTIBLE


# -- The compose service -----------------------------------------------------
#
# This is the property the converter exists for. A later edit that hands it the
# server's environment, a volume or a route out would undo it without anything
# else noticing, so it is asserted here.


def _repo() -> Path:
    mounted = os.environ.get("HOMESH_REPO")
    return Path(mounted) if mounted else Path(__file__).resolve().parents[2]


@pytest.fixture(params=["docker-compose.yml", "docker-compose.standby.yml"])
def compose(request) -> dict:
    path = _repo() / request.param
    assert path.is_file(), f"{path} is not visible to the suite"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_converter_is_given_nothing(compose):
    svc = compose["services"]["converter"]
    assert "env_file" not in svc
    assert svc.get("environment") in ({}, [], None) and "environment" in svc
    assert "volumes" not in svc
    assert "ports" not in svc
    assert svc["networks"] == ["convert"]
    assert compose["networks"]["convert"]["internal"] is True
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["build"]["target"] == "converter"
    for limit in ("pids_limit", "mem_limit", "cpus"):
        assert limit in svc


def test_only_the_server_reaches_the_converter(compose):
    on_convert = sorted(
        name for name, svc in compose["services"].items()
        if "convert" in (svc.get("networks") or [])
    )
    assert on_convert in (["api", "converter"], ["api", "converter", "worker"])
    assert compose["services"]["api"]["build"]["target"] == "server"
