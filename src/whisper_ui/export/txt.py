from __future__ import annotations

from typing import TYPE_CHECKING

from whisper_ui.export.utils import collapse_newlines, strip_control_chars

if TYPE_CHECKING:
    from whisper_ui.core.models import TranscriptResult


class TxtExporter:
    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def mime_type(self) -> str:
        return "text/plain"

    def export(self, result: TranscriptResult) -> bytes:
        lines: list[str] = []
        current_speaker: str | None = None
        for seg in result.segments:
            if seg.speaker and seg.speaker != current_speaker:
                current_speaker = seg.speaker
                lines.append(f"\n[{current_speaker}]")
            # Sanitise like SRT/VTT: an embedded newline in a segment (which the
            # LLM correction stage can introduce) would otherwise break the
            # one-line-per-segment layout and could even forge a "[SPEAKER_*]"
            # header line. strip_control_chars also removes stray control bytes.
            lines.append(collapse_newlines(strip_control_chars(seg.text)))
        return "\n".join(lines).strip().encode("utf-8")
