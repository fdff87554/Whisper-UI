from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field, model_validator
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

    # -- Optional LLM text correction (Ollama) --
    # Empty string disables the feature entirely — even jobs that opt in via
    # the UI checkbox will be silently skipped. Set to a reachable Ollama URL
    # to enable: "http://ollama:11434" for the bundled `llm` compose profile,
    # or any external Ollama server.
    ollama_base_url: str = ""
    ollama_model: str = "gemma4:e2b"
    # keep_alive format follows Ollama's duration string (e.g. "30m", "1h",
    # "-1" = forever). Longer values amortize model load cost across chunks.
    ollama_keep_alive: str = "30m"
    ollama_request_timeout: int = 120
    llm_chunk_size: int = 8
    llm_chunk_context: int = 2
    llm_temperature: float = 0.1

    @property
    def stale_job_timeout(self) -> int:
        """Threshold (seconds) after which a PROCESSING job is considered stale.

        Derived from ``job_timeout_max`` plus ``stale_job_buffer`` so it always
        stays consistent when the cap is tuned.
        """
        return self.job_timeout_max + self.stale_job_buffer

    @model_validator(mode="after")
    def _validate_timeout_invariants(self) -> Settings:
        """Fail-fast at startup if queue-timeout settings are inconsistent.

        Without these checks operators can set values that silently produce
        counter-intuitive behavior — e.g. ``JOB_TIMEOUT_MAX=60`` with the
        default ``JOB_TIMEOUT_FLOOR=1800`` would clamp every computed timeout
        back up to 1800 rather than the intended 60. The constraints below
        mirror what ``calculate_job_timeout`` already assumes and what
        ``stale_job_timeout`` depends on.
        """
        if self.job_timeout_floor <= 0:
            raise ValueError(f"job_timeout_floor must be > 0, got {self.job_timeout_floor}")
        if self.job_timeout_default < self.job_timeout_floor:
            raise ValueError(
                f"job_timeout_default ({self.job_timeout_default}) must be >= "
                f"job_timeout_floor ({self.job_timeout_floor})"
            )
        if self.job_timeout_max < self.job_timeout_default:
            raise ValueError(
                f"job_timeout_max ({self.job_timeout_max}) must be >= job_timeout_default ({self.job_timeout_default})"
            )
        if self.job_timeout_audio_multiplier <= 0:
            raise ValueError(f"job_timeout_audio_multiplier must be > 0, got {self.job_timeout_audio_multiplier}")
        if self.stale_job_buffer < 0:
            raise ValueError(f"stale_job_buffer must be >= 0, got {self.stale_job_buffer}")
        if self.diarize_heartbeat_interval < 0:
            raise ValueError(f"diarize_heartbeat_interval must be >= 0, got {self.diarize_heartbeat_interval}")
        # Redis progress keys must outlive the longest possible job run,
        # otherwise the UI loses progress state mid-pipeline on long jobs.
        if self.redis_processing_expiry < self.stale_job_timeout:
            raise ValueError(
                f"redis_processing_expiry ({self.redis_processing_expiry}) must be >= "
                f"stale_job_timeout ({self.stale_job_timeout}) = "
                f"job_timeout_max + stale_job_buffer"
            )
        if self.llm_chunk_size <= 0:
            raise ValueError(f"llm_chunk_size must be > 0, got {self.llm_chunk_size}")
        if self.llm_chunk_context < 0:
            raise ValueError(f"llm_chunk_context must be >= 0, got {self.llm_chunk_context}")
        if not 0.0 <= self.llm_temperature <= 2.0:
            raise ValueError(f"llm_temperature must be within [0.0, 2.0], got {self.llm_temperature}")
        if self.ollama_request_timeout <= 0:
            raise ValueError(f"ollama_request_timeout must be > 0, got {self.ollama_request_timeout}")
        return self


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    from whisper_ui.core.device import detect_device, validate_compute_type

    settings = Settings()
    resolved_device = detect_device(settings.device)
    resolved_compute = validate_compute_type(resolved_device, settings.compute_type)
    return settings.model_copy(update={"device": resolved_device, "compute_type": resolved_compute})
