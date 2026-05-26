"""Tests for request parameter validation helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from whisper_ui.web.validation import MAX_NUM_SPEAKERS, clamp_num_speakers, validate_hex_id


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
