from __future__ import annotations

from whisper_ui.export.utils import format_timestamp


def test_format_timestamp_comma():
    assert format_timestamp(0.0, ",") == "00:00:00,000"
    assert format_timestamp(1.5, ",") == "00:00:01,500"
    assert format_timestamp(3661.123, ",") == "01:01:01,123"


def test_format_timestamp_dot():
    assert format_timestamp(0.0, ".") == "00:00:00.000"
    assert format_timestamp(1.5, ".") == "00:00:01.500"
    assert format_timestamp(3661.123, ".") == "01:01:01.123"


def test_format_timestamp_default_separator():
    assert format_timestamp(1.5) == "00:00:01,500"
