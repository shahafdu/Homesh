"""Interface preferences."""

from __future__ import annotations

import pytest


def test_defaults_when_unset(client):
    body = client.get("/api/prefs").json()
    assert body == {
        "palette": "warm",
        "appearance": "auto",
        "view": "details",
        # Keeping to the kind you opened, which is what it did before there was
        # a choice about it.
        "viewer_scope": "kind",
    }


def test_update_persists(client):
    client.put("/api/prefs", json={"palette": "studio"})
    assert client.get("/api/prefs").json()["palette"] == "studio"


def test_partial_update_preserves_other_keys(client):
    """A client that only knows one key must not wipe the rest."""
    client.put("/api/prefs", json={"palette": "daylight", "view": "tiles-large"})
    client.put("/api/prefs", json={"appearance": "dark"})

    body = client.get("/api/prefs").json()
    # Item by item rather than a whole-dict comparison, so adding a preference
    # does not fail a test about something else entirely.
    assert body["palette"] == "daylight"
    assert body["appearance"] == "dark"
    assert body["view"] == "tiles-large"
    assert body["viewer_scope"] == "kind", "untouched keys keep their default"


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


@pytest.mark.parametrize("scope", ["kind", "all"])
def test_viewer_scope_round_trips(client, scope):
    """What the viewer's arrows move through, remembered per account.

    It belongs on the account rather than in the browser: somebody who prefers
    to walk a whole folder prefers it on the television as well as the phone.
    """
    client.put("/api/prefs", json={"viewer_scope": scope})
    assert client.get("/api/prefs").json()["viewer_scope"] == scope


def test_an_invented_scope_is_refused(client):
    assert client.put("/api/prefs", json={"viewer_scope": "everything"}).status_code == 422
