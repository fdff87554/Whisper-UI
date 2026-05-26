from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import authed_test_client, flash_messages
from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.ui import labels as ui_labels
from whisper_ui.web.app import create_app
from whisper_ui.web.deps import _format_relative_time, _format_time, make_content_disposition


@pytest.fixture
def app(settings, db, filestore, test_user):
    application = create_app()
    application.state.settings = settings
    application.state.db = db
    application.state.filestore = filestore
    application.state.redis = MagicMock()
    # Mock redis.hgetall to return empty dict (no progress data)
    application.state.redis.hgetall.return_value = {}
    # The middleware skips bootstrap mode once an admin exists. Tests
    # create users explicitly via fixtures, so flipping this latch up
    # front avoids /register?bootstrap=1 redirects before the first request.
    application.state.bootstrap_done = True

    # Convenience: existing tests build jobs via db.insert_job(Job(...))
    # without an owner_id, which would make them invisible to the authed
    # `client` (alice) after the owner-gate landed. Patch the insert so a
    # missing owner_id silently defaults to alice — explicit ownership in
    # new tests still wins. test_database.py uses the bare `db` fixture
    # from conftest.py and is unaffected.
    original_insert = db.insert_job

    def insert_with_default_owner(job):
        if job.owner_id is None:
            job.owner_id = test_user.id
        original_insert(job)

    db.insert_job = insert_with_default_owner
    return application


@pytest.fixture
def client(app, test_user):
    return authed_test_client(app, test_user)


@pytest.fixture
def admin_client(app, test_admin):
    return authed_test_client(app, test_admin)


def _create_completed_job(db, filestore) -> Job:
    result = TranscriptResult(
        segments=[],
        language="zh",
        duration=60.0,
    )
    job = Job(filename="test.mp3", status=JobStatus.COMPLETED, language="zh")
    result_path = filestore.save_result(job.id, result)
    job.result_path = str(result_path)
    db.insert_job(job)
    return job


def _create_failed_job(db, *, source_url: str | None = None) -> Job:
    job = Job(filename="fail.mp3", status=JobStatus.FAILED, error="test error", source_url=source_url)
    db.insert_job(job)
    return job


