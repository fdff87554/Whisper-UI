from __future__ import annotations

import logging
import re
from pathlib import Path

from redis import Redis

from whisper_ui.core.config import get_settings
from whisper_ui.core.constants import ERROR_DISPLAY_LENGTH, ERROR_MAX_LENGTH
from whisper_ui.core.messages import PIPELINE_COMPLETE
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.assign_speakers import AssignSpeakersStage
from whisper_ui.pipeline.diarize import DiarizeStage
from whisper_ui.pipeline.download import DownloadStage
from whisper_ui.pipeline.orchestrator import STAGE_WEIGHTS_WITH_DOWNLOAD, PipelineOrchestrator
from whisper_ui.pipeline.postprocess import PostprocessStage
from whisper_ui.pipeline.preprocess import PreprocessStage
from whisper_ui.pipeline.transcribe import TranscribeStage
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.ui.labels import JOBS_TIMEOUT_ERROR
from whisper_ui.worker.progress import RedisProgressReporter

logger = logging.getLogger(__name__)

_RQ_TIMEOUT_MESSAGE_PATTERN = re.compile(r"\((\d+)\s*seconds?\)")


def _extract_rq_timeout_seconds(exc: BaseException) -> int | str:
    """Return the configured RQ ``job_timeout`` for the running job.

    RQ's death-penalty handler formats the timeout into the exception
    *message* but does not attach it as an attribute on the exception
    instance (see ``rq.timeouts.UnixSignalDeathPenalty.handle_death_penalty``
    in RQ 2.7.0). So:

    1. In a real worker context, ``rq.get_current_job().timeout`` holds the
       actual configured value from enqueue time.
    2. Outside a worker context (unit tests that call
       ``process_transcription`` directly), fall back to parsing the
       formatted message.
    3. If both fail, return ``"?"`` so the error label still renders.
    """
    try:
        from rq import get_current_job

        current = get_current_job()
        if current is not None and current.timeout:
            return current.timeout
    except Exception:
        logger.debug("rq.get_current_job() unavailable while extracting timeout", exc_info=True)

    match = _RQ_TIMEOUT_MESSAGE_PATTERN.search(str(exc))
    if match:
        return int(match.group(1))
    return "?"


def _cleanup_preprocessed(context: dict) -> None:
    """Remove the intermediate 16kHz WAV file created by PreprocessStage."""
    audio_path = context.get("audio_path")
    if audio_path is None:
        return
    try:
        Path(audio_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to clean up preprocessed file: %s", audio_path)


def process_transcription(job_id: str) -> str:
    from rq.timeouts import BaseTimeoutException

    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    reporter = RedisProgressReporter(redis, job_id, processing_ttl=settings.redis_processing_expiry)
    db = JobDatabase(settings.database_path)
    filestore = FileStore(settings.upload_dir, settings.output_dir)

    context: dict = {}
    job: Job | None = None
    try:
        job = db.get_job(job_id)
        if job is None:
            reporter.fail(f"Job {job_id} not found in database.")
            return f"Job {job_id} not found"

        job.status = JobStatus.PROCESSING
        db.update_job(job)

        common_stages = [
            PreprocessStage(),
            TranscribeStage(
                model_name=job.model_name,
                compute_type=settings.compute_type,
                device=settings.device,
            ),
            AlignStage(device=settings.device),
            DiarizeStage(
                hf_token=settings.hf_token,
                device=settings.device,
                enabled=job.enable_diarization,
                heartbeat_interval=settings.diarize_heartbeat_interval,
            ),
            AssignSpeakersStage(),
            PostprocessStage(convert_to_traditional=job.convert_to_traditional),
        ]

        context = {
            "language": job.language,
            "batch_size": settings.batch_size,
            "num_speakers": job.num_speakers,
        }

        if job.source_url:
            download_dir = str(filestore.prepare_upload_path(job.id, "_").parent)
            stages = [DownloadStage(max_duration=settings.youtube_max_duration), *common_stages]
            stage_weights = STAGE_WEIGHTS_WITH_DOWNLOAD
            context["source_url"] = job.source_url
            context["download_dir"] = download_dir
            context["input_path"] = ""
        else:
            stages = common_stages
            stage_weights = None
            context["input_path"] = job.filepath

        def on_progress(progress: float, message: str) -> None:
            reporter.report(progress, message)
            job.progress = progress
            job.progress_message = message
            db.update_job(job)

        orchestrator = PipelineOrchestrator(stages, on_progress=on_progress, stage_weights=stage_weights)

        result = orchestrator.run(context)
        _cleanup_preprocessed(context)

        if job.source_url and context.get("video_title"):
            job.filename = context["video_title"]
        result_path = filestore.save_result(job_id, result)

        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.progress_message = PIPELINE_COMPLETE
        job.result_path = str(result_path)
        job.duration = result.duration
        db.update_job(job)
        reporter.complete(str(result_path))

        logger.info("Job %s completed successfully.", job_id)
        return f"Job {job_id} completed"

    except BaseTimeoutException as e:
        _cleanup_preprocessed(context)
        timeout_seconds = _extract_rq_timeout_seconds(e)
        error_msg = JOBS_TIMEOUT_ERROR.format(seconds=timeout_seconds)
        logger.exception("Job %s timed out: %s", job_id, error_msg)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error = error_msg[:ERROR_MAX_LENGTH]
            job.progress_message = f"Failed: {error_msg[:ERROR_DISPLAY_LENGTH]}"
            db.update_job(job)
        reporter.fail(error_msg)
        return f"Job {job_id} timed out: {error_msg}"

    except Exception as e:
        _cleanup_preprocessed(context)
        error_msg = str(e)
        logger.exception("Job %s failed: %s", job_id, error_msg)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error = error_msg[:ERROR_MAX_LENGTH]
            job.progress_message = f"Failed: {error_msg[:ERROR_DISPLAY_LENGTH]}"
            db.update_job(job)
        reporter.fail(error_msg)
        return f"Job {job_id} failed: {error_msg}"

    finally:
        db.close()
