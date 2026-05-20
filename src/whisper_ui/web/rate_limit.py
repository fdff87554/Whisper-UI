"""Redis-backed sliding-window rate limit for login attempts.

The limit is structured as two parallel counters per attempt: one keyed on
the username (so credential stuffing against one account is bounded) and
one keyed on the client IP (so a single source cannot exhaust attempts
across many accounts). Hitting the threshold on *either* counter rejects
the attempt.

The window is implemented as a Redis ``INCR`` with ``EXPIRE NX``: the TTL
is set only on first increment so the window slides forward exactly once
per burst, not on every failed login.

Keys live under ``auth:rl:`` so production operators can clear them with
``redis-cli DEL auth:rl:user:alice`` without touching pipeline state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis


def _user_key(username: str) -> str:
    return f"auth:rl:user:{username.lower()}"


def _ip_key(ip: str) -> str:
    return f"auth:rl:ip:{ip}"


def check_and_increment(
    redis: Redis,
    *,
    username: str,
    ip: str,
    max_attempts: int,
    window_seconds: int,
) -> bool:
    """Record a failed-login attempt and return whether further attempts are blocked.

    Atomically increments both the per-user and per-IP counter. Sets a TTL
    only on first increment (``EXPIRE NX``) so the window starts ticking
    from the *first* failure of a burst, not from each subsequent failure.

    Returns ``True`` when at least one counter has crossed ``max_attempts``,
    meaning the caller should reject the login attempt with a generic
    "too many attempts" message — the same message used regardless of
    which counter triggered, so attackers cannot probe which dimension
    is locked.

    The Redis pipeline groups the four commands into a single round trip
    but does not wrap them in MULTI/EXEC; the counters are independent so
    there is no cross-command invariant to protect.
    """
    pipe = redis.pipeline()
    pipe.incr(_user_key(username))
    pipe.expire(_user_key(username), window_seconds, nx=True)
    pipe.incr(_ip_key(ip))
    pipe.expire(_ip_key(ip), window_seconds, nx=True)
    user_count, _, ip_count, _ = pipe.execute()
    return user_count > max_attempts or ip_count > max_attempts


def is_locked(redis: Redis, *, username: str, ip: str, max_attempts: int) -> bool:
    """Return True when an attempt should be rejected without consuming a slot.

    Used at the start of the login handler to short-circuit before any
    argon2 work, so a locked account does not pay verification cost on
    every probe. Counters are not modified.
    """
    user_count = int(redis.get(_user_key(username)) or 0)
    ip_count = int(redis.get(_ip_key(ip)) or 0)
    return user_count > max_attempts or ip_count > max_attempts


def reset_user(redis: Redis, username: str) -> None:
    """Clear the per-user counter on successful login.

    Note the IP counter is deliberately not reset: an attacker who learns
    one valid credential should not be able to launder their IP through
    that account to keep probing others.
    """
    redis.delete(_user_key(username))
