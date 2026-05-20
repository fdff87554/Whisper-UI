"""Login, register, logout endpoint behaviour: bootstrap, validation, errors."""

from __future__ import annotations

import fakeredis
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
    # FakeRedis (rather than MagicMock) so rate_limit.is_locked /
    # check_and_increment exercise the real INCR / EXPIRE / GET path.
    application.state.redis = fakeredis.FakeRedis()
    application.state.bootstrap_done = True
    return application


def _anon_client(app):
    client = TestClient(app, follow_redirects=False)
    client.headers["Origin"] = "http://testserver"
    return client


def test_login_page_renders_for_anonymous(app):
    client = _anon_client(app)

    resp = client.get("/login")

    assert resp.status_code == 200
    assert "登入" in resp.text


def test_login_redirects_already_logged_in_user_to_root(app, test_user):
    client = authed_test_client(app, test_user)

    resp = client.get("/login", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_login_post_with_correct_credentials_sets_session(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    assert "session" in resp.cookies or any(c.name == "session" for c in client.cookies.jar)


def test_login_post_case_insensitive_username(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "ALICE", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_login_post_with_wrong_password_returns_to_login_with_error(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "wrongpassword"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?error=invalid")


def test_login_post_with_unknown_username_returns_same_error(app):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "ghost", "password": "password123"},
    )

    assert resp.status_code == 302
    # Same error code as wrong-password so existence is not leaked.
    assert resp.headers["location"].startswith("/login?error=invalid")


def test_login_post_for_inactive_user_returns_inactive_error(app, db, test_user):
    users_repo.set_active(db.conn, test_user.id, active=False)
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?error=inactive")


def test_login_next_parameter_only_accepts_relative_path(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123", "next": "http://evil.example/x"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"  # absolute next was discarded


def test_login_next_parameter_relative_path_respected(app, test_user):
    client = _anon_client(app)

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123", "next": "/upload"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/upload"


def test_login_htmx_response_uses_hx_redirect(app, test_user):
    client = _anon_client(app)
    client.headers["HX-Request"] = "true"

    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 204
    assert resp.headers["hx-redirect"] == "/"


def test_register_page_renders_bootstrap_when_no_admin(app, db):
    """When count_active_admins == 0, /register shows the bootstrap text."""
    client = _anon_client(app)

    resp = client.get("/register")

    assert resp.status_code == 200
    assert "管理員" in resp.text


def test_register_page_renders_normal_mode_when_admin_exists(app, db, test_admin):
    client = _anon_client(app)

    resp = client.get("/register")

    assert resp.status_code == 200
    assert "註冊帳號" in resp.text


def test_register_first_account_becomes_admin(app, db):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "founder", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/"

    created = users_repo.get_user_by_username(db.conn, "founder")
    assert created is not None
    assert created.is_admin is True
    assert app.state.bootstrap_done is True


def test_register_second_account_is_not_admin(app, db, test_admin):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "bob", "password": "password123"},
    )

    assert resp.status_code == 302

    created = users_repo.get_user_by_username(db.conn, "bob")
    assert created is not None
    assert created.is_admin is False


def test_register_duplicate_username_returns_error(app, db, test_user, test_admin):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "alice", "password": "differentpw"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/register?error=username_taken")


def test_register_invalid_username_pattern_returns_error(app, test_admin):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "ab", "password": "password123"},  # too short
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/register?error=username_invalid")


def test_register_short_password_returns_error(app, test_admin):
    client = _anon_client(app)

    resp = client.post(
        "/register",
        data={"username": "newuser", "password": "short"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/register?error=password_short")


def test_logout_clears_session_and_redirects(app, test_user):
    client = authed_test_client(app, test_user)

    resp = client.post("/logout", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_logout_with_no_session_still_redirects(app):
    client = _anon_client(app)

    resp = client.post("/logout")

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_login_does_not_log_password(app, test_user, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="whisper_ui.web.routes.auth_routes")
    client = _anon_client(app)

    client.post("/login", data={"username": "alice", "password": "secretvalue42"})
    client.post("/login", data={"username": "alice", "password": "wrongpasswordsecret"})

    # Neither the right nor wrong password may appear in any log message.
    combined = " ".join(record.getMessage() for record in caplog.records)
    assert "secretvalue42" not in combined
    assert "wrongpasswordsecret" not in combined


def test_session_persists_across_requests_after_login(app, test_user):
    client = _anon_client(app)

    login_resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )
    assert login_resp.status_code == 302

    # follow-up request — should be authenticated now
    follow = client.get("/", follow_redirects=False)
    assert follow.status_code == 200


def test_login_rate_limit_blocks_after_threshold(app, test_user):
    """Exceeding settings.max_login_attempts within the window must block
    further attempts. Default is 5 attempts → the 6th request hits the
    rate-limit branch regardless of password correctness.
    """
    client = _anon_client(app)
    settings = app.state.settings
    # Drive the per-user counter over the threshold with deliberate fails.
    for _ in range(settings.max_login_attempts + 1):
        client.post("/login", data={"username": "alice", "password": "wrong"})

    # Even the correct password now bounces because the counter is over the limit.
    resp = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?error=rate_limited")


def test_login_rate_limit_resets_on_successful_login(app, test_user):
    client = _anon_client(app)
    settings = app.state.settings
    # 4 fails (one under the limit).
    for _ in range(settings.max_login_attempts - 1):
        client.post("/login", data={"username": "alice", "password": "wrong"})

    # Successful login clears the per-user counter.
    ok = client.post(
        "/login",
        data={"username": "alice", "password": "password123"},
    )
    assert ok.status_code == 302
    assert ok.headers["location"] == "/"

    # After the reset, a fresh fail-burst should still be allowed.
    fresh = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert fresh.status_code == 302
    assert fresh.headers["location"].startswith("/login?error=invalid")


def test_login_rate_limit_uses_generic_message(app):
    """The rate-limit error message must not reveal whether the username
    exists, so attackers cannot probe usernames via lockout behaviour.
    """
    client = _anon_client(app)
    settings = app.state.settings
    for _ in range(settings.max_login_attempts + 1):
        client.post("/login", data={"username": "ghost", "password": "wrong"})

    resp = client.post(
        "/login",
        data={"username": "ghost", "password": "wrong"},
        follow_redirects=True,
    )

    # Rate-limit message is rendered; same template as any other login error.
    assert resp.status_code == 200
    assert "嘗試次數過多" in resp.text


def test_change_password_invalidates_existing_session(app, db, test_user):
    """End-to-end equivalent of "admin reset" — bump session_version, then
    confirm an existing session is no longer valid.
    """
    # Establish a session by hand-built cookie.
    client = _anon_client(app)
    client.cookies.set("session", make_session_cookie(test_user))

    # First request: should work.
    first = client.get("/", follow_redirects=False)
    assert first.status_code == 200

    # Bump session_version (admin's "set new password" would do this).
    users_repo.set_password(db.conn, test_user.id, "newpassword")

    # Second request: stale session, should redirect to /login.
    second = client.get("/", follow_redirects=False)
    assert second.status_code == 302
    assert second.headers["location"].startswith("/login?next=")
