from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from whisper_ui.core.config import Settings
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.fixture(autouse=True)
def _fast_argon2(monkeypatch):
    """Replace the production argon2 hasher with a cheap configuration.

    Production parameters (~100ms per hash) would multiply the cost of every
    test that touches password hashing or login. Tests don't care about
    cryptographic strength — they care about correctness of the wrapper
    code — so a 1-pass, 8 KiB-memory hasher is fine.
    """
    from argon2 import PasswordHasher

    from whisper_ui.storage import users_repo

    monkeypatch.setattr(
        users_repo,
        "_hasher",
        PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=8),
    )
