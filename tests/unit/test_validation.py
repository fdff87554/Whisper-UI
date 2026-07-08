"""Tests for request parameter validation helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from whisper_ui.core.languages import DEFAULT_WHISPER_MODEL
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.web.validation import (
    MAX_NUM_SPEAKERS,
    clamp_num_speakers,
    mark_enqueue_failed,
    normalize_status_filter,
    validate_hex_id,
    validate_upload_options,
)


@pytest.mark.parametrize("valid", ["", *(s.value for s in JobStatus)])
def test_normalize_status_filter_keeps_valid_values(valid):
    assert normalize_status_filter(valid) == valid


@pytest.mark.parametrize("bad", ["bogus", "COMPLETED", "queued ", "'; DROP TABLE jobs;--"])
def test_normalize_status_filter_resets_unknown_to_all(bad):
    assert normalize_status_filter(bad) == ""


def test_validate_hex_id_accepts_32_lowercase_hex():
    value = "0123456789abcdef0123456789abcdef"
    assert validate_hex_id(value) == value


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abc",  # too short
        "0123456789abcdef0123456789abcde",  # 31 chars
        "0123456789abcdef0123456789abcdef0",  # 33 chars
        "0123456789ABCDEF0123456789ABCDEF",  # uppercase rejected
        "0123456789abcdef0123456789abcdeg",  # non-hex char
        "../../../etc/passwd",  # path traversal attempt
    ],
)
def test_validate_hex_id_rejects_malformed(bad):
    with pytest.raises(HTTPException) as exc:
        validate_hex_id(bad, "job_id")
    assert exc.value.status_code == 400
    assert "job_id" in exc.value.detail


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 0),
        (1, 1),
        (MAX_NUM_SPEAKERS, MAX_NUM_SPEAKERS),
        (-1, 0),
        (-9999, 0),
        (MAX_NUM_SPEAKERS + 1, MAX_NUM_SPEAKERS),
        (99999, MAX_NUM_SPEAKERS),
    ],
)
def test_clamp_num_speakers_keeps_values_within_bounds(value, expected):
    assert clamp_num_speakers(value) == expected


class TestValidateUploadOptions:
    def test_accepts_valid_language_and_model(self):
        assert validate_upload_options("zh", DEFAULT_WHISPER_MODEL) is None

    def test_rejects_unknown_language_first(self):
        err = validate_upload_options("klingon", DEFAULT_WHISPER_MODEL)
        assert err is not None
        assert err.error_code == "invalid_language"
        assert err.value == "klingon"
        assert "klingon" in err.message

    def test_rejects_unknown_model(self):
        err = validate_upload_options("zh", "not-a-model")
        assert err is not None
        assert err.error_code == "invalid_model"
        assert err.value == "not-a-model"


class TestMarkEnqueueFailed:
    def test_sets_failed_status_error_and_persists(self):
        job = Job(filename="t.mp3", status=JobStatus.QUEUED)
        db = MagicMock()
        try:
            raise RuntimeError("enqueue boom")  # active exception for logger.exception
        except RuntimeError:
            mark_enqueue_failed(job, db)
        assert job.status == JobStatus.FAILED
        assert job.error  # shared enqueue-failure label
        db.update_job.assert_called_once_with(job)
