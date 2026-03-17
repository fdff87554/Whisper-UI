"""Request parameter validation helpers."""

from __future__ import annotations

import re

from fastapi import HTTPException

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


def validate_hex_id(value: str, name: str = "id") -> str:
    """Validate that a string is a 32-character lowercase hex ID."""
    if not _HEX32_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {name} format")
    return value
