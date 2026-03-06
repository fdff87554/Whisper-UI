from __future__ import annotations

from whisper_ui.core.models import Segment, TranscriptResult


def _format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _format_text(segment: Segment) -> str:
    prefix = f"<v {segment.speaker}>" if segment.speaker else ""
    return f"{prefix}{segment.text}"


class VttExporter:
    @property
    def format_name(self) -> str:
        return "VTT"

    @property
    def file_extension(self) -> str:
        return ".vtt"

    @property
    def mime_type(self) -> str:
        return "text/vtt"

    def export(self, result: TranscriptResult) -> bytes:
        lines: list[str] = ["WEBVTT", ""]
        for seg in result.segments:
            lines.append(f"{_format_timestamp(seg.start)} --> {_format_timestamp(seg.end)}")
            lines.append(_format_text(seg))
            lines.append("")
        return "\n".join(lines).encode("utf-8")
