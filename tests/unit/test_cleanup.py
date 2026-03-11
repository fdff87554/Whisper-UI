from __future__ import annotations

from unittest.mock import patch

from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.diarize import DiarizeStage
from whisper_ui.pipeline.transcribe import TranscribeStage


class TestCleanupCallsRelease:
    @patch("whisper_ui.pipeline.transcribe.release_gpu_memory")
    def test_transcribe_stage_cleanup_calls_release(self, mock_release):
        stage = TranscribeStage()
        stage._model = "fake_model"
        stage.cleanup()
        mock_release.assert_called_once()
        assert stage._model is None

    @patch("whisper_ui.pipeline.align.release_gpu_memory")
    def test_align_stage_cleanup_calls_release(self, mock_release):
        stage = AlignStage()
        stage._model = "fake_model"
        stage._metadata = "fake_metadata"
        stage.cleanup()
        mock_release.assert_called_once()
        assert stage._model is None
        assert stage._metadata is None

    @patch("whisper_ui.pipeline.diarize.release_gpu_memory")
    def test_diarize_stage_cleanup_calls_release(self, mock_release):
        stage = DiarizeStage()
        stage._pipeline = "fake_pipeline"
        stage.cleanup()
        mock_release.assert_called_once()
        assert stage._pipeline is None
