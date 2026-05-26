"""Tests for request parameter validation helpers."""

from __future__ import annotations

import pytest

from whisper_ui.web.validation import MAX_NUM_SPEAKERS, clamp_num_speakers


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
