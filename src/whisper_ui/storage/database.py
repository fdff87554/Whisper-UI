from __future__ import annotations

import dataclasses
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from whisper_ui.core.constants import DEFAULT_JOB_LIST_LIMIT, SQLITE_BUSY_TIMEOUT_MS
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.migrations import init_db

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

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
    "llm_correction_enabled",
    "created_at",
    "updated_at",
    "error",
    "result_path",
    "duration",
    "batch_id",
    "source_url",
    "owner_id",
]


_JOB_FIELD_NAMES = {f.name for f in dataclasses.fields(Job)}


def _job_filter(*, status: str | None, owner_id: int | None) -> tuple[list[str], list[object]]:
    """Build WHERE-clause fragments + positional params for jobs queries.

    Returned ``clauses`` are joined by the caller with ``" AND "`` so the
    same helper supports both single-filter and combined-filter callsites.
    """
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    return clauses, params


def _row_to_job(row: sqlite3.Row) -> Job:
    d = dict(row)
    unknown = d.keys() - _JOB_FIELD_NAMES
    if unknown:
        logger.warning("Job %s: ignoring unknown DB fields (version mismatch?): %s", d.get("id"), unknown)
        d = {k: v for k, v in d.items() if k in _JOB_FIELD_NAMES}
    d["status"] = JobStatus(d["status"])
    d["enable_diarization"] = bool(d.get("enable_diarization", 1))
    d["convert_to_traditional"] = bool(d.get("convert_to_traditional", 1))
    d["llm_correction_enabled"] = bool(d.get("llm_correction_enabled", 0))
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

    @property
    def conn(self) -> sqlite3.Connection:
        """Underlying SQLite connection.

        Exposed so sibling repositories (e.g. ``users_repo``) can share the
        same connection and the same ``row_factory`` / WAL configuration
        without each one having to open its own. Read-only by convention;
        callers must not close it — that is :meth:`close`'s job.
        """
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def insert_job(self, job: Job) -> None:
        placeholders = ", ".join("?" for _ in _JOB_COLUMNS)
        cols = ", ".join(_JOB_COLUMNS)
        values = [getattr(job, col) for col in _JOB_COLUMNS]
        self._conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", values)
        self._conn.commit()

    def get_job(self, job_id: str, *, owner_id: int | None = None) -> Job | None:
        """Fetch a job by id.

        When ``owner_id`` is supplied, the row is returned only if its
        ``owner_id`` column equals the argument — used by route handlers to
        404 (rather than 403) cross-user access attempts, which avoids
        leaking job existence. Pass ``None`` (default) to skip the filter,
        as the admin views and system-level callers do.
        """
        if owner_id is not None:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND owner_id = ?",
                (job_id, owner_id),
            ).fetchone()
        else:
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

    def count_jobs(self, *, status: str | None = None, owner_id: int | None = None) -> int:
        """Count jobs, optionally restricted by status and/or owner.

        ``owner_id=None`` disables the owner filter (admin views and system
        callers); ``WHERE owner_id = ?`` never matches NULL rows so legacy
        pre-auth jobs are invisible to per-user counts.
        """
        clauses, params = _job_filter(status=status, owner_id=owner_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._conn.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()
        return row[0]

    def list_jobs_filtered(
        self,
        *,
        status: str | None = None,
        limit: int,
        offset: int = 0,
        owner_id: int | None = None,
    ) -> list[Job]:
        """List jobs filtered by status and/or owner, newest first.

        See :meth:`count_jobs` for the ``owner_id`` semantics — passing
        ``None`` returns rows for any owner (admin view).
        """
        clauses, params = _job_filter(status=status, owner_id=owner_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def recover_stale_jobs(self, timeout_seconds: int, error_message: str) -> int:
        """Mark PROCESSING jobs whose updated_at is older than the timeout as FAILED.

        Concurrency contract: this is a single UPDATE statement with a WHERE
        clause gated on ``updated_at < threshold``. SQLite WAL mode allows
        many concurrent readers but serializes writers, and the configured
        busy_timeout (SQLITE_BUSY_TIMEOUT_MS) makes the second writer block
        instead of failing. Once the first writer commits, every recovered
        row's ``updated_at`` has been bumped to ``datetime.now(UTC)``, so
        the second writer's WHERE clause no longer matches those rows and
        its UPDATE is a no-op for them. Two workers calling this method at
        the same instant therefore cannot double-recover the same job; the
        sum of their rowcounts equals the number of distinct stale jobs.
        See ``test_recover_stale_jobs_concurrent_workers_dont_double_recover``.
        """
        threshold = (datetime.now(UTC) - timedelta(seconds=timeout_seconds)).isoformat()
        cursor = self._conn.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE status = ? AND updated_at < ?",
            (
                JobStatus.FAILED.value,
                error_message,
                datetime.now(UTC).isoformat(),
                JobStatus.PROCESSING.value,
                threshold,
            ),
        )
        self._conn.commit()
        return cursor.rowcount

    def update_job(self, job: Job) -> None:
        job.touch()
        set_clause = ", ".join(f"{col} = ?" for col in _JOB_COLUMNS if col != "id")
        values = [getattr(job, col) for col in _JOB_COLUMNS if col != "id"]
        values.append(job.id)
        self._conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    def list_jobs_by_batch(self, batch_id: str, *, owner_id: int | None = None) -> list[Job]:
        if owner_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE batch_id = ? AND owner_id = ? ORDER BY created_at ASC",
                (batch_id, owner_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at ASC",
                (batch_id,),
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def get_batch_stats(self, batch_ids: set[str], *, owner_id: int | None = None) -> dict[str, dict]:
        """Return aggregate stats per batch using a single query.

        Pass ``owner_id`` to scope stats to one user; ``None`` (default) is
        the admin / system view that aggregates across all owners.
        """
        if not batch_ids:
            return {}
        placeholders = ", ".join("?" for _ in batch_ids)
        owner_clause = " AND owner_id = ?" if owner_id is not None else ""
        owner_params = (owner_id,) if owner_id is not None else ()
        rows = self._conn.execute(
            f"""
            SELECT batch_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS failed
            FROM jobs
            WHERE batch_id IN ({placeholders}){owner_clause}
            GROUP BY batch_id
            """,
            (JobStatus.COMPLETED.value, JobStatus.FAILED.value, *batch_ids, *owner_params),
        ).fetchall()
        result = {}
        for row in rows:
            total = row["total"]
            completed = row["completed"]
            failed = row["failed"]
            result[row["batch_id"]] = {
                "completed": completed,
                "failed": failed,
                "total": total,
                "all_done": (completed + failed) == total,
            }
        return result

    def get_status_counts(self, *, owner_id: int | None = None) -> dict[str, int]:
        """Return a dict mapping each status value to its count.

        Pass ``owner_id`` to count only that user's jobs; ``None`` (default)
        counts across all owners.
        """
        if owner_id is not None:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM jobs WHERE owner_id = ? GROUP BY status",
                (owner_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status",
            ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = row["cnt"]
        return counts

    def count_completed_since(self, since_iso: str, *, owner_id: int | None = None) -> int:
        if owner_id is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = ? AND updated_at >= ? AND owner_id = ?",
                (JobStatus.COMPLETED.value, since_iso, owner_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = ? AND updated_at >= ?",
                (JobStatus.COMPLETED.value, since_iso),
            ).fetchone()
        return row[0]

    def has_active_jobs(self, *, owner_id: int | None = None) -> bool:
        if owner_id is not None:
            row = self._conn.execute(
                "SELECT EXISTS(SELECT 1 FROM jobs WHERE status IN (?, ?) AND owner_id = ?)",
                (JobStatus.QUEUED.value, JobStatus.PROCESSING.value, owner_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT EXISTS(SELECT 1 FROM jobs WHERE status IN (?, ?))",
                (JobStatus.QUEUED.value, JobStatus.PROCESSING.value),
            ).fetchone()
        return bool(row[0])

    def delete_job(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()

    def list_terminal_job_ids_older_than(
        self,
        threshold_iso: str,
        *,
        statuses: tuple[str, ...] = (JobStatus.COMPLETED.value,),
    ) -> list[str]:
        """Return ids of jobs in ``statuses`` whose updated_at < threshold.

        Defaults to COMPLETED only because FAILED jobs are the ones a user
        is most likely to retry — and retry currently reuses the original
        upload path, so reclaiming a FAILED job's upload would silently
        break the retry button. Callers that explicitly want both states
        (e.g. an admin sweep) can opt in by passing
        ``statuses=(JobStatus.COMPLETED.value, JobStatus.FAILED.value)``.

        Ordered by ``updated_at`` ascending so the oldest jobs come first
        (matching the spirit of a retention sweep) and ``id`` as a stable
        tie-breaker, which lets callers iterate deterministically across
        multiple sweeps without needing a reclaim flag column.
        """
        placeholders = ", ".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT id FROM jobs WHERE status IN ({placeholders}) AND updated_at < ? ORDER BY updated_at ASC, id ASC",
            (*statuses, threshold_iso),
        ).fetchall()
        return [row["id"] for row in rows]
