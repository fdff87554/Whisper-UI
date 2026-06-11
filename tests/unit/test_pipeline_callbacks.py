"""Edge-branch coverage for worker/pipeline_callbacks helpers.

The happy paths are exercised end-to-end by the dispatcher tests; these
pin the tolerance branches that only trigger on malformed RQ meta or
missing generation counters.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from rq.timeouts import JobTimeoutException

from whisper_ui.worker.pipeline_callbacks import (
    extract_meta_generation,
    format_failure_message,
    is_stale_callback,
)


class TestExtractMetaGeneration:
    def test_none_job_returns_none(self):
        assert extract_meta_generation(None) is None

    def test_job_without_meta_returns_none(self):
        job = MagicMock()
        job.meta = None
        assert extract_meta_generation(job) is None

    def test_meta_missing_generation_key_returns_none(self):
        job = MagicMock()
        job.meta = {"parent_job_id": "p"}
        assert extract_meta_generation(job) is None

    def test_non_numeric_generation_returns_none(self):
        # A corrupted / hand-edited meta value must degrade to "untracked",
        # not crash the failure callback that is cleaning up a dead job.
        job = MagicMock()
        job.meta = {"generation": "not-a-number"}
        assert extract_meta_generation(job) is None

    def test_unconvertible_type_returns_none(self):
        job = MagicMock()
        job.meta = {"generation": ["3"]}
        assert extract_meta_generation(job) is None

    def test_numeric_string_generation_converts(self):
        job = MagicMock()
        job.meta = {"generation": "3"}
        assert extract_meta_generation(job) == 3


class TestIsStaleCallback:
    def test_unknown_meta_generation_is_not_stale(self):
        assert is_stale_callback(5, None) is False

    def test_missing_central_counter_is_not_stale(self):
        # Counter TTL expiry must fail open: by then the context/progress
        # keys have expired too, so there is no stale state to protect.
        assert is_stale_callback(None, 3) is False

    def test_both_unknown_is_not_stale(self):
        assert is_stale_callback(None, None) is False

    def test_older_meta_is_stale(self):
        assert is_stale_callback(2, 1) is True

    def test_equal_generation_is_current(self):
        assert is_stale_callback(2, 2) is False


class TestFormatFailureMessage:
    def test_timeout_class_uses_chinese_label(self):
        exc = JobTimeoutException("Task exceeded maximum timeout value (90 seconds)")
        msg = format_failure_message(JobTimeoutException, exc)
        assert "90" in msg
        assert "上限" in msg

    def test_timeout_class_without_instance_uses_placeholder(self):
        msg = format_failure_message(JobTimeoutException, None)
        assert "?" in msg

    def test_plain_exception_uses_str(self):
        assert format_failure_message(RuntimeError, RuntimeError("boom")) == "boom"

    def test_no_information_falls_back(self):
        assert format_failure_message(None, None) == "unknown pipeline failure"
