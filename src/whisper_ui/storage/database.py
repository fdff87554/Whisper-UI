from __future__ import annotations

import dataclasses
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from whisper_ui.core.constants import SQLITE_BUSY_TIMEOUT_MS
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.migrations import init_db

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Cap on how many recovered job ids the stale-recovery WARNING line
# embeds. Picked so an operator can scan the list without horizontal
# scroll. The Python sample buffer in recover_stale_jobs is bounded
# to this many ids regardless of backlog size; note that SQLite's
# RETURNING clause itself still materialises every recovered row in
# temporary storage server-side before streaming them to the cursor
# (https://sqlite.org/lang_returning.html), so the practical upper
# bound on stale recovery's transient memory is set by N * sizeof(id)
# at the engine layer — fine for the UUID-only id column we request
# (~1MB at N=10K worst case), but worth knowing if the SELECT list
# ever grows beyond a single column.
_STALE_RECOVERY_LOG_SAMPLE = 20

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
    "batch_title",
    "source_url",
    "owner_id",
    "source_job_id",
    "quality_warning",
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
    """SQLite-backed job repository.

    Thread-safety contract: ``check_same_thread=False`` lets sibling
    repositories on the same event-loop thread share one connection
    (see :attr:`conn`). WAL + ``busy_timeout`` keep concurrent readers
    from blocking each other, but Python's ``sqlite3`` binding does
    **not** serialise concurrent writers on a single connection.

    The web tier owns ``app.state.db`` and uses it from the event-loop
    thread only. Any code that runs in another thread (the upload
    retention sweep is the canonical example, see
    ``web/app.py:_run_retention_sweep``) must open its own
    short-lived ``JobDatabase`` instead of borrowing the shared one.
    """

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

    def list_stale_processing_job_ids(self, timeout_seconds: int) -> list[str]:
        """Return ids of PROCESSING jobs whose updated_at is older than the timeout.

        Candidate list for the liveness-aware stale reaper
        (``worker.pipeline_dispatcher.recover_stale_pipeline_jobs``): the caller
        checks each candidate's RQ pipeline liveness and only fails the
        genuinely-dead ones, sparing jobs that are merely waiting behind a
        slow/backed-up worker. Ordered oldest-first for deterministic logs.
        """
        threshold = (datetime.now(UTC) - timedelta(seconds=timeout_seconds)).isoformat()
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE status = ? AND updated_at < ? ORDER BY updated_at ASC, id ASC",
            (JobStatus.PROCESSING.value, threshold),
        ).fetchall()
        return [row["id"] for row in rows]

    def recover_stale_jobs(self, timeout_seconds: int, error_message: str, *, only_ids: list[str] | None = None) -> int:
        """Mark PROCESSING jobs whose updated_at is older than the timeout as FAILED.

        When ``only_ids`` is given, the UPDATE is additionally restricted to
        that id set — the liveness-aware reaper passes the subset of stale
        candidates whose RQ pipeline is genuinely dead, so jobs still waiting
        in a queue are never failed. ``only_ids=[]`` is a no-op.

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
        # SQLite's RETURNING clause (3.35+) gives the recovered ids back
        # atomically with the UPDATE, so the audit log cannot drift from
        # the actual rowcount even when multiple frontends run the stale
        # checker concurrently. The startup version guard in
        # migrations._ensure_sqlite_version ensures this is always
        # available; a too-old deploy raises before init_db returns.
        #
        # The RETURNING cursor is iterated row-by-row (instead of
        # ``fetchall()``) so the Python-side sample buffer stays bounded
        # by ``_STALE_RECOVERY_LOG_SAMPLE``. SQLite still buffers every
        # recovered row server-side before any value is sent to the
        # cursor (see https://sqlite.org/lang_returning.html), so a
        # post-outage worst case where N runs into the thousands does
        # consume proportional temporary memory inside the engine —
        # but each row carries only the UUID id column we request, so
        # the practical upper bound stays well under any sane SQLite
        # cache limit. Tighter bounding would require batching the
        # UPDATE (e.g. SELECT id LIMIT N then UPDATE WHERE id IN ...)
        # and is not justified at current row sizes.
        sql = "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE status = ? AND updated_at < ?"
        params: list[object] = [
            JobStatus.FAILED.value,
            error_message,
            datetime.now(UTC).isoformat(),
            JobStatus.PROCESSING.value,
            threshold,
        ]
        if only_ids is not None:
            if not only_ids:
                return 0
            placeholders = ", ".join("?" for _ in only_ids)
            sql += f" AND id IN ({placeholders})"
            params.extend(only_ids)
        sql += " RETURNING id"
        cursor = self._conn.execute(sql, params)
        recovered = 0
        shown: list[str] = []
        for row in cursor:
            recovered += 1
            if len(shown) < _STALE_RECOVERY_LOG_SAMPLE:
                shown.append(row[0])
        self._conn.commit()
        if recovered > 0:
            tail = f" (+{recovered - len(shown)} more)" if recovered > len(shown) else ""
            logger.warning(
                "stale recovery marked %d job(s) FAILED after %ds timeout: ids=%s%s",
                recovered,
                timeout_seconds,
                shown,
                tail,
            )
        return recovered

    def update_job(self, job: Job) -> None:
        job.touch()
        set_clause = ", ".join(f"{col} = ?" for col in _JOB_COLUMNS if col != "id")
        values = [getattr(job, col) for col in _JOB_COLUMNS if col != "id"]
        values.append(job.id)
        self._conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    def update_job_progress(self, job_id: str, progress: float, message: str) -> None:
        """Persist only the progress fields, never status/result_path/error.

        The full-column :meth:`update_job` overwrites every column from an
        in-memory snapshot, which is only safe once generation gating has
        confirmed the writer owns the current attempt. During a Redis outage
        gating cannot be checked, so the progress mirror falls back to this
        targeted UPDATE: a stale writer can at worst nudge the progress number,
        never resurrect a COMPLETED row to PROCESSING or drop its result_path.
        ``updated_at`` is refreshed so an actively-reporting job is not falsely
        reaped as stale.
        """
        self._conn.execute(
            "UPDATE jobs SET progress = ?, progress_message = ?, updated_at = ? WHERE id = ?",
            (progress, message, datetime.now(UTC).isoformat(), job_id),
        )
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

    def count_completed_by_day(self, days: int = 7, *, owner_id: int | None = None) -> list[int]:
        """Return completed-job counts for the last `days` days, oldest first.

        Uses updated_at because that is when a job transitions to COMPLETED;
        created_at is the upload moment. The buckets are inclusive of the
        current calendar day in UTC. Length is exactly `days` so the
        sparkline renderer can rely on a fixed-width input.
        """
        today_utc = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        start_day = today_utc - timedelta(days=days - 1)
        params: list = [JobStatus.COMPLETED.value, start_day.isoformat()]
        sql = "SELECT substr(updated_at, 1, 10) AS day, COUNT(*) FROM jobs WHERE status = ? AND updated_at >= ?"
        if owner_id is not None:
            sql += " AND owner_id = ?"
            params.append(owner_id)
        sql += " GROUP BY day"
        rows = self._conn.execute(sql, params).fetchall()
        counts_by_day = {row[0]: row[1] for row in rows}
        result = []
        for offset in range(days):
            day = (start_day + timedelta(days=offset)).date().isoformat()
            result.append(counts_by_day.get(day, 0))
        return result

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
