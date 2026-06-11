from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from whisper_ui.core.config import Settings
from whisper_ui.core.exceptions import TranscriptionError
from whisper_ui.pipeline.transcribe import TranscribeStage
from whisper_ui.pipeline.whispercpp_transcribe import WhisperCppTranscribeStage
from whisper_ui.worker.stage_tasks import _build_transcribe_stage

_MODULE = "whisper_ui.pipeline.whispercpp_transcribe"


def _fake_cli_writer(payload: dict, calls: list[list[str]] | None = None):
    """Return a subprocess.run stand-in that writes ``payload`` to <-of>.json.

    When ``calls`` is given, every invoked command line is appended to it so
    tests can assert on the exact flags passed to whisper-cli.
    """

    def _run(cmd, *args, **kwargs):  # resilient to subprocess.run kwargs (encoding/errors/etc.)
        if calls is not None:
            calls.append(list(cmd))
        out_prefix = cmd[cmd.index("-of") + 1]
        Path(f"{out_prefix}.json").write_text(json.dumps(payload), encoding="utf-8")
        return MagicMock(returncode=0, stderr="", stdout="")

    return _run


@contextlib.contextmanager
def _patched_model_paths(vad_path: str = "/m/vad.bin"):
    """Patch both model resolutions so execute() never touches the network."""
    with (
        patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m/ggml-large-v3.bin"),
        patch.object(WhisperCppTranscribeStage, "_resolve_vad_model_path", return_value=vad_path),
    ):
        yield


class TestAdapter:
    def test_converts_ms_to_seconds_and_strips_text(self):
        data = {
            "result": {"language": "zh"},
            "transcription": [{"offsets": {"from": 1200, "to": 2500}, "text": "  hi  "}],
        }
        assert WhisperCppTranscribeStage._to_whisperx_result(data, "zh") == {
            "language": "zh",
            "segments": [{"start": 1.2, "end": 2.5, "text": "hi"}],
        }

    def test_missing_language_falls_back_to_requested(self):
        # A truthy "unknown" would silently disable the zh-only postprocess
        # and LLM gates; the explicitly requested language must win instead.
        assert WhisperCppTranscribeStage._to_whisperx_result({}, "zh") == {"language": "zh", "segments": []}

    def test_missing_language_with_auto_request_stays_unknown(self):
        assert WhisperCppTranscribeStage._to_whisperx_result({}, "auto") == {"language": "unknown", "segments": []}

    def test_detected_language_wins_over_requested(self):
        data = {"result": {"language": "en"}, "transcription": []}
        assert WhisperCppTranscribeStage._to_whisperx_result(data, "auto") == {"language": "en", "segments": []}

    def test_null_offsets_do_not_crash(self):
        # An explicit JSON null offset must not raise (None / 1000.0 -> TypeError).
        data = {"transcription": [{"offsets": {"from": None, "to": None}, "text": "x"}]}
        out = WhisperCppTranscribeStage._to_whisperx_result(data, "zh")
        assert out == {"language": "zh", "segments": [{"start": 0.0, "end": 0.0, "text": "x"}]}

    def test_non_dict_payload_is_safe(self):
        assert WhisperCppTranscribeStage._to_whisperx_result([1, 2, 3], "zh") == {"language": "zh", "segments": []}


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
            _patched_model_paths(),
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
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(payload)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"}, on_progress=lambda p, m: progress.append((p, m)))

        assert progress[0][0] == 0.0
        assert progress[-1][0] == 1.0

    def test_nonzero_exit_raises(self):
        stage = WhisperCppTranscribeStage()
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", return_value=MagicMock(returncode=2, stderr="boom")),
            pytest.raises(TranscriptionError, match="exit 2"),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"})

    def test_missing_json_raises(self):
        stage = WhisperCppTranscribeStage()
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", return_value=MagicMock(returncode=0, stderr="")),
            pytest.raises(TranscriptionError, match="no JSON"),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"})

    def test_binary_not_found_raises(self):
        stage = WhisperCppTranscribeStage(binary="whisper-cli")
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", side_effect=FileNotFoundError()),
            pytest.raises(TranscriptionError, match="whisper-cli"),
        ):
            stage.execute({"audio_path": "/tmp/a.wav"})


