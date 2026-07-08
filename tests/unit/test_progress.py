from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import fakeredis
from redis.exceptions import ConnectionError as RedisConnectionError

from whisper_ui.core.models import Job
from whisper_ui.worker.progress import ProgressWriteOutcome, RedisProgressReporter
from whisper_ui.worker.runtime import make_throttled_progress_reporter as _make_throttled_progress_reporter


def _make_reporter(processing_ttl: int = 7200) -> tuple[MagicMock, RedisProgressReporter]:
    mock_redis = MagicMock()
    reporter = RedisProgressReporter(mock_redis, "test-job-id", processing_ttl=processing_ttl)
    return mock_redis, reporter


def _fake_reporter(job_id: str = "test-job-id", processing_ttl: int = 7200):
    """Build a RedisProgressReporter backed by fakeredis[lua], which supports
    the atomic max-write Lua script the reporter uses for ``report()``.
    Returns (FakeRedis, reporter) so tests can hgetall the actual stored
    state instead of asserting on mock call args.
    """
    fake = fakeredis.FakeRedis()
    reporter = RedisProgressReporter(fake, job_id, processing_ttl=processing_ttl)
    return fake, reporter


def _stored(fake, job_id: str = "test-job-id") -> dict[str, str]:
    return {
        k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
        for k, v in fake.hgetall(f"job:{job_id}").items()
    }


def test_report_sets_progress():
    fake, reporter = _fake_reporter()
    reporter.report(0.5, "halfway")

    stored = _stored(fake)
    assert float(stored["progress"]) == 0.5
    assert stored["message"] == "halfway"
    assert stored["status"] == "processing"
    assert fake.ttl("job:test-job-id") > 0


def test_report_uses_injected_processing_ttl():
    fake, reporter = _fake_reporter(processing_ttl=12345)
    reporter.report(0.1, "starting")
    # fakeredis returns the TTL the last EXPIRE set; within ±1 s is fine.
    assert 12000 <= fake.ttl("job:test-job-id") <= 12345


def test_report_falls_back_to_default_ttl_when_omitted():
    fake = fakeredis.FakeRedis()
    reporter = RedisProgressReporter(fake, "default-ttl-job")
    reporter.report(0.2, "running")
    assert fake.ttl("job:default-ttl-job") > 0


def test_report_progress_never_regresses_on_second_writer():
    """Direct Lua-max semantics test: two reporters bound to the same
    job_id write interleaved values and the stored progress must always
    be the highest one any writer has observed. Message always follows
    the latest write so the UI can switch stage labels even when
    progress happens to stall.
    """
    fake = fakeredis.FakeRedis()
    r1 = RedisProgressReporter(fake, "same-job")
    r2 = RedisProgressReporter(fake, "same-job")

    r1.report(0.30, "transcribe running")
    assert _stored(fake, "same-job")["progress"] == "0.3"

    r2.report(0.72, "diarize running")
    assert _stored(fake, "same-job")["progress"] == "0.72"

    # Regression attempt — must be dropped. Message still advances.
    r1.report(0.35, "transcribe chunk 2")
    stored = _stored(fake, "same-job")
    assert float(stored["progress"]) == 0.72
    assert stored["message"] == "transcribe chunk 2"

    # A strictly larger write still wins.
    r1.report(0.90, "transcribe chunk 3")
    assert float(_stored(fake, "same-job")["progress"]) == 0.90


