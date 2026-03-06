from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0.0,
    progress_message TEXT DEFAULT '',
    language TEXT NOT NULL DEFAULT 'zh',
    num_speakers INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    result_path TEXT,
    duration REAL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
