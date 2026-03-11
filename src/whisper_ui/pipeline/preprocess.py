from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from whisper_ui.core.constants import FFMPEG_CONVERT_TIMEOUT, FFPROBE_TIMEOUT, STDERR_MAX_LENGTH
from whisper_ui.core.exceptions import PreprocessError
from whisper_ui.core.messages import PREPROCESS_CONVERTING, PREPROCESS_DONE
from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".wma", ".aac", ".opus", ".mp4", ".webm", ".mkv"}


class PreprocessStage:
    @property
    def name(self) -> str:
        return "preprocess"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        input_path = Path(context["input_path"])
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise PreprocessError(f"Unsupported file format: {input_path.suffix}")

        if on_progress:
            on_progress(0.0, PREPROCESS_CONVERTING)

        output_path = input_path.with_suffix(".16k.wav")

        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_CONVERT_TIMEOUT)
            if result.returncode != 0:
                raise PreprocessError(f"FFmpeg failed: {result.stderr[:STDERR_MAX_LENGTH]}")
        except FileNotFoundError as err:
            raise PreprocessError("FFmpeg not found. Please install FFmpeg.") from err
        except subprocess.TimeoutExpired as err:
            raise PreprocessError("Audio conversion timed out (>5min).") from err

        duration = _get_duration(output_path)

        if on_progress:
            on_progress(1.0, PREPROCESS_DONE)

        context["audio_path"] = str(output_path)
        context["duration"] = duration
        return context

    def cleanup(self) -> None:
        pass


def _get_duration(path: Path) -> float:
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
        return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Could not determine audio duration for %s", path)
        return 0.0
