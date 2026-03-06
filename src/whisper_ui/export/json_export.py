from __future__ import annotations

import json

from whisper_ui.core.models import TranscriptResult


class JsonExporter:
    @property
    def format_name(self) -> str:
        return "JSON"

    @property
    def file_extension(self) -> str:
        return ".json"

    @property
    def mime_type(self) -> str:
        return "application/json"

    def export(self, result: TranscriptResult) -> bytes:
        payload = {
            "language": result.language,
            "duration": result.duration,
            "segments": [
                {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "speaker": s.speaker,
                }
                for s in result.segments
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
