from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from whisper_ui.core.models import TranscriptResult


class JsonExporter:
    @property
    def file_extension(self) -> str:
        return ".json"

    @property
    def mime_type(self) -> str:
        return "application/json"

    def export(self, result: TranscriptResult) -> bytes:
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
