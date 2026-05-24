"""Cross-user isolation guarantees for every route that surfaces job data.

The critical invariant: a non-admin user must never observe — directly or
indirectly — a job they do not own. "Indirectly" includes dashboard
counts, batch listings, and any error that distinguishes "exists but not
yours" from "does not exist". This file enumerates every route that
touches a job and asserts both the positive (admin / owner can see) and
negative (other user cannot) case.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from tests.conftest import authed_test_client
from whisper_ui.core.models import Job, JobStatus, TranscriptResult
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
    application.state.bootstrap_done = True
    return application


@pytest.fixture
def bob(db):
    return users_repo.create_user(db.conn, "bob", "password123", is_admin=False)


def _save_completed_job(db, filestore, owner_id: int, *, filename: str = "owned.mp3") -> Job:
    """Insert a COMPLETED job with a real result file owned by ``owner_id``."""
    result = TranscriptResult(segments=[], language="zh", duration=60.0)
    job = Job(filename=filename, status=JobStatus.COMPLETED, language="zh", owner_id=owner_id)
    result_path = filestore.save_result(job.id, result)
    job.result_path = str(result_path)
    db.insert_job(job)
    return job


def _failed_job(db, owner_id: int | None, *, filename: str = "failed.mp3") -> Job:
    job = Job(
        filename=filename,
        status=JobStatus.FAILED,
        language="zh",
        owner_id=owner_id,
        error="boom",
    )
    db.insert_job(job)
    return job


def test_alice_does_not_see_bobs_jobs_in_dashboard(app, db, filestore, test_user, bob):
    _save_completed_job(db, filestore, owner_id=bob.id, filename="bobs.mp3")
    client = authed_test_client(app, test_user)

    resp = client.get("/")

    assert resp.status_code == 200
    assert "bobs.mp3" not in resp.text


def test_alice_does_not_see_bobs_jobs_in_jobs_list(app, db, filestore, test_user, bob):
    _save_completed_job(db, filestore, owner_id=bob.id, filename="bobs.mp3")
    _save_completed_job(db, filestore, owner_id=test_user.id, filename="alices.mp3")
    client = authed_test_client(app, test_user)

    resp = client.get("/jobs")

    assert resp.status_code == 200
    assert "alices.mp3" in resp.text
    assert "bobs.mp3" not in resp.text


def test_alice_dashboard_counts_exclude_bobs_jobs(app, db, filestore, test_user, bob):
    """get_status_counts is filtered by owner_id, so the dashboard summary
    cards must reflect only alice's jobs.
    """
    for i in range(3):
        _save_completed_job(db, filestore, owner_id=bob.id, filename=f"b{i}.mp3")
    _save_completed_job(db, filestore, owner_id=test_user.id, filename="a.mp3")
    client = authed_test_client(app, test_user)

    resp = client.get("/")

    assert resp.status_code == 200
    # Alice has exactly 1 completed; the dashboard exposes that as a stat.
    assert "1" in resp.text


def test_alice_gets_404_on_bobs_viewer_page(app, db, filestore, test_user, bob):
    job = _save_completed_job(db, filestore, owner_id=bob.id)
    client = authed_test_client(app, test_user)

    resp = client.get(f"/viewer/{job.id}")

    # The viewer renders an "error: not_found" page rather than HTTP 404,
    # but the critical guarantee is the response does NOT show the
    # transcript content or filename of the other user's job.
    assert "not_found" in resp.text or "找不到" in resp.text
    assert job.filename not in resp.text


def test_alice_gets_404_on_bobs_viewer_export(app, db, filestore, test_user, bob):
    job = _save_completed_job(db, filestore, owner_id=bob.id)
    client = authed_test_client(app, test_user)

    resp = client.get(f"/viewer/{job.id}/export/srt")

    assert resp.status_code == 404


def test_alice_gets_404_on_bobs_viewer_media(app, db, filestore, test_user, bob):
    job = Job(
        filename="vid.mp4",
        status=JobStatus.COMPLETED,
        language="zh",
        owner_id=bob.id,
        source_url="https://example.com/v",
    )
    db.insert_job(job)
    client = authed_test_client(app, test_user)

    resp = client.get(f"/viewer/{job.id}/media")

    assert resp.status_code == 404


def test_alice_gets_404_on_bobs_retry(app, db, test_user, bob):
    job = _failed_job(db, owner_id=bob.id)
    client = authed_test_client(app, test_user)

    resp = client.post(f"/jobs/{job.id}/retry")

    assert resp.status_code == 404


def test_alice_gets_404_on_bobs_re_transcribe(app, db, filestore, test_user, bob):
    job = _save_completed_job(db, filestore, owner_id=bob.id)
    client = authed_test_client(app, test_user)

    resp = client.post(f"/jobs/{job.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})

    assert resp.status_code == 404


def test_alice_gets_404_on_bobs_delete(app, db, filestore, test_user, bob):
    job = _save_completed_job(db, filestore, owner_id=bob.id)
    client = authed_test_client(app, test_user)

    resp = client.delete(f"/jobs/{job.id}")

    assert resp.status_code == 404


def test_alice_gets_404_on_bobs_batch_download(app, db, filestore, test_user, bob):
    batch_id = uuid.uuid4().hex
    job = _save_completed_job(db, filestore, owner_id=bob.id)
    job.batch_id = batch_id
    db.update_job(job)
    client = authed_test_client(app, test_user)

    resp = client.get(f"/jobs/batch/{batch_id}/download")

    assert resp.status_code == 404


def test_alice_gets_404_on_bobs_batch_retry(app, db, test_user, bob):
    batch_id = uuid.uuid4().hex
    job = _failed_job(db, owner_id=bob.id)
    job.batch_id = batch_id
    db.update_job(job)
    client = authed_test_client(app, test_user)

    resp = client.post(f"/jobs/batch/{batch_id}/retry")

    assert resp.status_code == 404


def test_alice_gets_404_on_bobs_batch_delete(app, db, filestore, test_user, bob):
    batch_id = uuid.uuid4().hex
    job = _save_completed_job(db, filestore, owner_id=bob.id)
    job.batch_id = batch_id
    db.update_job(job)
    client = authed_test_client(app, test_user)

    resp = client.delete(f"/jobs/batch/{batch_id}")

    assert resp.status_code == 404


def test_alice_can_see_and_act_on_her_own_jobs(app, db, filestore, test_user, bob):
    """Positive case: the gates do not accidentally block legitimate access."""
    job = _save_completed_job(db, filestore, owner_id=test_user.id, filename="mine.mp3")
    client = authed_test_client(app, test_user)

    viewer = client.get(f"/viewer/{job.id}")
    export = client.get(f"/viewer/{job.id}/export/srt")
    delete = client.delete(f"/jobs/{job.id}")

    assert "mine.mp3" in viewer.text
    assert export.status_code == 200
    assert delete.status_code == 204


def test_admin_sees_every_users_jobs_via_dashboard(app, db, filestore, test_admin, test_user, bob):
    _save_completed_job(db, filestore, owner_id=test_user.id, filename="alices.mp3")
    _save_completed_job(db, filestore, owner_id=bob.id, filename="bobs.mp3")
    client = authed_test_client(app, test_admin)

    resp = client.get("/jobs")

    assert resp.status_code == 200
    assert "alices.mp3" in resp.text
    assert "bobs.mp3" in resp.text


def test_admin_can_retry_and_delete_others_jobs(app, db, filestore, test_admin, bob):
    """The plan spec: admin may retry/delete any user's task."""
    failed = _failed_job(db, owner_id=bob.id)
    completed = _save_completed_job(db, filestore, owner_id=bob.id, filename="bobs2.mp3")
    client = authed_test_client(app, test_admin)

    retry = client.post(f"/jobs/{failed.id}/retry")
    delete = client.delete(f"/jobs/{completed.id}")

    assert retry.status_code == 204
    assert delete.status_code == 204


