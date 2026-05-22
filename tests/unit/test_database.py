from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.database import JobDatabase

if TYPE_CHECKING:
    from pathlib import Path


def test_insert_and_get(db: JobDatabase):
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.filename == "test.mp3"
    assert fetched.status == JobStatus.PENDING


def test_insert_and_get_preserves_owner_id(db: JobDatabase):
    job = Job(filename="owned.mp3", filepath="/tmp/owned.mp3", owner_id=42)
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.owner_id == 42


def test_legacy_job_without_owner_id_is_stored_as_null(db: JobDatabase):
    """Jobs created without an owner (legacy data) survive the round-trip as None."""
    job = Job(filename="legacy.mp3", filepath="/tmp/legacy.mp3")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.owner_id is None


def test_get_nonexistent(db: JobDatabase):
    assert db.get_job("nonexistent") is None


def test_list_jobs(db: JobDatabase):
    for i in range(3):
        db.insert_job(Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3"))
    jobs = db.list_jobs()
    assert len(jobs) == 3


def test_update_job(db: JobDatabase):
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    db.insert_job(job)

    job.status = JobStatus.COMPLETED
    job.progress = 1.0
    db.update_job(job)

    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.COMPLETED
    assert fetched.progress == 1.0


def test_delete_job(db: JobDatabase):
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    db.insert_job(job)
    db.delete_job(job.id)
    assert db.get_job(job.id) is None


def test_list_jobs_with_limit(db: JobDatabase):
    for i in range(5):
        db.insert_job(Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3"))
    jobs = db.list_jobs(limit=2)
    assert len(jobs) == 2


def test_row_to_job_ignores_unknown_db_fields(db: JobDatabase):
    """Simulate a version mismatch where the DB has columns the Job dataclass doesn't know about."""
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    db.insert_job(job)

    # Add an unknown column to the jobs table (simulates a newer frontend schema)
    db._conn.execute("ALTER TABLE jobs ADD COLUMN future_field TEXT DEFAULT 'hello'")
    db._conn.commit()

    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.filename == "test.mp3"
    assert fetched.status == JobStatus.PENDING
    assert not hasattr(fetched, "future_field")


def test_list_jobs_ignores_unknown_db_fields(db: JobDatabase):
    """Ensure list_jobs also handles unknown DB columns gracefully."""
    db.insert_job(Job(filename="a.mp3", filepath="/tmp/a.mp3"))
    db.insert_job(Job(filename="b.mp3", filepath="/tmp/b.mp3"))

    db._conn.execute("ALTER TABLE jobs ADD COLUMN future_field TEXT DEFAULT 'x'")
    db._conn.commit()

    jobs = db.list_jobs()
    assert len(jobs) == 2
    assert all(j.filename in ("a.mp3", "b.mp3") for j in jobs)


def test_count_jobs_total(db: JobDatabase):
    for i in range(3):
        db.insert_job(Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3"))
    assert db.count_jobs() == 3


def test_count_jobs_by_status(db: JobDatabase):
    for i in range(3):
        job = Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3")
        if i == 2:
            job.status = JobStatus.COMPLETED
        db.insert_job(job)

    assert db.count_jobs(status="pending") == 2
    assert db.count_jobs(status="completed") == 1
    assert db.count_jobs(status="failed") == 0


def test_list_jobs_filtered_no_filter(db: JobDatabase):
    for i in range(5):
        db.insert_job(Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3"))
    jobs = db.list_jobs_filtered(limit=3, offset=0)
    assert len(jobs) == 3
    jobs_page2 = db.list_jobs_filtered(limit=3, offset=3)
    assert len(jobs_page2) == 2


def test_list_jobs_filtered_by_status(db: JobDatabase):
    for i in range(4):
        job = Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3")
        if i % 2 == 0:
            job.status = JobStatus.COMPLETED
        db.insert_job(job)

    completed = db.list_jobs_filtered(status="completed", limit=10)
    assert len(completed) == 2
    assert all(j.status == JobStatus.COMPLETED for j in completed)

    pending = db.list_jobs_filtered(status="pending", limit=10)
    assert len(pending) == 2
    assert all(j.status == JobStatus.PENDING for j in pending)


def test_recover_stale_jobs(db: JobDatabase):
    stale = Job(filename="stale.mp3", filepath="/tmp/stale.mp3")
    stale.status = JobStatus.PROCESSING
    db.insert_job(stale)

    # Backdate updated_at to simulate a stale job
    db._conn.execute(
        "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (stale.id,),
    )
    db._conn.commit()

    fresh = Job(filename="fresh.mp3", filepath="/tmp/fresh.mp3")
    fresh.status = JobStatus.PROCESSING
    db.insert_job(fresh)

    recovered = db.recover_stale_jobs(timeout_seconds=60, error_message="timeout")
    assert recovered == 1

    stale_fetched = db.get_job(stale.id)
    assert stale_fetched is not None
    assert stale_fetched.status == JobStatus.FAILED
    assert stale_fetched.error == "timeout"

    fresh_fetched = db.get_job(fresh.id)
    assert fresh_fetched is not None
    assert fresh_fetched.status == JobStatus.PROCESSING


def test_recover_stale_jobs_logs_warning_with_ids(db: JobDatabase, caplog):
    import logging as _logging

    stale = Job(filename="stale.mp3", filepath="/tmp/stale.mp3")
    stale.status = JobStatus.PROCESSING
    db.insert_job(stale)
    db._conn.execute(
        "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (stale.id,),
    )
    db._conn.commit()

    with caplog.at_level(_logging.WARNING, logger="whisper_ui.storage.database"):
        db.recover_stale_jobs(timeout_seconds=60, error_message="timeout")

    msg = next(r.getMessage() for r in caplog.records if "stale recovery marked" in r.getMessage())
    assert stale.id in msg
    assert "1 job(s)" in msg


def test_recover_stale_jobs_silent_when_no_candidates(db: JobDatabase, caplog):
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="whisper_ui.storage.database"):
        recovered = db.recover_stale_jobs(timeout_seconds=60, error_message="timeout")

    assert recovered == 0
    assert not any("stale recovery" in r.getMessage() for r in caplog.records)


def test_has_active_jobs_empty(db: JobDatabase):
    assert db.has_active_jobs() is False


def test_has_active_jobs_only_terminal(db: JobDatabase):
    completed = Job(filename="done.mp3", filepath="/tmp/done.mp3")
    completed.status = JobStatus.COMPLETED
    db.insert_job(completed)

    failed = Job(filename="err.mp3", filepath="/tmp/err.mp3")
    failed.status = JobStatus.FAILED
    db.insert_job(failed)

    assert db.has_active_jobs() is False


def test_has_active_jobs_with_queued(db: JobDatabase):
    queued = Job(filename="q.mp3", filepath="/tmp/q.mp3")
    queued.status = JobStatus.QUEUED
    db.insert_job(queued)

    assert db.has_active_jobs() is True


def test_has_active_jobs_with_processing(db: JobDatabase):
    processing = Job(filename="p.mp3", filepath="/tmp/p.mp3")
    processing.status = JobStatus.PROCESSING
    db.insert_job(processing)

    assert db.has_active_jobs() is True


def test_llm_correction_enabled_roundtrip(db: JobDatabase):
    job = Job(filename="t.mp3", filepath="/tmp/t.mp3", llm_correction_enabled=True)
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.llm_correction_enabled is True


def test_llm_correction_enabled_defaults_false(db: JobDatabase):
    job = Job(filename="t.mp3", filepath="/tmp/t.mp3")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.llm_correction_enabled is False


def test_legacy_db_without_llm_column_upgrades(tmp_dir: Path):
    """Simulate a pre-upgrade database with the older schema (no llm_correction_enabled)
    and verify that init_db adds the column with DEFAULT 0, without losing existing rows."""
    db_path = tmp_dir / "legacy.db"
    legacy_schema = """
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
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        error TEXT,
        result_path TEXT,
        duration REAL,
        batch_id TEXT,
        source_url TEXT
    );
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO jobs (id, filename, filepath, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy-1", "old.mp3", "/tmp/old.mp3", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    database = JobDatabase(db_path)
    try:
        fetched = database.get_job("legacy-1")
        assert fetched is not None
        assert fetched.filename == "old.mp3"
        assert fetched.llm_correction_enabled is False
    finally:
        database.close()


def test_legacy_db_without_owner_id_column_upgrades(tmp_dir: Path):
    """A pre-auth deployment's DB does not have owner_id. After init_db
    migrates the schema, the column exists, existing rows have NULL,
    and new inserts respect the owner_id value provided.
    """
    db_path = tmp_dir / "legacy_owner.db"
    legacy_schema = """
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
        source_url TEXT
    );
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO jobs (id, filename, filepath, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy-2", "old.mp3", "/tmp/old.mp3", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    database = JobDatabase(db_path)
    try:
        # Legacy row still readable and owner_id is None.
        legacy = database.get_job("legacy-2")
        assert legacy is not None
        assert legacy.owner_id is None

        # New inserts with owner_id work.
        owned = Job(filename="new.mp3", filepath="/tmp/new.mp3", owner_id=7)
        database.insert_job(owned)
        fetched = database.get_job(owned.id)
        assert fetched is not None
        assert fetched.owner_id == 7
    finally:
        database.close()


def test_get_job_with_owner_id_returns_job_when_owner_matches(db: JobDatabase):
    job = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    db.insert_job(job)

    assert db.get_job(job.id, owner_id=1) is not None


def test_get_job_with_owner_id_returns_none_when_owner_differs(db: JobDatabase):
    job = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    db.insert_job(job)

    assert db.get_job(job.id, owner_id=2) is None


def test_get_job_with_owner_id_does_not_match_legacy_null_owner(db: JobDatabase):
    """Legacy jobs (owner_id IS NULL) must not be visible to per-user lookups."""
    legacy = Job(filename="legacy.mp3", filepath="/tmp/legacy.mp3")  # owner_id defaults to None
    db.insert_job(legacy)

    assert db.get_job(legacy.id, owner_id=1) is None
    # Admin view (no owner filter) still sees the legacy job.
    assert db.get_job(legacy.id) is not None


def test_list_jobs_filtered_with_owner_excludes_other_owners(db: JobDatabase):
    db.insert_job(Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1))
    db.insert_job(Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=2))
    db.insert_job(Job(filename="c.mp3", filepath="/tmp/c.mp3"))  # legacy NULL

    user1_jobs = db.list_jobs_filtered(limit=10, owner_id=1)
    admin_jobs = db.list_jobs_filtered(limit=10)

    assert [j.filename for j in user1_jobs] == ["a.mp3"]
    assert {j.filename for j in admin_jobs} == {"a.mp3", "b.mp3", "c.mp3"}


def test_count_jobs_with_owner_excludes_other_owners(db: JobDatabase):
    db.insert_job(Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1))
    db.insert_job(Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=2))

    assert db.count_jobs(owner_id=1) == 1
    assert db.count_jobs() == 2


