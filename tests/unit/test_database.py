from __future__ import annotations

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.database import JobDatabase


def test_insert_and_get(db: JobDatabase):
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.filename == "test.mp3"
    assert fetched.status == JobStatus.PENDING


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
