from __future__ import annotations

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.export.utils import format_timestamp


def _format_text(segment: Segment) -> str:
    prefix = f"[{segment.speaker}] " if segment.speaker else ""
    return f"{prefix}{segment.text}"


class SrtExporter:
    @property
    def format_name(self) -> str:
        return "SRT"

    @property
    def file_extension(self) -> str:
        return ".srt"

    @property
    def mime_type(self) -> str:
        return "text/plain"

    def export(self, result: TranscriptResult) -> bytes:
        lines: list[str] = []
        for i, seg in enumerate(result.segments, 1):
            lines.append(str(i))
            lines.append(f"{format_timestamp(seg.start, ',')} --> {format_timestamp(seg.end, ',')}")
            lines.append(_format_text(seg))
            lines.append("")
        return "\n".join(lines).encode("utf-8")
