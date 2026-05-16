from __future__ import annotations

from typing import TYPE_CHECKING

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
