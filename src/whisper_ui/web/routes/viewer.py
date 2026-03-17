from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from whisper_ui.core.models import JobStatus
from whisper_ui.export.factory import get_exporter
from whisper_ui.web.deps import DbDep, FileStoreDep, make_content_disposition, templates
from whisper_ui.web.validation import validate_hex_id

router = APIRouter()

_SPEAKER_COLOR_CLASSES = [
    "speaker-1",
    "speaker-2",
    "speaker-3",
    "speaker-4",
    "speaker-5",
    "speaker-6",
    "speaker-7",
    "speaker-8",
]


def _build_speaker_colors(segments: list) -> dict[str, str]:
    """Assign a color CSS class to each unique speaker."""
    colors: dict[str, str] = {}
    for seg in segments:
        speaker = getattr(seg, "speaker", None)
        if speaker and speaker not in colors:
            idx = len(colors) % len(_SPEAKER_COLOR_CLASSES)
            colors[speaker] = _SPEAKER_COLOR_CLASSES[idx]
    return colors


@router.get("/viewer", response_class=HTMLResponse)
async def viewer_redirect():
    return RedirectResponse("/jobs", status_code=302)


@router.get("/viewer/{job_id}", response_class=HTMLResponse)
async def viewer_page(request: Request, db: DbDep, filestore: FileStoreDep, job_id: str):
    validate_hex_id(job_id, "job_id")

    job = None
    result = None
    error = None
    speaker_colors: dict[str, str] = {}

    job = db.get_job(job_id)
    if job is None:
        error = "not_found"
    elif job.status != JobStatus.COMPLETED:
        error = "not_completed"
    else:
        result = filestore.load_result(job_id)
        if result is None:
            error = "no_result"
        else:
            speaker_colors = _build_speaker_colors(result.segments)

    return templates.TemplateResponse(
        request=request,
        name="viewer.html",
        context={
            "active_page": "viewer",
            "job": job,
            "result": result,
            "error": error,
            "speaker_colors": speaker_colors,
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
