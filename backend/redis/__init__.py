"""Redis connection pool and utilities."""

import logging
import sys
import time
from typing import Optional

# ---- Real redis package bootstrap ----------------------------------------
# backend/redis/ shadows the 'redis' pip package.  conftest.py pre-loads the
# real package into sys.modules["_redis_real"] and sys.modules["redis"] before
# any test collection, so we can safely retrieve it here.
#
# In production (no conftest.py), we perform the bootstrap ourselves by
# locating the real redis in site-packages.

def _get_real_redis():
    """Return the installed redis package object, not this shadow module."""
    # Use the cached pre-loaded module if available (conftest.py path).
    cached = sys.modules.get("_redis_real")
    if cached is not None:
        return cached

    # Production / direct-run path: load from site-packages.
    import importlib.util
    import os

    project_backend_redis = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "redis"
    )

    for search_dir in sys.path:
        redis_init = os.path.join(search_dir, "redis", "__init__.py")
        if not os.path.isfile(redis_init):
            continue
        redis_dir = os.path.join(search_dir, "redis")
        # Skip our own shadow package
        if os.path.realpath(redis_dir) == os.path.realpath(project_backend_redis):
            continue

        spec = importlib.util.spec_from_file_location(
            "_redis_real",
            redis_init,
            submodule_search_locations=[redis_dir],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_redis_real"] = mod
        sys.modules.setdefault("redis", mod)
        spec.loader.exec_module(mod)
        return mod

    raise ImportError(
        "backend.redis: cannot locate the real 'redis' pip package. "
        "Install it with: pip install redis"
    )


_real_redis = _get_real_redis()

# Expose core symbols used throughout this module.
Redis = _real_redis.Redis
ConnectionPool = _real_redis.ConnectionPool
# ---- End bootstrap -------------------------------------------------------

from backend.config import REDIS_MAX_CONNECTIONS, REDIS_URL

logger = logging.getLogger(__name__)

# Global connection pool
_connection_pool: Optional[ConnectionPool] = None


def get_connection_pool() -> ConnectionPool:
    """Get or create the Redis connection pool."""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = ConnectionPool.from_url(
            REDIS_URL,
            max_connections=REDIS_MAX_CONNECTIONS,
            decode_responses=True,
        )
    return _connection_pool


def get_redis_client() -> Redis:
    """Get a Redis client from the connection pool with retry logic."""
    pool = get_connection_pool()
    client = Redis(connection_pool=pool)

    # Test connection with retry logic
    max_retries = 3
    for attempt in range(max_retries):
        try:
            client.ping()
            return client
        except (_real_redis.ConnectionError, _real_redis.TimeoutError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    f"Redis connection attempt {attempt + 1} failed: {e}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                logger.error(f"Failed to connect to Redis after {max_retries} attempts: {e}")
                raise

    return client


def close_connection_pool():
    """Close the Redis connection pool (useful for cleanup)."""
    global _connection_pool
    if _connection_pool is not None:
        _connection_pool.disconnect()
        _connection_pool = None
