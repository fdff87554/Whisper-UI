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


def test_zip_entry_base_strips_path_separators_from_uploads():
    # PurePosixPath.name leaves backslashes intact on POSIX; they must not
    # survive into the ZIP entry where a Windows extractor could treat them
    # as separators (Zip-Slip).
    job = Job(id="abc", filename="..\\..\\evil.srt")
    base = _zip_entry_base(job)
    assert "\\" not in base
    assert "/" not in base
    assert not base.startswith(".")


def test_zip_entry_base_preserves_cjk_filenames():
    job = Job(id="abc", filename="我的會議.mp3")
    assert _zip_entry_base(job) == "我的會議"


def test_zip_entry_base_falls_back_to_job_id_when_sanitised_empty():
    job = Job(id="onlydots", filename="....mp3")
    assert _zip_entry_base(job) == "onlydots"
