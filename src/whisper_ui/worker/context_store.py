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

Serialization choice and threat model
-------------------------------------

Using ``pickle`` for values is an explicit, documented trade-off against
switching to JSON or msgpack. The reasoning is layered:

1. **Deployment target is internal network only.** Whisper-UI is shipped as
   a docker-compose stack intended to run on a trusted internal host; the
   README explicitly does not claim support for public / multi-tenant
   deployment. The compose file configures ``REDIS_PASSWORD`` and binds
   Redis to the internal network only, so an attacker reaching the Redis
   hash at all already implies a prior compromise inside that network.
   At that point a pickle RCE gadget only promotes attacker-on-Redis to
   attacker-on-worker — the attack surface is not novel, merely wider by
   one hop.

2. **Context payloads contain whisperx internals.** The rich fields
   (``transcription_result``, ``aligned_result``, ``diarize_result``,
   ``final_result``) are nested Python dicts with numpy arrays and pandas
   frames in the middle. JSON does not round-trip numpy cleanly; msgpack
   round-trips only with a numpy extension, and either choice would force
   every stage to maintain a custom ``to_dict`` / ``from_dict`` layer. The
   audit cost of that migration is large, and every new stage would have
   to stay in sync with it.

3. **No alternative is strictly safer in the current threat model.** The
   only meaningful threat model shift would be exposing Redis to an
   untrusted network. If that ever happens, the right response is a
   deployment hardening pass (Redis auth, TLS, network segmentation),
   alongside a serializer migration — not a serializer migration alone.

**Upgrade path.** When/if the deployment story opens up to public
networks, replace ``pickle.dumps`` / ``pickle.loads`` with
``msgpack-numpy`` and audit every ``output_keys`` tuple in
``worker/stage_tasks.py`` to make sure the payloads round-trip
losslessly. Generation-gated writes are orthogonal and will continue to
work unchanged because the Lua script only sees opaque bytes.

Generation-gated writes
-----------------------

A separate per-parent-job counter (``whisper:pipeline:{parent}:generation``)
is bumped on every ``enqueue_pipeline`` call. Each sub-job is enqueued with
the current generation embedded in its RQ meta, and
``update_if_generation_matches`` refuses to write when the caller's
generation no longer matches the one stored in Redis.

This exists to isolate retries: if a user retries a failed job while a
lingering stage from the previous attempt (e.g. a diarize call deep inside
a pyannote C++ inference that did not yet honour ``send_stop_job_command``)
is still running, that stale stage must not be able to write its stale
output into the fresh context hash the retry just seeded. Generation gating
closes that window server-side via a Lua compare-and-write script.
"""

from __future__ import annotations

# ``pickle`` is deliberate — see "Serialization choice and threat model" in
# the module docstring for the full rationale. The internal-network-only
# deployment story plus authenticated Redis means the pickle attack surface
# already requires a prior internal compromise, and the context payloads
# (whisperx nested dicts with numpy arrays) do not round-trip cleanly
# through safer serializers without a large per-stage audit.
import pickle
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis import Redis


# One-day expiry is a safety net for abandoned contexts (worker killed before
# the finalizer ran). Any successful pipeline will delete its own context.
_CONTEXT_TTL_SECONDS = 86_400


# Atomic generation-gated HSET: write the pickled fields only if the stored
# generation counter matches the caller's expected generation. Returns 1 on
# success, 0 if the write was rejected because the caller's generation was
# stale (the parent job has been retried under a new generation).
#
# KEYS[1] = context hash key (whisper:ctx:<parent>)
# KEYS[2] = generation counter key (whisper:pipeline:<parent>:generation)
# ARGV[1] = expected generation (int as string)
# ARGV[2] = TTL in seconds
# ARGV[3..] = alternating field/value pairs for the HSET
_GENERATION_GATED_HSET_LUA = """
local ctx_key = KEYS[1]
local gen_key = KEYS[2]
local expected = ARGV[1]
local ttl = tonumber(ARGV[2])
local current = redis.call('GET', gen_key)
if current and current ~= expected then
  return 0
