from __future__ import annotations

from pathlib import PurePosixPath
from typing import Protocol

from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS


class NamedFile(Protocol):
    name: str


def filter_supported_files(files: list[NamedFile]) -> tuple[list[NamedFile], int]:
    """Return (supported_files, skipped_count)."""
    supported = [f for f in files if PurePosixPath(f.name).suffix.lower() in SUPPORTED_EXTENSIONS]
    return supported, len(files) - len(supported)
