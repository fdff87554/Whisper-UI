from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from whisper_ui.core.models import TranscriptResult


class Exporter(Protocol):
    @property
    def file_extension(self) -> str: ...

    @property
    def mime_type(self) -> str: ...

    def export(self, result: TranscriptResult) -> bytes: ...
