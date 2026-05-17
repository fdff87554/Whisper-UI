from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from whisper_ui.core.exceptions import PipelineError
from whisper_ui.core.models import Job, JobStatus
from whisper_ui.pipeline.progress_bands import build_stage_weights
from whisper_ui.worker.context_store import PipelineContextStore
from whisper_ui.worker.runtime import WorkerRuntime
from whisper_ui.worker.stage_tasks import (
    _banded_progress,
    _execute_stage,
    pick_stage_weights,
    run_diarize,
    run_postprocess,
    run_preprocess,
)


def _make_runtime(redis_client) -> WorkerRuntime:
    settings = MagicMock()
    settings.ollama_base_url = "http://ollama.internal:11434"
    settings.device = "cpu"
    settings.compute_type = "int8"
    settings.youtube_max_duration = 3600
    settings.diarize_heartbeat_interval = 30
    settings.hf_token = "fake-token-not-real"
    return WorkerRuntime(
        settings=settings,
        redis=redis_client,
        reporter=MagicMock(),
        db=MagicMock(),
        filestore=MagicMock(),
    )


@pytest.mark.parametrize(
    "source_url, llm_enabled, has_download, has_llm",
    [
        (None, False, False, False),
        (None, True, False, True),
        ("https://youtu.be/x", False, True, False),
        ("https://youtu.be/x", True, True, True),
    ],
)
def test_pick_stage_weights_matches_job_shape(source_url, llm_enabled, has_download, has_llm):
    job = Job(source_url=source_url, llm_correction_enabled=llm_enabled)
    runtime = _make_runtime(fakeredis.FakeRedis())
    expected = build_stage_weights(has_download=has_download, has_llm=has_llm)
    assert pick_stage_weights(job, runtime) == expected


def test_pick_stage_weights_ignores_llm_when_ollama_url_is_blank():
    """Even if the user opted in, an empty Ollama URL must fall back to the
    non-LLM bands so the dispatcher does not allocate an LLM progress band
    for a pipeline that will not run it."""
    job = Job(llm_correction_enabled=True)
    runtime = _make_runtime(fakeredis.FakeRedis())
    runtime.settings.ollama_base_url = ""
    assert pick_stage_weights(job, runtime) == build_stage_weights(has_download=False, has_llm=False)


def test_banded_progress_maps_local_to_global_range():
    calls: list[tuple[float, str]] = []
    report = _banded_progress(lambda p, m: calls.append((p, m)), (0.2, 0.6))

    report(0.0, "start")
    report(0.5, "half")
    report(1.0, "done")

    assert calls[0][0] == pytest.approx(0.2)
    assert calls[1][0] == pytest.approx(0.4)
    assert calls[2][0] == pytest.approx(0.6)
    assert [m for _, m in calls] == ["start", "half", "done"]


class _RecordingStage:
    name = "fake"

    def __init__(self, new_keys: dict[str, Any]):
        self._new_keys = new_keys
        self.cleanup_called = False

    def execute(self, context: dict, on_progress=None) -> dict:
        if on_progress:
            on_progress(1.0, "done")
        result = dict(context)
        result.update(self._new_keys)
        return result

    def cleanup(self) -> None:
        self.cleanup_called = True


class _ExplodingStage:
    name = "boom"

    def execute(self, context, on_progress=None):
        raise RuntimeError("kaboom")

    def cleanup(self) -> None:
        pass


def test_execute_stage_wraps_non_timeout_errors_as_pipeline_error():
    with pytest.raises(PipelineError) as excinfo:
        _execute_stage(_ExplodingStage(), {}, lambda p, m: None, stage_name="boom")
    assert "Stage 'boom' failed" in str(excinfo.value)


def test_execute_stage_always_cleans_up():
    stage = _RecordingStage({"audio_path": "/tmp/x"})
    _execute_stage(stage, {}, lambda p, m: None, stage_name="fake")
    assert stage.cleanup_called is True


def _install_fake_runtime(monkeypatch, fake_redis, job: Job) -> WorkerRuntime:
    runtime = _make_runtime(fake_redis)
    runtime.db.get_job.return_value = job

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        yield runtime

    monkeypatch.setattr("whisper_ui.worker.stage_tasks.build_worker_runtime", _fake_builder)
    return runtime


