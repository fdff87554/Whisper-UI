"""End-to-end tracing test: a single HTTP request produces an access log
line tagged with the same request_id that downstream handler logs use.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from whisper_ui.core.logging_setup import RequestContextFilter
from whisper_ui.web.middleware.request_id import REQUEST_ID_HEADER, RequestIdMiddleware


@pytest.fixture
def caplog_with_context(caplog):
    """Attach the project's RequestContextFilter to caplog so request_id /
    user_id attributes are available on captured records.

    pytest's caplog handler is independent of the production dictConfig,
    so without explicitly attaching the filter the records would be
    missing ``record.request_id`` and the assertions below would raise
    AttributeError. Production startup applies the filter via
    setup_logging().
    """
    caplog.handler.addFilter(RequestContextFilter())
    yield caplog
    caplog.handler.removeFilter(caplog.handler.filters[-1])


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    handler_logger = logging.getLogger("whisper_ui.test.handler")

    @app.get("/ok")
    def ok():
        handler_logger.info("handler-running")
        return {"ok": True}

    @app.get("/boom")
    def boom():
        handler_logger.error("about-to-raise")
        raise RuntimeError("intentional explosion")

    return app


def test_access_log_emitted_with_method_path_status_duration(caplog_with_context):
    app = _make_app()
    client = TestClient(app)

    with caplog_with_context.at_level(logging.INFO):
        response = client.get("/ok")

    assert response.status_code == 200
    access_records = [r for r in caplog_with_context.records if r.name == "whisper_ui.web.access"]
    assert len(access_records) == 1
    msg = access_records[0].getMessage()
    assert "method=GET" in msg
    assert "path=/ok" in msg
    assert "status=200" in msg
    assert "duration_ms=" in msg
    assert "ip=" in msg


def test_handler_log_and_access_log_share_request_id(caplog_with_context):
    app = _make_app()
    client = TestClient(app)
    inbound_id = "fee1deadbeef"

    with caplog_with_context.at_level(logging.INFO):
        response = client.get("/ok", headers={REQUEST_ID_HEADER: inbound_id})

    assert response.headers[REQUEST_ID_HEADER] == inbound_id

    interesting = {"whisper_ui.web.access", "whisper_ui.test.handler"}
    relevant = [r for r in caplog_with_context.records if r.name in interesting]
    assert len(relevant) >= 2
    ids = {getattr(r, "request_id", None) for r in relevant}
    assert ids == {inbound_id}


def test_access_log_emitted_with_sentinel_status_when_handler_raises(caplog_with_context):
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)

    with caplog_with_context.at_level(logging.INFO):
        response = client.get("/boom")

    assert response.status_code == 500
    access_records = [r for r in caplog_with_context.records if r.name == "whisper_ui.web.access"]
    assert len(access_records) == 1
    # The middleware records the sentinel because call_next propagated the
    # exception before producing a response object the middleware could
    # inspect for status_code.
    assert "status=500" in access_records[0].getMessage()


def test_sequential_requests_get_distinct_request_ids(caplog_with_context):
    app = _make_app()
    client = TestClient(app)

    with caplog_with_context.at_level(logging.INFO):
        client.get("/ok")
        client.get("/ok")

    access_records = [r for r in caplog_with_context.records if r.name == "whisper_ui.web.access"]
    assert len(access_records) == 2
    ids = {getattr(r, "request_id", None) for r in access_records}
    assert len(ids) == 2


def test_access_log_duration_ms_is_non_negative_integer(caplog_with_context):
    app = _make_app()
    client = TestClient(app)

    with caplog_with_context.at_level(logging.INFO):
        client.get("/ok")

    access_records = [r for r in caplog_with_context.records if r.name == "whisper_ui.web.access"]
    msg = access_records[0].getMessage()
    duration_token = next(t for t in msg.split() if t.startswith("duration_ms="))
    value = int(duration_token.split("=", 1)[1])
    assert value >= 0
