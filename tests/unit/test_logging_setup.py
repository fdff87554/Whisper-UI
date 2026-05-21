"""Tests for the centralised logging configuration."""

from __future__ import annotations

import logging

import pytest

from whisper_ui.core.logging_setup import (
    _DEFAULT_REQUEST_ID,
    _DEFAULT_USER_ID,
    RequestContextFilter,
    current_request_id,
    current_user_id,
    reset_request_context,
    set_request_context,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _restore_logging():
    """Reset root logger handlers between tests so dictConfig state does not leak."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


@pytest.mark.parametrize(
    ("env_value", "expected_level_name"),
    [
        ("DEBUG", "DEBUG"),
        ("INFO", "INFO"),
        ("WARNING", "WARNING"),
        ("ERROR", "ERROR"),
        ("CRITICAL", "CRITICAL"),
        ("info", "INFO"),
        ("  warning  ", "WARNING"),
    ],
)
def test_setup_logging_honours_valid_log_level(monkeypatch, env_value, expected_level_name):
    monkeypatch.setenv("LOG_LEVEL", env_value)

    setup_logging()

    assert logging.getLogger().getEffectiveLevel() == getattr(logging, expected_level_name)


def test_setup_logging_falls_back_to_info_for_invalid_log_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "TRACE")

    setup_logging()

    assert logging.getLogger().getEffectiveLevel() == logging.INFO


def test_setup_logging_defaults_to_info_when_env_unset(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    setup_logging()

    assert logging.getLogger().getEffectiveLevel() == logging.INFO


def test_setup_logging_pins_rq_loggers_to_warning(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    setup_logging()

    assert logging.getLogger("rq").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("rq.worker").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("rq.scheduler").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("uvicorn.access").getEffectiveLevel() == logging.WARNING


def test_setup_logging_is_idempotent(monkeypatch):
    """Calling setup_logging twice must not double-attach handlers."""
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    setup_logging()
    setup_logging()

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1


def test_setup_logging_emits_records_with_request_context(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    setup_logging()
    tokens = set_request_context(request_id="abc12345", user_id="alice")
    try:
        logging.getLogger("whisper_ui.test").info("hello world")
    finally:
        reset_request_context(tokens)

    captured = capsys.readouterr().err
    assert "hello world" in captured
    assert "req=abc12345" in captured
    assert "user=alice" in captured


def test_request_context_filter_defaults_when_unset():
    """A LogRecord processed outside any request must get the default dashes."""
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="msg",
        args=(),
        exc_info=None,
    )

    assert RequestContextFilter().filter(record) is True
    assert record.request_id == _DEFAULT_REQUEST_ID
    assert record.user_id == _DEFAULT_USER_ID


def test_set_and_reset_request_context_round_trip():
    tokens = set_request_context(request_id="r1", user_id="u1")
    try:
        assert current_request_id() == "r1"
        assert current_user_id() == "u1"
    finally:
        reset_request_context(tokens)

    assert current_request_id() == _DEFAULT_REQUEST_ID
    assert current_user_id() == _DEFAULT_USER_ID


def test_request_context_isolated_across_nested_blocks():
    """Nested set/reset must restore the outer value, not the default."""
    outer = set_request_context(request_id="outer", user_id="outer-u")
    try:
        inner = set_request_context(request_id="inner", user_id="inner-u")
        try:
            assert current_request_id() == "inner"
        finally:
            reset_request_context(inner)
        assert current_request_id() == "outer"
    finally:
        reset_request_context(outer)
