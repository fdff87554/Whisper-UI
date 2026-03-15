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
