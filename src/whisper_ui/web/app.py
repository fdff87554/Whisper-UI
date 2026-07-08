from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from whisper_ui.web.auth import AuthMiddleware
from whisper_ui.web.deps import templates
from whisper_ui.web.middleware.request_id import RequestIdMiddleware


def _build_csp(nonce: str) -> str:
    """Pragmatic Content-Security-Policy for the htmx + Alpine.js frontend.

    ``script-src`` allowlists jsDelivr (htmx / Alpine are loaded from there with
    SRI) and carries a per-request nonce for the handful of inline ``<script>``
    blocks. ``'unsafe-eval'`` is required because Alpine's standard build
    evaluates its ``x-*`` directive expressions via ``new Function`` — dropping
    it would need the @alpinejs/csp build and an inline-expression rewrite, a
    much larger change. Everything else is locked to ``'self'`` so this still
    adds real XSS defence-in-depth: no framing, no plugins, no base-tag or
    form-action hijack, no off-origin data exfiltration.
    """
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' 'unsafe-eval' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
        )
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth headers for the internal-network deployment.

    A per-request CSP nonce is stashed on ``request.state`` before the route
    runs so the templates' inline ``<script>`` blocks can reference it (see the
    ``csp_nonce`` template context processor). No HSTS because TLS is expected
    to be terminated by an upstream reverse proxy that owns the TLS headers.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", _build_csp(nonce))
        return response


logger = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent


_UPLOAD_RETENTION_CHECK_INTERVAL = 3600  # once per hour is enough for a daily-grained policy
# Cap how many uploads a single sweep tear down so the offloaded thread
# never sits on the executor for an absurd duration after, say, a long
# outage left thousands of expired COMPLETED jobs piled up. 200/hour =
# 4800/day, which already comfortably exceeds any plausible deployment.
_UPLOAD_RETENTION_BATCH_LIMIT = 200


def _redact_redis_url(url: str) -> str:
    """Return a redis URL safe to log, with any password removed.

    compose builds ``REDIS_URL`` as ``redis://:<password>@host:port/db`` when
    ``REDIS_PASSWORD`` is set; a password can also arrive as a ``password=``
    query param. Either way it must never reach the logs. When the URL carries
    a credential we rebuild it as ``scheme://***@host:port/path`` (dropping the
    userinfo and the query string, which may itself carry the password); a URL
    with no detectable credential is returned unchanged. A credentialed URL
    whose host cannot be cleanly parsed (e.g. a password containing ``#`` or a
    missing host) falls back to ``"<redacted>"`` so a malformed value never
    leaks the secret.
    """
    has_userinfo = "@" in url
    if not has_userinfo and "password=" not in url.lower():
        return url
    try:
        parts = urlsplit(url)
        if not parts.hostname:
            return "<redacted>"
        # SplitResult.port parses lazily and raises ValueError on a malformed
        # or out-of-range port, so the hostname/port assembly must stay inside
        # the try to keep this helper fail-safe (never raise, never leak).
        netloc = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    except ValueError:
        return "<redacted>"
    userinfo = "***@" if has_userinfo else ""
    return f"{parts.scheme}://{userinfo}{netloc}{parts.path}"


def _run_retention_sweep(db_path, filestore, threshold_iso: str, limit: int) -> int:
    """Sync helper run inside ``asyncio.to_thread`` for the retention loop.

    Opens its own short-lived :class:`JobDatabase` instead of reusing
    ``app.state.db``. The web tier's shared connection is used from the
    event-loop thread by request handlers; sharing it with this worker
    thread would put two threads on the same SQLite connection, which
    Python's sqlite3 binding does not serialise even with
    ``check_same_thread=False``. A per-sweep connection costs one SQLite
    open + WAL pragma per hour and isolates the retention path entirely.

    The id list is iterated lazily and ``limit`` only caps **successful**
    deletions, not the number of ids inspected. Without this contract a
    backlog larger than ``limit`` would stall: retention does not touch
    the DB row, so the next sweep returns the same id list, and slicing
    ``ids[:limit]`` would re-visit only the (already-reclaimed) first
    ``limit`` ids and never reach the rest. Counting only ``True``
    returns from ``delete_upload_files`` lets the loop skip past
    already-reclaimed dirs and find real work further down the list.
    """
    from contextlib import closing

    from whisper_ui.storage.database import JobDatabase

    with closing(JobDatabase(db_path)) as db:
        ids = db.list_terminal_job_ids_older_than(threshold_iso)
    removed = 0
    for jid in ids:
        if removed >= limit:
            break
        if filestore.delete_upload_files(jid):
            removed += 1
    return removed


