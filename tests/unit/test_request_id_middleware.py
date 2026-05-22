"""Tests for the request-id middleware and its contextvar plumbing."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from whisper_ui.core.logging_setup import current_request_id, current_user_id
from whisper_ui.web.middleware.request_id import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    _normalise_request_id,
)

_HEX_8_PATTERN = re.compile(r"^[0-9a-f]{8}$")


def _make_app() -> tuple[FastAPI, dict]:
    """Build a tiny FastAPI app whose handler captures the live contextvars."""
    captured: dict = {}
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo")
    def echo():
        captured["request_id"] = current_request_id()
        captured["user_id"] = current_user_id()
        return {"ok": True}

    return app, captured


def test_generates_request_id_when_header_missing():
    app, captured = _make_app()
    client = TestClient(app)

    response = client.get("/echo")

    assert response.status_code == 200
    assert _HEX_8_PATTERN.match(response.headers[REQUEST_ID_HEADER])
    assert captured["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_echoes_valid_inbound_request_id():
    app, captured = _make_app()
    client = TestClient(app)

    response = client.get("/echo", headers={REQUEST_ID_HEADER: "deadbeefcafe"})

    assert response.headers[REQUEST_ID_HEADER] == "deadbeefcafe"
    assert captured["request_id"] == "deadbeefcafe"


def test_lowercases_uppercase_hex_request_id():
    app, captured = _make_app()
    client = TestClient(app)

    response = client.get("/echo", headers={REQUEST_ID_HEADER: "ABCDEF12"})

    assert response.headers[REQUEST_ID_HEADER] == "abcdef12"
    assert captured["request_id"] == "abcdef12"


@pytest.mark.parametrize(
    "bad_value",
    [
        "short",  # < 8 chars
        "x" * 8,  # not hex
        "abc-def-12",  # contains dash
        "a" * 65,  # too long
        "../etc/passwd",  # path-traversal style
        " ",  # whitespace
    ],
)
def test_generates_new_id_when_inbound_header_invalid(bad_value):
    app, _captured = _make_app()
    client = TestClient(app)

    response = client.get("/echo", headers={REQUEST_ID_HEADER: bad_value})

    assert _HEX_8_PATTERN.match(response.headers[REQUEST_ID_HEADER])
    assert response.headers[REQUEST_ID_HEADER] != bad_value


def test_user_id_defaults_to_dash_before_auth_runs():
    app, captured = _make_app()
    client = TestClient(app)

    client.get("/echo")

    # Without AuthMiddleware in the stack the user var stays at the default.
    assert captured["user_id"] == "-"


def test_response_always_carries_request_id_header():
    app, _ = _make_app()
    client = TestClient(app)

    for _ in range(3):
        response = client.get("/echo")
        assert REQUEST_ID_HEADER in response.headers


def test_context_var_resets_after_request():
    """The contextvar must not leak past the request boundary."""
    app, _ = _make_app()
    client = TestClient(app)

    client.get("/echo", headers={REQUEST_ID_HEADER: "feedfeedfeed"})

    # After the request finishes the contextvar should fall back to '-'.
    assert current_request_id() == "-"


def test_concurrent_requests_get_distinct_ids():
    """Two sequential requests must end up with different generated ids."""
    app, _captured = _make_app()
    client = TestClient(app)

    first = client.get("/echo")
    second = client.get("/echo")

    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


def test_normalise_request_id_helpers():
    assert _normalise_request_id("12345678") == "12345678"
    assert _normalise_request_id("DEADBEEF") == "deadbeef"
    assert _normalise_request_id(None) != ""
    assert len(_normalise_request_id("bad!")) == 8


def _make_app_with_settings(*, trust_proxy_headers: bool):
    """Build a minimal app whose settings carries the trust_proxy_headers
    flag the way create_app() would expose it (via app.state.settings).
    """
    from types import SimpleNamespace

    app = FastAPI()
    app.state.settings = SimpleNamespace(trust_proxy_headers=trust_proxy_headers)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    def ok():
        return {"ok": True}

    return app


def test_client_ip_uses_request_client_when_proxy_headers_not_trusted(caplog):
    """Default deployment: XFF must be ignored even if a hostile client
    sends it. The access log keeps request.client.host so spoofing the
    forensic IP requires bypassing the connection layer.
    """
    import logging

    from whisper_ui.core.logging_setup import RequestContextFilter

    app = _make_app_with_settings(trust_proxy_headers=False)
    client = TestClient(app)
    caplog.handler.addFilter(RequestContextFilter())
    try:
        with caplog.at_level(logging.INFO):
            client.get("/ok", headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
        access = next(r for r in caplog.records if r.name == "whisper_ui.web.access")
        assert "ip=testclient" in access.getMessage()
        assert "9.9.9.9" not in access.getMessage()
    finally:
        caplog.handler.removeFilter(caplog.handler.filters[-1])


def test_client_ip_uses_leftmost_xff_when_proxy_headers_trusted(caplog):
    """Reverse-proxy deployment opted in: XFF left-most wins so the log
    records the original client, not the proxy's inner-network IP.
    """
    import logging

    from whisper_ui.core.logging_setup import RequestContextFilter

    app = _make_app_with_settings(trust_proxy_headers=True)
    client = TestClient(app)
    caplog.handler.addFilter(RequestContextFilter())
    try:
        with caplog.at_level(logging.INFO):
            client.get("/ok", headers={"X-Forwarded-For": "9.9.9.9, 10.0.0.1"})
        access = next(r for r in caplog.records if r.name == "whisper_ui.web.access")
        assert "ip=9.9.9.9" in access.getMessage()
    finally:
        caplog.handler.removeFilter(caplog.handler.filters[-1])


def test_client_ip_falls_back_to_request_client_when_xff_absent(caplog):
    """trust_proxy_headers=True but the proxy didn't add the header
    (e.g. internal monitoring probe): fall back to request.client.host
    rather than emitting a misleading '-' default.
    """
    import logging

    from whisper_ui.core.logging_setup import RequestContextFilter

    app = _make_app_with_settings(trust_proxy_headers=True)
    client = TestClient(app)
    caplog.handler.addFilter(RequestContextFilter())
    try:
        with caplog.at_level(logging.INFO):
            client.get("/ok")
        access = next(r for r in caplog.records if r.name == "whisper_ui.web.access")
        assert "ip=testclient" in access.getMessage()
    finally:
        caplog.handler.removeFilter(caplog.handler.filters[-1])
