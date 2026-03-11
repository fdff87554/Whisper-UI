from __future__ import annotations

from unittest.mock import MagicMock, patch

from whisper_ui.pipeline.orchestrator import PipelineOrchestrator
from whisper_ui.pipeline.preprocess import PreprocessStage


class TestPreprocessIntegration:
    def test_rejects_unsupported_format(self, tmp_path):
        stage = PreprocessStage()
        fake_file = tmp_path / "test.xyz"
        fake_file.write_text("not audio")
        context = {"input_path": str(fake_file)}

        import pytest

        from whisper_ui.core.exceptions import PreprocessError

        with pytest.raises(PreprocessError, match="Unsupported file format"):
            stage.execute(context)

    @patch("whisper_ui.pipeline.preprocess.subprocess.run")
    def test_preprocess_calls_ffmpeg(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        wav_path = tmp_path / "test.wav"
        wav_path.write_bytes(b"RIFF" + b"\x00" * 100)

        with patch("whisper_ui.pipeline.preprocess._get_duration", return_value=10.0):
            stage = PreprocessStage()
            context = stage.execute({"input_path": str(wav_path)})

        assert "audio_path" in context
        assert context["duration"] == 10.0
        mock_run.assert_called_once()


class TestOrchestratorIntegration:
    def test_multiple_stages_chain_context(self):
        from whisper_ui.core.models import Segment, TranscriptResult

        class AddKeyStage:
            def __init__(self, key, value):
                self._key = key
                self._value = value

            @property
            def name(self):
                return self._key

            def execute(self, context, on_progress=None):
                context[self._key] = self._value
                return context

            def cleanup(self):
                pass

        result = TranscriptResult(segments=[Segment(start=0, end=1, text="hi")])
        stages = [
            AddKeyStage("step1", "done"),
            AddKeyStage("step2", "done"),
            AddKeyStage("transcript_result", result),
        ]
        orchestrator = PipelineOrchestrator(stages)
        out = orchestrator.run({})
        assert out is result
