from __future__ import annotations

from whisper_ui.core.constants import (
    DEFAULT_JOB_LIST_LIMIT,
    ERROR_DISPLAY_LENGTH,
    ERROR_MAX_LENGTH,
    FFMPEG_CONVERT_TIMEOUT,
    FFPROBE_TIMEOUT,
    JOB_ID_DISPLAY_LENGTH,
    MESSAGE_MAX_LENGTH,
    REDIS_COMPLETED_EXPIRY,
    REDIS_FAILED_EXPIRY,
    REDIS_PROCESSING_EXPIRY,
    SQLITE_BUSY_TIMEOUT_MS,
    STDERR_MAX_LENGTH,
    TIMESTAMP_DISPLAY_LENGTH,
)


def test_string_lengths_positive():
    for val in (ERROR_MAX_LENGTH, ERROR_DISPLAY_LENGTH, MESSAGE_MAX_LENGTH, STDERR_MAX_LENGTH):
        assert val > 0


def test_display_lengths_positive():
    assert JOB_ID_DISPLAY_LENGTH > 0
    assert TIMESTAMP_DISPLAY_LENGTH > 0


def test_default_job_list_limit_positive():
    assert DEFAULT_JOB_LIST_LIMIT > 0


def test_timeouts_positive():
    assert FFMPEG_CONVERT_TIMEOUT > 0
    assert FFPROBE_TIMEOUT > 0
    assert SQLITE_BUSY_TIMEOUT_MS > 0


def test_redis_expiry_values_reasonable():
    assert REDIS_PROCESSING_EXPIRY >= 3600  # at least 1 hour
    assert REDIS_COMPLETED_EXPIRY >= 3600
    assert REDIS_FAILED_EXPIRY >= 3600


def test_error_display_shorter_than_max():
    assert ERROR_DISPLAY_LENGTH < ERROR_MAX_LENGTH
