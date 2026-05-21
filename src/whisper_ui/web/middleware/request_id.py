"""Request-ID middleware that pins a correlation id onto every HTTP request.

Reads the inbound ``X-Request-ID`` header when present (must be 8-64 hex
characters; anything else is treated as missing) or generates a fresh
8-character hex id. Exposes the id via the contextvars in
:mod:`whisper_ui.core.logging_setup`, so every log line emitted during
the request — including those from downstream middleware and the route
handler — automatically renders ``[req=<id> user=...]``. Writes the same
id back on the response as ``X-Request-ID`` so a browser devtools panel
or upstream nginx access log can join on the same key.

Registered as the **outermost** middleware in :func:`whisper_ui.web.app.create_app`
so the context var is set before SessionMiddleware / AuthMiddleware run.
The user_id var is initialised to the default ``'-'`` here; AuthMiddleware
overlays the resolved user later in the chain.
"""

from __future__ import annotations

import re
import secrets
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from whisper_ui.core.logging_setup import reset_request_context, set_request_context

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp


REQUEST_ID_HEADER = "X-Request-ID"
# Accept hex strings of 8-64 chars so callers using longer trace ids
# (Cloudflare Ray IDs, AWS X-Ray subsegment ids, etc.) still propagate
# faithfully. Reject anything else (path traversal, control chars,
# absurdly long blobs) and generate a fresh id instead.
_REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{8,64}$", re.IGNORECASE)


def _generate_request_id() -> str:
    """Return a short, URL-safe hex id (8 chars = 32 bits of entropy)."""
    return secrets.token_hex(4)


def _normalise_request_id(raw: str | None) -> str:
    if raw is None:
        return _generate_request_id()
    candidate = raw.strip()
    if _REQUEST_ID_PATTERN.match(candidate):
        return candidate.lower()
    return _generate_request_id()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a per-request correlation id to the logging contextvars."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _normalise_request_id(request.headers.get(REQUEST_ID_HEADER))
        tokens = set_request_context(request_id=request_id, user_id="-")
        try:
            response = await call_next(request)
        finally:
            # Reset before adding the header so concurrent requests cannot
            # observe each other's id even if the response object is later
            # processed on a different task.
            reset_request_context(tokens)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