def _run_stale_recovery(db_path, redis, timeout_seconds: int, error_message: str) -> int:
    """Sync helper run inside ``asyncio.to_thread`` for the stale-job checker.

    The liveness-aware reaper does a SQLite scan plus several Redis roundtrips
    per stale candidate (one ``RQJob.fetch`` per sub-job), so running it inline
    on the event loop would block the web tier during the sweep. Mirrors
    ``_run_retention_sweep``: it opens its own short-lived ``JobDatabase``
    instead of borrowing ``app.state.db`` (Python's sqlite3 does not serialise a
    shared connection across threads); the redis client is thread-safe and is
    passed straight through.
    """
    from contextlib import closing

    from whisper_ui.storage.database import JobDatabase
    from whisper_ui.worker.pipeline_dispatcher import recover_stale_pipeline_jobs

    with closing(JobDatabase(db_path)) as db:
        return recover_stale_pipeline_jobs(db, redis, timeout_seconds, error_message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from datetime import UTC, datetime, timedelta

    from redis.exceptions import RedisError

    from whisper_ui.core.config import get_settings
    from whisper_ui.core.constants import STALE_JOB_CHECK_INTERVAL
    from whisper_ui.core.redis_client import create_redis
    from whisper_ui.storage.database import JobDatabase
    from whisper_ui.storage.filestore import FileStore
    from whisper_ui.ui.labels import JOBS_STALE_ERROR

    settings = get_settings()
    app.state.settings = settings
    app.state.db = JobDatabase(settings.database_path)
    app.state.filestore = FileStore(settings.upload_dir, settings.output_dir)
    app.state.redis = create_redis(settings)
    try:
        app.state.redis.ping()
    except RedisError:
        logger.warning("Redis is not reachable at %s — job submission will fail", _redact_redis_url(settings.redis_url))

    async def _stale_job_checker():
        while True:
            await asyncio.sleep(STALE_JOB_CHECK_INTERVAL)
            try:
                recovered = await asyncio.to_thread(
                    _run_stale_recovery,
                    settings.database_path,
                    app.state.redis,
                    settings.stale_job_timeout,
                    JOBS_STALE_ERROR,
                )
                if recovered > 0:
                    logger.warning("Recovered %d stale job(s)", recovered)
            except Exception:
                logger.exception("Stale job check failed")

    async def _upload_retention_sweep():
        # Sleep once before the first sweep so a freshly restarted instance
        # is not immediately deleting files while operators are still
        # poking at the dashboard. The DB query and the shutil.rmtree per
        # reclaimed job are sync I/O — offload them to asyncio.to_thread,
        # and have the thread open its own JobDatabase so this loop never
        # shares a SQLite connection with the request handlers running
        # on the event-loop thread.
        while True:
            await asyncio.sleep(_UPLOAD_RETENTION_CHECK_INTERVAL)
            try:
                threshold = datetime.now(UTC) - timedelta(days=settings.upload_retention_days)
                removed = await asyncio.to_thread(
                    _run_retention_sweep,
                    settings.database_path,
                    app.state.filestore,
                    threshold.isoformat(),
                    _UPLOAD_RETENTION_BATCH_LIMIT,
                )
                if removed:
                    logger.info(
                        "Upload retention sweep reclaimed %d job upload dirs older than %d days",
                        removed,
                        settings.upload_retention_days,
                    )
            except Exception:
                logger.exception("Upload retention sweep failed")

    tasks = [asyncio.create_task(_stale_job_checker())]
    if settings.upload_retention_days > 0:
        tasks.append(asyncio.create_task(_upload_retention_sweep()))
        logger.info("Upload retention enabled: %d day(s)", settings.upload_retention_days)

    logger.info("Whisper UI started")
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    app.state.db.close()
    app.state.redis.close()
    logger.info("Whisper UI stopped")


def create_app() -> FastAPI:
    from whisper_ui.core.config import get_settings
    from whisper_ui.core.logging_setup import setup_logging

    # Apply the project-wide dictConfig before anything else logs. Calling
    # later would leave early startup lines (Settings validation, etc.)
    # going through Python's default WARNING-only root logger.
    setup_logging()

    settings = get_settings()
    application = FastAPI(title="Whisper UI", lifespan=lifespan)
    # bootstrap_done flips True after the first active admin is observed.
    # Initialised here (not in lifespan) so AuthMiddleware can read it
    # before the first request completes even on a fresh process.
    application.state.bootstrap_done = False

    # Session secret: in production this must be set via SESSION_SECRET in
    # the environment. An empty value generates an ephemeral random secret
    # so the app still boots in dev, at the cost of invalidating every
    # session whenever the process restarts.
    session_secret = settings.session_secret
    if not session_secret:
        session_secret = secrets.token_hex(32)
        logger.warning(
            "SESSION_SECRET is unset; using an ephemeral random secret. "
            "All existing sessions will be invalidated on each restart. "
            "Set SESSION_SECRET in production (e.g. `openssl rand -hex 32`)."
        )

    # Middleware order: add_middleware is LIFO, so the order below results in
    # RequestIdMiddleware (outermost) → SessionMiddleware → AuthMiddleware →
    # SecurityHeadersMiddleware → route handler on the inbound path.
    # RequestIdMiddleware must run first so the request_id context var is
    # already populated when SessionMiddleware / AuthMiddleware emit their
    # own log lines (CSRF rejection, session invalidation, etc.).
    # AuthMiddleware needs the session cookie decoded before it runs, so
    # SessionMiddleware must be outside it.
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(AuthMiddleware)
    application.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    application.add_middleware(RequestIdMiddleware)

    application.mount("/static", StaticFiles(directory=_WEB_DIR / "static"), name="static")

    # Template globals
    from whisper_ui.core.languages import LANGUAGE_LABELS
    from whisper_ui.export.factory import available_formats
    from whisper_ui.ui import labels

    templates.env.globals["labels"] = labels
    templates.env.globals["LANGUAGE_LABELS"] = LANGUAGE_LABELS
    templates.env.globals["export_formats"] = available_formats()

    # Routes
    from whisper_ui.web.routes import admin, auth_routes, dashboard, jobs, metrics, upload, viewer

    application.include_router(auth_routes.router)
    application.include_router(dashboard.router)
    application.include_router(upload.router)
    application.include_router(jobs.router)
    application.include_router(viewer.router)
    application.include_router(admin.router)
    application.include_router(metrics.router)

    @application.get("/health")
    async def health():
        return {"status": "ok"}

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Anything that escapes a route handler reaches here; log the full
        # traceback for operators but never let the exception text reach the
        # client — it can contain file paths or partially-rendered SQL.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    return application


app = create_app()
