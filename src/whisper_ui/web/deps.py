from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


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
