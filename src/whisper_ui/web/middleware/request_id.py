"""Request-ID middleware that owns per-request observability.

Two responsibilities, kept in one middleware because they share the same
request lifecycle and timing window:

1. **Correlation id**: read the inbound ``X-Request-ID`` header (8-64 hex
   chars; anything else is treated as missing) or generate a fresh
   8-character hex id; publish it on the contextvars from
   :mod:`whisper_ui.core.logging_setup` so every log line during the
   request renders ``[req=<id> user=...]``; echo it back as
   ``X-Request-ID`` for upstream nginx / browser devtools to join on.
2. **Structured access log**: on response (success **or** exception)
   emit one INFO line on the ``whisper_ui.web.access`` logger with
   ``method``, ``path``, ``status``, ``duration_ms``, and ``ip``. This
   replaces uvicorn's built-in access log (silenced via dictConfig and
   the ``--no-access-log`` flag on the container CMD) so every access
   record automatically inherits the request_id / user_id contextvars.

Registered as the **outermost** middleware in
:func:`whisper_ui.web.app.create_app` so the context var is set before
SessionMiddleware / AuthMiddleware run and so the duration timer covers
their work. The user_id var is initialised to the default ``'-'`` here;
AuthMiddleware overlays the resolved user later in the chain.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from whisper_ui.core.logging_setup import reset_request_context, set_request_context, set_user_id

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
# Status code reported when call_next raises before producing a response.
# uvicorn / starlette will translate the exception into a real 500 to the
# client, but our middleware never sees the constructed response, so the
# access log records the sentinel instead of guessing.
_UNCAUGHT_STATUS_SENTINEL = 500

_access_logger = logging.getLogger("whisper_ui.web.access")


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


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "-"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a per-request correlation id and emit a structured access log."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _normalise_request_id(request.headers.get(REQUEST_ID_HEADER))
        tokens = set_request_context(request_id=request_id, user_id="-")
        start_ns = time.perf_counter_ns()
        status_code: int = _UNCAUGHT_STATUS_SENTINEL
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000
            # AuthMiddleware sits inside this middleware and runs its own
            # finally before ours, resetting the user_id contextvar to '-'.
            # Starlette's BaseHTTPMiddleware also runs the inner middleware
            # in a sub-task whose contextvar mutations do not propagate back
            # to this outer task. Re-read the resolved user from
            # ``request.state`` (which Starlette shares across the middleware
            # call chain via the Request object, not via contextvars) and
            # re-set the var so the access log line's ``[user=...]`` tag
            # matches the authenticated identity. The token is discarded —
            # ``reset_request_context`` below restores both vars to their
            # pre-set_request_context state regardless of intermediate sets.
            user = getattr(request.state, "user", None)
            if user is not None:
                set_user_id(user.username)
            _access_logger.info(
                "method=%s path=%s status=%s duration_ms=%s ip=%s",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                _client_ip(request),
            )
            # Reset after logging so the access record still renders with
            # this request's id (the filter reads the contextvar at format
            # time, not at LogRecord creation).
            reset_request_context(tokens)
