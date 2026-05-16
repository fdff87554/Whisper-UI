"""Dispatcher that assembles a per-pipeline DAG of RQ sub-jobs.

``enqueue_pipeline`` seeds the shared context in Redis and fans out one
RQ sub-job per logical stage (or stage group) instead of running every
stage inside one monolithic worker task. The sub-jobs are wired
together via ``depends_on`` so that:

* download (if any) runs before preprocess
* transcribe_align and diarize start in parallel after preprocess
* assign_speakers fans them back in
* postprocess (and the optional llm_correction) finish the chain

The final job carries an ``on_success`` callback that saves the transcript
result, marks the parent job COMPLETED, and clears the context store. Every
sub-job also carries an ``on_failure`` callback that marks the parent FAILED,
cancels any dependent jobs that have not yet run, and cleans up the
intermediate 16 kHz WAV.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rq import Callback, Queue
from rq.command import send_stop_job_command
from rq.timeouts import BaseTimeoutException

from whisper_ui.core.constants import (
    ERROR_DISPLAY_LENGTH,
    ERROR_MAX_LENGTH,
    PIPELINE_STATE_TTL_SECONDS,
    WORKER_QUEUE_CPU,
    WORKER_QUEUE_GPU,
    WORKER_QUEUE_IO,
)
from whisper_ui.core.messages import PIPELINE_COMPLETE
from whisper_ui.core.models import JobStatus
from whisper_ui.ui.labels import JOBS_TIMEOUT_ERROR
from whisper_ui.worker.context_store import PipelineContextStore
from whisper_ui.worker.runtime import (
    build_worker_runtime,
    cleanup_preprocessed_audio,
    extract_rq_timeout_seconds,
    is_llm_active,
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
from whisper_ui.worker.timeout import calculate_job_timeout

# Stage function → queue name. IO stages (download, preprocess, llm_correction)
# go to a queue serviced by workers that do not hold a CUDA context, GPU
# stages go to the GPU queue, and lightweight CPU finalisation stages go to
# the CPU queue. See whisper_ui.core.constants for the rationale.
_STAGE_QUEUES = {
    run_download.__name__: WORKER_QUEUE_IO,
    run_preprocess.__name__: WORKER_QUEUE_IO,
    run_llm_correction.__name__: WORKER_QUEUE_IO,
    run_transcribe_align.__name__: WORKER_QUEUE_GPU,
    run_diarize.__name__: WORKER_QUEUE_GPU,
    run_assign_speakers.__name__: WORKER_QUEUE_CPU,
    run_postprocess.__name__: WORKER_QUEUE_CPU,
}

if TYPE_CHECKING:
    from redis import Redis
    from rq.job import Job as RQJob

    from whisper_ui.core.config import Settings
    from whisper_ui.core.models import Job
    from whisper_ui.storage.database import JobDatabase
    from whisper_ui.storage.filestore import FileStore
    from whisper_ui.worker.progress import RedisProgressReporter

logger = logging.getLogger(__name__)


# Redis keys for tracking sub-jobs and the generation counter. The
# subjobs set is scoped per-generation so a stale callback from a
# superseded attempt cannot accidentally enumerate the new attempt's
# sub-jobs. See Commit 18 / PR #39 Round 2 review for the full rationale:
# an earlier version of this module stored all sub-jobs under a single
# parent-scoped key and cleared it on retry, which made attempt 2's ids
# visible to attempt 1's late finalize callback.
def _subjobs_key(parent_job_id: str, generation: int) -> str:
    return f"whisper:pipeline:{parent_job_id}:subjobs:{generation}"


def _generation_key(parent_job_id: str) -> str:
    return f"whisper:pipeline:{parent_job_id}:generation"


def _bump_generation(redis: Redis, parent_job_id: str) -> int:
    """Atomically advance the generation counter for ``parent_job_id``.

    Returns the new generation. Every enqueue_pipeline call (including
    retries) goes through this, so sibling sub-jobs from a previous attempt
    that somehow kept running will see a newer generation when they try
    to commit their output and silently drop the write.
    """
    new_gen = redis.incr(_generation_key(parent_job_id))
    redis.expire(_generation_key(parent_job_id), PIPELINE_STATE_TTL_SECONDS)
    return int(new_gen)


def _current_generation(redis: Redis, parent_job_id: str) -> int | None:
    """Return the generation counter for ``parent_job_id``, or None when
    no attempt has ever been enqueued (or the counter has expired).
    """
    raw = redis.get(_generation_key(parent_job_id))
    if raw is None:
        return None
    return int(raw)


def _record_subjob(redis: Redis, parent_job_id: str, generation: int, sub_job_id: str) -> None:
    key = _subjobs_key(parent_job_id, generation)
    redis.sadd(key, sub_job_id)
    redis.expire(key, PIPELINE_STATE_TTL_SECONDS)


def _load_subjob_ids(redis: Redis, parent_job_id: str, generation: int) -> list[str]:
    raw = redis.smembers(_subjobs_key(parent_job_id, generation))
    return [item.decode() if isinstance(item, bytes) else item for item in raw]


def _clear_subjob_set(redis: Redis, parent_job_id: str, generation: int) -> None:
    redis.delete(_subjobs_key(parent_job_id, generation))


def enqueue_pipeline(
    job: Job,
    *,
    redis: Redis,
    settings: Settings,
    filestore: FileStore,
) -> str:
    """Build and enqueue the RQ DAG for ``job``.

    The DAG shape depends on three flags that come from the Job record and
    the deployment settings:

    * ``job.source_url`` → prepend a download sub-job
    * ``job.enable_diarization`` → add a diarize branch parallel to transcribe
    * ``job.llm_correction_enabled`` (AND ``settings.ollama_base_url``) → add
      an llm_correction sub-job after postprocess

    Returns the id of the last sub-job in the chain, so callers can attach
    monitoring to the tail of the pipeline if they want to.
    """
    queues = {
        name: Queue(name=name, connection=redis) for name in (WORKER_QUEUE_IO, WORKER_QUEUE_GPU, WORKER_QUEUE_CPU)
    }

    initial_context: dict = {
        "language": job.language,
        "batch_size": settings.batch_size,
        "num_speakers": job.num_speakers,
    }
    if job.source_url:
        initial_context["source_url"] = job.source_url
        initial_context["download_dir"] = str(filestore.prepare_upload_path(job.id, "_").parent)
        initial_context["input_path"] = ""
    else:
        initial_context["input_path"] = job.filepath or ""

    # Bump the generation counter *before* initializing the context so any
    # stale writer from the previous attempt sees the new value and drops
    # its write the next time it calls update_if_generation_matches.
    generation = _bump_generation(redis, job.id)

    # Seed the progress hash with the new generation immediately so a stale
    # writer that arrives after the retry route deleted ``job:{id}`` (wiping
    # the hash-embedded generation field) sees the fresh generation=N via
    # both the central counter (checked in Lua KEYS[2]) AND the hash field.
    # This is the hash-level belt on top of the central-counter suspenders
    # added in Commit 21, and also gives the UI a clean ``progress=0,
    # status=queued`` read right after retry instead of an empty hash.
    progress_key = f"job:{job.id}"
    redis.hset(
        progress_key,
        mapping={
            "progress": "0",
            "status": "queued",
            "generation": str(generation),
        },
    )
    redis.expire(progress_key, settings.redis_processing_expiry)

    ctx_store = PipelineContextStore(redis, job.id)
    ctx_store.initialize(initial_context)
    # The subjobs set is now per-generation, so we do NOT clear previous
    # attempts' sets here. An attempt 1 sub-job set will be cleaned up by
    # its own finalize callback (or expire naturally via PIPELINE_STATE_TTL_SECONDS),
    # and attempt 2's callback only ever looks at its own generation's set.
    # This is what keeps a stale attempt 1 callback from cancelling attempt
    # 2's live sub-jobs — the mechanism that broke in Round 2 review.

    timeout = calculate_job_timeout(job.duration, settings)
    success_cb = Callback("whisper_ui.worker.pipeline_dispatcher.finalize_success")
    failure_cb = Callback("whisper_ui.worker.pipeline_dispatcher.finalize_failure")
    # Sub-job meta carries both the parent_job_id (for callbacks that need
    # to route to the right Job row) and the generation (so stage tasks
    # can gate their writes against stale retries).
    meta = {"parent_job_id": job.id, "generation": generation}

    llm_active = is_llm_active(job, settings)

    enqueued: list[RQJob] = []

    def _enqueue(func, *, depends_on=None, is_final: bool) -> RQJob:
        queue_name = _STAGE_QUEUES[func.__name__]
        kwargs = {
            "job_timeout": timeout,
            "meta": dict(meta),
            "on_failure": failure_cb,
        }
        if depends_on is not None:
            kwargs["depends_on"] = depends_on
        if is_final:
            kwargs["on_success"] = success_cb
        sub = queues[queue_name].enqueue(func, job.id, **kwargs)
        enqueued.append(sub)
        _record_subjob(redis, job.id, generation, sub.id)
        return sub

    # Build the DAG. Note the "is_final" flag is only true on the very last
    # sub-job of the chosen branch, so finalize_success runs exactly once.
    if job.source_url:
        download_job = _enqueue(run_download, is_final=False)
        preprocess_job = _enqueue(run_preprocess, depends_on=download_job, is_final=False)
    else:
        preprocess_job = _enqueue(run_preprocess, is_final=False)

    transcribe_job = _enqueue(
        run_transcribe_align,
        depends_on=preprocess_job,
        is_final=False,
    )

    if job.enable_diarization:
        diarize_job = _enqueue(run_diarize, depends_on=preprocess_job, is_final=False)
        assign_deps: list[RQJob] = [transcribe_job, diarize_job]
    else:
        assign_deps = [transcribe_job]

    assign_job = _enqueue(run_assign_speakers, depends_on=assign_deps, is_final=False)
    postprocess_final = not llm_active
    postprocess_job = _enqueue(
        run_postprocess,
        depends_on=assign_job,
        is_final=postprocess_final,
    )

    if llm_active:
        llm_job = _enqueue(run_llm_correction, depends_on=postprocess_job, is_final=True)
        tail_id = llm_job.id
    else:
        tail_id = postprocess_job.id

    logger.info(
        "Enqueued pipeline DAG for job %s with %d sub-jobs",
        job.id,
        len(enqueued),
    )
    return tail_id


def _apply_filename_from_video_title(job: Job, context: dict) -> None:
    """If the pipeline downloaded a YouTube video, surface its title as the
    user-facing filename so the UI shows the human-readable title instead
    of the auto-generated "_" placeholder used at enqueue time.
    """
    if job.source_url and context.get("video_title"):
        job.filename = context["video_title"]


def finalize_success(rq_job, connection, _result) -> None:
    """RQ ``on_success`` callback for the final sub-job.

    Converts the accumulated context into a persisted transcript file,
    updates the parent Job row to COMPLETED, and clears the Redis context
    hash + sub-job tracking set for this attempt's generation.

    Callback staleness: if the parent's generation counter has moved on
    since this sub-job was enqueued (e.g. the user retried mid-pipeline),
    the callback short-circuits without touching any state. Without this
    guard, a stale attempt-1 success callback could mark an in-progress
    attempt 2 as COMPLETED with attempt 1's transcript file — see PR #39
    Round 2 review R2-1.
    """
    parent_job_id = rq_job.meta.get("parent_job_id") if rq_job.meta else None
    if not parent_job_id:
        logger.error("finalize_success invoked without parent_job_id in meta")
        return

    meta_generation = _extract_meta_generation(rq_job)

    # Pass meta_generation into build_worker_runtime so runtime.reporter's
    # terminal writes (complete / fail) are gated by the Lua scripts even
    # if the Python short-circuit below is somehow bypassed — defense in
    # depth.
    with build_worker_runtime(parent_job_id, generation=meta_generation) as runtime:
        if _is_stale_callback(runtime.redis, parent_job_id, meta_generation):
            logger.warning(
                "finalize_success dropped for job %s: stale callback from generation %s "
                "(current generation has moved on)",
                parent_job_id,
                meta_generation,
            )
            return

        job = runtime.db.get_job(parent_job_id)
        if job is None:
            logger.error("finalize_success could not find parent job %s", parent_job_id)
            return

        ctx_store = PipelineContextStore(runtime.redis, parent_job_id)
        context = ctx_store.load()
        reporter = runtime.reporter

        transcript_result = context.get("transcript_result")
        if transcript_result is None:
            logger.error("finalize_success for job %s: no transcript_result in context", parent_job_id)
            _mark_failed(job, runtime.db, reporter, "transcript_result missing")
            ctx_store.delete()
            if meta_generation is not None:
                _clear_subjob_set(runtime.redis, parent_job_id, meta_generation)
            return

        try:
            _apply_filename_from_video_title(job, context)
            result_path = runtime.filestore.save_result(parent_job_id, transcript_result)

            job.status = JobStatus.COMPLETED
            job.progress = 1.0
            job.progress_message = PIPELINE_COMPLETE
            job.result_path = str(result_path)
            job.duration = transcript_result.duration
            runtime.db.update_job(job)
            reporter.complete(str(result_path))

            logger.info("Job %s completed successfully via DAG pipeline", parent_job_id)
        finally:
            cleanup_preprocessed_audio(context)
            ctx_store.delete()
            if meta_generation is not None:
                _clear_subjob_set(runtime.redis, parent_job_id, meta_generation)


def _extract_meta_generation(rq_job) -> int | None:
    """Pull the generation id a sub-job was enqueued under out of its RQ meta.

    Sub-jobs from the legacy monolithic path (or tests that fabricate a
    MagicMock RQ job) may not carry this field at all — treat that as
    "no generation tracked" so the finalize callbacks fall through to
    their original behaviour and we do not regress the legacy path.
    """
    if rq_job is None or not getattr(rq_job, "meta", None):
        return None
    raw = rq_job.meta.get("generation")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_stale_callback(redis: Redis, parent_job_id: str, meta_generation: int | None) -> bool:
    """Return True when a finalize callback's meta generation has been
    superseded by a retry that bumped the parent generation counter.

    Degrades gracefully when either generation is unknown: if the meta
    has no generation (legacy / fabricated job) or the Redis counter is
    missing, we treat the callback as still valid so legacy code paths
    and tests that don't set up the full retry machinery keep working.
    Only a strictly-older meta generation than the current counter
    triggers the stale short-circuit.
    """
    if meta_generation is None:
        return False
    current = _current_generation(redis, parent_job_id)
    if current is None:
        return False
    return meta_generation < current


def _format_failure_message(exc_type, exc_value) -> str:
    """Turn an RQ ``on_failure`` exception triple into the user-facing error.

    RQ timeouts get routed through the same ``JOBS_TIMEOUT_ERROR`` label the
    legacy monolithic worker used (see ``worker/tasks.py``) so the UI shows
    the Chinese "任務總執行時間超出上限" message regardless of whether the
    pipeline ran in the DAG or legacy path. Any other exception falls back
    to ``str(exc_value)``, matching the pre-DAG behaviour for non-timeout
    failures.

    RQ passes the exception *class* as ``exc_type`` (not an instance), so the
    type check must use ``issubclass``. ``exc_value`` is still the instance
    and is passed through to ``extract_rq_timeout_seconds`` for message-regex
    parsing when the current-job lookup is unavailable.
    """
    if exc_type is not None and isinstance(exc_type, type) and issubclass(exc_type, BaseTimeoutException):
        seconds = extract_rq_timeout_seconds(exc_value) if exc_value is not None else "?"
        return JOBS_TIMEOUT_ERROR.format(seconds=seconds)
    if exc_value is not None:
        return str(exc_value)
    return "unknown pipeline failure"


def finalize_failure(rq_job, connection, _exc_type, exc_value, _traceback) -> None:
    """RQ ``on_failure`` callback attached to every sub-job.

    Called once per failing sub-job. The first invocation (for the
    current generation) marks the parent FAILED and cancels the other
    sub-jobs in the same generation; subsequent invocations from the
    same generation are no-ops because the parent is already terminal.

    Callback staleness: when the parent's generation counter has moved
    on since this sub-job was enqueued (e.g. the user retried mid-run),
    the callback short-circuits without cancelling or marking anything.
    Without this guard an attempt 1 failure could mark an in-flight
    attempt 2 as FAILED and cancel all of attempt 2's sub-jobs — the
    exact bug from PR #39 Round 2 review R2-1 that the reproduction
    test below covers.
    """
    parent_job_id = rq_job.meta.get("parent_job_id") if rq_job.meta else None
    if not parent_job_id:
        logger.error("finalize_failure invoked without parent_job_id in meta")
        return

    meta_generation = _extract_meta_generation(rq_job)
    error_msg = _format_failure_message(_exc_type, exc_value)

    # Pass meta_generation into build_worker_runtime so runtime.reporter is
    # gated by the Lua fail script even if the Python short-circuit below
    # is bypassed — same defense-in-depth as finalize_success.
    with build_worker_runtime(parent_job_id, generation=meta_generation) as runtime:
        if _is_stale_callback(runtime.redis, parent_job_id, meta_generation):
            logger.warning(
                "finalize_failure dropped for job %s: stale callback from generation %s "
                "(current generation has moved on)",
                parent_job_id,
                meta_generation,
            )
            return

        job = runtime.db.get_job(parent_job_id)
        if job is None:
            logger.error("finalize_failure could not find parent job %s", parent_job_id)
            return

        if job.status == JobStatus.FAILED:
            logger.debug(
                "finalize_failure: job %s already FAILED, skipping duplicate cleanup",
                parent_job_id,
            )
            return

        ctx_store = PipelineContextStore(runtime.redis, parent_job_id)
        context = ctx_store.load()
        reporter = runtime.reporter

        try:
            if meta_generation is not None:
                _cancel_remaining_subjobs(
                    runtime.redis,
                    parent_job_id,
                    generation=meta_generation,
                    exclude=rq_job.id,
                )
            _mark_failed(job, runtime.db, reporter, error_msg)
        finally:
            cleanup_preprocessed_audio(context)
            ctx_store.delete()
            if meta_generation is not None:
                _clear_subjob_set(runtime.redis, parent_job_id, meta_generation)


def _mark_failed(
    job: Job,
    db: JobDatabase,
    reporter: RedisProgressReporter,
    error_msg: str,
) -> None:
    job.status = JobStatus.FAILED
    job.error = error_msg[:ERROR_MAX_LENGTH]
    job.progress_message = f"Failed: {error_msg[:ERROR_DISPLAY_LENGTH]}"
    db.update_job(job)
    reporter.fail(error_msg)


def _cancel_remaining_subjobs(
    redis: Redis,
    parent_job_id: str,
    *,
    generation: int,
    exclude: str,
) -> None:
    """Stop every sub-job belonging to the same parent *and same
    generation*, except the one that just failed (``exclude``).

    Scoping by generation is what prevents a stale attempt-1 callback
    from cancelling attempt 2's live sub-jobs. ``_load_subjob_ids`` only
    returns sub-jobs that were enqueued under this exact generation; a
    retry opens a fresh set under a new generation that stale callbacks
    cannot see.

    This has to handle two distinct states for each sibling:

    1. **Already running.** ``RQJob.cancel()`` alone does *not* stop a
       running job — it only removes pending / deferred ones from the
       queue. For running jobs we fire ``send_stop_job_command`` first,
       which RQ delivers via a Redis pub/sub channel to the owning
       worker; the worker then raises a stop exception inside the job
       process at the next safe point. (See PR #39 review R3: without
       this, the diarize branch could keep running after transcribe
       failed and eventually write its result into the Redis context
       store, polluting a subsequent retry.)
    2. **Still queued / deferred.** ``send_stop_job_command`` is a no-op
       for jobs that have not started, so we follow it with ``cancel()``
       to evict them from the queue registry. Downstream dependent jobs
       (e.g. assign_speakers waiting on transcribe_align + diarize)
       would otherwise sit in the deferred registry forever.

    Both calls are best-effort: any failure only debug-logs and the
    loop keeps going so one stuck sibling cannot block the others.

    Note that even after ``send_stop_job_command`` lands, a worker
    already inside a native extension call (pyannote / whisperx C++
    inference) can take a bounded amount of time to actually exit. That
    residual window is closed by the generation-id gating in the next
    commit — this layer is the fast-path that minimizes wasted GPU time.
    """
    from rq.job import Job as RQJob

    for sub_id in _load_subjob_ids(redis, parent_job_id, generation):
        if sub_id == exclude:
            continue
        # Layer 1a: stop if currently running. Fire-and-forget — the stop
        # command is delivered over Redis pub/sub, so we cannot
        # synchronously confirm it took effect.
        try:
            send_stop_job_command(redis, sub_id)
        except Exception:
            logger.debug(
                "send_stop_job_command for %s failed (likely not currently running)",
                sub_id,
                exc_info=True,
            )
        # Layer 1b: cancel queued / deferred siblings so the queue drains.
        try:
            sub = RQJob.fetch(sub_id, connection=redis)
        except Exception:
            logger.debug("sub-job %s no longer exists, skipping cancel", sub_id)
            continue
        try:
            sub.cancel()
        except Exception:
            logger.warning("failed to cancel sub-job %s", sub_id, exc_info=True)


__all__ = [
    "enqueue_pipeline",
    "finalize_failure",
    "finalize_success",
]