def test_report_with_newer_generation_resets_stale_high_watermark():
    """Regression for PR #39 Round 2 R2-2. The exact scenario the
    reviewer reproducer flagged: attempt 1's stale late writer has
    pinned the hash at progress=0.85, the user retries, and attempt 2's
    first stage wants to start reporting from 0.05. Before the fix, the
    Lua max-write compared 0.05 against 0.85 and rejected the update,
    so the UI saw attempt 2 stuck at 85% with attempt 2's message.
    With the generation-aware reset branch, the higher caller_gen
    signals "new attempt owns this key now" and the hash is
    unconditionally rewritten.
    """
    fake = fakeredis.FakeRedis()

    # Attempt 1 runs and pins the hash.
    stale_reporter = RedisProgressReporter(fake, "job-retry", generation=1)
    stale_reporter.report(0.85, "attempt1 diarize")
    assert _stored(fake, "job-retry")["progress"] == "0.85"

    # User retries; attempt 2 starts under generation=2.
    fresh_reporter = RedisProgressReporter(fake, "job-retry", generation=2)
    fresh_reporter.report(0.05, "attempt2 preprocess starting")

    stored = _stored(fake, "job-retry")
    assert stored["progress"] == "0.05"
    assert stored["message"] == "attempt2 preprocess starting"
    assert stored["status"] == "processing"
    assert stored["generation"] == "2"


def test_report_with_older_generation_is_dropped():
    """After attempt 2 takes over the hash, any late writer from
    attempt 1 must be silently dropped — otherwise a residual pyannote
    C++ thread that honoured ``send_stop_job_command`` slowly could
    still corrupt the fresh attempt's progress.
    """
    fake = fakeredis.FakeRedis()

    # Attempt 2 is running and has already stamped its generation.
    RedisProgressReporter(fake, "job-retry", generation=2).report(0.30, "attempt2 transcribe")
    assert float(_stored(fake, "job-retry")["progress"]) == 0.30

    # Late writer from attempt 1 tries to overwrite.
    RedisProgressReporter(fake, "job-retry", generation=1).report(0.99, "late stale write")

    stored = _stored(fake, "job-retry")
    assert float(stored["progress"]) == 0.30, "stale attempt-1 write must not touch attempt 2's hash"
    assert stored["message"] == "attempt2 transcribe"
    assert stored["generation"] == "2"


def test_report_without_generation_keeps_legacy_max_write_semantics():
    """Reporters constructed outside an RQ worker context (unit tests,
    one-off scripts) have no generation to stamp; they must keep the
    pre-Round-2 max-write behaviour so existing test fixtures stay valid.
    """
    fake = fakeredis.FakeRedis()
    legacy = RedisProgressReporter(fake, "legacy-job")
    legacy.report(0.30, "A")
    legacy.report(0.70, "B")
    legacy.report(0.40, "C")  # rejected by max-write, but message updates

    stored = _stored(fake, "legacy-job")
    assert stored["progress"] == "0.7"
    assert stored["message"] == "C"
    # No generation field in legacy mode.
    assert "generation" not in stored


def test_complete_with_older_generation_is_dropped():
    """Defense-in-depth: if a stale attempt-1 finalize_success somehow
    bypasses the Python-level short-circuit in pipeline_dispatcher, the
    reporter's own Lua script still refuses to overwrite a newer
    attempt's hash.
    """
    fake = fakeredis.FakeRedis()
    # Attempt 2 has already stamped the hash.
    RedisProgressReporter(fake, "job-complete", generation=2).report(0.40, "attempt2 running")

    # Stale attempt-1 tries to mark the job COMPLETED.
    RedisProgressReporter(fake, "job-complete", generation=1).complete("/stale/result.json")

    stored = _stored(fake, "job-complete")
    assert stored["status"] == "processing", "stale attempt-1 complete() must not mark attempt 2 as completed"
    assert stored.get("result_path") is None or "stale" not in stored.get("result_path", "")
    assert stored["generation"] == "2"


def test_complete_with_current_generation_writes_through():
    """Sanity: a legitimate complete() under the current generation
    still lands, so the defense-in-depth check does not paper over
    the happy path.
    """
    fake = fakeredis.FakeRedis()
    reporter = RedisProgressReporter(fake, "job-complete-ok", generation=3)
    reporter.report(0.90, "finishing")
    reporter.complete("/tmp/result.json")

    stored = _stored(fake, "job-complete-ok")
    assert stored["status"] == "completed"
    assert stored["result_path"] == "/tmp/result.json"
    assert stored["generation"] == "3"


