from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import escape

from whisper_ui.core.constants import MAX_BATCH_SIZE
from whisper_ui.core.languages import DEFAULT_WHISPER_MODEL, LANGUAGE_CHOICES, WHISPER_MODELS
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.core.url_validation import (
    GoogleDriveURLError,
    TwitterURLError,
    UnsupportedPlaylistTypeError,
    YouTubeURLError,
    is_google_drive_url,
    is_twitter_url,
    is_youtube_playlist_url,
    validate_google_drive_url,
    validate_twitter_url,
    validate_youtube_playlist_url,
    validate_youtube_url,
)
from whisper_ui.pipeline.audio_probe import get_audio_duration_seconds
from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.deps import CurrentUserDep, DbDep, FileStoreDep, RedisDep, SettingsDep, templates
from whisper_ui.web.flash import set_flash
from whisper_ui.web.playlist import (
    PlaylistEmptyError,
    PlaylistFetchError,
    PlaylistInfo,
    PlaylistTooLargeError,
    PlaylistUnavailableError,
    expand_playlist,
)
from whisper_ui.web.validation import clamp_num_speakers, mark_enqueue_failed, validate_upload_options
from whisper_ui.worker.pipeline_dispatcher import enqueue_pipeline

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MB

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_upload_toast(
    submitted: int, failed: int = 0, skipped: int = 0, deduped: int = 0, skipped_files: int = 0
) -> tuple[str, str]:
    """Compose the post-upload toast message and category.

    Mirrors the label concatenation the client used to do from query params:
    a success base line plus optional failed / skipped-url / skipped-file /
    deduped clauses. The category is ``warning`` whenever anything was failed
    or skipped.
    """
    message = ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", str(submitted))
    if failed:
        message += ui_labels.TOAST_UPLOAD_FAILED.replace("{count}", str(failed))
    if skipped:
        message += ui_labels.TOAST_URL_SKIPPED.replace("{count}", str(skipped))
    if skipped_files:
        message += ui_labels.TOAST_FILE_SKIPPED.replace("{count}", str(skipped_files))
    if deduped:
        message += ui_labels.TOAST_URL_DEDUPED.replace("{count}", str(deduped))
    category = "warning" if (failed or skipped or skipped_files) else "success"
    return message, category


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024**3):.1f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024**2):.0f} MB"
    # Sub-MB limits would otherwise round to "0 MB" in the rejection toast.
    return f"{size_bytes / 1024:.0f} KB"


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _htmx_error(message: str) -> Response:
    """Return an HTML fragment for htmx to swap into the feedback area."""
    html = f'<div class="alert alert-error" role="alert">{escape(message)}</div>'
    return HTMLResponse(content=html)


# Number of leading bytes inspected to reject obvious non-media uploads.
_MAGIC_SNIFF_BYTES = 16
# Binary signatures for file types that are never audio/video. This is a
# denylist, not an allowlist: it rejects a payload disguised with a media
# extension (e.g. a PDF renamed to .mp3) without risking false rejection of
# the many legitimate media containers. ffmpeg remains the real gate
# downstream — anything that slips past here still fails preprocessing.
_DENY_UPLOAD_SIGNATURES = (
    b"%PDF",  # PDF
    b"MZ",  # Windows PE / DOS executable
    b"\x7fELF",  # ELF executable
    b"PK\x03\x04",  # ZIP / Office / jar
    b"\x1f\x8b",  # gzip
    b"\xd0\xcf\x11\xe0",  # legacy OLE (old Office)
)


def _is_disallowed_upload(head: bytes) -> bool:
    """Return True for leading bytes that clearly belong to a non-media file."""
    stripped = head.lstrip()
    if stripped.startswith((b"<", b"#!")):  # HTML/XML/SVG markup or a script
        return True
    return head.startswith(_DENY_UPLOAD_SIGNATURES)


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
                # Offload the blocking disk write so a slow disk during a
                # GB-scale upload does not stall the event loop between reads.
                await asyncio.to_thread(f.write, chunk)
    except (Exception, asyncio.CancelledError):
        dest.unlink(missing_ok=True)
        raise
    return True


