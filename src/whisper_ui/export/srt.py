from __future__ import annotations

from typing import TYPE_CHECKING

from whisper_ui.export.utils import collapse_newlines, format_timestamp, neutralize_srt_arrows

if TYPE_CHECKING:
    from whisper_ui.core.models import Segment, TranscriptResult


def _format_text(segment: Segment) -> str:
    prefix = f"[{segment.speaker}] " if segment.speaker else ""
    return collapse_newlines(neutralize_srt_arrows(f"{prefix}{segment.text}"))


class SrtExporter:
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
