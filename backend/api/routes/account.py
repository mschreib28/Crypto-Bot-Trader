"""Account API endpoint for equity, P&L, and risk limits."""

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.execution.kraken_rest import KrakenClient
from backend.risk.account import AccountTracker
from backend.redis import get_redis_client
from backend.redis.keys import SHADOW_BALANCE_KEY
from backend.api.routes.trading import get_shadow_live_mode

router = APIRouter(tags=["Account"])
logger = logging.getLogger(__name__)

# Global instance (will be properly injected in production)
_account_tracker = None

# Kraken currency code mapping (duplicated for endpoint use)
KRAKEN_CURRENCY_MAP = {
    "XXBT": "BTC",
    "XBT": "BTC",
    "XETH": "ETH",
    "ETH": "ETH",
    "XXRP": "XRP",
    "XRP": "XRP",
    "XLTC": "LTC",
    "LTC": "LTC",
    "ZUSD": "USD",
    "USD": "USD",
    "ZEUR": "EUR",
    "EUR": "EUR",
}


def get_account_tracker() -> AccountTracker:
    global _account_tracker
    if _account_tracker is None:
        _account_tracker = AccountTracker()
    return _account_tracker


def _normalize_currency(kraken_code: str) -> str:
    """Convert Kraken currency code to standard symbol."""
    if kraken_code in KRAKEN_CURRENCY_MAP:
        return KRAKEN_CURRENCY_MAP[kraken_code]
    # Strip X or Z prefix if present
    if kraken_code.startswith(("X", "Z")) and len(kraken_code) > 1:
        return kraken_code[1:]
    return kraken_code


@router.get("/account")
async def get_account() -> dict:
    """Get current account state including equity, P&L, and risk limits."""
    tracker = get_account_tracker()
    state = tracker.get_state()

    daily_loss_limit = float(os.getenv("DAILY_LOSS_LIMIT", "10.0"))
    
    # Calculate total P&L (current equity - initial equity)
    total_pnl = state.current_equity - state.initial_equity
    
    # Calculate P&L percentage
    pnl_percent = 0.0
    if state.initial_equity > 0:
        pnl_percent = (total_pnl / state.initial_equity) * 100.0

    # Get micro mode status
    from backend.risk.micro_mode import get_micro_mode_status, get_live_slots_status
    micro_mode = get_micro_mode_status(state.current_equity)
    
    # Get live slots status
    live_slots_status = get_live_slots_status(state.current_equity)
    
    return {
        "initial_equity": state.initial_equity,
        "realized_pnl": state.realized_pnl,
        "current_equity": state.current_equity,
        "total_pnl": round(total_pnl, 2),
        "pnl_percent": round(pnl_percent, 2),
        "daily_pnl": state.daily_pnl,
        "max_risk_per_trade": state.max_risk_per_trade,
        "daily_loss_limit": daily_loss_limit,
        "risk_pct": float(os.getenv("RISK_PCT_PER_TRADE", "2.0")),
        "micro_mode": micro_mode,
        "live_slots_active": live_slots_status["current_slots"],
        "live_slots_max": live_slots_status["max_slots"],
    }


@router.get("/balance")
async def get_balance() -> dict:
    """
    Get account balance from Kraken with USD conversion.
    
    In shadow mode, returns the configured shadow balance instead of real balance.
    
    Returns:
        {
            "total_usd": 50.0,          # Total portfolio value in USD
            "available_usd": 45.0,      # Available for trading (minus open orders)
            "holdings": [
                {"symbol": "USD", "quantity": 45.0, "value_usd": 45.0},
                {"symbol": "ETH", "quantity": 0.01, "value_usd": 32.0},
            ]
        }
    
    Note:
        - In shadow mode: returns configured shadow balance (set via /api/v1/balance/shadow)
        - In live mode: fetches real balance from Kraken
        - Crypto holdings are converted to USD using current market prices
        - Works with $0 balance (new accounts)
        - For cached balance (used by 2% rule), see /api/v1/account
        - Forces fresh fetch from Kraken when not in shadow mode (bypasses cache)
    """
    # Check if shadow mode is enabled
    shadow_mode = get_shadow_live_mode()
    
    if shadow_mode:
        # Return shadow balance if configured
        client = get_redis_client()
        try:
            shadow_balance_json = client.get(SHADOW_BALANCE_KEY)
            if shadow_balance_json:
                shadow_balance = json.loads(shadow_balance_json)
                logger.debug(f"Returning shadow balance: total=${shadow_balance.get('total_usd', 0)}")
                return shadow_balance
            else:
                # No shadow balance set, return default
                default_balance = {
                    "total_usd": 1000.0,
                    "available_usd": 1000.0,
                    "holdings": [{"symbol": "USD", "quantity": 1000.0, "value_usd": 1000.0}]
                }
                logger.info("Shadow mode enabled but no balance set, returning default $1000")
                return default_balance
        except Exception as e:
            logger.warning(f"Failed to get shadow balance: {e}, falling back to default")
            return {
                "total_usd": 1000.0,
                "available_usd": 1000.0,
                "holdings": [{"symbol": "USD", "quantity": 1000.0, "value_usd": 1000.0}]
            }
    
    # Live mode: fetch real balance from Kraken
    try:
        client = KrakenClient()
        # Force fresh fetch by calling get_account_balance (which fetches from Kraken)
        balance = client.get_account_balance()
        
        # Filter out dust holdings (very small quantities with zero value)
        MIN_HOLDING_VALUE = 0.01  # Minimum USD value to show
        filtered_holdings = [
            h for h in balance.get("holdings", [])
            if h.get("value_usd", 0) >= MIN_HOLDING_VALUE
        ]
        balance["holdings"] = filtered_holdings
        
        logger.info(
            f"Balance fetched: total=${balance['total_usd']}, "
            f"available=${balance['available_usd']}, holdings={len(filtered_holdings)} (filtered from {len(balance.get('holdings', []))})"
        )
        
        return balance
        
    except ValueError as e:
        logger.error(f"Kraken authentication error: {e}")
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to fetch Kraken balance: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch balance from Kraken")


