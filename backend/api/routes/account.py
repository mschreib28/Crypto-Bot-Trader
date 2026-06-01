"""Account API endpoint for equity, P&L, and risk limits."""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.risk.account import AccountTracker
from backend.redis import get_redis_client
from backend.redis.keys import SHADOW_BALANCE_KEY, SHADOW_INITIAL_EQUITY_KEY
from backend.api.routes.trading import get_bot_mode, get_shadow_live_mode

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


def _position_mark_price(p: Any) -> Optional[float]:
    """Spot for MTM: current_price when set, else entry_price."""
    if getattr(p, "current_price", None) is not None:
        return float(p.current_price)
    ep = getattr(p, "entry_price", None)
    return float(ep) if ep is not None else None


def _incremental_unrealized_usd(positions: List[Any]) -> float:
    """Per-position mark-to-market P&L (long/short), not full notional."""
    total = 0.0
    for p in positions:
        if p.quantity <= 0:
            continue
        mark = _position_mark_price(p)
        if mark is None or p.entry_price is None or p.entry_price <= 0:
            continue
        if p.side == "long":
            total += (mark - p.entry_price) * p.quantity
        else:
            total += (p.entry_price - mark) * p.quantity
    return total


def _open_positions_mark_value_usd(positions: List[Any]) -> float:
    """
    Sum of qty × mark for open positions.
    Fallback NAV uses Redis shadow total_usd (post-fill ledger) plus this sum — valid only
    while total_usd is maintained as cash+proceeds ledger (see update_shadow_balance), not full NAV.
    """
    s = 0.0
    for p in positions:
        if p.quantity <= 0:
            continue
        mark = _position_mark_price(p)
        if mark is None or mark <= 0:
            continue
        s += p.quantity * mark
    return s


def _allocated_entry_cost_usd(positions: List[Any]) -> float:
    """Sum of entry notional (qty × entry_price) for open positions."""
    total = 0.0
    for p in positions:
        if p.quantity <= 0:
            continue
        ep = getattr(p, "entry_price", None)
        if ep is None or ep <= 0:
            continue
        total += float(p.quantity) * float(ep)
    return total


def _account_holdings_from_positions(positions: List[Any]) -> List[dict]:
    """Open positions for /account: symbol, qty, MTM value, mark, entry."""
    out: List[dict] = []
    for p in positions:
        if p.quantity <= 0:
            continue
        mark = _position_mark_price(p)
        if mark is None or mark <= 0:
            continue
        qty = float(p.quantity)
        ep = float(p.entry_price)
        out.append(
            {
                "symbol": p.symbol,
                "quantity": round(qty, 8),
                "value_usd": round(qty * float(mark), 4),
                "current_price": round(float(mark), 8),
                "entry_price": round(ep, 8),
            }
        )
    return out


def _shadow_account_available_usd(
    current_equity: float,
    positions: List[Any],
    paper_usd_available: Optional[float],
) -> float:
    """Free USD for shadow /account: paper balance when present, else Redis, else equity − entry cost."""
    if paper_usd_available is not None:
        return round(float(paper_usd_available), 2)
    try:
        client = get_redis_client()
        shadow_balance_json = client.get(SHADOW_BALANCE_KEY)
        if shadow_balance_json:
            data = json.loads(shadow_balance_json)
            if "available_usd" in data:
                return round(float(data["available_usd"]), 2)
    except Exception as exc:
        logger.debug("shadow available_usd: Redis fallback failed (%s)", exc)
    cost = _allocated_entry_cost_usd(positions)
    return max(0.0, round(float(current_equity) - cost, 2))


async def _shadow_nav_paper_status() -> Optional[float]:
    """Kraken paper portfolio NAV from CLI (authoritative)."""
    try:
        from backend.execution.kraken_cli import paper_ensure_init, paper_status

        await paper_ensure_init()
        ps = await paper_status()
        return float(ps.current_value)
    except Exception as exc:
        logger.warning("shadow NAV: paper_status failed (%s)", exc)
        return None


def _shadow_nav_ledger_plus_marks(ledger_equity: float, positions: List[Any]) -> float:
    """NAV when paper_status is unavailable: ledger total_usd + open position full MTM."""
    return float(ledger_equity) + _open_positions_mark_value_usd(positions)


