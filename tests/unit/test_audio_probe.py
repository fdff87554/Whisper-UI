from __future__ import annotations

import logging
import subprocess
from unittest.mock import patch

import pytest

from whisper_ui.pipeline.audio_probe import _log_label, get_audio_duration_seconds


def _mock_run(stdout: str = "", *, returncode: int = 0):
    result = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")
    return patch("whisper_ui.pipeline.audio_probe.subprocess.run", return_value=result)


def test_returns_parsed_float():
    with _mock_run(stdout="123.45\n"):
        assert get_audio_duration_seconds("/tmp/test.wav") == 123.45


def test_returns_none_on_empty_stdout():
    with _mock_run(stdout=""):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_returns_none_on_non_numeric_output():
    with _mock_run(stdout="not-a-number"):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_returns_none_on_zero_duration():
    with _mock_run(stdout="0\n"):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_returns_none_on_negative_duration():
    with _mock_run(stdout="-1.5\n"):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_returns_none_when_ffprobe_missing():
    with patch(
        "whisper_ui.pipeline.audio_probe.subprocess.run",
        side_effect=FileNotFoundError("ffprobe"),
    ):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_returns_none_on_timeout():
    with patch(
        "whisper_ui.pipeline.audio_probe.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
    ):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_returns_none_on_permission_error():
    """PermissionError is an OSError subclass that is not FileNotFoundError;
    the helper must still honor its 'never block upload' contract.
    """
    with patch(
        "whisper_ui.pipeline.audio_probe.subprocess.run",
        side_effect=PermissionError("read access denied"),
    ):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_returns_none_on_generic_oserror():
    with patch(
        "whisper_ui.pipeline.audio_probe.subprocess.run",
        side_effect=OSError("I/O failure"),
    ):
        assert get_audio_duration_seconds("/tmp/test.wav") is None


def test_log_label_prefers_job_id_when_supplied():
    assert _log_label("/tmp/x.wav", "deadbeef") == "job=deadbeef"


def test_log_label_falls_back_to_basename_without_job_id():
    assert _log_label("/var/lib/whisper/uploads/abc/source.mp3", None) == "file=source.mp3"


def test_log_label_never_contains_absolute_path():
    label = _log_label("/secret/path/to/private/audio.wav", None)
    assert "/secret/path" not in label
    assert "audio.wav" in label


def test_timeout_log_includes_job_id_and_no_absolute_path(caplog):
    with (
        patch(
            "whisper_ui.pipeline.audio_probe.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
        ),
        caplog.at_level(logging.WARNING, logger="whisper_ui.pipeline.audio_probe"),
    ):
        get_audio_duration_seconds("/secret/x.wav", job_id="abc12345")

    record = next(r for r in caplog.records if "timeout" in r.getMessage().lower())
    msg = record.getMessage()
    assert "job=abc12345" in msg
    assert "/secret/x.wav" not in msg
    assert "ffprobe timeout" in msg


def test_file_not_found_log_distinguishes_missing_binary(caplog):
    with (
        patch(
            "whisper_ui.pipeline.audio_probe.subprocess.run",
            side_effect=FileNotFoundError("ffprobe"),
        ),
        caplog.at_level(logging.WARNING, logger="whisper_ui.pipeline.audio_probe"),
    ):
        get_audio_duration_seconds("/secret/x.wav", job_id="abc12345")

    record = next(r for r in caplog.records if "binary missing" in r.getMessage())
    assert "job=abc12345" in record.getMessage()
    assert "/secret" not in record.getMessage()


def test_generic_oserror_log_includes_exception_class(caplog):
    with (
        patch(
            "whisper_ui.pipeline.audio_probe.subprocess.run",
            side_effect=PermissionError("denied"),
        ),
        caplog.at_level(logging.WARNING, logger="whisper_ui.pipeline.audio_probe"),
    ):
        get_audio_duration_seconds("/secret/x.wav", job_id="abc12345")

    record = next(r for r in caplog.records if "ffprobe failed" in r.getMessage())
    msg = record.getMessage()
    assert "PermissionError" in msg
    assert "job=abc12345" in msg
    assert "/secret" not in msg


@pytest.mark.parametrize(
    ("call_kwargs", "expected_label_fragment"),
    [
        ({}, "file=x.wav"),
        ({"job_id": "jid123"}, "job=jid123"),
    ],
)
def test_label_selection_uses_kwarg(call_kwargs, expected_label_fragment, caplog):
    with (
        patch(
            "whisper_ui.pipeline.audio_probe.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ),
        caplog.at_level(logging.WARNING, logger="whisper_ui.pipeline.audio_probe"),
    ):
        get_audio_duration_seconds("/tmp/x.wav", **call_kwargs)

    assert any(expected_label_fragment in r.getMessage() for r in caplog.records)
