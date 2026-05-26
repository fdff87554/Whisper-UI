from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import OrderedDict
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from whisper_ui.core.constants import DEFAULT_JOBS_PER_PAGE
from whisper_ui.core.languages import SUPPORTED_LANGUAGES, WHISPER_MODELS
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.pipeline.audio_probe import get_audio_duration_seconds
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.auth import owner_filter
from whisper_ui.web.batch_zip import create_batch_zip
from whisper_ui.web.deps import (
    CurrentUserDep,
    DbDep,
    FileStoreDep,
    RedisDep,
    SettingsDep,
    make_content_disposition,
    templates,
)
from whisper_ui.web.validation import clamp_num_speakers, validate_hex_id
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


def _get_progress_data(redis, jobs: list[Job]) -> dict[str, dict[str, str]]:
    data = {}
    for job in jobs:
        if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
            data[job.id] = RedisProgressReporter.get_progress(redis, job.id)
    return data


def _build_media_available_map(filestore, jobs: list[Job]) -> dict[str, bool]:
    """Pre-compute which URL jobs still have their downloaded media on disk.

    The Download Media button in _job_card.html should hide once retention
    has reclaimed the source media; doing the stat() up front (rather than
    inside the template) keeps the template free of FS access and matches
    the pattern used for `media_available` on the viewer route.
    """
    return {job.id: filestore.get_source_media_path(job.id) is not None for job in jobs if job.source_url}


def _build_list_context(db: JobDatabase, redis, filestore, status: str, page: int, owner_id: int | None) -> dict:
    status_filter = status or None
    total_count = db.count_jobs(status=status_filter, owner_id=owner_id)
    total_pages = max(1, math.ceil(total_count / DEFAULT_JOBS_PER_PAGE))
    page = max(0, page)
    page = min(page, total_pages - 1)

    offset = page * DEFAULT_JOBS_PER_PAGE
    jobs = db.list_jobs_filtered(status=status_filter, limit=DEFAULT_JOBS_PER_PAGE, offset=offset, owner_id=owner_id)

    groups = _group_jobs_by_batch(jobs)
    batch_ids = {key for key, _ in groups if not key.startswith("_single:")}
    batch_info = db.get_batch_stats(batch_ids, owner_id=owner_id)
    progress_data = _get_progress_data(redis, jobs)
    media_available_map = _build_media_available_map(filestore, jobs)
    has_active = db.has_active_jobs(owner_id=owner_id)

    return {
        "groups": groups,
        "batch_info": batch_info,
        "progress_data": progress_data,
        "media_available_map": media_available_map,
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
    filestore: FileStoreDep,
    settings: SettingsDep,
    user: CurrentUserDep,
    status: str = "",
    page: int = 0,
):
    if status not in _VALID_STATUS_FILTERS:
        status = ""
    owner_id = owner_filter(user)
    ctx = _build_list_context(db, redis, filestore, status, page, owner_id)
    ctx["active_page"] = "jobs"
    ctx["status_counts"] = db.get_status_counts(owner_id=owner_id)
    # The re-transcribe modal (full page only, not the /jobs/list fragment)
    # reuses the upload form's option choices.
    ctx["supported_languages"] = SUPPORTED_LANGUAGES
    ctx["whisper_models"] = WHISPER_MODELS
    # Pass only the derived availability flags the template needs, not the
    # whole Settings object, so a future sensitive field can never leak into
    # the rendered HTML by accident.
    ctx["diarization_available"] = settings.diarization_available
    ctx["llm_correction_available"] = settings.llm_correction_available
    return templates.TemplateResponse(request=request, name="jobs.html", context=ctx)


@router.get("/jobs/list", response_class=HTMLResponse)
async def jobs_list_fragment(
    request: Request,
    db: DbDep,
    redis: RedisDep,
    filestore: FileStoreDep,
    user: CurrentUserDep,
    status: str = "",
    page: int = 0,
):
    ctx = _build_list_context(db, redis, filestore, status, page, owner_filter(user))
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
        return get_audio_duration_seconds(job.filepath, job_id=job.id)
    except Exception:  # pragma: no cover - defensive, ffprobe helper already swallows
        logger.exception("Failed to probe duration for retry of job %s", job.id)
        return None


_BULK_ACTIONS = frozenset({"retry", "delete", "export"})


