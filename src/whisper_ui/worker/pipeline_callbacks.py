"""Helpers consumed by the RQ on_success / on_failure callbacks.

``pipeline_dispatcher`` still owns ``finalize_success`` and
``finalize_failure`` themselves (their dotted paths are baked into
``rq.Callback`` invocations on every queued sub-job and changing them
would break in-flight jobs across a deploy). This module hosts the
supporting helpers — staleness check, sibling cancellation, error
formatting, parent-job mark-failed — so the dispatcher file can stay
focused on DAG assembly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rq.command import send_stop_job_command
from rq.timeouts import BaseTimeoutException

from whisper_ui.core.constants import ERROR_DISPLAY_LENGTH, ERROR_MAX_LENGTH
from whisper_ui.core.models import JobStatus
from whisper_ui.ui.labels import JOBS_TIMEOUT_ERROR
from whisper_ui.worker.runtime import extract_rq_timeout_seconds

if TYPE_CHECKING:
    from redis import Redis

    from whisper_ui.core.models import Job
    from whisper_ui.storage.database import JobDatabase
    from whisper_ui.worker.progress import RedisProgressReporter

logger = logging.getLogger(__name__)


def extract_meta_generation(rq_job) -> int | None:
    """Pull the generation id a sub-job was enqueued under out of its RQ meta.

    Sub-jobs from fabricated MagicMock RQ jobs in unit tests may not carry
    this field at all — treat that as "no generation tracked" so callbacks
    fall through to their pre-generation behaviour and unit tests that
    don't set up the full retry machinery keep working.
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


def is_stale_callback(current_generation: int | None, meta_generation: int | None) -> bool:
    """Return True when a callback's meta generation has been superseded.

    Degrades gracefully when either generation is unknown: if the meta has
    no generation or the current counter is missing, the callback is treated
    as still valid. Only a strictly-older meta than the current counter
    triggers the stale short-circuit.

    Generation gating lives in three places that must agree on this rule:
    here (Python, callback path), worker/progress.py
    ``_LUA_TERMINAL_GENERATION_GATE`` (Lua, terminal HSET path),
    and worker/context_store.py ``_GENERATION_GATED_HSET_LUA``
    (Lua, stage-output HSET path). Change one, change the others.
    """
    if meta_generation is None or current_generation is None:
        return False
    return meta_generation < current_generation


def format_failure_message(exc_type, exc_value) -> str:
    """Turn an RQ on_failure exception triple into the user-facing error.

    RQ timeouts route through ``JOBS_TIMEOUT_ERROR`` so the UI shows the
    Chinese "任務總執行時間超出上限" message; everything else falls back
    to ``str(exc_value)``.

    RQ passes the exception *class* as ``exc_type`` (not an instance), so
    the type check uses ``issubclass``; ``exc_value`` is the instance and
    is forwarded to ``extract_rq_timeout_seconds`` for message-regex
    parsing when the current-job lookup is unavailable.
    """
    if exc_type is not None and isinstance(exc_type, type) and issubclass(exc_type, BaseTimeoutException):
        seconds = extract_rq_timeout_seconds(exc_value) if exc_value is not None else "?"
        return JOBS_TIMEOUT_ERROR.format(seconds=seconds)
    if exc_value is not None:
        return str(exc_value)
    return "unknown pipeline failure"


def mark_failed(job: Job, db: JobDatabase, reporter: RedisProgressReporter, error_msg: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error_msg[:ERROR_MAX_LENGTH]
    job.progress_message = f"Failed: {error_msg[:ERROR_DISPLAY_LENGTH]}"
    db.update_job(job)
    reporter.fail(error_msg)


def cancel_remaining_subjobs(
    redis: Redis,
    sibling_ids: list[str],
    *,
    exclude: str,
) -> None:
    """Stop every sub-job in ``sibling_ids`` except the one named ``exclude``.

    The caller is responsible for scoping ``sibling_ids`` to the current
    generation so stale callbacks cannot reach a fresh retry's sub-jobs.

    Each sibling can be in one of two states:

    1. **Already running.** ``RQJob.cancel()`` alone does *not* stop a
       running job — it only removes pending / deferred ones from the
       queue. For running jobs ``send_stop_job_command`` is fired first;
       RQ delivers it via Redis pub/sub and the owning worker raises a
       stop exception at the next safe point. Without this, the diarize
       branch could keep running after transcribe failed and eventually
       write its result into the Redis context store, polluting a later
       retry.
    2. **Still queued / deferred.** ``send_stop_job_command`` is a no-op
       for jobs that have not started, so it is followed by ``cancel()``
       to evict them from the queue registry. Downstream dependent jobs
       would otherwise sit in the deferred registry forever.

    Both calls are best-effort: failures only debug-log and the loop
    keeps going so one stuck sibling cannot block the others. Workers
    already inside a native extension call (pyannote / whisperx C++
    inference) can take a bounded amount of time to actually exit after
    the stop command lands; that residual window is closed by the
    generation-gated context write inside the stage task body.
    """
    from rq.job import Job as RQJob

    for sub_id in sibling_ids:
        if sub_id == exclude:
            continue
        try:
            send_stop_job_command(redis, sub_id)
        except Exception:
            logger.debug(
                "send_stop_job_command for %s failed (likely not currently running)",
                sub_id,
                exc_info=True,
            )
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
    "cancel_remaining_subjobs",
    "extract_meta_generation",
    "format_failure_message",
    "is_stale_callback",
    "mark_failed",
]
