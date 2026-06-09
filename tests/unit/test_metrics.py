"""Tests for the Prometheus /metrics collector."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
from prometheus_client import CollectorRegistry, generate_latest

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.storage.database import JobDatabase
from whisper_ui.web.routes.metrics import WhisperCollector


def _seed_jobs(db: JobDatabase, statuses: list[JobStatus]) -> None:
    for status in statuses:
        job = Job(filename="x.mp3", filepath="/tmp/x.mp3")
        job.status = status
        db.insert_job(job)


def _scrape(db: JobDatabase, redis) -> str:
    registry = CollectorRegistry()
    registry.register(WhisperCollector(db, redis))
    return generate_latest(registry).decode()


def test_whisper_collector_reports_status_counts_and_queue_depth(tmp_path):
    from rq import Queue

    redis = fakeredis.FakeRedis()
    db = JobDatabase(tmp_path / "m.db")
    try:
        _seed_jobs(
            db,
            [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.COMPLETED, JobStatus.COMPLETED, JobStatus.FAILED],
        )
        Queue("whisper:gpu", connection=redis).enqueue("math.sqrt", 4)

        out = _scrape(db, redis)

        assert 'whisper_jobs_total{status="completed"} 2.0' in out
        assert 'whisper_jobs_total{status="failed"} 1.0' in out
        assert 'whisper_jobs_total{status="queued"} 1.0' in out
        assert 'whisper_jobs_total{status="processing"} 1.0' in out
        assert 'whisper_jobs_total{status="pending"} 0.0' in out  # missing status -> 0, series never disappears
        assert 'whisper_queue_depth{queue="whisper:gpu"} 1.0' in out
        assert 'whisper_queue_depth{queue="whisper:io"} 0.0' in out
        assert 'whisper_queue_depth{queue="whisper:llm"} 0.0' in out  # dedicated LLM queue is scraped
        assert "whisper_failed_jobs" in out
        assert "whisper_started_jobs" in out
        assert "whisper_rq_workers " in out
    finally:
        db.close()


def test_whisper_collector_degrades_when_redis_fails(tmp_path):
    """A Redis failure must not break the scrape: SQLite job metrics still emit,
    queue gauges are skipped, and collect() never raises."""
    db = JobDatabase(tmp_path / "m.db")
    try:
        _seed_jobs(db, [JobStatus.COMPLETED])
        broken = MagicMock()
        broken.llen.side_effect = RuntimeError("redis down")

        out = _scrape(db, broken)  # must not raise

        assert 'whisper_jobs_total{status="completed"} 1.0' in out
        assert "whisper_queue_depth" not in out  # redis gauges skipped on failure
    finally:
        db.close()
