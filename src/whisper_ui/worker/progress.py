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

# Sentinel meaning "no generation check" in the Lua scripts. Callers that
# did not attach a generation (legacy monolithic path, tests that predate
# the generation machinery) pass this as caller_gen and the scripts skip
# every generation-related branch, falling back to their pre-Round-2
# max-write / unconditional-replace semantics.
_NO_GENERATION = -1


# Atomic generation-aware max-write for progress reports.
#
# Three branches, in priority order:
#
# 1. ``caller_gen < 0`` (sentinel) → legacy mode. No generation tracking.
#    Max-write by progress value; message and status always update.
#    Existing Phase 2 semantics, preserved so the legacy ``tasks.py``
#    path and any reporter built via ``build_worker_runtime`` without a
#    generation stays bit-for-bit compatible.
#
# 2. ``caller_gen > stored_gen`` (including stored_gen missing) → reset.
#    A fresh attempt is taking over the progress hash. Unconditionally
#    overwrite progress / message / status and stamp the new generation.
#    This is the path that unblocks "attempt 2 wants to start from 0.05
#    after attempt 1 was pinned at 0.85" — the Round 2 R2-2 scenario
#    the reproducer in the plan exercises end-to-end.
#
# 3. ``caller_gen == stored_gen`` → max-write within the attempt. Same
#    semantics as the Phase 2 script: progress only advances, message
#    still updates even when progress holds so the UI can switch stage
#    labels mid-attempt.
#
# 4. ``caller_gen < stored_gen`` → drop. A late writer from a superseded
#    attempt is trying to touch a hash that already belongs to a newer
#    attempt. Silently ignore it and return 0 so Python-level metrics
#    can count if desired.
_PROGRESS_MAX_WRITE_LUA = """
local key = KEYS[1]
local new_progress = tonumber(ARGV[1])
local message = ARGV[2]
local ttl = tonumber(ARGV[3])
local caller_gen = tonumber(ARGV[4])

if caller_gen < 0 then
  -- Legacy mode: no generation tracking at all. Max-write.
  local current = redis.call('HGET', key, 'progress')
  if (not current) or (new_progress >= tonumber(current)) then
    redis.call('HSET', key, 'progress', tostring(new_progress),
               'message', message, 'status', 'processing')
  else
    redis.call('HSET', key, 'message', message, 'status', 'processing')
  end
  redis.call('EXPIRE', key, ttl)
  return 1
end

local stored_gen_raw = redis.call('HGET', key, 'generation')
local stored_gen = nil
if stored_gen_raw then
  stored_gen = tonumber(stored_gen_raw)
end

if (not stored_gen) or caller_gen > stored_gen then
  -- New attempt takes over: reset the hash unconditionally.
  redis.call('HSET', key,
    'progress', tostring(new_progress),
    'message', message,
    'status', 'processing',
    'generation', tostring(caller_gen))
  redis.call('EXPIRE', key, ttl)
  return 1
end

if caller_gen < stored_gen then
  -- Stale writer from a superseded attempt. Drop silently.
  return 0
end

-- Same generation as the hash: normal max-write semantics.
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


# Atomic generation-aware terminal write for complete().
#
# Mirrors the max-write script's generation logic but writes the
# terminal "completed" hash contents when the generation check passes.
# Terminal writes are unconditional within the caller's generation: a
# stage that believes the pipeline is done owns the hash regardless of
# what progress value was previously stored.
_PROGRESS_COMPLETE_LUA = """
local key = KEYS[1]
local result_path = ARGV[1]
local pipeline_complete_msg = ARGV[2]
local ttl = tonumber(ARGV[3])
local caller_gen = tonumber(ARGV[4])

if caller_gen >= 0 then
  local stored_gen_raw = redis.call('HGET', key, 'generation')
  if stored_gen_raw then
    local stored_gen = tonumber(stored_gen_raw)
    if stored_gen and caller_gen < stored_gen then
      return 0
    end
  end
end

redis.call('HSET', key,
  'progress', '1.0',
  'message', pipeline_complete_msg,
  'status', 'completed',
  'result_path', result_path)
