from __future__ import annotations

from whisper_ui.core.models import Segment, TranscriptResult


def _format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


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
            lines.append(f"{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}")
            lines.append(_format_text(seg))
            lines.append("")
        return "\n".join(lines).encode("utf-8")
