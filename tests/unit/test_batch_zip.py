"""Tests for the batch export ZIP builder."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.web.batch_zip import _zip_entry_base, create_batch_zip


def _result() -> TranscriptResult:
    return TranscriptResult(segments=[Segment(start=0.0, end=1.0, text="hi")], language="zh", duration=1.0)


def _filestore(results: dict[str, TranscriptResult | None]) -> MagicMock:
    fs = MagicMock()
    fs.load_result.side_effect = lambda job_id: results.get(job_id)
    return fs


def test_zip_entry_base_uses_filename_stem_for_uploads():
    job = Job(id="abc", filename="meeting notes.mp3")
    assert _zip_entry_base(job) == "meeting notes"


def test_zip_entry_base_falls_back_to_job_id_for_url_jobs():
    job = Job(id="job123", filename="watch?v=xyz", source_url="https://youtu.be/xyz")
    assert _zip_entry_base(job) == "job123"


def test_create_batch_zip_returns_none_when_no_completed_results():
    jobs = [Job(id="j1", filename="a.mp3", status=JobStatus.FAILED)]
    assert create_batch_zip(jobs, _filestore({}), "srt") is None


def test_create_batch_zip_skips_non_completed_and_missing_results():
    jobs = [
        Job(id="done", filename="a.mp3", status=JobStatus.COMPLETED),
        Job(id="processing", filename="b.mp3", status=JobStatus.PROCESSING),
        Job(id="no_result", filename="c.mp3", status=JobStatus.COMPLETED),
    ]
    fs = _filestore({"done": _result(), "no_result": None})

    data = create_batch_zip(jobs, fs, "srt")

    assert data is not None
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == ["a.srt"]


def test_create_batch_zip_deduplicates_colliding_filenames():
    jobs = [
        Job(id="j1", filename="same.mp3", status=JobStatus.COMPLETED),
        Job(id="j2", filename="same.mp3", status=JobStatus.COMPLETED),
    ]
    fs = _filestore({"j1": _result(), "j2": _result()})

    data = create_batch_zip(jobs, fs, "txt")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["same (1).txt", "same.txt"]
