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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    result_path TEXT,
    duration REAL
);
"""

_MIGRATIONS: list[str] = [
    "ALTER TABLE jobs ADD COLUMN model_name TEXT NOT NULL DEFAULT 'large-v3'",
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
        except sqlite3.OperationalError:
            pass
