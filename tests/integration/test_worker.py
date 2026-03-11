from __future__ import annotations

from unittest.mock import MagicMock, patch

from whisper_ui.core.models import Job
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

    def test_job_model_has_diarization_fields(self):
        job = Job(enable_diarization=False, convert_to_traditional=False)
        assert job.enable_diarization is False
        assert job.convert_to_traditional is False

    def test_job_defaults_diarization_enabled(self):
        job = Job()
        assert job.enable_diarization is True
        assert job.convert_to_traditional is True
