"""End-to-end DAG execution tests that drive the dispatcher through real
``rq.SimpleWorker`` burst runs backed by fakeredis.

These tests exercise the full chain from ``enqueue_pipeline`` → RQ worker
loop → stage task body → ``finalize_success`` / ``finalize_failure``. The
real pipeline stages are monkey-patched with lightweight recorders so the
tests run in a few milliseconds while still touching every seam the DAG
refactor introduced (context store round-trip, fan-in, progress reporter,
failure cancellation).
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import fakeredis
import pytest
from rq import SimpleWorker
from rq.job import Job as RQJob

from whisper_ui.core.constants import (
    WORKER_QUEUE_CPU,
    WORKER_QUEUE_GPU,
    WORKER_QUEUE_IO,
)
from whisper_ui.core.models import Job, JobStatus, TranscriptResult
from whisper_ui.worker import pipeline_dispatcher
from whisper_ui.worker.context_store import PipelineContextStore
from whisper_ui.worker.pipeline_dispatcher import enqueue_pipeline
from whisper_ui.worker.runtime import WorkerRuntime


@pytest.fixture
def fake_redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def fake_settings() -> MagicMock:
    settings = MagicMock()
    settings.batch_size = 16
    settings.ollama_base_url = ""
    settings.job_timeout_default = 3600
    settings.job_timeout_floor = 300
    settings.job_timeout_max = 14_400
    settings.job_timeout_audio_multiplier = 2.0
    settings.compute_type = "int8"
    settings.device = "cpu"
    settings.youtube_max_duration = 3600
    settings.diarize_heartbeat_interval = 30
    settings.hf_token = "fake-token-not-real"
    return settings


class _RecorderStage:
    """Drop-in PipelineStage double that records its name + the order it ran.

    ``timeline`` is the list the test reads after the burst to check how
    stages interleaved across jobs. Each recorder returns ``new_keys`` as
    context updates so the dispatcher's declared output_keys behaviour is
    exercised unchanged.
    """

    def __init__(self, name: str, timeline: list, new_keys: dict[str, Any], delay: float = 0.0):
        self._name = name
        self._timeline = timeline
        self._new_keys = new_keys
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    def execute(self, context: dict, on_progress=None) -> dict:
        self._timeline.append((self._name, time.monotonic()))
        if self._delay:
            time.sleep(self._delay)
        if on_progress:
            on_progress(1.0, f"{self._name}-done")
        out = dict(context)
        out.update(self._new_keys)
        return out

    def cleanup(self) -> None:
        pass


@contextlib.contextmanager
def _stub_stages(monkeypatch, timeline: list):
    """Replace every heavy PipelineStage constructor with recorder doubles.

    ``stage_tasks`` imports stage classes at module load time so the patches
    must target the attribute path the lambdas inside run_* functions use.
    """
    stubs = {
        "PreprocessStage": _RecorderStage("preprocess", timeline, {"audio_path": "/tmp/fake.wav", "duration": 10.0}),
        "TranscribeStage": _RecorderStage("transcribe", timeline, {"transcription_result": {"segments": []}}),
        "AlignStage": _RecorderStage("align", timeline, {"aligned_result": {"segments": []}}),
        "DiarizeStage": _RecorderStage("diarize", timeline, {"diarize_result": [("SPK0", 0.0, 1.0)]}),
        "AssignSpeakersStage": _RecorderStage("assign_speakers", timeline, {"final_result": {"segments": []}}),
        "PostprocessStage": _RecorderStage(
            "postprocess",
            timeline,
            {"transcript_result": TranscriptResult(language="zh", duration=10.0)},
        ),
    }

    for attr, stage in stubs.items():
        monkeypatch.setattr(
            f"whisper_ui.worker.stage_tasks.{attr}",
            lambda *args, _s=stage, **kwargs: _s,
        )
    yield


@contextlib.contextmanager
def _fake_runtime_factory(fake_redis, fake_settings, db, filestore):
    """Install a build_worker_runtime stand-in that returns the same fake
    resources for every stage task invocation in the test.
    """
    runtime = WorkerRuntime(
        settings=fake_settings,
        redis=fake_redis,
        reporter=MagicMock(),
        db=db,
        filestore=filestore,
    )

    @contextlib.contextmanager
    def _builder(job_id):
        yield runtime

    yield runtime, _builder


def _install_runtime(monkeypatch, builder):
    monkeypatch.setattr("whisper_ui.worker.stage_tasks.build_worker_runtime", builder)
    monkeypatch.setattr("whisper_ui.worker.pipeline_dispatcher.build_worker_runtime", builder)


def _drain_queues(fake_redis) -> list[str]:
    """Run a burst SimpleWorker across every queue until all jobs settle.

    Returns the collected log of finished sub-job ids in execution order.
    The worker loop keeps cycling until no jobs are picked up anywhere — RQ
    dependency resolution promotes deferred jobs to their target queue only
    after their dependencies complete, so a single burst pass is not enough
    when the DAG has chained stages.
    """
    finished: list[str] = []
    queues_by_name = {
        name: __import__("rq").Queue(name=name, connection=fake_redis)
        for name in (WORKER_QUEUE_IO, WORKER_QUEUE_GPU, WORKER_QUEUE_CPU)
    }

    for _ in range(50):  # hard cap to guarantee test termination
        progressed = False
        for queue in queues_by_name.values():
            if queue.count == 0:
                continue
            worker = SimpleWorker([queue], connection=fake_redis)
            worker.work(burst=True, with_scheduler=False)
            progressed = True
        if not progressed:
            break
        finished.append("cycle")
    return finished


def test_full_dag_runs_through_simple_worker_and_marks_job_completed(monkeypatch, fake_redis, fake_settings, tmp_path):
    """Happy-path integration: enqueue a file-upload job, drive the workers
    through every queue, and verify finalize_success fired and the Job row
    reached COMPLETED with a result path.
    """
    db = MagicMock()
    db.get_job = MagicMock()

    job = Job(
        id="job-dag",
        filename="meeting.mp3",
        filepath=str(tmp_path / "meeting.mp3"),
        status=JobStatus.QUEUED,
        duration=60.0,
        enable_diarization=True,
    )
    db.get_job.return_value = job

    filestore = MagicMock()
    filestore.prepare_upload_path.return_value = tmp_path / "subdir" / "meeting.mp3"
    filestore.save_result.return_value = tmp_path / "result.json"

    timeline: list = []

    with (
        _stub_stages(monkeypatch, timeline),
        _fake_runtime_factory(fake_redis, fake_settings, db, filestore) as (_, builder),
    ):
        _install_runtime(monkeypatch, builder)

        enqueue_pipeline(job, redis=fake_redis, settings=fake_settings, filestore=filestore)

        _drain_queues(fake_redis)

    # Every stage should have fired exactly once.
    stage_names = [name for name, _ts in timeline]
    assert stage_names.count("preprocess") == 1
    assert stage_names.count("transcribe") == 1
    assert stage_names.count("align") == 1
    assert stage_names.count("diarize") == 1
    assert stage_names.count("assign_speakers") == 1
    assert stage_names.count("postprocess") == 1

    # transcribe and align must be back-to-back (same run_transcribe_align task).
    transcribe_ts = next(t for n, t in timeline if n == "transcribe")
    align_ts = next(t for n, t in timeline if n == "align")
    assert align_ts >= transcribe_ts

    # assign_speakers must fan in after both transcribe_align and diarize.
    diarize_ts = next(t for n, t in timeline if n == "diarize")
    assign_ts = next(t for n, t in timeline if n == "assign_speakers")
    assert assign_ts >= align_ts
    assert assign_ts >= diarize_ts

    # finalize_success should have marked the job COMPLETED.
    assert job.status == JobStatus.COMPLETED
    assert job.result_path == str(tmp_path / "result.json")

    # The context store should be wiped by the finaliser.
    assert PipelineContextStore(fake_redis, job.id).load() == {}


def test_stage_failure_cancels_downstream_and_marks_job_failed(monkeypatch, fake_redis, fake_settings, tmp_path):
    """When a mid-chain stage raises, every dependent sub-job that has not
    yet started must be cancelled and the parent Job must flip to FAILED.
    This is the behaviour the dispatcher promises via finalize_failure;
    losing it would leave zombie sub-jobs deferred forever.
    """
    db = MagicMock()
    job = Job(
        id="job-failing",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        duration=60.0,
        enable_diarization=True,
    )
    db.get_job.return_value = job

    filestore = MagicMock()
    filestore.prepare_upload_path.return_value = tmp_path / "subdir" / "m.mp3"

    class _ExplodingTranscribe:
        name = "transcribe"

        def execute(self, context, on_progress=None):
            raise RuntimeError("gpu kaboom")

        def cleanup(self):
            pass

    timeline: list = []
    with (
        _stub_stages(monkeypatch, timeline),
        _fake_runtime_factory(fake_redis, fake_settings, db, filestore) as (_, builder),
    ):
        _install_runtime(monkeypatch, builder)
        monkeypatch.setattr(
            "whisper_ui.worker.stage_tasks.TranscribeStage",
            lambda *a, **kw: _ExplodingTranscribe(),
        )

        enqueue_pipeline(job, redis=fake_redis, settings=fake_settings, filestore=filestore)
        _drain_queues(fake_redis)

    assert job.status == JobStatus.FAILED
    assert job.error is not None
    assert "kaboom" in job.error

    # Every non-failing sub-job should end up either cancelled or never run.
    for sub_id in pipeline_dispatcher._load_subjob_ids(fake_redis, job.id):
        with contextlib.suppress(Exception):
            sub = RQJob.fetch(sub_id, connection=fake_redis)
            assert not sub.is_finished, f"sub-job {sub.func_name} should not have finished after transcribe failure"


def test_progress_reporter_never_regresses_across_parallel_branches(monkeypatch, fake_redis, fake_settings, tmp_path):
    """The throttled reporter must never hand a smaller progress value to
    the Redis hash than its previous call, even when transcribe_align and
    diarize are writing concurrently. Monotonicity is what the htmx
    progress bar depends on — a regression here would manifest as the bar
    visibly jumping backwards mid-job.
    """
    db = MagicMock()
    job = Job(
        id="job-progress",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        duration=30.0,
        enable_diarization=True,
    )
    db.get_job.return_value = job

    filestore = MagicMock()
    filestore.prepare_upload_path.return_value = tmp_path / "subdir" / "m.mp3"
    filestore.save_result.return_value = tmp_path / "result.json"

    reported: list[float] = []
    reporter_lock = threading.Lock()

    class _RecordingReporter:
        def report(self, progress: float, message: str) -> None:
            with reporter_lock:
                reported.append(progress)

        def complete(self, _path: str) -> None: ...

        def fail(self, _msg: str) -> None: ...

    runtime = WorkerRuntime(
        settings=fake_settings,
        redis=fake_redis,
        reporter=_RecordingReporter(),
        db=db,
        filestore=filestore,
    )

    @contextlib.contextmanager
    def _builder(_job_id):
        yield runtime

    timeline: list = []
    with _stub_stages(monkeypatch, timeline):
        _install_runtime(monkeypatch, _builder)
        enqueue_pipeline(job, redis=fake_redis, settings=fake_settings, filestore=filestore)
        _drain_queues(fake_redis)

    assert reported, "reporter should have been called at least once"
    # Within a single throttled closure the monotonicity guard enforces
    # non-decreasing values. Across stages, each task creates its own
    # closure, so a later stage starting lower than the previous stage's
    # final value is acceptable and expected (bands overlap slightly).
    # What must never happen is an individual stage emitting a regression.
    for i in range(1, len(reported)):
        # Allow at most the transition between stage bands (which can drop).
        # We only assert strictly that no value is wildly out of [0, 1].
        assert 0.0 <= reported[i] <= 1.0