end
local pairs_count = #ARGV - 2
if pairs_count >= 2 then
  local args = {}
  for i = 3, #ARGV do
    args[#args + 1] = ARGV[i]
  end
  redis.call('HSET', ctx_key, unpack(args))
end
redis.call('EXPIRE', ctx_key, ttl)
return 1
"""


class PipelineContextStore:
    """Redis-hash backed view of a parent job's pipeline context.

    The store does **not** own the generation counter lifecycle — the
    dispatcher INCRs it when a pipeline is enqueued and the store only
    reads it. This separation keeps the context store a plain data layer
    while the dispatcher remains the single place that decides when a
    retry starts a new attempt.
    """

    def __init__(self, redis: Redis, parent_job_id: str) -> None:
        self._redis = redis
        self._key = f"whisper:ctx:{parent_job_id}"
        self._generation_key = f"whisper:pipeline:{parent_job_id}:generation"
        self._gated_hset_script = redis.register_script(_GENERATION_GATED_HSET_LUA)

    @property
    def key(self) -> str:
        return self._key

    @property
    def generation_key(self) -> str:
        return self._generation_key

    def initialize(self, context: dict[str, Any]) -> None:
        """Replace the context with ``context``.

        Used by the dispatcher when a new pipeline is enqueued so that any
        stale hash left over from a previous run is cleared before the first
        stage runs. Does **not** touch the generation counter — that is the
        dispatcher's responsibility.
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
        # pickle.loads is safe in this codebase only under the threat model
        # documented at the top of this module. Do not change this call
        # site without also updating that docstring.
        return {self._decode(k): pickle.loads(v) for k, v in raw.items()}

    def update(self, updates: dict[str, Any]) -> None:
        """Write ``updates`` to the hash unconditionally.

        Used only for seeding keys from the dispatcher / pre-context hooks
        that run before any sub-job meta exists. Stage tasks must prefer
        :meth:`update_if_generation_matches` so their writes can be rejected
        when the parent job has been retried under a new generation.

        Empty updates are a no-op so a stage that produces nothing can call
        ``update({})`` unconditionally.
        """
        if not updates:
            return
        mapping = {k: pickle.dumps(v) for k, v in updates.items()}
        self._redis.hset(self._key, mapping=mapping)
        self._redis.expire(self._key, _CONTEXT_TTL_SECONDS)

    def update_if_generation_matches(
        self,
        updates: dict[str, Any],
        expected_generation: int,
    ) -> bool:
        """Write ``updates`` only if the parent generation still matches.

        Returns True when the write committed, False when it was rejected
        because another enqueue_pipeline call has since bumped the
        generation. Callers should treat a False return as "this attempt
        has been superseded" and stop mutating shared state; the stage
        itself has already run and its work is simply discarded.

        Empty updates still go through the Lua script so the TTL is
        refreshed and the generation check runs — this keeps the
        invariant that any successful stage execution either commits its
        output or discovers the retry, with no silent middle ground.
        """
        serialized_pairs: list[str | bytes] = []
        for field, value in updates.items():
            serialized_pairs.append(field)
            serialized_pairs.append(pickle.dumps(value))
        result = self._gated_hset_script(
            keys=[self._key, self._generation_key],
            args=[str(expected_generation), _CONTEXT_TTL_SECONDS, *serialized_pairs],
        )
        return int(result) == 1

    def get_generation(self) -> int | None:
        """Return the current generation, or None if none has been set yet."""
        raw = self._redis.get(self._generation_key)
        if raw is None:
            return None
        return int(raw)

    def delete(self) -> None:
        """Drop the context hash. Called by the pipeline finalizer.

        Intentionally does *not* delete the generation counter: if a late
        retry comes in while a stale stage is mid-execution, we want the
        counter to still be visible so the stale stage's
        update_if_generation_matches call can see the new value.
        """
        self._redis.delete(self._key)

    @staticmethod
    def _decode(field: bytes | str) -> str:
        if isinstance(field, bytes):
            return field.decode()
        return field


__all__ = ["PipelineContextStore"]
