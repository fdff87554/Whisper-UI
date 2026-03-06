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
