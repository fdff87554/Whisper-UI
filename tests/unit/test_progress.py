from __future__ import annotations

from unittest.mock import MagicMock

from whisper_ui.worker.progress import RedisProgressReporter


def _make_reporter() -> tuple[MagicMock, RedisProgressReporter]:
    mock_redis = MagicMock()
    reporter = RedisProgressReporter(mock_redis, "test-job-id")
    return mock_redis, reporter


def test_report_sets_progress():
    mock_redis, reporter = _make_reporter()
    reporter.report(0.5, "halfway")

    mock_redis.hset.assert_called_once()
    call_kwargs = mock_redis.hset.call_args
    mapping = call_kwargs.kwargs.get("mapping") or call_kwargs[1].get("mapping")
    assert mapping["progress"] == "0.5"
    assert mapping["message"] == "halfway"
    assert mapping["status"] == "processing"
    mock_redis.expire.assert_called_once_with("job:test-job-id", 7200)


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
    mock_redis.expire.assert_called_once_with("job:test-job-id", 86400)


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
