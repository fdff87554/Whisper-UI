from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from whisper_ui.core.models import Job

if TYPE_CHECKING:
    from whisper_ui.storage.database import JobDatabase


class TestWorkerTaskSetup:
    def test_process_transcription_job_not_found(self, db: JobDatabase, settings):
        mock_redis = MagicMock()
        mock_redis.hset = MagicMock()
        mock_redis.expire = MagicMock()

        with (
            patch("whisper_ui.worker.tasks.get_settings", return_value=settings),
            patch("whisper_ui.worker.tasks.Redis") as mock_redis_cls,
            patch("whisper_ui.worker.tasks.JobDatabase") as mock_db_cls,
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
            patch("whisper_ui.worker.tasks.get_settings", return_value=settings),
            patch("whisper_ui.worker.tasks.Redis") as mock_redis_cls,
            patch("whisper_ui.worker.tasks.JobDatabase") as mock_db_cls,
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

        timeout_exc = JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")
        timeout_exc._timeout = 3600  # type: ignore[attr-defined]

        mock_redis = MagicMock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.run.side_effect = timeout_exc

        # process_transcription constructs and closes its own JobDatabase.
        # Let it use a real one bound to the same path as the test fixture
        # so it commits; re-open afterwards to verify persisted state.
        with (
            patch("whisper_ui.worker.tasks.get_settings", return_value=settings),
            patch("whisper_ui.worker.tasks.Redis") as mock_redis_cls,
            patch("whisper_ui.worker.tasks.FileStore") as mock_filestore_cls,
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

    def test_job_model_has_diarization_fields(self):
        job = Job(enable_diarization=False, convert_to_traditional=False)
        assert job.enable_diarization is False
        assert job.convert_to_traditional is False

    def test_job_defaults_diarization_enabled(self):
        job = Job()
        assert job.enable_diarization is True
        assert job.convert_to_traditional is True
