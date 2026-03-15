from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from whisper_ui.core.constants import MAX_BATCH_SIZE
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.database import JobDatabase


def test_job_batch_id_default():
    job = Job()
    assert job.batch_id is None


def test_job_batch_id_set():
    bid = uuid.uuid4().hex
    job = Job(batch_id=bid)
    assert job.batch_id == bid


def test_insert_and_get_job_with_batch_id(db: JobDatabase):
    bid = uuid.uuid4().hex
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3", batch_id=bid)
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.batch_id == bid


def test_insert_job_without_batch_id(db: JobDatabase):
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3")
    db.insert_job(job)
    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.batch_id is None


def test_list_jobs_by_batch(db: JobDatabase):
    bid = uuid.uuid4().hex
    for i in range(3):
        db.insert_job(Job(filename=f"file{i}.mp3", filepath=f"/tmp/file{i}.mp3", batch_id=bid))
    # Insert a job without batch_id — should not appear in batch query
    db.insert_job(Job(filename="solo.mp3", filepath="/tmp/solo.mp3"))

    batch_jobs = db.list_jobs_by_batch(bid)
    assert len(batch_jobs) == 3
    assert all(j.batch_id == bid for j in batch_jobs)


def test_list_jobs_by_batch_ordering(db: JobDatabase):
    bid = uuid.uuid4().hex
    names = ["first.mp3", "second.mp3", "third.mp3"]
    for name in names:
        db.insert_job(Job(filename=name, filepath=f"/tmp/{name}", batch_id=bid))

    batch_jobs = db.list_jobs_by_batch(bid)
    assert [j.filename for j in batch_jobs] == names


def test_list_jobs_by_batch_empty(db: JobDatabase):
    batch_jobs = db.list_jobs_by_batch("nonexistent")
    assert batch_jobs == []


def test_max_batch_size_is_positive():
    assert MAX_BATCH_SIZE > 0


def test_folder_upload_basename_extraction():
    """Folder uploads include directory prefixes that must be stripped."""
    assert PurePosixPath("subfolder/audio.mp3").name == "audio.mp3"
    assert PurePosixPath("a/b/c/recording.wav").name == "recording.wav"


def test_flat_file_basename_unchanged():
    """Plain filenames without directory prefixes are unaffected by basename extraction."""
    assert PurePosixPath("audio.mp3").name == "audio.mp3"
    assert PurePosixPath("my recording.wav").name == "my recording.wav"


def test_batch_id_persists_through_update(db: JobDatabase):
    bid = uuid.uuid4().hex
    job = Job(filename="test.mp3", filepath="/tmp/test.mp3", batch_id=bid)
    db.insert_job(job)

    job.status = JobStatus.COMPLETED
    db.update_job(job)

    fetched = db.get_job(job.id)
    assert fetched is not None
    assert fetched.batch_id == bid
    assert fetched.status == JobStatus.COMPLETED
