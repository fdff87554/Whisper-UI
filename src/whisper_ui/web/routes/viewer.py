from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from whisper_ui.core.constants import DEFAULT_JOB_LIST_LIMIT
from whisper_ui.core.models import JobStatus
from whisper_ui.export.factory import get_exporter
from whisper_ui.web.deps import DbDep, FileStoreDep, make_content_disposition, templates
from whisper_ui.web.validation import validate_hex_id

router = APIRouter()


@router.get("/viewer", response_class=HTMLResponse)
@router.get("/viewer/{job_id}", response_class=HTMLResponse)
async def viewer_page(request: Request, db: DbDep, filestore: FileStoreDep, job_id: str | None = None):
    completed_jobs = db.list_jobs_filtered(status=JobStatus.COMPLETED.value, limit=DEFAULT_JOB_LIST_LIMIT)

    job = None
    result = None
    error = None

    if job_id:
        validate_hex_id(job_id, "job_id")
        job = db.get_job(job_id)
        if job is None:
            error = "not_found"
        elif job.status != JobStatus.COMPLETED:
            error = "not_completed"
        else:
            result = filestore.load_result(job_id)
            if result is None:
                error = "no_result"

    return templates.TemplateResponse(
        request=request,
        name="viewer.html",
        context={
            "active_page": "viewer",
            "completed_jobs": completed_jobs,
            "job": job,
            "result": result,
            "error": error,
            "selected_job_id": job_id,
        },
    )


@router.get("/viewer/{job_id}/export/{format_name}")
async def export_download(job_id: str, format_name: str, db: DbDep, filestore: FileStoreDep):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id)
    if job is None or job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=404)

    result = filestore.load_result(job_id)
    if result is None:
        raise HTTPException(status_code=404)

    try:
        exporter = get_exporter(format_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    data = exporter.export(result)
    filename = f"{Path(job.filename).stem}{exporter.file_extension}"

    return Response(
        content=data,
        media_type=exporter.mime_type,
        headers={"Content-Disposition": make_content_disposition(filename)},
    )