def test_count_jobs_combines_status_and_owner_filter(db: JobDatabase):
    completed = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    completed.status = JobStatus.COMPLETED
    db.insert_job(completed)
    db.insert_job(Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=1))  # PENDING

    assert db.count_jobs(status=JobStatus.COMPLETED.value, owner_id=1) == 1
    assert db.count_jobs(owner_id=1) == 2


def test_get_status_counts_with_owner_only_counts_user_jobs(db: JobDatabase):
    job_user = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    job_user.status = JobStatus.COMPLETED
    db.insert_job(job_user)
    job_other = Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=2)
    job_other.status = JobStatus.COMPLETED
    db.insert_job(job_other)

    assert db.get_status_counts(owner_id=1) == {JobStatus.COMPLETED.value: 1}
    assert db.get_status_counts()[JobStatus.COMPLETED.value] == 2


def test_count_completed_since_with_owner(db: JobDatabase):
    mine = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    mine.status = JobStatus.COMPLETED
    db.insert_job(mine)
    theirs = Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=2)
    theirs.status = JobStatus.COMPLETED
    db.insert_job(theirs)

    long_ago = "2000-01-01T00:00:00+00:00"
    assert db.count_completed_since(long_ago, owner_id=1) == 1
    assert db.count_completed_since(long_ago) == 2


