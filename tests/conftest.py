from __future__ import annotations

from pathlib import Path

import pytest

from whisper_ui.core.config import Settings
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def settings(tmp_dir: Path) -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        database_path=tmp_dir / "test.db",
        upload_dir=tmp_dir / "uploads",
        output_dir=tmp_dir / "outputs",
        device="cpu",
    )


@pytest.fixture
def db(settings: Settings) -> JobDatabase:
    database = JobDatabase(settings.database_path)
    yield database
    database.close()


@pytest.fixture
def filestore(settings: Settings) -> FileStore:
    return FileStore(settings.upload_dir, settings.output_dir)