_UPLOAD_TABS = frozenset({"files", "folder", "url"})


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, settings: SettingsDep, user: CurrentUserDep, mode: str = "files"):
    # `mode` is a UX hint from the dashboard quick-action cards
    # (/upload?mode=folder|url), not a security boundary — an unknown
    # value simply falls back to the default files tab.
    initial_tab = mode if mode in _UPLOAD_TABS else "files"
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={
            "active_page": "upload",
            # Only the derived values the form needs — not the whole Settings
            # object — so no sensitive field can leak into the rendered HTML.
            "diarization_available": settings.diarization_available,
            "diarization_default": settings.diarization_default_for_form,
            "llm_correction_available": settings.llm_correction_available,
            "default_language": settings.language,
            "default_model": settings.whisper_model,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
            "supported_languages": LANGUAGE_CHOICES,
            "whisper_models": WHISPER_MODELS,
            "initial_tab": initial_tab,
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
    user: CurrentUserDep,
    files: Annotated[list[UploadFile] | None, File()] = None,
    language: Annotated[str, Form()] = "zh",
    model_name: Annotated[str, Form()] = DEFAULT_WHISPER_MODEL,
    num_speakers: Annotated[int, Form()] = 0,
    enable_diarization: Annotated[bool, Form()] = False,
    convert_to_traditional: Annotated[bool, Form()] = False,
    llm_correction_enabled: Annotated[bool, Form()] = False,
):
    htmx = _is_htmx(request)

    # Server-side validation of select inputs
    option_error = validate_upload_options(language, model_name)
    if option_error:
        return _error_redirect_or_fragment(
            request,
            f"/upload?error={option_error.error_code}&value={quote(option_error.value)}",
            option_error.message,
        )

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
    failed_count = 0
    skipped_count = 0
    # Captured from the first skipped file so that a batch where *every* file is
    # invalid can surface a precise reason on the upload page instead of a
    # "0 submitted" toast.
    first_skip: tuple[str, str] | None = None

    logger.info(
        "upload batch starting: user_id=%s files=%d batch_id=%s",
        user.id,
        len(valid_files),
        batch_id or "-",
    )

    max_size = settings.max_upload_size
    for uploaded_file in valid_files:
        display_name = PurePosixPath(uploaded_file.filename or "unknown").name

        # Reject non-media content by skipping just this file, not the whole
        # batch: earlier valid files in the loop have already been inserted and
        # enqueued, so an early return here would leave a partial batch while
        # telling the user the upload failed (prompting a duplicate re-upload).
        header = await uploaded_file.read(_MAGIC_SNIFF_BYTES)
        await uploaded_file.seek(0)
        if _is_disallowed_upload(header):
            logger.warning(
                "upload skipped (non-media content): user_id=%s filename=%r",
                user.id,
                display_name,
            )
            skipped_count += 1
            if first_skip is None:
                msg = ui_labels.UPLOAD_INVALID_FILE_CONTENT.format(name=display_name)
                first_skip = (f"/upload?error=invalid_content&name={quote(display_name)}", msg)
            continue

        job = Job(
            filename=display_name,
            language=language,
            model_name=model_name,
            num_speakers=clamp_num_speakers(num_speakers) or None,
            # Clamp opt-in flags to what this deployment can actually run so the
            # persisted flag is honest and no no-op stage is enqueued.
            enable_diarization=enable_diarization and settings.diarization_available,
            convert_to_traditional=convert_to_traditional,
            llm_correction_enabled=llm_correction_enabled and settings.llm_correction_available,
            batch_id=batch_id,
            owner_id=user.id,
        )

        dest = filestore.prepare_upload_path(job.id, display_name)
        try:
            within_limit = await _stream_to_file(uploaded_file, dest, max_size)
        except OSError:
            # Disk full / permission / I/O error while saving. Degrade to a
            # per-file skip like the content and size checks above, instead of
            # letting the OSError escape to a 500 that aborts the whole batch
            # (leaving earlier files already queued and prompting a re-upload).
            dest.unlink(missing_ok=True)
            logger.error("upload skipped (write failed): user_id=%s filename=%r", user.id, display_name)
            skipped_count += 1
            if first_skip is None:
                msg = ui_labels.UPLOAD_SAVE_FAILED.format(name=display_name)
                first_skip = (f"/upload?error=save_failed&name={quote(display_name)}", msg)
            continue
        if not within_limit:
            dest.unlink(missing_ok=True)
            limit_str = _format_size(max_size)
            logger.warning(
                "upload skipped: user_id=%s filename=%r exceeds max_size=%d",
                user.id,
                display_name,
                max_size,
            )
            skipped_count += 1
            if first_skip is None:
                msg = ui_labels.UPLOAD_FILE_TOO_LARGE.format(name=display_name, limit=limit_str)
                first_skip = (f"/upload?error=too_large&name={quote(display_name)}&limit={quote(limit_str)}", msg)
            continue

        job.filepath = str(dest)
        job.duration = await asyncio.to_thread(get_audio_duration_seconds, dest, job_id=job.id)
        job.status = JobStatus.QUEUED
        db.insert_job(job)
        logger.info(
            "upload job inserted: job_id=%s user_id=%s filename=%r duration=%s model=%s diarize=%s llm=%s",
            job.id,
            user.id,
            display_name,
            f"{job.duration:.1f}" if job.duration else "unknown",
            model_name,
            job.enable_diarization,
            job.llm_correction_enabled,
        )

        try:
            # Offload the synchronous Redis/RQ enqueue so a large batch (up to
            # MAX_BATCH_SIZE) does not monopolise the event loop and stall
            # other requests (e.g. the /jobs poll). enqueue_pipeline touches
            # only Redis/RQ/filestore (the redis client is thread-safe); the
            # SQLite insert_job above stays on the loop per the single-
            # connection design.
            await asyncio.to_thread(enqueue_pipeline, job, redis=redis, settings=settings, filestore=filestore)
            submitted_count += 1
        except Exception:
            mark_enqueue_failed(job, db)
            failed_count += 1

    logger.info(
        "upload batch finished: user_id=%s submitted=%d failed=%d skipped=%d batch_id=%s",
        user.id,
        submitted_count,
        failed_count,
        skipped_count,
        batch_id or "-",
    )

    # Every file was skipped as invalid (nothing queued and nothing failed at
    # enqueue): show the first skip reason on the upload page rather than a
    # "0 submitted" toast on /jobs.
    if submitted_count == 0 and failed_count == 0 and first_skip is not None:
        url, msg = first_skip
        return _error_redirect_or_fragment(request, url, msg)

    message, category = _build_upload_toast(submitted_count, failed=failed_count, skipped_files=skipped_count)
    set_flash(request, message, category)
    if htmx:
        return Response(status_code=204, headers={"HX-Redirect": "/jobs"})
    return RedirectResponse("/jobs", status_code=303)


