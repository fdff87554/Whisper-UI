"""End-to-end golden-path test for the upload -> worker -> export flow.

Strategy: fake the model inference layer (whisperx + pyannote) but keep
every other I/O boundary real — ffmpeg, SQLite, the filestore, and a
fakeredis stand-in for Redis. This proves that the code we own
(routes, worker task, orchestrator, postprocess, exporters) wires up
correctly end-to-end without depending on multi-GB model downloads or
GPUs in CI.

Skipped when ffmpeg is not on PATH or fakeredis is not installed; mark
the test as ``integration`` so the default ``pytest`` run leaves it
alone (see pyproject.toml ``[tool.pytest.ini_options].addopts``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from whisper_ui.core.config import Settings
from whisper_ui.core.models import JobStatus
from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.transcribe import TranscribeStage
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.web.app import create_app
from whisper_ui.worker.tasks import process_transcription

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

_FFMPEG = shutil.which("ffmpeg")
_REQUIRES_FFMPEG = pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg not installed on host")
try:
    import fakeredis  # noqa: F401
except ImportError:  # pragma: no cover - explicitly handled
    _HAS_FAKEREDIS = False
else:
    _HAS_FAKEREDIS = True
_REQUIRES_FAKEREDIS = pytest.mark.skipif(not _HAS_FAKEREDIS, reason="fakeredis not installed")


def _generate_sine_wav(dest: Path, *, duration_sec: float = 1.0) -> None:
    """Write a 16 kHz mono sine WAV with ffmpeg so the test owns no binary fixtures."""
    subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration_sec}",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def fake_redis() -> Iterator[Any]:
    import fakeredis

    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server)
    yield client
    client.flushall()


@pytest.fixture
def integration_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file="/dev/null",
        redis_url="redis://localhost:6379/0",  # ignored — we inject fakeredis directly
        database_path=tmp_path / "e2e.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        device="cpu",
        compute_type="int8",
        # Pin the dynamic timeout to a small predictable value so the
        # integration assertions are stable regardless of host clock skew.
        job_timeout_default=300,
        job_timeout_floor=60,
        job_timeout_max=600,
        redis_processing_expiry=900,
        stale_job_buffer=60,
        # Keep LLM correction out of this golden path — it has its own
        # unit-test coverage and would just bring httpx into the equation.
        ollama_base_url="",
    )


@pytest.fixture
def integration_app(integration_settings: Settings, fake_redis: Any) -> Iterator[Any]:
    db = JobDatabase(integration_settings.database_path)
    filestore = FileStore(integration_settings.upload_dir, integration_settings.output_dir)
    app = create_app()
    app.state.settings = integration_settings
    app.state.db = db
    app.state.filestore = filestore
    app.state.redis = fake_redis
    yield app
    db.close()


@pytest.fixture
def integration_client(integration_app: Any) -> TestClient:
    return TestClient(integration_app, raise_server_exceptions=False)


def _patch_inference_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace whisperx-backed stages with deterministic fakes.

    Postprocess/orchestrator/preprocess all run for real so that ffmpeg,
    the SQLite job state machine, the throttled progress reporter, and
    the filestore IO are exercised exactly as in production.
    """

    def fake_transcribe(self, context: dict[str, Any], on_progress=None) -> dict[str, Any]:
        if on_progress:
            on_progress(0.0, "loading")
            on_progress(1.0, "done")
        context["transcription_result"] = {
            "language": context.get("language", "zh"),
            "segments": [
                {"start": 0.0, "end": 0.5, "text": "你好"},
                {"start": 0.5, "end": 1.0, "text": "世界"},
            ],
        }
        # Real whisperx returns a numpy array here; the only downstream consumer
        # is AlignStage, which we fake out below, so a sentinel object is fine.
        context["whisperx_audio"] = b"fake-audio"
        return context

    def fake_align(self, context: dict[str, Any], on_progress=None) -> dict[str, Any]:
        if on_progress:
            on_progress(1.0, "done")
        context["aligned_result"] = context["transcription_result"]
        return context

    monkeypatch.setattr(TranscribeStage, "execute", fake_transcribe)
    monkeypatch.setattr(AlignStage, "execute", fake_align)


@_REQUIRES_FFMPEG
@_REQUIRES_FAKEREDIS
def test_upload_to_export_golden_path(
    monkeypatch: pytest.MonkeyPatch,
    integration_client: TestClient,
    integration_app: Any,
    integration_settings: Settings,
    fake_redis: Any,
    tmp_path: Path,
) -> None:
    _patch_inference_stages(monkeypatch)

    # Step 1: ffmpeg builds a real 1-second sine WAV on disk.
    sample = tmp_path / "sample.wav"
    _generate_sine_wav(sample)
    assert sample.exists() and sample.stat().st_size > 0

    # Step 2: Drive the real upload route. fakeredis stands in for Redis;
    # we patch rq.Queue so the test does not also try to drive a worker
    # process — process_transcription is invoked synchronously below.
    mock_queue = MagicMock()
    monkeypatch.setattr("rq.Queue", MagicMock(return_value=mock_queue))

    with sample.open("rb") as fh:
        resp = integration_client.post(
            "/upload",
            data={
                "language": "zh",
                "model_name": "large-v3",
                "num_speakers": "0",
                "enable_diarization": "false",
                "convert_to_traditional": "false",
                "llm_correction_enabled": "false",
            },
            files={"files": ("sample.wav", fh, "audio/wav")},
            follow_redirects=False,
        )
    assert resp.status_code == 303, resp.text
    assert "submitted=1" in resp.headers["location"]

    db: JobDatabase = integration_app.state.db
    jobs = db.list_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == JobStatus.QUEUED
    assert mock_queue.enqueue.call_count == 1

    # Step 3: Run the real worker task in-process. This exercises real
    # ffmpeg conversion (PreprocessStage), real SQLite state transitions,
    # the throttled progress reporter, and the real filestore. The worker
    # task owns its own JobDatabase handle (closes it in finally), so the
    # patch returns a fresh handle on the same DB file rather than the
    # fixture's handle, which the test still needs afterwards.
    monkeypatch.setattr("whisper_ui.worker.tasks.Redis.from_url", lambda _url: fake_redis)
    monkeypatch.setattr(
        "whisper_ui.worker.tasks.JobDatabase",
        lambda path: JobDatabase(path),
    )
    monkeypatch.setattr(
        "whisper_ui.worker.tasks.get_settings",
        lambda: integration_settings,
    )

    result = process_transcription(job.id)
    assert "completed" in result

    # Step 4: Job should now be COMPLETED with a result file on disk.
    refreshed = db.get_job(job.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.COMPLETED, refreshed.error
    assert refreshed.result_path is not None
    assert Path(refreshed.result_path).exists()

    # Step 5: Hit the SRT export route — proves the postprocess output
    # round-trips through the filestore loader and exporter factory.
    export_resp = integration_client.get(f"/viewer/{job.id}/export/srt")
    assert export_resp.status_code == 200
    body = export_resp.text
    assert "你好" in body
    assert "世界" in body
    # SRT timestamps look like "00:00:00,000 --> 00:00:00,500"
    assert "-->" in body
