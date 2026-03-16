from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from whisper_ui.core.constants import (
    DEFAULT_JOBS_PER_PAGE,
    ERROR_MAX_LENGTH,
    STALE_JOB_CHECK_INTERVAL,
    STALE_JOB_TIMEOUT,
)
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.database import JobDatabase
from whisper_ui.ui._batch_zip import create_batch_zip
from whisper_ui.ui.labels import JOBS_STALE_ERROR
from whisper_ui.web.deps import DbDep, FileStoreDep, RedisDep, templates
from whisper_ui.worker.progress import RedisProgressReporter

logger = logging.getLogger(__name__)
router = APIRouter()

_last_stale_check = 0.0


def _check_stale_jobs(db: JobDatabase) -> None:
    global _last_stale_check
    now = time.monotonic()
    if now - _last_stale_check >= STALE_JOB_CHECK_INTERVAL:
        recovered = db.recover_stale_jobs(STALE_JOB_TIMEOUT, JOBS_STALE_ERROR)
        if recovered > 0:
            logger.warning("Recovered %d stale job(s)", recovered)
        _last_stale_check = now


def _group_jobs_by_batch(jobs: list[Job]) -> list[tuple[str, list[Job]]]:
    groups: OrderedDict[str, list[Job]] = OrderedDict()
    for job in jobs:
        if job.batch_id is not None:
            groups.setdefault(job.batch_id, []).append(job)
        else:
            groups[f"_single:{job.id}"] = [job]
    return list(groups.items())


def _get_batch_info(db: JobDatabase, batch_ids: set[str]) -> dict[str, dict]:
    info = {}
    for batch_id in batch_ids:
        all_jobs = db.list_jobs_by_batch(batch_id)
        completed = sum(1 for j in all_jobs if j.status == JobStatus.COMPLETED)
        failed = sum(1 for j in all_jobs if j.status == JobStatus.FAILED)
        total = len(all_jobs)
        all_done = all(j.status in (JobStatus.COMPLETED, JobStatus.FAILED) for j in all_jobs)
        info[batch_id] = {
            "all_jobs": all_jobs,
            "completed": completed,
            "failed": failed,
            "total": total,
            "all_done": all_done,
        }
    return info


def _get_progress_data(redis, jobs: list[Job]) -> dict[str, dict[str, str]]:
    data = {}
    for job in jobs:
        if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            data[job.id] = RedisProgressReporter.get_progress(redis, job.id)
    return data


def _build_list_context(db: JobDatabase, redis, status: str, page: int) -> dict:
    _check_stale_jobs(db)

    status_filter = status or None
    total_count = db.count_jobs(status=status_filter)
    total_pages = max(1, math.ceil(total_count / DEFAULT_JOBS_PER_PAGE))
    page = min(page, max(0, total_pages - 1))

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
    ctx = _build_list_context(db, redis, status, page)
    ctx["active_page"] = "jobs"
    ctx["submitted"] = submitted
    return templates.TemplateResponse(request=request, name="jobs.html", context=ctx)


@router.get("/jobs/list", response_class=HTMLResponse)
async def jobs_list_fragment(request: Request, db: DbDep, redis: RedisDep, status: str = "", page: int = 0):
    ctx = _build_list_context(db, redis, status, page)
    return templates.TemplateResponse(request=request, name="_job_list.html", context=ctx)


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, db: DbDep, redis: RedisDep):
    job = db.get_job(job_id)
    if job is None or job.status != JobStatus.FAILED:
        return Response(status_code=404)

    try:
        from rq import Queue

        job.status = JobStatus.QUEUED
        job.error = None
        job.progress = 0.0
        job.progress_message = ""
        job.result_path = None
        job.duration = None
        db.update_job(job)
        redis.delete(f"job:{job.id}")

        q = Queue(connection=redis)
        q.enqueue(
            "whisper_ui.worker.tasks.process_transcription",
            job.id,
            job_timeout="1h",
        )
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)[:ERROR_MAX_LENGTH]
        db.update_job(job)

    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, db: DbDep, filestore: FileStoreDep, redis: RedisDep):
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
async def retry_batch(batch_id: str, db: DbDep, redis: RedisDep):
    all_jobs = db.list_jobs_by_batch(batch_id)
    if not all_jobs:
        return Response(status_code=404)

    from rq import Queue

    q = Queue(connection=redis)
    for job in all_jobs:
        if job.status != JobStatus.FAILED:
            continue
        try:
            job.status = JobStatus.QUEUED
            job.error = None
            job.progress = 0.0
            job.progress_message = ""
            job.result_path = None
            job.duration = None
            db.update_job(job)
            redis.delete(f"job:{job.id}")
            q.enqueue(
                "whisper_ui.worker.tasks.process_transcription",
                job.id,
                job_timeout="1h",
            )
        except Exception:
            logger.exception("Failed to retry job %s", job.id)
            job.status = JobStatus.FAILED
            job.error = "Failed to enqueue retry"
            db.update_job(job)

    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.delete("/jobs/batch/{batch_id}")
async def delete_batch(batch_id: str, db: DbDep, filestore: FileStoreDep, redis: RedisDep):
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
async def batch_download(batch_id: str, db: DbDep, filestore: FileStoreDep, format: str = "srt"):
    all_jobs = db.list_jobs_by_batch(batch_id)
    if not all_jobs:
        return Response(status_code=404)

    try:
        zip_data = create_batch_zip(all_jobs, filestore, format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if zip_data is None:
        return Response(status_code=404)

    filename = f"batch_{batch_id[:8]}.zip"
    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
