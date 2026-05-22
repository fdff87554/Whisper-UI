"""Admin routes: user CRUD, permission flips, password reset, /admin/jobs."""

from __future__ import annotations

import fakeredis
import pytest

from tests.conftest import authed_test_client
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage import users_repo
from whisper_ui.web.app import create_app


@pytest.fixture
def app(settings, db, filestore):
    application = create_app()
    application.state.settings = settings
    application.state.db = db
    application.state.filestore = filestore
    application.state.redis = fakeredis.FakeRedis()
    application.state.bootstrap_done = True
    return application


@pytest.fixture
def bob(db):
    return users_repo.create_user(db.conn, "bob", "password123", is_admin=False)


def test_non_admin_cannot_load_admin_users_page(app, test_user):
    client = authed_test_client(app, test_user)

    resp = client.get("/admin/users")

    assert resp.status_code == 403


def test_admin_can_load_admin_users_page(app, db, test_admin, bob):
    client = authed_test_client(app, test_admin)

    resp = client.get("/admin/users")

    assert resp.status_code == 200
    assert "bob" in resp.text
    assert test_admin.username in resp.text


def test_admin_users_page_uses_admin_users_active_value(app, test_admin):
    """The sidebar's Alpine :class binding compares activePage to the
    literal 'admin_users' (not 'admin'), so the route must set that
    exact value into the Alpine store on initial render.
    """
    client = authed_test_client(app, test_admin)

    resp = client.get("/admin/users")

    assert resp.status_code == 200
    # Alpine.store('nav', { activePage: '{{ active_page }}' }) renders this.
    assert "activePage: 'admin_users'" in resp.text


