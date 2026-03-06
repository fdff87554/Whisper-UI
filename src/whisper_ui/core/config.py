from __future__ import annotations

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
    device: str = "cuda"
    batch_size: int = 4

    # Language
    language: str = "zh"

    # HuggingFace
    hf_token: str = ""


def get_settings() -> Settings:
    return Settings()
