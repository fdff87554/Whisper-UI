"""Shared worker runtime helpers used by both the legacy monolithic task and
the per-stage DAG entrypoints.

``build_worker_runtime`` centralises the boilerplate of loading settings,
opening Redis / SQLite connections, and wiring up a progress reporter, so
every worker task can access those shared resources the same way. A context
manager is used so the caller gets deterministic cleanup (database close)
regardless of whether the task completes, raises, or is killed.

``make_throttled_progress_reporter`` is re-exported from here so it lives
alongside the other runtime helpers.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
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
def build_worker_runtime(job_id: str) -> Iterator[WorkerRuntime]:
    """Open the shared worker resources tied to a single job execution.

    The same ``job_id`` is used as the Redis progress key so all sub-tasks
    belonging to the same parent job converge onto a single progress hash.
    """
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    reporter = RedisProgressReporter(
        redis,
        job_id,
        processing_ttl=settings.redis_processing_expiry,
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

            reporter.report(progress, message)
            job.progress = progress
            job.progress_message = message
            db.update_job(job)

            last_progress = progress
            last_written_at = now
            last_message = message

    return report


__all__ = [
    "WorkerRuntime",
    "build_worker_runtime",
    "make_throttled_progress_reporter",
]
