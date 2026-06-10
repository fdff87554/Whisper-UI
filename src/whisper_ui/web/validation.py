"""Request parameter validation helpers."""

from __future__ import annotations

import re

from fastapi import HTTPException

from whisper_ui.core.models import JobStatus

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