def _parse_bulk_job_ids(form_value: str) -> list[str]:
    """Parse the job_ids form field as a comma-separated list, trimmed and de-duplicated.

    Order-preserving so the caller's selection order survives the round-trip,
    which matters for the export ZIP file listing.
    """
    seen: set[str] = set()
    job_ids: list[str] = []
    for raw in form_value.split(","):
        candidate = raw.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            job_ids.append(candidate)
    return job_ids


# Registered before /jobs/{job_id}/retry so that "/jobs/bulk/retry" does
# not get matched by the per-job route with job_id="bulk".
@router.post("/jobs/bulk/{action}")
async def bulk_job_action(
    request: Request,
    action: str,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    filestore: FileStoreDep,
    user: CurrentUserDep,
):
    """Apply `action` to every job_id in the form payload owned by the user.

    Supported actions: retry (FAILED → QUEUED + enqueue), delete (COMPLETED
    or FAILED only), export (zip COMPLETED results in the requested format).
    Per-job ownership is enforced via owner_filter; jobs the user does not
    own are reported as failed and do not leak existence.

    Partial failures do not abort the whole operation. The response carries
    an HX-Trigger-After-Settle "bulkPartial" event so the client can surface
    a toast covering both the success count and the failure count.
    """
    if action not in _BULK_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown bulk action")
    form = await request.form()
    job_ids_raw = form.get("job_ids", "")
    if not isinstance(job_ids_raw, str):
        raise HTTPException(status_code=400, detail="Invalid job_ids payload")
    job_ids = _parse_bulk_job_ids(job_ids_raw)
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job ids provided")
    for job_id in job_ids:
        validate_hex_id(job_id, "job_id")

    owner_id = owner_filter(user)
    if action == "export":
        format_name = form.get("format_name", "srt") or "srt"
        if not isinstance(format_name, str):
            raise HTTPException(status_code=400, detail="Invalid format_name payload")
        jobs = [job for job_id in job_ids if (job := db.get_job(job_id, owner_id=owner_id)) is not None]
        if not jobs:
            raise HTTPException(status_code=404, detail="No matching jobs")
        try:
            zip_data = await asyncio.to_thread(create_batch_zip, jobs, filestore, format_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        if zip_data is None:
            raise HTTPException(status_code=404, detail="No completed results in selection")
        filename = f"selection_{format_name}.zip"
        return Response(
            content=zip_data,
            media_type="application/zip",
            headers={"Content-Disposition": make_content_disposition(filename)},
        )

    succeeded = 0
    failed = 0
    for job_id in job_ids:
        job = db.get_job(job_id, owner_id=owner_id)
        if job is None:
            failed += 1
            continue
        if action == "retry":
            if job.status != JobStatus.FAILED:
                failed += 1
                continue
            try:
                retry_duration = await asyncio.to_thread(_probe_retry_duration, job)
                job.status = JobStatus.QUEUED
                job.error = None
                job.progress = 0.0
                job.progress_message = ""
                job.result_path = None
                job.duration = retry_duration
                db.update_job(job)
                enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
                succeeded += 1
            except Exception:
                logger.exception("bulk retry failed for job %s", job.id)
                job.status = JobStatus.FAILED
                job.error = ui_labels.UPLOAD_ENQUEUE_FAILED
                db.update_job(job)
                failed += 1
        else:  # delete
            if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
                failed += 1
                continue
            try:
                await asyncio.to_thread(filestore.delete_job_files, job.id)
            except OSError:
                logger.exception("bulk delete: filestore reclaim failed for job %s", job.id)
                failed += 1
                continue
            db.delete_job(job.id)
            redis.delete(f"job:{job.id}")
            succeeded += 1

    logger.info(
        "bulk action complete: action=%s user_id=%s succeeded=%d failed=%d total=%d",
        action,
        user.id,
        succeeded,
        failed,
        len(job_ids),
    )
    headers = {"HX-Trigger": "refreshJobList"}
    if failed > 0:
        headers["HX-Trigger-After-Settle"] = json.dumps({"bulkPartial": {"ok": succeeded, "failed": failed}})
    elif succeeded > 0:
        headers["HX-Trigger-After-Settle"] = json.dumps({"bulkComplete": {"ok": succeeded}})
    return Response(status_code=204, headers=headers)


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    filestore: FileStoreDep,
    user: CurrentUserDep,
):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None or job.status != JobStatus.FAILED:
        # 404 (not 403) so cross-user access does not leak job existence.
        return Response(status_code=404)

    previous_error = job.error
    try:
        retry_duration = await asyncio.to_thread(_probe_retry_duration, job)
        job.status = JobStatus.QUEUED
        job.error = None
        job.progress = 0.0
        job.progress_message = ""
        job.result_path = None
        job.duration = retry_duration
        db.update_job(job)
        enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
        logger.info(
            "job retried: job_id=%s user_id=%s filename=%r previous_error=%r",
            job.id,
            user.id,
            job.filename,
            previous_error or "(none)",
        )
    except Exception:
        logger.exception("Failed to enqueue retry for job %s", job.id)
        job.status = JobStatus.FAILED
        job.error = ui_labels.UPLOAD_ENQUEUE_FAILED
        db.update_job(job)

    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.post("/jobs/{job_id}/re-transcribe")
