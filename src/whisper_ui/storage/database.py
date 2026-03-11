from __future__ import annotations

import sqlite3
from pathlib import Path

from whisper_ui.core.constants import DEFAULT_JOB_LIST_LIMIT, SQLITE_BUSY_TIMEOUT_MS
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.migrations import init_db

_JOB_COLUMNS = [
    "id",
    "filename",
    "filepath",
    "status",
    "progress",
    "progress_message",
    "language",
    "model_name",
    "num_speakers",
    "enable_diarization",
    "convert_to_traditional",
    "created_at",
    "updated_at",
    "error",
    "result_path",
    "duration",
]


def _row_to_job(row: sqlite3.Row) -> Job:
    d = dict(row)
    d["status"] = JobStatus(d["status"])
    d["enable_diarization"] = bool(d.get("enable_diarization", 1))
    d["convert_to_traditional"] = bool(d.get("convert_to_traditional", 1))
    return Job(**d)


class JobDatabase:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        init_db(self._conn)

    def close(self) -> None:
        self._conn.close()

    def insert_job(self, job: Job) -> None:
        placeholders = ", ".join("?" for _ in _JOB_COLUMNS)
        cols = ", ".join(_JOB_COLUMNS)
        values = [getattr(job, col) for col in _JOB_COLUMNS]
        self._conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", values)
        self._conn.commit()

    def get_job(self, job_id: str) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def list_jobs(self, *, limit: int = DEFAULT_JOB_LIST_LIMIT, offset: int = 0) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def update_job(self, job: Job) -> None:
        job.touch()
        set_clause = ", ".join(f"{col} = ?" for col in _JOB_COLUMNS if col != "id")
        values = [getattr(job, col) for col in _JOB_COLUMNS if col != "id"]
        values.append(job.id)
        self._conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    def delete_job(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()
