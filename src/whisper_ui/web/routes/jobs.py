from __future__ import annotations

import logging
import math
from collections import OrderedDict
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from whisper_ui.core.constants import DEFAULT_JOBS_PER_PAGE
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.pipeline.audio_probe import get_audio_duration_seconds
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.batch_zip import create_batch_zip
from whisper_ui.web.deps import DbDep, FileStoreDep, RedisDep, SettingsDep, make_content_disposition, templates
from whisper_ui.web.validation import validate_hex_id
from whisper_ui.worker.pipeline_dispatcher import enqueue_pipeline
from whisper_ui.worker.progress import RedisProgressReporter

if TYPE_CHECKING:
    from whisper_ui.storage.database import JobDatabase

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_STATUS_FILTERS = frozenset({"", *JobStatus})


def _group_jobs_by_batch(jobs: list[Job]) -> list[tuple[str, list[Job]]]:
    groups: OrderedDict[str, list[Job]] = OrderedDict()
    for job in jobs:
        if job.batch_id is not None:
            groups.setdefault(job.batch_id, []).append(job)
        else:
            groups[f"_single:{job.id}"] = [job]
    return list(groups.items())


def _get_batch_info(db: JobDatabase, batch_ids: set[str]) -> dict[str, dict]:
    return db.get_batch_stats(batch_ids)


def _get_progress_data(redis, jobs: list[Job]) -> dict[str, dict[str, str]]:
    data = {}
    for job in jobs:
        if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            data[job.id] = RedisProgressReporter.get_progress(redis, job.id)
    return data


def _build_list_context(db: JobDatabase, redis, status: str, page: int) -> dict:
    status_filter = status or None
    total_count = db.count_jobs(status=status_filter)
    total_pages = max(1, math.ceil(total_count / DEFAULT_JOBS_PER_PAGE))
    page = max(0, page)
    page = min(page, total_pages - 1)

    offset = page * DEFAULT_JOBS_PER_PAGE
    jobs = db.list_jobs_filtered(status=status_filter, limit=DEFAULT_JOBS_PER_PAGE, offset=offset)

    groups = _group_jobs_by_batch(jobs)
    batch_ids = {key for key, _ in groups if not key.startswith("_single:")}
    batch_info = _get_batch_info(db, batch_ids)
    progress_data = _get_progress_data(redis, jobs)
    has_active = db.has_active_jobs()

    return {
        "groups": groups,
        "batch_info": batch_info,
        "progress_data": progress_data,
        "has_active": has_active,
        "status": status,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    db: DbDep,
    redis: RedisDep,
    submitted: int | None = None,
    status: str = "",
    page: int = 0,
):
    if status not in _VALID_STATUS_FILTERS:
        status = ""
    ctx = _build_list_context(db, redis, status, page)
    ctx["active_page"] = "jobs"
    ctx["submitted"] = submitted
    ctx["status_counts"] = db.get_status_counts()
    return templates.TemplateResponse(request=request, name="jobs.html", context=ctx)


@router.get("/jobs/list", response_class=HTMLResponse)
async def jobs_list_fragment(request: Request, db: DbDep, redis: RedisDep, status: str = "", page: int = 0):
    ctx = _build_list_context(db, redis, status, page)
    return templates.TemplateResponse(request=request, name="_job_list.html", context=ctx)


def _probe_retry_duration(job: Job) -> float | None:
    """Return the audio duration to use when sizing a retry job_timeout.

    For direct uploads the filepath still points at the original file on
    disk, so re-probing is cheap and avoids depending on the previous run
    having populated Job.duration. For URL jobs the original file has
    been cleaned up, so fall back to None → job_timeout_default.

    Returning None here is the contract that lets calculate_job_timeout
    fall back to settings.job_timeout_default; callers must not treat
    None as an error.
    """
    if job.source_url:
        return None
    try:
        return get_audio_duration_seconds(job.filepath)
    except Exception:  # pragma: no cover - defensive, ffprobe helper already swallows
        logger.exception("Failed to probe duration for retry of job %s", job.id)
        return None


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    filestore: FileStoreDep,
):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id)
    if job is None or job.status != JobStatus.FAILED:
        return Response(status_code=404)

    try:
        retry_duration = _probe_retry_duration(job)
        job.status = JobStatus.QUEUED
        job.error = None
        job.progress = 0.0
        job.progress_message = ""
        job.result_path = None
        job.duration = retry_duration
        db.update_job(job)
        redis.delete(f"job:{job.id}")

        enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    except Exception:
        logger.exception("Failed to enqueue retry for job %s", job.id)
        job.status = JobStatus.FAILED
        job.error = ui_labels.UPLOAD_ENQUEUE_FAILED
        db.update_job(job)

    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, db: DbDep, filestore: FileStoreDep, redis: RedisDep):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id)
    if job is None:
        return Response(status_code=404)
    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
        return Response(status_code=409)

    filestore.delete_job_files(job.id)
    db.delete_job(job.id)
    redis.delete(f"job:{job.id}")
    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.post("/jobs/batch/{batch_id}/retry")
async def retry_batch(
    batch_id: str,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    filestore: FileStoreDep,
):
    validate_hex_id(batch_id, "batch_id")
    all_jobs = db.list_jobs_by_batch(batch_id)
    if not all_jobs:
        return Response(status_code=404)

    for job in all_jobs:
        if job.status != JobStatus.FAILED:
            continue
        try:
            retry_duration = _probe_retry_duration(job)
            job.status = JobStatus.QUEUED
            job.error = None
            job.progress = 0.0
            job.progress_message = ""
            job.result_path = None
            job.duration = retry_duration
            db.update_job(job)
            redis.delete(f"job:{job.id}")
            enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
        except Exception:
            logger.exception("Failed to retry job %s", job.id)
            job.status = JobStatus.FAILED
            job.error = "Failed to enqueue retry"
            db.update_job(job)

    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.delete("/jobs/batch/{batch_id}")
async def delete_batch(batch_id: str, db: DbDep, filestore: FileStoreDep, redis: RedisDep):
    validate_hex_id(batch_id, "batch_id")
    all_jobs = db.list_jobs_by_batch(batch_id)
    if not all_jobs:
        return Response(status_code=404)

    for job in all_jobs:
        if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
            continue
        filestore.delete_job_files(job.id)
        db.delete_job(job.id)
        redis.delete(f"job:{job.id}")

    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.get("/jobs/batch/{batch_id}/download")
async def batch_download(batch_id: str, db: DbDep, filestore: FileStoreDep, format_name: str = "srt"):
    validate_hex_id(batch_id, "batch_id")
    all_jobs = db.list_jobs_by_batch(batch_id)
    if not all_jobs:
        raise HTTPException(status_code=404, detail="Batch not found")

    try:
        zip_data = create_batch_zip(all_jobs, filestore, format_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if zip_data is None:
        raise HTTPException(status_code=404, detail="No completed results in batch")

    filename = f"batch_{batch_id[:8]}.zip"
    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": make_content_disposition(filename)},
    )
