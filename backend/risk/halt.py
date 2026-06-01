"""Halt mode state management for the Risk Manager.

This module manages the system halt state, which when enabled, causes all
TradeIntents to be rejected with reason "system_halted".

Halt state is persisted in Redis and survives Risk Manager restarts.
"""

import logging

from backend.redis import get_redis_client
from backend.redis.keys import SYSTEM_HALT

logger = logging.getLogger(__name__)


def set_halt_mode(enabled: bool) -> None:
    """
    Set the system halt mode state.
    
    When halt mode is enabled, all TradeIntents will be rejected with
    reason "system_halted". When disabled, normal risk evaluation proceeds.
    
    Args:
        enabled: True to enable halt mode, False to disable
        
    Note:
        Halt state is persisted in Redis key `system:halt` and survives
        Risk Manager restarts.
    """
    try:
        redis_client = get_redis_client()
        # Store as "1" for enabled, "0" for disabled (Redis string)
        redis_client.set(SYSTEM_HALT, "1" if enabled else "0")
        logger.info(f"System halt mode set to: {enabled}")
    except Exception as e:
        logger.error(f"Failed to set halt mode in Redis: {e}")
        raise


def is_halted() -> bool:
    """
    Check if the system is currently in halt mode.
    
    Returns:
        True if system is halted, False otherwise.
        Defaults to True if Redis is unavailable (fail-safe: block trades
        when halt state cannot be verified). Returns False only when Redis
        explicitly confirms the system is not halted.
        
    Note:
        The halt state is read from Redis key `system:halt`.
        If the key doesn't exist, the system is considered not halted.
    """
    try:
        redis_client = get_redis_client()
        halt_value = redis_client.get(SYSTEM_HALT)
        
        # Key doesn't exist or is None -> not halted (default state)
        if halt_value is None:
            return False
        
        # Check for various truthy string representations
        return halt_value in ("1", "true", "True", "TRUE")
        
    except Exception as e:
        logger.warning(
            f"Failed to check halt state from Redis: {e}. "
            f"Assuming halted (fail-safe: block all trades when halt state is unverifiable)."
        )
        # Fail-safe: if we can't verify halt state, assume halted to prevent
        # unintended trades when Redis is unavailable.
        return True
