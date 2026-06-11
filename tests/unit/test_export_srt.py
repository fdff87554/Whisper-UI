from __future__ import annotations

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.export.srt import SrtExporter


def test_srt_export():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.5, text="Hello world", speaker="SPEAKER_00"),
            Segment(start=2.0, end=4.0, text="How are you"),
        ],
    )
    exporter = SrtExporter()
    data = exporter.export(result).decode("utf-8")
    lines = data.strip().split("\n")

    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,500"
    assert lines[2] == "[SPEAKER_00] Hello world"
    assert lines[4] == "2"
    assert lines[5] == "00:00:02,000 --> 00:00:04,000"
    assert lines[6] == "How are you"


def test_srt_format_metadata():
    exporter = SrtExporter()
    assert exporter.format_name == "SRT"
    assert exporter.file_extension == ".srt"
    assert exporter.mime_type == "text/plain"


def test_srt_collapses_newlines_in_text_to_keep_cue_single_line():
    # A newline inside the text (the LLM-correction stage can emit one) would
    # otherwise split the cue and break the blank-line block delimiter.
    result = TranscriptResult(
        segments=[Segment(start=0.0, end=1.0, text="line one\nline two", speaker="SPEAKER_00")],
    )
    lines = SrtExporter().export(result).decode("utf-8").split("\n")

    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,000"
    assert lines[2] == "[SPEAKER_00] line one line two"
    assert lines[3] == ""


def test_srt_neutralizes_arrow_in_text():
    # SRT has no escaping mechanism; a literal "-->" inside the text would
    # mimic a timing line, so it is substituted with a visual equivalent.
    result = TranscriptResult(
        segments=[Segment(start=0.0, end=1.0, text="from a --> to b")],
    )
    data = SrtExporter().export(result).decode("utf-8")

    assert "from a → to b" in data
    assert data.count("-->") == 1  # the genuine timing line only
