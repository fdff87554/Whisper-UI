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
