"""User account persistence and password hashing.

Thin functional wrapper around the ``users`` table. Functions take a
:class:`sqlite3.Connection` rather than the project's :class:`JobDatabase`
so the repo can be unit-tested against an in-memory connection without
having to stand up the full DB facade.

Password hashes are produced by :class:`argon2.PasswordHasher`. The module
holds a single ``_hasher`` instance so tests may monkeypatch it with
deliberately low-cost parameters to keep the test suite fast.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterable

# Production uses argon2-cffi's safe defaults (time_cost=3, memory_cost=64MiB,
# parallelism=4). Tests monkeypatch this to a cheap configuration.
_hasher = PasswordHasher()

# Pre-computed argon2id hash used by :func:`dummy_verify` so the "username
# does not exist" code path performs roughly the same amount of work as the
# "username exists, wrong password" path. The plaintext is unknown to any
# attacker — verifying any user-supplied password against this hash will
# fail. Parameters match argon2-cffi 23.x defaults so production timing is
# representative.
_DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$T0VQ6TkOUPw4nH+9sFOUdg$hh5tGD+mY+teKZQ20rQyKcW9MYVLQDxY+PgIjDT2vNU"


class LastAdminError(ValueError):
    """Raised when an operation would leave the system with zero active admins."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    password_hash: str
    is_admin: bool
    is_active: bool
    session_version: int
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        is_admin=bool(row["is_admin"]),
        is_active=bool(row["is_active"]),
        session_version=row["session_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    is_admin: bool = False,
    is_active: bool = True,
) -> User:
    """Create a new user row and return it.

    Raises :class:`sqlite3.IntegrityError` when the username already exists.
    The unique index uses ``COLLATE NOCASE`` so ``"Alice"`` and ``"alice"``
    collide.

    Input validation (password length, username format) is the route layer's
    responsibility — this function trusts internal callers.
    """
    now = _now()
    password_hash = _hasher.hash(password)
    cur = conn.execute(
        "INSERT INTO users "
        "(username, password_hash, is_admin, is_active, session_version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (username, password_hash, int(is_admin), int(is_active), now, now),
    )
    conn.commit()
    user = get_user_by_id(conn, cur.lastrowid)
    assert user is not None  # just inserted
    return user


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> User | None:
    """Look up a user by username (case-insensitive via COLLATE NOCASE)."""
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    return _row_to_user(row) if row else None


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    return [_row_to_user(r) for r in rows]


def usernames_for(conn: sqlite3.Connection, user_ids: Iterable[int]) -> dict[int, str]:
    """Return ``{id: username}`` for the given ids in one query.

    Used by the admin jobs list to label only the owners visible on the current
    page, instead of scanning the whole users table on every poll. Unknown ids
    are simply absent from the result; an empty input returns ``{}``.
    """
    ids = list(dict.fromkeys(user_ids))  # de-dupe, preserve order
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, username FROM users WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    return {row["id"]: row["username"] for row in rows}


def count_active_admins(conn: sqlite3.Connection) -> int:
    """Return the number of users with both is_admin=1 and is_active=1."""
    row = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()
    return row[0]


def verify_password(user: User, password: str) -> bool:
    """Return True when the password matches the user's stored hash.

    Catches :class:`VerifyMismatchError` and :class:`VerificationError` so
    callers never have to wrap this in try/except; any other exception
    (e.g. a corrupt hash) propagates so it is loud in logs.
    """
    try:
        return _hasher.verify(user.password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def dummy_verify(password: str) -> None:
    """Do equivalent argon2 work for the "user does not exist" code path.

    Without this, the login endpoint's response time leaks "username exists"
    vs "username unknown" because the username-unknown branch skips the
    expensive ``_hasher.verify`` call entirely. Swallows all exceptions —
    the verification is expected to fail and the result is discarded.
    """
    with contextlib.suppress(Exception):
        _hasher.verify(_DUMMY_HASH, password)


def set_password(conn: sqlite3.Connection, user_id: int, new_password: str) -> None:
    """Replace a user's password hash and bump session_version to invalidate
    every existing session on that account.
    """
    new_hash = _hasher.hash(new_password)
    conn.execute(
        "UPDATE users SET password_hash = ?, session_version = session_version + 1, updated_at = ? WHERE id = ?",
        (new_hash, _now(), user_id),
    )
    conn.commit()


def set_active(conn: sqlite3.Connection, user_id: int, active: bool) -> None:
    """Activate or deactivate a user account.

    Deactivation also bumps ``session_version`` so existing sessions are
    invalidated immediately. Activation does not bump (no security need).

    Raises :class:`LastAdminError` when deactivating would leave the system
    without any active admin.
    """
    user = get_user_by_id(conn, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    if not active and user.is_admin and user.is_active and count_active_admins(conn) <= 1:
        raise LastAdminError("Cannot deactivate the last active admin")
    if active:
        conn.execute(
            "UPDATE users SET is_active = 1, updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET is_active = 0, session_version = session_version + 1, updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )
    conn.commit()


def set_admin(conn: sqlite3.Connection, user_id: int, admin: bool) -> None:
    """Toggle a user's admin flag.

    Raises :class:`LastAdminError` when demoting would leave the system
    without any active admin. Promotion does not bump session_version
    (the user's existing sessions are still valid; they just gain admin UI).
    """
    user = get_user_by_id(conn, user_id)
    if user is None:
        raise ValueError(f"User {user_id} not found")
    if not admin and user.is_admin and user.is_active and count_active_admins(conn) <= 1:
        raise LastAdminError("Cannot demote the last active admin")
    conn.execute(
        "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?",
        (int(admin), _now(), user_id),
    )
    conn.commit()
