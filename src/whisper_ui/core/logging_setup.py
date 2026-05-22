"""Centralised logging configuration for the frontend and worker processes.

Reads ``LOG_LEVEL`` from the environment (one of ``DEBUG``, ``INFO``,
``WARNING``, ``ERROR``, ``CRITICAL``; default ``INFO``). ``LOG_JSON`` is
reserved for a future structured-output mode and currently has no effect.

The :class:`RequestContextFilter` pulls ``request_id`` and ``user_id`` from
context vars set by :mod:`whisper_ui.web.middleware.request_id`, so every
log line emitted during a request can be traced back to the same request
without operators having to correlate by timestamp + client IP. Worker
processes (no HTTP context) render the default ``-`` for both fields.

Idempotent: ``setup_logging()`` may be called more than once; later calls
replace any earlier dictConfig. Both ``create_app()`` and the worker
entrypoint call it once at process startup.
"""

from __future__ import annotations

import logging
import logging.config
import os
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextvars import Token


_DEFAULT_REQUEST_ID = "-"
_DEFAULT_USER_ID = "-"

_request_id_var: ContextVar[str] = ContextVar(
    "whisper_ui_request_id",
    default=_DEFAULT_REQUEST_ID,
)
_user_id_var: ContextVar[str] = ContextVar(
    "whisper_ui_user_id",
    default=_DEFAULT_USER_ID,
)


def set_request_context(*, request_id: str, user_id: str) -> tuple[Token[str], Token[str]]:
    """Set request_id / user_id on the current context.

    Returns the reset tokens so the caller can roll back the values in a
    ``finally`` block. Required because the same asyncio task may serve
    multiple requests (e.g., under ``TestClient``) and leaked vars would
    show up on the wrong request's logs.
    """
    return _request_id_var.set(request_id), _user_id_var.set(user_id)


def reset_request_context(tokens: tuple[Token[str], Token[str]]) -> None:
    """Reset both context vars using the tokens returned by ``set_request_context``."""
    rid_token, uid_token = tokens
    _request_id_var.reset(rid_token)
    _user_id_var.reset(uid_token)


def set_user_id(user_id: str) -> Token[str]:
    """Overlay just the user_id on the current context.

    Used by AuthMiddleware after the session resolves to a known user, so
    every downstream log line (including the eventual access log) renders
    that user instead of the ``'-'`` placeholder the request-id middleware
    initialised. Pairs with :func:`reset_user_id` in a ``finally`` block.
    """
    return _user_id_var.set(user_id)


def reset_user_id(token: Token[str]) -> None:
    """Reset the user_id context var using the token from :func:`set_user_id`."""
    _user_id_var.reset(token)


def current_request_id() -> str:
    """Return the request_id for the current context (or '-' if unset)."""
    return _request_id_var.get()


def current_user_id() -> str:
    """Return the user_id for the current context (or '-' if unset)."""
    return _user_id_var.get()


class RequestContextFilter(logging.Filter):
    """Inject request_id and user_id from context vars into every LogRecord.

    Default values ('-') keep the formatter output well-aligned for log
    lines emitted outside an HTTP request (worker stages, background loops,
    pre-middleware startup) — those lines simply show ``[req=- user=-]``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        record.user_id = _user_id_var.get()
        return True


_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _resolve_level(raw: str | None) -> str:
    if raw is None:
        return "INFO"
    candidate = raw.strip().upper()
    return candidate if candidate in _VALID_LEVELS else "INFO"


def setup_logging() -> None:
    """Apply the project-wide ``dictConfig``; safe to call multiple times.

    Pins ``rq`` / ``rq.worker`` to WARNING so the every-13-minute
    ``cleaning registries for queue ...`` heartbeat does not crowd out
    signal in production. Pins ``uvicorn.access`` to WARNING because the
    stock access log lacks user_id / request_id and is replaced by the
    structured access log emitted from :mod:`whisper_ui.web.middleware.request_id`.
    """
    level = _resolve_level(os.getenv("LOG_LEVEL"))

    config: dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_context": {
                "()": RequestContextFilter,
            },
        },
        "formatters": {
            "default": {
                "format": ("%(asctime)s %(levelname)s %(name)s [req=%(request_id)s user=%(user_id)s] %(message)s"),
                "datefmt": "%Y-%m-%dT%H:%M:%S%z",
            },
        },
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "default",
                "filters": ["request_context"],
            },
        },
        "loggers": {
            "rq": {"level": "WARNING"},
            "rq.worker": {"level": "WARNING"},
            "rq.scheduler": {"level": "WARNING"},
            "uvicorn.access": {"level": "WARNING"},
        },
        "root": {"level": level, "handlers": ["stderr"]},
    }
    logging.config.dictConfig(config)