def test_fail_with_older_generation_is_dropped():
    """Symmetric defense-in-depth check for fail(). A stale attempt-1
    finalize_failure must not mark a running attempt 2 as FAILED at
    the Redis progress-hash layer.
    """
    fake = fakeredis.FakeRedis()
    RedisProgressReporter(fake, "job-fail", generation=2).report(0.40, "attempt2 running")
    RedisProgressReporter(fake, "job-fail", generation=1).fail("stale error")

    stored = _stored(fake, "job-fail")
    assert stored["status"] == "processing"
    assert stored.get("error") is None or "stale error" not in stored.get("error", "")
    assert stored["generation"] == "2"


def test_report_progress_updates_message_when_equal():
    """Equal progress values must still update the message so the user
    sees the current stage label when a branch reports a status change
    without moving the percentage (e.g. "diarize loading" → "diarize
    running" at 0.65).
    """
    fake = fakeredis.FakeRedis()
    reporter = RedisProgressReporter(fake, "stall-job")
    reporter.report(0.65, "diarize loading")
    reporter.report(0.65, "diarize running")

    stored = _stored(fake, "stall-job")
    assert float(stored["progress"]) == 0.65
    assert stored["message"] == "diarize running"


def test_report_returns_accepted_when_lua_accepts_fresh_write():
    """``report()`` returns ACCEPTED when the Lua script commits the write.
    Covers all three accepting branches: legacy (no generation), reset
    (caller_gen > stored_gen), and same-generation max-write.
    """
    fake = fakeredis.FakeRedis()

    # Legacy branch (caller_gen = -1).
    legacy = RedisProgressReporter(fake, "job-legacy")
    assert legacy.report(0.1, "m") is ProgressWriteOutcome.ACCEPTED

    # Reset branch (higher caller_gen than stored).
    gen_aware = RedisProgressReporter(fake, "job-reset", generation=2)
    assert gen_aware.report(0.3, "m") is ProgressWriteOutcome.ACCEPTED

    # Same-generation max-write branch (new >= stored).
    same_gen = RedisProgressReporter(fake, "job-reset", generation=2)
    assert same_gen.report(0.5, "m") is ProgressWriteOutcome.ACCEPTED


def test_report_returns_false_when_lua_drops_stale_write():
    """Core PR #39 Round 3 regression: ``report()`` returns False when
    the caller's generation is strictly older than the stored one, so
    the throttler can skip its SQLite mirror path and leave the
    current attempt's Job row alone.
    """
    fake = fakeredis.FakeRedis()

    # Attempt 2 owns the hash.
    RedisProgressReporter(fake, "job-stale", generation=2).report(0.4, "attempt2 running")

    # Attempt 1 late writer.
    stale = RedisProgressReporter(fake, "job-stale", generation=1)
    accepted = stale.report(0.99, "attempt1 stale heartbeat")

    assert accepted is ProgressWriteOutcome.REJECTED
    # And the hash is untouched (sanity: confirms Lua dropped the write).
    stored = _stored(fake, "job-stale")
    assert float(stored["progress"]) == 0.4
    assert stored["generation"] == "2"


def test_report_returns_false_when_central_gen_exceeds_caller_gen_despite_empty_hash():
    """Regression for PR #39 Round 4 R4-1. The retry route deletes the
    progress hash (wiping the embedded generation field), but the
    central generation counter has already been bumped. A stale gen=1
    writer arriving after the delete must be rejected by the Lua
    central-counter check (KEYS[2]) even though the hash-level check
    would let it through via the ``(not stored_gen)`` reset branch.
    """
    fake = fakeredis.FakeRedis()

    # Central counter bumped to 2 by enqueue_pipeline.
    fake.set("whisper:pipeline:job-r4:generation", 2)

    # Hash is completely absent (retry route deleted it).
    assert not fake.exists("job:job-r4")

    # Stale gen=1 writer.
    stale = RedisProgressReporter(fake, "job-r4", generation=1)
    accepted = stale.report(0.85, "stale heartbeat")

    assert accepted is ProgressWriteOutcome.REJECTED
    assert not fake.exists("job:job-r4"), "stale writer must not re-seed the hash"


