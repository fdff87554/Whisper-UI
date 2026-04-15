from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest
from rq.job import Job as RQJob

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.worker.context_store import PipelineContextStore
from whisper_ui.worker.pipeline_dispatcher import (
    _load_subjob_ids,
    enqueue_pipeline,
)
from whisper_ui.worker.stage_tasks import (
    run_assign_speakers,
    run_diarize,
    run_download,
    run_llm_correction,
    run_postprocess,
    run_preprocess,
    run_transcribe_align,
)

STAGE_TASK_NAMES = {
    run_download.__name__,
    run_preprocess.__name__,
    run_transcribe_align.__name__,
    run_diarize.__name__,
    run_assign_speakers.__name__,
    run_postprocess.__name__,
    run_llm_correction.__name__,
}


def _build_settings(*, ollama: str = "http://ollama.internal:11434") -> MagicMock:
    settings = MagicMock()
    settings.batch_size = 16
    settings.ollama_base_url = ollama
    settings.job_timeout_default = 3600
    settings.job_timeout_floor = 300
    settings.job_timeout_max = 14_400
    settings.job_timeout_audio_multiplier = 2.0
    return settings


def _build_filestore(tmp_path) -> MagicMock:
    fs = MagicMock()
    fs.prepare_upload_path.return_value = tmp_path / "subdir" / "file.mp3"
    return fs


def _load_subjobs(redis, parent_id) -> list[RQJob]:
    return [RQJob.fetch(sub_id, connection=redis) for sub_id in _load_subjob_ids(redis, parent_id)]


def _stage_func(sub: RQJob) -> str:
    return sub.func_name.rsplit(".", 1)[-1]


def _by_stage(subs: list[RQJob]) -> dict[str, RQJob]:
    return {_stage_func(s): s for s in subs}


def test_file_upload_dag_includes_preprocess_through_postprocess(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-file",
        filename="meeting.mp3",
        filepath=str(tmp_path / "meeting.mp3"),
        status=JobStatus.QUEUED,
        duration=120.0,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))

    assert set(subs.keys()) == {
        "run_preprocess",
        "run_transcribe_align",
        "run_diarize",
        "run_assign_speakers",
        "run_postprocess",
    }


def test_url_upload_dag_prepends_download(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-url",
        filename="video",
        source_url="https://www.youtube.com/watch?v=abc",
        status=JobStatus.QUEUED,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))

    assert "run_download" in subs
    assert subs["run_preprocess"]._dependency_ids == [subs["run_download"].id]


