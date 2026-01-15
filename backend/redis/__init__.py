"""Redis connection pool and utilities."""

import logging
import time
from typing import Optional

import redis
from redis.connection import ConnectionPool

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


def get_redis_client() -> redis.Redis:
    """Get a Redis client from the connection pool with retry logic."""
    pool = get_connection_pool()
    client = redis.Redis(connection_pool=pool)
    
    # Test connection with retry logic
    max_retries = 3
    for attempt in range(max_retries):
        try:
            client.ping()
            return client
        except (redis.ConnectionError, redis.TimeoutError) as e:
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
