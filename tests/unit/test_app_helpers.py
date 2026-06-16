from __future__ import annotations

import pytest

from whisper_ui.web.app import _redact_redis_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Password in userinfo is stripped, host:port/db preserved.
        ("redis://:supersecret@redis:6379/0", "redis://***@redis:6379/0"),
        ("redis://user:supersecret@redis:6379/1", "redis://***@redis:6379/1"),
        # Host without an explicit port still redacts cleanly.
        ("redis://:pw@redis/0", "redis://***@redis/0"),
        # No credential present: returned unchanged.
        ("redis://localhost:6379/0", "redis://localhost:6379/0"),
    ],
)
def test_redact_redis_url_strips_password(url: str, expected: str):
    assert _redact_redis_url(url) == expected


def test_redact_redis_url_never_leaks_password():
    assert "supersecret" not in _redact_redis_url("redis://:supersecret@redis:6379/0")


def test_redact_redis_url_handles_malformed_input():
    # Must not raise even on a value that cannot be parsed as a URL.
    assert _redact_redis_url("redis://[oops") == "<redacted>"