def test_llm_enabled_dag_appends_llm_correction(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings()
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-llm",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        llm_correction_enabled=True,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))

    assert "run_llm_correction" in subs
    assert subs["run_llm_correction"]._dependency_ids == [subs["run_postprocess"].id]
    # on_success must attach to the *final* job only
    assert subs["run_llm_correction"]._success_callback_name is not None
    assert subs["run_postprocess"]._success_callback_name is None


def test_llm_opt_in_without_ollama_url_is_ignored(tmp_path):
    """The deployment-level kill switch (empty ollama_base_url) must drop
    the llm_correction sub-job even if the user opted in, otherwise the
    dispatcher would allocate a sub-job that silently skips itself."""
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-llm-off",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        llm_correction_enabled=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))

    assert "run_llm_correction" not in subs
    assert subs["run_postprocess"]._success_callback_name is not None


def test_diarize_disabled_removes_diarize_sub_job(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-nodiar",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        enable_diarization=False,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))

    assert "run_diarize" not in subs
    assert subs["run_assign_speakers"]._dependency_ids == [subs["run_transcribe_align"].id]


def test_assign_speakers_fans_in_transcribe_and_diarize(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-fanin",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    subs = _by_stage(_load_subjobs(redis, job.id))
    deps = set(subs["run_assign_speakers"]._dependency_ids)

    assert deps == {subs["run_transcribe_align"].id, subs["run_diarize"].id}


def test_all_subjobs_carry_failure_callback_and_parent_meta(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-meta",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        enable_diarization=True,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)

    for sub in _load_subjobs(redis, job.id):
        assert sub._failure_callback_name is not None
        assert sub.meta.get("parent_job_id") == job.id
        assert _stage_func(sub) in STAGE_TASK_NAMES


def test_enqueue_pipeline_seeds_initial_context(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-ctx",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.QUEUED,
        language="zh",
        num_speakers=3,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    ctx = PipelineContextStore(redis, job.id).load()

    assert ctx["language"] == "zh"
    assert ctx["num_speakers"] == 3
    assert ctx["batch_size"] == settings.batch_size
    assert ctx["input_path"] == str(tmp_path / "m.mp3")
    assert "source_url" not in ctx


def test_url_upload_seeds_download_context(tmp_path):
    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-ctx-url",
        source_url="https://www.youtube.com/watch?v=abc",
        status=JobStatus.QUEUED,
    )

    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)
    ctx = PipelineContextStore(redis, job.id).load()

    assert ctx["source_url"] == job.source_url
    assert ctx["input_path"] == ""
    assert "download_dir" in ctx


def test_finalize_success_marks_job_completed(monkeypatch, tmp_path):
    from whisper_ui.core.models import TranscriptResult
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    job = Job(
        id="job-success",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.PROCESSING,
    )

    # Seed the context store as if postprocess just wrote its result.
    transcript = TranscriptResult(language="zh", duration=42.0)
    preprocessed = tmp_path / "preprocessed.wav"
    preprocessed.write_bytes(b"fake")
    PipelineContextStore(redis, job.id).initialize({"transcript_result": transcript, "audio_path": str(preprocessed)})

    runtime = MagicMock()
    runtime.redis = redis
    runtime.db.get_job.return_value = job
    runtime.filestore.save_result.return_value = tmp_path / "result.json"
    runtime.reporter = MagicMock()

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id):
        yield runtime

    monkeypatch.setattr(pd, "build_worker_runtime", _fake_builder)

    fake_rq_job = MagicMock()
    fake_rq_job.meta = {"parent_job_id": job.id}

    pd.finalize_success(fake_rq_job, redis, None)

    assert job.status == JobStatus.COMPLETED
    assert job.progress == 1.0
    assert job.result_path == str(tmp_path / "result.json")
    assert job.duration == pytest.approx(42.0)
    runtime.reporter.complete.assert_called_once_with(str(tmp_path / "result.json"))
    assert not preprocessed.exists(), "preprocessed WAV should be cleaned up"
    assert PipelineContextStore(redis, job.id).load() == {}


def test_finalize_failure_marks_job_failed_and_cancels_siblings(monkeypatch, tmp_path):
    from whisper_ui.worker import pipeline_dispatcher as pd

    redis = fakeredis.FakeRedis()
    settings = _build_settings(ollama="")
    filestore = _build_filestore(tmp_path)
    job = Job(
        id="job-fail",
        filename="m.mp3",
        filepath=str(tmp_path / "m.mp3"),
        status=JobStatus.PROCESSING,
        enable_diarization=True,
    )
    enqueue_pipeline(job, redis=redis, settings=settings, filestore=filestore)

    # Pick one sub-job to "fail". The rest should get cancelled.
    subs = _load_subjobs(redis, job.id)
    failing = next(s for s in subs if _stage_func(s) == "run_transcribe_align")
    others = [s for s in subs if s.id != failing.id]

    runtime = MagicMock()
    runtime.redis = redis
    runtime.db.get_job.return_value = job
    runtime.reporter = MagicMock()

    from contextlib import contextmanager

    @contextmanager
    def _fake_builder(job_id):
        yield runtime

    monkeypatch.setattr(pd, "build_worker_runtime", _fake_builder)

    failing.meta = {"parent_job_id": job.id}
    pd.finalize_failure(failing, redis, RuntimeError, RuntimeError("kaboom"), None)

    assert job.status == JobStatus.FAILED
    assert "kaboom" in (job.error or "")
    runtime.reporter.fail.assert_called_once()

    for other in others:
        refreshed = RQJob.fetch(other.id, connection=redis)
        assert refreshed.is_canceled, f"sub-job {_stage_func(other)} should be cancelled"
    # context store should be wiped
    assert PipelineContextStore(redis, job.id).load() == {}
