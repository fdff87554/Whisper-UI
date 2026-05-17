from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from whisper_ui.core.constants import VIEWER_SEARCH_SEGMENT_LIMIT
from whisper_ui.core.models import JobStatus
from whisper_ui.export.factory import get_exporter
from whisper_ui.web.deps import DbDep, FileStoreDep, make_content_disposition, templates
from whisper_ui.web.validation import validate_hex_id

_MEDIA_MIME_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".m4a": "audio/mp4",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}

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

    search_disabled = result is not None and len(result.segments) > VIEWER_SEARCH_SEGMENT_LIMIT
    # The Download Media button is only useful when the URL job's
    # downloaded media file is still on disk. Retention can reclaim it
    # while the transcript stays — checking here keeps the template free
    # of file-system access and avoids the 404 dead-click the button
    # would otherwise produce.
    media_available = (
        job is not None and job.source_url is not None and filestore.get_source_media_path(job_id) is not None
    )

    return templates.TemplateResponse(
        request=request,
        name="viewer.html",
        context={
            "active_page": "jobs",
            "job": job,
            "result": result,
            "error": error,
            "speaker_colors": speaker_colors,
            "search_disabled": search_disabled,
            "media_available": media_available,
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


@router.get("/viewer/{job_id}/media")
async def media_download(job_id: str, db: DbDep, filestore: FileStoreDep):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id)
    if job is None or not job.source_url:
        raise HTTPException(status_code=404)

    media_path = filestore.get_source_media_path(job_id)
    if media_path is None or not media_path.exists():
        raise HTTPException(status_code=404)

    ext = media_path.suffix.lower()
    mime = _MEDIA_MIME_TYPES.get(ext, "application/octet-stream")
    filename = f"{Path(job.filename).stem}{ext}"

    return FileResponse(
        path=media_path,
        media_type=mime,
        headers={"Content-Disposition": make_content_disposition(filename, "attachment")},
    )
