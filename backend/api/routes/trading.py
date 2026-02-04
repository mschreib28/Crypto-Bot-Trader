"""Trading control API endpoints."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.redis import get_redis_client
from backend.redis.keys import TRADING_ENABLED_KEY, SHADOW_LIVE_MODE_KEY
from backend.api.routes.events import log_activity

router = APIRouter(tags=["Trading"])
logger = logging.getLogger(__name__)


class TradingStatusResponse(BaseModel):
    """Response model for trading status."""
    enabled: bool
    updated_at: Optional[str] = None


class TradingEnabledRequest(BaseModel):
    """Request model for setting trading enabled state."""
    enabled: bool


def get_trading_enabled() -> bool:
    """
    Check if trading execution is enabled.
    
    Reads from Redis key. Defaults to False (safe) if not set or on error.
    
    Returns:
        True if trading is enabled, False otherwise.
    """
    client = get_redis_client()
    try:
        value = client.get(TRADING_ENABLED_KEY)
        if value is None:
            return False
        return value.lower() == "true"
    except Exception as e:
        logger.warning(f"Failed to read trading_enabled: {e}")
        return False  # Fail-closed: assume disabled


def set_trading_enabled(enabled: bool) -> str:
    """
    Set trading execution enabled state.
    
    Args:
        enabled: Whether trading should be enabled.
        
    Returns:
        ISO timestamp of when the change was made.
    """
    client = get_redis_client()
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    client.set(TRADING_ENABLED_KEY, str(enabled).lower())
    client.set(f"{TRADING_ENABLED_KEY}:updated_at", timestamp)
    
    logger.info(f"Trading enabled set to {enabled} at {timestamp}")
    return timestamp


@router.get("/trading/status")
async def get_trading_status() -> TradingStatusResponse:
    """
    Get current trading enabled status.
    
    Returns:
        Current trading state and last update timestamp.
    """
    try:
        client = get_redis_client()
        enabled = get_trading_enabled()
        updated_at = client.get(f"{TRADING_ENABLED_KEY}:updated_at")
        
        return TradingStatusResponse(
            enabled=enabled,
            updated_at=updated_at
        )
    except Exception as e:
        logger.error(f"Failed to get trading status: {e}", exc_info=True)
        # Fail-closed: return disabled status on error
        return TradingStatusResponse(
            enabled=False,
            updated_at=None
        )


@router.post("/trading/enabled")
async def set_trading_enabled_endpoint(request: TradingEnabledRequest) -> TradingStatusResponse:
    """
    Enable or disable trading execution.
    
    When disabled (default), the screener will still run and detect signals,
    but no trades will be executed automatically.
    
    When enabled, signals meeting the confidence threshold will be auto-executed.
    
    Args:
        request: Contains 'enabled' boolean.
        
    Returns:
        Updated trading state and timestamp.
    """
    try:
        timestamp = set_trading_enabled(request.enabled)
        
        # Log to activity feed
        state = "enabled" if request.enabled else "disabled"
        log_activity(
            activity_type="system",
            message=f"Trading {state}",
            details={"trading_enabled": request.enabled},
        )
        
        return TradingStatusResponse(
            enabled=request.enabled,
            updated_at=timestamp
        )
    except Exception as e:
        logger.error(f"Failed to set trading enabled: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update trading state")


# Shadow-Live Mode Functions

def get_shadow_live_mode() -> bool:
    """
    Check if shadow-live mode is enabled.
    
    Reads from Redis key. Defaults to False (safe) if not set or on error.
    
    Returns:
        True if shadow-live mode is enabled, False otherwise.
    """
    client = get_redis_client()
    try:
        value = client.get(SHADOW_LIVE_MODE_KEY)
        if value is None:
            return False
        return value.lower() == "true"
    except Exception as e:
        logger.warning(f"Failed to read shadow_live_mode: {e}")
        return False  # Fail-closed: assume disabled


def set_shadow_live_mode(enabled: bool) -> str:
    """
    Set shadow-live mode state.
    
    When enabled, the bot will log ORDER_INTENT, STOP_INTENT, and TAKE_PROFIT_INTENT
    without actually executing orders. This is used for pre-live validation.
    
    Args:
        enabled: Whether shadow-live mode should be enabled.
        
    Returns:
        ISO timestamp of when the change was made.
    """
    client = get_redis_client()
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    client.set(SHADOW_LIVE_MODE_KEY, str(enabled).lower())
    client.set(f"{SHADOW_LIVE_MODE_KEY}:updated_at", timestamp)
    
    logger.info(f"Shadow-live mode set to {enabled} at {timestamp}")
    return timestamp


@router.get("/trading/shadow-status")
async def get_shadow_status() -> TradingStatusResponse:
    """
    Get current shadow-live mode status.
    
    Returns:
        Current shadow-live mode state and last update timestamp.
    """
    try:
        client = get_redis_client()
        enabled = get_shadow_live_mode()
        updated_at = client.get(f"{SHADOW_LIVE_MODE_KEY}:updated_at")
        
        return TradingStatusResponse(
            enabled=enabled,
            updated_at=updated_at
        )
    except Exception as e:
        logger.error(f"Failed to get shadow-live status: {e}", exc_info=True)
        # Fail-closed: return disabled status on error
        return TradingStatusResponse(
            enabled=False,
            updated_at=None
        )


@router.post("/trading/shadow-enabled")
async def set_shadow_live_mode_endpoint(request: TradingEnabledRequest) -> TradingStatusResponse:
    """
    Enable or disable shadow-live mode.
    
    When enabled, the bot will:
    - Generate signals normally
    - Calculate position sizes normally
    - Log ORDER_INTENT, STOP_INTENT, TAKE_PROFIT_INTENT to activity feed
    - NOT place actual orders on the exchange
    
    This is used for pre-live validation (24-48 hours recommended).
    
    If live trading is enabled, it will be automatically disabled
    to ensure mutual exclusivity.
    
    Args:
        request: Contains 'enabled' boolean.
        
    Returns:
        Updated shadow-live mode state and timestamp.
    """
    try:
        # If enabling shadow-live, disable live trading (mutually exclusive)
        if request.enabled:
            trading_enabled = get_trading_enabled()
            if trading_enabled:
                logger.info("Disabling live trading (mutually exclusive with shadow-live mode)")
                set_trading_enabled(False)
        
        timestamp = set_shadow_live_mode(request.enabled)
        
        # Log to activity feed
        state = "enabled" if request.enabled else "disabled"
        log_activity(
            activity_type="system",
            message=f"Shadow-live mode {state}",
            details={"shadow_live_mode": request.enabled},
        )
        
        return TradingStatusResponse(
            enabled=request.enabled,
            updated_at=timestamp
        )
    except Exception as e:
        logger.error(f"Failed to set shadow-live mode: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update shadow-live mode state")