def _completed_upload_job_with_audio(db, filestore, *, filename: str = "meeting.mp3") -> Job:
    """A COMPLETED upload job with both its source audio and result on disk."""
    result = TranscriptResult(segments=[], language="zh", duration=60.0)
    job = Job(filename=filename, status=JobStatus.COMPLETED, language="zh", model_name="large-v3")
    filestore.save_upload(job.id, filename, b"original audio bytes")
    job.filepath = str(filestore.get_upload_path(job.id, filename))
    job.result_path = str(filestore.save_result(job.id, result))
    db.insert_job(job)
    return job


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestDashboardRoutes:
    def test_root_serves_dashboard(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "總覽" in resp.text

    def test_dashboard_active_fragment(self, client):
        resp = client.get("/dashboard/active")
        assert resp.status_code == 200

    def test_dashboard_polls_slowly_when_idle(self, client, db):
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'hx-trigger="every 30s"' in resp.text

    def test_dashboard_polls_fast_when_active(self, client, db):
        db.insert_job(Job(filename="active.mp3", status=JobStatus.PROCESSING, language="zh"))
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'hx-trigger="every 5s"' in resp.text

    def test_dashboard_active_fragment_carries_idle_trigger(self, client, db):
        """Regression: the fragment must include its own wrapper + hx-trigger
        so the interval gets recomputed on every poll. Previously the trigger
        was pinned to whatever dashboard.html chose on initial render."""
        resp = client.get("/dashboard/active")
        assert resp.status_code == 200
        assert 'id="dashboard-active"' in resp.text
        assert 'hx-trigger="every 30s"' in resp.text
        assert 'hx-swap="morph:outerHTML"' in resp.text

    def test_dashboard_active_fragment_switches_to_fast_trigger(self, client, db):
        """Fragment requested while a job is active must return the fast
        trigger, proving the swap path picks up state transitions."""
        db.insert_job(Job(filename="active.mp3", status=JobStatus.PROCESSING, language="zh"))
        resp = client.get("/dashboard/active")
        assert resp.status_code == 200
        assert 'id="dashboard-active"' in resp.text
        assert 'hx-trigger="every 5s"' in resp.text


class TestUploadRoutes:
    def test_upload_page(self, client):
        resp = client.get("/upload")
        assert resp.status_code == 200
        assert "上傳音訊" in resp.text

    def test_upload_page_defaults_to_files_tab(self, client):
        resp = client.get("/upload")
        assert resp.status_code == 200
        assert "tab: 'files'" in resp.text

    def test_upload_page_selects_tab_from_mode_query(self, client):
        """Dashboard quick-action /upload?mode=folder|url must land the user
        on the matching tab (plan §B Finding F2)."""
        for mode in ("folder", "url"):
            resp = client.get(f"/upload?mode={mode}")
            assert resp.status_code == 200
            assert f"tab: '{mode}'" in resp.text

    def test_upload_page_falls_back_to_files_for_unknown_mode(self, client):
        """An unrecognised mode is a UX hint, not a hard error — fall back
        to the files tab rather than 4xx."""
        resp = client.get("/upload?mode=bogus")
        assert resp.status_code == 200
        assert "tab: 'files'" in resp.text

    def test_upload_page_renders_invalid_content_error(self, client):
        """The invalid_content redirect must render its message, not an empty
        alert (the template previously had no branch for this error code)."""
        resp = client.get("/upload?error=invalid_content&name=evil.mp3")
        assert resp.status_code == 200
        assert "不是有效的音訊或影片" in resp.text
        assert "evil.mp3" in resp.text


class TestJobsRoutes:
    def test_jobs_page_empty(self, client):
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert "任務列表" in resp.text

    def test_jobs_page_has_idempotent_store_and_reapplying_search(self, client):
        # The shared interactions partial must register the bulk store even on
        # a boosted nav, and the search must re-apply after list swaps. These
        # fixes live in the shared partial, so /jobs benefits too.
        resp = client.get("/jobs")
        assert "if (window.Alpine) registerJobSelectionStore()" in resp.text
        assert "filterJobs(query)" in resp.text
        assert "htmx:after-swap" in resp.text

    def test_jobs_bulk_confirm_uses_v2_modal_not_native_confirm(self, client):
        # Bulk retry/delete should open the shared confirm modal, matching the
        # per-row/batch actions, rather than a native window.confirm.
        resp = client.get("/jobs")
        assert "window.confirm(" not in resp.text  # no native confirm *call*
        assert "onConfirm: () => this._runBulk(action)" in resp.text

    def test_jobs_page_htmx_request_does_not_consume_flash(self, client):
        # Queue a flash via an upload, then fetch /jobs as an htmx request:
        # partial/boosted fetches must not pop a pending flash.
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            client.post(
                "/upload",
                data={"language": "zh", "model_name": "large-v3", "num_speakers": "0"},
                files=[("files", ("test.mp3", b"fake audio data", "audio/mpeg"))],
                follow_redirects=False,
            )
        htmx_resp = client.get("/jobs", headers={"HX-Request": "true"})
        assert 'id="flash-data"' not in htmx_resp.text
        # The flash survives and shows on the next genuine full-page load.
        full_resp = client.get("/jobs")
        assert 'id="flash-data"' in full_resp.text

    def test_jobs_list_fragment(self, client):
        resp = client.get("/jobs/list")
        assert resp.status_code == 200
        assert "job-list-wrapper" in resp.text

    def test_jobs_all_chip_shows_unfiltered_total_when_filtered(self, client, db, filestore):
        """Finding F5: opening /jobs?status=failed must still show the true
        total on the 全部 chip, not the failed-only count."""
        _create_completed_job(db, filestore)
        _create_failed_job(db)

        resp = client.get("/jobs?status=failed")

        assert resp.status_code == 200
        # The 全部 chip badge must reflect both jobs, not just the 1 failed.
        assert '<span class="badge badge-sm">2</span>' in resp.text

    def test_jobs_list_fragment_emits_stable_id_for_single_job(self, client, db, filestore):
        """Without a stable wrapper id Idiomorph falls back to positional
        matching, so a new job inserted at the top of the list can morph an
        old wrapper into a different job and drag preserved state (collapse
        checkbox, Alpine dropdown) onto the wrong entity.
        """
        job = _create_completed_job(db, filestore)
        resp = client.get("/jobs/list")
        assert resp.status_code == 200
        assert f'id="job-{job.id}"' in resp.text

    def test_jobs_page_shows_re_transcribe_for_completed_job(self, client, db, filestore):
        _create_completed_job(db, filestore)
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert "open-retranscribe" in resp.text
        assert "重新轉換" in resp.text

    def test_jobs_page_shows_version_badge_for_re_transcribe_version(self, client, db, filestore):
        root = _create_completed_job(db, filestore)
        version = Job(
            filename="meeting.mp3",
            status=JobStatus.COMPLETED,
            language="en",
            source_job_id=root.id,
        )
        filestore.save_result(version.id, TranscriptResult(segments=[], language="en", duration=1.0))
        version.result_path = str(filestore.get_output_dir(version.id) / "result.json")
        db.insert_job(version)

        resp = client.get("/jobs")

        assert resp.status_code == 200
        assert "重新轉換版本" in resp.text

    def test_jobs_list_hides_download_media_when_reclaimed(self, client, db, filestore):
        """The inline export dropdown in _job_card.html must also gate
        Download Media on media availability — same regression as the
        viewer template, but on the dashboard / jobs list surface."""
        result = TranscriptResult(segments=[], language="zh", duration=0.0)
        job = Job(
            filename="https://www.youtube.com/watch?v=jkl",
            source_url="https://www.youtube.com/watch?v=jkl",
            status=JobStatus.COMPLETED,
        )
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)
        # No media file on disk — simulates retention reclaim.

        resp = client.get("/jobs/list")
        assert resp.status_code == 200
        # Dropdown should not render the Download Media item for this job.
        assert f"/viewer/{job.id}/media" not in resp.text

    def test_jobs_list_fragment_emits_stable_ids_for_batch(self, client, db):
        """Same regression as above but for batch wrappers and the inner
        per-job rows. Both must carry stable ids so Idiomorph keys them
        through reorders.
        """
        batch_id = "deadbeef" * 4  # 32-char hex, valid uuid hex shape
        job_a = Job(filename="a.mp3", status=JobStatus.PROCESSING, batch_id=batch_id)
        job_b = Job(filename="b.mp3", status=JobStatus.PROCESSING, batch_id=batch_id)
        db.insert_job(job_a)
        db.insert_job(job_b)

        resp = client.get("/jobs/list")
        assert resp.status_code == 200
        assert f'id="job-group-{batch_id}"' in resp.text
        assert f'id="job-{job_a.id}"' in resp.text
        assert f'id="job-{job_b.id}"' in resp.text

    def test_batch_collapse_uses_store_not_open_attribute(self, client, db):
        """Regression: the batch collapse must be driven by the Alpine
        batchCollapse store, not the server-rendered [open] attribute that
        fought the user's checkbox toggle under polling (auto-collapse bug).
        """
        batch_id = "abcdef12" * 4
        db.insert_job(Job(filename="a.mp3", status=JobStatus.PROCESSING, batch_id=batch_id))
        db.insert_job(Job(filename="b.mp3", status=JobStatus.PROCESSING, batch_id=batch_id))

        resp = client.get("/jobs/list")
        assert resp.status_code == 200
        group = resp.text.split(f'id="job-group-{batch_id}"', 1)[1].split("collapse-content", 1)[0]
        # The div's opening tag must not carry the server-driven [open] attr,
        # and the checkbox must not be a server-rendered `checked` boolean.
        div_open_tag = group.split(">", 1)[0]
        assert " open" not in div_open_tag
        input_tag = group.split("<input", 1)[1].split(">", 1)[0]
        assert " checked" not in input_tag  # only :checked (bound), not a boolean
        # Expansion is now store-driven with a server-provided default.
        assert "batchCollapse" in group
        assert ":checked=" in group
        assert "isOpen(groupKey" in group

    def test_jobs_page_with_job(self, client, db, filestore):
        _create_completed_job(db, filestore)
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert "test.mp3" in resp.text

    def test_jobs_page_invalid_status_ignored(self, client):
        resp = client.get("/jobs?status=' %2B alert(1) %2B '")
        assert resp.status_code == 200
        assert "alert(1)" not in resp.text

    def test_jobs_list_filter(self, client, db, filestore):
        _create_completed_job(db, filestore)
        _create_failed_job(db)
        resp = client.get("/jobs/list?status=completed")
        assert resp.status_code == 200
        assert "test.mp3" in resp.text
        assert "fail.mp3" not in resp.text

    def test_delete_job(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.delete(f"/jobs/{job.id}")
        assert resp.status_code == 204
        assert db.get_job(job.id) is None

    def test_delete_nonexistent_job(self, client):
        resp = client.delete("/jobs/00000000000000000000000000000000")
        assert resp.status_code == 404

    def test_delete_invalid_id_returns_400(self, client):
        resp = client.delete("/jobs/not-a-valid-hex-id")
        assert resp.status_code == 400

    def test_negative_page_clamped_to_zero(self, client):
        resp = client.get("/jobs/list?page=-5")
        assert resp.status_code == 200

    def test_delete_active_job_returns_409(self, client, db):
        job = Job(filename="active.mp3", status=JobStatus.PROCESSING, language="zh")
        db.insert_job(job)
        resp = client.delete(f"/jobs/{job.id}")
        assert resp.status_code == 409
        assert db.get_job(job.id) is not None

    def test_retry_file_job_passes_probed_duration_to_dispatcher(self, client, db):
        job = _create_failed_job(db)
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=3600.0),
        ):
            resp = client.post(f"/jobs/{job.id}/retry")
        assert resp.status_code == 204
        mock_enqueue.assert_called_once()
        retried_job = mock_enqueue.call_args[0][0]
        assert retried_job.id == job.id
        assert retried_job.duration == 3600.0

    def test_retry_file_job_with_unknown_duration_passes_none(self, client, db):
        job = _create_failed_job(db)
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=None),
        ):
            resp = client.post(f"/jobs/{job.id}/retry")
        assert resp.status_code == 204
        assert mock_enqueue.call_args[0][0].duration is None

    def test_retry_url_job_passes_none_duration(self, client, db):
        job = _create_failed_job(db, source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        with patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue:
            resp = client.post(f"/jobs/{job.id}/retry")
        assert resp.status_code == 204
        mock_enqueue.assert_called_once()
        # URL jobs cannot be re-probed locally; dispatcher sees duration=None.
        assert mock_enqueue.call_args[0][0].duration is None

    def test_retry_probe_exception_passes_none_duration(self, client, db):
        job = _create_failed_job(db)
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch(
                "whisper_ui.web.routes.jobs.get_audio_duration_seconds",
                side_effect=OSError("file vanished"),
            ),
        ):
            resp = client.post(f"/jobs/{job.id}/retry")
        assert resp.status_code == 204
        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args[0][0].duration is None

    def test_retry_enqueue_failure_uses_generic_error(self, client, db):
        job = _create_failed_job(db)
        with (
            patch(
                "whisper_ui.web.routes.jobs.enqueue_pipeline",
                side_effect=Exception("Redis internal: secret/key/path leak"),
            ),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=60.0),
        ):
            client.post(f"/jobs/{job.id}/retry")
        refreshed = db.get_job(job.id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.FAILED
        assert "Redis internal" not in (refreshed.error or "")
        assert "secret" not in (refreshed.error or "")

    def test_delete_job_emits_audit_log(self, client, db, filestore, caplog):
        import logging as _logging

        job = _create_completed_job(db, filestore)
        with caplog.at_level(_logging.INFO, logger="whisper_ui.web.routes.jobs"):
            client.delete(f"/jobs/{job.id}")

        msg = next(r.getMessage() for r in caplog.records if "job deleted" in r.getMessage())
        assert job.id in msg
        assert "status_at_delete=completed" in msg

    def test_retry_job_emits_audit_log_with_previous_error(self, client, db, caplog):
        import logging as _logging

        job = _create_failed_job(db)
        # _create_failed_job sets error="Test failure" — verify it round-trips into the log.
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline"),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=60.0),
            caplog.at_level(_logging.INFO, logger="whisper_ui.web.routes.jobs"),
        ):
            client.post(f"/jobs/{job.id}/retry")

        msg = next(r.getMessage() for r in caplog.records if "job retried" in r.getMessage())
        assert job.id in msg
        assert "previous_error=" in msg

    def test_re_transcribe_creates_new_version_and_preserves_original(self, client, db, filestore, app):
        # hf_token present so the diarization flag passes through un-clamped.
        app.state.settings = app.state.settings.model_copy(update={"hf_token": "hf-test-not-real"})
        src = _completed_upload_job_with_audio(db, filestore)
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=42.0),
        ):
            resp = client.post(
                f"/jobs/{src.id}/re-transcribe",
                data={"language": "en", "model_name": "medium", "enable_diarization": "true"},
            )

        assert resp.status_code == 204
        assert resp.headers.get("hx-trigger") == "refreshJobList"
        new_job = mock_enqueue.call_args[0][0]
        # A distinct job carrying the new parameters, linked to the source.
        assert new_job.id != src.id
        assert new_job.language == "en"
        assert new_job.model_name == "medium"
        assert new_job.enable_diarization is True
        assert new_job.source_job_id == src.id
        assert new_job.status == JobStatus.QUEUED
        # The original transcript is untouched.
        original = db.get_job(src.id)
        assert original.status == JobStatus.COMPLETED
        assert original.result_path == src.result_path

    def test_re_transcribe_clamps_diarization_when_hf_token_absent(self, client, db, filestore, app):
        # Force no hf_token so a posted diarization flag must be clamped to
        # False (honest persisted flag, no no-op sub-job).
        app.state.settings = app.state.settings.model_copy(update={"hf_token": ""})
        src = _completed_upload_job_with_audio(db, filestore)
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=42.0),
        ):
            resp = client.post(
                f"/jobs/{src.id}/re-transcribe",
                data={"language": "zh", "model_name": "large-v3", "enable_diarization": "true"},
            )

        assert resp.status_code == 204
        assert mock_enqueue.call_args[0][0].enable_diarization is False

    def test_re_transcribe_copies_audio_to_new_job_dir(self, client, db, filestore):
        src = _completed_upload_job_with_audio(db, filestore, filename="meeting.mp3")
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue,
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=42.0),
        ):
            client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})

        new_job = mock_enqueue.call_args[0][0]
        # New job has its own independent copy; the source copy survives.
        assert filestore.get_upload_path(new_job.id, "meeting.mp3").exists()
        assert filestore.get_upload_path(src.id, "meeting.mp3").exists()

    def test_re_transcribe_source_audio_gone_returns_409_without_new_job(self, client, db, filestore):
        # Completed job whose upload file was reclaimed by retention (never created here).
        result = TranscriptResult(segments=[], language="zh", duration=60.0)
        src = Job(filename="gone.mp3", status=JobStatus.COMPLETED, language="zh")
        src.result_path = str(filestore.save_result(src.id, result))
        db.insert_job(src)
        before = db.count_jobs()

        with patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue:
            resp = client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})

        assert resp.status_code == 409
        mock_enqueue.assert_not_called()
        assert db.count_jobs() == before

    def test_re_transcribe_url_job_skips_copy_and_reenqueues(self, client, db, filestore):
        result = TranscriptResult(segments=[], language="zh", duration=60.0)
        src = Job(
            filename="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            status=JobStatus.COMPLETED,
            language="zh",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        src.result_path = str(filestore.save_result(src.id, result))
        db.insert_job(src)

        with patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue:
            resp = client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "en", "model_name": "medium"})

        assert resp.status_code == 204
        new_job = mock_enqueue.call_args[0][0]
        # URL jobs re-download (no media to copy); source_url carries over.
        assert new_job.source_url == src.source_url
        assert new_job.source_job_id == src.id

    def test_re_transcribe_rejects_non_completed_job(self, client, db, filestore):
        src = _create_failed_job(db)
        resp = client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})
        assert resp.status_code == 404

    def test_re_transcribe_invalid_model_returns_400(self, client, db, filestore):
        src = _completed_upload_job_with_audio(db, filestore)
        resp = client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "not-a-model"})
        assert resp.status_code == 400

    def test_re_transcribe_enqueue_failure_marks_new_job_failed(self, client, db, filestore):
        src = _completed_upload_job_with_audio(db, filestore)
        with (
            patch(
                "whisper_ui.web.routes.jobs.enqueue_pipeline",
                side_effect=Exception("Redis internal: secret/key leak"),
            ),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=42.0),
        ):
            resp = client.post(f"/jobs/{src.id}/re-transcribe", data={"language": "zh", "model_name": "large-v3"})

        assert resp.status_code == 204
        # The original is untouched; only the new version flips to FAILED with a
        # generic, leak-free message.
        assert db.get_job(src.id).status == JobStatus.COMPLETED
        new_versions = db.list_jobs_by_source(src.id)
        assert len(new_versions) == 1
        assert new_versions[0].status == JobStatus.FAILED
        assert "secret" not in (new_versions[0].error or "")

    def test_bulk_retry_requeues_failed_jobs_and_emits_trigger(self, client, db):
        """Two failed jobs → bulk retry → both transition to QUEUED and the
        response carries HX-Trigger: refreshJobList (plan §5.6)."""
        failed_a = _create_failed_job(db)
        failed_b = _create_failed_job(db)

        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline"),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=None),
        ):
            resp = client.post("/jobs/bulk/retry", data={"job_ids": f"{failed_a.id},{failed_b.id}"})

        assert resp.status_code == 204
        assert resp.headers.get("hx-trigger") == "refreshJobList"
        # After-settle event carries the success summary so the client can
        # surface a single toast for the operation.
        after_settle = resp.headers.get("hx-trigger-after-settle", "")
        assert "bulkComplete" in after_settle
        for original in (failed_a, failed_b):
            refreshed = db.get_job(original.id)
            assert refreshed.status == JobStatus.QUEUED

    def test_bulk_delete_keeps_active_jobs_and_reports_partial_failure(self, client, db, filestore):
        """COMPLETED jobs delete; PROCESSING jobs are reported failed so the
        active worker is not interrupted (status enum is the gate). Partial
        failure surfaces as bulkPartial in HX-Trigger-After-Settle."""
        completed = _create_completed_job(db, filestore)
        active = Job(filename="active.mp3", status=JobStatus.PROCESSING, language="zh")
        db.insert_job(active)

        resp = client.post(
            "/jobs/bulk/delete",
            data={"job_ids": f"{completed.id},{active.id}"},
        )

        assert resp.status_code == 204
        assert resp.headers.get("hx-trigger") == "refreshJobList"
        after_settle = resp.headers.get("hx-trigger-after-settle", "")
        assert "bulkPartial" in after_settle
        assert db.get_job(completed.id) is None
        assert db.get_job(active.id) is not None

    def test_bulk_action_rejects_unknown_action(self, client, db):
        failed = _create_failed_job(db)

        resp = client.post("/jobs/bulk/banana", data={"job_ids": failed.id})

        assert resp.status_code == 400

    def test_bulk_action_isolates_jobs_owned_by_other_users(self, client, db, filestore, test_admin):
        """Bulk routes must respect owner_filter — passing another user's
        job_id should be reported as failed, not silently succeed."""
        their_job = Job(filename="theirs.mp3", status=JobStatus.FAILED, owner_id=test_admin.id + 999)
        db.insert_job(their_job)

        resp = client.post("/jobs/bulk/retry", data={"job_ids": their_job.id})

        assert resp.status_code == 204
        # No partial-complete trigger because the job was not owned by the
        # current user → the failed counter went up, succeeded stayed at 0.
        after_settle = resp.headers.get("hx-trigger-after-settle", "")
        assert "bulkPartial" in after_settle
        assert "bulkComplete" not in after_settle
        # The stranger's job is unchanged.
        refreshed = db.get_job(their_job.id)
        assert refreshed.status == JobStatus.FAILED

    def test_jobs_page_renders_per_status_chip_counts(self, client, db, filestore):
        """v2 sticky filter shows each status's count, not just total."""
        _create_completed_job(db, filestore)
        _create_failed_job(db)

        resp = client.get("/jobs")

        assert resp.status_code == 200
        assert "已完成" in resp.text
        assert "失敗" in resp.text
        assert "JOBS_SEARCH_PLACEHOLDER" not in resp.text  # label is rendered, not the constant name

    def test_jobs_list_fragment_includes_data_job_search_with_url(self, client, db):
        """URL jobs must be findable via the search box — the new
        data-job-search attribute combines filename + source URL."""
        url = "https://www.youtube.com/watch?v=abc123"
        failed = _create_failed_job(db, source_url=url)

        resp = client.get("/jobs/list")

        assert resp.status_code == 200
        assert f'data-job-search="{failed.filename} {url}"' in resp.text

    def test_jobs_card_renders_bulk_select_checkbox_for_completed_jobs(self, client, db, filestore):
        """Bulk-eligible rows (completed / failed) carry the selection
        checkbox bound to $store.jobSelection."""
        completed = _create_completed_job(db, filestore)

        resp = client.get("/jobs/list")

        assert resp.status_code == 200
        assert f"$store.jobSelection.has('{completed.id}')" in resp.text
        assert "checkbox checkbox-sm" in resp.text

    def test_jobs_card_omits_bulk_checkbox_for_active_jobs(self, client, db):
        """Active jobs (queued / processing) cannot be retried or deleted
        in bulk, so they must NOT render a selection checkbox."""
        active = Job(filename="busy.mp3", status=JobStatus.PROCESSING)
        db.insert_job(active)

        resp = client.get("/jobs/list")

        assert resp.status_code == 200
        assert f"$store.jobSelection.has('{active.id}')" not in resp.text

    def test_jobs_card_checkbox_carries_status_for_bulk_gating(self, client, db, filestore):
        """The selection toggle must pass the job status so the bulk bar can
        gate export (needs completed) / retry (needs failed). Finding F3."""
        completed = _create_completed_job(db, filestore)
        failed = _create_failed_job(db)

        resp = client.get("/jobs/list")

        assert resp.status_code == 200
        assert f"toggle('{completed.id}', 'completed')" in resp.text
        assert f"toggle('{failed.id}', 'failed')" in resp.text

    def test_delete_job_returns_500_and_preserves_db_row_when_filestore_fails(self, client, db, filestore, caplog):
        """PR #53 review F2: a filesystem failure must NOT lead to a
        DB-deleted-but-files-still-on-disk inconsistency. The route must
        return 5xx, leave the row in place, and not emit the success
        audit log.
        """
        import logging as _logging

        job = _create_completed_job(db, filestore)

        with (
            patch.object(filestore, "delete_job_files", side_effect=PermissionError("denied")),
            caplog.at_level(_logging.ERROR, logger="whisper_ui.web.routes.jobs"),
        ):
            resp = client.delete(f"/jobs/{job.id}")

        assert resp.status_code == 500
        # DB row preserved so the user can retry.
        assert db.get_job(job.id) is not None
        # Audit log records the abort, not a misleading "job deleted".
        assert any("job delete aborted" in r.getMessage() for r in caplog.records)
        assert not any("job deleted:" in r.getMessage() for r in caplog.records)


