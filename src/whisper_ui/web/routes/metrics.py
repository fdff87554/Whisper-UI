"""Prometheus ``/metrics`` endpoint (minimal, scrape-time, pull-based).

Exposes operational gauges computed on each scrape from the data the frontend
already holds — RQ/Redis registries and the SQLite ``jobs`` table — with no
persistent counters. A Redis blip degrades to the SQLite-only metrics instead
of failing the scrape (mirrors the lifespan's tolerance of an unreachable
Redis). The endpoint is unauthenticated (added to ``auth.PUBLIC_PATHS``, same
posture as ``/health``); it exposes only counts/depths, no PII. An operator
fronting the box publicly should block ``/metrics`` at the reverse proxy.

Per-stage latency is intentionally NOT exposed here (workers have no HTTP
server); it ships as ``elapsed_ms`` in the structured JSON logs and a
histogram is a documented follow-up.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

from whisper_ui.core.constants import WORKER_QUEUE_CPU, WORKER_QUEUE_GPU, WORKER_QUEUE_IO, WORKER_QUEUE_LLM
from whisper_ui.core.models import JobStatus

# DbDep/RedisDep stay runtime imports: FastAPI evaluates these Depends
# annotations at startup, so moving them into TYPE_CHECKING would NameError.
from whisper_ui.web.deps import DbDep, RedisDep  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Iterator

    from redis import Redis

    from whisper_ui.storage.database import JobDatabase

logger = logging.getLogger(__name__)

router = APIRouter()

# RQ queues to report. "default" is the ad-hoc maintenance queue every worker
# also subscribes to.
_QUEUES = (WORKER_QUEUE_GPU, WORKER_QUEUE_IO, WORKER_QUEUE_CPU, WORKER_QUEUE_LLM, "default")
_STATUSES = tuple(s.value for s in JobStatus)


class WhisperCollector:
    """Scrape-time collector reading current state from SQLite + Redis/RQ.

    ``collect()`` never raises: SQLite and Redis sections are independently
    guarded so a failure in one still yields the other's metrics and the
    scrape returns 200.
    """

    def __init__(self, db: JobDatabase, redis: Redis) -> None:
        self._db = db
        self._redis = redis

    def collect(self) -> Iterator[GaugeMetricFamily]:
        jobs = GaugeMetricFamily(
            "whisper_jobs_total",
            "Job rows by status (from SQLite).",
            labels=["status"],
        )
        try:
            counts = self._db.get_status_counts()
            for status in _STATUSES:
                jobs.add_metric([status], counts.get(status, 0))
        except Exception:
            logger.exception("metrics: get_status_counts failed; emitting empty whisper_jobs_total")
        yield jobs

        depth = GaugeMetricFamily("whisper_queue_depth", "Jobs waiting in each RQ queue.", labels=["queue"])
        failed = GaugeMetricFamily("whisper_failed_jobs", "Jobs in each RQ failed registry.", labels=["queue"])
        started = GaugeMetricFamily(
            "whisper_started_jobs", "Jobs in each RQ started (in-flight) registry.", labels=["queue"]
        )
        workers = GaugeMetricFamily("whisper_rq_workers", "Registered RQ workers.")
        try:
            from rq import Queue, Worker
            from rq.registry import FailedJobRegistry, StartedJobRegistry

            for name in _QUEUES:
                depth.add_metric([name], len(Queue(name, connection=self._redis)))
                failed.add_metric([name], FailedJobRegistry(name, connection=self._redis).count)
                started.add_metric([name], StartedJobRegistry(name, connection=self._redis).count)
            workers.add_metric([], len(Worker.all(connection=self._redis)))
        except Exception:
            # Redis unreachable / RQ error → skip these gauges, keep the scrape healthy.
            logger.exception("metrics: RQ/Redis collection failed; emitting SQLite metrics only")
            return
        yield depth
        yield failed
        yield started
        yield workers


@router.get("/metrics")
async def metrics(db: DbDep, redis: RedisDep) -> Response:
    # A fresh per-request registry keeps the collector stateless — no module
    # globals, no double-registration across scrapes.
    registry = CollectorRegistry()
    registry.register(WhisperCollector(db, redis))
    # generate_latest drives the collector, which makes ~16 blocking sync-Redis
    # round-trips plus a SQLite scan; offload it so a slow scrape (or a Redis
    # hiccup) does not stall the event loop for every other request.
    payload = await asyncio.to_thread(generate_latest, registry)
    return Response(payload, media_type=CONTENT_TYPE_LATEST)