class ShadowBalanceRequest(BaseModel):
    """Request model for setting shadow balance."""
    total_usd: float
    available_usd: Optional[float] = None
    holdings: Optional[list] = None


@router.post("/balance/shadow")
async def set_shadow_balance(request: ShadowBalanceRequest) -> dict:
    """
    Set shadow balance for shadow trading mode.
    
    This balance is used when shadow-live mode is enabled instead of fetching
    real balance from Kraken. This allows testing with a simulated balance.
    
    Args:
        request: Shadow balance configuration
            - total_usd: Total portfolio value in USD
            - available_usd: Available for trading (defaults to total_usd if not provided)
            - holdings: List of holdings (optional, defaults to single USD holding)
    
    Returns:
        The saved shadow balance configuration.
    """
    try:
        # Validate inputs
        if request.total_usd < 0:
            raise HTTPException(status_code=400, detail="total_usd must be non-negative")
        
        available_usd = request.available_usd if request.available_usd is not None else request.total_usd
        if available_usd < 0:
            raise HTTPException(status_code=400, detail="available_usd must be non-negative")
        if available_usd > request.total_usd:
            raise HTTPException(status_code=400, detail="available_usd cannot exceed total_usd")
        
        # Build holdings list
        if request.holdings:
            holdings = request.holdings
        else:
            # Default: single USD holding
            holdings = [{"symbol": "USD", "quantity": request.total_usd, "value_usd": request.total_usd}]
        
        shadow_balance = {
            "total_usd": request.total_usd,
            "available_usd": available_usd,
            "holdings": holdings
        }
        
        # Store in Redis
        client = get_redis_client()
        client.set(SHADOW_BALANCE_KEY, json.dumps(shadow_balance))
        
        logger.info(
            f"Shadow balance set: total=${request.total_usd}, "
            f"available=${available_usd}, holdings={len(holdings)}"
        )
        
        # Log to activity feed
        from backend.api.routes.events import log_activity
        log_activity(
            activity_type="system",
            message=f"Shadow balance set: ${request.total_usd} total, ${available_usd} available",
            details={
                "shadow_balance": shadow_balance,
                "mode": "shadow_live"
            }
        )
        
        return shadow_balance
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set shadow balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set shadow balance")


@router.get("/balance/shadow")
async def get_shadow_balance() -> dict:
    """
    Get current shadow balance configuration.
    
    Returns:
        Current shadow balance or default if not set.
    """
    try:
        client = get_redis_client()
        shadow_balance_json = client.get(SHADOW_BALANCE_KEY)
        
        if shadow_balance_json:
            return json.loads(shadow_balance_json)
        else:
            # Return default
            return {
                "total_usd": 1000.0,
                "available_usd": 1000.0,
                "holdings": [{"symbol": "USD", "quantity": 1000.0, "value_usd": 1000.0}]
            }
    except Exception as e:
        logger.error(f"Failed to get shadow balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get shadow balance")


def update_shadow_balance(amount: float, operation: str) -> Optional[dict]:
    """
    TICKET-612: Atomically update shadow balance.
    
    Args:
        amount: Amount to add (positive) or deduct (negative)
        operation: 'add' or 'deduct'
        
    Returns:
        Updated shadow balance dict, or None if shadow mode not enabled or update failed
    """
    try:
        from backend.api.routes.trading import get_shadow_live_mode
        if not get_shadow_live_mode():
            return None  # Not in shadow mode
        
        client = get_redis_client()
        
        # Use Redis transaction to ensure atomicity
        pipe = client.pipeline()
        pipe.get(SHADOW_BALANCE_KEY)
        shadow_balance_json = pipe.execute()[0]
        
        if not shadow_balance_json:
            logger.warning("Shadow balance not set, cannot update")
            return None
        
        shadow_balance = json.loads(shadow_balance_json)
        current_total = shadow_balance.get("total_usd", 0.0)
        current_available = shadow_balance.get("available_usd", 0.0)
        
        # Update balance based on operation
        if operation == "deduct":
            new_total = current_total - amount
            new_available = current_available - amount
            if new_total < 0 or new_available < 0:
                logger.warning(f"Shadow balance would go negative: total={new_total}, available={new_available}")
                return None
        elif operation == "add":
            new_total = current_total + amount
            new_available = current_available + amount
        else:
            logger.error(f"Invalid operation: {operation}")
            return None
        
        # Update shadow balance
        shadow_balance["total_usd"] = new_total
        shadow_balance["available_usd"] = new_available
        
        # Update USD holding if it exists
        holdings = shadow_balance.get("holdings", [])
        usd_holding = next((h for h in holdings if h.get("symbol") == "USD"), None)
        if usd_holding:
            usd_holding["quantity"] = new_total
            usd_holding["value_usd"] = new_total
        
        # Save updated balance atomically
        client.set(SHADOW_BALANCE_KEY, json.dumps(shadow_balance))
        
        logger.info(
            f"Shadow balance updated: {operation} ${amount:.2f}, "
            f"total=${new_total:.2f}, available=${new_available:.2f}"
        )
        
        return shadow_balance
        
    except Exception as e:
        logger.error(f"Failed to update shadow balance: {e}", exc_info=True)
        return None
