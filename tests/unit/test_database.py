from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tests.helpers.store import list_jobs
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


def test_insert_and_get_preserves_quality_warning(db: JobDatabase):
    job = Job(filename="noisy.mp3", filepath="/tmp/noisy.mp3", quality_warning="轉錄結果異常")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.quality_warning == "轉錄結果異常"


def test_get_nonexistent(db: JobDatabase):
    assert db.get_job("nonexistent") is None


def test_list_jobs(db: JobDatabase):
    for i in range(3):
        db.insert_job(Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3"))
    jobs = list_jobs(db)
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
    jobs = list_jobs(db, limit=2)
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

    jobs = list_jobs(db)
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


def test_recover_stale_jobs_log_caps_id_list_at_sample_size(db: JobDatabase, caplog):
    """PR #53 Round 2 G2: a large backlog must produce a bounded log line
    (20 ids embedded + '(+N-20 more)' tail) so an apocalyptic recovery
    sweep does not balloon the WARNING into a multi-megabyte string.
    The accurate total still goes into the message prefix.
    """
    import logging as _logging

    from whisper_ui.storage.database import _STALE_RECOVERY_LOG_SAMPLE

    backlog = _STALE_RECOVERY_LOG_SAMPLE + 5
    stale_ids: list[str] = []
    for i in range(backlog):
        job = Job(filename=f"stale-{i}.mp3", filepath=f"/tmp/{i}.mp3")
        job.status = JobStatus.PROCESSING
        db.insert_job(job)
        stale_ids.append(job.id)
    db._conn.execute(
        "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE status = ?",
        (JobStatus.PROCESSING.value,),
    )
    db._conn.commit()

    with caplog.at_level(_logging.WARNING, logger="whisper_ui.storage.database"):
        recovered = db.recover_stale_jobs(timeout_seconds=60, error_message="timeout")

    assert recovered == backlog
    msg = next(r.getMessage() for r in caplog.records if "stale recovery marked" in r.getMessage())
    assert f"{backlog} job(s)" in msg
    overflow = backlog - _STALE_RECOVERY_LOG_SAMPLE
    assert f"(+{overflow} more)" in msg
    # The ids embedded in the message are a subset of the original stale
    # set (SQLite ordering is implementation-defined for an UPDATE without
    # ORDER BY, so we only assert membership + count, not order).
    embedded = [sid for sid in stale_ids if sid in msg]
    assert len(embedded) == _STALE_RECOVERY_LOG_SAMPLE


def test_init_db_raises_when_sqlite_version_lacks_returning(monkeypatch, tmp_path):
    """PR #53 review F5: recover_stale_jobs relies on UPDATE ... RETURNING
    for race-free id capture, so a too-old libsqlite must fail at init_db
    time instead of producing an OperationalError 60 seconds later when
    the first stale recovery fires.
    """
    import sqlite3 as _sqlite3

    from whisper_ui.storage import migrations

    monkeypatch.setattr(migrations.sqlite3, "sqlite_version", "3.34.0")
    monkeypatch.setattr(migrations.sqlite3, "sqlite_version_info", (3, 34, 0))

    conn = _sqlite3.connect(tmp_path / "wont-init.db")
    try:
        with pytest.raises(RuntimeError, match=r"requires SQLite >= 3\.35\.0"):
            migrations.init_db(conn)
    finally:
        conn.close()


def test_init_db_accepts_sqlite_3_35(monkeypatch, tmp_path):
    """Boundary: exactly 3.35.0 is acceptable (RETURNING introduced)."""
    import sqlite3 as _sqlite3

    from whisper_ui.storage import migrations

    monkeypatch.setattr(migrations.sqlite3, "sqlite_version", "3.35.0")
    monkeypatch.setattr(migrations.sqlite3, "sqlite_version_info", (3, 35, 0))

    conn = _sqlite3.connect(tmp_path / "ok.db")
    try:
        # Should not raise; the underlying real sqlite is much newer so
        # the schema execution itself still works fine.
        migrations.init_db(conn)
    finally:
        conn.close()


def test_reinit_does_not_relog_index_migrations(tmp_path, caplog):
    """Migrations re-run on every connection (one per stage task). The
    IF NOT EXISTS index entries never raise, so without an existence check
    each task would emit a misleading 'migration applied' INFO line."""
    import logging as _logging
    import sqlite3 as _sqlite3

    from whisper_ui.storage import migrations

    db_path = tmp_path / "relog.db"
    conn = _sqlite3.connect(db_path)
    migrations.init_db(conn)
    conn.close()

    # The first init_db above legitimately logs the index creation; only the
    # re-run must stay silent. clear() also drops records captured when an
    # earlier test raised the global log level (caplog captures the whole
    # test, not just the at_level block).
    caplog.clear()
    conn = _sqlite3.connect(db_path)
    try:
        with caplog.at_level(_logging.INFO, logger="whisper_ui.storage.migrations"):
            migrations.init_db(conn)
    finally:
        conn.close()

    applied = [r.getMessage() for r in caplog.records if "schema migration applied" in r.getMessage()]
    assert applied == []


def test_stray_source_job_id_index_is_dropped_on_upgrade(tmp_path):
    """v2.10-v2.11 installs created idx_jobs_source_job_id; the index was
    removed from the migration list in v2.12.0 without a DROP, leaving
    upgraded deployments with an index fresh installs never get."""
    import sqlite3 as _sqlite3

    from whisper_ui.storage import migrations

    db_path = tmp_path / "stray.db"
    conn = _sqlite3.connect(db_path)
    migrations.init_db(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source_job_id ON jobs(source_job_id)")
    conn.commit()
    conn.close()

    conn = _sqlite3.connect(db_path)
    try:
        migrations.init_db(conn)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_jobs_source_job_id'"
        ).fetchone()
    finally:
        conn.close()

    assert row is None


def test_hot_path_indexes_are_created(tmp_path):
    """The polled job-list / dashboard / sweep queries get supporting indexes."""
    import sqlite3 as _sqlite3

    from whisper_ui.storage import migrations

    conn = _sqlite3.connect(tmp_path / "idx.db")
    try:
        migrations.init_db(conn)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    finally:
        conn.close()

    assert "idx_jobs_status_updated_at" in names
    assert "idx_jobs_batch_id" in names


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
        # The same migration pass also adds quality_warning; legacy rows
        # surface it as None and updates can persist a value afterwards.
        assert fetched.quality_warning is None
        fetched.quality_warning = "轉錄結果異常"
        database.update_job(fetched)
        assert database.get_job("legacy-1").quality_warning == "轉錄結果異常"
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


def test_count_completed_by_day_returns_fixed_width_bucket_list(db: JobDatabase):
    """7-day sparkline contract: result length equals days, oldest first."""
    long_ago = "2000-01-01T00:00:00+00:00"
    completed = Job(filename="old.mp3", filepath="/tmp/old.mp3")
    completed.status = JobStatus.COMPLETED
    completed.updated_at = long_ago
    db.insert_job(completed)

    buckets = db.count_completed_by_day(days=7)

    assert len(buckets) == 7
    assert all(isinstance(value, int) for value in buckets)
    assert sum(buckets) == 0  # `long_ago` falls outside the 7-day window


def test_count_completed_by_day_groups_today_under_owner(db: JobDatabase):
    """Bucket counts respect the owner_id filter and roll up by UTC day."""
    mine = Job(filename="m.mp3", filepath="/tmp/m.mp3", owner_id=1)
    mine.status = JobStatus.COMPLETED
    db.insert_job(mine)
    theirs = Job(filename="t.mp3", filepath="/tmp/t.mp3", owner_id=2)
    theirs.status = JobStatus.COMPLETED
    db.insert_job(theirs)

    mine_buckets = db.count_completed_by_day(days=7, owner_id=1)
    everyone_buckets = db.count_completed_by_day(days=7)

    assert mine_buckets[-1] == 1
    assert everyone_buckets[-1] == 2


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


def test_list_stale_processing_job_ids_returns_old_processing_only(db: JobDatabase):
    stale = Job(filename="stale.mp3", filepath="/tmp/stale.mp3")
    stale.status = JobStatus.PROCESSING
    db.insert_job(stale)
    fresh = Job(filename="fresh.mp3", filepath="/tmp/fresh.mp3")
    fresh.status = JobStatus.PROCESSING
    db.insert_job(fresh)
    queued = Job(filename="queued.mp3", filepath="/tmp/queued.mp3")
    queued.status = JobStatus.QUEUED
    db.insert_job(queued)

    # Backdate the stale PROCESSING job and the (irrelevant) QUEUED job; only
    # the old PROCESSING one is a stale-recovery candidate.
    db._conn.execute(
        "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
        (stale.id, queued.id),
    )
    db._conn.commit()

    assert db.list_stale_processing_job_ids(timeout_seconds=60) == [stale.id]


def test_recover_stale_jobs_only_ids_restricts_to_subset(db: JobDatabase):
    """``only_ids`` lets the liveness-aware reaper fail just the genuinely-dead
    subset, leaving other equally-old PROCESSING jobs untouched."""
    dead = Job(filename="dead.mp3", filepath="/tmp/dead.mp3")
    dead.status = JobStatus.PROCESSING
    db.insert_job(dead)
    alive = Job(filename="alive.mp3", filepath="/tmp/alive.mp3")
    alive.status = JobStatus.PROCESSING
    db.insert_job(alive)
    db._conn.execute(
        "UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE status = ?",
        (JobStatus.PROCESSING.value,),
    )
    db._conn.commit()

    recovered = db.recover_stale_jobs(timeout_seconds=60, error_message="dead pipeline", only_ids=[dead.id])

    assert recovered == 1
    assert db.get_job(dead.id).status == JobStatus.FAILED
    # The equally-old but live job is spared because it is not in only_ids.
    assert db.get_job(alive.id).status == JobStatus.PROCESSING


def test_recover_stale_jobs_only_ids_empty_is_noop(db: JobDatabase):
    stale = Job(filename="s.mp3", filepath="/tmp/s.mp3")
    stale.status = JobStatus.PROCESSING
    db.insert_job(stale)
    db._conn.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (stale.id,))
    db._conn.commit()

    assert db.recover_stale_jobs(timeout_seconds=60, error_message="x", only_ids=[]) == 0
    assert db.get_job(stale.id).status == JobStatus.PROCESSING


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


