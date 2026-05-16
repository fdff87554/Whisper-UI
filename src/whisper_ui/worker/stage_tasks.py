"""Per-stage RQ task entrypoints for the parallel pipeline DAG.

Each ``run_*`` function is a single RQ job. The dispatcher assembles these
into a DAG per pipeline run, passing the parent job id only — the actual
pipeline context lives in Redis and is read/written through
:class:`PipelineContextStore` so that fan-out branches can run in different
worker processes.

The ``transcribe`` and ``align`` stages are merged into
``run_transcribe_align`` because they share an in-memory audio buffer that is
expensive to serialize across processes and always run back-to-back on the
same GPU worker anyway. Diarize stays separate so it can fan out to a second
GPU worker when multiple cards are available.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from rq.timeouts import BaseTimeoutException

from whisper_ui.core.exceptions import PipelineError
from whisper_ui.core.models import JobStatus
from whisper_ui.pipeline.align import AlignStage
from whisper_ui.pipeline.assign_speakers import AssignSpeakersStage
from whisper_ui.pipeline.diarize import DiarizeStage
from whisper_ui.pipeline.download import DownloadStage
from whisper_ui.pipeline.postprocess import PostprocessStage
from whisper_ui.pipeline.preprocess import PreprocessStage
from whisper_ui.pipeline.progress_bands import (
    StageWeights,
    build_stage_weights,
)
from whisper_ui.pipeline.transcribe import TranscribeStage
from whisper_ui.worker.context_store import PipelineContextStore
from whisper_ui.worker.runtime import (
    WorkerRuntime,
    build_worker_runtime,
    is_llm_active,
    make_throttled_progress_reporter,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from whisper_ui.core.models import Job
    from whisper_ui.pipeline.base import PipelineStage, ProgressCallback

logger = logging.getLogger(__name__)


def pick_stage_weights(job: Job, runtime: WorkerRuntime) -> StageWeights:
    """Build the stage weight bands matching this job's pipeline shape."""
    return build_stage_weights(
        has_download=bool(job.source_url),
        has_llm=is_llm_active(job, runtime.settings),
    )


def _load_job(runtime: WorkerRuntime, parent_job_id: str) -> Job:
    job = runtime.db.get_job(parent_job_id)
    if job is None:
        raise PipelineError(f"Job {parent_job_id} not found while running stage task")
    return job


def _mark_processing_if_queued(runtime: WorkerRuntime, job: Job) -> None:
    """Flip the parent job from QUEUED to PROCESSING on first stage entry.

    Multiple sub-jobs can be the "first" one to actually run (e.g.
    transcribe_align and diarize start in parallel after preprocess), so
    every stage task idempotently promotes the job when it observes
    QUEUED. The SQLite write path serialises concurrent writers via WAL
    mode + busy_timeout, so two parallel branches flipping simultaneously
    converge on the same PROCESSING state without corrupting the row.

    This guards two downstream behaviours that depend on the
    QUEUED → PROCESSING transition:

    - ``recover_stale_jobs`` only scans status = 'processing'; without
      this flip a crashed DAG leaves the parent stuck in QUEUED forever.
    - Dashboard polling speed and status badges branch on PROCESSING.
    """
    if job.status == JobStatus.QUEUED:
        job.status = JobStatus.PROCESSING
        runtime.db.update_job(job)


def _banded_progress(
    throttled: Callable[[float, str], None],
    band: tuple[float, float],
) -> ProgressCallback:
    """Wrap a throttled reporter so stages can emit local [0, 1] progress.

    Each stage's local progress is linearly mapped into its global band using
    the same formula the legacy orchestrator applied.
    """
    start, end = band
    span = end - start

    def on_progress(local: float, message: str) -> None:
        global_progress = start + local * span
        throttled(global_progress, message)

    return on_progress


def _execute_stage(
    stage: PipelineStage,
    context: dict[str, Any],
    on_progress: ProgressCallback,
    *,
    stage_name: str,
) -> dict[str, Any]:
    """Run a stage and convert non-timeout failures into ``PipelineError``.

    This mirrors the legacy orchestrator's contract so stage tasks emit the
    same error shape the worker-tasks layer already knows how to classify.
    """
    try:
        updated = stage.execute(context, on_progress=on_progress)
    except BaseTimeoutException:
        raise
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError(f"Stage '{stage_name}' failed: {e}") from e
    finally:
        stage.cleanup()
    return updated