async def re_transcribe_job(
    job_id: str,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    filestore: FileStoreDep,
    user: CurrentUserDep,
    language: Annotated[str, Form()] = "zh",
    model_name: Annotated[str, Form()] = "large-v3",
    num_speakers: Annotated[int, Form()] = 0,
    enable_diarization: Annotated[bool, Form()] = False,
    convert_to_traditional: Annotated[bool, Form()] = False,
    llm_correction_enabled: Annotated[bool, Form()] = False,
):
    """Re-run transcription on a completed job's audio with new parameters.

    Creates a *new* job (a new version) that reuses the source audio without
    re-uploading, leaving the original transcript untouched so the user can
    compare versions. ``source_job_id`` links the new job back to the version
    chain's root for UI grouping.
    """
    validate_hex_id(job_id, "job_id")
    src = db.get_job(job_id, owner_id=owner_filter(user))
    # 404 (not 403/409) for missing/cross-user so existence does not leak;
    # only a COMPLETED job has a transcript worth preserving as a prior version.
    if src is None or src.status != JobStatus.COMPLETED:
        return Response(status_code=404)

    if language not in SUPPORTED_LANGUAGES:
        return HTMLResponse(ui_labels.UPLOAD_INVALID_LANGUAGE.format(value=language), status_code=400)
    if model_name not in WHISPER_MODELS:
        return HTMLResponse(ui_labels.UPLOAD_INVALID_MODEL.format(value=model_name), status_code=400)

    # Flat chain: every version points at the original root so grouping is a
    # single lookup. Re-transcribing a version re-roots to the same source.
    root_id = src.source_job_id or src.id
    new_job = Job(
        filename=src.filename,
        language=language,
        model_name=model_name,
        num_speakers=clamp_num_speakers(num_speakers) or None,
        # Clamp opt-in flags to deployment availability so the new version's
        # persisted flags are honest even if the source job (or a tampered
        # request) carries flags this deployment cannot run.
        enable_diarization=enable_diarization and settings.diarization_available,
        convert_to_traditional=convert_to_traditional,
        llm_correction_enabled=llm_correction_enabled and settings.llm_correction_available,
        source_url=src.source_url,
        owner_id=user.id,
        source_job_id=root_id,
        status=JobStatus.QUEUED,
    )

    if src.source_url:
        # URL jobs always re-download: enqueue_pipeline prepends a download
        # stage whenever source_url is set, so there is no local media to
        # copy. Point filepath at the new job's own dir, like upload_url_submit.
        new_job.filepath = str(filestore.prepare_upload_path(new_job.id, "_").parent)
    else:
        # Copy from the source job's OWN upload dir (each version owns an
        # independent copy), not the root — so deleting any version never
        # strands another. ``to_thread`` keeps a large copy off the event loop.
        try:
            dest = await asyncio.to_thread(filestore.copy_source_for_new_job, src.id, src.filename, new_job.id)
        except FileNotFoundError:
            # The retention sweep already reclaimed the source audio; there is
            # nothing to re-transcribe from, so do not insert a doomed job.
            logger.info(
                "re-transcribe rejected: source audio gone for job_id=%s user_id=%s",
                src.id,
                user.id,
            )
            return HTMLResponse(ui_labels.JOBS_RE_TRANSCRIBE_SOURCE_GONE, status_code=409)
        new_job.filepath = str(dest)
        new_job.duration = await asyncio.to_thread(get_audio_duration_seconds, dest, job_id=new_job.id)

    db.insert_job(new_job)
    try:
        enqueue_pipeline(new_job, redis=redis, settings=settings, filestore=filestore)
        logger.info(
            "job re-transcribe queued: new_job_id=%s source_job_id=%s user_id=%s model=%s lang=%s",
            new_job.id,
            root_id,
            user.id,
            model_name,
            language,
        )
    except Exception:
        logger.exception("Failed to enqueue re-transcribe job %s", new_job.id)
        new_job.status = JobStatus.FAILED
        new_job.error = ui_labels.UPLOAD_ENQUEUE_FAILED
        db.update_job(new_job)

    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, db: DbDep, filestore: FileStoreDep, redis: RedisDep, user: CurrentUserDep):
    validate_hex_id(job_id, "job_id")
    job = db.get_job(job_id, owner_id=owner_filter(user))
    if job is None:
        return Response(status_code=404)
    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
        return Response(status_code=409)

    try:
        await asyncio.to_thread(filestore.delete_job_files, job.id)
    except OSError as exc:
        # Keep the DB row + Redis state + audit log silent so retrying the
        # delete is a no-op-then-real-delete instead of "DB row gone but
        # files still on disk". See PR #53 review F2.
        logger.error(
            "job delete aborted: filestore reclaim failed for job_id=%s user_id=%s: %s",
            job.id,
            user.id,
            exc.__class__.__name__,
        )
        return Response(status_code=500)
    db.delete_job(job.id)
    redis.delete(f"job:{job.id}")
    logger.info(
        "job deleted: job_id=%s user_id=%s filename=%r status_at_delete=%s",
        job.id,
        user.id,
        job.filename,
        job.status,
    )
    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.post("/jobs/batch/{batch_id}/retry")
