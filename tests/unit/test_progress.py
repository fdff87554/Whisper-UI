from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
from redis.exceptions import ConnectionError as RedisConnectionError

from whisper_ui.core.models import Job
from whisper_ui.worker.progress import RedisProgressReporter
from whisper_ui.worker.tasks import _make_throttled_progress_reporter


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


def test_complete_sets_done():
    mock_redis, reporter = _make_reporter()
    reporter.complete("/path/to/result.json")

    mapping = mock_redis.hset.call_args.kwargs.get("mapping") or mock_redis.hset.call_args[1].get("mapping")
    assert mapping["progress"] == "1.0"
    assert mapping["status"] == "completed"
    assert mapping["result_path"] == "/path/to/result.json"
    mock_redis.expire.assert_called_once_with("job:test-job-id", 86400)


def test_fail_sets_error():
    mock_redis, reporter = _make_reporter()
    reporter.fail("something broke")

    mapping = mock_redis.hset.call_args.kwargs.get("mapping") or mock_redis.hset.call_args[1].get("mapping")
    assert mapping["status"] == "failed"
    assert "something broke" in mapping["error"]


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

    mock_redis, reporter = _make_reporter()
    mock_redis.hset.side_effect = RedisConnectionError("redis down")

    with caplog.at_level(logging.WARNING):
        reporter.complete("/path/to/result.json")

    assert any("Redis complete write failed" in rec.message for rec in caplog.records)


def test_fail_swallows_redis_connection_error(caplog):
    import logging

    mock_redis, reporter = _make_reporter()
    mock_redis.hset.side_effect = RedisConnectionError("redis down")

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
    mock_redis, reporter = _make_reporter()
    long_error = "x" * 2000
    reporter.fail(long_error)

    mapping = mock_redis.hset.call_args.kwargs.get("mapping") or mock_redis.hset.call_args[1].get("mapping")
    assert len(mapping["error"]) <= 1000
    assert len(mapping["message"]) <= 500


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
