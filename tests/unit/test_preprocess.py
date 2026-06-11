from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from whisper_ui.core.exceptions import PreprocessError
from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS, PreprocessStage


def test_supported_extensions():
    assert ".mp3" in SUPPORTED_EXTENSIONS
    assert ".wav" in SUPPORTED_EXTENSIONS
    assert ".m4a" in SUPPORTED_EXTENSIONS
    assert ".mp4" in SUPPORTED_EXTENSIONS


def test_unsupported_extension(tmp_path):
    fake_file = tmp_path / "test.xyz"
    fake_file.write_text("not audio")
    stage = PreprocessStage()
    with pytest.raises(PreprocessError, match="Unsupported file format"):
        stage.execute({"input_path": str(fake_file)})


@patch("whisper_ui.pipeline.preprocess.subprocess.run")
def test_ffmpeg_timeout_removes_partial_wav(mock_run, tmp_path):
    """audio_path is not in the context yet on this path, so the runtime
    cleanup hook cannot reach the half-written WAV — the stage itself must
    delete it, or a permanently-kept FAILED job leaks it."""
    src = tmp_path / "test.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 100)
    partial = tmp_path / "test.16k.wav"

    def run_then_timeout(cmd, **kwargs):
        partial.write_bytes(b"half-written")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    mock_run.side_effect = run_then_timeout
    stage = PreprocessStage()
    with pytest.raises(PreprocessError, match="timed out"):
        stage.execute({"input_path": str(src)})
    assert not partial.exists()


@patch("whisper_ui.pipeline.preprocess.subprocess.run")
def test_ffmpeg_failure_removes_partial_wav(mock_run, tmp_path):
    src = tmp_path / "test.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 100)
    partial = tmp_path / "test.16k.wav"

    def run_then_fail(cmd, **kwargs):
        partial.write_bytes(b"half-written")
        return MagicMock(returncode=1, stderr="boom")

    mock_run.side_effect = run_then_fail
    stage = PreprocessStage()
    with pytest.raises(PreprocessError, match="FFmpeg failed"):
        stage.execute({"input_path": str(src)})
    assert not partial.exists()


def test_preprocess_stage_name():
    stage = PreprocessStage()
    assert stage.name == "preprocess"


def test_preprocess_cleanup():
    stage = PreprocessStage()
    stage.cleanup()
