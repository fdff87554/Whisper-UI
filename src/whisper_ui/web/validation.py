"""Request parameter validation helpers."""

from __future__ import annotations

import re

from fastapi import HTTPException

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")

# Upper bound mirrors the diarization UI control (``max="20"``). The server
# clamps rather than trusting the form so a crafted request cannot push an
# absurd speaker count into pyannote.
MAX_NUM_SPEAKERS = 20


def validate_hex_id(value: str, name: str = "id") -> str:
    """Validate that a string is a 32-character lowercase hex ID."""
    if not _HEX32_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {name} format")
    return value


def clamp_num_speakers(value: int) -> int:
    """Clamp a requested speaker count to ``[0, MAX_NUM_SPEAKERS]``.

    0 means "let pyannote decide". Clamping (rather than rejecting) keeps a
    legitimate submission flowing while neutralising out-of-range values
    from hand-crafted requests that bypass the HTML ``min``/``max``.
    """
    return max(0, min(value, MAX_NUM_SPEAKERS))
