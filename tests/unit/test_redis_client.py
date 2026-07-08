from __future__ import annotations

from unittest.mock import MagicMock, patch

from whisper_ui.core.redis_client import create_redis


def _settings(**overrides) -> MagicMock:
    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    settings.redis_socket_timeout = 10
    settings.redis_socket_connect_timeout = 5
    settings.redis_health_check_interval = 30
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_create_redis_applies_socket_timeouts():
    with patch("whisper_ui.core.redis_client.Redis.from_url", return_value=MagicMock()) as from_url:
        create_redis(_settings())

    from_url.assert_called_once_with(
        "redis://localhost:6379/0",
        socket_timeout=10,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
    )


def test_create_redis_zero_timeout_disables_bound():
    """A timeout of 0 is passed to redis-py as None (no bound) rather than 0."""
    with patch("whisper_ui.core.redis_client.Redis.from_url", return_value=MagicMock()) as from_url:
        create_redis(_settings(redis_socket_timeout=0, redis_socket_connect_timeout=0))

    kwargs = from_url.call_args.kwargs
    assert kwargs["socket_timeout"] is None
    assert kwargs["socket_connect_timeout"] is None
