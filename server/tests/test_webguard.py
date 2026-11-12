"""Headers, cross-origin writes and the access log.

A page beside Homesh on the same host is the same *site* to a browser, so the
session cookie travels with its requests. These are about what such a page can
and cannot make Homesh do.
"""

from __future__ import annotations

import logging

from app.webguard import RedactQueries


class TestWritesFromAnotherPage:
    def test_a_write_from_another_port_on_the_same_host_is_refused(self, client):
        r = client.post(
            "/api/backups",
            headers={"Origin": "https://testserver:9101", "Host": "testserver"},
        )
        assert r.status_code == 403

    def test_a_write_from_another_site_is_refused(self, client):
        r = client.post("/api/backups", headers={"Origin": "https://elsewhere.example"})
        assert r.status_code == 403

    def test_an_opaque_origin_is_refused(self, client):
        assert client.post("/api/backups", headers={"Origin": "null"}).status_code == 403

    def test_a_write_from_our_own_page_is_accepted(self, client):
        r = client.post("/api/backups", headers={"Origin": "http://testserver"})
        assert r.status_code != 403

    def test_the_tailnet_address_behind_its_proxy_is_ours(self, client):
        """TLS ends at the proxy, so the page says https and the server sees http."""
        r = client.post(
            "/api/backups",
            headers={"Origin": "https://homesh.example.ts.net", "Host": "homesh.example.ts.net"},
        )
        assert r.status_code != 403

    def test_a_caller_that_names_no_origin_is_not_a_browser_being_tricked(self, client):
        assert client.post("/api/backups").status_code != 403

    def test_reading_is_never_refused_for_its_origin(self, client):
        r = client.get("/api/health", headers={"Origin": "https://elsewhere.example"})
        assert r.status_code == 200


class TestHeaders:
    def test_every_response_says_how_it_may_be_framed_and_sniffed(self, anon_client):
        r = anon_client.get("/api/health")
        assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "SAMEORIGIN"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "same-origin"


class TestTheAccessLog:
    def test_a_credential_in_the_query_never_reaches_the_log(self):
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 0,
            '%s - "%s %s HTTP/%s" %d',
            ("192.0.2.5:1234", "GET", "/api/renderers/ws?token=secret", "1.1", 101),
            None,
        )
        assert RedactQueries().filter(record)
        assert "secret" not in record.getMessage()
        assert "/api/renderers/ws" in record.getMessage()
