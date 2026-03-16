from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from whisper_ui.core.constants import ERROR_MAX_LENGTH, MAX_BATCH_SIZE
from whisper_ui.core.models import SUPPORTED_LANGUAGES, WHISPER_MODELS, Job, JobStatus
from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS
from whisper_ui.web.deps import DbDep, FileStoreDep, RedisDep, SettingsDep, templates

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MB

router = APIRouter()


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024**3):.1f} GB"
    return f"{size_bytes / (1024**2):.0f} MB"


async def _read_with_limit(upload: UploadFile, max_size: int) -> bytes | None:
    """Read upload in chunks; return None if file exceeds max_size."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/", response_class=HTMLResponse)
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


@router.post("/upload")
async def upload_submit(
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
    # Server-side validation of select inputs
    if language not in SUPPORTED_LANGUAGES:
        return RedirectResponse("/upload?error=invalid_language", status_code=303)
    if model_name not in WHISPER_MODELS:
        return RedirectResponse("/upload?error=invalid_model", status_code=303)

    # Distinguish "no file selected" from "unsupported format"
    has_any_files = files and any(f.filename for f in files)
    valid_files = [
        f for f in (files or []) if f.filename and PurePosixPath(f.filename).suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not has_any_files:
        return RedirectResponse("/upload?error=no_file", status_code=303)
    if not valid_files:
        return RedirectResponse("/upload?error=no_files", status_code=303)

    if len(valid_files) > MAX_BATCH_SIZE:
        return RedirectResponse(f"/upload?error=too_many&count={len(valid_files)}", status_code=303)

    batch_id = uuid.uuid4().hex if len(valid_files) > 1 else None
    submitted_count = 0

    try:
        from rq import Queue

        q = Queue(connection=redis)
    except Exception:
        return RedirectResponse("/upload?error=queue", status_code=303)

    max_size = settings.max_upload_size
    for uploaded_file in valid_files:
        display_name = PurePosixPath(uploaded_file.filename or "unknown").name

        file_data = await _read_with_limit(uploaded_file, max_size)
        if file_data is None:
            limit_str = _format_size(max_size)
            return RedirectResponse(f"/upload?error=too_large&name={display_name}&limit={limit_str}", status_code=303)

        job = Job(
            filename=display_name,
            language=language,
            model_name=model_name,
            num_speakers=num_speakers if num_speakers > 0 else None,
            enable_diarization=enable_diarization,
            convert_to_traditional=convert_to_traditional,
            batch_id=batch_id,
        )

        dest = filestore.save_upload(job.id, display_name, file_data)
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

    return RedirectResponse(f"/jobs?submitted={submitted_count}", status_code=303)
