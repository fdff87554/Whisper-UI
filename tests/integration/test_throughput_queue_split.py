"""Throughput tests for the 211 queue-split topology.

These validate, deterministically and without a GPU, the property the 211
optimization relies on: a dedicated GPU worker (``whisper:gpu``) plus a separate
io/cpu worker (``whisper:io`` + ``whisper:cpu``) lets io/cpu work proceed
independently of a busy GPU stage — whereas the single all-queues worker (the
2.7.0 topology) serializes everything behind the GPU stage.

A long GPU stage is simulated with a ``threading.Event`` gate (NOT ``sleep`` —
assertions are on stage ordering and queue depth, never wall-clock, so the test
is stable under CI load). The blocked worker runs in a background thread, so
``Worker._install_signal_handlers`` (main-thread-only ``signal.signal``) is
patched to a no-op. Every gate path is bounded so a regression fails fast
instead of hanging CI.

The heavy stage-stub harness is reused from the unit DAG parallelism test
rather than duplicated; only the GPU "busy" gate and the split-vs-combined
drivers are new here.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import fakeredis
import pytest
from rq import Queue, SimpleWorker

from tests.unit.test_pipeline_dag_parallelism import (
    _fake_runtime_factory,
    _install_runtime,
    _stub_stages,
)
from whisper_ui.core.constants import WORKER_QUEUE_CPU, WORKER_QUEUE_GPU, WORKER_QUEUE_IO
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.worker.pipeline_dispatcher import enqueue_pipeline

pytestmark = pytest.mark.integration

_GATE_TIMEOUT = 30.0  # stage backstop: never block forever if the test forgets to release
_SYNC_TIMEOUT = 10.0  # how long a test waits for a concurrent condition before failing


def _build_fake_settings() -> MagicMock:
    """Same shape as the unit harness's fake_settings (ollama disabled)."""
    settings = MagicMock()
    settings.batch_size = 16
    settings.ollama_base_url = ""
    # Mirror real Settings: with no ollama endpoint, llm_correction_available is
    # False (config.py). A bare MagicMock would otherwise return a truthy attr.
    settings.llm_correction_available = False
    settings.job_timeout_default = 3600
    settings.job_timeout_floor = 300
    settings.job_timeout_max = 14_400
    settings.job_timeout_audio_multiplier = 2.0
    settings.compute_type = "int8"
    settings.device = "cpu"
    settings.youtube_max_duration = 3600
    settings.diarize_heartbeat_interval = 30
    settings.hf_token = "fake-token-not-real"
    settings.redis_processing_expiry = 7200
    return settings


def _fake_filestore(tmp_path) -> MagicMock:
    filestore = MagicMock()
    filestore.prepare_upload_path.return_value = tmp_path / "subdir" / "in.mp3"
    filestore.save_result.return_value = tmp_path / "result.json"
    return filestore


def _make_upload_job(job_id: str, tmp_path) -> Job:
    return Job(
        id=job_id,
        filename=f"{job_id}.mp3",
        filepath=str(tmp_path / f"{job_id}.mp3"),
        status=JobStatus.QUEUED,
        duration=60.0,
        enable_diarization=True,
    )


class _GatedTranscribe:
    """Transcribe double that records its start then blocks on an Event.

    Stands in for a long-running GPU stage so the test can observe whether
    other queues make progress while the GPU worker is occupied. Bounded wait
    so a stuck gate fails the test instead of hanging CI.
    """

    name = "transcribe"

    def __init__(self, timeline: list, gate: threading.Event):
        self._timeline = timeline
        self._gate = gate

    def execute(self, context: dict, on_progress=None) -> dict:
        self._timeline.append(("transcribe", time.monotonic()))
        if not self._gate.wait(timeout=_GATE_TIMEOUT):
            raise TimeoutError("gpu gate never released")
        out = dict(context)
        out["transcription_result"] = {"segments": []}
        return out

    def cleanup(self) -> None:
        pass


def _burst_worker(fake_redis, queue_names: list[str]) -> None:
    queues = [Queue(name, connection=fake_redis) for name in queue_names]
    SimpleWorker(queues, connection=fake_redis).work(burst=True, with_scheduler=False)


