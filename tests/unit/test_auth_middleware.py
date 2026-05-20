"""Tests for the session/auth middleware: whitelist, bootstrap latch, CSRF, htmx redirect."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from tests.conftest import authed_test_client, make_session_cookie
from whisper_ui.storage import users_repo
from whisper_ui.web.app import create_app


@pytest.fixture
def app(settings, db, filestore):
    application = create_app()
    application.state.settings = settings
    application.state.db = db
    application.state.filestore = filestore
    application.state.redis = MagicMock()
    application.state.redis.hgetall.return_value = {}
    # Default to "bootstrap finished" so individual tests can opt back into
    # bootstrap mode by flipping the latch.
    application.state.bootstrap_done = True
    return application


def test_anonymous_request_to_protected_path_redirects_to_login(app):
    client = TestClient(app, follow_redirects=False)

    resp = client.get("/")

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_anonymous_htmx_request_returns_401_with_hx_redirect(app):
    client = TestClient(app)
    client.headers["HX-Request"] = "true"

    resp = client.get("/jobs/list")

    assert resp.status_code == 401
    assert resp.headers["hx-redirect"].startswith("/login?next=")


def test_static_assets_are_public(app):
    client = TestClient(app)

    resp = client.get("/static/style.css")

    # The asset file may or may not exist depending on whether `mise run css`
    # has been executed, but the auth layer must not gate it.
    assert resp.status_code in (200, 404)


def test_health_endpoint_is_public(app):
    client = TestClient(app)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_login_path_is_public_for_anonymous_get(app):
    """The /login route itself does not exist until commit 6, but the
    middleware must allow anonymous access so the future login form is
    reachable. The behaviour to verify here is "middleware does not
    redirect", which means we get whatever the router decides — 404 in
    this commit, 200 in commit 6+.
    """
    client = TestClient(app, follow_redirects=False)

    resp = client.get("/login")

    assert resp.status_code != 302  # not redirected to login
    assert "/login?next=" not in resp.headers.get("location", "")


def test_bootstrap_latch_redirects_when_no_admin_exists(app, db):
    app.state.bootstrap_done = False
    # No active admins → bootstrap mode.
    client = TestClient(app, follow_redirects=False)

    resp = client.get("/")

    assert resp.status_code == 302
    assert resp.headers["location"] == "/register?bootstrap=1"


def test_bootstrap_latch_flips_off_once_admin_exists(app, db, test_admin):
    """When count_active_admins > 0 the middleware should flip the latch
    on first request and never redirect to /register?bootstrap=1 again.
    """
    app.state.bootstrap_done = False
    client = TestClient(app, follow_redirects=False)

    resp = client.get("/")

    assert resp.status_code == 302
    # Redirected to /login (normal unauthenticated flow), NOT to bootstrap.
    assert resp.headers["location"].startswith("/login?next=")
    assert app.state.bootstrap_done is True


def test_csrf_post_without_origin_is_rejected(app, test_user):
    client = TestClient(app)
    client.cookies.set("session", make_session_cookie(test_user))
    # NB: deliberately no Origin header.
    client.headers.pop("Origin", None)

    resp = client.post("/upload", data={})

    assert resp.status_code == 403


def test_csrf_post_with_mismatched_origin_is_rejected(app, test_user):
    client = TestClient(app)
    client.cookies.set("session", make_session_cookie(test_user))
    client.headers["Origin"] = "http://evil.example"

    resp = client.post("/upload", data={})

    assert resp.status_code == 403


def test_csrf_post_with_matching_origin_passes_csrf_check(app, test_user):
    """Origin matching the Host header (testserver) passes the CSRF gate;
    the request still fails downstream (no file uploaded), but with a
    different status than 403.
    """
    client = authed_test_client(app, test_user)

    resp = client.post("/upload", data={})

    # Anything-but-403 means CSRF passed and the route validator took over.
    assert resp.status_code != 403


def test_session_with_stale_session_version_is_cleared(app, db, test_user):
    # Build a cookie with sv=0, then bump session_version to 1.
    cookie = make_session_cookie(test_user)
    users_repo.bump_session_version = None  # type: ignore[attr-defined]
    db.conn.execute("UPDATE users SET session_version = 1 WHERE id = ?", (test_user.id,))
    db.conn.commit()

    client = TestClient(app, follow_redirects=False)
    client.cookies.set("session", cookie)

    resp = client.get("/")

    # Stale session → treated as anonymous → redirected to login.
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_session_for_deactivated_user_is_cleared(app, db, test_user):
    cookie = make_session_cookie(test_user)
    users_repo.set_active(db.conn, test_user.id, active=False)

    client = TestClient(app, follow_redirects=False)
    client.cookies.set("session", cookie)

    resp = client.get("/")

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_admin_dependency_passes_for_admin_user(app, test_admin):
    """Smoke-test require_admin via a real route: /upload is non-admin so a
    normal authed_client works; once /admin/users exists in commit 9 we'll
    have a dedicated test. For now, validate the dep through deps.py wiring
    by ensuring admin_client itself works on the public dashboard.
    """
    client = authed_test_client(app, test_admin)

    resp = client.get("/")

    assert resp.status_code == 200


def test_unknown_session_uid_treated_as_anonymous(app, db, test_user):
    # Forge a cookie referencing a user id that does not exist.
    fake_user = type("U", (), {"id": 9999, "session_version": 0})()
    cookie = make_session_cookie(fake_user)  # type: ignore[arg-type]

    client = TestClient(app, follow_redirects=False)
    client.cookies.set("session", cookie)

    resp = client.get("/")

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")
