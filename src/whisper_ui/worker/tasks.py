from __future__ import annotations

import logging

from redis import Redis

from whisper_ui.core.config import get_settings
from whisper_ui.core.models import JobStatus
from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.assign_speakers import AssignSpeakersStage
from whisper_ui.pipeline.diarize import DiarizeStage
from whisper_ui.pipeline.orchestrator import PipelineOrchestrator
from whisper_ui.pipeline.postprocess import PostprocessStage
from whisper_ui.pipeline.preprocess import PreprocessStage
from whisper_ui.pipeline.transcribe import TranscribeStage
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.worker.progress import RedisProgressReporter

logger = logging.getLogger(__name__)


def process_transcription(job_id: str) -> str:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    reporter = RedisProgressReporter(redis, job_id)
    db = JobDatabase(settings.database_path)
    filestore = FileStore(settings.upload_dir, settings.output_dir)

    job = db.get_job(job_id)
    if job is None:
        reporter.fail(f"Job {job_id} not found in database.")
        return f"Job {job_id} not found"

    job.status = JobStatus.PROCESSING
    db.update_job(job)

    try:
        stages = [
            PreprocessStage(),
            TranscribeStage(
                model_name=settings.whisper_model,
                compute_type=settings.compute_type,
                device=settings.device,
            ),
            AlignStage(device=settings.device),
            DiarizeStage(hf_token=settings.hf_token, device=settings.device),
            AssignSpeakersStage(),
            PostprocessStage(convert_to_traditional=(job.language == "zh")),
        ]

        def on_progress(progress: float, message: str) -> None:
            reporter.report(progress, message)
            job.progress = progress
            job.progress_message = message
            db.update_job(job)

        orchestrator = PipelineOrchestrator(stages, on_progress=on_progress)

        context = {
            "input_path": job.filepath,
            "language": job.language,
            "batch_size": settings.batch_size,
            "num_speakers": job.num_speakers,
        }

        result = orchestrator.run(context)
        result_path = filestore.save_result(job_id, result)

        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.progress_message = "Complete"
        job.result_path = str(result_path)
        job.duration = result.duration
        db.update_job(job)
        reporter.complete(str(result_path))

        logger.info("Job %s completed successfully.", job_id)
        return f"Job {job_id} completed"

    except Exception as e:
        error_msg = str(e)
        logger.exception("Job %s failed: %s", job_id, error_msg)
        job.status = JobStatus.FAILED
        job.error = error_msg[:1000]
        job.progress_message = f"Failed: {error_msg[:200]}"
        db.update_job(job)
        reporter.fail(error_msg)
        return f"Job {job_id} failed: {error_msg}"

    finally:
        db.close()
