"""Shared async Redis connection with heartbeat and auto-reconnect.

Provides a module-level Redis client singleton that is created once
at startup and reused across all RedisQueue instances.

Multi-worker safety (uvicorn --workers > 1):
- Each child process forks after the lifespan start hook, so every
  worker gets its own copy of the _redis global.
- No shared state across workers; no synchronization needed.

Heartbeat:
- A background asyncio task pings Redis every ``_HEARTBEAT_INTERVAL``
  seconds (default 30s). If three consecutive pings fail, the client
  is recreated from scratch (new TCP connection).

Usage:
    from nanoclaw.storage.redis_client import get_redis, close_redis

    redis = await get_redis()
    await redis.set("key", "value")
    await close_redis()
"""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis

from nanoclaw.config import settings

logger = logging.getLogger(__name__)

_redis: Redis | None = None
_heartbeat_task: asyncio.Task[None] | None = None
_HEARTBEAT_INTERVAL = 30  # seconds
_MAX_HEARTBEAT_FAILURES = 3


async def get_redis() -> Redis:
    """Return the shared Redis connection, creating it if necessary.

    Verifies the connection with a ``PING`` command so failures
    surface immediately at startup (fail fast).  Starts a background
    heartbeat task on first call.
    """
    global _redis, _heartbeat_task
    if _redis is None:
        url = settings.redis_url or "redis://localhost:6379/0"
        _redis = Redis.from_url(url, decode_responses=True,
        socket_timeout=None, socket_connect_timeout=5)
        # Verify connection — crash now if Redis is unreachable
        await _redis.ping()
        # Start background heartbeat
        if _heartbeat_task is None:
            _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    return _redis


async def close_redis() -> None:
    """Close the shared Redis connection and stop the heartbeat."""
    global _redis, _heartbeat_task
    # Stop heartbeat first
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass
        _heartbeat_task = None
    # Close connection
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def _heartbeat_loop() -> None:
    """Background loop: ping Redis periodically, reconnect on failure.

    Runs until cancelled by ``close_redis()``.  After
    ``_MAX_HEARTBEAT_FAILURES`` consecutive failures the client is
    replaced with a fresh connection.
    """
    failures = 0
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            if _redis is not None:
                await _redis.ping()
                failures = 0  # Reset on success
        except Exception as exc:
            failures += 1
            logger.warning(
                "Redis heartbeat %d/%d failed: %s",
                failures, _MAX_HEARTBEAT_FAILURES, exc,
            )
            if failures >= _MAX_HEARTBEAT_FAILURES:
                logger.error("Redis heartbeat expired — reconnecting")
                await _reconnect_redis()
                failures = 0  # Reset failure counter regardless of outcome

        # If _redis is None (e.g. previous reconnect also failed),
        # attempt to re-establish the connection on the next cycle
        if _redis is None:
            try:
                await _reconnect_redis()
            except Exception:
                pass  # Will retry on the next heartbeat cycle


async def _reconnect_redis() -> None:
    """Replace the existing Redis client with a fresh connection."""
    global _redis
    old = _redis
    _redis = None  # Prevent get_redis() from returning stale client
    if old is not None:
        try:
            await old.aclose()
        except Exception:
            pass
    try:
        _redis = await get_redis()
        logger.info("Redis reconnected successfully")
    except Exception as exc:
        logger.error("Redis reconnect failed: %s", exc)
        # _redis stays None — next call to get_redis() will retry
