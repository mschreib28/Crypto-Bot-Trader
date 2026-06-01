"""Trading control API endpoints."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.redis import get_redis_client
from backend.redis.keys import BOT_MODE_KEY, TRADING_ENABLED_KEY, SHADOW_LIVE_MODE_KEY
from backend.api.routes.events import log_activity

router = APIRouter(tags=["Trading"])
logger = logging.getLogger(__name__)


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class BotModeResponse(BaseModel):
    mode: str
    updated_at: Optional[str] = None


class BotModeRequest(BaseModel):
    mode: str
    confirm: Optional[str] = None


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
    timestamp = _iso_timestamp()

    client.set(TRADING_ENABLED_KEY, str(enabled).lower())
    client.set(f"{TRADING_ENABLED_KEY}:updated_at", timestamp)
    if enabled:
        client.set(SHADOW_LIVE_MODE_KEY, "false")
        client.set(f"{SHADOW_LIVE_MODE_KEY}:updated_at", timestamp)

    _sync_canonical_bot_mode_key(timestamp)
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
        enabled = get_bot_mode() == "LIVE"
        updated_at = client.get(BOT_MODE_KEY + ":updated_at") or client.get(
            f"{TRADING_ENABLED_KEY}:updated_at"
        )

        ua = (
            updated_at.decode("utf-8") if isinstance(updated_at, bytes) else updated_at
        )
        return TradingStatusResponse(
            enabled=enabled,
            updated_at=ua
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
    Check if shadow/paper trading mode is active.

    Prefers canonical BOT_MODE_KEY (SHADOW → paper paths). Falls back to legacy
    SHADOW_LIVE_MODE_KEY when bot mode is unset.
    """
    client = get_redis_client()
    try:
        bot_mode = client.get(BOT_MODE_KEY)
        if bot_mode is not None:
            v = bot_mode.decode("utf-8") if isinstance(bot_mode, bytes) else str(bot_mode)
            v = v.strip().upper()
            if v == "SHADOW":
                return True
            if v == "LIVE":
                return False

        value = client.get(SHADOW_LIVE_MODE_KEY)
        if value is None:
            return False
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return str(value).lower() in ("true", "1")
    except Exception as e:
        logger.warning(f"Failed to read shadow_live_mode: {e}")
        return False  # Fail-closed: assume disabled


def _sync_canonical_bot_mode_key(timestamp: Optional[str] = None) -> str:
    """Write BOT_MODE_KEY from current legacy Redis flags. Returns mode string."""
    ts = timestamp or _iso_timestamp()
    mode = "LIVE" if get_trading_enabled() and not get_shadow_live_mode() else "SHADOW"
    client = get_redis_client()
    client.set(BOT_MODE_KEY, mode)
    client.set(BOT_MODE_KEY + ":updated_at", ts)
    return mode


def get_bot_mode() -> str:
    """
    Returns 'LIVE' or 'SHADOW'. Defaults to SHADOW on missing key, invalid value, or error.
    If BOT_MODE_KEY is unset, derives from legacy trading_enabled / shadow_live_mode.
    """
    try:
        client = get_redis_client()
        raw = client.get(BOT_MODE_KEY)
        if raw is not None:
            v = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            v = v.strip().upper()
            if v == "LIVE":
                return "LIVE"
            if v == "SHADOW":
                return "SHADOW"
        if get_trading_enabled() and not get_shadow_live_mode():
            return "LIVE"
        return "SHADOW"
    except Exception as e:
        logger.warning(f"Failed to read bot_mode: {e}")
        return "SHADOW"


def set_bot_mode(mode: str) -> str:
    """
    Set canonical 'LIVE' or 'SHADOW' and sync legacy Redis keys.
    Returns ISO timestamp of the change.
    """
    normalized = str(mode).strip().upper()
    if normalized not in ("LIVE", "SHADOW"):
        raise ValueError(f"Invalid bot mode: {mode!r} (expected LIVE or SHADOW)")

    client = get_redis_client()
    timestamp = _iso_timestamp()
    is_live = normalized == "LIVE"

    client.set(BOT_MODE_KEY, normalized)
    client.set(BOT_MODE_KEY + ":updated_at", timestamp)

    client.set(TRADING_ENABLED_KEY, "true" if is_live else "false")
    client.set(f"{TRADING_ENABLED_KEY}:updated_at", timestamp)

    client.set(SHADOW_LIVE_MODE_KEY, "false" if is_live else "true")
    client.set(f"{SHADOW_LIVE_MODE_KEY}:updated_at", timestamp)

    logger.info(f"Bot mode set to {normalized} at {timestamp}")
    return timestamp


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
    timestamp = _iso_timestamp()

    client.set(SHADOW_LIVE_MODE_KEY, str(enabled).lower())
    client.set(f"{SHADOW_LIVE_MODE_KEY}:updated_at", timestamp)

    _sync_canonical_bot_mode_key(timestamp)
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
        enabled = get_bot_mode() == "SHADOW"
        updated_at = client.get(BOT_MODE_KEY + ":updated_at") or client.get(
            f"{SHADOW_LIVE_MODE_KEY}:updated_at"
        )
        ua = (
            updated_at.decode("utf-8") if isinstance(updated_at, bytes) else updated_at
        )

        return TradingStatusResponse(
            enabled=enabled,
            updated_at=ua
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


@router.get("/trading/bot-mode")
async def get_bot_mode_endpoint() -> BotModeResponse:
    """Canonical SHADOW vs LIVE mode (single Redis key)."""
    try:
        client = get_redis_client()
        mode = get_bot_mode()
        raw = client.get(BOT_MODE_KEY + ":updated_at")
        updated_at = (
            raw.decode("utf-8") if isinstance(raw, bytes) else raw
        ) if raw else None
        return BotModeResponse(mode=mode, updated_at=updated_at)
    except Exception as e:
        logger.error(f"Failed to get bot mode: {e}", exc_info=True)
        return BotModeResponse(mode="SHADOW", updated_at=None)


@router.post("/trading/bot-mode")
async def set_bot_mode_endpoint(request: BotModeRequest) -> BotModeResponse:
    """Set SHADOW or LIVE. LIVE requires confirm token (fail-safe)."""
    mode_raw = (request.mode or "").strip().upper()
    if mode_raw not in ("SHADOW", "LIVE"):
        raise HTTPException(
            status_code=400,
            detail="mode must be SHADOW or LIVE",
        )
    if mode_raw == "LIVE" and request.confirm != "ENABLE_LIVE_TRADING":
        raise HTTPException(
            status_code=400,
            detail='Setting LIVE requires confirm: "ENABLE_LIVE_TRADING"',
        )
    try:
        ts = set_bot_mode(mode_raw)
        log_activity(
            activity_type="system",
            message=f"Bot mode set to {mode_raw}",
            details={"bot_mode": mode_raw},
        )
        return BotModeResponse(mode=mode_raw, updated_at=ts)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) from ve
    except Exception as e:
        logger.error(f"Failed to set bot mode: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update bot mode")
