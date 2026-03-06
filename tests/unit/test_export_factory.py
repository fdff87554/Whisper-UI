from __future__ import annotations

import pytest

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.export.factory import available_formats, get_exporter


def test_available_formats():
    formats = available_formats()
    assert "srt" in formats
    assert "vtt" in formats
    assert "txt" in formats
    assert "json" in formats
    assert "docx" in formats


def test_get_exporter_unknown():
    with pytest.raises(ValueError, match="Unknown export format"):
        get_exporter("xyz")


def test_txt_export():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.0, text="Hello", speaker="SPEAKER_00"),
            Segment(start=1.0, end=2.0, text="World", speaker="SPEAKER_00"),
            Segment(start=2.0, end=3.0, text="Bye", speaker="SPEAKER_01"),
        ],
    )
    exporter = get_exporter("txt")
    data = exporter.export(result).decode("utf-8")
    assert "[SPEAKER_00]" in data
    assert "[SPEAKER_01]" in data
    assert "Hello" in data


def test_json_export():
    result = TranscriptResult(
        segments=[Segment(start=0.0, end=1.0, text="test")],
        language="en",
        duration=1.0,
    )
    exporter = get_exporter("json")
    data = exporter.export(result).decode("utf-8")
    assert '"language": "en"' in data
    assert '"duration": 1.0' in data
    assert '"text": "test"' in data
