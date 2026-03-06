from __future__ import annotations

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.export.vtt import VttExporter


def test_vtt_export():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.5, text="Hello", speaker="SPEAKER_00"),
            Segment(start=2.0, end=3.0, text="World"),
        ],
    )
    exporter = VttExporter()
    data = exporter.export(result).decode("utf-8")
    assert data.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in data
    assert "<v SPEAKER_00>Hello" in data
    assert "World" in data


def test_vtt_format_metadata():
    exporter = VttExporter()
    assert exporter.format_name == "VTT"
    assert exporter.file_extension == ".vtt"
    assert exporter.mime_type == "text/vtt"