def _current_generation() -> int | None:
    """Return the generation id the currently-running RQ job was enqueued
    under, or None outside an RQ worker context (e.g. unit tests that
    invoke stage tasks directly).

    Stage tasks use this to tell the context store which attempt they
    belong to, so a stale write from a previous retry attempt can be
    rejected even after ``send_stop_job_command`` has fired.
    """
    try:
        from rq import get_current_job

        current = get_current_job()
        if current is None or not current.meta:
            return None
        gen = current.meta.get("generation")
        return int(gen) if gen is not None else None
    except Exception:
        logger.debug("rq.get_current_job() unavailable while reading generation", exc_info=True)
        return None


def _persist_outputs(
    ctx_store: PipelineContextStore,
    updated: dict[str, Any],
    output_keys: tuple[str, ...],
    *,
    stage_name: str,
) -> None:
    """Write only the declared outputs back to the context store.

    Declaring outputs explicitly (instead of diffing the whole dict) keeps
    fan-in safe: two concurrent branches writing to disjoint keys never stomp
    each other, and large intermediates that should not leave the producing
    process (e.g. ``whisperx_audio``) are automatically excluded.

    When the current RQ job meta carries a ``generation``, the write goes
    through the context store's generation-gated path so a retry that has
    already incremented the generation counter causes this stale stage's
    output to be silently dropped. If the generation cannot be determined
    (e.g. running outside a worker in unit tests), fall back to the
    unconditional update so tests keep working.
    """
    updates = {key: updated[key] for key in output_keys if key in updated}
    generation = _current_generation()
    if generation is None:
        ctx_store.update(updates)
        return
    committed = ctx_store.update_if_generation_matches(updates, generation)
    if not committed:
        logger.warning(
            "Stage %s output dropped: parent job was retried under a new generation",
            stage_name,
        )


def _run_single_stage(
    parent_job_id: str,
    *,
    stage_name: str,
    build_stage: Callable[[Job, WorkerRuntime], PipelineStage],
    output_keys: tuple[str, ...],
    pre_context_update: Callable[[Job, WorkerRuntime, dict[str, Any]], None] | None = None,
) -> str:
    """Shared driver used by every ``run_*`` entry below.

    ``pre_context_update`` lets stages that need to seed context fields from
    the live ``Job`` record (e.g. download preparing ``download_dir``) do so
    without duplicating the runtime setup.
    """
    with build_worker_runtime(parent_job_id, generation=_current_generation()) as runtime:
        job = _load_job(runtime, parent_job_id)
        _mark_processing_if_queued(runtime, job)
        ctx_store = PipelineContextStore(runtime.redis, parent_job_id)
        context = ctx_store.load()

        if pre_context_update is not None:
            pre_context_update(job, runtime, context)

        stage = build_stage(job, runtime)
        throttled = make_throttled_progress_reporter(runtime.reporter, runtime.db, job)
        weights = pick_stage_weights(job, runtime)
        band = weights.get(stage_name, (0.0, 1.0))
        on_progress = _banded_progress(throttled, band)

        updated = _execute_stage(stage, context.copy(), on_progress, stage_name=stage_name)
        _persist_outputs(ctx_store, updated, output_keys, stage_name=stage_name)

        logger.info("Stage %s finished for job %s", stage_name, parent_job_id)
        return f"{stage_name}:{parent_job_id}"


def _seed_download_context(job: Job, runtime: WorkerRuntime, context: dict[str, Any]) -> None:
    """Ensure download-specific context keys exist when the pipeline starts
    from a URL source.
    """
    context.setdefault("source_url", job.source_url or "")
    download_dir = str(runtime.filestore.prepare_upload_path(job.id, "_").parent)
    context["download_dir"] = download_dir
    context["input_path"] = context.get("input_path", "")
    # Persist the seeded keys so DownloadStage running in this same task
    # sees them after it reloads via its own context argument.
    ctx_store = PipelineContextStore(runtime.redis, job.id)
    ctx_store.update(
        {
            "source_url": context["source_url"],
            "download_dir": context["download_dir"],
            "input_path": context["input_path"],
        }
    )


def run_download(parent_job_id: str) -> str:
    return _run_single_stage(
        parent_job_id,
        stage_name="download",
        build_stage=lambda job, runtime: DownloadStage(
            max_duration=runtime.settings.youtube_max_duration,
        ),
        output_keys=("input_path", "video_title"),
        pre_context_update=_seed_download_context,
    )