def test_run_preprocess_seeds_input_path_for_file_upload(monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-abc", status=JobStatus.PROCESSING, filepath="/tmp/audio.mp3")
    _install_fake_runtime(monkeypatch, fake_redis, job)

    fake_stage = _RecordingStage({"audio_path": "/tmp/16k.wav", "duration": 42.0})
    with patch("whisper_ui.worker.stage_tasks.PreprocessStage", return_value=fake_stage):
        run_preprocess("job-abc")

    stored = PipelineContextStore(fake_redis, "job-abc").load()
    assert stored["input_path"] == "/tmp/audio.mp3"
    assert stored["audio_path"] == "/tmp/16k.wav"
    assert stored["duration"] == pytest.approx(42.0)


def test_run_diarize_only_persists_declared_output_keys(monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-d", status=JobStatus.PROCESSING, filepath="/tmp/a.mp3")
    _install_fake_runtime(monkeypatch, fake_redis, job)

    PipelineContextStore(fake_redis, "job-d").initialize({"audio_path": "/tmp/16k.wav"})

    fake_stage = _RecordingStage({"diarize_result": [("SPK0", 0.0, 1.0)], "stray_field": "should-not-persist"})
    with patch("whisper_ui.worker.stage_tasks.DiarizeStage", return_value=fake_stage):
        run_diarize("job-d")

    stored = PipelineContextStore(fake_redis, "job-d").load()
    assert "diarize_result" in stored
    assert stored["diarize_result"] == [("SPK0", 0.0, 1.0)]
    assert "stray_field" not in stored
    assert stored["audio_path"] == "/tmp/16k.wav"


def test_run_postprocess_persists_transcript_result(monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-p", status=JobStatus.PROCESSING, convert_to_traditional=False)
    _install_fake_runtime(monkeypatch, fake_redis, job)

    PipelineContextStore(fake_redis, "job-p").initialize({"final_result": {"segments": []}})

    fake_stage = _RecordingStage({"transcript_result": {"segments": [], "language": "zh"}})
    with patch("whisper_ui.worker.stage_tasks.PostprocessStage", return_value=fake_stage):
        run_postprocess("job-p")

    stored = PipelineContextStore(fake_redis, "job-p").load()
    assert stored["transcript_result"] == {"segments": [], "language": "zh"}


def test_stage_task_transitions_queued_parent_job_to_processing(monkeypatch):
    """Regression for R1: in the DAG path the parent job must flip from
    QUEUED to PROCESSING as soon as any stage actually starts running.
    Without this the stale-job reaper (which only scans PROCESSING) can
    never recover a crashed DAG, and the UI keeps showing the job as
    queued forever.
    """
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-queued", status=JobStatus.QUEUED, filepath="/tmp/a.mp3")
    runtime = _install_fake_runtime(monkeypatch, fake_redis, job)

    fake_stage = _RecordingStage({"audio_path": "/tmp/16k.wav", "duration": 1.0})
    with patch("whisper_ui.worker.stage_tasks.PreprocessStage", return_value=fake_stage):
        run_preprocess("job-queued")

    assert job.status == JobStatus.PROCESSING
    runtime.db.update_job.assert_any_call(job)


def test_stage_task_leaves_already_processing_job_alone(monkeypatch):
    """Second parallel branch (e.g. diarize starting after transcribe_align
    already flipped the flag) must not re-issue a pointless status update.
    This keeps SQLite writes limited to state transitions.
    """
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-proc", status=JobStatus.PROCESSING, filepath="/tmp/a.mp3")
    runtime = _install_fake_runtime(monkeypatch, fake_redis, job)

    fake_stage = _RecordingStage({"diarize_result": [("SPK0", 0.0, 1.0)]})
    with patch("whisper_ui.worker.stage_tasks.DiarizeStage", return_value=fake_stage):
        run_diarize("job-proc")

    # update_job must not be called for a status flip (no QUEUED-to-PROCESSING
    # transition happened). The diarize stage itself does not write status,
    # so update_job should not have been called at all here.
    for call in runtime.db.update_job.call_args_list:
        assert call.args[0].status == JobStatus.PROCESSING


def test_run_transcribe_align_also_transitions_queued_to_processing(monkeypatch):
    """run_transcribe_align has its own driver (not _run_single_stage) so
    it needs the same guarded transition. Guards against the common
    mistake of adding the fix in one place and forgetting the other.
    """
    fake_redis = fakeredis.FakeRedis()
    job = Job(id="job-ta", status=JobStatus.QUEUED, filepath="/tmp/a.mp3")
    _install_fake_runtime(monkeypatch, fake_redis, job)
    PipelineContextStore(fake_redis, "job-ta").initialize({"audio_path": "/tmp/16k.wav"})

    fake_transcribe = _RecordingStage({"transcription_result": {"segments": []}})
    fake_align = _RecordingStage({"aligned_result": {"segments": []}})

    from whisper_ui.worker.stage_tasks import run_transcribe_align

    with (
        patch("whisper_ui.worker.stage_tasks.TranscribeStage", return_value=fake_transcribe),
        patch("whisper_ui.worker.stage_tasks.AlignStage", return_value=fake_align),
    ):
        run_transcribe_align("job-ta")

    assert job.status == JobStatus.PROCESSING


def test_run_diarize_raises_pipeline_error_when_job_missing(monkeypatch):
    fake_redis = fakeredis.FakeRedis()
    runtime = _make_runtime(fake_redis)
    runtime.db.get_job.return_value = None

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id, *, generation=None):
        yield runtime

    monkeypatch.setattr("whisper_ui.worker.stage_tasks.build_worker_runtime", _fake_builder)

    with pytest.raises(PipelineError):
        run_diarize("missing-job")
