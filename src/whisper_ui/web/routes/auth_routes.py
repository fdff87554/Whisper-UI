"""Login, register, and logout endpoints.

These routes are intentionally on the auth middleware's public whitelist
(see :data:`whisper_ui.web.auth.PUBLIC_PATHS`) so an unauthenticated visitor
can reach them. They are still subject to the CSRF check on POST.

Registration has two distinct modes that share the same template and POST
handler:

* **Bootstrap mode** — the system has zero active admins. Any visitor who
  reaches ``/register`` is forced into this mode, and the first account
  created is unconditionally an admin. Triggered automatically by the
  middleware redirecting to ``/register?bootstrap=1``.
* **Open registration** — an admin already exists. New accounts default to
  non-admin / active. This matches the user's "self-service registration"
  requirement.

The bootstrap branch is server-determined (counted from the DB), not from
the ``?bootstrap=1`` query param — that param only controls UI wording.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from whisper_ui.core.logging_setup import mask_username
from whisper_ui.storage import users_repo
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web import rate_limit
from whisper_ui.web.deps import DbDep, RedisDep, SettingsDep, templates

logger = logging.getLogger(__name__)
router = APIRouter()

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
MIN_PASSWORD_LENGTH = 8


def _safe_next(next_url: str | None) -> str:
    """Return ``next_url`` only if it is a same-origin relative path.

    Rejects absolute URLs, protocol-relative (``//evil.example``), the
    backslash variant (``/\\evil.example``), and anything not starting with
    ``/``. This prevents open redirect attacks via the ``?next=`` query param.
    """
    if not next_url:
        return "/"
    # Browsers normalise "\" to "/" in the authority component, so a value like
    # "/\evil.example" resolves to "//evil.example" -> an off-site redirect.
    # Decide on the normalised form so that variant is rejected like "//host".
    normalized = next_url.replace("\\", "/")
    if not normalized.startswith("/") or normalized.startswith("//"):
        return "/"
    return next_url


def _redirect_after_auth(request: Request, location: str) -> Response:
    """Return a 302 for normal navigation, or 204 + HX-Redirect for htmx.

    htmx will swap the response body unless told otherwise; using an
    HX-Redirect header tells the htmx runtime to do a full-page navigation
    instead, which is what we want after login/register/logout.
    """
    if request.headers.get("hx-request") == "true":
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Redirect": location})
    return RedirectResponse(location, status_code=status.HTTP_302_FOUND)


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    settings: SettingsDep,
    next_url: Annotated[str, Query(alias="next")] = "/",
    error: str = "",
):
    """Render the login form, or bounce already-logged-in users away."""
    # If the user is already authenticated (middleware sets request.state.user
    # on public paths when a valid cookie is present), skip the form.
    current = getattr(request.state, "user", None)
    if current is not None:
        return RedirectResponse(_safe_next(next_url), status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "active_page": "",
            "next": _safe_next(next_url),
            "error_message": _login_error_message(error, settings.login_lockout_seconds),
            "allow_registration": settings.allow_registration,
        },
    )


def _login_error_message(error: str, lockout_seconds: int = 0) -> str | None:
    if error == "invalid":
        return ui_labels.AUTH_LOGIN_FAILED
    if error == "inactive":
        return ui_labels.AUTH_ACCOUNT_INACTIVE
    if error == "rate_limited":
        # Ceil, not floor: understating the wait makes users retry early and
        # get rejected again.
        minutes = max(1, math.ceil(lockout_seconds / 60))
        return ui_labels.AUTH_RATE_LIMITED.format(minutes=minutes)
    return None


def client_ip_from_request(request: Request, *, trust_proxy_headers: bool, trusted_proxy_count: int = 1) -> str:
    """Best-effort client IP for rate-limit bucketing.

    When ``trust_proxy_headers`` is True (operator opt-in), the client IP is the
    ``trusted_proxy_count``-th entry from the **RIGHT** of ``X-Forwarded-For``.
    The rightmost entries are the ones our own trusted proxies appended (each
    records the peer it received the connection from), so the Nth-from-right is
    the real client; everything further left is attacker-controlled. Reading the
    left-most entry — the old behaviour — let a client spoof ``X-Forwarded-For``
    to a fresh value per request and evade the per-IP rate limit entirely
    (append-mode proxies such as nginx ``$proxy_add_x_forwarded_for`` keep the
    forged prefix). If the header has fewer entries than the configured trusted
    hop count, the header is not trusted and we fall back to the direct peer.

    Falls back to ``request.client.host`` when not trusting proxies, and to the
    literal ``"unknown"`` when even that is unavailable (test clients, unusual
    ASGI servers) so the rate-limit code can still bucket under a stable key.
    """
    if trust_proxy_headers:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= trusted_proxy_count:
            return parts[-trusted_proxy_count]
    return request.client.host if request.client else "unknown"


@router.post("/login")
async def login_submit(
    request: Request,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_url: Annotated[str, Form(alias="next")] = "/",
):
    """Authenticate a user and start a session.

    Always performs an argon2 verify (dummy when the user is unknown) so the
    response time does not leak username existence. On wrong-password and
    unknown-user the response is the same generic message — never "user
    not found" vs "wrong password".

    Account-state leak protection: ``is_active`` is only checked **after**
    a successful password verification. An attacker probing arbitrary
    passwords gets a generic ``invalid`` regardless of whether the account
    exists, is active, or is deactivated. Only a legitimate user who knows
    their own password ever sees the ``inactive`` message.

    Rate limit: when the per-user OR per-IP counter has reached the
    threshold we short-circuit without doing any DB / argon2 work, so
    locked accounts cannot be used as a CPU-burn vector.
    """
    ip = client_ip_from_request(
        request,
        trust_proxy_headers=settings.trust_proxy_headers,
        trusted_proxy_count=settings.trusted_proxy_count,
    )
    if rate_limit.is_locked(
        redis,
        username=username,
        ip=ip,
        max_user_attempts=settings.max_login_attempts,
        max_ip_attempts=settings.max_login_attempts_per_ip,
    ):
        logger.warning("login rate-limited: username=%s ip=%s", mask_username(username), ip)
        return _login_error_redirect(request, next_url, "rate_limited")

    user_row = users_repo.get_user_by_username(db.conn, username)

    if user_row is None:
        # Unknown user: do a dummy argon2 verify so the unknown-user
        # branch takes a comparable wall-clock time to the wrong-password
        # branch below.
        users_repo.dummy_verify(password)
        logger.debug("login failed: unknown username %s", mask_username(username))
        _record_failure(redis, settings, username=username, ip=ip)
        return _login_error_redirect(request, next_url, "invalid")

    # Verify the password BEFORE checking is_active. Reversing this order
    # would let an attacker submit any password to a known username and
    # observe whether the account is deactivated, which is an account-
    # enumeration leak. Verifying first means the inactive branch is only
    # reachable by someone who can already authenticate as the user.
    if not users_repo.verify_password(user_row, password):
        logger.info("login failed: wrong password for %s", mask_username(user_row.username))
        _record_failure(redis, settings, username=user_row.username, ip=ip)
        return _login_error_redirect(request, next_url, "invalid")

    if not user_row.is_active:
        # Legitimate-but-deactivated user. Deliberately NOT recording a
        # rate-limit failure here: a user with the correct password is
        # not abusing the form, and counting them as a failure would let
        # them accidentally lock their own IP.
        logger.info("login blocked: inactive account %r", user_row.username)
        return _login_error_redirect(request, next_url, "inactive")

    request.session["uid"] = user_row.id
    request.session["sv"] = user_row.session_version
    rate_limit.reset_user(redis, user_row.username)
    logger.info("login succeeded for %r (is_admin=%s)", user_row.username, user_row.is_admin)
    return _redirect_after_auth(request, _safe_next(next_url))


def _record_failure(redis, settings, *, username: str, ip: str) -> None:
    """Bump the rate-limit counter for a failed login.

    Wraps :func:`rate_limit.check_and_increment` so the route stays
    readable and the threshold parameters live in one place. The return
    value is intentionally ignored — whether this attempt was the one
    that crossed the threshold is irrelevant; the next request will see
    it as locked and short-circuit at the top of the handler.
    """
    rate_limit.check_and_increment(
        redis,
        username=username,
        ip=ip,
        max_user_attempts=settings.max_login_attempts,
        max_ip_attempts=settings.max_login_attempts_per_ip,
        window_seconds=settings.login_lockout_seconds,
    )


def _login_error_redirect(request: Request, next_url: str, error: str) -> Response:
    safe_next = _safe_next(next_url)
    target = f"/login?error={error}"
    if safe_next != "/":
        target += f"&next={quote(safe_next)}"
    return _redirect_after_auth(request, target)


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, db: DbDep, settings: SettingsDep, error: str = ""):
    """Render the register form. Bootstrap mode is computed from the DB."""
    bootstrap = users_repo.count_active_admins(db.conn) == 0

    if not bootstrap and not settings.allow_registration:
        # Self-service signup is closed; only an admin can create accounts.
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    current = getattr(request.state, "user", None)
    if current is not None and not bootstrap:
        # Already logged in and a normal account exists — no reason to be here.
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "active_page": "",
            "bootstrap": bootstrap,
            "error_message": _register_error_message(error),
        },
    )


def _register_error_message(error: str) -> str | None:
    if error == "unavailable":
        # Generic: does not distinguish "username taken" from a transient error,
        # so public registration cannot be used to enumerate existing accounts.
        return ui_labels.AUTH_REGISTER_UNAVAILABLE
    if error == "username_invalid":
        return ui_labels.AUTH_USERNAME_INVALID
    if error == "password_short":
        return ui_labels.AUTH_PASSWORD_TOO_SHORT
    if error == "rate_limited":
        return ui_labels.AUTH_REGISTER_RATE_LIMITED
    return None


@router.post("/register")
async def register_submit(
    request: Request,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    """Create a new account and start a session.

    Validation:

    * self-service registration is open (or this is the bootstrap account)
    * username matches :data:`USERNAME_PATTERN`
    * password length >= :data:`MIN_PASSWORD_LENGTH`
    * username not already taken (case-insensitively, enforced by the
      ``COLLATE NOCASE`` unique index)

    Bootstrap behaviour: when no active admin exists in the DB, the first
    account is forced to ``is_admin=True``. The query-string flag is
    cosmetic; the real determination is the DB state.
    """
    bootstrap = users_repo.count_active_admins(db.conn) == 0

    if not bootstrap and not settings.allow_registration:
        # Closed signup: refuse even hand-crafted POSTs that skip the form.
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    # Rate-limit open registration per IP so account creation and the
    # username-taken oracle are bounded the way login attempts are. Bootstrap
    # is never throttled — the first admin must be creatable.
    ip = client_ip_from_request(
        request,
        trust_proxy_headers=settings.trust_proxy_headers,
        trusted_proxy_count=settings.trusted_proxy_count,
    )
    if not bootstrap and rate_limit.register_is_locked(
        redis, ip=ip, max_attempts=settings.max_register_attempts_per_ip
    ):
        logger.warning("register rate-limited: ip=%s", ip)
        return _redirect_after_auth(request, "/register?error=rate_limited")

    if not USERNAME_PATTERN.fullmatch(username):
        return _redirect_after_auth(request, "/register?error=username_invalid")
    if len(password) < MIN_PASSWORD_LENGTH:
        return _redirect_after_auth(request, "/register?error=password_short")

    # Count this attempt against the per-IP window before the DB/argon2 work so
    # both successful creates and username-taken probes are bounded.
    if not bootstrap:
        rate_limit.record_register_attempt(redis, ip=ip, window_seconds=settings.login_lockout_seconds)

    try:
        user = users_repo.create_user(
            db.conn,
            username=username,
            password=password,
            is_admin=bootstrap,
        )
    except sqlite3.IntegrityError:
        # Log the real reason for operators, but show the user a generic
        # message (see _register_error_message) so /register does not confirm
        # whether the account exists.
        logger.info("register failed: username %s already taken", mask_username(username))
        return _redirect_after_auth(request, "/register?error=unavailable")

    # Flip the bootstrap latch immediately so subsequent requests skip the
    # admin-count query.
    if bootstrap:
        request.app.state.bootstrap_done = True
        logger.info("bootstrap admin created: %r", user.username)
    else:
        logger.info("new user registered: %r", user.username)

    request.session["uid"] = user.id
    request.session["sv"] = user.session_version
    return _redirect_after_auth(request, "/")


@router.post("/logout")
async def logout(request: Request):
    """Clear the session and bounce to the login form."""
    uid = request.session.get("uid")
    request.session.clear()
    if uid is not None:
        logger.info("logout for uid=%s", uid)
    return _redirect_after_auth(request, "/login")