def run_preprocess(parent_job_id: str) -> str:
    def _seed_file_input(job: Job, runtime: WorkerRuntime, context: dict[str, Any]) -> None:
        # For direct-file uploads the input_path is on the Job record and
        # may not yet be in the Redis context (this is the first stage).
        if not context.get("input_path") and job.filepath:
            context["input_path"] = job.filepath
            PipelineContextStore(runtime.redis, job.id).update({"input_path": job.filepath})

    return _run_single_stage(
        parent_job_id,
        stage_name="preprocess",
        build_stage=lambda job, runtime: PreprocessStage(),
        output_keys=("audio_path", "duration"),
        pre_context_update=_seed_file_input,
    )


def run_transcribe_align(parent_job_id: str) -> str:
    """Run transcribe and align back-to-back in a single worker process.

    They share ``whisperx_audio`` (a large numpy buffer) through the local
    context dict, which avoids pickling it through Redis between two separate
    RQ tasks. The global progress bar still advances through the ``transcribe``
    and ``align`` bands independently.
    """
    with build_worker_runtime(parent_job_id, generation=_current_generation()) as runtime:
        job = _load_job(runtime, parent_job_id)
        _mark_processing_if_queued(runtime, job)
        ctx_store = PipelineContextStore(runtime.redis, parent_job_id)
        context = ctx_store.load()

        throttled = make_throttled_progress_reporter(runtime.reporter, runtime.db, job)
        weights = pick_stage_weights(job, runtime)

        transcribe = TranscribeStage(
            model_name=job.model_name,
            compute_type=runtime.settings.compute_type,
            device=runtime.settings.device,
        )
        align = AlignStage(device=runtime.settings.device)

        transcribe_progress = _banded_progress(throttled, weights.get("transcribe", (0.0, 1.0)))
        align_progress = _banded_progress(throttled, weights.get("align", (0.0, 1.0)))

        after_transcribe = _execute_stage(transcribe, context.copy(), transcribe_progress, stage_name="transcribe")
        after_align = _execute_stage(align, after_transcribe, align_progress, stage_name="align")

        _persist_outputs(
            ctx_store,
            after_align,
            output_keys=("transcription_result", "aligned_result"),
            stage_name="transcribe_align",
        )

        logger.info("Stage transcribe_align finished for job %s", parent_job_id)
        return f"transcribe_align:{parent_job_id}"


def run_diarize(parent_job_id: str) -> str:
    return _run_single_stage(
        parent_job_id,
        stage_name="diarize",
        build_stage=lambda job, runtime: DiarizeStage(
            hf_token=runtime.settings.hf_token,
            device=runtime.settings.device,
            enabled=job.enable_diarization,
            heartbeat_interval=runtime.settings.diarize_heartbeat_interval,
        ),
        output_keys=("diarize_result",),
    )


def run_assign_speakers(parent_job_id: str) -> str:
    return _run_single_stage(
        parent_job_id,
        stage_name="assign_speakers",
        build_stage=lambda job, runtime: AssignSpeakersStage(),
        output_keys=("final_result",),
    )


def run_postprocess(parent_job_id: str) -> str:
    return _run_single_stage(
        parent_job_id,
        stage_name="postprocess",
        build_stage=lambda job, runtime: PostprocessStage(
            convert_to_traditional=job.convert_to_traditional,
        ),
        output_keys=("transcript_result",),
    )


def run_llm_correction(parent_job_id: str) -> str:
    def _build(job: Job, runtime: WorkerRuntime) -> PipelineStage:
        # Lazy import so workers built without the worker-llm extras (httpx)
        # can still boot and process non-LLM jobs.
        from whisper_ui.pipeline.llm_correction import LLMCorrectionStage

        settings = runtime.settings
        return LLMCorrectionStage(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            keep_alive=settings.ollama_keep_alive,
            chunk_size=settings.llm_chunk_size,
            chunk_context=settings.llm_chunk_context,
            temperature=settings.llm_temperature,
            request_timeout=float(settings.ollama_request_timeout),
        )

    return _run_single_stage(
        parent_job_id,
        stage_name="llm_correction",
        build_stage=_build,
        output_keys=("transcript_result",),
    )


__all__ = [
    "pick_stage_weights",
    "run_assign_speakers",
    "run_diarize",
    "run_download",
    "run_llm_correction",
    "run_postprocess",
    "run_preprocess",
    "run_transcribe_align",
]
