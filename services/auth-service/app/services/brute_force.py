"""Brute-force protection using Redis.

Tracks failed login attempts per username+IP and temporarily blocks
further attempts after a threshold is exceeded.
"""

import logging
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Configuration
MAX_FAILED_ATTEMPTS = 5
BLOCK_DURATION_SECONDS = 300  # 5 minutes
WINDOW_SECONDS = 300  # 5-minute sliding window

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _key(username: str, ip: str | None) -> str:
    safe_ip = ip or "unknown"
    return f"login_attempts:{safe_ip}:{username}"


def _block_key(username: str, ip: str | None) -> str:
    safe_ip = ip or "unknown"
    return f"login_blocked:{safe_ip}:{username}"


def is_blocked(username: str, ip: str | None = None) -> bool:
    """Return True if the username+IP is temporarily blocked."""
    try:
        r = _get_redis()
        return bool(r.exists(_block_key(username, ip)))
    except Exception as exc:
        logger.warning("Redis check failed for brute-force protection: %s", exc)
        return False


def record_failed_attempt(username: str, ip: str | None = None) -> None:
    """Record a failed login attempt and block if threshold exceeded."""
    try:
        r = _get_redis()
        key = _key(username, ip)
        block_key = _block_key(username, ip)

        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, WINDOW_SECONDS)
        results: list[Any] = pipe.execute()
        attempts = int(results[0])

        if attempts >= MAX_FAILED_ATTEMPTS:
            r.setex(block_key, BLOCK_DURATION_SECONDS, "1")
            logger.warning(
                "Brute-force block activated for username=%s ip=%s (attempts=%s)",
                username, ip, attempts,
            )
    except Exception as exc:
        logger.warning("Redis record failed for brute-force protection: %s", exc)


def record_successful_login(username: str, ip: str | None = None) -> None:
    """Clear failed attempts after a successful login."""
    try:
        r = _get_redis()
        r.delete(_key(username, ip))
        r.delete(_block_key(username, ip))
    except Exception as exc:
        logger.warning("Redis clear failed for brute-force protection: %s", exc)


def get_remaining_seconds(username: str, ip: str | None = None) -> int:
    """Return remaining block duration in seconds, or 0 if not blocked."""
    try:
        r = _get_redis()
        ttl = r.ttl(_block_key(username, ip))
        return max(0, int(ttl))
    except Exception:
        return 0
