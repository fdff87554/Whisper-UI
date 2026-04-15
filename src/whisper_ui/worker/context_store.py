"""Shared pipeline context storage backed by a Redis hash.

Each parent job owns one context hash at `whisper:ctx:{parent_job_id}`. Stage
tasks read the full context at the start of their execution, run their stage,
and write back only the keys that stage is declared to produce. Storing fields
in a hash (one Redis field per context key) means two stages that write
disjoint keys can commit concurrently without racing — this is what makes
fan-in at assign_speakers safe.

Values are pickled individually per field so every supported Python object a
stage stores in the context (dataclasses, dicts, lists, primitives) survives
the round trip. Large arrays such as ``whisperx_audio`` are deliberately *not*
persisted here; they only live in the process that produced them.
"""

from __future__ import annotations

import pickle
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis import Redis


# One-day expiry is a safety net for abandoned contexts (worker killed before
# the finalizer ran). Any successful pipeline will delete its own context.
_CONTEXT_TTL_SECONDS = 86_400


class PipelineContextStore:
    """Redis-hash backed view of a parent job's pipeline context."""

    def __init__(self, redis: Redis, parent_job_id: str) -> None:
        self._redis = redis
        self._key = f"whisper:ctx:{parent_job_id}"

    @property
    def key(self) -> str:
        return self._key

    def initialize(self, context: dict[str, Any]) -> None:
        """Replace the context with ``context``.

        Used by the dispatcher when a new pipeline is enqueued so that any
        stale hash left over from a previous run is cleared before the first
        stage runs.
        """
        self._redis.delete(self._key)
        if context:
            mapping = {k: pickle.dumps(v) for k, v in context.items()}
            self._redis.hset(self._key, mapping=mapping)
        self._redis.expire(self._key, _CONTEXT_TTL_SECONDS)

    def load(self) -> dict[str, Any]:
        """Load the full context as a Python dict.

        Returns an empty dict when no context has been initialized yet, which
        mirrors the legacy in-process behaviour where a stage receives an
        empty dict before the pipeline has started seeding it.
        """
        raw = self._redis.hgetall(self._key)
        return {self._decode(k): pickle.loads(v) for k, v in raw.items()}

    def update(self, updates: dict[str, Any]) -> None:
        """Write ``updates`` to the hash, replacing any existing fields.

        Empty updates are a no-op so a stage that produces nothing can call
        ``update({})`` unconditionally.
        """
        if not updates:
            return
        mapping = {k: pickle.dumps(v) for k, v in updates.items()}
        self._redis.hset(self._key, mapping=mapping)
        self._redis.expire(self._key, _CONTEXT_TTL_SECONDS)

    def delete(self) -> None:
        """Drop the context hash. Called by the pipeline finalizer."""
        self._redis.delete(self._key)

    @staticmethod
    def _decode(field: bytes | str) -> str:
        if isinstance(field, bytes):
            return field.decode()
        return field


__all__ = ["PipelineContextStore"]