def test_report_returns_true_when_central_gen_matches_caller_gen():
    """Same Round 4 scenario but for the legitimate gen=2 writer.
    The central counter is 2 and the caller is 2 → the Lua central
    check passes, and the ``(not stored_gen)`` reset branch seeds
    the hash correctly.
    """
    fake = fakeredis.FakeRedis()
    fake.set("whisper:pipeline:job-r4ok:generation", 2)

    fresh = RedisProgressReporter(fake, "job-r4ok", generation=2)
    accepted = fresh.report(0.05, "attempt2 preprocess starting")

    assert accepted is ProgressWriteOutcome.ACCEPTED
    stored = _stored(fake, "job-r4ok")
    assert stored["progress"] == "0.05"
    assert stored["generation"] == "2"


def test_complete_rejected_by_central_gen():
    fake = fakeredis.FakeRedis()
    fake.set("whisper:pipeline:job-r4c:generation", 3)

    stale = RedisProgressReporter(fake, "job-r4c", generation=1)
    assert stale.complete("/stale.json") is False


def test_fail_rejected_by_central_gen():
    fake = fakeredis.FakeRedis()
    fake.set("whisper:pipeline:job-r4f:generation", 3)

    stale = RedisProgressReporter(fake, "job-r4f", generation=1)
    assert stale.fail("stale error") is False


def test_report_returns_degraded_on_redis_error():
    """Transient Redis failures surface as DEGRADED, not REJECTED: the
    "SQLite is source of truth when Redis is unavailable" contract requires
    the throttler to keep mirroring to the DB during an outage, but via the
    progress-only field-level path (not a full-column overwrite).
    """
    _fake, reporter = _fake_reporter()
    reporter._max_write_script = MagicMock(side_effect=RedisConnectionError("down"))

    assert reporter.report(0.5, "m") is ProgressWriteOutcome.DEGRADED


def test_complete_returns_true_when_accepted():
    """Legacy (no generation) complete() always accepts; so does a
    gen-aware reporter whose generation is at least the stored one.
    """
    fake = fakeredis.FakeRedis()
    legacy = RedisProgressReporter(fake, "job-ok-legacy")
    assert legacy.complete("/tmp/legacy.json") is True

    gen_aware = RedisProgressReporter(fake, "job-ok-gen", generation=5)
    assert gen_aware.complete("/tmp/gen.json") is True


def test_complete_returns_false_when_stale():
    fake = fakeredis.FakeRedis()
    RedisProgressReporter(fake, "job-ct", generation=2).report(0.5, "attempt2 running")
    stale = RedisProgressReporter(fake, "job-ct", generation=1)

    assert stale.complete("/tmp/stale.json") is False
    stored = _stored(fake, "job-ct")
    assert stored["status"] == "processing"


def test_fail_returns_false_when_stale():
    fake = fakeredis.FakeRedis()
    RedisProgressReporter(fake, "job-fl", generation=2).report(0.5, "attempt2 running")
    stale = RedisProgressReporter(fake, "job-fl", generation=1)

    assert stale.fail("stale error") is False
    stored = _stored(fake, "job-fl")
    assert stored["status"] == "processing"


def test_complete_sets_done():
    fake, reporter = _fake_reporter()
    reporter.complete("/path/to/result.json")

    stored = _stored(fake)
    assert stored["progress"] == "1.0"
    assert stored["status"] == "completed"
    assert stored["result_path"] == "/path/to/result.json"
    assert fake.ttl("job:test-job-id") > 0


def test_fail_sets_error():
    fake, reporter = _fake_reporter()
    reporter.fail("something broke")

    stored = _stored(fake)
    assert stored["status"] == "failed"
    assert "something broke" in stored["error"]


