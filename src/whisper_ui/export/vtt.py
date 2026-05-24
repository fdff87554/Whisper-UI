from __future__ import annotations

from typing import TYPE_CHECKING

from whisper_ui.export.utils import collapse_newlines, format_timestamp

if TYPE_CHECKING:
    from whisper_ui.core.models import Segment, TranscriptResult


def _format_text(segment: Segment) -> str:
    prefix = f"<v {segment.speaker}>" if segment.speaker else ""
    return collapse_newlines(f"{prefix}{segment.text}")


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
            lines.append(f"{format_timestamp(seg.start, '.')} --> {format_timestamp(seg.end, '.')}")
            lines.append(_format_text(seg))
            lines.append("")
        return "\n".join(lines).encode("utf-8")
