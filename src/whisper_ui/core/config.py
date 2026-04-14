from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Storage
    database_path: Path = Field(default=_PROJECT_ROOT / "data" / "db" / "whisper_ui.db")
    upload_dir: Path = Field(default=_PROJECT_ROOT / "data" / "uploads")
    output_dir: Path = Field(default=_PROJECT_ROOT / "data" / "outputs")

    # Whisper
    whisper_model: str = "large-v3"
    compute_type: str = "int8_float16"
    device: str = "auto"
    batch_size: int = 4

    # Language
    language: str = "zh"

    # HuggingFace
    hf_token: str = ""

    # Upload
    max_upload_size: int = 2 * 1024 * 1024 * 1024  # 2 GB

    # YouTube
    youtube_max_duration: int = 14400  # seconds (4 hours)

    # Queue / job timeouts (seconds)
    # Fallback when audio duration is unknown (e.g., YouTube URL before download).
    job_timeout_default: int = 7200  # 2h
    # Lower bound applied to dynamically calculated timeouts so short audio still
    # gets enough headroom for model loading and overhead.
    job_timeout_floor: int = 1800  # 30min
    # Dynamic timeout = audio_duration_seconds * multiplier, then clamped into
    # [job_timeout_floor, job_timeout_max]. 3x gives comfortable headroom on GPU
    # for large-v3 + alignment + diarization across the pipeline.
    job_timeout_audio_multiplier: float = 3.0
    # Hard upper cap to prevent runaway jobs; also the basis for stale recovery.
    job_timeout_max: int = 28800  # 8h
    # Extra buffer added on top of job_timeout_max when reclaiming stale jobs
    # whose worker died without updating the DB.
    stale_job_buffer: int = 1800  # 30min
    # Redis TTL for progress keys of running jobs. Should exceed the longest
    # possible job timeout so the UI does not lose progress state mid-run.
    redis_processing_expiry: int = 30600  # job_timeout_max + stale_job_buffer
    # How often DiarizeStage's background heartbeat refreshes progress so
    # stale-job-recovery and the UI can see the task is still alive.
    diarize_heartbeat_interval: int = 30

    @property
    def stale_job_timeout(self) -> int:
        """Threshold (seconds) after which a PROCESSING job is considered stale.

        Derived from ``job_timeout_max`` plus ``stale_job_buffer`` so it always
        stays consistent when the cap is tuned.
        """
        return self.job_timeout_max + self.stale_job_buffer


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    from whisper_ui.core.device import detect_device, validate_compute_type

    settings = Settings()
    resolved_device = detect_device(settings.device)
    resolved_compute = validate_compute_type(resolved_device, settings.compute_type)
    return settings.model_copy(update={"device": resolved_device, "compute_type": resolved_compute})
