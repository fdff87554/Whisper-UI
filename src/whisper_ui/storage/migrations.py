from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


# RETURNING clause requires SQLite 3.35+. recover_stale_jobs depends on it
# for atomic capture of the recovered ids (race-free vs. the older
# SELECT-then-UPDATE pattern). Failing fast on startup is preferable to
# silently downgrading to the racy fallback or producing a runtime
# OperationalError on the first stale recovery.
_MIN_SQLITE_VERSION = (3, 35, 0)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0.0,
    progress_message TEXT DEFAULT '',
    language TEXT NOT NULL DEFAULT 'zh',
    model_name TEXT NOT NULL DEFAULT 'large-v3',
    num_speakers INTEGER,
    enable_diarization INTEGER NOT NULL DEFAULT 1,
    convert_to_traditional INTEGER NOT NULL DEFAULT 1,
    llm_correction_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    result_path TEXT,
    duration REAL,
    batch_id TEXT,
    batch_title TEXT,
    source_url TEXT,
    owner_id INTEGER,
    source_job_id TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    session_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_active_admin ON users(is_active, is_admin);
"""

_MIGRATIONS: list[str] = [
    "ALTER TABLE jobs ADD COLUMN model_name TEXT NOT NULL DEFAULT 'large-v3'",
    "ALTER TABLE jobs ADD COLUMN enable_diarization INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE jobs ADD COLUMN convert_to_traditional INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE jobs ADD COLUMN batch_id TEXT",
    "ALTER TABLE jobs ADD COLUMN source_url TEXT",
    "ALTER TABLE jobs ADD COLUMN llm_correction_enabled INTEGER NOT NULL DEFAULT 0",
    # owner_id is nullable so existing deployments with pre-auth jobs migrate
    # cleanly. Legacy rows stay NULL and remain visible only via the admin
    # /admin/jobs view (route-level filters use `WHERE owner_id = ?`, which
    # never matches NULL).
    "ALTER TABLE jobs ADD COLUMN owner_id INTEGER",
    "CREATE INDEX IF NOT EXISTS idx_jobs_owner_id ON jobs(owner_id)",
    # source_job_id links re-transcribe versions back to their root job so the
    # UI can group transcript versions of the same audio. Indexed because the
    # version-grouping lookup runs whenever the viewer renders a version's
    # sibling list.
    "ALTER TABLE jobs ADD COLUMN source_job_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_jobs_source_job_id ON jobs(source_job_id)",
    # Denormalized batch display name (e.g. an expanded playlist's title).
    # Nullable: file-upload batches and pre-existing rows have none.
    "ALTER TABLE jobs ADD COLUMN batch_title TEXT",
]


def init_db(conn: sqlite3.Connection) -> None:
    _ensure_sqlite_version()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    _run_migrations(conn)


def _ensure_sqlite_version() -> None:
    """Refuse to start when libsqlite is too old for the RETURNING clause.

    The recover_stale_jobs helper uses ``UPDATE ... RETURNING id`` so the
    recovered ids in its audit log are race-free with the UPDATE itself.
    Falling back to a SELECT-then-UPDATE pattern would silently lose that
    guarantee; raising here at startup makes a too-old deployment fail
    immediately instead of surfacing the issue only after the first
    stale recovery fires (60s into uptime by default).
    """
    current = sqlite3.sqlite_version_info[:3]
    if current < _MIN_SQLITE_VERSION:
        required = ".".join(str(p) for p in _MIN_SQLITE_VERSION)
        raise RuntimeError(
            f"Whisper-UI requires SQLite >= {required} (found {sqlite3.sqlite_version}). "
            "Upgrade the host's libsqlite3 or use the project's Docker image "
            "(python:3.13-slim ships with 3.40+)."
        )


def _run_migrations(conn: sqlite3.Connection) -> None:
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
            logger.info("schema migration applied: %s", sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                logger.debug("schema migration already applied (skipped): %s", sql)
                continue
            raise