def _merge_shadow_holdings_by_symbol(holdings: List[dict]) -> List[dict]:
    """Merge rows that normalize to the same symbol (avoids duplicate React keys / double MTM)."""
    buckets: Dict[str, Dict[str, float]] = {}
    for h in holdings:
        sym = str(h.get("symbol", ""))
        qty = float(h.get("quantity", 0.0))
        val = float(h.get("value_usd", 0.0))
        if sym not in buckets:
            buckets[sym] = {"quantity": qty, "value_usd": val}
        else:
            b = buckets[sym]
            b["quantity"] += qty
            b["value_usd"] += val
    order = sorted(buckets.keys(), key=lambda s: (0 if s == "USD" else 1, s))
    out: List[dict] = []
    for sym in order:
        b = buckets[sym]
        out.append(
            {
                "symbol": sym,
                "quantity": round(b["quantity"], 8),
                "value_usd": round(b["value_usd"], 4),
            }
        )
    return out


def _shadow_nav_from_paper(usd_available: float, holdings: List[dict]) -> float:
    """
    Shadow portfolio NAV: free USD + crypto mark-to-market (per-row value_usd).
    USD / ZUSD rows in holdings are excluded so we do not double-count cash.
    """
    cash = float(usd_available)
    crypto_mtm = 0.0
    for h in holdings:
        sym = h.get("symbol", "")
        if sym in ("USD", "ZUSD"):
            continue
        crypto_mtm += float(h.get("value_usd", 0.0))
    return cash + crypto_mtm


async def _build_shadow_paper_holdings(pb: Any) -> List[dict]:
    """
    Per-asset USD values from qty × spot (not the legacy residual split across all cryptos).
    """
    from backend.execution.kraken_cli import (
        _USD_ASSETS,
        _normalize_kraken_asset,
        get_ticker,
        symbol_to_cli_pair,
    )

    holdings: List[dict] = []
    for asset, bal in pb.balances.items():
        if bal.total <= 0:
            continue
        sym = _normalize_kraken_asset(asset)
        if asset in _USD_ASSETS or sym == "USD":
            holdings.append(
                {
                    "symbol": "USD",
                    "quantity": bal.total,
                    "value_usd": bal.total,
                }
            )
            continue
        try:
            cli_pair = symbol_to_cli_pair(f"{sym}/USD")
            ticker = await get_ticker(cli_pair)
            value_usd = bal.total * ticker.last
        except Exception as exc:
            logger.warning("shadow holdings: ticker failed for %s (%s)", asset, exc)
            value_usd = 0.0
        holdings.append(
            {
                "symbol": sym,
                "quantity": bal.total,
                "value_usd": round(value_usd, 4),
            }
        )
    return holdings


async def _shadow_paper_balance_dict(pb: Any, ps: Any) -> Dict[str, Any]:
    """Shared /balance and /balance/shadow payload for Kraken paper (shadow mode)."""
    raw_holdings = await _build_shadow_paper_holdings(pb)
    holdings = _merge_shadow_holdings_by_symbol(raw_holdings)
    total_usd = _shadow_nav_from_paper(pb.usd_available, holdings)
    usd_available = pb.usd_available
    cli_value = float(ps.current_value)
    if abs(cli_value - total_usd) > max(0.05, 0.001 * max(cli_value, total_usd, 1.0)):
        logger.debug(
            "shadow NAV: computed=%.4f vs kraken paper status current_value=%.4f",
            total_usd,
            cli_value,
        )
    return {
        "total_usd": round(total_usd, 4),
        "available_usd": round(usd_available, 4),
        "holdings": holdings,
        "unrealized_pnl": round(ps.unrealized_pnl, 4),
        "unrealized_pnl_pct": round(ps.unrealized_pnl_pct, 4),
        "paper_trades": ps.total_trades,
    }


