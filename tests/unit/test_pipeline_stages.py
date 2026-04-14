"""Unit tests for pipeline stages (transcribe, align, diarize, assign_speakers)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from whisper_ui.core.exceptions import AlignmentError, DiarizationError, TranscriptionError
from whisper_ui.core.messages import (
    ALIGN_DONE,
    ALIGN_LOADING,
    ALIGN_SKIPPED,
    ASSIGN_DONE,
    ASSIGN_FAILED,
    ASSIGN_SKIPPED,
    DIARIZE_DONE,
    DIARIZE_SKIPPED,
    DIARIZE_SKIPPED_DISABLED,
    TRANSCRIBE_DONE,
    TRANSCRIBE_LOADING,
)
from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.assign_speakers import AssignSpeakersStage
from whisper_ui.pipeline.diarize import DiarizeStage
from whisper_ui.pipeline.transcribe import TranscribeStage


class TestTranscribeStage:
    def test_execute_sets_context_keys(self):
        stage = TranscribeStage(model_name="base", compute_type="int8", device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_model.return_value = MagicMock(transcribe=MagicMock(return_value={"segments": []}))
        mock_whisperx.load_audio.return_value = "audio_array"

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            context = {"audio_path": "/tmp/test.wav", "language": "zh", "batch_size": 4}
            result = stage.execute(context)

        assert "transcription_result" in result
        assert "whisperx_audio" in result

    def test_progress_callback_called(self):
        stage = TranscribeStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_model.return_value = MagicMock(transcribe=MagicMock(return_value={"segments": []}))
        mock_whisperx.load_audio.return_value = "audio"
        progress_calls = []

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            stage.execute(
                {"audio_path": "/tmp/test.wav"},
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )

        assert progress_calls[0] == (0.0, TRANSCRIBE_LOADING)
        assert progress_calls[-1] == (1.0, TRANSCRIBE_DONE)

    def test_cleanup_guard_no_model(self):
        stage = TranscribeStage(device="cpu")
        assert stage._model is None
        with patch("whisper_ui.pipeline.transcribe.gc.collect") as mock_gc:
            stage.cleanup()
        mock_gc.assert_not_called()

    def test_cleanup_releases_model(self):
        stage = TranscribeStage(device="cpu")
        stage._model = MagicMock()
        with (
            patch("whisper_ui.pipeline.transcribe.gc.collect") as mock_gc,
            patch("whisper_ui.pipeline.transcribe.release_gpu_memory"),
        ):
            stage.cleanup()
        assert stage._model is None
        mock_gc.assert_called_once()

    def test_import_error_raises_transcription_error(self):
        stage = TranscribeStage(device="cpu")
        with (
            patch.dict("sys.modules", {"whisperx": None}),
            pytest.raises(TranscriptionError, match="not installed"),
        ):
            stage.execute({"audio_path": "/tmp/test.wav"})


class TestAlignStage:
    def test_execute_sets_aligned_result(self):
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.return_value = ("model", "metadata")
        mock_whisperx.align.return_value = {"segments": [{"text": "hi"}]}

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            context = {
                "transcription_result": {"segments": [], "language": "zh"},
                "whisperx_audio": "audio",
            }
            result = stage.execute(context)

        assert "aligned_result" in result

    def test_progress_callback_called(self):
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.return_value = ("model", "metadata")
        mock_whisperx.align.return_value = {"segments": []}
        progress_calls = []

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            stage.execute(
                {"transcription_result": {"segments": []}, "whisperx_audio": "audio"},
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )

        assert progress_calls[0] == (0.0, ALIGN_LOADING)
        assert progress_calls[-1] == (1.0, ALIGN_DONE)

    def test_cleanup_guard_no_resources(self):
        stage = AlignStage(device="cpu")
        with patch("whisper_ui.pipeline.align.gc.collect") as mock_gc:
            stage.cleanup()
        mock_gc.assert_not_called()

    def test_cleanup_releases_both(self):
        stage = AlignStage(device="cpu")
        stage._model = MagicMock()
        stage._metadata = MagicMock()
        with (
            patch("whisper_ui.pipeline.align.gc.collect") as mock_gc,
            patch("whisper_ui.pipeline.align.release_gpu_memory"),
        ):
            stage.cleanup()
        assert stage._model is None
        assert stage._metadata is None
        mock_gc.assert_called_once()

    def test_import_error_raises_alignment_error(self):
        stage = AlignStage(device="cpu")
        with (
            patch.dict("sys.modules", {"whisperx": None}),
            pytest.raises(AlignmentError, match="not installed"),
        ):
            stage.execute({"transcription_result": {"segments": []}, "whisperx_audio": "audio"})

    def test_alignment_failure_returns_context_without_aligned_result(self):
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.side_effect = ValueError("model not found")

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            context = {
                "transcription_result": {"segments": [], "language": "ja"},
                "whisperx_audio": "audio",
                "language": "ja",
            }
            result = stage.execute(context)

        assert "aligned_result" not in result

    def test_alignment_failure_reports_skipped_progress(self):
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.side_effect = ValueError("model not found")
        progress_calls = []

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            stage.execute(
                {
                    "transcription_result": {"segments": [], "language": "ja"},
                    "whisperx_audio": "audio",
                    "language": "ja",
                },
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )

        assert progress_calls[-1] == (1.0, ALIGN_SKIPPED)

    def test_alignment_failure_logs_warning(self):
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.side_effect = ValueError("model not found")

        with (
            patch.dict("sys.modules", {"whisperx": mock_whisperx}),
            patch("whisper_ui.pipeline.align.logger") as mock_logger,
        ):
            stage.execute(
                {
                    "transcription_result": {"segments": [], "language": "ja"},
                    "whisperx_audio": "audio",
                    "language": "ja",
                },
            )

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "ja" in str(call_args)
        assert "model not found" in str(call_args)

    def test_align_execution_failure_skips_without_partial_result(self):
        stage = AlignStage(device="cpu")
        mock_whisperx = MagicMock()
        mock_whisperx.load_align_model.return_value = ("model", "metadata")
        mock_whisperx.align.side_effect = RuntimeError("alignment crashed")
        progress_calls = []

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            context = {
                "transcription_result": {"segments": [{"text": "hello"}], "language": "ja"},
                "whisperx_audio": "audio",
                "language": "ja",
            }
            result = stage.execute(context, on_progress=lambda p, m: progress_calls.append((p, m)))

        assert "aligned_result" not in result
        assert progress_calls[-1] == (1.0, ALIGN_SKIPPED)
        assert stage._model is not None
        assert stage._metadata is not None


class TestDiarizeStage:
    def test_disabled_skips(self):
        stage = DiarizeStage(enabled=False, device="cpu")
        progress_calls = []
        result = stage.execute({}, on_progress=lambda p, m: progress_calls.append((p, m)))
        assert result.get("diarize_result") is None
        assert progress_calls[-1] == (1.0, DIARIZE_SKIPPED_DISABLED)

    def test_no_token_skips(self):
        stage = DiarizeStage(hf_token="", device="cpu")
        progress_calls = []
        result = stage.execute({}, on_progress=lambda p, m: progress_calls.append((p, m)))
        assert result.get("diarize_result") is None
        assert progress_calls[-1] == (1.0, DIARIZE_SKIPPED)

    def test_execute_success(self):
        stage = DiarizeStage(hf_token="test-token", device="cpu")
        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock(return_value="diarize_segments")
        mock_pipeline_cls.return_value = mock_pipeline_instance

        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        with patch.dict("sys.modules", {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}):
            context = {"audio_path": "/tmp/test.wav"}
            progress_calls = []
            result = stage.execute(context, on_progress=lambda p, m: progress_calls.append((p, m)))

        assert result["diarize_result"] == "diarize_segments"
        assert progress_calls[-1] == (1.0, DIARIZE_DONE)

    def test_auth_error_message(self):
        stage = DiarizeStage(hf_token="bad-token", device="cpu")
        mock_pipeline_cls = MagicMock(side_effect=Exception("401 Unauthorized"))
        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        with (
            patch.dict("sys.modules", {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}),
            pytest.raises(DiarizationError, match="authorization error"),
        ):
            stage.execute({"audio_path": "/tmp/test.wav"})

    def test_cleanup_guard_no_pipeline(self):
        stage = DiarizeStage(device="cpu")
        with patch("whisper_ui.pipeline.diarize.gc.collect") as mock_gc:
            stage.cleanup()
        mock_gc.assert_not_called()

    def test_cleanup_releases_pipeline(self):
        stage = DiarizeStage(device="cpu")
        stage._pipeline = MagicMock()
        with (
            patch("whisper_ui.pipeline.diarize.gc.collect") as mock_gc,
            patch("whisper_ui.pipeline.diarize.release_gpu_memory"),
        ):
            stage.cleanup()
        assert stage._pipeline is None
        mock_gc.assert_called_once()

    def test_rq_timeout_is_not_wrapped_as_diarization_error(self):
        from rq.timeouts import JobTimeoutException

        stage = DiarizeStage(hf_token="test-token", device="cpu")
        mock_pipeline_cls = MagicMock()
        mock_pipeline_instance = MagicMock(
            side_effect=JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")
        )
        mock_pipeline_cls.return_value = mock_pipeline_instance

        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        with (
            patch.dict("sys.modules", {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}),
            pytest.raises(JobTimeoutException, match="Task exceeded"),
        ):
            stage.execute({"audio_path": "/tmp/test.wav"})

    def test_heartbeat_refreshes_progress_during_slow_pipeline(self):
        """A long-running diarization must keep emitting progress updates
        so Redis TTL stays warm and stale-job-recovery does not reap it.
        """
        import time as time_module

        stage = DiarizeStage(hf_token="test-token", device="cpu", heartbeat_interval=1)

        def _slow_diarize(**_kwargs):
            # Sleep longer than two heartbeat intervals so we are certain
            # at least one extra progress call fires before we return.
            time_module.sleep(2.5)
            return "diarize_segments"

        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.return_value = MagicMock(side_effect=_slow_diarize)
        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        progress_calls: list[tuple[float, str]] = []

        with patch.dict("sys.modules", {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}):
            stage.execute(
                {"audio_path": "/tmp/test.wav"},
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )

        # Expected baseline: loading (0.0) + running (0.2) + done (1.0) = 3 calls.
        # Heartbeat adds at least one more with the "已執行" phrase.
        heartbeat_messages = [m for _, m in progress_calls if "已執行" in m]
        assert len(heartbeat_messages) >= 1
        assert progress_calls[-1] == (1.0, DIARIZE_DONE)

    def test_heartbeat_disabled_when_interval_zero(self):
        stage = DiarizeStage(hf_token="test-token", device="cpu", heartbeat_interval=0)
        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.return_value = MagicMock(return_value="diarize_segments")
        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        progress_calls: list[tuple[float, str]] = []
        with patch.dict("sys.modules", {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}):
            stage.execute(
                {"audio_path": "/tmp/test.wav"},
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )
        assert not any("已執行" in m for _, m in progress_calls)


class TestAssignSpeakersStage:
    def test_no_diarize_skips(self):
        stage = AssignSpeakersStage()
        progress_calls = []
        result = stage.execute(
            {"diarize_result": None, "aligned_result": {"segments": []}},
            on_progress=lambda p, m: progress_calls.append((p, m)),
        )
        assert "final_result" not in result
        assert progress_calls[-1] == (1.0, ASSIGN_SKIPPED)

    def test_success(self):
        stage = AssignSpeakersStage()
        mock_whisperx = MagicMock()
        mock_whisperx.assign_word_speakers.return_value = {"segments": [{"speaker": "S1"}]}
        progress_calls = []

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            result = stage.execute(
                {"diarize_result": "diarize_data", "aligned_result": {"segments": []}},
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )

        assert "final_result" in result
        assert progress_calls[-1] == (1.0, ASSIGN_DONE)

    def test_exception_falls_back(self):
        stage = AssignSpeakersStage()
        mock_whisperx = MagicMock()
        mock_whisperx.assign_word_speakers.side_effect = RuntimeError("fail")
        aligned = {"segments": [{"text": "hello"}]}
        progress_calls = []

        with patch.dict("sys.modules", {"whisperx": mock_whisperx}):
            result = stage.execute(
                {"diarize_result": "data", "aligned_result": aligned},
                on_progress=lambda p, m: progress_calls.append((p, m)),
            )

        assert result["final_result"] is aligned
        assert progress_calls[-1] == (1.0, ASSIGN_FAILED)