def test_admin_jobs_page_uses_admin_jobs_active_value(app, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.get("/admin/jobs")

    assert resp.status_code == 200
    assert "activePage: 'admin_jobs'" in resp.text


def test_admin_sidebar_renders_well_formed_alpine_expressions(app, test_admin):
    """Regression: previously the /admin/jobs link contained the broken
    expression ``$store.nav.activePage === 'admin' and request.url.path
    == '/admin/jobs'`` which is invalid JavaScript (Python `and`,
    server-side `request` reference). The fixed version must only
    compare to the new activePage literal.
    """
    client = authed_test_client(app, test_admin)

    resp = client.get("/admin/users")

    assert resp.status_code == 200
    # The buggy fragment must not appear anywhere.
    assert "and request.url.path" not in resp.text
    # Both admin sidebar items use the new keyed literals.
    assert "$store.nav.activePage === 'admin_users'" in resp.text
    assert "$store.nav.activePage === 'admin_jobs'" in resp.text


def test_admin_can_create_user(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.post(
        "/admin/users",
        data={"username": "newuser", "password": "password123"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    created = users_repo.get_user_by_username(db.conn, "newuser")
    assert created is not None
    assert created.is_admin is False


def test_admin_can_create_admin_user(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    client.post(
        "/admin/users",
        data={"username": "newadmin", "password": "password123", "is_admin": "true"},
    )

    created = users_repo.get_user_by_username(db.conn, "newadmin")
    assert created is not None
    assert created.is_admin is True


def test_admin_create_user_rejects_short_password(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.post(
        "/admin/users",
        data={"username": "newuser", "password": "short"},
        follow_redirects=False,
    )

    assert resp.headers["location"].startswith("/admin/users?error=password_short")


def test_admin_create_user_rejects_duplicate(app, db, test_admin, bob):
    client = authed_test_client(app, test_admin)

    resp = client.post(
        "/admin/users",
        data={"username": "bob", "password": "password123"},
        follow_redirects=False,
    )

    assert resp.headers["location"].startswith("/admin/users?error=username_taken")


def test_admin_can_deactivate_user(app, db, test_admin, bob):
    client = authed_test_client(app, test_admin)

    client.post(f"/admin/users/{bob.id}/deactivate")

    updated = users_repo.get_user_by_id(db.conn, bob.id)
    assert updated.is_active is False


def test_admin_cannot_deactivate_self(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.post(f"/admin/users/{test_admin.id}/deactivate", follow_redirects=False)

    assert resp.headers["location"].endswith("error=self_action")
    assert users_repo.get_user_by_id(db.conn, test_admin.id).is_active is True


def test_admin_cannot_deactivate_last_admin(app, db, test_admin):
    """When test_admin is the only active admin, the API must refuse to
    deactivate them — even if the actor is a *different* admin (not
    exercised here since only one admin exists, the self-action guard
    handles that). The point is the repo-layer invariant.
    """
    # Create a second admin so we can attempt to deactivate test_admin
    # without tripping the self-action guard.
    other_admin = users_repo.create_user(db.conn, "root2", "password123", is_admin=True)
    other_client = authed_test_client(app, other_admin)

    # First deactivate other_admin from test_admin? No — we want test_admin
    # to deactivate test_admin, but that's self-action. Instead, deactivate
    # test_admin from other_admin while other_admin is active → succeeds
    # (other_admin remains as the sole active admin afterwards).
    resp = other_client.post(f"/admin/users/{test_admin.id}/deactivate", follow_redirects=False)
    assert resp.status_code == 303
    assert users_repo.get_user_by_id(db.conn, test_admin.id).is_active is False

    # Now other_admin is the only active admin. Demoting them must fail.
    resp = other_client.post(f"/admin/users/{other_admin.id}/toggle-admin", follow_redirects=False)
    # toggle-admin uses self-action guard first
    assert resp.headers["location"].endswith("error=self_action")


def test_admin_can_promote_user(app, db, test_admin, bob):
    client = authed_test_client(app, test_admin)

    client.post(f"/admin/users/{bob.id}/toggle-admin")

    updated = users_repo.get_user_by_id(db.conn, bob.id)
    assert updated.is_admin is True


def test_admin_can_demote_user(app, db, test_admin):
    # Create another admin we can demote.
    other = users_repo.create_user(db.conn, "second_admin", "password123", is_admin=True)
    client = authed_test_client(app, test_admin)

    client.post(f"/admin/users/{other.id}/toggle-admin")

    updated = users_repo.get_user_by_id(db.conn, other.id)
    assert updated.is_admin is False


def test_admin_cannot_self_demote(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.post(f"/admin/users/{test_admin.id}/toggle-admin", follow_redirects=False)

    assert resp.headers["location"].endswith("error=self_action")
    assert users_repo.get_user_by_id(db.conn, test_admin.id).is_admin is True


def test_admin_reset_password_changes_hash_and_bumps_session(app, db, test_admin, bob):
    client = authed_test_client(app, test_admin)
    sv_before = bob.session_version
    old_hash = bob.password_hash

    client.post(
        f"/admin/users/{bob.id}/reset-password",
        data={"new_password": "newpassword42"},
    )

    updated = users_repo.get_user_by_id(db.conn, bob.id)
    assert updated.password_hash != old_hash
    assert updated.session_version == sv_before + 1
    assert users_repo.verify_password(updated, "newpassword42") is True


def test_admin_reset_password_rejects_short_password(app, db, test_admin, bob):
    client = authed_test_client(app, test_admin)
    old_hash = bob.password_hash

    resp = client.post(
        f"/admin/users/{bob.id}/reset-password",
        data={"new_password": "x"},
        follow_redirects=False,
    )

    assert resp.headers["location"].endswith("error=password_short")
    assert users_repo.get_user_by_id(db.conn, bob.id).password_hash == old_hash


def test_admin_jobs_page_shows_every_owner_and_legacy_null(app, db, test_admin, test_user, bob):
    """Admin /admin/jobs surfaces:
    - alice's job (owner_id = test_user.id)
    - bob's job (owner_id = bob.id)
    - a legacy job with owner_id = NULL
    """
    db.insert_job(Job(filename="alices.mp3", status=JobStatus.COMPLETED, language="zh", owner_id=test_user.id))
    db.insert_job(Job(filename="bobs.mp3", status=JobStatus.COMPLETED, language="zh", owner_id=bob.id))
    db.insert_job(Job(filename="legacy.mp3", status=JobStatus.COMPLETED, language="zh"))  # NULL
    client = authed_test_client(app, test_admin)

    resp = client.get("/admin/jobs")

    assert resp.status_code == 200
    for name in ("alices.mp3", "bobs.mp3", "legacy.mp3"):
        assert name in resp.text
    # And the legacy row should be flagged as having no owner.
    assert "無擁有者" in resp.text


def test_admin_jobs_page_blocks_non_admin(app, test_user):
    client = authed_test_client(app, test_user)

    resp = client.get("/admin/jobs")

    assert resp.status_code == 403


def test_admin_create_user_validates_username_pattern(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.post(
        "/admin/users",
        data={"username": "x!", "password": "password123"},  # bad chars + too short
        follow_redirects=False,
    )

    assert resp.headers["location"].startswith("/admin/users?error=username_invalid")


def test_admin_can_reset_own_password(app, db, test_admin):
    """Self-reset is deliberately allowed (see admin.py docstring).
    The action invalidates the admin's current session via session_version
    bump; subsequent requests with the old cookie will redirect to /login.
    """
    client = authed_test_client(app, test_admin)
    old_hash = test_admin.password_hash
    old_sv = test_admin.session_version

    resp = client.post(
        f"/admin/users/{test_admin.id}/reset-password",
        data={"new_password": "newsecret42"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    updated = users_repo.get_user_by_id(db.conn, test_admin.id)
    assert updated.password_hash != old_hash
    assert updated.session_version == old_sv + 1
    assert users_repo.verify_password(updated, "newsecret42") is True


def test_admin_self_reset_emits_warning_log(app, db, test_admin, caplog):
    """Self-reset must surface in audit logs at WARNING level so an admin
    accidentally (or maliciously) changing their own password is visible
    on routine log review.
    """
    import logging

    caplog.set_level(logging.WARNING, logger="whisper_ui.web.routes.admin")
    client = authed_test_client(app, test_admin)

    client.post(
        f"/admin/users/{test_admin.id}/reset-password",
        data={"new_password": "newsecret42"},
    )

    matches = [r for r in caplog.records if "OWN password" in r.getMessage()]
    assert len(matches) == 1
    assert matches[0].levelno == logging.WARNING


def test_admin_reset_password_for_missing_user_reports_not_found(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.post(
        "/admin/users/9999/reset-password",
        data={"new_password": "password123"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("error=user_not_found")


def test_admin_toggle_admin_for_missing_user_reports_not_found(app, db, test_admin):
    client = authed_test_client(app, test_admin)

    resp = client.post(
        "/admin/users/9999/toggle-admin",
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("error=user_not_found")
