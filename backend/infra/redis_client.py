"""
infra/redis_client.py — Redis connection singleton.

Usage:
    from infra.redis_client import get_redis, init_redis, close_redis

    # In the app lifespan (main.py):
    await init_redis()
    r = get_redis()
    await close_redis()

    # In service modules:
    from infra.redis_client import get_redis
    r = get_redis()
    await r.set("key", "value")

All keys used in the project follow the prefix:
    game:{game_id}:<suffix>
"""

import logging
from typing import Optional

import redis.asyncio as aioredis

from settings import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_redis: Optional[aioredis.Redis] = None


async def init_redis() -> aioredis.Redis:
    """
    Creates (or returns) the Redis connection.
    Must be called only once during the application lifespan.
    """
    global _redis
    if _redis is None:
        log.info("Connecting to Redis at %s", settings.redis_url)
        _redis = aioredis.from_url(
            settings.redis_url,
            password=settings.redis_password or None,
            encoding="utf-8",
            decode_responses=True,
        )
        # Immediate connection check
        await _redis.ping()
        log.info("Redis connection established")
    return _redis


def get_redis() -> aioredis.Redis:
    """
    Returns the already initialized Redis client.
    Raises RuntimeError if init_redis() has not been called yet.
    """
    if _redis is None:
        raise RuntimeError(
            "Redis not initialised. Call `await init_redis()` during app startup."
        )
    return _redis


async def close_redis() -> None:
    """
    Closes the Redis connection.
    Must be called during application shutdown.
    """
    global _redis
    if _redis is not None:
        log.info("Closing Redis connection")
        await _redis.aclose()
        _redis = None


# ---------------------------------------------------------------------------
# Redis key schema — documentation of keys used in the project
# ---------------------------------------------------------------------------

class RedisKeys:
    """
    Centralized namespace for building Redis keys.

    Use these methods instead of manually constructing strings in services.
    """

    @staticmethod
    def game_state(game_id: str) -> str:
        """Hash with all scalar fields of GameState."""
        return f"game:{game_id}:state"

    @staticmethod
    def players(game_id: str) -> str:
        """Hash { player_id → JSON(Player) }."""
        return f"game:{game_id}:players"

    @staticmethod
    def votes(game_id: str) -> str:
        """Hash { voter_id → target_id } — daytime votes."""
        return f"game:{game_id}:votes"

    @staticmethod
    def wolf_votes(game_id: str) -> str:
        """Hash { wolf_id → target_id } — private nighttime wolf votes."""
        return f"game:{game_id}:wolf_votes"

    @staticmethod
    def wolf_target(game_id: str) -> str:
        """String — target chosen by wolves (tally result)."""
        return f"game:{game_id}:wolf_target"

    @staticmethod
    def seer_action(game_id: str) -> str:
        """String — player_id chosen by the seer this night."""
        return f"game:{game_id}:seer_action"

    @staticmethod
    def timer_end(game_id: str) -> str:
        """String — UNIX timestamp (float) of phase timer expiration."""
        return f"game:{game_id}:timer_end"

    @staticmethod
    def pubsub_channel(game_id: str) -> str:
        """Redis Pub/Sub channel for cross-instance event propagation."""
        return f"channel:{game_id}:events"