from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

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
    source_url TEXT,
    owner_id INTEGER
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
]


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    _run_migrations(conn)


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
