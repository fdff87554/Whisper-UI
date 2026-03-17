from __future__ import annotations

import asyncio
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import escape

from whisper_ui.core.constants import ERROR_MAX_LENGTH, MAX_BATCH_SIZE
from whisper_ui.core.languages import SUPPORTED_LANGUAGES, WHISPER_MODELS
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.deps import DbDep, FileStoreDep, RedisDep, SettingsDep, templates
from whisper_ui.web.url_validation import PlaylistURLError, YouTubeURLError, validate_youtube_url

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MB

router = APIRouter()


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024**3):.1f} GB"
    return f"{size_bytes / (1024**2):.0f} MB"


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _htmx_error(message: str) -> Response:
    """Return an HTML fragment for htmx to swap into the feedback area."""
    html = f'<div class="alert alert-error" role="alert">{escape(message)}</div>'
    return HTMLResponse(content=html)


async def _stream_to_file(upload: UploadFile, dest: Path, max_size: int) -> bool:
    """Stream upload chunks directly to disk. Return False if file exceeds max_size."""
    total = 0
    try:
        with dest.open("wb") as f:
            while True:
                chunk = await upload.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    return False
                f.write(chunk)
    except (Exception, asyncio.CancelledError):
        dest.unlink(missing_ok=True)
        raise
    return True


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, settings: SettingsDep):
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={
            "active_page": "upload",
            "settings": settings,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "supported_languages": SUPPORTED_LANGUAGES,
            "whisper_models": WHISPER_MODELS,
        },
    )


def _error_redirect_or_fragment(request: Request, redirect_url: str, message: str) -> Response:
    """Return redirect for normal requests, HTML fragment for htmx requests."""
    if _is_htmx(request):
        return _htmx_error(message)
    return RedirectResponse(redirect_url, status_code=303)


@router.post("/upload")
async def upload_submit(
    request: Request,
    db: DbDep,
    filestore: FileStoreDep,
    redis: RedisDep,
    settings: SettingsDep,
    files: Annotated[list[UploadFile] | None, File()] = None,
    language: Annotated[str, Form()] = "zh",
    model_name: Annotated[str, Form()] = "large-v3",
    num_speakers: Annotated[int, Form()] = 0,
    enable_diarization: Annotated[bool, Form()] = False,
    convert_to_traditional: Annotated[bool, Form()] = False,
):
    htmx = _is_htmx(request)

    # Server-side validation of select inputs
    if language not in SUPPORTED_LANGUAGES:
        msg = ui_labels.UPLOAD_INVALID_LANGUAGE.format(value=language)
        return _error_redirect_or_fragment(request, f"/upload?error=invalid_language&value={quote(language)}", msg)
    if model_name not in WHISPER_MODELS:
        msg = ui_labels.UPLOAD_INVALID_MODEL.format(value=model_name)
        return _error_redirect_or_fragment(request, f"/upload?error=invalid_model&value={quote(model_name)}", msg)

    # Distinguish "no file selected" from "unsupported format"
    has_any_files = files and any(f.filename for f in files)
    valid_files = [
        f for f in (files or []) if f.filename and PurePosixPath(f.filename).suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not has_any_files:
        return _error_redirect_or_fragment(request, "/upload?error=no_file", ui_labels.UPLOAD_NO_FILE)
    if not valid_files:
        return _error_redirect_or_fragment(request, "/upload?error=no_files", ui_labels.UPLOAD_NO_SUPPORTED_FILES)

    if len(valid_files) > MAX_BATCH_SIZE:
        msg = ui_labels.UPLOAD_BATCH_EXCEEDS_LIMIT.format(limit=MAX_BATCH_SIZE, count=len(valid_files))
        return _error_redirect_or_fragment(request, f"/upload?error=too_many&count={len(valid_files)}", msg)

    batch_id = uuid.uuid4().hex if len(valid_files) > 1 else None
    submitted_count = 0

    try:
        from rq import Queue

        q = Queue(connection=redis)
    except Exception:
        msg = ui_labels.UPLOAD_QUEUE_ERROR.format(error="Redis")
        return _error_redirect_or_fragment(request, "/upload?error=queue", msg)

    max_size = settings.max_upload_size
    for uploaded_file in valid_files:
        display_name = PurePosixPath(uploaded_file.filename or "unknown").name

        job = Job(
            filename=display_name,
            language=language,
            model_name=model_name,
            num_speakers=num_speakers if num_speakers > 0 else None,
            enable_diarization=enable_diarization,
            convert_to_traditional=convert_to_traditional,
            batch_id=batch_id,
        )

        dest = filestore.prepare_upload_path(job.id, display_name)
        within_limit = await _stream_to_file(uploaded_file, dest, max_size)
        if not within_limit:
            dest.unlink(missing_ok=True)
            limit_str = _format_size(max_size)
            msg = ui_labels.UPLOAD_FILE_TOO_LARGE.format(name=display_name, limit=limit_str)
            return _error_redirect_or_fragment(
                request,
                f"/upload?error=too_large&name={quote(display_name)}&limit={quote(limit_str)}",
                msg,
            )

        job.filepath = str(dest)
        job.status = JobStatus.QUEUED
        db.insert_job(job)

        try:
            q.enqueue(
                "whisper_ui.worker.tasks.process_transcription",
                job.id,
                job_timeout="1h",
            )
            submitted_count += 1
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)[:ERROR_MAX_LENGTH]
            db.update_job(job)

    redirect_url = f"/jobs?submitted={submitted_count}"
    if htmx:
        return Response(status_code=204, headers={"HX-Redirect": redirect_url})
    return RedirectResponse(redirect_url, status_code=303)


