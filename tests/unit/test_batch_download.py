from __future__ import annotations

import zipfile
from io import BytesIO
from typing import TYPE_CHECKING

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.web.batch_zip import create_batch_zip

if TYPE_CHECKING:
    from whisper_ui.storage.filestore import FileStore


def _make_job(filename: str, status: JobStatus = JobStatus.COMPLETED, batch_id: str = "batch1") -> Job:
    return Job(filename=filename, filepath=f"/tmp/{filename}", status=status, batch_id=batch_id)


def _save_result(filestore: FileStore, job: Job) -> None:
    result = TranscriptResult(
        segments=[Segment(start=0.0, end=1.0, text=f"Hello from {job.filename}")],
        language="en",
        duration=1.0,
    )
    filestore.save_result(job.id, result)


def test_create_batch_zip_all_completed(filestore: FileStore):
    jobs = [_make_job("audio1.mp3"), _make_job("audio2.wav")]
    for job in jobs:
        _save_result(filestore, job)

    data = create_batch_zip(jobs, filestore, "txt")
    assert data is not None

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert len(names) == 2
        assert "audio1.txt" in names
        assert "audio2.txt" in names


def test_create_batch_zip_mixed_statuses(filestore: FileStore):
    completed = _make_job("done.mp3", status=JobStatus.COMPLETED)
    failed = _make_job("fail.mp3", status=JobStatus.FAILED)
    processing = _make_job("busy.mp3", status=JobStatus.PROCESSING)
    _save_result(filestore, completed)

    data = create_batch_zip([completed, failed, processing], filestore, "srt")
    assert data is not None

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert "done.srt" in names


def test_create_batch_zip_no_results(filestore: FileStore):
    jobs = [_make_job("missing.mp3")]
    # No result saved — load_result returns None
    data = create_batch_zip(jobs, filestore, "txt")
    assert data is None


def test_create_batch_zip_duplicate_filenames(filestore: FileStore):
    jobs = [_make_job("meeting.mp3"), _make_job("meeting.mp3"), _make_job("meeting.mp3")]
    for job in jobs:
        _save_result(filestore, job)

    data = create_batch_zip(jobs, filestore, "txt")
    assert data is not None

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = sorted(zf.namelist())
        assert len(names) == 3
        assert "meeting.txt" in names
        assert "meeting (1).txt" in names
        assert "meeting (2).txt" in names


def test_create_batch_zip_colliding_filenames(filestore: FileStore):
    """Filenames with pre-existing numeric suffixes should not collide."""
    jobs = [
        _make_job("meeting.mp3"),
        _make_job("meeting (1).mp3"),
        _make_job("meeting.mp3"),
    ]
    for job in jobs:
        _save_result(filestore, job)

    data = create_batch_zip(jobs, filestore, "txt")
    assert data is not None

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = sorted(zf.namelist())
        assert len(names) == 3
        assert "meeting (1).txt" in names
        assert "meeting (2).txt" in names
        assert "meeting.txt" in names


def test_create_batch_zip_empty_jobs(filestore: FileStore):
    data = create_batch_zip([], filestore, "txt")
    assert data is None


def test_create_batch_zip_url_job_uses_job_id_filename(filestore: FileStore):
    """URL jobs store the canonical YouTube URL as ``filename``; that produces
    ZIP entries like ``watch?v=abc.srt`` (broken on Windows tools). The
    helper must fall back to ``job.id`` for any job with ``source_url``."""
    url_job = Job(
        filename="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        filepath="/tmp/url-job",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        status=JobStatus.COMPLETED,
        batch_id="batch-url",
    )
    _save_result(filestore, url_job)

    data = create_batch_zip([url_job], filestore, "srt")
    assert data is not None

    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert len(names) == 1
        entry = names[0]
        assert entry == f"{url_job.id}.srt"
        for forbidden in ("?", "/", ":", "="):
            assert forbidden not in entry
