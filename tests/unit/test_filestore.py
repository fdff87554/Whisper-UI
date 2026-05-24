from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from whisper_ui.core.models import Segment, TranscriptResult

if TYPE_CHECKING:
    from whisper_ui.storage.filestore import FileStore


def test_save_and_load_upload(filestore: FileStore):
    data = b"fake audio data"
    path = filestore.save_upload("job1", "test.mp3", data)
    assert path.exists()
    assert path.read_bytes() == data


def test_save_and_load_result(filestore: FileStore):
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.0, text="Hello", speaker="SPEAKER_00"),
            Segment(start=1.0, end=2.0, text="World"),
        ],
        language="zh",
        duration=2.0,
    )
    path = filestore.save_result("job1", result)
    assert path.exists()

    loaded = filestore.load_result("job1")
    assert loaded is not None
    assert len(loaded.segments) == 2
    assert loaded.segments[0].text == "Hello"
    assert loaded.segments[0].speaker == "SPEAKER_00"
    assert loaded.segments[1].speaker is None
    assert loaded.language == "zh"
    assert loaded.duration == 2.0


def test_load_nonexistent_result(filestore: FileStore):
    assert filestore.load_result("nonexistent") is None


def test_save_upload_sanitizes_path_traversal(filestore: FileStore):
    data = b"malicious content"
    path = filestore.save_upload("job1", "/etc/passwd", data)
    assert path.parent.name == "job1"
    assert path.name == "passwd"
    assert path.read_bytes() == data


def test_save_upload_sanitizes_relative_traversal(filestore: FileStore):
    data = b"malicious content"
    path = filestore.save_upload("job1", "../../etc/shadow", data)
    assert path.parent.name == "job1"
    assert path.name == "shadow"


def test_get_upload_path_sanitizes_filename(filestore: FileStore):
    path = filestore.get_upload_path("job1", "/etc/passwd")
    assert path.name == "passwd"
    assert "job1" in str(path)


def test_delete_job_files(filestore: FileStore):
    filestore.save_upload("job2", "test.mp3", b"data")
    result = TranscriptResult(segments=[], language="zh", duration=0.0)
    filestore.save_result("job2", result)

    filestore.delete_job_files("job2")
    assert filestore.load_result("job2") is None


def test_delete_upload_files_removes_uploads_but_keeps_result(filestore: FileStore):
    filestore.save_upload("job3", "input.mp3", b"data")
    result = TranscriptResult(segments=[], language="zh", duration=0.0)
    filestore.save_result("job3", result)

    removed = filestore.delete_upload_files("job3")
    assert removed is True
    # Upload dir is gone; the saved transcript still loads.
    assert not filestore.get_upload_path("job3", "input.mp3").exists()
    assert filestore.load_result("job3") is not None


def test_delete_upload_files_returns_false_when_nothing_to_remove(filestore: FileStore):
    assert filestore.delete_upload_files("job-does-not-exist") is False


def test_delete_job_files_logs_info_with_directory_summary(filestore: FileStore, caplog):
    import logging as _logging

    filestore.save_upload("job-log", "input.mp3", b"data")
    result = TranscriptResult(segments=[], language="zh", duration=0.0)
    filestore.save_result("job-log", result)

    with caplog.at_level(_logging.INFO, logger="whisper_ui.storage.filestore"):
        filestore.delete_job_files("job-log")

    info = next(r.getMessage() for r in caplog.records if "deleted job dirs" in r.getMessage())
    assert "job-log" in info
    assert "upload" in info
    assert "output" in info


def test_delete_upload_files_logs_debug_on_success(filestore: FileStore, caplog):
    import logging as _logging

    filestore.save_upload("job-dbg", "input.mp3", b"data")
    with caplog.at_level(_logging.DEBUG, logger="whisper_ui.storage.filestore"):
        filestore.delete_upload_files("job-dbg")

    assert any("reclaimed upload dir" in r.getMessage() and "job-dbg" in r.getMessage() for r in caplog.records)


def test_copy_source_for_new_job_copies_audio_to_new_dir(filestore: FileStore):
    data = b"original audio bytes"
    filestore.save_upload("root", "meeting.mp3", data)

    dest = filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")

    assert dest.parent.name == "version1"
    assert dest.name == "meeting.mp3"
    assert dest.read_bytes() == data


def test_copy_source_for_new_job_leaves_original_intact(filestore: FileStore):
    data = b"original audio bytes"
    src = filestore.save_upload("root", "meeting.mp3", data)

    filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")

    # The new version is an independent copy; the source job is untouched.
    assert src.exists()
    assert src.read_bytes() == data


def test_copy_source_for_new_job_independent_of_source_deletion(filestore: FileStore):
    filestore.save_upload("root", "meeting.mp3", b"data")
    filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")

    filestore.delete_job_files("root")

    # Deleting the source job does not affect the copied version.
    assert filestore.get_upload_path("version1", "meeting.mp3").exists()


def test_copy_source_for_new_job_raises_when_source_missing(filestore: FileStore):
    with pytest.raises(FileNotFoundError):
        filestore.copy_source_for_new_job("gone", "meeting.mp3", "version1")


def test_delete_job_files_raises_on_filesystem_failure(filestore: FileStore, monkeypatch):
    """Manual delete routes rely on this raising — if shutil.rmtree fails,
    the route must see the OSError and keep the DB row, not silently
    proceed to db.delete_job (which would leave files on disk while UI /
    audit log claim the job was deleted).
    """
    import shutil

    filestore.save_upload("job-fail", "input.mp3", b"data")

    def _boom(*_args, **_kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(shutil, "rmtree", _boom)

    with pytest.raises(PermissionError):
        filestore.delete_job_files("job-fail")