class TestCliFlags:
    _PAYLOAD = {"result": {"language": "zh"}, "transcription": []}

    def _run_and_capture(self, stage: WhisperCppTranscribeStage) -> list[str]:
        calls: list[list[str]] = []
        mock_whisperx = MagicMock()
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(self._PAYLOAD, calls)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            stage.execute({"audio_path": "/tmp/a.wav", "language": "zh"})
        return calls[0]

    def test_default_flags_enable_vad_and_disable_context(self):
        cmd = self._run_and_capture(WhisperCppTranscribeStage())
        assert "--vad" in cmd
        assert cmd[cmd.index("-vm") + 1] == "/m/vad.bin"
        assert cmd[cmd.index("-mc") + 1] == "0"

    def test_vad_disabled_omits_vad_flags(self):
        cmd = self._run_and_capture(WhisperCppTranscribeStage(vad=False))
        assert "--vad" not in cmd
        assert "-vm" not in cmd

    def test_negative_max_context_keeps_cli_default(self):
        cmd = self._run_and_capture(WhisperCppTranscribeStage(max_context=-1))
        assert "-mc" not in cmd

    def test_auto_language_passes_through_to_cli(self):
        calls: list[list[str]] = []
        payload = {"result": {"language": "en"}, "transcription": []}
        mock_whisperx = MagicMock()
        stage = WhisperCppTranscribeStage()
        with (
            _patched_model_paths(),
            patch(f"{_MODULE}.subprocess.run", side_effect=_fake_cli_writer(payload, calls)),
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
        ):
            ctx = stage.execute({"audio_path": "/tmp/a.wav", "language": "auto"})
        cmd = calls[0]
        assert cmd[cmd.index("-l") + 1] == "auto"
        # The detected language reported by whisper.cpp flows into the result.
        assert ctx["transcription_result"]["language"] == "en"

    def test_vad_resolution_failure_fails_the_stage(self):
        stage = WhisperCppTranscribeStage()
        with (
            patch.object(WhisperCppTranscribeStage, "_resolve_model_path", return_value="/m.bin"),
            patch.object(
                WhisperCppTranscribeStage, "_resolve_vad_model_path", side_effect=RuntimeError("download failed")
            ),
            pytest.raises(TranscriptionError, match="download failed"),
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

    def test_ignores_directory_named_like_model(self, tmp_path):
        """A same-named directory under model_dir is not a usable model; fall
        through to the hub download instead of handing whisper-cli a dir."""
        (tmp_path / "ggml-large-v3.bin").mkdir()
        stage = WhisperCppTranscribeStage(model_name="large-v3", model_dir=tmp_path)
        mock_hf = MagicMock()
        mock_hf.hf_hub_download.return_value = "/cache/ggml-large-v3.bin"
        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            assert stage._resolve_model_path() == "/cache/ggml-large-v3.bin"


class TestVadModelResolution:
    def test_prefers_existing_local_vad_model(self, tmp_path):
        vad = tmp_path / "ggml-silero-v5.1.2.bin"
        vad.write_bytes(b"x")
        stage = WhisperCppTranscribeStage(model_dir=tmp_path)
        assert stage._resolve_vad_model_path() == str(vad)

    def test_downloads_vad_model_when_absent(self, tmp_path):
        stage = WhisperCppTranscribeStage(model_dir=tmp_path)
        mock_hf = MagicMock()
        mock_hf.hf_hub_download.return_value = "/cache/ggml-silero-v5.1.2.bin"
        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            assert stage._resolve_vad_model_path() == "/cache/ggml-silero-v5.1.2.bin"
        mock_hf.hf_hub_download.assert_called_once_with(
            repo_id="ggml-org/whisper-vad", filename="ggml-silero-v5.1.2.bin"
        )

    def test_ignores_directory_named_like_vad_model(self, tmp_path):
        (tmp_path / "ggml-silero-v5.1.2.bin").mkdir()
        stage = WhisperCppTranscribeStage(model_dir=tmp_path)
        mock_hf = MagicMock()
        mock_hf.hf_hub_download.return_value = "/cache/ggml-silero-v5.1.2.bin"
        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            assert stage._resolve_vad_model_path() == "/cache/ggml-silero-v5.1.2.bin"


class TestBackendSelection:
    @staticmethod
    def _runtime(backend: str) -> MagicMock:
        runtime = MagicMock()
        runtime.settings.transcribe_backend = backend
        runtime.settings.whispercpp_binary = "whisper-cli"
        runtime.settings.whispercpp_threads = 0
        runtime.settings.whispercpp_vad = True
        runtime.settings.whispercpp_vad_model = "ggml-silero-v5.1.2.bin"
        runtime.settings.whispercpp_max_context = 0
        runtime.settings.compute_type = "int8_float16"
        runtime.settings.device = "rocm"
        return runtime

    def test_selects_whispercpp_backend(self):
        job = MagicMock()
        job.model_name = "large-v3"
        stage = _build_transcribe_stage(job, self._runtime("whispercpp"), "cuda")
        assert isinstance(stage, WhisperCppTranscribeStage)

    def test_whispercpp_stage_receives_vad_settings(self):
        job = MagicMock()
        job.model_name = "large-v3"
        runtime = self._runtime("whispercpp")
        runtime.settings.whispercpp_vad = False
        runtime.settings.whispercpp_max_context = -1
        stage = _build_transcribe_stage(job, runtime, "cuda")
        assert stage._vad is False
        assert stage._vad_model == "ggml-silero-v5.1.2.bin"
        assert stage._max_context == -1

    def test_defaults_to_whisperx_backend(self):
        job = MagicMock()
        job.model_name = "large-v3"
        stage = _build_transcribe_stage(job, self._runtime("whisperx"), "cuda")
        assert isinstance(stage, TranscribeStage)


class TestTranscribeBackendConfig:
    # _env_file=None isolates the validator test from a developer's local .env.
    def test_normalizes_case(self):
        assert Settings(transcribe_backend="WhisperCPP", _env_file=None).transcribe_backend == "whispercpp"

    def test_rejects_unknown_backend(self):
        with pytest.raises(ValidationError):
            Settings(transcribe_backend="bogus", _env_file=None)


class TestWhispercppVadConfig:
    def test_defaults_enable_vad_with_silero_model(self):
        settings = Settings(_env_file=None)
        assert settings.whispercpp_vad is True
        assert settings.whispercpp_vad_model == "ggml-silero-v5.1.2.bin"
        assert settings.whispercpp_max_context == 0

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("WHISPERCPP_VAD", "false")
        monkeypatch.setenv("WHISPERCPP_MAX_CONTEXT", "-1")
        settings = Settings(_env_file=None)
        assert settings.whispercpp_vad is False
        assert settings.whispercpp_max_context == -1

    def test_rejects_max_context_below_minus_one(self):
        with pytest.raises(ValidationError):
            Settings(whispercpp_max_context=-2, _env_file=None)

    def test_rejects_vad_enabled_without_model(self):
        with pytest.raises(ValidationError):
            Settings(whispercpp_vad=True, whispercpp_vad_model="", _env_file=None)
