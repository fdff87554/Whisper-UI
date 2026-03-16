from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from whisper_ui.core.models import Job, JobStatus, TranscriptResult
from whisper_ui.web.app import create_app


@pytest.fixture
def app(settings, db, filestore):
    application = create_app()
    application.state.settings = settings
    application.state.db = db
    application.state.filestore = filestore
    application.state.redis = MagicMock()
    # Mock redis.hgetall to return empty dict (no progress data)
    application.state.redis.hgetall.return_value = {}
    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


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


def _create_failed_job(db) -> Job:
    job = Job(filename="fail.mp3", status=JobStatus.FAILED, error="test error")
    db.insert_job(job)
    return job


class TestUploadRoutes:
    def test_upload_page(self, client):
        resp = client.get("/upload")
        assert resp.status_code == 200
        assert "上傳音訊" in resp.text

    def test_root_serves_upload_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "上傳音訊" in resp.text


class TestJobsRoutes:
    def test_jobs_page_empty(self, client):
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert "任務列表" in resp.text

    def test_jobs_page_with_submitted(self, client):
        resp = client.get("/jobs?submitted=3")
        assert resp.status_code == 200
        assert "3" in resp.text

    def test_jobs_list_fragment(self, client):
        resp = client.get("/jobs/list")
        assert resp.status_code == 200
        assert "job-list-wrapper" in resp.text

    def test_jobs_page_with_job(self, client, db, filestore):
        _create_completed_job(db, filestore)
        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert "test.mp3" in resp.text

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
        resp = client.delete("/jobs/nonexistent")
        assert resp.status_code == 404

    def test_delete_active_job_returns_409(self, client, db):
        job = Job(filename="active.mp3", status=JobStatus.PROCESSING, language="zh")
        db.insert_job(job)
        resp = client.delete(f"/jobs/{job.id}")
        assert resp.status_code == 409
        assert db.get_job(job.id) is not None


class TestViewerRoutes:
    def test_viewer_page_empty(self, client):
        resp = client.get("/viewer")
        assert resp.status_code == 200
        assert "檢視器" in resp.text

    def test_viewer_with_job(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}")
        assert resp.status_code == 200
        assert "test.mp3" in resp.text

    def test_viewer_not_found(self, client):
        resp = client.get("/viewer/nonexistent")
        assert resp.status_code == 200
        assert "找不到" in resp.text

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
        resp = client.get("/viewer/nonexistent/export/srt")
        assert resp.status_code == 404

    def test_export_invalid_format_returns_400(self, client, db, filestore):
        job = _create_completed_job(db, filestore)
        resp = client.get(f"/viewer/{job.id}/export/invalid_format")
        assert resp.status_code == 400
