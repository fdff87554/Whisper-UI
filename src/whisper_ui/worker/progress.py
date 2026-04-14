from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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

    def report(self, progress: float, message: str) -> None:
        self._redis.hset(
            self._key,
            mapping={
                "progress": str(progress),
                "message": message,
                "status": "processing",
            },
        )
        self._redis.expire(self._key, self._processing_ttl)

    def complete(self, result_path: str) -> None:
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

    def fail(self, error: str) -> None:
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

    @staticmethod
    def get_progress(redis: Redis, job_id: str) -> dict[str, str]:
        key = f"job:{job_id}"
        data = redis.hgetall(key)
        if not data:
            return {}
        return {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in data.items()
        }
