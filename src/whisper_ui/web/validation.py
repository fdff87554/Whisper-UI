"""Request validation and shared route helpers."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, NamedTuple

from fastapi import HTTPException

from whisper_ui.core.languages import LANGUAGE_CHOICES, WHISPER_MODELS
from whisper_ui.core.models import JobStatus
from whisper_ui.ui import labels as ui_labels

if TYPE_CHECKING:
    from whisper_ui.core.models import Job
    from whisper_ui.storage.database import JobDatabase

logger = logging.getLogger(__name__)

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")

# Valid values for the jobs-list ``status`` query filter: the empty string
# ("all") plus every JobStatus value. Shared by the user and admin job routes
# so they reject an unknown status identically.
VALID_STATUS_FILTERS = frozenset({"", *JobStatus})

# Upper bound mirrors the diarization UI control (``max="20"``). The server
# clamps rather than trusting the form so a crafted request cannot push an
# absurd speaker count into pyannote.
MAX_NUM_SPEAKERS = 20


def validate_hex_id(value: str, name: str = "id") -> str:
    """Validate that a string is a 32-character lowercase hex ID."""
    if not _HEX32_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {name} format")
    return value


def normalize_status_filter(status: str) -> str:
    """Return ``status`` if it is a valid jobs-list filter, else ``""`` (all).

    Resetting an unknown value to "all" (rather than 400-ing) keeps the page
    rendering and stops a bogus filter from being baked into the polling
    wrapper's hx-get URL.
    """
    return status if status in VALID_STATUS_FILTERS else ""


def clamp_num_speakers(value: int) -> int:
    """Clamp a requested speaker count to ``[0, MAX_NUM_SPEAKERS]``.

    0 means "let pyannote decide". Clamping (rather than rejecting) keeps a
    legitimate submission flowing while neutralising out-of-range values
    from hand-crafted requests that bypass the HTML ``min``/``max``.
    """
    return max(0, min(value, MAX_NUM_SPEAKERS))


class UploadOptionError(NamedTuple):
    """A rejected upload option. ``error_code`` feeds the ``?error=`` query the
    upload page reads; ``value`` is the offending input; ``message`` is the
    user-facing label."""

    error_code: str  # "invalid_language" | "invalid_model"
    value: str
    message: str


def validate_upload_options(language: str, model_name: str) -> UploadOptionError | None:
    """Validate the language / model select inputs shared by every job-creating
    route (file upload, URL upload, re-transcribe). Returns the first rejection
    or None. Each caller renders its own response shape from the result so the
    check itself cannot drift across the three entry points.
    """
    if language not in LANGUAGE_CHOICES:
        return UploadOptionError("invalid_language", language, ui_labels.UPLOAD_INVALID_LANGUAGE.format(value=language))
    if model_name not in WHISPER_MODELS:
        return UploadOptionError("invalid_model", model_name, ui_labels.UPLOAD_INVALID_MODEL.format(value=model_name))
    return None


def mark_enqueue_failed(job: Job, db: JobDatabase) -> None:
    """Flip a job to FAILED with the shared enqueue-failure label.

    Shared by the three new-job entry points (and mirrored by the retry path)
    so the failure status, label, and persistence stay identical wherever an
    ``enqueue_pipeline`` call raises after the row already exists.
    """
    logger.exception("Failed to enqueue job %s", job.id)
    job.status = JobStatus.FAILED
    job.error = ui_labels.UPLOAD_ENQUEUE_FAILED
    db.update_job(job)
