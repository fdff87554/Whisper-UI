from __future__ import annotations

from whisper_ui.export.base import Exporter
from whisper_ui.export.docx_export import DocxExporter
from whisper_ui.export.json_export import JsonExporter
from whisper_ui.export.srt import SrtExporter
from whisper_ui.export.txt import TxtExporter
from whisper_ui.export.vtt import VttExporter

_EXPORTERS: dict[str, type[Exporter]] = {
    "srt": SrtExporter,
    "vtt": VttExporter,
    "txt": TxtExporter,
    "json": JsonExporter,
    "docx": DocxExporter,
}


def get_exporter(format_name: str) -> Exporter:
    cls = _EXPORTERS.get(format_name.lower())
    if cls is None:
        available = ", ".join(sorted(_EXPORTERS.keys()))
        raise ValueError(f"Unknown export format: {format_name}. Available: {available}")
    return cls()


def available_formats() -> list[str]:
    return sorted(_EXPORTERS.keys())