def test_has_active_jobs_with_owner(db: JobDatabase):
    user_queued = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1)
    user_queued.status = JobStatus.QUEUED
    db.insert_job(user_queued)
    other_queued = Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=2)
    other_queued.status = JobStatus.QUEUED
    db.insert_job(other_queued)

    assert db.has_active_jobs(owner_id=1) is True
    assert db.has_active_jobs(owner_id=999) is False


def test_list_jobs_by_batch_with_owner_filters_out_other_users(db: JobDatabase):
    db.insert_job(Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1, batch_id="b1"))
    db.insert_job(Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=2, batch_id="b1"))

    user_jobs = db.list_jobs_by_batch("b1", owner_id=1)
    all_jobs = db.list_jobs_by_batch("b1")

    assert [j.filename for j in user_jobs] == ["a.mp3"]
    assert len(all_jobs) == 2


def test_get_batch_stats_with_owner(db: JobDatabase):
    mine = Job(filename="a.mp3", filepath="/tmp/a.mp3", owner_id=1, batch_id="b1")
    mine.status = JobStatus.COMPLETED
    db.insert_job(mine)
    theirs = Job(filename="b.mp3", filepath="/tmp/b.mp3", owner_id=2, batch_id="b1")
    theirs.status = JobStatus.COMPLETED
    db.insert_job(theirs)

    user_stats = db.get_batch_stats({"b1"}, owner_id=1)
    admin_stats = db.get_batch_stats({"b1"})

    assert user_stats["b1"]["completed"] == 1
    assert user_stats["b1"]["total"] == 1
    assert admin_stats["b1"]["completed"] == 2


def test_recover_stale_jobs_ignores_non_processing(db: JobDatabase):
    queued = Job(filename="queued.mp3", filepath="/tmp/queued.mp3")
    queued.status = JobStatus.QUEUED
    db.insert_job(queued)

    db._conn.execute(
        "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (queued.id,),
    )
    db._conn.commit()

    recovered = db.recover_stale_jobs(timeout_seconds=60, error_message="timeout")
    assert recovered == 0

    fetched = db.get_job(queued.id)
    assert fetched is not None
    assert fetched.status == JobStatus.QUEUED


