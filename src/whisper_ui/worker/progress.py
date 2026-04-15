from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from whisper_ui.core.constants import (
    ERROR_MAX_LENGTH,
    MESSAGE_MAX_LENGTH,
    REDIS_COMPLETED_EXPIRY,
    REDIS_FAILED_EXPIRY,
)
from whisper_ui.core.messages import PIPELINE_COMPLETE

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


# Fallback TTL used when callers do not pass a processing expiry. Long enough
# to survive the default job timeout but short enough that crashed workers do
# not leave orphaned progress keys around for the whole completed-job window.
_DEFAULT_PROCESSING_TTL = 7200


# Atomic "max-write" script for parallel-branch progress reporting.
#
# Background: in the DAG pipeline, ``transcribe_align`` and ``diarize`` run
# as sibling sub-jobs in separate worker processes. Both hold a progress
# reporter bound to the same parent job_id and both can ``HSET`` the
# ``progress`` field concurrently. A straight ``HSET`` would let whichever
# write lands last win, which means the user's progress bar can visibly
# jump backwards (e.g. diarize at 0.72 → transcribe interleaves with 0.40 →
# bar rewinds to 0.40). The in-process throttled guard inside
# ``make_throttled_progress_reporter`` only protects a single closure, not
# two sibling workers.
#
# This Lua script runs server-side so the HGET / compare / HSET are one
# atomic operation. Progress only advances when the new value is >= the
# current one; ``message`` and ``status`` always update (message transitions
# between stages are information the user wants even if progress happens to
# stay the same, and ``status`` can only monotonically stay on "processing"
# until ``complete``/``fail`` is called).
_PROGRESS_MAX_WRITE_LUA = """
local key = KEYS[1]
local new_progress = tonumber(ARGV[1])
local message = ARGV[2]
local ttl = tonumber(ARGV[3])
local current = redis.call('HGET', key, 'progress')
if (not current) or (new_progress >= tonumber(current)) then
  redis.call('HSET', key, 'progress', tostring(new_progress),
             'message', message, 'status', 'processing')
else
  redis.call('HSET', key, 'message', message, 'status', 'processing')
end
redis.call('EXPIRE', key, ttl)
return 1
"""


class RedisProgressReporter:
    def __init__(
        self,
        redis: Redis,
        job_id: str,
        *,
        processing_ttl: int = _DEFAULT_PROCESSING_TTL,
    ) -> None:
        self._redis = redis
        self._job_id = job_id
        self._key = f"job:{job_id}"
        self._processing_ttl = processing_ttl
        # register_script loads the script lazily; the first call goes
        # through EVAL and subsequent calls reuse the cached SHA via
        # EVALSHA, which is cheap on the network path.
        self._max_write_script = redis.register_script(_PROGRESS_MAX_WRITE_LUA)

    def report(self, progress: float, message: str) -> None:
        # Progress reports are best-effort: SQLite remains the source of
        # truth for job state. A transient Redis outage mid-job must not
        # take down the worker — log and swallow so the pipeline keeps
        # running and the user only loses live progress updates.
        try:
            self._max_write_script(
                keys=[self._key],
                args=[progress, message, self._processing_ttl],
            )
        except RedisError:
            logger.warning("Redis progress write failed for %s", self._job_id, exc_info=True)

    def complete(self, result_path: str) -> None:
        try:
            self._redis.hset(
                self._key,
                mapping={
                    "progress": "1.0",
                    "message": PIPELINE_COMPLETE,
                    "status": "completed",
                    "result_path": result_path,
                },
            )
            self._redis.expire(self._key, REDIS_COMPLETED_EXPIRY)
        except RedisError:
            logger.warning("Redis complete write failed for %s", self._job_id, exc_info=True)

    def fail(self, error: str) -> None:
        try:
            self._redis.hset(
                self._key,
                mapping={
                    "progress": "0.0",
                    "message": error[:MESSAGE_MAX_LENGTH],
                    "status": "failed",
                    "error": error[:ERROR_MAX_LENGTH],
                },
            )
            self._redis.expire(self._key, REDIS_FAILED_EXPIRY)
        except RedisError:
            logger.warning("Redis fail write failed for %s", self._job_id, exc_info=True)

    @staticmethod
    def get_progress(redis: Redis, job_id: str) -> dict[str, str]:
        key = f"job:{job_id}"
        try:
            data = redis.hgetall(key)
        except RedisError:
            logger.warning("Redis progress read failed for %s", job_id, exc_info=True)
            return {}
        if not data:
            return {}
        return {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in data.items()
        }
