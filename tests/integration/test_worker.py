from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from rq.timeouts import JobTimeoutException

from whisper_ui.core.models import Job
from whisper_ui.worker.tasks import _extract_rq_timeout_seconds

if TYPE_CHECKING:
    from whisper_ui.storage.database import JobDatabase


class TestWorkerTaskSetup:
    def test_process_transcription_job_not_found(self, db: JobDatabase, settings):
        mock_redis = MagicMock()
        mock_redis.hset = MagicMock()
        mock_redis.expire = MagicMock()

        with (
            patch("whisper_ui.worker.runtime.get_settings", return_value=settings),
            patch("whisper_ui.worker.runtime.Redis") as mock_redis_cls,
            patch("whisper_ui.worker.runtime.JobDatabase") as mock_db_cls,
            patch("whisper_ui.worker.runtime.FileStore"),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            mock_db_instance = MagicMock()
            mock_db_instance.get_job.return_value = None
            mock_db_cls.return_value = mock_db_instance

            from whisper_ui.worker.tasks import process_transcription

            result = process_transcription("nonexistent-id")
            assert "not found" in result

    def test_process_transcription_db_get_job_raises(self, db: JobDatabase, settings):
        mock_redis = MagicMock()
        mock_redis.hset = MagicMock()
        mock_redis.expire = MagicMock()

        with (
            patch("whisper_ui.worker.runtime.get_settings", return_value=settings),
            patch("whisper_ui.worker.runtime.Redis") as mock_redis_cls,
            patch("whisper_ui.worker.runtime.JobDatabase") as mock_db_cls,
            patch("whisper_ui.worker.runtime.FileStore"),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            mock_db_instance = MagicMock()
            mock_db_instance.get_job.side_effect = RuntimeError("db read failed")
            mock_db_cls.return_value = mock_db_instance

            from whisper_ui.worker.tasks import process_transcription

            result = process_transcription("some-job-id")
            assert "failed" in result
            assert "db read failed" in result
            mock_db_instance.update_job.assert_not_called()

    def test_extract_rq_timeout_prefers_get_current_job(self):
        """When running inside a real RQ worker, get_current_job().timeout is
        the authoritative source regardless of what the exception message says.
        """
        fake_job = MagicMock()
        fake_job.timeout = 7200
        with patch("rq.get_current_job", return_value=fake_job):
            exc = JobTimeoutException("Task exceeded maximum timeout value (999 seconds)")
            assert _extract_rq_timeout_seconds(exc) == 7200

    def test_extract_rq_timeout_falls_back_to_message_regex(self):
        """Outside a worker context, get_current_job() returns None and the
        helper must parse the RQ-formatted message instead.
        """
        with patch("rq.get_current_job", return_value=None):
            exc = JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")
            assert _extract_rq_timeout_seconds(exc) == 3600

    def test_extract_rq_timeout_returns_placeholder_on_total_failure(self):
        with patch("rq.get_current_job", return_value=None):
            exc = JobTimeoutException("something totally unexpected")
            assert _extract_rq_timeout_seconds(exc) == "?"

    def test_extract_rq_timeout_swallows_get_current_job_errors(self):
        """get_current_job() raising must not crash the error-handling path."""
        with patch("rq.get_current_job", side_effect=RuntimeError("no context")):
            exc = JobTimeoutException("Task exceeded maximum timeout value (1800 seconds)")
            assert _extract_rq_timeout_seconds(exc) == 1800

    def test_process_transcription_classifies_rq_timeout(self, db: JobDatabase, settings):
        """A JobTimeoutException raised mid-pipeline must surface as the
        Chinese timeout label, not "Diarization failed: ...", so users can
        tell the job layer killed the task.
        """
        from rq.timeouts import JobTimeoutException

        from whisper_ui.core.models import Job, JobStatus
        from whisper_ui.storage.database import JobDatabase as RealJobDatabase

        job = Job(filename="long.mp3", status=JobStatus.QUEUED, filepath="/tmp/long.mp3")
        db.insert_job(job)

        # Intentionally do NOT set timeout_exc._timeout — RQ's real death
        # penalty does not set it either; worker/tasks.py extracts the
        # timeout via rq.get_current_job() or by parsing the message.
        timeout_exc = JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")

        mock_redis = MagicMock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.run.side_effect = timeout_exc

        # process_transcription constructs and closes its own JobDatabase.
        # Let it use a real one bound to the same path as the test fixture
        # so it commits; re-open afterwards to verify persisted state.
        with (
            patch("whisper_ui.worker.runtime.get_settings", return_value=settings),
            patch("whisper_ui.worker.runtime.Redis") as mock_redis_cls,
            patch("whisper_ui.worker.runtime.FileStore") as mock_filestore_cls,
            patch("whisper_ui.worker.tasks.PipelineOrchestrator", return_value=mock_orchestrator),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            mock_filestore_cls.return_value = MagicMock()

            from whisper_ui.worker.tasks import process_transcription

            result = process_transcription(job.id)

        assert "timed out" in result
        assert "3600" in result

        verify_db = RealJobDatabase(settings.database_path)
        try:
            reloaded = verify_db.get_job(job.id)
        finally:
            verify_db.close()
        assert reloaded is not None
        assert reloaded.status == JobStatus.FAILED
        assert reloaded.error is not None
        assert "Diarization failed" not in reloaded.error
        assert "3600" in reloaded.error
        assert "超出上限" in reloaded.error

    def test_process_transcription_timeout_through_real_orchestrator(self, db: JobDatabase, settings, tmp_path):
        """Regression guard for PR #34 Finding 1: the timeout classification
        must survive the real PipelineOrchestrator path.

        Earlier tests mocked PipelineOrchestrator away, which hid the fact
        that its 'except Exception as e: raise PipelineError(...)' was
        wrapping BaseTimeoutException back into a stage-level error before
        worker/tasks.py could classify it. This test drives a real
        orchestrator with a real DiarizeStage and only mocks the outermost
        pyannote call so the RQ timeout exception originates from inside
        the stage boundary exactly as it would in production.
        """
        import sys

        from rq.timeouts import JobTimeoutException

        from whisper_ui.core.models import Job, JobStatus
        from whisper_ui.pipeline.diarize import DiarizeStage
        from whisper_ui.pipeline.orchestrator import PipelineOrchestrator
        from whisper_ui.storage.database import JobDatabase as RealJobDatabase

        audio_file = tmp_path / "long.wav"
        audio_file.write_bytes(b"RIFF" + b"\x00" * 100)

        job = Job(filename="long.wav", status=JobStatus.QUEUED, filepath=str(audio_file))
        db.insert_job(job)

        timeout_exc = JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")

        class _SeedAudioPathStage:
            """Mimics preprocess: copies input_path into audio_path so
            DiarizeStage can run without invoking real ffmpeg / whisperx."""

            @property
            def name(self) -> str:
                return "preprocess"

            def execute(self, context, on_progress=None):
                context["audio_path"] = context["input_path"]
                return context

            def cleanup(self) -> None:
                pass

        stage = DiarizeStage(hf_token="test-token", device="cpu", heartbeat_interval=0)
        mock_pipeline_cls = MagicMock()
        mock_pipeline_cls.return_value = MagicMock(side_effect=timeout_exc)
        mock_diarize_module = MagicMock()
        mock_diarize_module.DiarizationPipeline = mock_pipeline_cls

        def _orchestrator_factory(*_args, **kwargs):
            return PipelineOrchestrator(
                [_SeedAudioPathStage(), stage],
                on_progress=kwargs.get("on_progress"),
            )

        mock_redis = MagicMock()

        with (
            patch("whisper_ui.worker.runtime.get_settings", return_value=settings),
            patch("whisper_ui.worker.runtime.Redis") as mock_redis_cls,
            patch("whisper_ui.worker.runtime.FileStore") as mock_filestore_cls,
            patch("whisper_ui.worker.tasks.PipelineOrchestrator", side_effect=_orchestrator_factory),
            patch.dict(sys.modules, {"whisperx.diarize": mock_diarize_module, "whisperx": MagicMock()}),
        ):
            mock_redis_cls.from_url.return_value = mock_redis
            mock_filestore_cls.return_value = MagicMock()

            from whisper_ui.worker.tasks import process_transcription

            result = process_transcription(job.id)

        assert "timed out" in result

        verify_db = RealJobDatabase(settings.database_path)
        try:
            reloaded = verify_db.get_job(job.id)
        finally:
            verify_db.close()
        assert reloaded is not None
        assert reloaded.status == JobStatus.FAILED
        assert reloaded.error is not None
        # The real orchestrator used to produce "Stage 'diarize' failed: ..."
        # before the Phase 2 fix; assert explicitly that those wrappings are
        # gone and the Chinese timeout label with the actual seconds value
        # (extracted via the RQ message regex fallback) is used instead.
        assert "Diarization failed" not in reloaded.error
        assert "Stage 'diarize' failed" not in reloaded.error
        assert "超出上限" in reloaded.error
        assert "3600" in reloaded.error

    def test_job_model_has_diarization_fields(self):
        job = Job(enable_diarization=False, convert_to_traditional=False)
        assert job.enable_diarization is False
        assert job.convert_to_traditional is False

    def test_job_defaults_diarization_enabled(self):
        job = Job()
        assert job.enable_diarization is True
        assert job.convert_to_traditional is True
