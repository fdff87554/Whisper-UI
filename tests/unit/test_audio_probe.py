from __future__ import annotations

import subprocess
from unittest.mock import patch

from whisper_ui.pipeline.audio_probe import get_audio_duration_seconds


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
