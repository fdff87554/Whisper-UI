from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from whisper_ui.core.exceptions import TranscriptionError
from whisper_ui.pipeline.transcribe import TranscribeStage
from whisper_ui.pipeline.whispercpp_transcribe import WhisperCppTranscribeStage
from whisper_ui.worker.stage_tasks import _build_transcribe_stage

_MODULE = "whisper_ui.pipeline.whispercpp_transcribe"


def _fake_cli_writer(payload: dict):
    """Return a subprocess.run stand-in that writes ``payload`` to <-of>.json."""

    def _run(cmd, capture_output, text):
        out_prefix = cmd[cmd.index("-of") + 1]
        Path(f"{out_prefix}.json").write_text(json.dumps(payload), encoding="utf-8")
        return MagicMock(returncode=0, stderr="")

    return _run


class TestAdapter:
    def test_converts_ms_to_seconds_and_strips_text(self):
        data = {
            "result": {"language": "zh"},
            "transcription": [{"offsets": {"from": 1200, "to": 2500}, "text": "  hi  "}],
        }
        assert WhisperCppTranscribeStage._to_whisperx_result(data) == {
            "language": "zh",
            "segments": [{"start": 1.2, "end": 2.5, "text": "hi"}],
        }

    def test_handles_missing_fields(self):
        assert WhisperCppTranscribeStage._to_whisperx_result({}) == {"language": "unknown", "segments": []}


class TestExecute:
    def test_parses_json_and_sets_context_keys(self):
        stage = WhisperCppTranscribeStage(model_name="large-v3")
        payload = {
            "result": {"language": "en"},
            "transcription": [
                {"offsets": {"from": 0, "to": 3000}, "text": " Hello world"},
                {"offsets": {"from": 3000, "to": 6000}, "text": "second"},
            ],
        }
        mock_whisperx = MagicMock()
        mock_whisperx.load_audio.return_value = "AUDIO_ARRAY"
        with (
            patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m/ggml-large-v3.bin"),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(payload)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            ctx = stage.execute({"audio_path": "/tmp/a.16k.wav", "language": "en"})

        assert ctx["transcription_result"]["language"] == "en"
        assert ctx["transcription_result"]["segments"] == [
            {"start": 0.0, "end": 3.0, "text": "Hello world"},
            {"start": 3.0, "end": 6.0, "text": "second"},
        ]
        assert ctx["whisperx_audio"] == "AUDIO_ARRAY"

    def test_progress_callback_brackets_run(self):
        stage = WhisperCppTranscribeStage()
        payload = {"result": {"language": "en"}, "transcription": []}
        progress: list[tuple[float, str]] = []
        mock_whisperx = MagicMock()
        with (
            patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m.bin"),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(payload)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"}, on_progress=lambda p, m: progress.append((p, m)))

        assert progress[0][0] == 0.0
        assert progress[-1][0] == 1.0

    def test_nonzero_exit_raises(self):
        stage = WhisperCppTranscribeStage()
        with (
            patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m.bin"),
            patch(f"{_MODULE}.subprocess.run", return_value=MagicMock(returncode=2, stderr="boom")),
            pytest.raises(TranscriptionError, match="exit 2"),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"})

    def test_missing_json_raises(self):
        stage = WhisperCppTranscribeStage()
        with (
            patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m.bin"),
            patch(f"{_MODULE}.subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            pytest.raises(TranscriptionError, match="no JSON"),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"})

    def test_binary_not_found_raises(self):
        stage = WhisperCppTranscribeStage(binary="whisper-cli")
        with (
            patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m.bin"),
            patch(f"{_MODULE}.subprocess.run", side_effect=FileNotFoundError()),
            pytest.raises(TranscriptionError, match="whisper-cli"),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"})


class TestModelResolution:
    def test_prefers_existing_local_model(self, tmp_path):
        model = tmp_path / "ggml-large-v3.bin"
        model.write_bytes(b"x")
        stage = WhisperCppTranscribeStage(model_name="large-v3", model_dir=tmp_path)
        assert stage._resolve_model_path() == str(model)

    def test_downloads_when_absent(self, tmp_path):
        stage = WhisperCppTranscribeStage(model_name="large-v3", model_dir=tmp_path)
        mock_hf = MagicMock()
        mock_hf.hf_hub_download.return_value = "/cache/ggml-large-v3.bin"
        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            assert stage._resolve_model_path() == "/cache/ggml-large-v3.bin"
        mock_hf.hf_hub_download.assert_called_once_with(repo_id="ggerganov/whisper.cpp", filename="ggml-large-v3.bin")


class TestBackendSelection:
    @staticmethod
    def _runtime(backend: str) -> MagicMock:
        runtime = MagicMock()
        runtime.settings.transcribe_backend = backend
        runtime.settings.whispercpp_binary = "whisper-cli"
        runtime.settings.whispercpp_threads = 0
        runtime.settings.compute_type = "int8_float16"
        runtime.settings.device = "rocm"
        return runtime

    def test_selects_whispercpp_backend(self):
        job = MagicMock()
        job.model_name = "large-v3"
        stage = _build_transcribe_stage(job, self._runtime("whispercpp"), "cuda")
        assert isinstance(stage, WhisperCppTranscribeStage)

    def test_defaults_to_whisperx_backend(self):
        job = MagicMock()
        job.model_name = "large-v3"
        stage = _build_transcribe_stage(job, self._runtime("whisperx"), "cuda")
        assert isinstance(stage, TranscribeStage)
