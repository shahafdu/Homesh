"""Office documents shown as PDFs, rendered by the converter container.

The converter is replaced here by a stand-in for app.convertclient: what is
being tested is the server's side -- the cache, and what a person is told when
the converter is not there -- not LibreOffice.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text

from app import convertclient, documents
from app.scanner import scan_source
from app.sources.local import LocalConnector

PDF = b"%PDF-1.4\n% a stand-in\n"


@pytest.fixture
def a_document(db, tmp_path):
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "report.docx").write_bytes(b"the document's own bytes")
    prefix = f"/local/test{uuid.uuid4().hex[:6]}"
    with db.begin() as conn:
        sid = conn.execute(
            text(
                "INSERT INTO sources (kind, name, mount_prefix, remote_id, audience) "
                "VALUES ('local', 'Test', :p, :root, 'everyone') RETURNING id"
            ),
            {"p": prefix, "root": str(tmp_path)},
        ).scalar_one()
    scan_source(sid, LocalConnector(tmp_path))
    with db.connect() as conn:
        return conn.execute(
            text(
                "SELECT i.id FROM items i JOIN replicas r ON r.item_id = i.id "
                "WHERE r.filename = 'report.docx'"
            )
        ).scalar_one()


@pytest.fixture
def converter(monkeypatch):
    calls: list[tuple[bytes, str]] = []
    behaviour: dict = {"raise": None}

    async def fake(data: bytes, ext: str) -> bytes:
        calls.append((data, ext))
        if behaviour["raise"] is not None:
            raise behaviour["raise"]
        return PDF

    monkeypatch.setattr(convertclient, "to_pdf", fake)
    return calls, behaviour


def test_the_pdf_is_converted_once_and_cached(client, a_document, converter):
    calls, _ = converter
    r = client.get(f"/api/documents/{a_document}")
    assert r.status_code == 200
    assert r.content == PDF
    assert calls == [(b"the document's own bytes", "docx")]

    cached = list(documents._cache_dir().glob("*.pdf"))
    assert any(p.read_bytes() == PDF for p in cached)
    assert not list(documents._cache_dir().glob("*.part")), "a partial file was left"

    again = client.get(f"/api/documents/{a_document}")
    assert again.status_code == 200 and again.content == PDF
    assert len(calls) == 1, "a cached document was converted again"


def test_an_unreachable_converter_is_a_sentence(client, a_document, converter):
    _, behaviour = converter
    behaviour["raise"] = convertclient.ConverterUnavailable("ConnectError")
    r = client.get(f"/api/documents/{a_document}")
    assert r.status_code == 503
    assert r.json()["detail"] == "Documents can't be shown right now. Try again in a minute."


def test_a_document_that_cannot_be_rendered(client, a_document, converter):
    _, behaviour = converter
    behaviour["raise"] = convertclient.NotConvertible("422")
    r = client.get(f"/api/documents/{a_document}")
    assert r.status_code == 502
    assert r.json()["detail"] == "that document could not be converted"


def test_a_document_too_large_is_not_sent(client, a_document, converter, monkeypatch):
    calls, _ = converter
    monkeypatch.setattr(documents, "MAX_SOURCE_BYTES", 4)
    r = client.get(f"/api/documents/{a_document}")
    assert r.status_code == 413
    assert calls == []


def test_the_server_no_longer_runs_libreoffice():
    """There is no fallback: a fallback would put LibreOffice back beside the keys."""
    assert not hasattr(documents, "_run_soffice")


# -- The client --------------------------------------------------------------


@pytest.fixture
def answering(monkeypatch):
    def use(handler):
        monkeypatch.setattr(convertclient, "_transport", httpx.MockTransport(handler))
    return use


async def test_the_client_returns_the_pdf(answering):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ext"] = request.url.params["ext"]
        seen["body"] = request.content
        return httpx.Response(200, content=PDF)

    answering(handler)
    assert await convertclient.to_pdf(b"doc", "docx") == PDF
    assert seen == {"ext": "docx", "body": b"doc"}


@pytest.mark.parametrize("code", [413, 415, 422])
async def test_a_refusal_is_not_convertible(answering, code):
    answering(lambda request: httpx.Response(code))
    with pytest.raises(convertclient.NotConvertible):
        await convertclient.to_pdf(b"doc", "docx")


async def test_something_that_is_not_a_pdf_is_not_passed_on(answering):
    answering(lambda request: httpx.Response(200, content=b"<html>"))
    with pytest.raises(convertclient.NotConvertible):
        await convertclient.to_pdf(b"doc", "docx")


@pytest.mark.parametrize("code", [500, 503])
async def test_a_busy_or_broken_converter_is_unavailable(answering, code):
    answering(lambda request: httpx.Response(code))
    with pytest.raises(convertclient.ConverterUnavailable):
        await convertclient.to_pdf(b"doc", "docx")


async def test_an_unreachable_converter_is_unavailable(answering):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    answering(handler)
    with pytest.raises(convertclient.ConverterUnavailable):
        await convertclient.to_pdf(b"doc", "docx")
