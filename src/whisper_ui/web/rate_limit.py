"""Redis-backed sliding-window rate limit for login attempts.

The limit is structured as two parallel counters per attempt: one keyed on
the username (so credential stuffing against one account is bounded) and
one keyed on the client IP (so a single source cannot exhaust attempts
across many accounts). Each counter has its own threshold
(``max_user_attempts`` and ``max_ip_attempts``); tripping either one
rejects the next attempt.

The window is implemented as a Redis ``INCR`` with ``EXPIRE NX``: the TTL
is set only on first increment so the window slides forward exactly once
per burst, not on every failed login.

Keys live under ``auth:rl:`` so production operators can clear them with
``redis-cli DEL auth:rl:user:alice`` without touching pipeline state.

Logging policy: every check_and_increment writes a DEBUG line with both
counter values, the thresholds, and which counter (if any) tripped, so
operators can replay an attack timeline by lifting LOG_LEVEL to DEBUG.
A WARNING is emitted once on the request that actually trips the limit,
naming the dimension that hit it ("user" vs "ip" vs "both") so security
review does not have to cross-reference counter values manually.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis


logger = logging.getLogger(__name__)


def _user_key(username: str) -> str:
    return f"auth:rl:user:{username.lower()}"


def _ip_key(ip: str) -> str:
    return f"auth:rl:ip:{ip}"


def _describe_trip(
    *,
    user_count: int,
    ip_count: int,
    max_user_attempts: int,
    max_ip_attempts: int,
) -> str | None:
    """Return 'user', 'ip', 'both', or None depending on which counter tripped."""
    user_over = user_count >= max_user_attempts
    ip_over = ip_count >= max_ip_attempts
    if user_over and ip_over:
        return "both"
    if user_over:
        return "user"
    if ip_over:
        return "ip"
    return None


def check_and_increment(
    redis: Redis,
    *,
    username: str,
    ip: str,
    max_user_attempts: int,
    max_ip_attempts: int,
    window_seconds: int,
) -> bool:
    """Record a failed-login attempt and return whether further attempts are blocked.

    Atomically increments both the per-user and per-IP counter. Sets a TTL
    only on first increment (``EXPIRE NX``) so the window starts ticking
    from the *first* failure of a burst, not from each subsequent failure.

    Returns ``True`` when the per-user counter has reached ``max_user_attempts``
    OR the per-IP counter has reached ``max_ip_attempts``. The two thresholds
    are independent so an office NAT shared by many legitimate users can
    safely be assigned a higher IP threshold than the strict per-account one.

    The boundary is ``>=`` (not ``>``) so the semantics match the user-facing
    documentation: ``max=5`` means "5 failures allowed, the 6th is blocked".

    Returns the same boolean regardless of which dimension triggered so the
    caller cannot leak which counter is full.
    """
    pipe = redis.pipeline()
    pipe.incr(_user_key(username))
    pipe.expire(_user_key(username), window_seconds, nx=True)
    pipe.incr(_ip_key(ip))
    pipe.expire(_ip_key(ip), window_seconds, nx=True)
    user_count, _, ip_count, _ = pipe.execute()
    tripped = _describe_trip(
        user_count=user_count,
        ip_count=ip_count,
        max_user_attempts=max_user_attempts,
        max_ip_attempts=max_ip_attempts,
    )

    logger.debug(
        "rate-limit attempt username=%r ip=%s user_count=%d/%d ip_count=%d/%d tripped=%s",
        username,
        ip,
        user_count,
        max_user_attempts,
        ip_count,
        max_ip_attempts,
        tripped or "no",
    )
    if tripped is not None:
        logger.warning(
            "rate-limit tripped on %s counter: username=%r ip=%s user_count=%d/%d ip_count=%d/%d",
            tripped,
            username,
            ip,
            user_count,
            max_user_attempts,
            ip_count,
            max_ip_attempts,
        )
    return tripped is not None


def is_locked(
    redis: Redis,
    *,
    username: str,
    ip: str,
    max_user_attempts: int,
    max_ip_attempts: int,
) -> bool:
    """Return True when an attempt should be rejected without consuming a slot.

    Used at the start of the login handler to short-circuit before any
    argon2 work, so a locked account does not pay verification cost on
    every probe. Counters are not modified.
    """
    user_count = int(redis.get(_user_key(username)) or 0)
    ip_count = int(redis.get(_ip_key(ip)) or 0)
    locked = user_count >= max_user_attempts or ip_count >= max_ip_attempts
    if locked:
        logger.debug(
            "rate-limit short-circuit: username=%r ip=%s user_count=%d/%d ip_count=%d/%d",
            username,
            ip,
            user_count,
            max_user_attempts,
            ip_count,
            max_ip_attempts,
        )
    return locked


def reset_user(redis: Redis, username: str) -> None:
    """Clear the per-user counter on successful login.

    Note the IP counter is deliberately not reset: an attacker who learns
    one valid credential should not be able to launder their IP through
    that account to keep probing others.
    """
    deleted = redis.delete(_user_key(username))
    if deleted:
        logger.info("rate-limit per-user counter cleared on successful login: username=%r", username)
