"""Admin-only views: user management and global jobs list.

All endpoints depend on :data:`AdminUserDep`, which 403s non-admins before
the handler runs. Mutating handlers additionally guard against the
"system locked out of itself" failure modes:

* Cannot deactivate / demote / delete the last active admin
  (enforced inside ``users_repo`` so future call sites inherit the rule).
* Self-action policy:

  - ``deactivate`` / ``toggle-admin``: blocked, because both could lock
    the operator out of the admin UI in a single click.
  - ``activate``: silently allowed — to reach this endpoint at all the
    admin must already be active, so activating self is a harmless no-op.
  - ``reset-password``: **deliberately allowed**. A single-admin deployment
    has no other admin to perform the reset, and the action is harmless
    (the operator types their own new password and logs back in). Logged
    at WARNING level for audit. A future self-service "change my
    password" page would supersede this.

The global jobs page reuses :func:`jobs._build_list_context` with
``owner_id=None`` (admin view), so the same UI components render — only
the data scope differs.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from whisper_ui.core.constants import DEFAULT_JOBS_PER_PAGE
from whisper_ui.core.models import JobStatus
from whisper_ui.storage import users_repo
from whisper_ui.storage.users_repo import LastAdminError
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.deps import AdminUserDep, DbDep, FileStoreDep, RedisDep, templates
from whisper_ui.web.routes.auth_routes import MIN_PASSWORD_LENGTH, USERNAME_PATTERN
from whisper_ui.web.routes.jobs import _build_list_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(request: Request, db: DbDep, admin: AdminUserDep, error: str = ""):
    """Render the user list with per-user action buttons."""
    users = users_repo.list_users(db.conn)

    # Per-user job count via a single grouped query keeps page load O(1)
    # in number of round trips even with many users.
    cur = db.conn.execute("SELECT owner_id, COUNT(*) AS cnt FROM jobs GROUP BY owner_id")
    job_counts = {row["owner_id"]: row["cnt"] for row in cur.fetchall()}

    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={
            "active_page": "admin",
            "users": users,
            "job_counts": job_counts,
            "current_admin_id": admin.id,
            "error_message": _admin_error_message(error),
        },
    )


def _admin_error_message(error: str) -> str | None:
    if error == "last_admin":
        return ui_labels.ADMIN_LAST_ADMIN_ERROR
    if error == "self_action":
        return ui_labels.ADMIN_SELF_ACTION_ERROR
    if error == "username_taken":
        return ui_labels.AUTH_USERNAME_TAKEN
    if error == "username_invalid":
        return ui_labels.AUTH_USERNAME_INVALID
    if error == "password_short":
        return ui_labels.AUTH_PASSWORD_TOO_SHORT
    return None


def _admin_redirect(target: str = "/admin/users", *, error: str = "") -> Response:
    if error:
        target = f"{target}?error={quote(error)}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users")
async def admin_create_user(
    request: Request,
    db: DbDep,
    admin: AdminUserDep,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    is_admin: Annotated[bool, Form()] = False,
):
    """Admin-driven user creation; same validation as the public /register."""
    if not USERNAME_PATTERN.fullmatch(username):
        return _admin_redirect(error="username_invalid")
    if len(password) < MIN_PASSWORD_LENGTH:
        return _admin_redirect(error="password_short")

    try:
        users_repo.create_user(db.conn, username=username, password=password, is_admin=is_admin)
    except sqlite3.IntegrityError:
        return _admin_redirect(error="username_taken")

    logger.info("admin %r created user %r (is_admin=%s)", admin.username, username, is_admin)
    return _admin_redirect()


@router.post("/users/{user_id}/deactivate")
async def admin_deactivate_user(user_id: int, db: DbDep, admin: AdminUserDep):
    if user_id == admin.id:
        return _admin_redirect(error="self_action")
    try:
        users_repo.set_active(db.conn, user_id, active=False)
    except LastAdminError:
        return _admin_redirect(error="last_admin")
    logger.info("admin %r deactivated uid=%s", admin.username, user_id)
    return _admin_redirect()


@router.post("/users/{user_id}/activate")
async def admin_activate_user(user_id: int, db: DbDep, admin: AdminUserDep):
    # No self-action guard: to reach this handler the admin must already
    # be active (the auth middleware would have cleared a deactivated
    # session). Activating self is a harmless no-op.
    users_repo.set_active(db.conn, user_id, active=True)
    logger.info("admin %r activated uid=%s", admin.username, user_id)
    return _admin_redirect()


@router.post("/users/{user_id}/toggle-admin")
async def admin_toggle_admin(user_id: int, db: DbDep, admin: AdminUserDep):
    """Promote a non-admin to admin or vice versa.

    Disallows self-action so an admin cannot accidentally lock themselves
    out of the admin UI in one click. To change your own admin status,
    an operator with shell access should manipulate the DB directly.
    """
    if user_id == admin.id:
        return _admin_redirect(error="self_action")
    target = users_repo.get_user_by_id(db.conn, user_id)
    if target is None:
        return _admin_redirect()
    try:
        users_repo.set_admin(db.conn, user_id, admin=not target.is_admin)
    except LastAdminError:
        return _admin_redirect(error="last_admin")
    logger.info("admin %r set is_admin=%s on uid=%s", admin.username, not target.is_admin, user_id)
    return _admin_redirect()


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: int,
    db: DbDep,
    admin: AdminUserDep,
    new_password: Annotated[str, Form()],
):
    """Set a user's password directly. Bumps session_version (invalidates
    all that user's sessions). No "old password" required — the admin is
    trusted to verify identity through an out-of-band channel.

    Self-reset is allowed (see module docstring) but logged at WARNING
    so audit log searches surface every "admin reset their own password"
    event. The next request from that admin will be rejected by the
    session-version check and they will be redirected back to /login.
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return _admin_redirect(error="password_short")
    target = users_repo.get_user_by_id(db.conn, user_id)
    if target is None:
        return _admin_redirect()
    users_repo.set_password(db.conn, user_id, new_password)
    if user_id == admin.id:
        logger.warning(
            "admin %r reset OWN password — session invalidated, re-login required",
            admin.username,
        )
    else:
        logger.info("admin %r reset password for %r", admin.username, target.username)
    return _admin_redirect()


@router.get("/jobs", response_class=HTMLResponse)
async def admin_jobs_page(
    request: Request,
    db: DbDep,
    redis: RedisDep,
    filestore: FileStoreDep,
    admin: AdminUserDep,
    status: str = "",
    page: int = 0,
):
    """Render the global jobs list (every owner, plus legacy NULL rows)."""
    valid_statuses = {"", *JobStatus}
    if status not in valid_statuses:
        status = ""
    # owner_id=None → no owner filter; admin sees everything.
    ctx = _build_list_context(db, redis, filestore, status, page, owner_id=None)
    ctx["active_page"] = "admin"
    ctx["status_counts"] = db.get_status_counts()
    ctx["DEFAULT_JOBS_PER_PAGE"] = DEFAULT_JOBS_PER_PAGE

    # Provide username lookup for the per-job "owner" badge so the template
    # does not have to query per row.
    users = {u.id: u.username for u in users_repo.list_users(db.conn)}
    ctx["owner_usernames"] = users
    return templates.TemplateResponse(request=request, name="admin_jobs.html", context=ctx)
