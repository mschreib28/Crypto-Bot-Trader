"""Nonce management for Kraken API requests.

Nonces must be monotonically increasing and unique to prevent collisions.
This module provides atomic nonce generation using Redis.
"""

import logging
from typing import Optional

from backend.redis import get_redis_client
from backend.redis.keys import EXECUTION_NONCE

logger = logging.getLogger(__name__)


def get_next_nonce() -> int:
    """
    Get the next nonce atomically using Redis INCR.
    
    This ensures nonces are:
    - Monotonically increasing
    - Unique (no collisions)
    - Thread-safe and process-safe
    
    Returns:
        Next nonce value (integer)
        
    Raises:
        redis.RedisError: If Redis connection fails
    """
    client = get_redis_client()
    
    try:
        # Atomic increment operation
        # If key doesn't exist, Redis sets it to 0 then increments to 1
        nonce = client.incr(EXECUTION_NONCE)
        logger.debug(f"Generated nonce: {nonce}")
        return nonce
    except Exception as e:
        logger.error(f"Failed to generate nonce: {e}")
        raise


def reset_nonce(value: int = 0) -> None:
    """
    Reset the nonce counter (useful for testing or manual intervention).
    
    Args:
        value: Value to set the nonce to (default: 0)
        
    Raises:
        redis.RedisError: If Redis connection fails
    """
    client = get_redis_client()
    client.set(EXECUTION_NONCE, value)
    logger.info(f"Reset nonce to {value}")


def get_current_nonce() -> Optional[int]:
    """
    Get the current nonce value without incrementing.
    
    Returns:
        Current nonce value, or None if not set
        
    Raises:
        redis.RedisError: If Redis connection fails
    """
    client = get_redis_client()
    value = client.get(EXECUTION_NONCE)
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
