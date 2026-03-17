from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from redis import Redis

from whisper_ui.core.config import Settings
from whisper_ui.core.constants import JOB_ID_DISPLAY_LENGTH, TIMESTAMP_DISPLAY_LENGTH
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore

_WEB_DIR = Path(__file__).parent

templates = Jinja2Templates(directory=_WEB_DIR / "templates")


def _format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def make_content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Build a Content-Disposition header value safe for non-ASCII filenames.

    Uses RFC 6266 ``filename*=UTF-8''...`` so the header value stays ASCII-safe
    while preserving the original Unicode filename for modern browsers.
    """
    encoded = quote(filename, safe="")
    return f"{disposition}; filename*=UTF-8''{encoded}"


templates.env.filters["format_time"] = _format_time
templates.env.globals["JOB_ID_DISPLAY_LENGTH"] = JOB_ID_DISPLAY_LENGTH
templates.env.globals["TIMESTAMP_DISPLAY_LENGTH"] = TIMESTAMP_DISPLAY_LENGTH


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