class _NoOpDeathPenalty:
    """Death-penalty stand-in for the in-thread test workers.

    The real SIGALRM penalty can't be armed off the main thread, and rq's
    TimerDeathPenalty would spawn a per-job timer thread we don't need — the
    test bounds every wait itself (gate timeout + ``thread.join``). The death
    penalty is not what these tests exercise, so disable it entirely.
    """

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> _NoOpDeathPenalty:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _make_worker_thread_safe(monkeypatch) -> None:
    """SimpleWorker.work() runs jobs in-process; in a background thread its
    signal-based hooks (SIGINT/SIGTERM handlers + the SIGALRM death penalty)
    raise "signal only works in main thread". Disable both so the gated GPU
    worker can block in a thread; the test's own gate/join timeouts bound it."""
    monkeypatch.setattr(SimpleWorker, "_install_signal_handlers", lambda self: None)
    monkeypatch.setattr(SimpleWorker, "death_penalty_class", _NoOpDeathPenalty)


def _wait_for(predicate, timeout: float = _SYNC_TIMEOUT) -> bool:
    """Poll until predicate() is true (or timeout). A synchronisation barrier,
    not a wall-clock assertion — returns as soon as the condition holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _count(timeline: list, stage: str) -> int:
    return sum(1 for name, _ts in timeline if name == stage)


def _patch_gated_transcribe(monkeypatch, timeline, gate) -> None:
    monkeypatch.setattr(
        "whisper_ui.worker.stage_tasks.TranscribeStage",
        lambda *a, _t=timeline, _g=gate, **kw: _GatedTranscribe(_t, _g),
    )


def test_split_topology_lets_io_progress_while_gpu_busy(monkeypatch, tmp_path):
    """A separate io/cpu worker advances a newly-arrived job's io stage while
    the dedicated GPU worker is blocked on an earlier job's transcribe."""
    _make_worker_thread_safe(monkeypatch)
    fake_redis = fakeredis.FakeRedis()
    settings = _build_fake_settings()
    db = MagicMock()
    filestore = _fake_filestore(tmp_path)
    timeline: list = []
    gate = threading.Event()

    with (
        _stub_stages(monkeypatch, timeline),
        _fake_runtime_factory(fake_redis, settings, db, filestore) as (_, builder),
    ):
        _install_runtime(monkeypatch, builder)
        _patch_gated_transcribe(monkeypatch, timeline, gate)

        job_a = _make_upload_job("job-a", tmp_path)
        enqueue_pipeline(job_a, redis=fake_redis, settings=settings, filestore=filestore)

        # Drain io so job-a's GPU stage is promoted onto the gpu queue.
        _burst_worker(fake_redis, [WORKER_QUEUE_IO])
        assert Queue(WORKER_QUEUE_GPU, connection=fake_redis).count >= 1

        # The dedicated GPU worker picks up job-a's transcribe and blocks.
        gpu_thread = threading.Thread(target=_burst_worker, args=(fake_redis, [WORKER_QUEUE_GPU]))
        gpu_thread.start()
        try:
            assert _wait_for(lambda: _count(timeline, "transcribe") >= 1), "GPU worker never reached transcribe"
            preprocess_before = _count(timeline, "preprocess")

            # A new job arrives while the single GPU worker is occupied.
            job_b = _make_upload_job("job-b", tmp_path)
            enqueue_pipeline(job_b, redis=fake_redis, settings=settings, filestore=filestore)

            # A SEPARATE io/cpu worker drains it — independent of the busy GPU.
            _burst_worker(fake_redis, [WORKER_QUEUE_IO, WORKER_QUEUE_CPU])

            assert _count(timeline, "preprocess") == preprocess_before + 1, (
                "io worker did not advance job-b's preprocess while the GPU was busy"
            )
            assert gpu_thread.is_alive(), "GPU worker should still be blocked on the gate"
        finally:
            gate.set()
            gpu_thread.join(timeout=_SYNC_TIMEOUT)
        assert not gpu_thread.is_alive()