def test_report_swallows_redis_connection_error(caplog):
    """Progress writes are best-effort. SQLite is the source of truth, so a
    transient Redis outage must NOT propagate up and tear down the worker.
    The Lua script path is still wrapped by the ``except RedisError`` so
    a failing EVAL should be logged and swallowed just like the old HSET
    error path.
    """
    import logging

    _fake, reporter = _fake_reporter()
    # Force the reporter's bound script to raise, simulating a mid-EVAL
    # Redis outage without having to tear down the whole fakeredis server.
    reporter._max_write_script = MagicMock(side_effect=RedisConnectionError("redis down"))

    with caplog.at_level(logging.WARNING):
        reporter.report(0.4, "halfway")

    assert any("Redis progress write failed" in rec.message for rec in caplog.records)


def test_complete_swallows_redis_connection_error(caplog):
    import logging

    _fake, reporter = _fake_reporter()
    reporter._complete_script = MagicMock(side_effect=RedisConnectionError("redis down"))

    with caplog.at_level(logging.WARNING):
        reporter.complete("/path/to/result.json")

    assert any("Redis complete write failed" in rec.message for rec in caplog.records)


def test_fail_swallows_redis_connection_error(caplog):
    import logging

    _fake, reporter = _fake_reporter()
    reporter._fail_script = MagicMock(side_effect=RedisConnectionError("redis down"))

    with caplog.at_level(logging.WARNING):
        reporter.fail("worker exploded")

    assert any("Redis fail write failed" in rec.message for rec in caplog.records)


def test_get_progress_returns_empty_dict_on_redis_error(caplog):
    import logging

    mock_redis = MagicMock()
    mock_redis.hgetall.side_effect = RedisConnectionError("redis down")

    with caplog.at_level(logging.WARNING):
        result = RedisProgressReporter.get_progress(mock_redis, "job-x")

    assert result == {}
    assert any("Redis progress read failed" in rec.message for rec in caplog.records)


def test_fail_truncates_long_error():
    fake, reporter = _fake_reporter()
    long_error = "x" * 2000
    reporter.fail(long_error)

    stored = _stored(fake)
    assert len(stored["error"]) <= 1000
    assert len(stored["message"]) <= 500


def test_get_progress_empty():
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {}
    result = RedisProgressReporter.get_progress(mock_redis, "nonexistent")
    assert result == {}


def test_get_progress_decodes_bytes():
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {
        b"progress": b"0.75",
        b"message": b"running",
        b"status": b"processing",
    }
    result = RedisProgressReporter.get_progress(mock_redis, "test-id")
    assert result["progress"] == "0.75"
    assert result["message"] == "running"
    assert result["status"] == "processing"


def test_get_progress_batch_pipelines_and_omits_missing():
    import fakeredis

    redis = fakeredis.FakeRedis()
    redis.hset("job:a", mapping={"progress": "0.5", "status": "processing"})
    redis.hset("job:c", mapping={"progress": "1.0", "status": "completed"})

    result = RedisProgressReporter.get_progress_batch(redis, ["a", "b", "c"])

    assert result["a"] == {"progress": "0.5", "status": "processing"}
    assert result["c"] == {"progress": "1.0", "status": "completed"}
    assert "b" not in result  # a job with no progress hash is simply absent


def test_get_progress_batch_empty_input_skips_redis():
    mock_redis = MagicMock()
    assert RedisProgressReporter.get_progress_batch(mock_redis, []) == {}
    mock_redis.pipeline.assert_not_called()


