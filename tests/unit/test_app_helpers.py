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
        # Credential present but no host -> never echo it back.
        ("redis://:secret@/0", "<redacted>"),
        # Password containing '#' breaks the authority parse -> fail safe.
        ("redis://:pa#ss@redis:6379/0", "<redacted>"),
        # Password as a query param is dropped along with the rest of the query.
        ("redis://redis:6379/0?password=secret", "redis://redis:6379/0"),
        # A malformed port makes SplitResult.port raise -> fail safe, never raise.
        ("redis://:pw@host:invalidport/0", "<redacted>"),
        ("redis://:pw@host:99999/0", "<redacted>"),
    ],
)
def test_redact_redis_url_strips_password(url: str, expected: str):
    assert _redact_redis_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "redis://:supersecret@redis:6379/0",
        "redis://:secret@/0",
        "redis://:pa#ss@redis:6379/0",
        "redis://redis:6379/0?password=supersecret",
        "redis://:pw@[oops",  # malformed but credentialed
    ],
)
def test_redact_redis_url_never_leaks_password(url: str):
    redacted = _redact_redis_url(url)
    assert "secret" not in redacted
    assert "pa#ss" not in redacted
    assert "pw" not in redacted


def test_redact_redis_url_handles_malformed_credentialed_input():
    # A credentialed URL that cannot be parsed must fail safe, never raise.
    assert _redact_redis_url("redis://:pw@[oops") == "<redacted>"