@router.post("/upload/url")
async def upload_url_submit(
    request: Request,
    db: DbDep,
    filestore: FileStoreDep,
    redis: RedisDep,
    settings: SettingsDep,
    user: CurrentUserDep,
    url: Annotated[str, Form()],
    language: Annotated[str, Form()] = "zh",
    model_name: Annotated[str, Form()] = DEFAULT_WHISPER_MODEL,
    num_speakers: Annotated[int, Form()] = 0,
    enable_diarization: Annotated[bool, Form()] = False,
    convert_to_traditional: Annotated[bool, Form()] = False,
    llm_correction_enabled: Annotated[bool, Form()] = False,
):
    htmx = _is_htmx(request)

    option_error = validate_upload_options(language, model_name)
    if option_error:
        return _error_redirect_or_fragment(
            request,
            f"/upload?error={option_error.error_code}&value={quote(option_error.value)}",
            option_error.message,
        )

    # Parse textarea: split by newlines, strip whitespace, filter empty lines
    raw_lines = url.replace("\r\n", "\n").split("\n")
    lines = [(i + 1, line.strip()) for i, line in enumerate(raw_lines) if line.strip()]

    if not lines:
        msg = ui_labels.UPLOAD_URL_NO_INPUT
        return _error_redirect_or_fragment(request, "/upload?error=no_url", msg)

    if len(lines) > MAX_BATCH_SIZE:
        msg = ui_labels.UPLOAD_URL_EXCEEDS_LIMIT.format(limit=MAX_BATCH_SIZE, count=len(lines))
        return _error_redirect_or_fragment(request, f"/upload?error=too_many_urls&count={len(lines)}", msg)

    # Validate each URL, separating valid from invalid. Playlist links are
    # kept as ("playlist", url) tokens so expansion can splice their videos
    # back in at the same position, preserving the submitted order.
    valid_tokens: list[tuple[str, str]] = []
    invalid_line_nums: list[int] = []
    has_unsupported_playlist = False
    for line_num, raw_url in lines:
        try:
            if is_google_drive_url(raw_url):
                valid_tokens.append(("url", validate_google_drive_url(raw_url)))
            elif is_twitter_url(raw_url):
                valid_tokens.append(("url", validate_twitter_url(raw_url)))
            elif is_youtube_playlist_url(raw_url):
                valid_tokens.append(("playlist", validate_youtube_playlist_url(raw_url)))
            else:
                valid_tokens.append(("url", validate_youtube_url(raw_url)))
        except UnsupportedPlaylistTypeError:
            invalid_line_nums.append(line_num)
            has_unsupported_playlist = True
        except (YouTubeURLError, GoogleDriveURLError, TwitterURLError):
            invalid_line_nums.append(line_num)

    if not valid_tokens:
        if has_unsupported_playlist:
            msg = ui_labels.UPLOAD_URL_PLAYLIST_NOT_SUPPORTED
            return _error_redirect_or_fragment(request, "/upload?error=playlist", msg)
        msg = ui_labels.UPLOAD_URL_ALL_INVALID
        return _error_redirect_or_fragment(request, "/upload?error=all_invalid_urls", msg)

    # Expand playlists into their videos' watch URLs. Any playlist failure
    # rejects the whole submission: nothing has been persisted yet, so the
    # user can fix the input and resubmit without leftover jobs.
    valid_urls: list[str] = []
    expanded: dict[str, PlaylistInfo] = {}
    try:
        for kind, value in valid_tokens:
            if kind != "playlist":
                valid_urls.append(value)
                continue
            if value not in expanded:
                expanded[value] = await asyncio.to_thread(expand_playlist, value, limit=MAX_BATCH_SIZE)
            valid_urls.extend(expanded[value].video_urls)
    except PlaylistTooLargeError as e:
        if e.count:
            msg = ui_labels.UPLOAD_URL_PLAYLIST_TOO_LARGE.format(count=e.count, limit=e.limit)
            return _error_redirect_or_fragment(request, f"/upload?error=playlist_too_large&count={e.count}", msg)
        msg = ui_labels.UPLOAD_URL_PLAYLIST_TOO_LARGE_UNKNOWN.format(limit=e.limit)
        return _error_redirect_or_fragment(request, "/upload?error=playlist_too_large", msg)
    except PlaylistEmptyError:
        msg = ui_labels.UPLOAD_URL_PLAYLIST_EMPTY
        return _error_redirect_or_fragment(request, "/upload?error=playlist_empty", msg)
    except PlaylistUnavailableError:
        msg = ui_labels.UPLOAD_URL_PLAYLIST_UNAVAILABLE
        return _error_redirect_or_fragment(request, "/upload?error=playlist_unavailable", msg)
    except PlaylistFetchError:
        msg = ui_labels.UPLOAD_URL_PLAYLIST_FETCH_FAILED
        return _error_redirect_or_fragment(request, "/upload?error=playlist_fetch_failed", msg)

    # Counted once per unique playlist, mirroring how the playable videos of a
    # twice-pasted playlist become one job + a deduped count rather than two
    # jobs: the toast reports the deduplicated submission, so multiplying the
    # same skipped videos by paste count would overstate them.
    unavailable_count = sum(info.unavailable_count for info in expanded.values())

    if len(valid_urls) > MAX_BATCH_SIZE:
        msg = ui_labels.UPLOAD_URL_EXCEEDS_LIMIT.format(limit=MAX_BATCH_SIZE, count=len(valid_urls))
        return _error_redirect_or_fragment(request, f"/upload?error=too_many_urls&count={len(valid_urls)}", msg)

    # Deduplicate while preserving order
    unique_urls = list(dict.fromkeys(valid_urls))
    duplicates_removed = len(valid_urls) - len(unique_urls)

    # Batch ID only when multiple URLs
    batch_id = uuid.uuid4().hex if len(unique_urls) > 1 else None

    # The batch carries the playlist's title only for a pure single-playlist
    # submission; mixed or multi-playlist batches have no one obvious name.
    # Checked via token kinds, not token count: the same playlist pasted twice
    # collapses to one entry in `expanded` yet is still a pure single-playlist
    # submission and keeps its title.
    batch_title = None
    if batch_id and len(expanded) == 1 and all(kind == "playlist" for kind, _ in valid_tokens):
        batch_title = next(iter(expanded.values())).title or None

    submitted_count = 0
    failed_count = 0
    logger.info(
        "url upload batch starting: user_id=%s urls=%d playlists=%d unavailable=%d skipped=%d deduped=%d batch_id=%s",
        user.id,
        len(unique_urls),
        len(expanded),
        unavailable_count,
        len(invalid_line_nums),
        duplicates_removed,
        batch_id or "-",
    )
    for clean_url in unique_urls:
        job = Job(
            filename=clean_url,
            source_url=clean_url,
            language=language,
            model_name=model_name,
            num_speakers=clamp_num_speakers(num_speakers) or None,
            # Clamp opt-in flags to deployment availability (see file-upload branch).
            enable_diarization=enable_diarization and settings.diarization_available,
            convert_to_traditional=convert_to_traditional,
            llm_correction_enabled=llm_correction_enabled and settings.llm_correction_available,
            batch_id=batch_id,
            batch_title=batch_title,
            owner_id=user.id,
        )

        upload_dir = filestore.prepare_upload_dir(job.id)
        job.filepath = str(upload_dir)
        job.status = JobStatus.QUEUED
        db.insert_job(job)
        logger.info(
            "url upload job inserted: job_id=%s user_id=%s model=%s diarize=%s llm=%s",
            job.id,
            user.id,
            model_name,
            job.enable_diarization,
            job.llm_correction_enabled,
        )

        try:
            # See upload_submit: offload the sync enqueue so a large URL batch
            # does not block the event loop. insert_job stays on the loop.
            await asyncio.to_thread(enqueue_pipeline, job, redis=redis, settings=settings, filestore=filestore)
            submitted_count += 1
        except Exception:
            mark_enqueue_failed(job, db)
            failed_count += 1

    logger.info(
        "url upload batch finished: user_id=%s submitted=%d failed=%d batch_id=%s",
        user.id,
        submitted_count,
        failed_count,
        batch_id or "-",
    )

    # Even when every enqueue failed we fall through to the /jobs redirect so
    # the per-URL FAILED rows we just inserted are visible to the user.

    message, category = _build_upload_toast(
        submitted_count,
        failed=failed_count,
        # Unavailable playlist entries (private/deleted videos) count as
        # skipped: they were part of the submission but produced no job.
        skipped=len(invalid_line_nums) + unavailable_count,
        deduped=duplicates_removed,
    )
    set_flash(request, message, category)
    if htmx:
        return Response(status_code=204, headers={"HX-Redirect": "/jobs"})
    return RedirectResponse("/jobs", status_code=303)
