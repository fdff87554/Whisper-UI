"""Tests for the rate-limit decision logging.

The integration-level "does the limit actually block requests?" coverage
lives in tests/unit/test_auth_routes.py; this file focuses narrowly on
the log messages because those are what a security operator will grep
during an incident.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from whisper_ui.web.rate_limit import (
    _describe_trip,
    check_and_increment,
    is_locked,
    record_register_attempt,
    register_is_locked,
    reset_user,
)


@pytest.fixture
def redis():
    return fakeredis.FakeStrictRedis()


@pytest.fixture
def down_redis():
    """A Redis client whose every call raises, simulating an outage."""
    client = MagicMock()
    client.get.side_effect = RedisConnectionError("down")
    client.delete.side_effect = RedisConnectionError("down")
    client.pipeline.side_effect = RedisConnectionError("down")
    return client


def _call_check(redis, username: str = "alice", ip: str = "1.2.3.4") -> bool:
    return check_and_increment(
        redis,
        username=username,
        ip=ip,
        max_user_attempts=5,
        max_ip_attempts=20,
        window_seconds=900,
    )


def test_describe_trip_user_only():
    assert (
        _describe_trip(
            user_count=5,
            ip_count=1,
            max_user_attempts=5,
            max_ip_attempts=20,
        )
        == "user"
    )


def test_describe_trip_ip_only():
    assert (
        _describe_trip(
            user_count=1,
            ip_count=20,
            max_user_attempts=5,
            max_ip_attempts=20,
        )
        == "ip"
    )


def test_describe_trip_both():
    assert (
        _describe_trip(
            user_count=10,
            ip_count=30,
            max_user_attempts=5,
            max_ip_attempts=20,
        )
        == "both"
    )


def test_describe_trip_none():
    assert (
        _describe_trip(
            user_count=2,
            ip_count=3,
            max_user_attempts=5,
            max_ip_attempts=20,
        )
        is None
    )


def test_debug_log_emitted_on_every_attempt(redis, caplog):
    with caplog.at_level(logging.DEBUG, logger="whisper_ui.web.rate_limit"):
        _call_check(redis)

    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug) == 1
    msg = debug[0].getMessage()
    assert "user_count=1/5" in msg
    assert "ip_count=1/20" in msg
    assert "tripped=no" in msg


def test_warning_log_emitted_only_when_threshold_tripped(redis, caplog):
    with caplog.at_level(logging.DEBUG, logger="whisper_ui.web.rate_limit"):
        for _ in range(4):
            assert _call_check(redis) is False
        # 5th attempt hits the user threshold (5 >= 5) and trips.
        assert _call_check(redis) is True

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "tripped on user counter" in warnings[0].getMessage()
    assert "user_count=5/5" in warnings[0].getMessage()


def test_warning_message_names_ip_dimension_when_only_ip_trips(redis, caplog):
    """20 different usernames from the same IP — only the IP counter trips."""
    with caplog.at_level(logging.WARNING, logger="whisper_ui.web.rate_limit"):
        tripped = False
        for i in range(20):
            tripped = _call_check(redis, username=f"u{i}", ip="9.9.9.9") or tripped

    assert tripped is True
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    msg = warning.getMessage()
    assert "tripped on ip counter" in msg
    assert "ip_count=20/20" in msg


def test_is_locked_logs_debug_only_when_already_locked(redis, caplog):
    # Pre-populate the user counter so is_locked finds it locked.
    redis.set("auth:rl:user:alice", 5)

    with caplog.at_level(logging.DEBUG, logger="whisper_ui.web.rate_limit"):
        assert (
            is_locked(
                redis,
                username="alice",
                ip="1.2.3.4",
                max_user_attempts=5,
                max_ip_attempts=20,
            )
            is True
        )

    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("short-circuit" in r.getMessage() for r in debug)


def test_is_locked_silent_when_under_threshold(redis, caplog):
    with caplog.at_level(logging.DEBUG, logger="whisper_ui.web.rate_limit"):
        is_locked(
            redis,
            username="alice",
            ip="1.2.3.4",
            max_user_attempts=5,
            max_ip_attempts=20,
        )

    assert caplog.records == []


def test_reset_user_logs_info_when_counter_actually_cleared(redis, caplog):
    redis.set("auth:rl:user:alice", 3)

    with caplog.at_level(logging.INFO, logger="whisper_ui.web.rate_limit"):
        reset_user(redis, "alice")

    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info) == 1
    assert "cleared on successful login" in info[0].getMessage()


def test_reset_user_silent_when_no_counter_to_clear(redis, caplog):
    with caplog.at_level(logging.INFO, logger="whisper_ui.web.rate_limit"):
        reset_user(redis, "ghost")

    assert caplog.records == []


# --- Fail-open on Redis outage: auth must not 500 when the queue store is down ---


def test_check_and_increment_fails_open_on_redis_error(down_redis, caplog):
    with caplog.at_level(logging.WARNING, logger="whisper_ui.web.rate_limit"):
        blocked = _call_check(down_redis)

    assert blocked is False
    assert any("failing open" in r.message for r in caplog.records)


def test_is_locked_fails_open_on_redis_error(down_redis):
    assert is_locked(down_redis, username="alice", ip="1.2.3.4", max_user_attempts=5, max_ip_attempts=20) is False


def test_register_is_locked_fails_open_on_redis_error(down_redis):
    assert register_is_locked(down_redis, ip="1.2.3.4", max_attempts=10) is False


def test_record_register_attempt_swallows_redis_error(down_redis):
    # Must not raise even though the pipeline call errors.
    record_register_attempt(down_redis, ip="1.2.3.4", window_seconds=900)


def test_reset_user_swallows_redis_error(down_redis):
    # Called on successful login; a Redis outage here must not 500 after auth.
    reset_user(down_redis, "alice")
