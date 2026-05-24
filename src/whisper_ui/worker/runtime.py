"""Shared worker runtime helpers used by the per-stage DAG entrypoints.

``build_worker_runtime`` centralises the boilerplate of loading settings,
opening Redis / SQLite connections, and wiring up a progress reporter, so
every stage task can access those shared resources the same way. A context
manager is used so the caller gets deterministic cleanup (database close)
regardless of whether the task completes, raises, or is killed.

``make_throttled_progress_reporter`` and ``is_llm_active`` live here too
so the runtime module is the one place stage tasks pull common helpers
from.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from redis import Redis

from whisper_ui.core.config import get_settings
from whisper_ui.core.constants import (
    PROGRESS_WRITE_MIN_DELTA,
    PROGRESS_WRITE_MIN_INTERVAL_SEC,
)
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore
from whisper_ui.worker.progress import RedisProgressReporter

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from whisper_ui.core.config import Settings
    from whisper_ui.core.models import Job

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerRuntime:
    """Bundle of shared resources a worker task needs for one invocation.

    Acquired via :func:`build_worker_runtime`. The ``db`` handle is closed
    automatically when the context manager exits.
    """

    settings: Settings
    redis: Redis
    reporter: RedisProgressReporter
    db: JobDatabase
    filestore: FileStore


@contextmanager
def build_worker_runtime(job_id: str, *, generation: int | None = None) -> Iterator[WorkerRuntime]:
    """Open the shared worker resources tied to a single job execution.

    The same ``job_id`` is used as the Redis progress key so all sub-tasks
    belonging to the same parent job converge onto a single progress hash.

    ``generation`` is stamped onto the bundled ``reporter``; pass the RQ
    job's meta generation so progress writes from a superseded retry get
    rejected by the Lua gating script. Leave it None when invoked outside
    an RQ worker context (unit tests, one-off scripts) so the reporter
    skips generation gating and falls back to plain max-write semantics.
    """
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    reporter = RedisProgressReporter(
        redis,
        job_id,
        processing_ttl=settings.redis_processing_expiry,
        generation=generation,
    )
    db = JobDatabase(settings.database_path)
    filestore = FileStore(settings.upload_dir, settings.output_dir)
    try:
        yield WorkerRuntime(
            settings=settings,
            redis=redis,
            reporter=reporter,
            db=db,
            filestore=filestore,
        )
    finally:
        db.close()


def cleanup_preprocessed_audio(context: dict) -> None:
    """Remove the intermediate 16 kHz WAV created by PreprocessStage, if any.

    Called on both the success and failure completion paths so an aborted
    pipeline does not leave the temporary WAV behind. Centralising the
    implementation here keeps the success and failure callbacks from
    drifting on what counts as a missing path.
    """
    audio_path = context.get("audio_path")
    if not audio_path:
        return
    try:
        Path(audio_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to clean up preprocessed file: %s", audio_path)


def is_llm_active(job: Job, settings: Settings) -> bool:
    """Return whether the LLM correction stage should run for ``job``.

    Two conditions must hold: the user opted in on the upload form, and the
    deployment exposes an Ollama endpoint. Both the dispatcher (to decide
    whether to enqueue ``run_llm_correction``) and the stage selector (to
    pick the matching progress weight table) consult this so the two
    decisions never drift.
    """
    return bool(job.llm_correction_enabled) and settings.llm_correction_available


def make_throttled_progress_reporter(
    reporter: RedisProgressReporter,
    db: JobDatabase,
    job: Job,
    *,
    min_delta: float = PROGRESS_WRITE_MIN_DELTA,
    min_interval_sec: float = PROGRESS_WRITE_MIN_INTERVAL_SEC,
    monotonic: Callable[[], float] = time.monotonic,
) -> Callable[[float, str], None]:
    """Wrap progress callbacks so high-frequency sub-stage updates do not
    thrash SQLite and Redis.

    The throttle drops a report when both of these hold:
    - progress moved less than ``min_delta`` from the last written value,
    - less than ``min_interval_sec`` has elapsed since the last write.

    It always flushes when the message changes (stage transition, state
    flip), on the very first call, and whenever progress reaches 1.0, so
    no user-visible milestone is ever swallowed.
    """
    last_progress = -1.0
    last_written_at = 0.0
    last_message = ""
    # The diarize heartbeat invokes ``report`` from a background thread while
    # the main thread is blocked inside the C++ inference call, so in normal
    # operation only one thread mutates the closure state at a time. The lock
    # is cheap defence-in-depth: it guarantees the read-decide-write sequence
    # below is atomic even if some future stage spawns a worker thread that
    # also calls on_progress.
    lock = threading.Lock()

    def report(progress: float, message: str) -> None:
        nonlocal last_progress, last_written_at, last_message

        with lock:
            # Monotonicity guard: drop any in-closure regression unconditionally,
            # even if the message changed. The only realistic source of one is a
            # late diarize heartbeat racing the main thread's DIARIZE_DONE flush;
            # letting it through would visibly rewind the bar from 100% back to
            # ~94%. Worker retries always spin up a fresh closure, so legitimate
            # rewinds never reach this point.
            if last_progress >= 0 and progress < last_progress:
                return

            now = monotonic()
            force = last_progress < 0 or message != last_message or progress >= 1.0
            if not force:
                delta = progress - last_progress
                if delta < min_delta and (now - last_written_at) < min_interval_sec:
                    return

            # The reporter returns False exactly when its Lua script
            # determined the caller's generation is strictly older than
            # the stored one (i.e. the parent job has been retried under
            # a newer attempt). In that case we must NOT touch the Job
            # object or the SQLite row: db.update_job performs a
            # full-column UPDATE from the in-memory snapshot, so a stale
            # Job captured by this closure would overwrite the current
            # attempt's status / result_path / error fields. Silently
            # drop the DB mirror and leave the closure's monotonic /
            # throttle state untouched so a subsequent (equally stale)
            # call takes the same fast path instead of slipping through.
            accepted = reporter.report(progress, message)
            if not accepted:
                logger.debug(
                    "progress write for %s dropped server-side (stale generation); "
                    "skipping DB mirror to avoid overwriting the current attempt",
                    job.id,
                )
                return

            job.progress = progress
            job.progress_message = message
            db.update_job(job)

            last_progress = progress
            last_written_at = now
            last_message = message

    return report


_RQ_TIMEOUT_MESSAGE_PATTERN = re.compile(r"\((\d+)\s*seconds?\)")


def extract_rq_timeout_seconds(exc: BaseException) -> int | str:
    """Return the configured RQ ``job_timeout`` for the running job.

    RQ's death-penalty handler formats the timeout into the exception
    *message* but does not attach it as an attribute on the exception
    instance (see ``rq.timeouts.UnixSignalDeathPenalty.handle_death_penalty``
    in RQ 2.7.0). So:

    1. In a real worker context, ``rq.get_current_job().timeout`` holds the
       actual configured value from enqueue time.
    2. Outside a worker context (unit tests that call the worker entrypoints
       directly, or the DAG failure callback which runs outside the timing-
       out job itself), fall back to parsing the formatted message.
    3. If both fail, return ``"?"`` so the error label still renders.

    Lives in ``runtime`` because the DAG stage tasks (raise the timeout
    inside the worker) and ``finalize_failure`` (sees the exception from
    outside the timing-out job) both need to render the same Chinese
    label, and the helper has to be importable from both without a
    cross-dependency.
    """
    try:
        from rq import get_current_job

        current = get_current_job()
        if current is not None and current.timeout:
            return current.timeout
    except Exception:
        logger.debug("rq.get_current_job() unavailable while extracting timeout", exc_info=True)

    match = _RQ_TIMEOUT_MESSAGE_PATTERN.search(str(exc))
    if match:
        return int(match.group(1))
    return "?"


__all__ = [
    "WorkerRuntime",
    "build_worker_runtime",
    "cleanup_preprocessed_audio",
    "extract_rq_timeout_seconds",
    "is_llm_active",
    "make_throttled_progress_reporter",
]
