from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult
from whisper_ui.web.app import create_app
from whisper_ui.web.deps import _format_time, make_content_disposition


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


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


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
        resp = client.get("/viewer/00000000000000000000000000000000")
        assert resp.status_code == 200
        assert "找不到" in resp.text

    def test_viewer_invalid_id_returns_400(self, client):
        resp = client.get("/viewer/nonexistent")
        assert resp.status_code == 400

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

    def test_upload_post_submits_job(self, client, app):
        mock_queue = MagicMock()
        with patch("rq.Queue", return_value=mock_queue):
            # Need to patch at the import location
            resp = self._upload(client)
        assert resp.status_code == 303
        assert "/jobs?submitted=" in resp.headers["location"]

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
        with patch("rq.Queue", return_value=MagicMock()):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        assert "error=too_large" in resp.headers["location"]

    def test_upload_too_large_url_encodes_special_chars(self, client, app):
        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 5})
        files = [("files", ("evil&limit=1.mp3", b"x" * 10, "audio/mpeg"))]
        with patch("rq.Queue", return_value=MagicMock()):
            resp = self._upload(client, files=files)
        assert resp.status_code == 303
        location = resp.headers["location"]
        # '&' in filename must be percent-encoded, not split the query string
        assert "evil%26limit%3D1.mp3" in location

    def test_upload_too_large_cleans_up_partial_file(self, client, app, filestore):
        app.state.settings = app.state.settings.model_copy(update={"max_upload_size": 5})
        files = [("files", ("big.mp3", b"x" * 10, "audio/mpeg"))]
        with patch("rq.Queue", return_value=MagicMock()):
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
        mock_queue = MagicMock()
        with patch("rq.Queue", return_value=mock_queue):
            resp = self._post_url(client)
        assert resp.status_code == 303
        assert "/jobs?submitted=1" in resp.headers["location"]

    def test_upload_url_creates_job_with_source_url(self, client, app, db):
        mock_queue = MagicMock()
        with patch("rq.Queue", return_value=mock_queue):
            self._post_url(client)
        jobs = db.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_upload_invalid_url_redirects(self, client):
        resp = self._post_url(client, url="https://example.com/not-youtube")
        assert resp.status_code == 303
        assert "error=invalid_url" in resp.headers["location"]

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
        mock_queue = MagicMock()
        with patch("rq.Queue", return_value=mock_queue):
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
        assert resp.headers.get("HX-Redirect") == "/jobs?submitted=1"

    def test_upload_url_uses_2h_timeout(self, client, app, db):
        mock_queue = MagicMock()
        with patch("rq.Queue", return_value=mock_queue):
            self._post_url(client)
        mock_queue.enqueue.assert_called_once()
        call_kwargs = mock_queue.enqueue.call_args
        assert call_kwargs[1]["job_timeout"] == "2h"


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
        mock_queue = MagicMock()
        with patch("rq.Queue", return_value=mock_queue):
            resp = client.post(f"/jobs/batch/{batch_id}/retry")
        assert resp.status_code == 204

    def test_delete_batch(self, client, db, filestore):
        batch_id = "c" * 32
        jobs = self._create_batch(db, filestore, batch_id=batch_id)
        resp = client.delete(f"/jobs/batch/{batch_id}")
        assert resp.status_code == 204
        for job in jobs:
            assert db.get_job(job.id) is None


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
