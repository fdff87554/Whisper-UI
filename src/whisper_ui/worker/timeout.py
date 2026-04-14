"""Dynamic RQ job_timeout calculation for transcription jobs.

The transcription pipeline's cost scales roughly linearly with audio
duration (preprocess + large-v3 inference + alignment + diarization).
Hardcoding job_timeout at enqueue time means either short files waste
queue capacity or long files get killed mid-pipeline. This helper
derives a timeout from the probed audio duration and applies sane
floor / cap / fallback bounds from :class:`Settings`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from whisper_ui.core.config import Settings


def calculate_job_timeout(
    audio_duration_seconds: float | None,
    settings: Settings,
) -> int:
    """Return the RQ job_timeout to use for a transcription job (seconds).

    Args:
        audio_duration_seconds: Probed duration of the input audio, or
            ``None`` when the duration cannot be determined yet
            (e.g., YouTube URL submissions before download).
        settings: Whisper UI settings carrying the timeout bounds.

    Returns:
        Integer seconds, guaranteed to lie within
        ``[settings.job_timeout_floor, settings.job_timeout_max]`` when a
        duration is provided, or :attr:`Settings.job_timeout_default`
        when it is not.
    """
    if audio_duration_seconds is None or audio_duration_seconds <= 0:
        return settings.job_timeout_default

    estimated = audio_duration_seconds * settings.job_timeout_audio_multiplier
    clamped = max(settings.job_timeout_floor, min(settings.job_timeout_max, estimated))
    return int(clamped)