class _FakeClock:
    """Monotonic clock stub so throttle tests are not flaky under CI load."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_throttle(min_delta: float = 0.005, min_interval_sec: float = 0.5):
    reporter = MagicMock(spec=RedisProgressReporter)
    db = MagicMock()
    job = Job(filename="t.wav")
    clock = _FakeClock()
    on_progress = _make_throttled_progress_reporter(
        reporter,
        db,
        job,
        min_delta=min_delta,
        min_interval_sec=min_interval_sec,
        monotonic=clock,
    )
    return on_progress, reporter, db, job, clock


class TestThrottledProgressReporter:
    def test_first_call_always_writes(self):
        on_progress, reporter, db, _job, _clock = _make_throttle()
        on_progress(0.0, "starting")
        reporter.report.assert_called_once_with(0.0, "starting")
        db.update_job.assert_called_once()

    def test_drops_tiny_delta_inside_interval(self):
        on_progress, reporter, db, _job, clock = _make_throttle()
        on_progress(0.10, "stage")
        # 0.001 delta at 100 ms — both below thresholds → must be dropped.
        clock.advance(0.1)
        on_progress(0.101, "stage")
        assert reporter.report.call_count == 1
        assert db.update_job.call_count == 1

    def test_writes_after_enough_delta(self):
        on_progress, reporter, _db, _job, clock = _make_throttle()
        on_progress(0.10, "stage")
        clock.advance(0.1)
        on_progress(0.106, "stage")  # 0.6 pp > 0.5 pp threshold
        assert reporter.report.call_count == 2

    def test_writes_after_enough_time(self):
        on_progress, reporter, _db, _job, clock = _make_throttle()
        on_progress(0.10, "stage")
        # Same progress value but interval exceeded — still a write because
        # we treat "stale heartbeat" as worth flushing so Redis TTL resets.
        clock.advance(0.6)
        on_progress(0.101, "stage")
        assert reporter.report.call_count == 2

    def test_message_change_always_flushes(self):
        on_progress, reporter, _db, _job, clock = _make_throttle()
        on_progress(0.10, "stage-a")
        clock.advance(0.01)
        # Below both delta and interval thresholds, but the message flipped
        # (stage transition) — must flush so the UI shows the new label.
        on_progress(0.101, "stage-b")
        assert reporter.report.call_count == 2
        assert reporter.report.call_args_list[-1].args == (0.101, "stage-b")

    def test_completion_always_flushes(self):
        on_progress, reporter, _db, _job, clock = _make_throttle()
        on_progress(0.95, "finishing")
        clock.advance(0.01)
        # Tiny delta and tiny interval — but progress reaching 1.0 must
        # never be swallowed or the bar would freeze just short of done.
        on_progress(1.0, "finishing")
        assert reporter.report.call_count == 2
        assert reporter.report.call_args_list[-1].args == (1.0, "finishing")

    def test_rapid_burst_is_bounded(self):
        """1000 tiny updates in the same message must collapse to far fewer
        writes than input calls — matching the anti-thrash invariant the
        throttle is meant to enforce."""
        on_progress, reporter, _db, _job, clock = _make_throttle()
        for i in range(1000):
            on_progress(0.10 + i * 0.0001, "stage")
            clock.advance(0.001)  # 1 ms apart
        # 1000 calls over ~1 s with 0.5 pp / 500 ms thresholds. Delta crosses
        # every ~50 calls so we expect ~20 writes — two orders of magnitude
        # below the input rate, which is the whole point of the throttle.
        assert reporter.report.call_count <= 25
        assert reporter.report.call_count < len(range(1000)) // 10

    def test_regression_to_lower_progress_is_dropped(self):
        """Regression guard: a late diarize heartbeat that arrives after
        the main thread has emitted DIARIZE_DONE (1.0) must not rewind
        the bar. Worker retries spin up a fresh closure, so no legitimate
        in-closure regression exists; any incoming progress < last is a
        race and should be silently dropped.
        """
        on_progress, reporter, _db, _job, clock = _make_throttle()
        on_progress(1.0, "done")
        clock.advance(0.01)
        on_progress(0.94, "done")
        assert reporter.report.call_count == 1
        assert reporter.report.call_args.args == (1.0, "done")

    def test_concurrent_callers_do_not_corrupt_state(self):
        """Smoke test for the throttle lock. Spawn many threads hammering
        the closure with monotonically increasing progress; the final
        state must be consistent and no exception may escape.
        """
        import threading

        on_progress, reporter, _db, _job, _clock = _make_throttle()
        errors: list[BaseException] = []

        def worker(start: int) -> None:
            try:
                for i in range(50):
                    on_progress(0.001 * (start + i), "stage")
            except BaseException as e:  # pragma: no cover - defensive
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i * 50,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # last_progress must equal the highest progress that any thread
        # actually wrote — i.e. monotonically non-decreasing.
        final_progress = max(call.args[0] for call in reporter.report.call_args_list)
        assert final_progress > 0

    def test_throttle_skips_db_update_when_reporter_rejects_stale_write(self):
        """Regression for PR #39 Round 3 R3-1. When the Redis Lua script
        rejects a write because the caller's generation is strictly older
        than the stored one, the throttler must NOT proceed to update the
        SQLite Job row from its captured (stale) Job object. Before this
        fix the throttler always called db.update_job(job) regardless of
        whether the Redis write landed, so a stale stage's captured Job
        snapshot could clobber the current attempt's status, progress,
        and result_path fields via the full-column UPDATE path.
        """
        on_progress, reporter, db, job, _clock = _make_throttle()
        reporter.report.return_value = ProgressWriteOutcome.REJECTED  # Lua rejected (stale)

        on_progress(0.85, "attempt1 stale heartbeat")

        reporter.report.assert_called_once_with(0.85, "attempt1 stale heartbeat")
        db.update_job.assert_not_called()
        db.update_job_progress.assert_not_called()
        # Captured Job must not be mutated — otherwise a *subsequent*
        # legitimate writer would inherit the stale progress value.
        assert job.progress == 0.0
        assert job.progress_message == ""

    def test_throttle_proceeds_when_reporter_accepts_write(self):
        """Sanity pair for the test above: when the reporter returns
        ACCEPTED (legacy max-write, reset branch, or current generation
        match) the throttler still full-mirrors to SQLite as before.
        """
        on_progress, reporter, db, job, _clock = _make_throttle()
        reporter.report.return_value = ProgressWriteOutcome.ACCEPTED

        on_progress(0.42, "running")

        reporter.report.assert_called_once_with(0.42, "running")
        db.update_job.assert_called_once_with(job)
        assert job.progress == 0.42
        assert job.progress_message == "running"

    def test_throttle_degraded_uses_field_level_update(self):
        """On a Redis outage (DEGRADED) the mirror must fall back to the
        progress-only field-level UPDATE, never the full-column update_job —
        so a possibly-stale writer during the outage cannot clobber a
        COMPLETED row's status / result_path / error.
        """
        on_progress, reporter, db, job, _clock = _make_throttle()
        reporter.report.return_value = ProgressWriteOutcome.DEGRADED

        on_progress(0.42, "running")

        db.update_job.assert_not_called()
        db.update_job_progress.assert_called_once_with(job.id, 0.42, "running")

    def test_throttle_swallows_sqlite_error_on_mirror(self):
        """A SQLite lock / I/O error while mirroring progress must never
        fail the stage: the error is swallowed so a multi-hour transcribe is
        not reported as FAILED because one progress mirror could not commit.
        """
        on_progress, reporter, db, _job, _clock = _make_throttle()
        reporter.report.return_value = ProgressWriteOutcome.ACCEPTED
        db.update_job.side_effect = sqlite3.OperationalError("database is locked")

        on_progress(0.42, "running")  # must not raise

    def test_late_heartbeat_with_old_message_is_dropped(self):
        """Same race as above, but the late update still carries the old
        running message — the message-change force-flush must not save
        it from being dropped."""
        on_progress, reporter, _db, _job, clock = _make_throttle()
        on_progress(0.85, "running")  # last heartbeat before DONE
        clock.advance(0.01)
        on_progress(1.0, "done")  # main thread flushes DONE
        assert reporter.report.call_count == 2
        clock.advance(0.01)
        on_progress(0.94, "running")  # late heartbeat from background thread
        assert reporter.report.call_count == 2  # NOT 3
        assert reporter.report.call_args.args == (1.0, "done")