def test_combined_worker_blocks_io_behind_busy_gpu(monkeypatch, tmp_path):
    """Control / contrast: with ONE worker on all queues (the 2.7.0 topology),
    a newly-arrived job's io stage cannot run while that worker is blocked on
    the GPU stage — its preprocess sits queued."""
    _make_worker_thread_safe(monkeypatch)
    fake_redis = fakeredis.FakeRedis()
    settings = _build_fake_settings()
    db = MagicMock()
    filestore = _fake_filestore(tmp_path)
    timeline: list = []
    gate = threading.Event()

    with (
        _stub_stages(monkeypatch, timeline),
        _fake_runtime_factory(fake_redis, settings, db, filestore) as (_, builder),
    ):
        _install_runtime(monkeypatch, builder)
        _patch_gated_transcribe(monkeypatch, timeline, gate)

        job_a = _make_upload_job("job-a", tmp_path)
        enqueue_pipeline(job_a, redis=fake_redis, settings=settings, filestore=filestore)

        # One worker on ALL queues: drains job-a preprocess, then blocks on its
        # GPU stage with no other worker free to take over.
        combined = threading.Thread(
            target=_burst_worker,
            args=(fake_redis, [WORKER_QUEUE_IO, WORKER_QUEUE_GPU, WORKER_QUEUE_CPU]),
        )
        combined.start()
        try:
            assert _wait_for(lambda: _count(timeline, "transcribe") >= 1), "combined worker never reached transcribe"
            preprocess_before = _count(timeline, "preprocess")

            # A new job arrives — but the ONLY worker is stuck on the GPU stage.
            job_b = _make_upload_job("job-b", tmp_path)
            enqueue_pipeline(job_b, redis=fake_redis, settings=settings, filestore=filestore)

            # job-b's preprocess is immediately queued; with no free worker it
            # stays there and does not run.
            assert _wait_for(lambda: Queue(WORKER_QUEUE_IO, connection=fake_redis).count >= 1)
            assert _count(timeline, "preprocess") == preprocess_before, (
                "combined worker unexpectedly advanced io while the GPU was busy"
            )
            assert Queue(WORKER_QUEUE_IO, connection=fake_redis).count >= 1, "job-b preprocess should be stuck queued"
            assert combined.is_alive()
        finally:
            gate.set()
            combined.join(timeout=_SYNC_TIMEOUT)
        assert not combined.is_alive()


def _drain_split(fake_redis) -> int:
    """Drive a GPU-only worker and a separate io/cpu worker in alternating
    bursts (the split topology) until no queue makes progress. Returns the
    iteration count — a deterministic throughput proxy, not wall-clock."""
    iocpu = [Queue(WORKER_QUEUE_IO, connection=fake_redis), Queue(WORKER_QUEUE_CPU, connection=fake_redis)]
    gpu = [Queue(WORKER_QUEUE_GPU, connection=fake_redis)]
    for i in range(50):  # hard cap to guarantee termination
        progressed = False
        if any(q.count for q in iocpu):
            SimpleWorker(iocpu, connection=fake_redis).work(burst=True, with_scheduler=False)
            progressed = True
        if gpu[0].count:
            SimpleWorker(gpu, connection=fake_redis).work(burst=True, with_scheduler=False)
            progressed = True
        if not progressed:
            return i
    return 50


def test_split_drain_completes_all_jobs(monkeypatch, tmp_path):
    """With no gate, the split topology drives a batch of N jobs through every
    stage to COMPLETED within the iteration cap — each stage runs exactly N
    times."""
    fake_redis = fakeredis.FakeRedis()
    settings = _build_fake_settings()
    filestore = _fake_filestore(tmp_path)
    n_jobs = 3
    jobs = [_make_upload_job(f"job-{i}", tmp_path) for i in range(n_jobs)]
    jobs_by_id = {job.id: job for job in jobs}

    db = MagicMock()
    db.get_job.side_effect = lambda jid: jobs_by_id[jid]

    timeline: list = []
    with (
        _stub_stages(monkeypatch, timeline),
        _fake_runtime_factory(fake_redis, settings, db, filestore) as (_, builder),
    ):
        _install_runtime(monkeypatch, builder)
        for job in jobs:
            enqueue_pipeline(job, redis=fake_redis, settings=settings, filestore=filestore)
        iterations = _drain_split(fake_redis)

    for stage in ("preprocess", "transcribe", "align", "diarize", "assign_speakers", "postprocess"):
        assert _count(timeline, stage) == n_jobs, f"{stage} ran {_count(timeline, stage)} times, expected {n_jobs}"
    for job in jobs:
        assert job.status == JobStatus.COMPLETED, f"{job.id} ended {job.status}, expected COMPLETED"
    assert iterations < 50, "split drain did not settle within the iteration cap"
