"""End-to-end golden-path test for the upload -> DAG worker -> export flow.

Strategy: fake the model inference layer (whisperx + pyannote) but keep
every other I/O boundary real — ffmpeg, SQLite, the filestore, and a
fakeredis stand-in for Redis. The dispatcher fans the job out into RQ
sub-jobs, an in-process ``SimpleWorker`` burst loop drains them, and
finalize_success marks the parent COMPLETED. This proves that the code
we own (routes, dispatcher, stage_tasks, orchestrator, postprocess,
exporters) wires up correctly end-to-end without depending on multi-GB
model downloads or GPUs in CI.

Skipped when ffmpeg is not on PATH or fakeredis is not installed; mark
the test as ``integration`` so the default ``pytest`` run leaves it
alone (see pyproject.toml ``[tool.pytest.ini_options].addopts``).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from whisper_ui.core.config import Settings
from whisper_ui.core.constants import (
    WORKER_QUEUE_CPU,
    WORKER_QUEUE_GPU,
    WORKER_QUEUE_IO,
)
from whisper_ui.core.models import JobStatus
from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.transcribe import TranscribeStage
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.web.app import create_app
from whisper_ui.worker.runtime import WorkerRuntime

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


def _install_dag_runtime(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: Any,
    settings: Settings,
    db: JobDatabase,
    filestore: FileStore,
) -> None:
    """Force every build_worker_runtime call inside the worker modules to
    return a runtime backed by the test's fakeredis/settings/db/filestore.

    The bundled reporter is generation-aware so terminal writes hit the
    real Lua scripts on fakeredis (the same path production takes).
    """
    from whisper_ui.worker.progress import RedisProgressReporter

    @contextlib.contextmanager
    def _builder(job_id, *, generation=None):
        runtime = WorkerRuntime(
            settings=settings,
            redis=fake_redis,
            reporter=RedisProgressReporter(
                fake_redis,
                job_id,
                processing_ttl=settings.redis_processing_expiry,
                generation=generation,
            ),
            db=db,
            filestore=filestore,
        )
        yield runtime

    monkeypatch.setattr("whisper_ui.worker.stage_tasks.build_worker_runtime", _builder)
    monkeypatch.setattr("whisper_ui.worker.pipeline_dispatcher.build_worker_runtime", _builder)


def _drain_queues(fake_redis: Any) -> None:
    """Run SimpleWorker burst cycles across every pipeline queue until idle.

    Dependent sub-jobs only become eligible after their predecessors finish,
    so a single burst pass is not enough for a chained DAG. Loop with a hard
    cap to guarantee termination.
    """
    from rq import Queue, SimpleWorker

    queues = {
        name: Queue(name=name, connection=fake_redis) for name in (WORKER_QUEUE_IO, WORKER_QUEUE_GPU, WORKER_QUEUE_CPU)
    }
    for _ in range(50):
        progressed = False
        for queue in queues.values():
            if queue.count == 0:
                continue
            SimpleWorker([queue], connection=fake_redis).work(burst=True, with_scheduler=False)
            progressed = True
        if not progressed:
            return


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

    # Wire the worker runtime to the test's resources so the DAG sub-jobs
    # see the same fakeredis, JobDatabase, and filestore the web app uses.
    db: JobDatabase = integration_app.state.db
    filestore: FileStore = integration_app.state.filestore
    _install_dag_runtime(monkeypatch, fake_redis, integration_settings, db, filestore)

    # Step 1: ffmpeg builds a real 1-second sine WAV on disk.
    sample = tmp_path / "sample.wav"
    _generate_sine_wav(sample)
    assert sample.exists() and sample.stat().st_size > 0

    # Step 2: Drive the real upload route. enqueue_pipeline runs against
    # fakeredis so sub-jobs land in real queues we can drain in-process.
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

    jobs = db.list_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == JobStatus.QUEUED

    # Step 3: Drain the DAG. Each sub-job runs the real stage_task body
    # against the test's runtime, exercising ffmpeg / SQLite / filestore /
    # context store / generation-gated progress writes.
    _drain_queues(fake_redis)

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