if caller_gen >= 0 then
  redis.call('HSET', key, 'generation', tostring(caller_gen))
end
redis.call('EXPIRE', key, ttl)
return 1
"""


# Atomic generation-aware terminal write for fail().
_PROGRESS_FAIL_LUA = """
local key = KEYS[1]
local message = ARGV[1]
local error_text = ARGV[2]
local ttl = tonumber(ARGV[3])
local caller_gen = tonumber(ARGV[4])

if caller_gen >= 0 then
  local stored_gen_raw = redis.call('HGET', key, 'generation')
  if stored_gen_raw then
    local stored_gen = tonumber(stored_gen_raw)
    if stored_gen and caller_gen < stored_gen then
      return 0
    end
  end
end

redis.call('HSET', key,
  'progress', '0.0',
  'message', message,
  'status', 'failed',
  'error', error_text)
if caller_gen >= 0 then
  redis.call('HSET', key, 'generation', tostring(caller_gen))
end
redis.call('EXPIRE', key, ttl)
return 1
"""


class RedisProgressReporter:
    """Best-effort progress / terminal-state writer for a single job.

    ``generation`` scopes every write to an attempt. When provided the
    reporter's report/complete/fail calls go through the generation-aware
    Lua scripts above, so a stale writer from a superseded attempt has
    its writes dropped server-side and cannot corrupt the new attempt's
    progress hash. When left as ``None`` the scripts fall back to legacy
    semantics (max-write for report, unconditional replace for
    complete/fail) — this keeps the pre-Round-2 behaviour for the
    legacy ``worker.tasks.process_transcription`` path and for any
    caller that does not yet know which attempt it belongs to.
    """

    def __init__(
        self,
        redis: Redis,
        job_id: str,
        *,
        processing_ttl: int = _DEFAULT_PROCESSING_TTL,
        generation: int | None = None,
    ) -> None:
        self._redis = redis
        self._job_id = job_id
        self._key = f"job:{job_id}"
        self._processing_ttl = processing_ttl
        self._generation = generation
        # register_script loads each script lazily; the first call goes
        # through EVAL and subsequent calls reuse the cached SHA via
        # EVALSHA, which is cheap on the network path.
        self._max_write_script = redis.register_script(_PROGRESS_MAX_WRITE_LUA)
        self._complete_script = redis.register_script(_PROGRESS_COMPLETE_LUA)
        self._fail_script = redis.register_script(_PROGRESS_FAIL_LUA)

    @property
    def generation(self) -> int | None:
        return self._generation

    def _caller_gen_arg(self) -> int:
        """Translate the optional generation into the Lua sentinel encoding.

        ``None`` in Python becomes ``-1`` in the script, which short-
        circuits every generation branch and falls back to legacy
        semantics. Any non-negative integer is passed through as-is.
        """
        return _NO_GENERATION if self._generation is None else int(self._generation)

    def report(self, progress: float, message: str) -> None:
        # Progress reports are best-effort: SQLite remains the source of
        # truth for job state. A transient Redis outage mid-job must not
        # take down the worker — log and swallow so the pipeline keeps
        # running and the user only loses live progress updates.
        try:
            self._max_write_script(
                keys=[self._key],
                args=[progress, message, self._processing_ttl, self._caller_gen_arg()],
            )
        except RedisError:
            logger.warning("Redis progress write failed for %s", self._job_id, exc_info=True)

    def complete(self, result_path: str) -> None:
        try:
            self._complete_script(
                keys=[self._key],
                args=[result_path, PIPELINE_COMPLETE, REDIS_COMPLETED_EXPIRY, self._caller_gen_arg()],
            )
        except RedisError:
            logger.warning("Redis complete write failed for %s", self._job_id, exc_info=True)

    def fail(self, error: str) -> None:
        try:
            self._fail_script(
                keys=[self._key],
                args=[
                    error[:MESSAGE_MAX_LENGTH],
                    error[:ERROR_MAX_LENGTH],
                    REDIS_FAILED_EXPIRY,
                    self._caller_gen_arg(),
                ],
            )
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
