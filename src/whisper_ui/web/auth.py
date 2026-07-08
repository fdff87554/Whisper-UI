"""Session-cookie authentication, authorization, CSRF and bootstrap latch.

This module wires together five pieces of behaviour that are inseparable
in practice:

* **Session resolution** — read ``request.session["uid"]`` / ``["sv"]`` set
  by Starlette's :class:`SessionMiddleware`, look the user up in the DB,
  drop the row onto ``request.state.user`` for downstream dependencies.
* **Session-version invalidation** — admin password resets, deactivation,
  and self-changes bump ``users.session_version``; sessions whose ``sv``
  differs from the DB are silently cleared and treated as anonymous.
* **First-run bootstrap latch** — until at least one active admin exists,
  every non-public path redirects to ``/register?bootstrap=1``. After the
  first admin is created, an app-level ``bootstrap_done`` flag avoids
  hitting the DB on every subsequent request.
* **CSRF defense** — for ``POST/PUT/PATCH/DELETE`` we require ``Origin``
  (or ``Referer`` as fallback) to match the request's Host header. This
  is paired with ``SameSite=Lax`` session cookies; htmx requests in the
  same browser context send a same-origin ``Origin`` header automatically.
* **Public path whitelist** — ``/login``, ``/register``, ``/logout``,
  ``/health``, ``/metrics``, ``/favicon.ico`` and anything under ``/static/``
  skip the auth gate so the login form, health check, Prometheus scrape and
  assets are reachable while signed out.

Dependencies (:data:`CurrentUserDep`, :data:`AdminUserDep`) read what the
middleware put on ``request.state``; they do not re-query the DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote, urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from whisper_ui.core.logging_setup import reset_user_id, set_user_id
from whisper_ui.storage import users_repo

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextvars import Token


logger = logging.getLogger(__name__)


# Paths that never require authentication. Exact match (not prefix), so
# ``/login/extra`` would still be gated — there's no nested route below
# any of these so exact match is precise enough and avoids accidental
# bypass via path traversal.
PUBLIC_PATHS = frozenset({"/login", "/register", "/logout", "/health", "/metrics", "/favicon.ico"})

# Path prefixes that never require authentication. ``/static/`` covers
# CSS, vendored JS, and any future static assets.
PUBLIC_PREFIXES = ("/static/", "/shared/")

# HTTP methods that require CSRF protection. GET / HEAD / OPTIONS are
# considered safe; the route handlers must not perform mutating actions
# on those verbs (and they don't — verified by inspecting routes/*.py).
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class CurrentUser:
    """The minimum slice of :class:`users_repo.User` needed by route handlers.

    Kept frozen so downstream code cannot mutate it and assume that change
    has been persisted. Routes that need the full row should re-query.
    """

    id: int
    username: str
    is_admin: bool


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _check_csrf(request: Request) -> bool:
    """Validate that a mutating request originated from the same site.

    Compares ``Origin`` (preferred) or ``Referer`` (fallback) against the
    request's own host. The host source is ``request.url.netloc`` rather
    than the raw ``Host`` header: Starlette derives ``url.netloc`` from
    the ASGI scope (``server`` + ``Host``), which is more robust to
    HTTP/2 ``:authority`` translation and to proxy configurations that
    arrive without a literal ``Host`` header.

    When ``settings.trust_proxy_headers`` is True, ``X-Forwarded-Host``
    is also accepted as the expected host — required when a reverse
    proxy terminates TLS and rewrites the Host header to the upstream
    address (the client's browser sees ``example.com`` but the app sees
    ``Host: app:8000``).

    Returns False on any of: missing both Origin and Referer, malformed
    URL, or hostname/port mismatch against every accepted host value.
    """
    candidate = request.headers.get("origin") or request.headers.get("referer")
    if not candidate:
        return False

    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if not parsed.netloc:
        return False

    accepted_hosts: list[str] = []
    netloc = request.url.netloc
    if netloc:
        accepted_hosts.append(netloc.lower())

    settings = getattr(request.app.state, "settings", None)
    if settings is not None and getattr(settings, "trust_proxy_headers", False):
        xfh = request.headers.get("x-forwarded-host")
        if xfh:
            accepted_hosts.append(xfh.lower())

    if not accepted_hosts:
        return False
    return parsed.netloc.lower() in accepted_hosts


def _unauthenticated_response(request: Request) -> Response:
    """Send an unauthenticated client to /login.

    htmx requests get ``401 + HX-Redirect`` so the browser follows the
    redirect via the htmx hook; everything else gets a plain 302 so a
    bookmarked URL behaves the obvious way.
    """
    next_url = quote(request.url.path)
    if request.headers.get("hx-request") == "true":
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, headers={"HX-Redirect": f"/login?next={next_url}"})
    return RedirectResponse(f"/login?next={next_url}", status_code=status.HTTP_302_FOUND)


class AuthMiddleware(BaseHTTPMiddleware):
    """Mount in front of route handlers, after :class:`SessionMiddleware`.

    Responsibilities are documented at the module level. Ordering rule:
    must run **after** SessionMiddleware on the inbound path so
    ``request.session`` is available, and **before** the request reaches
    the routers so they always see a populated or absent
    ``request.state.user``.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        # Static assets and health probes bypass all auth machinery so they
        # work without a session cookie. /health in particular should answer
        # even before the bootstrap latch has been resolved, so external
        # monitors don't see the app as down during initial setup.
        if path == "/health" or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        db = request.app.state.db

        # Bootstrap latch: until the first admin exists, hijack every
        # non-public path and force it to /register?bootstrap=1. The
        # one-shot latch on app.state avoids hitting the DB on every
        # request once an admin has been created.
        if not request.app.state.bootstrap_done:
            active_admins = users_repo.count_active_admins(db.conn)
            if active_admins == 0:
                if not _is_public(path):
                    logger.debug("Bootstrap pending: redirecting %s to /register?bootstrap=1", path)
                    return RedirectResponse("/register?bootstrap=1", status_code=status.HTTP_302_FOUND)
            else:
                logger.info(
                    "Bootstrap latch flipped: %d active admin(s) observed on first protected request",
                    active_admins,
                )
                request.app.state.bootstrap_done = True

        # CSRF check on mutating verbs runs before authentication so a
        # missing-Origin POST is rejected even if an attacker somehow has
        # a valid session cookie cached.
        if request.method in MUTATING_METHODS and not _check_csrf(request):
            logger.warning(
                "CSRF check failed for %s %s (origin=%r referer=%r host=%r)",
                request.method,
                path,
                request.headers.get("origin"),
                request.headers.get("referer"),
                request.headers.get("host"),
            )
            return Response("CSRF check failed", status_code=status.HTTP_403_FORBIDDEN)

        # Resolve the session user. session.get returns None for missing
        # keys, which is the anonymous case.
        session_uid = request.session.get("uid")
        session_sv = request.session.get("sv")
        user: CurrentUser | None = None
        if session_uid is not None and session_sv is not None:
            user_row = users_repo.get_user_by_id(db.conn, session_uid)
            if user_row is None:
                # Cookie references a row that no longer exists (admin
                # hard-delete or DB rebuild). Log at INFO so a security
                # reviewer can distinguish this from the more common
                # "session_version bump" path.
                logger.info(
                    "Session invalidated: cookie uid=%s no longer exists in users table",
                    session_uid,
                )
                request.session.clear()
            elif not user_row.is_active:
                logger.info(
                    "Session invalidated: user %r (uid=%s) is deactivated",
                    user_row.username,
                    session_uid,
                )
                request.session.clear()
            elif user_row.session_version != session_sv:
                logger.info(
                    "Session invalidated: user %r (uid=%s) session_version mismatch (cookie=%s db=%s)",
                    user_row.username,
                    session_uid,
                    session_sv,
                    user_row.session_version,
                )
                request.session.clear()
            else:
                user = CurrentUser(id=user_row.id, username=user_row.username, is_admin=user_row.is_admin)

        user_token: Token[str] | None = None
        if user is not None:
            # Publish the resolved username on the contextvar so the
            # structured access log and every downstream logger.info /
            # logger.warning gets [user=<name>] without each call site
            # having to thread the user through manually.
            user_token = set_user_id(user.username)
        try:
            # Public paths are always allowed, but if the user does happen
            # to be logged in we still expose `request.state.user` so the
            # templates can render a "signed in as X" header.
            if _is_public(path):
                if user is not None:
                    request.state.user = user
                return await call_next(request)

            if user is None:
                logger.debug("Redirecting unauthenticated request to /login: path=%s", path)
                return _unauthenticated_response(request)

            request.state.user = user
            return await call_next(request)
        finally:
            if user_token is not None:
                reset_user_id(user_token)


def owner_filter(user: CurrentUser) -> int | None:
    """Return the ``owner_id`` value to pass to :mod:`JobDatabase` queries.

    Non-admin users see only their own jobs (filter on ``owner_id = user.id``);
    admins see everything (``None`` disables the filter at the SQL layer).
    Returning ``None`` rather than ``user.id`` for admins is what allows
    admin-only views like ``/admin/jobs`` to surface legacy NULL-owner rows
    from pre-auth deployments.
    """
    return None if user.is_admin else user.id


def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency: return the request's authenticated user.

    The middleware has already gated unauthenticated traffic on protected
    routes, so a missing ``request.state.user`` here means the route is
    in the public whitelist but the dependency was applied anyway —
    treat it as 401 rather than crashing.
    """
    user: CurrentUser | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    """FastAPI dependency: 403 unless the request's user has the admin flag."""
    if not user.is_admin:
        logger.warning(
            "require_admin rejected: user %r (uid=%s) is not an admin",
            user.username,
            user.id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