def test_insert_and_get_preserves_source_job_id(db: JobDatabase):
    job = Job(filename="v2.mp3", filepath="/tmp/v2.mp3", source_job_id="root-abc")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.source_job_id == "root-abc"


def test_source_job_id_defaults_to_none_for_direct_uploads(db: JobDatabase):
    job = Job(filename="root.mp3", filepath="/tmp/root.mp3")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.source_job_id is None


def test_legacy_db_without_source_job_id_column_upgrades(tmp_dir: Path):
    """A deployment predating versioning has no source_job_id column. After
    init_db migrates the schema, the column exists, legacy rows read as None,
    and new inserts persist the value.
    """
    db_path = tmp_dir / "legacy_source.db"
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
        source_url TEXT,
        owner_id INTEGER
    );
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO jobs (id, filename, filepath, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy-3", "old.mp3", "/tmp/old.mp3", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    database = JobDatabase(db_path)
    try:
        legacy = database.get_job("legacy-3")
        assert legacy is not None
        assert legacy.source_job_id is None

        version = Job(filename="v.mp3", filepath="/tmp/v.mp3", source_job_id="legacy-3")
        database.insert_job(version)
        fetched = database.get_job(version.id)
        assert fetched is not None
        assert fetched.source_job_id == "legacy-3"
    finally:
        database.close()


def test_legacy_db_without_batch_title_column_upgrades(tmp_dir: Path):
    """A deployment predating playlist batches has no batch_title column.
    After init_db migrates the schema, legacy rows read as None and new
    inserts persist the value.
    """
    db_path = tmp_dir / "legacy_batch_title.db"
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
        source_url TEXT,
        owner_id INTEGER,
        source_job_id TEXT
    );
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript(legacy_schema)
    conn.execute(
        "INSERT INTO jobs (id, filename, filepath, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("legacy-4", "old.mp3", "/tmp/old.mp3", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    database = JobDatabase(db_path)
    try:
        legacy = database.get_job("legacy-4")
        assert legacy is not None
        assert legacy.batch_title is None

        titled = Job(filename="ep1.mp4", filepath="/tmp/ep1.mp4", batch_id="b1", batch_title="Team Meetings 2026Q2")
        database.insert_job(titled)
        fetched = database.get_job(titled.id)
        assert fetched is not None
        assert fetched.batch_title == "Team Meetings 2026Q2"
    finally:
        database.close()
