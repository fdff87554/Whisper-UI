from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from whisper_ui.core.constants import FFMPEG_CONVERT_TIMEOUT, STDERR_MAX_LENGTH
from whisper_ui.core.exceptions import PreprocessError
from whisper_ui.core.messages import PREPROCESS_CONVERTING, PREPROCESS_DONE
from whisper_ui.pipeline.audio_probe import get_audio_duration_seconds

if TYPE_CHECKING:
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
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_CONVERT_TIMEOUT)
        except FileNotFoundError as err:
            raise PreprocessError("FFmpeg not found. Please install FFmpeg.") from err
        except subprocess.TimeoutExpired as err:
            # ffmpeg -y may have produced a partial WAV; audio_path is not in
            # the context yet, so the runtime's cleanup hook can never reach
            # it — remove it here or a permanently-kept FAILED job leaks it.
            output_path.unlink(missing_ok=True)
            raise PreprocessError(f"Audio conversion timed out (>{FFMPEG_CONVERT_TIMEOUT}s).") from err
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise PreprocessError(f"FFmpeg failed: {result.stderr[:STDERR_MAX_LENGTH]}")

        duration = get_audio_duration_seconds(output_path, job_id=context.get("parent_job_id")) or 0.0

        if on_progress:
            on_progress(1.0, PREPROCESS_DONE)

        context["audio_path"] = str(output_path)
        context["duration"] = duration
        return context

    def cleanup(self) -> None:
        pass
