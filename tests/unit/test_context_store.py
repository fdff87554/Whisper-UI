from __future__ import annotations

import fakeredis

from whisper_ui.worker.context_store import PipelineContextStore


def _store() -> PipelineContextStore:
    return PipelineContextStore(fakeredis.FakeRedis(), "job-1")


def test_initialize_replaces_previous_context():
    store = _store()
    store.initialize({"language": "zh", "batch_size": 16})
    store.initialize({"language": "en"})

    loaded = store.load()
    assert loaded == {"language": "en"}


def test_update_merges_fields_without_clobbering_others():
    store = _store()
    store.initialize({"language": "zh", "num_speakers": 2})
    store.update({"audio_path": "/tmp/a.wav"})

    loaded = store.load()
    assert loaded == {
        "language": "zh",
        "num_speakers": 2,
        "audio_path": "/tmp/a.wav",
    }


def test_update_with_empty_dict_is_a_noop():
    store = _store()
    store.initialize({"language": "zh"})
    store.update({})

    assert store.load() == {"language": "zh"}


def test_disjoint_updates_from_parallel_branches_are_both_preserved():
    store = _store()
    store.initialize({"audio_path": "/tmp/a.wav"})

    store.update({"transcription_result": {"segments": [1]}})
    store.update({"diarize_result": [("SPK0", 0.0, 1.0)]})

    loaded = store.load()
    assert loaded["transcription_result"] == {"segments": [1]}
    assert loaded["diarize_result"] == [("SPK0", 0.0, 1.0)]
    assert loaded["audio_path"] == "/tmp/a.wav"


def test_load_returns_empty_dict_for_uninitialized_context():
    assert _store().load() == {}


def test_delete_removes_all_fields():
    store = _store()
    store.initialize({"a": 1, "b": 2})
    store.delete()
    assert store.load() == {}


def test_update_if_generation_matches_commits_when_counter_absent():
    """Before the dispatcher has ever bumped the generation counter
    (e.g. a brand-new pipeline in flight from the old monolithic path),
    the gated write should behave like the unconditional update so the
    stage output is not silently lost.
    """
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-1")
    store.initialize({"audio_path": "/tmp/a.wav"})

    committed = store.update_if_generation_matches({"diarize_result": [("SPK0", 0.0, 1.0)]}, 1)

    assert committed is True
    assert store.load()["diarize_result"] == [("SPK0", 0.0, 1.0)]


def test_update_if_generation_matches_commits_on_current_generation():
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-2")
    store.initialize({"audio_path": "/tmp/a.wav"})
    redis.set(store.generation_key, 3)

    committed = store.update_if_generation_matches({"transcription_result": {"segments": []}}, 3)

    assert committed is True
    loaded = store.load()
    assert loaded["transcription_result"] == {"segments": []}
    assert loaded["audio_path"] == "/tmp/a.wav"


def test_update_if_generation_matches_drops_stale_write():
    """Core retry-isolation invariant: a stage that was enqueued under
    an older generation must not be able to write its output into the
    context after a retry has bumped the counter. This is what prevents
    a still-running diarize from a previous attempt from polluting a
    fresh retry's Redis hash.
    """
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-3")
    store.initialize({"audio_path": "/tmp/a.wav"})
    redis.set(store.generation_key, 2)

    committed = store.update_if_generation_matches({"diarize_result": ["stale!"]}, 1)

    assert committed is False
    assert "diarize_result" not in store.load()


def test_get_generation_returns_current_value():
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-4")
    assert store.get_generation() is None
    redis.set(store.generation_key, 7)
    assert store.get_generation() == 7


def test_delete_keeps_generation_counter_visible_to_late_writers():
    """The finalizer drops the context hash but must leave the
    generation counter in place so a late writer from a previous attempt
    can still observe the new generation after a retry and drop its write.
    """
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-5")
    store.initialize({"audio_path": "/tmp/a.wav"})
    redis.set(store.generation_key, 5)

    store.delete()

    assert store.load() == {}
    assert store.get_generation() == 5


def test_update_if_generation_matches_when_key_missing_accepts_write():
    """When the generation counter has expired (PIPELINE_STATE_TTL_SECONDS
    is a 24h safety net), the Lua script treats a missing counter as
    "no gating" and accepts the write. This regression pin documents
    that intentional behaviour: any production-relevant attempt has the
    counter set well within the TTL, so reaching this branch implies a
    pathological delay (worker hung > 24h after the parent finalised).
    """
    redis = fakeredis.FakeRedis()
    store = PipelineContextStore(redis, "job-ttl-expired")
    store.initialize({"audio_path": "/tmp/a.wav"})
    # No SET on generation_key — simulates TTL having already elapsed.
    assert redis.exists(store.generation_key) == 0

    committed = store.update_if_generation_matches(
        {"transcription_result": {"segments": []}},
        expected_generation=3,
    )

    assert committed is True
    assert "transcription_result" in store.load()
