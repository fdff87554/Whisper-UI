"""Unit tests for JSON export."""

from __future__ import annotations

import json

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.export.json_export import JsonExporter


class TestJsonExporter:
    def setup_method(self):
        self.exporter = JsonExporter()

    def test_properties(self):
        assert self.exporter.format_name == "JSON"
        assert self.exporter.file_extension == ".json"
        assert self.exporter.mime_type == "application/json"

    def test_export_structure(self):
        result = TranscriptResult(
            segments=[
                Segment(start=0.0, end=1.5, text="Hello", speaker="S1"),
                Segment(start=1.5, end=3.0, text="World"),
            ],
            language="en",
            duration=3.0,
        )
        data = json.loads(self.exporter.export(result))

        assert data["language"] == "en"
        assert data["duration"] == 3.0
        assert len(data["segments"]) == 2
        assert data["segments"][0]["start"] == 0.0
        assert data["segments"][0]["end"] == 1.5
        assert data["segments"][0]["text"] == "Hello"
        assert data["segments"][0]["speaker"] == "S1"
        assert data["segments"][1]["speaker"] is None

    def test_ensure_ascii_false(self):
        result = TranscriptResult(
            segments=[Segment(start=0.0, end=1.0, text="你好世界")],
            language="zh",
            duration=1.0,
        )
        raw = self.exporter.export(result)
        text = raw.decode("utf-8")
        # Chinese characters should appear directly, not as \uXXXX escapes
        assert "你好世界" in text
        assert "\\u" not in text

    def test_empty_segments(self):
        result = TranscriptResult(segments=[], language="en", duration=0.0)
        data = json.loads(self.exporter.export(result))
        assert data["segments"] == []
        assert data["duration"] == 0.0
