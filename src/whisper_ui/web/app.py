from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from whisper_ui.web.deps import templates

logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    from redis import Redis
    from redis.exceptions import RedisError

    from whisper_ui.core.config import get_settings
    from whisper_ui.storage.database import JobDatabase
    from whisper_ui.storage.filestore import FileStore

    settings = get_settings()
    app.state.settings = settings
    app.state.db = JobDatabase(settings.database_path)
    app.state.filestore = FileStore(settings.upload_dir, settings.output_dir)
    app.state.redis = Redis.from_url(settings.redis_url)
    try:
        app.state.redis.ping()
    except RedisError:
        logger.warning("Redis is not reachable at %s — job submission will fail", settings.redis_url)
    logger.info("Whisper UI started")
    yield
    app.state.db.close()
    app.state.redis.close()
    logger.info("Whisper UI stopped")


def create_app() -> FastAPI:
    application = FastAPI(title="Whisper UI", lifespan=lifespan)

    application.mount("/static", StaticFiles(directory=_WEB_DIR / "static"), name="static")

    # Template globals
    from whisper_ui.core.languages import LANGUAGE_LABELS
    from whisper_ui.export.factory import available_formats
    from whisper_ui.ui import labels

    templates.env.globals["labels"] = labels
    templates.env.globals["LANGUAGE_LABELS"] = LANGUAGE_LABELS
    templates.env.globals["export_formats"] = available_formats()

    # Routes
    from whisper_ui.web.routes import jobs, upload, viewer

    application.include_router(upload.router)
    application.include_router(jobs.router)
    application.include_router(viewer.router)

    return application


app = create_app()
