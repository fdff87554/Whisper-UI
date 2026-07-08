"""Single place that constructs Redis clients for the whole application.

Both the web tier (:mod:`whisper_ui.web.app`) and the worker runtime
(:mod:`whisper_ui.worker.runtime`) used to call ``Redis.from_url`` directly
with no timeouts, so a Redis host that accepted the connection but then went
silent (kernel freeze, firewall drop, network partition) would block the
caller's ``recv`` indefinitely — freezing the web event loop or wedging a
worker until the RQ death penalty fired. Routing every client through this
factory guarantees the socket timeouts are always applied.

Note: the RQ worker *loop* opens its own connection from the ``--url`` passed
to the RQ CLI (see ``worker/__main__.py``), which does not pass through here.
Operators who need timeouts on that connection can append them to
``REDIS_URL`` as query parameters (``?socket_timeout=10``); see the README.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from redis import Redis

if TYPE_CHECKING:
    from whisper_ui.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    """Build a Redis client with socket timeouts applied from ``settings``.

    ``socket_timeout`` bounds every blocking ``recv`` so a silent peer turns
    into a :class:`redis.exceptions.RedisError` the existing degradation paths
    already handle, instead of an unbounded hang. ``health_check_interval``
    makes a pooled connection validate itself with a PING after it has been
    idle, catching half-open connections before the next command hangs. A
    ``socket_timeout`` of 0 disables the bound (restores the legacy blocking
    behaviour) for operators who explicitly want it.
    """
    socket_timeout = settings.redis_socket_timeout or None
    connect_timeout = settings.redis_socket_connect_timeout or None
    return Redis.from_url(
        settings.redis_url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=connect_timeout,
        socket_keepalive=True,
        health_check_interval=settings.redis_health_check_interval,
    )
