"""Redis caching for portfolio exposure.

This module provides caching functionality for portfolio exposure calculations
to improve performance and reduce database load.
"""

import logging
from typing import Optional
from decimal import Decimal

from backend.redis import get_redis_client
from backend.redis.keys import PORTFOLIO_EXPOSURE_TOTAL
from backend.risk.exposure import calculate_portfolio_exposure

logger = logging.getLogger(__name__)


def get_cached_exposure() -> Optional[float]:
    """
    Get cached portfolio exposure from Redis.
    
    Returns:
        Cached exposure percentage, or None if not cached.
    """
    try:
        client = get_redis_client()
        cached_value = client.get(PORTFOLIO_EXPOSURE_TOTAL)
        
        if cached_value is None:
            return None
        
        return float(cached_value)
    except Exception as e:
        logger.warning(f"Failed to get cached exposure from Redis: {e}")
        return None


def update_exposure_cache(exposure: Optional[float] = None) -> bool:
    """
    Update the portfolio exposure cache in Redis.
    
    If exposure is not provided, calculates it fresh from the database.
    
    Args:
        exposure: Optional exposure value to cache. If None, calculates fresh.
        
    Returns:
        True if cache was updated successfully, False otherwise.
    """
    try:
        client = get_redis_client()
        
        if exposure is None:
            # Calculate fresh exposure
            exposure = calculate_portfolio_exposure()
        
        # Store in Redis as a string (Redis stores numbers as strings)
        client.set(PORTFOLIO_EXPOSURE_TOTAL, str(exposure))
        
        logger.debug(f"Updated exposure cache: {exposure}%")
        return True
    except Exception as e:
        logger.error(f"Failed to update exposure cache in Redis: {e}")
        return False


def get_portfolio_exposure_cached(
    use_cache: bool = True,
    fallback_to_calc: bool = True
) -> Optional[float]:
    """
    Get portfolio exposure, using cache if available.
    
    Args:
        use_cache: If True, try to get from cache first.
        fallback_to_calc: If True and cache miss, calculate fresh.
        
    Returns:
        Portfolio exposure percentage, or None if unavailable.
    """
    if use_cache:
        cached = get_cached_exposure()
        if cached is not None:
            logger.debug(f"Using cached exposure: {cached}%")
            return cached
    
    if fallback_to_calc:
        logger.debug("Cache miss, calculating fresh exposure")
        exposure = calculate_portfolio_exposure()
        # Update cache for next time
        update_exposure_cache(exposure)
        return exposure
    
    return None


def clear_exposure_cache() -> bool:
    """
    Clear the portfolio exposure cache from Redis.
    
    Returns:
        True if cache was cleared successfully, False otherwise.
    """
    try:
        client = get_redis_client()
        client.delete(PORTFOLIO_EXPOSURE_TOTAL)
        logger.debug("Cleared exposure cache")
        return True
    except Exception as e:
        logger.error(f"Failed to clear exposure cache: {e}")
        return False