def test_recover_stale_jobs_concurrent_workers_dont_double_recover(tmp_path: Path):
    """Two workers calling recover_stale_jobs at the same instant must not
    double-recover the same job. SQLite WAL serializes writers; once the
    first commit lands, the bumped updated_at takes the row out of the
    WHERE clause for every subsequent recovery call.
    """
    import threading

    db_path = tmp_path / "concurrent_recover.db"
    seed = JobDatabase(db_path)
    try:
        # Seed 5 stale PROCESSING jobs.
        ids = []
        for i in range(5):
            job = Job(filename=f"stale{i}.mp3", filepath=f"/tmp/stale{i}.mp3")
            job.status = JobStatus.PROCESSING
            seed.insert_job(job)
            ids.append(job.id)
        seed._conn.execute(
            "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id IN ({})".format(
                ",".join("?" * len(ids))
            ),
            ids,
        )
        seed._conn.commit()
    finally:
        seed.close()

    rowcounts: list[int] = []
    rowcounts_lock = threading.Lock()
    barrier = threading.Barrier(3)

    def worker():
        local = JobDatabase(db_path)
        try:
            barrier.wait()
            count = local.recover_stale_jobs(timeout_seconds=60, error_message="timeout")
            with rowcounts_lock:
                rowcounts.append(count)
        finally:
            local.close()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Sum of recovered rowcounts equals the number of distinct stale jobs;
    # no row gets recovered twice.
    assert sum(rowcounts) == 5

    # All 5 jobs are now FAILED — verify via a fresh connection.
    verify = JobDatabase(db_path)
    try:
        for job_id in ids:
            row = verify.get_job(job_id)
            assert row is not None
            assert row.status == JobStatus.FAILED
    finally:
        verify.close()


def test_list_terminal_job_ids_older_than_defaults_to_completed_only(db: JobDatabase):
    """The default retention sweep must skip FAILED jobs so the retry
    button still works after the upload window ages out."""
    now = datetime.now(UTC)
    old_iso = (now - timedelta(days=30)).isoformat()

    old_completed = Job(filename="oc.mp3", filepath="/tmp/oc.mp3", status=JobStatus.COMPLETED)
    old_failed = Job(filename="of.mp3", filepath="/tmp/of.mp3", status=JobStatus.FAILED)
    recent_completed = Job(filename="rc.mp3", filepath="/tmp/rc.mp3", status=JobStatus.COMPLETED)
    old_processing = Job(filename="op.mp3", filepath="/tmp/op.mp3", status=JobStatus.PROCESSING)
    for job in (old_completed, old_failed, recent_completed, old_processing):
        db.insert_job(job)

    db._conn.execute(
        "UPDATE jobs SET updated_at = ? WHERE id IN (?, ?, ?)",
        (old_iso, old_completed.id, old_failed.id, old_processing.id),
    )
    db._conn.commit()

    threshold = (now - timedelta(days=7)).isoformat()
    expired = set(db.list_terminal_job_ids_older_than(threshold))

    # FAILED is preserved (retry depends on its upload); only old COMPLETED qualifies.
    assert expired == {old_completed.id}


def test_list_terminal_job_ids_older_than_accepts_explicit_statuses(db: JobDatabase):
    """An admin sweep can opt into reclaiming FAILED jobs too by
    passing an explicit statuses tuple."""
    now = datetime.now(UTC)
    old_iso = (now - timedelta(days=30)).isoformat()

    old_completed = Job(filename="oc.mp3", filepath="/tmp/oc.mp3", status=JobStatus.COMPLETED)
    old_failed = Job(filename="of.mp3", filepath="/tmp/of.mp3", status=JobStatus.FAILED)
    db.insert_job(old_completed)
    db.insert_job(old_failed)
    db._conn.execute(
        "UPDATE jobs SET updated_at = ? WHERE id IN (?, ?)",
        (old_iso, old_completed.id, old_failed.id),
    )
    db._conn.commit()

    threshold = (now - timedelta(days=7)).isoformat()
    expired = set(
        db.list_terminal_job_ids_older_than(
            threshold,
            statuses=(JobStatus.COMPLETED.value, JobStatus.FAILED.value),
        )
    )

    assert expired == {old_completed.id, old_failed.id}


def test_list_terminal_job_ids_older_than_returns_empty_when_threshold_in_past(db: JobDatabase):
    db.insert_job(Job(filename="f.mp3", filepath="/tmp/f.mp3", status=JobStatus.COMPLETED))
    far_past = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    assert db.list_terminal_job_ids_older_than(far_past) == []
