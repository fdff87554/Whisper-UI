from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import pytest

from whisper_ui.core.config import Settings
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

    from whisper_ui.storage.users_repo import User


# Stable, clearly-not-real session signing key. The same value is used to
# sign manufactured session cookies in the helpers below, so it must match
# the SESSION_SECRET env var that `_test_session_secret` injects.
TEST_SESSION_SECRET = "test-session-secret-not-real-do-not-reuse"


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


@pytest.fixture(autouse=True)
def _test_session_secret(monkeypatch):
    """Pin SESSION_SECRET so manufactured cookies share the app's signing key.

    create_app() reads SESSION_SECRET via get_settings(); if it isn't set
    a random ephemeral key is used per-process, which would make the
    cookies built by :func:`make_session_cookie` un-verifiable. Setting
    it here keeps both ends in lockstep.
    """
    from whisper_ui.core.config import get_settings

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def test_user(db: JobDatabase) -> User:
    """A non-admin user fixture used by the default `client`.

    Creates the row through ``users_repo`` so the password hash is
    produced by the same argon2 configuration the login flow uses
    (cheap, via :func:`_fast_argon2`).
    """
    from whisper_ui.storage import users_repo

    return users_repo.create_user(db.conn, "alice", "password123", is_admin=False)


@pytest.fixture
def test_admin(db: JobDatabase) -> User:
    """An admin user fixture used by `admin_client`."""
    from whisper_ui.storage import users_repo

    return users_repo.create_user(db.conn, "root", "password123", is_admin=True)


def make_session_cookie(user: User) -> str:
    """Build a session cookie value Starlette's SessionMiddleware will accept.

    SessionMiddleware encodes the session as ``base64(json(payload))``
    then signs it with :class:`itsdangerous.TimestampSigner` using the
    configured ``secret_key``. Replicating that here lets tests skip the
    real /login round-trip when the route under test isn't login itself.
    """
    from itsdangerous import TimestampSigner

    payload = {"uid": user.id, "sv": user.session_version}
    data = base64.b64encode(json.dumps(payload).encode("utf-8"))
    signer = TimestampSigner(TEST_SESSION_SECRET)
    return signer.sign(data).decode("utf-8")


def authed_test_client(app: FastAPI, user: User):
    """A TestClient pre-loaded with a session cookie and an Origin header.

    The Origin header is required because :class:`AuthMiddleware` enforces
    CSRF on POST/PUT/PATCH/DELETE by comparing Origin to Host. Starlette's
    TestClient uses ``http://testserver`` as its default base URL.
    """
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("session", make_session_cookie(user))
    client.headers["Origin"] = "http://testserver"
    return client