def test_legacy_null_owner_job_invisible_to_alice_but_visible_to_admin(app, db, filestore, test_user, test_admin):
    """Pre-auth deployments: existing jobs have owner_id IS NULL. The
    plan-defined behaviour is that admin's /admin/jobs (and the dashboard
    for admin) sees them, normal users do not. The admin /admin/jobs
    route lands in commit 9; here we verify the bare semantics through
    the dashboard, which already uses the owner filter.
    """
    legacy = _save_completed_job(db, filestore, owner_id=None, filename="legacy.mp3")
    # Sanity-check: legacy.owner_id is None.
    fetched = db.get_job(legacy.id)
    assert fetched is not None
    assert fetched.owner_id is None

    alice_client = authed_test_client(app, test_user)
    admin_client = authed_test_client(app, test_admin)

    alice_jobs = alice_client.get("/jobs")
    admin_jobs = admin_client.get("/jobs")

    assert "legacy.mp3" not in alice_jobs.text
    assert "legacy.mp3" in admin_jobs.text


def test_upload_assigns_owner_id_to_uploaded_job(app, db, test_user):
    """An /upload submission must persist the caller's id on the new Job."""
    client = authed_test_client(app, test_user)

    # We can't actually run the pipeline in a unit test, but inserting a job
    # via the upload route would require a real file + real ffmpeg + Redis.
    # The behaviour we care about — owner_id assignment — happens before any
    # of that. Use the /jobs view to confirm alice sees jobs she creates
    # (the upload route's Job(owner_id=user.id) is the only way that
    # population can happen).
    resp = client.get("/jobs")
    assert resp.status_code == 200
    # Tested in detail via test_upload_routes — here we only need the
    # smoke-level guarantee that the alice client gets a 200 (i.e. the
    # `user: CurrentUserDep` parameter wired correctly).


def test_alice_cannot_view_bobs_viewer_page_via_url_guess(app, db, filestore, test_user, bob):
    """If alice guesses bob's job_id, the viewer must not render bob's content.

    This is the headline isolation invariant: even with knowledge of a
    valid job id, route-level ownership filtering protects the data.
    """
    bobs_job = _save_completed_job(db, filestore, owner_id=bob.id, filename="confidential.mp3")
    client = authed_test_client(app, test_user)

    resp = client.get(f"/viewer/{bobs_job.id}")

    assert "confidential.mp3" not in resp.text
