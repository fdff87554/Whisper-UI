from __future__ import annotations

from typing import Protocol, runtime_checkable

from whisper_ui.core.models import TranscriptResult


@runtime_checkable
class Exporter(Protocol):
    @property
    def format_name(self) -> str: ...

    @property
    def file_extension(self) -> str: ...

    @property
    def mime_type(self) -> str: ...

    def export(self, result: TranscriptResult) -> bytes: ...