@router.post("/upload/url")
async def upload_url_submit(
    request: Request,
    db: DbDep,
    filestore: FileStoreDep,
    redis: RedisDep,
    settings: SettingsDep,
    url: Annotated[str, Form()],
    language: Annotated[str, Form()] = "zh",
    model_name: Annotated[str, Form()] = "large-v3",
    num_speakers: Annotated[int, Form()] = 0,
    enable_diarization: Annotated[bool, Form()] = False,
    convert_to_traditional: Annotated[bool, Form()] = False,
):
    htmx = _is_htmx(request)

    if language not in SUPPORTED_LANGUAGES:
        msg = ui_labels.UPLOAD_INVALID_LANGUAGE.format(value=language)
        return _error_redirect_or_fragment(request, f"/upload?error=invalid_language&value={quote(language)}", msg)
    if model_name not in WHISPER_MODELS:
        msg = ui_labels.UPLOAD_INVALID_MODEL.format(value=model_name)
        return _error_redirect_or_fragment(request, f"/upload?error=invalid_model&value={quote(model_name)}", msg)

    try:
        clean_url = validate_youtube_url(url)
    except PlaylistURLError:
        msg = ui_labels.UPLOAD_URL_PLAYLIST_NOT_SUPPORTED
        return _error_redirect_or_fragment(request, "/upload?error=playlist", msg)
    except YouTubeURLError:
        msg = ui_labels.UPLOAD_INVALID_URL
        return _error_redirect_or_fragment(request, "/upload?error=invalid_url", msg)

    job = Job(
        filename=clean_url,
        source_url=clean_url,
        language=language,
        model_name=model_name,
        num_speakers=num_speakers if num_speakers > 0 else None,
        enable_diarization=enable_diarization,
        convert_to_traditional=convert_to_traditional,
    )

    upload_dir = filestore.prepare_upload_path(job.id, "_").parent
    job.filepath = str(upload_dir)
    job.status = JobStatus.QUEUED
    db.insert_job(job)

    try:
        from rq import Queue

        q = Queue(connection=redis)
        q.enqueue(
            "whisper_ui.worker.tasks.process_transcription",
            job.id,
            job_timeout="2h",
        )
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)[:ERROR_MAX_LENGTH]
        db.update_job(job)
        msg = ui_labels.UPLOAD_QUEUE_ERROR.format(error=str(e))
        return _error_redirect_or_fragment(request, "/upload?error=queue", msg)

    redirect_url = "/jobs?submitted=1"
    if htmx:
        return Response(status_code=204, headers={"HX-Redirect": redirect_url})
    return RedirectResponse(redirect_url, status_code=303)
