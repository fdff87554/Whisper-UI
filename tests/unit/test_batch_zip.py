"""Tests for the batch export ZIP builder."""

from __future__ import annotations

from unittest.mock import MagicMock

from whisper_ui.core.models import Job, Segment, TranscriptResult
from whisper_ui.web.batch_zip import _zip_entry_base


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
