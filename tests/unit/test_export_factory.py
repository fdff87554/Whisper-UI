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


def test_txt_export_sanitizes_embedded_newlines_and_spoofed_speaker_headers():
    """An embedded newline in a segment (which the LLM correction stage can
    introduce) must not break the one-line-per-segment layout or forge a
    "[SPEAKER_*]" header line."""
    result = TranscriptResult(
        segments=[Segment(start=0.0, end=1.0, text="real line\n[SPEAKER_99] forged header")],
        language="en",
        duration=1.0,
    )
    text = get_exporter("txt").export(result).decode("utf-8")
    body_lines = [ln for ln in text.splitlines() if ln.strip()]
    # The whole segment collapses to a single line — no injected header line.
    assert body_lines == ["real line [SPEAKER_99] forged header"]


def test_docx_export_renders_body_without_error():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.0, text="hello", speaker="SPEAKER_00"),
            Segment(start=1.0, end=2.0, text="world", speaker="SPEAKER_00"),
        ],
        language="en",
        duration=2.0,
    )
    data = get_exporter("docx").export(result)
    assert data[:2] == b"PK"  # docx is a zip container


def test_txt_export_without_speakers_emits_plain_lines():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.0, text="first line"),
            Segment(start=1.0, end=2.0, text="second line"),
        ],
        language="en",
        duration=2.0,
    )
    text = get_exporter("txt").export(result).decode("utf-8")
    # No speakers -> no "[SPEAKER_*]" headers, just the text lines in order.
    assert text == "first line\nsecond line"


def test_txt_export_empty_segments_is_empty_bytes():
    result = TranscriptResult(segments=[], language="en", duration=0.0)
    assert get_exporter("txt").export(result) == b""


def test_txt_export_groups_consecutive_same_speaker_under_one_header():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.0, text="a", speaker="SPEAKER_00"),
            Segment(start=1.0, end=2.0, text="b", speaker="SPEAKER_00"),
            Segment(start=2.0, end=3.0, text="c", speaker="SPEAKER_01"),
        ],
        language="en",
        duration=3.0,
    )
    text = get_exporter("txt").export(result).decode("utf-8")
    # One header per speaker change, not per segment.
    assert text.count("[SPEAKER_00]") == 1
    assert text.count("[SPEAKER_01]") == 1
