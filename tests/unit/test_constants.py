from __future__ import annotations

from whisper_ui.core.constants import (
    ERROR_DISPLAY_LENGTH,
    ERROR_MAX_LENGTH,
    REDIS_COMPLETED_EXPIRY,
    REDIS_FAILED_EXPIRY,
)


def test_redis_expiry_values_reasonable():
    assert REDIS_COMPLETED_EXPIRY >= 3600
    assert REDIS_FAILED_EXPIRY >= 3600


def test_error_display_shorter_than_max():
    assert ERROR_DISPLAY_LENGTH < ERROR_MAX_LENGTH
