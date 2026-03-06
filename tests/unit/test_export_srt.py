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
