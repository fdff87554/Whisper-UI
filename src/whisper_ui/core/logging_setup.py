"""Centralised logging configuration for the frontend and worker processes.

Reads ``LOG_LEVEL`` from the environment (one of ``DEBUG``, ``INFO``,
``WARNING``, ``ERROR``, ``CRITICAL``; default ``INFO``). ``LOG_JSON``
(truthy: ``1``/``true``/``yes``/``on``) switches the stderr handler to a
single-line JSON formatter — ts/level/logger/request_id/user_id/message plus
any structured ``extra={}`` fields (e.g. worker stage logs carry
stage/job_id/elapsed_ms) — for log aggregation (Loki/jq). Default stays the
human-readable text format.

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

import json
import logging
import logging.config
import os
import re
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


def mask_username(username: str) -> str:
    """Mask a username for failed-auth logging so logs don't retain raw PII.

    Keeps the first and last character for correlation (``alice`` -> ``a***e``);
    two characters or fewer are fully masked. Used on the attacker-controllable
    login / registration failure paths — on a failed login a user sometimes
    types a password into the username field, so the raw value can be
    credential-adjacent. Successfully authenticated users are still recorded in
    full via the access-log ``user_id`` context var.
    """
    if not username:
        return ""
    if len(username) <= 2:
        return "**"
    return f"{username[0]}***{username[-1]}"


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


_LOG_JSON_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _resolve_json(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in _LOG_JSON_TRUTHY


# Standard LogRecord attributes plus the request-context fields rendered
# explicitly. Anything NOT in here that a call site passed via ``extra={}``
# is copied into the JSON object as a structured field.
_RESERVED_LOGRECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
        "request_id", "user_id",
    }
)  # fmt: skip

# Structured ``extra={}`` field names whose value is redacted in the JSON log,
# so a call site that accidentally passes a token / password / raw URL (which
# may carry inline credentials) cannot leak it. Matched as a whole
# underscore-delimited word (or the whole key) rather than a bare substring, so
# ``redis_url`` / ``source_url`` / ``hf_token`` / ``api_key`` are redacted while
# ``curl_command`` / ``tokenizer_name`` (which merely contain "url" / "token")
# are not.
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:password|passwd|secret|token|authorization|api_?key|url)(?:_|$)",
    re.IGNORECASE,
)
_REDACTED = "***"


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(key))


class JsonFormatter(logging.Formatter):
    """Render each LogRecord as one JSON line (selected when ``LOG_JSON`` is set).

    Emits ts/level/logger/request_id/user_id/message, the rendered exception
    under ``exc`` when present, and any structured ``extra={}`` fields passed at
    the call site — so worker stage logs (stage/job_id/elapsed_ms) are queryable
    by jq/Loki without per-call-site formatting. ``default=str`` keeps a
    non-serialisable extra from breaking the line; ``ensure_ascii=False`` keeps
    non-ASCII (e.g. Chinese) readable.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", _DEFAULT_REQUEST_ID),
            "user_id": getattr(record, "user_id", _DEFAULT_USER_ID),
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOGRECORD_ATTRS and not key.startswith("_"):
                payload[key] = _REDACTED if _is_sensitive_key(key) else value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(*, log_level: str | None = None, log_json: bool | None = None) -> None:
    """Apply the project-wide ``dictConfig``; safe to call multiple times.

    ``log_level`` / ``log_json`` come from :class:`~whisper_ui.core.config.Settings`
    (so a value in ``.env`` is honoured — pydantic-settings loads ``.env`` into
    Settings, not ``os.environ``, so the old ``os.getenv`` read silently ignored
    ``.env``). When a parameter is None the ``LOG_LEVEL`` / ``LOG_JSON`` process
    environment variable is used as a fallback, which keeps the earliest startup
    call (before Settings is loaded) working.

    Pins ``rq`` / ``rq.worker`` to WARNING so the every-13-minute
    ``cleaning registries for queue ...`` heartbeat does not crowd out
    signal in production. Pins ``uvicorn.access`` to WARNING because the
    stock access log lacks user_id / request_id and is replaced by the
    structured access log emitted from :mod:`whisper_ui.web.middleware.request_id`.
    """
    level = _resolve_level(log_level if log_level is not None else os.getenv("LOG_LEVEL"))
    use_json = log_json if log_json is not None else _resolve_json(os.getenv("LOG_JSON"))
    formatter = "json" if use_json else "default"

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
            "json": {
                "()": JsonFormatter,
                "datefmt": "%Y-%m-%dT%H:%M:%S%z",
            },
        },
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": formatter,
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
