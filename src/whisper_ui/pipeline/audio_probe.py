"""Lightweight ffprobe-based audio duration helper.

Used both by :mod:`whisper_ui.pipeline.preprocess` (to record duration of
the converted 16kHz WAV) and by the upload routes (to size RQ
``job_timeout`` based on the source audio before enqueueing). Kept as a
module-level helper rather than a method so callers do not have to
instantiate a stage just to probe a file.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

from whisper_ui.core.constants import FFPROBE_TIMEOUT

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def get_audio_duration_seconds(path: Path | str) -> float | None:
    """Return the audio duration in seconds, or ``None`` on failure.

    Failures (ffprobe missing, parse error, timeout) are logged as warnings
    and yield ``None`` so callers can gracefully fall back to default
    behavior — probing must never block an upload or a pipeline run.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("ffprobe unavailable or timed out while probing %s", path)
        return None

    stdout = result.stdout.strip()
    if not stdout:
        logger.warning("ffprobe returned empty duration for %s", path)
        return None
    try:
        value = float(stdout)
    except ValueError:
        logger.warning("ffprobe returned non-numeric duration for %s: %r", path, stdout)
        return None

    if value <= 0:
        return None
    return value
