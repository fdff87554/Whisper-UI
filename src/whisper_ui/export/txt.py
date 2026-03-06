from __future__ import annotations

from whisper_ui.core.models import TranscriptResult


class TxtExporter:
    @property
    def format_name(self) -> str:
        return "TXT"

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
            lines.append(seg.text)
        return "\n".join(lines).strip().encode("utf-8")
