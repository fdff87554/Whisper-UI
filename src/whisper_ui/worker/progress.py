from __future__ import annotations

import logging

from redis import Redis

logger = logging.getLogger(__name__)


class RedisProgressReporter:
    def __init__(self, redis: Redis, job_id: str) -> None:
        self._redis = redis
        self._job_id = job_id
        self._key = f"job:{job_id}"

    def report(self, progress: float, message: str) -> None:
        self._redis.hset(
            self._key,
            mapping={
                "progress": str(progress),
                "message": message,
                "status": "processing",
            },
        )

    def complete(self, result_path: str) -> None:
        self._redis.hset(
            self._key,
            mapping={
                "progress": "1.0",
                "message": "Complete",
                "status": "completed",
                "result_path": result_path,
            },
        )
        self._redis.expire(self._key, 86400)

    def fail(self, error: str) -> None:
        self._redis.hset(
            self._key,
            mapping={
                "progress": "0.0",
                "message": error[:500],
                "status": "failed",
                "error": error[:1000],
            },
        )
        self._redis.expire(self._key, 86400)

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