@router.get("/account")
async def get_account() -> dict:
    """Get current account state including equity, P&L, and risk limits."""
    import asyncio

    tracker = get_account_tracker()
    # Offload blocking Redis/API calls to thread pool
    state = await asyncio.to_thread(tracker.get_state)

    daily_loss_limit = float(os.getenv("DAILY_LOSS_LIMIT", "10.0"))
    risk_pct = float(os.getenv("RISK_PCT_PER_TRADE", "2.0"))
    risk_mult = risk_pct / 100.0

    _positions: List[Any] = []
    try:
        from backend.positions.tracker import get_position_tracker as _get_pt

        _positions = await asyncio.to_thread(_get_pt().get_all_positions)
    except Exception as exc:
        logger.warning("get_all_positions failed in get_account: %s", exc)

    from backend.risk.micro_mode import get_micro_mode_status, get_live_slots_status

    if get_shadow_live_mode():
        # NAV = USD available + crypto MTM (same formula as /balance shadow), not paper status alone.
        current_equity: Optional[float] = None
        paper_usd_available: Optional[float] = None
        try:
            from backend.execution.kraken_cli import paper_balance, paper_ensure_init

            await paper_ensure_init()
            pb = await paper_balance()
            paper_usd_available = float(pb.usd_available)
            raw_holdings = await _build_shadow_paper_holdings(pb)
            merged = _merge_shadow_holdings_by_symbol(raw_holdings)
            current_equity = _shadow_nav_from_paper(pb.usd_available, merged)
        except Exception as exc:
            logger.warning("shadow NAV: paper_balance/holdings path failed (%s)", exc)
        if current_equity is None:
            current_equity = await _shadow_nav_paper_status()
        if current_equity is None:
            current_equity = _shadow_nav_ledger_plus_marks(state.current_equity, _positions)
        current_equity = round(float(current_equity), 2)

        unrealized_inc = _incremental_unrealized_usd(_positions)
        total_pnl = current_equity - state.initial_equity
        realized_pnl = total_pnl - unrealized_inc

        pnl_percent = 0.0
        if state.initial_equity > 0:
            pnl_percent = (total_pnl / state.initial_equity) * 100.0

        max_risk_per_trade = round(current_equity * risk_mult, 2)
        micro_mode = await asyncio.to_thread(get_micro_mode_status, current_equity)
        live_slots_status = await asyncio.to_thread(get_live_slots_status, current_equity)

        holdings = _account_holdings_from_positions(_positions)
        available_usd = _shadow_account_available_usd(
            current_equity, _positions, paper_usd_available
        )

        return {
            "initial_equity": state.initial_equity,
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized_inc, 2),
            "current_equity": current_equity,
            "total_pnl": round(total_pnl, 2),
            "pnl_percent": round(pnl_percent, 2),
            "daily_pnl": state.daily_pnl,
            "max_risk_per_trade": max_risk_per_trade,
            "daily_loss_limit": daily_loss_limit,
            "risk_pct": risk_pct,
            "available_usd": available_usd,
            "holdings": holdings,
            "micro_mode": micro_mode,
            "live_slots_active": live_slots_status["current_slots"],
            "live_slots_max": live_slots_status["max_slots"],
        }

    # Live (non-shadow): ledger-style equity + full-position notional offset (unchanged).
    cash_pnl = state.current_equity - state.initial_equity
    unrealized_total = _open_positions_mark_value_usd(_positions)
    total_pnl = cash_pnl + unrealized_total

    pnl_percent = 0.0
    if state.initial_equity > 0:
        pnl_percent = (total_pnl / state.initial_equity) * 100.0

    micro_mode = await asyncio.to_thread(get_micro_mode_status, state.current_equity)
    live_slots_status = await asyncio.to_thread(get_live_slots_status, state.current_equity)

    ce = float(state.current_equity)
    allocated = _allocated_entry_cost_usd(_positions)
    available_usd = max(0.0, round(ce - allocated, 2))
    holdings = _account_holdings_from_positions(_positions)

    return {
        "initial_equity": state.initial_equity,
        "realized_pnl": round(cash_pnl, 2),
        "unrealized_pnl": round(unrealized_total, 2),
        "current_equity": state.current_equity,
        "total_pnl": round(total_pnl, 2),
        "pnl_percent": round(pnl_percent, 2),
        "daily_pnl": state.daily_pnl,
        "max_risk_per_trade": state.max_risk_per_trade,
        "daily_loss_limit": daily_loss_limit,
        "risk_pct": risk_pct,
        "available_usd": available_usd,
        "holdings": holdings,
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
        # Paper mode: read balance from Kraken CLI paper account (authoritative source)
        try:
            from backend.execution.kraken_cli import paper_balance, paper_status, paper_ensure_init

            await paper_ensure_init()
            pb = await paper_balance()
            ps = await paper_status()
            result = await _shadow_paper_balance_dict(pb, ps)
            logger.debug("Returning paper balance: total=$%.2f", result["total_usd"])
            return result

        except Exception as e:
            logger.warning(f"CLI paper balance failed ({e}), falling back to Redis shadow balance")
            # Fall back to Redis-stored shadow balance if CLI unavailable
            client = get_redis_client()
            try:
                shadow_balance_json = client.get(SHADOW_BALANCE_KEY)
                if shadow_balance_json:
                    return json.loads(shadow_balance_json)
            except Exception:
                pass
            return {
                "total_usd": 50.0,
                "available_usd": 50.0,
                "holdings": [{"symbol": "USD", "quantity": 50.0, "value_usd": 50.0}]
            }
    
    # Live mode: fetch real balance from Kraken CLI
    try:
        from backend.execution.kraken_cli import get_live_account_balance
        balance = await get_live_account_balance()

        # Filter out dust holdings (< $0.01 value)
        MIN_HOLDING_VALUE = 0.01
        filtered_holdings = [
            h for h in balance.get("holdings", [])
            if h.get("value_usd", 0) >= MIN_HOLDING_VALUE
        ]
        balance["holdings"] = filtered_holdings

        logger.info(
            f"Balance fetched via CLI: total=${balance['total_usd']}, "
            f"holdings={len(filtered_holdings)}"
        )
        return balance

    except Exception as e:
        logger.error(f"Failed to fetch Kraken balance via CLI: {e}")
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

        client = get_redis_client()
        positions_closed = 0
        metrics_keys_deleted = 0
        bot_mode = get_bot_mode()

        if bot_mode == "SHADOW":
            from backend.positions.tracker import get_position_tracker, purge_all_position_redis_keys
            from backend.risk.metrics import clear_all_strategy_metrics_and_r_multiples, reset_strategy_metrics_for_ids

            tracker = get_position_tracker()
            positions_closed = len(tracker.get_all_positions())
            purge_all_position_redis_keys(client)
            metrics_keys_deleted = clear_all_strategy_metrics_and_r_multiples(client)

            try:
                from backend.db import get_session
                from backend.db.models import Strategy

                db_session = get_session()
                try:
                    strategies = db_session.query(Strategy).all()
                    all_ids = [sid for s in strategies for sid in (str(s.id), s.name)]
                    reset_strategy_metrics_for_ids(all_ids)
                    logger.info(
                        f"Reset auxiliary metrics keys for {len(strategies)} strategy/strategies after balance reset"
                    )
                finally:
                    db_session.close()
            except Exception as e:
                logger.warning(f"Failed to reset auxiliary strategy metrics after balance reset: {e}")
        else:
            logger.info(
                "set_shadow_balance: skipping Redis position purge and metrics clear (bot not in SHADOW mode)"
            )

        # Reset Kraken CLI paper account to the requested balance (authoritative source)
        try:
            from backend.execution.kraken_cli import paper_reset, paper_init, KrakenCLIError
            try:
                await paper_reset(balance=request.total_usd)
                logger.info(f"Kraken paper account reset to ${request.total_usd:.2f}")
            except KrakenCLIError:
                # Account might not exist yet — initialise instead
                await paper_init(balance=request.total_usd)
                logger.info(f"Kraken paper account initialised with ${request.total_usd:.2f}")
        except Exception as cli_err:
            logger.warning(f"CLI paper reset failed ({cli_err}), continuing with Redis-only balance")

        # Mirror to Redis for legacy compatibility and fallback reads
        client.set(SHADOW_BALANCE_KEY, json.dumps(shadow_balance))

        # Always update initial equity on reset so P&L is relative to the new balance
        client.set(SHADOW_INITIAL_EQUITY_KEY, str(request.total_usd))
        logger.info(f"Shadow initial equity reset to: ${request.total_usd}")

        logger.info(
            f"Shadow balance set: total=${request.total_usd}, "
            f"available=${available_usd}, holdings={len(holdings)}"
        )

        # Log to activity feed
        from backend.api.routes.events import log_activity

        if bot_mode == "SHADOW":
            reset_message = (
                f"Shadow account reset — balance set to ${request.total_usd:.2f}, "
                "all positions cleared"
            )
        else:
            reset_message = (
                f"Shadow balance mirror set to ${request.total_usd:.2f} "
                f"(Redis positions and metrics unchanged — bot mode {bot_mode})"
            )

        log_activity(
            activity_type="system",
            message=reset_message,
            details={
                "shadow_balance": shadow_balance,
                "positions_closed": positions_closed,
                "metrics_keys_deleted": metrics_keys_deleted,
                "mode": bot_mode,
            },
        )

        return {
            **shadow_balance,
            "positions_closed": positions_closed,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set shadow balance: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set shadow balance")


@router.get("/balance/shadow")
async def get_shadow_balance() -> dict:
    """
    Get current paper trading balance from Kraken CLI (authoritative source).

    Falls back to Redis mirror if CLI is unavailable.
    """
    try:
        from backend.execution.kraken_cli import paper_balance, paper_status, paper_ensure_init

        await paper_ensure_init()
        pb = await paper_balance()
        ps = await paper_status()
        body = await _shadow_paper_balance_dict(pb, ps)
        return {**body, "starting_balance": ps.starting_balance}

    except Exception as e:
        logger.warning(f"CLI paper balance failed ({e}), falling back to Redis")
        try:
            client = get_redis_client()
            shadow_balance_json = client.get(SHADOW_BALANCE_KEY)
            if shadow_balance_json:
                return json.loads(shadow_balance_json)
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="Paper trading state unavailable")


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