async def retry_batch(
    batch_id: str,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    filestore: FileStoreDep,
    user: CurrentUserDep,
):
    validate_hex_id(batch_id, "batch_id")
    all_jobs = db.list_jobs_by_batch(batch_id, owner_id=owner_filter(user))
    if not all_jobs:
        return Response(status_code=404)

    retried = 0
    for job in all_jobs:
        if job.status != JobStatus.FAILED:
            continue
        try:
            retry_duration = await asyncio.to_thread(_probe_retry_duration, job)
            job.status = JobStatus.QUEUED
            job.error = None
            job.progress = 0.0
            job.progress_message = ""
            job.result_path = None
            job.duration = retry_duration
            db.update_job(job)
            enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
            retried += 1
        except Exception:
            logger.exception("Failed to retry job %s", job.id)
            job.status = JobStatus.FAILED
            job.error = "Failed to enqueue retry"
            db.update_job(job)

    logger.info(
        "batch retry finished: batch_id=%s user_id=%s retried=%d total=%d",
        batch_id,
        user.id,
        retried,
        len(all_jobs),
    )
    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.delete("/jobs/batch/{batch_id}")
async def delete_batch(batch_id: str, db: DbDep, filestore: FileStoreDep, redis: RedisDep, user: CurrentUserDep):
    validate_hex_id(batch_id, "batch_id")
    all_jobs = db.list_jobs_by_batch(batch_id, owner_id=owner_filter(user))
    if not all_jobs:
        return Response(status_code=404)

    deleted = 0
    failed = 0
    for job in all_jobs:
        if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
            continue
        try:
            await asyncio.to_thread(filestore.delete_job_files, job.id)
        except OSError as exc:
            # Per-job atomicity: keep the failing job's DB row + Redis
            # state so the user can retry just that one. Other jobs in
            # the batch still get cleaned. See PR #53 review F2.
            logger.error(
                "batch delete skipped one job: job_id=%s batch_id=%s user_id=%s: %s",
                job.id,
                batch_id,
                user.id,
                exc.__class__.__name__,
            )
            failed += 1
            continue
        db.delete_job(job.id)
        redis.delete(f"job:{job.id}")
        deleted += 1

    logger.info(
        "batch deleted: batch_id=%s user_id=%s deleted=%d failed=%d total=%d",
        batch_id,
        user.id,
        deleted,
        failed,
        len(all_jobs),
    )
    return Response(status_code=204, headers={"HX-Trigger": "refreshJobList"})


@router.get("/jobs/batch/{batch_id}/download")
async def batch_download(
    batch_id: str,
    db: DbDep,
    filestore: FileStoreDep,
    user: CurrentUserDep,
    format_name: str = "srt",
):
    validate_hex_id(batch_id, "batch_id")
    all_jobs = db.list_jobs_by_batch(batch_id, owner_id=owner_filter(user))
    if not all_jobs:
        raise HTTPException(status_code=404, detail="Batch not found")

    try:
        zip_data = await asyncio.to_thread(create_batch_zip, all_jobs, filestore, format_name)
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
