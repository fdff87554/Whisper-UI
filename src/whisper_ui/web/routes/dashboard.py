from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from whisper_ui.core.models import JobStatus
from whisper_ui.web.deps import DbDep, RedisDep, templates
from whisper_ui.worker.progress import RedisProgressReporter

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: DbDep, redis: RedisDep):
    status_counts = db.get_status_counts()
    total = sum(status_counts.values())
    active = status_counts.get(JobStatus.QUEUED.value, 0) + status_counts.get(JobStatus.PROCESSING.value, 0)

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    completed_today = db.count_completed_since(today_start)

    active_jobs = db.list_jobs_filtered(status=JobStatus.PROCESSING.value, limit=5)
    queued_jobs = db.list_jobs_filtered(status=JobStatus.QUEUED.value, limit=5)
    active_jobs = active_jobs + queued_jobs
    active_jobs = active_jobs[:5]

    progress_data: dict[str, dict[str, str]] = {}
    for job in active_jobs:
        progress_data[job.id] = RedisProgressReporter.get_progress(redis, job.id)

    recent_completed = db.list_jobs_filtered(status=JobStatus.COMPLETED.value, limit=5)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "dashboard",
            "total_jobs": total,
            "active_count": active,
            "completed_today": completed_today,
            "active_jobs": active_jobs,
            "progress_data": progress_data,
            "recent_completed": recent_completed,
            "status_counts": status_counts,
        },
    )


@router.get("/dashboard/active", response_class=HTMLResponse)
async def dashboard_active_fragment(request: Request, db: DbDep, redis: RedisDep):
    active_jobs = db.list_jobs_filtered(status=JobStatus.PROCESSING.value, limit=5)
    queued_jobs = db.list_jobs_filtered(status=JobStatus.QUEUED.value, limit=5)
    active_jobs = active_jobs + queued_jobs
    active_jobs = active_jobs[:5]

    progress_data: dict[str, dict[str, str]] = {}
    for job in active_jobs:
        progress_data[job.id] = RedisProgressReporter.get_progress(redis, job.id)

    return templates.TemplateResponse(
        request=request,
        name="_dashboard_active.html",
        context={
            "active_jobs": active_jobs,
            "progress_data": progress_data,
        },
    )
