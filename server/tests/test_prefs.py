"""Interface preferences."""

from __future__ import annotations

import pytest


def test_defaults_when_unset(client):
    body = client.get("/api/prefs").json()
    assert body == {"palette": "warm", "appearance": "auto", "view": "details"}


def test_update_persists(client):
    client.put("/api/prefs", json={"palette": "studio"})
    assert client.get("/api/prefs").json()["palette"] == "studio"


def test_partial_update_preserves_other_keys(client):
    """A client that only knows one key must not wipe the rest."""
    client.put("/api/prefs", json={"palette": "daylight", "view": "tiles-large"})
    client.put("/api/prefs", json={"appearance": "dark"})

    body = client.get("/api/prefs").json()
    assert body == {"palette": "daylight", "appearance": "dark", "view": "tiles-large"}


@pytest.mark.parametrize(
    "patch",
    [
        {"palette": "chartreuse"},
        {"appearance": "sepia"},
        {"view": "carousel"},
    ],
)
def test_invalid_values_rejected(client, patch):
    assert client.put("/api/prefs", json=patch).status_code == 422


def test_unknown_keys_ignored(client):
    """The blob is written from the client, so it must not accept arbitrary data."""
    client.put("/api/prefs", json={"palette": "warm", "evil": {"a": 1}})
    assert "evil" not in client.get("/api/prefs").json()


@pytest.mark.parametrize("view", ["details", "columns", "tiles-small", "tiles-large"])
def test_all_view_modes_accepted(client, view):
    assert client.put("/api/prefs", json={"view": view}).json()["view"] == view


@pytest.mark.parametrize("palette", ["warm", "studio", "daylight"])
def test_all_palettes_accepted(client, palette):
    assert client.put("/api/prefs", json={"palette": palette}).json()["palette"] == palette
