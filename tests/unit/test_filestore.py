from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.helpers.store import save_upload
from whisper_ui.core.models import Segment, TranscriptResult

if TYPE_CHECKING:
    from whisper_ui.core.config import Settings
    from whisper_ui.storage.filestore import FileStore


def test_save_and_load_upload(filestore: FileStore):
    data = b"fake audio data"
    path = save_upload(filestore, "job1", "test.mp3", data)
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


def test_load_result_ignores_directory_named_result_json(filestore: FileStore, settings: Settings):
    """A directory squatting on the result path means "no result", not a crash."""
    (settings.output_dir / "jobdir" / "result.json").mkdir(parents=True)
    assert filestore.load_result("jobdir") is None


def test_load_result_returns_none_on_truncated_json(filestore: FileStore, settings: Settings):
    """A half-written result.json degrades to "no result" instead of raising."""
    job_dir = settings.output_dir / "corrupt"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text('{"segments": [{"start": 0.0, "end"', encoding="utf-8")
    assert filestore.load_result("corrupt") is None


def test_load_result_returns_none_on_non_dict_json(filestore: FileStore, settings: Settings):
    """Valid JSON whose top level is not an object degrades to "no result", not a 500."""
    for name, payload in [("listjson", "[]"), ("strjson", '"oops"'), ("numjson", "5")]:
        job_dir = settings.output_dir / name
        job_dir.mkdir(parents=True)
        (job_dir / "result.json").write_text(payload, encoding="utf-8")
        assert filestore.load_result(name) is None


def test_load_result_returns_none_on_unexpected_segment_key(filestore: FileStore, settings: Settings):
    """A segment object with an unknown key (strict Segment(**s)) is treated as missing."""
    job_dir = settings.output_dir / "badkey"
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(
        '{"segments": [{"start": 0.0, "end": 1.0, "text": "hi", "bogus": 1}], "language": "zh"}',
        encoding="utf-8",
    )
    assert filestore.load_result("badkey") is None


def test_save_result_is_atomic_and_leaves_no_temp_file(filestore: FileStore, settings: Settings):
    """save_result renames a temp file into place, leaving no .tmp residue."""
    result = TranscriptResult(segments=[Segment(start=0.0, end=1.0, text="hi")], language="zh", duration=1.0)
    filestore.save_result("atomic", result)
    job_dir = settings.output_dir / "atomic"
    assert (job_dir / "result.json").is_file()
    assert not (job_dir / "result.json.tmp").exists()
    assert filestore.load_result("atomic") is not None


def test_get_source_media_path_finds_downloaded_video(filestore: FileStore):
    path = save_upload(filestore, "yt", "video.mp4", b"fake video bytes")
    assert filestore.get_source_media_path("yt") == path


def test_get_source_media_path_ignores_directory_matching_glob(filestore: FileStore):
    """Path.glob also matches directories; a dir named video.* must not be
    served as the downloaded media file."""
    filestore.get_upload_path("yt", "video.mp4").mkdir(parents=True)
    assert filestore.get_source_media_path("yt") is None


def test_save_upload_sanitizes_path_traversal(filestore: FileStore):
    data = b"malicious content"
    path = save_upload(filestore, "job1", "/etc/passwd", data)
    assert path.parent.name == "job1"
    assert path.name == "passwd"
    assert path.read_bytes() == data


def test_save_upload_sanitizes_relative_traversal(filestore: FileStore):
    data = b"malicious content"
    path = save_upload(filestore, "job1", "../../etc/shadow", data)
    assert path.parent.name == "job1"
    assert path.name == "shadow"


def test_get_upload_path_sanitizes_filename(filestore: FileStore):
    path = filestore.get_upload_path("job1", "/etc/passwd")
    assert path.name == "passwd"
    assert "job1" in str(path)


def test_delete_job_files(filestore: FileStore):
    save_upload(filestore, "job2", "test.mp3", b"data")
    result = TranscriptResult(segments=[], language="zh", duration=0.0)
    filestore.save_result("job2", result)

    filestore.delete_job_files("job2")
    assert filestore.load_result("job2") is None


def test_delete_upload_files_removes_uploads_but_keeps_result(filestore: FileStore):
    save_upload(filestore, "job3", "input.mp3", b"data")
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

    save_upload(filestore, "job-log", "input.mp3", b"data")
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

    save_upload(filestore, "job-dbg", "input.mp3", b"data")
    with caplog.at_level(_logging.DEBUG, logger="whisper_ui.storage.filestore"):
        filestore.delete_upload_files("job-dbg")

    assert any("reclaimed upload dir" in r.getMessage() and "job-dbg" in r.getMessage() for r in caplog.records)


def test_copy_source_for_new_job_copies_audio_to_new_dir(filestore: FileStore):
    data = b"original audio bytes"
    save_upload(filestore, "root", "meeting.mp3", data)

    dest = filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")

    assert dest.parent.name == "version1"
    assert dest.name == "meeting.mp3"
    assert dest.read_bytes() == data


def test_copy_source_for_new_job_leaves_original_intact(filestore: FileStore):
    data = b"original audio bytes"
    src = save_upload(filestore, "root", "meeting.mp3", data)

    filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")

    # The new version is an independent copy; the source job is untouched.
    assert src.exists()
    assert src.read_bytes() == data


def test_copy_source_for_new_job_independent_of_source_deletion(filestore: FileStore):
    save_upload(filestore, "root", "meeting.mp3", b"data")
    filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")

    filestore.delete_job_files("root")

    # Deleting the source job does not affect the copied version.
    assert filestore.get_upload_path("version1", "meeting.mp3").exists()


def test_copy_source_for_new_job_raises_when_source_missing(filestore: FileStore):
    with pytest.raises(FileNotFoundError):
        filestore.copy_source_for_new_job("gone", "meeting.mp3", "version1")


def test_copy_source_for_new_job_raises_when_source_is_directory(filestore: FileStore):
    """A directory at the source-audio path is "audio missing", surfaced as the
    same clear FileNotFoundError instead of an IsADirectoryError from copy2."""
    filestore.get_upload_path("root", "meeting.mp3").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        filestore.copy_source_for_new_job("root", "meeting.mp3", "version1")


def test_delete_job_files_raises_on_filesystem_failure(filestore: FileStore, monkeypatch):
    """delete_job_files must surface a filesystem error rather than swallow it,
    so the caller can react. The manual delete routes are row-first (atomic
    terminal-gated row delete, then this best-effort reclaim), so a raise here
    means the row is already gone and the route logs the orphaned files for
    cleanup — see delete_job_files' docstring and the row-first delete routes.
    """
    import shutil

    save_upload(filestore, "job-fail", "input.mp3", b"data")

    def _boom(*_args, **_kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(shutil, "rmtree", _boom)

    with pytest.raises(PermissionError):
        filestore.delete_job_files("job-fail")


def test_prepare_upload_dir_creates_and_returns_directory(filestore: FileStore):
    job_dir = filestore.prepare_upload_dir("jobdir1")
    assert job_dir.is_dir()
    # prepare_upload_path resolves to a file inside the same directory.
    assert filestore.prepare_upload_path("jobdir1", "a.mp3").parent == job_dir
