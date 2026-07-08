from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from redis import Redis

from whisper_ui.core.config import Settings
from whisper_ui.core.constants import MAX_BATCH_SIZE, TIMESTAMP_DISPLAY_LENGTH
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.web.flash import consume_flash

_WEB_DIR = Path(__file__).parent


def _csp_nonce_context(request: Request) -> dict[str, str]:
    """Expose the per-request CSP nonce (set by SecurityHeadersMiddleware) to
    templates so inline <script> blocks can carry ``nonce="{{ csp_nonce }}"``.
    Falls back to "" if the middleware did not run (e.g. a direct render in a
    test), which simply yields a script that the CSP will reject in the browser.
    """
    return {"csp_nonce": getattr(request.state, "csp_nonce", "")}


templates = Jinja2Templates(directory=_WEB_DIR / "templates", context_processors=[_csp_nonce_context])


def _format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def make_content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Build a Content-Disposition header value safe for non-ASCII filenames.

    Emits both RFC 6266 forms: the plain ``filename="..."`` ASCII fallback
    (non-ASCII replaced with ``_``) for older tools and the client-side bulk
    export parser, plus ``filename*=UTF-8''...`` which modern browsers prefer
    and which preserves the original Unicode name.
    """
    ascii_fallback = "".join(c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in filename)
    encoded = quote(filename, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def _format_relative_time(iso_str: str) -> str:
    """Convert an ISO timestamp to a human-readable relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        diff = now - dt
        seconds = int(diff.total_seconds())

        if seconds < 60:
            return "剛剛"
        if seconds < 3600:
            minutes = seconds // 60
            return f"{minutes} 分鐘前"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} 小時前"
        days = seconds // 86400
        if days == 1:
            return "昨天"
        if days < 30:
            return f"{days} 天前"
        return iso_str[:10]
    except (ValueError, TypeError):
        return iso_str


templates.env.filters["format_time"] = _format_time
templates.env.filters["relative_time"] = _format_relative_time
templates.env.globals["TIMESTAMP_DISPLAY_LENGTH"] = TIMESTAMP_DISPLAY_LENGTH
templates.env.globals["MAX_BATCH_SIZE"] = MAX_BATCH_SIZE
# base.html consumes queued flash messages on full-page renders (see flash.py).
templates.env.globals["consume_flash"] = consume_flash


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> JobDatabase:
    return request.app.state.db


def get_filestore(request: Request) -> FileStore:
    return request.app.state.filestore


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[JobDatabase, Depends(get_db)]
FileStoreDep = Annotated[FileStore, Depends(get_filestore)]
RedisDep = Annotated[Redis, Depends(get_redis)]

# Re-exported from whisper_ui.web.auth so route modules can pull every
# request-scoped dependency from one place.
from whisper_ui.web.auth import CurrentUser, get_current_user, require_admin  # noqa: E402

CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminUserDep = Annotated[CurrentUser, Depends(require_admin)]
