"""Shared async Redis connection.

Provides a module-level Redis client singleton that is created once
at startup and reused across all RedisQueue instances.

Usage:
    from nanoclaw.storage.redis_client import get_redis, close_redis

    redis = await get_redis()
    await redis.set("key", "value")
    await close_redis()
"""

from __future__ import annotations

from redis.asyncio import Redis

from nanoclaw.config import settings

_redis: Redis | None = None


async def get_redis() -> Redis:
    """Return the shared Redis connection, creating it if necessary."""
    global _redis
    if _redis is None:
        url = settings.redis_url or "redis://localhost:6379/0"
        _redis = Redis.from_url(url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    """Close the shared Redis connection."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