class TestViewerRoutes:
    def test_viewer_redirects_to_jobs(self, client):
        resp = client.get("/viewer", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/jobs"

    def test_viewer_with_job(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert "test.mp3" in resp.text

    def test_viewer_disables_search_for_huge_transcript(self, client, db, filestore):
        from whisper_ui.core.constants import VIEWER_SEARCH_SEGMENT_LIMIT

        big_segments = [
            Segment(start=float(i), end=float(i + 1), text=f"seg{i}") for i in range(VIEWER_SEARCH_SEGMENT_LIMIT + 1)
        ]
        result = TranscriptResult(segments=big_segments, language="zh", duration=float(len(big_segments)))
        job = Job(filename="huge.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert "已停用即時搜尋" in resp.text
        assert 'placeholder="輸入關鍵字篩選' not in resp.text
        # Search is disabled for performance, so the per-segment data-raw copy
        # and x-html highlight eval must be skipped — but the text still renders
        # server-side so it stays visible.
        assert "data-raw=" not in resp.text
        assert ">seg0</span>" in resp.text

    def test_viewer_keeps_search_under_limit(self, client, db, filestore):
        from whisper_ui.core.constants import VIEWER_SEARCH_SEGMENT_LIMIT

        small_segments = [
            Segment(start=float(i), end=float(i + 1), text=f"seg{i}")
            for i in range(min(10, VIEWER_SEARCH_SEGMENT_LIMIT))
        ]
        result = TranscriptResult(segments=small_segments, language="zh", duration=10.0)
        job = Job(filename="small.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert 'placeholder="輸入關鍵字篩選' in resp.text
        assert "已停用即時搜尋" not in resp.text

    def test_viewer_not_found(self, client):
        resp = client.get("/viewer/00000000000000000000000000000000")
        assert resp.status_code == 200
        assert "找不到" in resp.text

    def test_viewer_invalid_id_returns_400(self, client):
        resp = client.get("/viewer/nonexistent")
        assert resp.status_code == 400

    def test_viewer_hides_download_media_when_source_media_reclaimed(self, client, db, filestore):
        """URL job whose download was reclaimed by retention must not render
        the Download Media button, in either the with-segments or
        no-segments branch of the viewer template."""
        # With segments
        result = TranscriptResult(
            segments=[Segment(start=0.0, end=1.0, text="hi")],
            language="zh",
            duration=1.0,
        )
        job = Job(
            filename="https://www.youtube.com/watch?v=abc",
            source_url="https://www.youtube.com/watch?v=abc",
            status=JobStatus.COMPLETED,
        )
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)
        # No media file written to upload dir — simulates retention reclaim.

        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert "下載影片" not in resp.text

    def test_viewer_renders_segment_text_server_side(self, client, db, filestore):
        """Regression: transcript text must render as server-side element content,
        not only inside a client-side ``x-html`` attribute.

        v2.3.0 rendered text via ``x-html="whisperHighlight({{ seg.text|tojson }},
        ...)"``. ``tojson`` emits a double-quoted JSON string, which closed the
        double-quoted attribute early and left the expression malformed, so the
        text never rendered whenever Alpine evaluated it. The text must appear as
        visible element body, independent of JS."""
        marker = "逐字稿可見內容XYZ"
        result = TranscriptResult(
            segments=[Segment(start=0.0, end=1.0, text=marker, speaker="SPEAKER_00")],
            language="zh",
            duration=1.0,
        )
        job = Job(filename="render.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")

        assert resp.status_code == 200
        assert f">{marker}</span>" in resp.text
        # Search is enabled under the limit, so the highlight path is wired up.
        assert "data-raw=" in resp.text

    def test_viewer_segment_copy_button_is_keyboard_reachable(self, client, db, filestore):
        """Regression for WCAG 2.1.1 + 1.4.13 (plan §4 P0): the per-segment
        copy button must be visible without hover and expose an aria-label."""
        segments = [Segment(start=0.0, end=1.0, text="hello")]
        result = TranscriptResult(segments=segments, language="zh", duration=1.0)
        job = Job(filename="copy.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")

        assert resp.status_code == 200
        assert "opacity-0 group-hover:opacity-100" not in resp.text
        assert 'aria-label="複製此段"' in resp.text

    def test_viewer_renders_speaker_glyph_when_speaker_assigned(self, client, db, filestore):
        """Non-color cue for speakers (WCAG 1.4.1): segments with a speaker
        should render one of the eight glyphs alongside the speaker label."""
        segments = [Segment(start=0.0, end=1.0, text="hello", speaker="SPEAKER_00")]
        result = TranscriptResult(segments=segments, language="zh", duration=1.0)
        job = Job(filename="diar.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")

        assert resp.status_code == 200
        assert "[SPEAKER_00]" in resp.text
        assert any(glyph in resp.text for glyph in "●▲■◆★✦◉♦")

    def test_viewer_includes_keyboard_shortcut_hint(self, client, db, filestore):
        """Viewer should expose its keyboard shortcuts so they are
        discoverable (Nielsen #7 + 6)."""
        segments = [Segment(start=0.0, end=1.0, text="hello")]
        result = TranscriptResult(segments=segments, language="zh", duration=1.0)
        job = Job(filename="hint.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")

        assert resp.status_code == 200
        assert "鍵盤捷徑" in resp.text
        assert "聚焦搜尋" in resp.text

    def test_viewer_hides_download_media_in_no_segments_branch(self, client, db, filestore):
        """No-segments fallback toolbar must also gate Download Media on
        media_available (regression for PR #41 followup F1)."""
        result = TranscriptResult(segments=[], language="zh", duration=0.0)
        job = Job(
            filename="https://www.youtube.com/watch?v=def",
            source_url="https://www.youtube.com/watch?v=def",
            status=JobStatus.COMPLETED,
        )
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)

        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert "下載影片" not in resp.text

    def test_viewer_shows_download_media_when_source_media_present(self, client, db, filestore):
        """Positive control: if the media file is still on disk, the button
        should render so the gate is not over-eager."""
        result = TranscriptResult(
            segments=[Segment(start=0.0, end=1.0, text="hi")],
            language="zh",
            duration=1.0,
        )
        job = Job(
            filename="https://www.youtube.com/watch?v=ghi",
            source_url="https://www.youtube.com/watch?v=ghi",
            status=JobStatus.COMPLETED,
        )
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)
        # Drop a video.* file in the upload dir so get_source_media_path finds it.
        media_dir = filestore.prepare_upload_path(job.id, "_").parent
        (media_dir / "video.mp4").write_bytes(b"fake mp4")

        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert "下載影片" in resp.text

    def test_export_srt(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}/export/srt")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"

    def test_export_json(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}/export/json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_export_nonexistent(self, client):
        resp = client.get("/viewer/00000000000000000000000000000000/export/srt")
        assert resp.status_code == 404

    def test_export_invalid_id_returns_400(self, client):
        resp = client.get("/viewer/nonexistent/export/srt")
        assert resp.status_code == 400

    def test_export_invalid_format_returns_400(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}/export/invalid_format")
        assert resp.status_code == 400
        # The fixed error message must not echo the caller-supplied format.
        assert "invalid_format" not in resp.text

    def test_export_non_ascii_filename(self, client, db, filestore):
        result = TranscriptResult(segments=[], language="zh", duration=60.0)
        job = Job(filename="\u6e2c\u8a66\u97f3\u6a94.mp3", status=JobStatus.COMPLETED, language="zh")
        result_path = filestore.save_result(job.id, result)
        job.result_path = str(result_path)
        db.insert_job(job)
        resp = client.get(f"/viewer/{job.id}/export/srt")
        assert resp.status_code == 200
        cd = resp.headers["content-disposition"]
        assert "filename*=UTF-8''" in cd
        assert "%E6%B8%AC%E8%A9%A6%E9%9F%B3%E6%AA%94" in cd


class TestUploadPost:
    def _upload(self, client, files=None, **form_data):
        data = {
            "language": form_data.get("language", "zh"),
            "model_name": form_data.get("model_name", "large-v3"),
            "num_speakers": form_data.get("num_speakers", "0"),
        }
        file_list = files or [("files", ("test.mp3", b"fake audio data", "audio/mpeg"))]
        return client.post("/upload", data=data, files=file_list, follow_redirects=False)

    def test_upload_post_redirects_to_clean_jobs_and_flashes(self, client, app):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(client)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        # The success toast rides in the session flash, rendered on the next
        # full-page load rather than via query params.
        page = client.get("/jobs")
        assert flash_messages(page.text) == [ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "1")]

    def test_upload_flash_is_consumed_after_one_render(self, client, app):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            self._upload(client)
        first = client.get("/jobs")
        assert 'id="flash-data"' in first.text
        # Flash is one-shot: a reload no longer re-shows the toast.
        second = client.get("/jobs")
        assert 'id="flash-data"' not in second.text

    def test_upload_rejects_pdf_disguised_as_audio(self, client):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(
                client,
                files=[("files", ("evil.mp3", b"%PDF-1.7 not really audio", "audio/mpeg"))],
            )
        assert resp.status_code == 303
        assert "error=invalid_content" in resp.headers["location"]

    def test_upload_rejects_html_disguised_as_audio(self, client):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(
                client,
                files=[("files", ("evil.mp4", b"<!DOCTYPE html><html>", "video/mp4"))],
            )
        assert resp.status_code == 303
        assert "error=invalid_content" in resp.headers["location"]

    def test_upload_skips_invalid_file_but_submits_valid_ones(self, client, db, app):
        """A non-media file in a batch is skipped, not fatal: the valid files
        are still queued and the user is told how many were skipped (rather
        than the whole batch failing and prompting a duplicate re-upload)."""
        files = [
            ("files", ("good.mp3", b"ID3 fake audio payload", "audio/mpeg")),
            ("files", ("evil.mp3", b"%PDF-1.7 not really audio", "audio/mpeg")),
        ]
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as mock_enqueue:
            resp = self._upload(client, files=files)

        # Valid file went through; redirect is the success path to /jobs.
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        assert mock_enqueue.call_count == 1
        jobs = db.list_jobs()
        assert len(jobs) == 1 and jobs[0].filename == "good.mp3"
        # The toast reports the skipped file.
        page = client.get("/jobs")
        expected = ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "1") + ui_labels.TOAST_FILE_SKIPPED.replace(
            "{count}", "1"
        )
        assert flash_messages(page.text) == [expected]

    def test_upload_clamps_diarization_and_llm_when_unavailable(self, client, db, app):
        # Force neither hf_token nor ollama_base_url, so both opt-in flags must
        # be clamped to False at persistence even when posted true.
        app.state.settings = app.state.settings.model_copy(update={"hf_token": "", "ollama_base_url": ""})
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as mock_enqueue:
            resp = client.post(
                "/upload",
                data={
                    "language": "zh",
                    "model_name": "large-v3",
                    "num_speakers": "0",
                    "enable_diarization": "true",
                    "llm_correction_enabled": "true",
                },
                files=[("files", ("test.mp3", b"fake audio data", "audio/mpeg"))],
                follow_redirects=False,
            )
        assert resp.status_code == 303
        job = mock_enqueue.call_args[0][0]
        assert job.enable_diarization is False
        assert job.llm_correction_enabled is False

    def test_upload_passes_through_flags_when_services_available(self, client, db, app):
        app.state.settings = app.state.settings.model_copy(
            update={"hf_token": "hf-test-not-real", "ollama_base_url": "http://ollama:11434"}
        )
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as mock_enqueue:
            resp = client.post(
                "/upload",
                data={
                    "language": "zh",
                    "model_name": "large-v3",
                    "num_speakers": "0",
                    "enable_diarization": "true",
                    "llm_correction_enabled": "true",
                },
                files=[("files", ("test.mp3", b"fake audio data", "audio/mpeg"))],
                follow_redirects=False,
            )
        assert resp.status_code == 303
        job = mock_enqueue.call_args[0][0]
        assert job.enable_diarization is True
        assert job.llm_correction_enabled is True

    def test_upload_no_file_redirects(self, client):
        resp = client.post("/upload", data={"language": "zh"}, follow_redirects=False)
        assert resp.status_code == 303
        assert "error=no_file" in resp.headers["location"]

    def test_upload_invalid_language_redirects(self, client):
        resp = self._upload(client, language="xx_invalid")
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert "error=invalid_language" in location
        assert "value=xx_invalid" in location

    def test_upload_invalid_model_redirects(self, client):
        resp = self._upload(client, model_name="nonexistent-model")
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert "error=invalid_model" in location
        assert "value=nonexistent-model" in location

    def test_upload_file_too_large_redirects(self, client, app):
        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 10})
        files = [("files", ("big.mp3", b"x" * 20, "audio/mpeg"))]
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        assert "error=too_large" in resp.headers["location"]

    def test_upload_too_large_url_encodes_special_chars(self, client, app):
        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 5})
        files = [("files", ("evil&limit=1.mp3", b"x" * 10, "audio/mpeg"))]
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        location = resp.headers["location"]
        # '&' in filename must be percent-encoded, not split the query string
        assert "evil%26limit%3D1.mp3" in location

    def test_upload_too_large_cleans_up_partial_file(self, client, app, filestore):
        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 5})
        files = [("files", ("big.mp3", b"x" * 10, "audio/mpeg"))]
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        # Verify no partial files remain in upload dir
        upload_files = list(filestore._upload_dir.rglob("big.mp3"))
        assert upload_files == []

    def test_upload_unsupported_format_redirects(self, client):
        files = [("files", ("document.pdf", b"fake pdf", "application/pdf"))]
        resp = self._upload(client, files=files)
        assert resp.status_code == 303
        assert "error=no_files" in resp.headers["location"]

    def test_upload_logs_batch_start_and_finish(self, client, app, caplog):
        import logging as _logging

        with (
            patch("whisper_ui.web.routes.upload.enqueue_pipeline"),
            caplog.at_level(_logging.INFO, logger="whisper_ui.web.routes.upload"),
        ):
            self._upload(client)

        messages = [r.getMessage() for r in caplog.records]
        assert any("upload batch starting" in m and "files=1" in m for m in messages)
        assert any("upload job inserted" in m and "filename='test.mp3'" in m for m in messages)
        assert any("upload batch finished" in m and "submitted=1" in m for m in messages)

    def test_upload_log_reflects_clamped_flags_not_raw_request(self, client, app, caplog):
        import logging as _logging

        # Without hf_token / ollama_base_url the posted flags are clamped to
        # False; the "job inserted" log must report the clamped job.* values,
        # not the misleading raw request flags.
        app.state.settings = app.state.settings.model_copy(update={"hf_token": "", "ollama_base_url": ""})
        with (
            patch("whisper_ui.web.routes.upload.enqueue_pipeline"),
            caplog.at_level(_logging.INFO, logger="whisper_ui.web.routes.upload"),
        ):
            client.post(
                "/upload",
                data={
                    "language": "zh",
                    "model_name": "large-v3",
                    "num_speakers": "0",
                    "enable_diarization": "true",
                    "llm_correction_enabled": "true",
                },
                files=[("files", ("test.mp3", b"fake audio data", "audio/mpeg"))],
                follow_redirects=False,
            )

        inserted = next(r.getMessage() for r in caplog.records if "upload job inserted" in r.getMessage())
        assert "diarize=False" in inserted
        assert "llm=False" in inserted

    def test_upload_too_large_logs_skip(self, client, app, caplog):
        import logging as _logging

        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 5})
        files = [("files", ("big.mp3", b"x" * 10, "audio/mpeg"))]
        with (
            patch("whisper_ui.web.routes.upload.enqueue_pipeline"),
            caplog.at_level(_logging.WARNING, logger="whisper_ui.web.routes.upload"),
        ):
            self._upload(client, files=files)

        assert any("upload skipped" in r.getMessage() for r in caplog.records)

    def test_upload_partial_enqueue_failure_reports_failed_count(self, client, app, db):
        # First file succeeds, second raises — simulate a transient Redis error.
        enqueue_side_effects = [None, Exception("Redis hiccup")]
        files = [
            ("files", ("a.mp3", b"data1", "audio/mpeg")),
            ("files", ("b.mp3", b"data2", "audio/mpeg")),
        ]
        with patch(
            "whisper_ui.web.routes.upload.enqueue_pipeline",
            side_effect=enqueue_side_effects,
        ):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        page = client.get("/jobs")
        expected = ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "1") + ui_labels.TOAST_UPLOAD_FAILED.replace(
            "{count}", "1"
        )
        assert flash_messages(page.text) == [expected]
        statuses = sorted(j.status for j in db.list_jobs())
        assert statuses == sorted([JobStatus.QUEUED, JobStatus.FAILED])

    def test_upload_htmx_error_escapes_html(self, client, app):
        """Verify htmx error responses escape user input to prevent XSS."""
        files = [("files", ("test.mp3", b"fake", "audio/mpeg"))]
        resp = client.post(
            "/upload",
            data={"language": "<script>alert(1)</script>", "model_name": "large-v3"},
            files=files,
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert "<script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


class TestUploadURLPost:
    def _post_url(self, client, url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", **form_data):
        data = {
            "url": url,
            "language": form_data.get("language", "zh"),
            "model_name": form_data.get("model_name", "large-v3"),
            "num_speakers": form_data.get("num_speakers", "0"),
        }
        return client.post("/upload/url", data=data, follow_redirects=False)

    def test_upload_url_submits_job(self, client, app, db):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = self._post_url(client)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        page = client.get("/jobs")
        assert flash_messages(page.text) == [ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "1")]

    def test_upload_url_creates_job_with_source_url(self, client, app, db):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            self._post_url(client)
        jobs = db.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_upload_invalid_url_redirects(self, client):
        resp = self._post_url(client, url="https://example.com/not-youtube")
        assert resp.status_code == 303
        assert "error=all_invalid_urls" in resp.headers["location"]

    def test_upload_playlist_url_redirects(self, client):
        resp = self._post_url(client, url="https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")
        assert resp.status_code == 303
        assert "error=playlist" in resp.headers["location"]

    def test_upload_empty_url_returns_422(self, client):
        resp = self._post_url(client, url="")
        assert resp.status_code == 422

    def test_upload_url_invalid_language(self, client):
        resp = self._post_url(client, language="xx_invalid")
        assert resp.status_code == 303
        assert "error=invalid_language" in resp.headers["location"]

    def test_upload_url_htmx_returns_redirect_header(self, client, app, db):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline"):
            resp = client.post(
                "/upload/url",
                data={
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "language": "zh",
                    "model_name": "large-v3",
                    "num_speakers": "0",
                },
                headers={"HX-Request": "true"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert resp.headers.get("HX-Redirect") == "/jobs"

    def test_upload_url_passes_job_without_probed_duration(self, client, app, db):
        with patch("whisper_ui.web.routes.upload.enqueue_pipeline") as mock_enqueue:
            self._post_url(client)
        mock_enqueue.assert_called_once()
        # URL uploads cannot be probed until after download; the dispatcher
        # receives the Job with duration=None so the timeout helper picks the
        # settings.job_timeout_default internally.
        enqueued_job = mock_enqueue.call_args[0][0]
        assert enqueued_job.duration is None
        assert enqueued_job.source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_upload_url_enqueue_failure_redirects_to_jobs_with_failed_count(self, client, app, db):
        """When enqueue fails the jobs are already persisted as FAILED; the
        redirect must land on /jobs so the user can see them, matching how
        /upload (file route) handles partial/total enqueue failures.
        """
        with patch(
            "whisper_ui.web.routes.upload.enqueue_pipeline",
            side_effect=Exception("Redis connection lost"),
        ):
            resp = self._post_url(client)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/jobs"
        page = client.get("/jobs")
        assert flash_messages(page.text) == [
            ui_labels.TOAST_UPLOAD_SUCCESS.replace("{count}", "0")
            + ui_labels.TOAST_UPLOAD_FAILED.replace("{count}", "1")
        ]
        jobs = db.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.FAILED

    def test_upload_url_enqueue_failure_htmx_returns_hx_redirect_to_jobs(self, client, app, db):
        """htmx variant: same behaviour, delivered via HX-Redirect so the
        fragment swap does not swallow the FAILED-job visibility.
        """
        with patch(
            "whisper_ui.web.routes.upload.enqueue_pipeline",
            side_effect=Exception("Redis connection lost"),
        ):
            resp = client.post(
                "/upload/url",
                data={
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "language": "zh",
                    "model_name": "large-v3",
                    "num_speakers": "0",
                },
                headers={"HX-Request": "true"},
                follow_redirects=False,
            )
        assert resp.status_code == 204
        assert resp.headers.get("HX-Redirect") == "/jobs"
        jobs = db.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.FAILED


class TestBatchRoutes:
    _BATCH_ID = "a" * 32

    def _create_batch(self, db, filestore, batch_id=None):
        batch_id = batch_id or self._BATCH_ID
        jobs = []
        for i, name in enumerate(["a.mp3", "b.mp3"]):
            result = TranscriptResult(
                segments=[Segment(start=0.0, end=1.0, text=f"Hello {i}")],
                language="zh",
                duration=1.0,
            )
            job = Job(filename=name, status=JobStatus.COMPLETED, language="zh", batch_id=batch_id)
            filestore.save_result(job.id, result)
            job.result_path = "dummy"
            db.insert_job(job)
            jobs.append(job)
        return jobs

    def test_batch_download_success(self, client, db, filestore):
        self._create_batch(db, filestore)
        resp = client.get(f"/jobs/batch/{self._BATCH_ID}/download?format_name=txt")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

    def test_batch_download_not_found(self, client):
        resp = client.get(f"/jobs/batch/{'0' * 32}/download?format_name=srt")
        assert resp.status_code == 404

    def test_batch_download_invalid_format(self, client, db, filestore):
        self._create_batch(db, filestore)
        resp = client.get(f"/jobs/batch/{self._BATCH_ID}/download?format_name=invalid")
        assert resp.status_code == 400

    def test_batch_download_invalid_id_returns_400(self, client):
        resp = client.get("/jobs/batch/not-hex/download?format_name=srt")
        assert resp.status_code == 400

    def test_retry_batch(self, client, db, app):
        batch_id = "b" * 32
        job = Job(filename="fail.mp3", status=JobStatus.FAILED, error="err", batch_id=batch_id)
        db.insert_job(job)
        with patch("whisper_ui.web.routes.jobs.enqueue_pipeline"):
            resp = client.post(f"/jobs/batch/{batch_id}/retry")
        assert resp.status_code == 204

    def test_retry_batch_url_job_passes_none_duration(self, client, db, app):
        batch_id = "d" * 32
        job = Job(
            filename="url.mp3",
            status=JobStatus.FAILED,
            error="err",
            batch_id=batch_id,
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        db.insert_job(job)
        with patch("whisper_ui.web.routes.jobs.enqueue_pipeline") as mock_enqueue:
            resp = client.post(f"/jobs/batch/{batch_id}/retry")
        assert resp.status_code == 204
        mock_enqueue.assert_called_once()
        assert mock_enqueue.call_args[0][0].duration is None

    def test_delete_batch(self, client, db, filestore):
        batch_id = "c" * 32
        jobs = self._create_batch(db, filestore, batch_id=batch_id)
        resp = client.delete(f"/jobs/batch/{batch_id}")
        assert resp.status_code == 204
        for job in jobs:
            assert db.get_job(job.id) is None

    def test_retry_batch_emits_summary_log(self, client, db, caplog):
        import logging as _logging

        batch_id = "e" * 32
        for i in range(3):
            db.insert_job(
                Job(filename=f"f{i}.mp3", status=JobStatus.FAILED, error="err", batch_id=batch_id),
            )
        with (
            patch("whisper_ui.web.routes.jobs.enqueue_pipeline"),
            patch("whisper_ui.web.routes.jobs.get_audio_duration_seconds", return_value=60.0),
            caplog.at_level(_logging.INFO, logger="whisper_ui.web.routes.jobs"),
        ):
            client.post(f"/jobs/batch/{batch_id}/retry")

        summary = next(r.getMessage() for r in caplog.records if "batch retry finished" in r.getMessage())
        assert "retried=3" in summary
        assert "total=3" in summary

    def test_delete_batch_emits_summary_log(self, client, db, filestore, caplog):
        import logging as _logging

        batch_id = "f" * 32
        self._create_batch(db, filestore, batch_id=batch_id)
        with caplog.at_level(_logging.INFO, logger="whisper_ui.web.routes.jobs"):
            client.delete(f"/jobs/batch/{batch_id}")

        summary = next(r.getMessage() for r in caplog.records if "batch deleted" in r.getMessage())
        assert "deleted=" in summary
        assert batch_id in summary

    def test_delete_batch_keeps_db_rows_for_jobs_whose_filestore_delete_fails(self, client, db, filestore, caplog):
        """PR #53 review F2: per-job atomicity in batch delete. If one
        job's filesystem reclaim fails, that job's DB row must stay; the
        rest of the batch still gets cleaned and the summary log reports
        deleted + failed counts honestly.
        """
        import logging as _logging

        from whisper_ui.core.models import Job, JobStatus

        batch_id = "1" * 32
        jobs = [Job(filename=f"f{i}.mp3", status=JobStatus.COMPLETED, batch_id=batch_id) for i in range(3)]
        for j in jobs:
            db.insert_job(j)

        original_delete = filestore.delete_job_files
        call_count = {"n": 0}

        def _failing_delete(job_id):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise PermissionError("denied for middle job")
            return original_delete(job_id)

        with (
            patch.object(filestore, "delete_job_files", side_effect=_failing_delete),
            caplog.at_level(_logging.INFO, logger="whisper_ui.web.routes.jobs"),
        ):
            resp = client.delete(f"/jobs/batch/{batch_id}")

        assert resp.status_code == 204
        # First and third jobs succeeded; the middle one stayed.
        assert db.get_job(jobs[0].id) is None
        assert db.get_job(jobs[1].id) is not None
        assert db.get_job(jobs[2].id) is None

        summary = next(r.getMessage() for r in caplog.records if "batch deleted" in r.getMessage())
        assert "deleted=2" in summary
        assert "failed=1" in summary
        assert "total=3" in summary
        assert any("batch delete skipped one job" in r.getMessage() for r in caplog.records)


class TestRetentionSweep:
    """End-to-end checks on _run_retention_sweep's backlog handling.

    Reclaiming an upload dir does not modify the corresponding DB row
    (the row + result.json are kept so the viewer keeps working). That
    means the same id list comes back on every sweep; the loop must
    therefore only count *successful* deletions against the batch limit,
    otherwise a backlog larger than the limit stalls forever on the
    first N already-reclaimed ids.
    """

    def _make_expired_completed_job(self, db, filestore, settings, idx: int, old_iso: str) -> str:
        from whisper_ui.core.models import Job

        job = Job(filename=f"old-{idx}.mp3", status=JobStatus.COMPLETED, language="zh")
        db.insert_job(job)
        # Seed an upload dir so delete_upload_files has something to reclaim.
        (settings.upload_dir / job.id).mkdir(parents=True)
        (settings.upload_dir / job.id / "source.mp3").write_bytes(b"x")
        # Backdate updated_at past the retention threshold.
        db._conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (old_iso, job.id))
        db._conn.commit()
        return job.id

    def test_sweep_progresses_past_batch_limit_across_runs(self, db, filestore, settings, tmp_dir):
        """Two-sweep walk: a backlog of (limit + 52) drains in one full
        batch + one partial batch instead of stalling on the first batch.
        Regression for PR #41 followup F-batch-stall."""
        from datetime import UTC, datetime, timedelta

        from whisper_ui.web.app import _run_retention_sweep

        limit = 50  # keep the test fast; algorithm is identical at any limit
        backlog = limit + 20  # one full batch + a partial batch left over
        now = datetime.now(UTC)
        old_iso = (now - timedelta(days=30)).isoformat()
        threshold_iso = (now - timedelta(days=7)).isoformat()

        job_ids = [self._make_expired_completed_job(db, filestore, settings, i, old_iso) for i in range(backlog)]

        # Close the test's fixture-owned connection so the sweep's
        # short-lived connection sees the committed rows.
        db.close()

        # Sweep 1: fills its budget with successful deletions.
        removed_1 = _run_retention_sweep(settings.database_path, filestore, threshold_iso, limit)
        assert removed_1 == limit
        remaining_after_1 = sum(1 for jid in job_ids if (settings.upload_dir / jid).exists())
        assert remaining_after_1 == backlog - limit

        # Sweep 2: must reach the leftover 20 even though the first
        # `limit` ids in the query result are now already-reclaimed.
        removed_2 = _run_retention_sweep(settings.database_path, filestore, threshold_iso, limit)
        assert removed_2 == backlog - limit
        remaining_after_2 = sum(1 for jid in job_ids if (settings.upload_dir / jid).exists())
        assert remaining_after_2 == 0

        # Sweep 3: nothing left to do.
        removed_3 = _run_retention_sweep(settings.database_path, filestore, threshold_iso, limit)
        assert removed_3 == 0

    def test_list_terminal_job_ids_orders_oldest_first(self, db):
        """Stable ORDER BY makes the sweep deterministic; assert that
        the SQL contract returns oldest-first regardless of insert order."""
        from datetime import UTC, datetime, timedelta

        from whisper_ui.core.models import Job

        now = datetime.now(UTC)
        # Insert in non-chronological order to detect any reliance on rowid.
        ts_newer = (now - timedelta(days=10)).isoformat()
        ts_older = (now - timedelta(days=40)).isoformat()
        newer = Job(filename="newer.mp3", status=JobStatus.COMPLETED, language="zh")
        older = Job(filename="older.mp3", status=JobStatus.COMPLETED, language="zh")
        db.insert_job(newer)
        db.insert_job(older)
        db._conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts_newer, newer.id))
        db._conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (ts_older, older.id))
        db._conn.commit()

        threshold_iso = (now - timedelta(days=1)).isoformat()
        ids = db.list_terminal_job_ids_older_than(threshold_iso)
        assert ids == [older.id, newer.id]


class TestContentDispositionHelper:
    def test_ascii_filename(self):
        result = make_content_disposition("report.pdf")
        assert result == "attachment; filename*=UTF-8''report.pdf"

    def test_non_ascii_filename(self):
        result = make_content_disposition("\u6e2c\u8a66.srt")
        assert "filename*=UTF-8''" in result
        assert "%E6%B8%AC%E8%A9%A6" in result

    def test_inline_disposition(self):
        result = make_content_disposition("file.txt", disposition="inline")
        assert result.startswith("inline;")


class TestFormatTime:
    def test_under_one_hour(self):
        assert _format_time(125) == "02:05"

    def test_exactly_one_hour(self):
        assert _format_time(3600) == "01:00:00"

    def test_over_one_hour(self):
        assert _format_time(3661) == "01:01:01"

    def test_zero(self):
        assert _format_time(0) == "00:00"

    def test_large_duration(self):
        assert _format_time(36000) == "10:00:00"


class TestFormatRelativeTime:
    def test_invalid_input(self):
        assert _format_relative_time("not-a-date") == "not-a-date"

    def test_recent_shows_just_now(self):
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        result = _format_relative_time(now)
        assert result == "剛剛"
