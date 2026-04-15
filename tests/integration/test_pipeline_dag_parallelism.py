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
    gen = pipeline_dispatcher._current_generation(fake_redis, job.id)
    if gen is not None:
        for sub_id in pipeline_dispatcher._load_subjob_ids(fake_redis, job.id, gen):
            with contextlib.suppress(Exception):
                sub = RQJob.fetch(sub_id, connection=fake_redis)
                assert not sub.is_finished, f"sub-job {sub.func_name} should not have finished after transcribe failure"


def test_retry_isolates_new_attempt_from_stale_sibling_writes(monkeypatch, fake_redis, fake_settings, tmp_path):
    """End-to-end retry isolation (PR #39 R3 Layer 2). Simulates a stale
    diarize that is still running after transcribe_align failed and a
    retry has been enqueued; the stale stage's final write must be
    rejected because the generation counter has moved on.

    Walks through the exact scenario:
    1. First enqueue bumps generation to 1 and seeds fresh context.
    2. A stage task under generation=1 captures its generation but has
       not yet written its output (simulated by not calling _persist_outputs).
    3. The user retries the job: enqueue_pipeline runs again, bumps
       generation to 2, re-seeds the context with fresh initial values.
    4. The gen=1 stage finally tries to write its output. The gated
       write returns False and the new attempt's context is untouched.
    """
    from whisper_ui.worker.context_store import PipelineContextStore
    from whisper_ui.worker.pipeline_dispatcher import enqueue_pipeline as real_enqueue

    job = Job(
        id="job-retry-iso",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        enable_diarization=True,
    )
    filestore = MagicMock()
    filestore.prepare_upload_path.return_value = tmp_path / "subdir" / "m.mp3"

    # Attempt 1 — dispatcher bumps generation to 1, seeds context.
    real_enqueue(job, redis=fake_redis, settings=fake_settings, filestore=filestore)
    store_v1 = PipelineContextStore(fake_redis, job.id)
    assert store_v1.get_generation() == 1

    # Stale diarize reads its generation (1) at the moment of execution —
    # before the user retries. It has produced its result but has not yet
    # called update_if_generation_matches (e.g. because it is still deep
    # inside a pyannote C++ call while the retry enqueues a new attempt).
    stale_generation = 1
    stale_output = {"diarize_result": ["STALE_FROM_ATTEMPT_1"]}

    # Attempt 2 — user retries, dispatcher bumps generation to 2 and
    # re-initializes the context with fresh seed keys (no diarize_result).
    real_enqueue(job, redis=fake_redis, settings=fake_settings, filestore=filestore)
    store_v2 = PipelineContextStore(fake_redis, job.id)
    assert store_v2.get_generation() == 2
    assert "diarize_result" not in store_v2.load()

    # The stale stage finally flushes its output. Because its cached
    # generation no longer matches the current one, the write must be
    # dropped and the new attempt's context must stay clean.
    committed = store_v2.update_if_generation_matches(stale_output, stale_generation)

    assert committed is False
    assert "diarize_result" not in store_v2.load(), "stale diarize output from attempt 1 leaked into attempt 2 context"


def test_parallel_branch_progress_never_regresses_in_redis(monkeypatch, fake_redis, fake_settings, tmp_path):
    """End-to-end monotonicity check for the DAG path. Two real
    ``RedisProgressReporter`` instances (one per parallel branch) pound
    the same parent job_id from different threads with interleaved
    progress values; the final stored progress must equal the highest
    value any branch ever reported, and the full write history — read
    back via ``RedisProgressReporter.get_progress`` after each call —
    must never show a regression.

    This is the PR #39 review fix for R2 (Gemini + user): the previous
    version of this test was named "never_regresses" but its assertion
    only checked ``0.0 <= v <= 1.0``, which passed even while the bar
    was visibly jumping backwards under parallel worker writes.
    """
    from whisper_ui.worker.progress import RedisProgressReporter

    parent_id = "job-parallel-mono"
    transcribe_reporter = RedisProgressReporter(fake_redis, parent_id)
    diarize_reporter = RedisProgressReporter(fake_redis, parent_id)

    # Interleave writes so the server-side Lua max is the only thing
    # keeping progress monotonic. If it were a plain HSET the "0.20"
    # write from transcribe would clobber the "0.72" diarize write.
    writes = [
        (transcribe_reporter, 0.10, "transcribe starting"),
        (diarize_reporter, 0.72, "diarize running"),
        (transcribe_reporter, 0.20, "transcribe chunk 2"),
        (transcribe_reporter, 0.55, "transcribe chunk 3"),
        (diarize_reporter, 0.85, "diarize running"),
        (transcribe_reporter, 0.60, "align starting"),
    ]

    history: list[float] = []
    for reporter, progress, message in writes:
        reporter.report(progress, message)
        after = RedisProgressReporter.get_progress(fake_redis, parent_id)
        history.append(float(after["progress"]))

    # Core invariant: every consecutive pair is non-decreasing.
    for i in range(1, len(history)):
        assert history[i] >= history[i - 1], (
            f"progress regressed between step {i - 1} ({history[i - 1]}) and step {i} ({history[i]}); "
            f"full history: {history}"
        )

    # And the final value is the max of all writes, which is diarize's 0.85.
    assert history[-1] == 0.85

    # Message must reflect the latest write even when progress did not
    # advance (the 0.20 transcribe write that was rejected still updated
    # its message). This is what lets the UI show the current stage name.
    final = RedisProgressReporter.get_progress(fake_redis, parent_id)
    assert final["message"] == "align starting"


def test_parallel_reporters_from_threads_never_regress(fake_redis):
    """Same invariant as above but driven from real threads writing
    concurrently. Catches races the sequential-interleave test cannot
    see — if the Lua EVAL were somehow non-atomic, the thread version
    would surface it as a regression under load.
    """
    from whisper_ui.worker.progress import RedisProgressReporter

    parent_id = "job-parallel-mono-threaded"
    n_writes = 200

    def writer(start: float, step: float, label: str) -> None:
        reporter = RedisProgressReporter(fake_redis, parent_id)
        for i in range(n_writes):
            reporter.report(start + i * step, f"{label}-{i}")

    threads = [
        threading.Thread(target=writer, args=(0.00, 0.003, "transcribe")),
        threading.Thread(target=writer, args=(0.20, 0.003, "diarize")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = RedisProgressReporter.get_progress(fake_redis, parent_id)
    final_progress = float(final["progress"])
    # The highest value any writer produced is 0.20 + 199 * 0.003 ≈ 0.797
    expected_max = max(0.00 + (n_writes - 1) * 0.003, 0.20 + (n_writes - 1) * 0.003)
    assert final_progress == pytest.approx(expected_max, abs=1e-6)
