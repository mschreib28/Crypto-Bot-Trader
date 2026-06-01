"""Main execution function for approved TradeIntents.

This module provides:
- execute_trade: New function with 2% rule position sizing (Ticket 35)
- execute_approved_intent: Legacy function for RiskDecision-based execution

Flow for execute_trade:
1. Get account equity
2. Calculate position size using 2% rule
3. Validate against risk limits
4. Execute on Kraken
5. Return Fill object
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.risk.evaluator import TradeIntent

from backend.risk.models import RiskDecision
from backend.risk.portfolio import get_current_equity
from backend.risk.two_percent import TwoPercentRule
from backend.risk.account import AccountTracker
from backend.risk.sizing import PositionSizer
from backend.execution.models import Fill
from backend.execution.order_manager import calculate_slippage
from backend.execution import kraken_cli
from backend.db import get_session
from backend.db.models import Signal
from backend.positions.tracker import get_position_tracker
from backend.api.routes.events import log_activity

logger = logging.getLogger(__name__)

# Async lock for order serialization (one live order at a time)
_execution_lock: Optional[asyncio.Lock] = None


def _get_execution_lock() -> asyncio.Lock:
    """Lazily create the asyncio.Lock (must be inside the running event loop)."""
    global _execution_lock
    if _execution_lock is None:
        _execution_lock = asyncio.Lock()
    return _execution_lock


# 2% rule position sizing components (Ticket 35)
_two_percent_rule: Optional[TwoPercentRule] = None
_account_tracker: Optional[AccountTracker] = None
_position_sizer: Optional[PositionSizer] = None


def _get_two_percent_rule() -> TwoPercentRule:
    """Get or create TwoPercentRule instance."""
    global _two_percent_rule
    if _two_percent_rule is None:
        _two_percent_rule = TwoPercentRule()
    return _two_percent_rule


def _get_account_tracker() -> AccountTracker:
    """Get or create AccountTracker instance."""
    global _account_tracker
    if _account_tracker is None:
        _account_tracker = AccountTracker()
    return _account_tracker


def _get_position_sizer() -> PositionSizer:
    """Get or create PositionSizer instance."""
    global _position_sizer
    if _position_sizer is None:
        _position_sizer = PositionSizer()
    return _position_sizer



async def execute_trade(
    intent: "TradeIntent",
    current_price: float,
    live: bool = False,
) -> Optional[Fill]:
    """
    Execute trade with 2% rule position sizing.
    
    This function implements Ticket 35 from EXECUTION_PLAN_M5.md:
    1. Get account equity
    2. Calculate position size using 2% rule and stop-loss
    3. Validate 2% rule
    4. Validate Kraken minimum
    5. Check daily loss limit
    6. Execute on Kraken
    7. Return Fill
    
    Args:
        intent: TradeIntent from strategy signal
        current_price: Current market price for the symbol
        live: True = real Kraken orders; False = paper/shadow execution path
        
    Returns:
        Fill object if trade executed, None if rejected
    """
    # SECURITY: live=True only when canonical bot mode is LIVE (defense in depth).
    try:
        from backend.api.routes.trading import get_bot_mode

        if live and get_bot_mode() != "LIVE":
            logger.warning(
                f"execute_trade(..., live=True) for {intent.symbol} but bot mode is not LIVE — aborting."
            )
            return None
    except Exception as _gate_err:
        logger.error(
            f"execute_trade() cannot verify bot mode gate: {_gate_err}. "
            f"Aborting trade for {intent.symbol} (fail-safe)."
        )
        return None

    use_paper = not live
    metadata = intent.metadata or {}
    strategy_canonical = metadata.get("strategy_canonical")

    # Get risk components
    two_percent_rule = _get_two_percent_rule()
    account_tracker = _get_account_tracker()
    position_sizer = _get_position_sizer()

    # 1. Get account equity
    equity = account_tracker.current_equity
    risk_pct = float(os.getenv("RISK_PCT_PER_TRADE", "2.0"))
    stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "5.0"))

    # TICKET-708 / Task 3: paper sizing uses global shadow balance or per-strategy SIM balance
    shadow_equity = equity  # Default to account equity
    if use_paper:
        try:
            if strategy_canonical:
                from backend.supervisor.store import ensure_strategy_sim_balance

                bal = ensure_strategy_sim_balance(strategy_canonical)
                shadow_equity = float(bal.get("total_usd", 500.0))
            else:
                from backend.redis import get_redis_client
                from backend.redis.keys import SHADOW_BALANCE_KEY
                import json

                redis_client = get_redis_client()
                shadow_balance_json = redis_client.get(SHADOW_BALANCE_KEY)

                if shadow_balance_json:
                    shadow_balance = json.loads(shadow_balance_json)
                    shadow_equity = shadow_balance.get("total_usd", 31.80)
                else:
                    shadow_equity = 31.80
        except Exception as e:
            logger.warning(f"Failed to get paper sizing equity: {e}, using default")
            shadow_equity = 31.80 if not strategy_canonical else 500.0
    
    # For SELL orders: use actual held quantity, not calculated size
    if intent.side == "sell":
        tracker = get_position_tracker()
        position = tracker.get_position(intent.symbol)
        
        if position is None or position.quantity <= 0:
            logger.warning(
                f"SELL rejected: no position held for {intent.symbol}"
            )
            log_activity(
                activity_type="warning",
                message=f"SELL signal ignored for {intent.symbol} - no position",
                details={
                    "symbol": intent.symbol,
                    "strategy": intent.strategy_id,
                    "reason": "no_position",
                },
            )
            return None
        
        # Cancel any existing stop-loss order before selling
        if position.stop_loss_order_id:
            try:
                logger.info(f"Cancelling stop-loss order {position.stop_loss_order_id} before SELL")
                await kraken_cli.cancel_order(position.stop_loss_order_id)
                logger.info(f"Stop-loss order cancelled: {position.stop_loss_order_id}")
            except Exception as e:
                logger.warning(f"Failed to cancel stop-loss order: {e} (may already be filled/cancelled)")
        
        # Use actual held quantity for sell (never recalculate from risk sizing)
        from backend.positions.quantity import floor_qty_8dp, is_valid_position_quantity

        sell_quantity = position.quantity
        if not is_valid_position_quantity(sell_quantity):
            logger.error(
                f"SELL rejected: invalid/dust position quantity "
                f"({sell_quantity!r}) for {intent.symbol} — purging"
            )
            tracker.purge_corrupted_position(
                intent.symbol, reason="invalid_quantity_before_sell"
            )
            return None

        sell_quantity = floor_qty_8dp(sell_quantity)
        position_value_usd = sell_quantity * current_price

        # Validate sell sizing inputs before proceeding
        if sell_quantity <= 0:
            logger.error(
                f"SELL rejected: position quantity is non-positive "
                f"({sell_quantity}) for {intent.symbol}"
            )
            tracker.purge_corrupted_position(
                intent.symbol, reason="zero_quantity_after_floor"
            )
            return None
        if position_value_usd <= 0:
            logger.error(
                f"SELL rejected: computed position value is non-positive "
                f"(qty={sell_quantity}, price={current_price}) for {intent.symbol}"
            )
            return None

        # Get stop_loss_price from position if available
        stop_loss_price = position.stop_loss_price if position.stop_loss_price else None

        # Calculate stop_loss_pct from position's stop_loss_price if available
        if stop_loss_price and position.entry_price:
            if position.side == "long":
                stop_loss_pct_calc = ((position.entry_price - stop_loss_price) / position.entry_price) * 100.0
            else:  # short
                stop_loss_pct_calc = ((stop_loss_price - position.entry_price) / position.entry_price) * 100.0
        else:
            stop_loss_pct_calc = 0.0  # Use 0.0 when no stop_loss_price available

        max_risk_usd = position_value_usd * (stop_loss_pct_calc / 100.0) if stop_loss_pct_calc > 0 else 0.0

        logger.info(
            f"SELL order: using actual position qty={sell_quantity} "
            f"for {intent.symbol} (value=${position_value_usd:.2f})"
        )

        # Create a simple sizing object for sell (matching PositionSize structure)
        # Ensure all attributes match PositionSize dataclass structure
        class SellSizing:
            pass
        sizing = SellSizing()
        sizing.quantity = sell_quantity
        sizing.position_size_usd = position_value_usd
        sizing.max_risk_usd = max_risk_usd
        sizing.stop_loss_price = stop_loss_price  # None if not available
        sizing.stop_loss_pct = stop_loss_pct_calc  # 0.0 if no stop_loss_price
        
        # TICKET-706: For SELL orders in shadow mode, log ORDER_INTENT early
        if use_paper:
            metadata = intent.metadata or {}
            bar_timestamp = metadata.get("bar_timestamp") or metadata.get("timestamp")
            strategy_interval = metadata.get("timeframe") or metadata.get("interval") or "15m"
            candle_tag = f"candle={bar_timestamp} tf={strategy_interval}" if bar_timestamp else ""
            
            log_activity(
                activity_type="ORDER_INTENT",
                message=f"Order intent: {intent.side.upper()} {sizing.quantity} {intent.symbol} @ ${current_price:.2f} {candle_tag}".strip(),
                details={
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": sizing.quantity,
                    "price": current_price,
                    "position_size_usd": sizing.position_size_usd,
                    "max_risk_usd": sizing.max_risk_usd,
                    "stop_loss_price": sizing.stop_loss_price,
                    "stop_loss_pct": sizing.stop_loss_pct,
                    "strategy": intent.strategy_id,
                    "equity": shadow_equity,
                    "mode": "shadow_live",
                    "bar_timestamp": bar_timestamp,
                    "timeframe": strategy_interval,
                },
            )
            logger.info(
                f"[SHADOW-LIVE] ORDER_INTENT logged (SELL): {intent.side.upper()} {sizing.quantity} {intent.symbol} "
                f"@ ${current_price:.2f}"
            )
    else:
        # BUY orders: use equity-based 2% rule sizing or Scout sizing
        # Extract metadata first
        metadata = intent.metadata or {}
        
        # Check for Soldier scale-in (fixed $3.00 size)
        soldier_scale_in = metadata.get("soldier_scale_in", False)
        
        # Check if equity < $50: Use Scout sizing (fixed $1.50, 42% stop)
        # Note: Soldier scale-in uses fixed sizing, so skip Scout sizing check
        use_scout_sizing = equity < 50.0 and not soldier_scale_in
        
        if soldier_scale_in:
            logger.info(
                f"Soldier scale-in: using fixed ${metadata.get('scale_in_size_usd', '3.00')} entry"
            )
        elif use_scout_sizing:
            logger.info(
                f"Scout sizing: equity=${equity:.2f} < $50, using fixed $1.50 entry with 42% stop"
            )
        else:
            logger.info(
                f"Position sizing: equity=${equity:.2f}, risk_pct={risk_pct}%, "
                f"stop_loss_pct={stop_loss_pct}%, price=${current_price}"
            )
        
        # 2. Extract stop loss and ATR from metadata (if available)
        explicit_stop_loss_price = metadata.get("stop_loss_price")
        strategy_specific = metadata.get("strategy_specific", {})
        atr_value = strategy_specific.get("atr")
        if soldier_scale_in:
            # TICKET-602: Fixed Soldier scale-in size to $2.00
            soldier_scale_in_size_usd = float(metadata.get("scale_in_size_usd", os.getenv("SOLDIER_SCALE_IN_SIZE_USD", "2.00")))
            quantity = soldier_scale_in_size_usd / current_price
            
            # Create sizing object for Soldier scale-in (no stop-loss, breakeven will be set separately)
            from backend.risk.sizing import PositionSize
            sizing = PositionSize(
                max_risk_usd=0.0,  # Risk managed by breakeven stop
                position_size_usd=round(soldier_scale_in_size_usd, 2),
                quantity=round(quantity, 8),
                stop_loss_price=0.0,  # Will be set to breakeven after execution
                stop_loss_pct=0.0,
            )
            logger.info(
                f"Soldier scale-in sizing: fixed ${soldier_scale_in_size_usd:.2f} @ ${current_price:.2f} "
                f"-> qty={sizing.quantity}"
            )
        else:
            # 2. Calculate position size (with micro mode checks or Scout sizing)
            # TICKET-706, 708: In shadow mode, use shadow equity and handle sizing failures gracefully
            sizing = None
            sizing_error = None
            
            try:
                # Use shadow equity in shadow mode, account equity otherwise
                sizing_equity = shadow_equity if use_paper else equity
                # Ensure minimum equity for calculation
                sizing_equity = max(sizing_equity, 10.0) if use_paper else sizing_equity
                
                sizing = position_sizer.calculate(
                    account_equity=sizing_equity,
                    risk_pct=risk_pct,
                    entry_price=current_price,
                    stop_loss_pct=stop_loss_pct,
                    strategy_id=intent.strategy_id,
                    atr=atr_value,
                    stop_loss_price=explicit_stop_loss_price,
                    symbol=intent.symbol,
                )
            except Exception as e:
                sizing_error = str(e)
                logger.warning(f"Position sizing failed: {e}")

        if sizing is None:
            logger.warning(
                f"Trade skipped: sizing returned no position for {intent.symbol}. "
                f"{f'Error: {sizing_error}' if sizing_error else 'Check micro mode or minimum notional in logs.'}"
            )
            log_activity(
                activity_type="error",
                message=f"Sizing rejected {intent.symbol}: below minimum notional",
                details={
                    "symbol": intent.symbol,
                    "strategy": intent.strategy_id,
                    "reason": sizing_error or "sizing_skip",
                    "equity": shadow_equity if use_paper else equity,
                },
            )
            return None
        
        # BUG3: Hard cap max risk to 1% of equity to prevent catastrophic sizing.
        # 2% rule on $500 → $200 position with 5% stop → $10 max loss. But if price
        # blows past the stop before invalidation fires, the actual loss can be 2-3×.
        # Capping at 1% ($5 max risk on $500) limits position to $100 at 5% stop.
        if sizing is not None and getattr(sizing, "max_risk_usd", 0) > 0:
            _sizing_equity_cap = shadow_equity if use_paper else equity
            _max_risk_cap = _sizing_equity_cap * 0.01
            if _max_risk_cap > 0 and sizing.max_risk_usd > _max_risk_cap:
                _scale = _max_risk_cap / sizing.max_risk_usd
                sizing.max_risk_usd = _max_risk_cap
                sizing.position_size_usd = sizing.position_size_usd * _scale
                sizing.quantity = sizing.quantity * _scale
                logger.info(
                    f"1% risk cap applied for {intent.symbol}: "
                    f"max_risk=${_max_risk_cap:.2f}, "
                    f"position=${sizing.position_size_usd:.2f}, qty={sizing.quantity:.6f}"
                )

        # Supervisor size factor: applied after 1% cap so REDUCED never exceeds ACTIVE risk
        _supervisor_factor = float(metadata.get("supervisor_size_factor", 1.0))
        if sizing is not None and _supervisor_factor != 1.0:
            sizing.max_risk_usd *= _supervisor_factor
            sizing.position_size_usd *= _supervisor_factor
            sizing.quantity *= _supervisor_factor
            logger.info(
                f"Supervisor scaled position by {_supervisor_factor}× → "
                f"position=${sizing.position_size_usd:.2f}, qty={sizing.quantity:.6f}"
            )

        # 2.5 Check available USD balance - can't buy more than we have
        # TICKET-708: In shadow mode, check shadow balance instead of account balance
        if use_paper:
            # Shadow mode: check shadow balance
            try:
                from backend.redis.keys import SHADOW_BALANCE_KEY
                import json
                redis_client = get_redis_client()
                shadow_balance_json = redis_client.get(SHADOW_BALANCE_KEY)
                
                if shadow_balance_json:
                    shadow_balance = json.loads(shadow_balance_json)
                    available_usd = shadow_balance.get("available_usd", shadow_equity)
                else:
                    available_usd = shadow_equity
                
                position_cost = sizing.position_size_usd + (sizing.position_size_usd * 0.0026)  # Add estimated fees
                
                if available_usd < position_cost:
                    # TICKET-706: Still log ORDER_INTENT even if balance insufficient
                    logger.warning(
                        f"[SHADOW-LIVE] Shadow balance insufficient: available=${available_usd:.2f}, "
                        f"required=${position_cost:.2f}. Will log ORDER_INTENT with adjusted size."
                    )
                    # Adjust sizing to available balance
                    adjusted_usd = max(1.0, available_usd - 0.50)  # Leave $0.50 buffer, min $1
                    adjusted_qty = adjusted_usd / current_price
                    sizing.position_size_usd = adjusted_usd
                    sizing.quantity = adjusted_qty
                    sizing.max_risk_usd = adjusted_usd * (stop_loss_pct / 100.0)
            except Exception as e:
                logger.warning(f"Failed to check shadow balance: {e}, proceeding with calculated size")
        else:
            # Live mode: check account balance via CLI
            try:
                from backend.execution.kraken_cli import get_balance_sync, _USD_ASSETS
                _raw_bal = get_balance_sync()
                available_usd = sum(
                    float(v) for k, v in _raw_bal.items() if k in _USD_ASSETS
                )
                
                if sizing.position_size_usd > available_usd:
                    if available_usd < 1.0:  # Minimum $1 trade
                        logger.warning(
                            f"BUY rejected: insufficient USD balance. "
                            f"Need ${sizing.position_size_usd:.2f}, have ${available_usd:.2f}"
                        )
                        log_activity(
                            activity_type="warning",
                            message=f"BUY {intent.symbol} rejected - insufficient USD (${available_usd:.2f})",
                            details={
                                "symbol": intent.symbol,
                                "strategy": intent.strategy_id,
                                "reason": "insufficient_usd",
                                "needed": sizing.position_size_usd,
                                "available": available_usd,
                            },
                        )
                        return None
                    
                    # Reduce position to available balance (leave $0.50 buffer for fees)
                    adjusted_usd = available_usd - 0.50
                    adjusted_qty = adjusted_usd / current_price
                    logger.info(
                        f"BUY: Reducing position from ${sizing.position_size_usd:.2f} to "
                        f"${adjusted_usd:.2f} (available=${available_usd:.2f})"
                    )
                    sizing.position_size_usd = adjusted_usd
                    sizing.quantity = adjusted_qty
                    sizing.max_risk_usd = adjusted_usd * (stop_loss_pct / 100.0)
            except Exception as e:
                logger.warning(f"Failed to check USD balance, proceeding with calculated size: {e}")
        
        # 3. Validate 2% rule (only for BUY - we're not risking more on sells)
        # TICKET-706: Skip validations in shadow mode, but log ORDER_INTENT first
        if use_paper:
            # TICKET-706: Log ORDER_INTENT early in shadow mode (before validations that could fail)
            bar_timestamp = metadata.get("bar_timestamp") or metadata.get("timestamp")
            strategy_interval = metadata.get("timeframe") or metadata.get("interval") or "15m"
            candle_tag = f"candle={bar_timestamp} tf={strategy_interval}" if bar_timestamp else ""
            
            log_activity(
                activity_type="ORDER_INTENT",
                message=f"Order intent: {intent.side.upper()} {sizing.quantity} {intent.symbol} @ ${current_price:.2f} {candle_tag}".strip(),
                details={
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": sizing.quantity,
                    "price": current_price,
                    "position_size_usd": sizing.position_size_usd,
                    "max_risk_usd": sizing.max_risk_usd,
                    "stop_loss_price": sizing.stop_loss_price,
                    "stop_loss_pct": sizing.stop_loss_pct,
                    "strategy": intent.strategy_id,
                    "equity": shadow_equity,
                    "mode": "shadow_live",
                    "bar_timestamp": bar_timestamp,
                    "timeframe": strategy_interval,
                    "tp1_price": metadata.get("tp1_price"),
                    "tp2_price": metadata.get("tp2_price"),
                    "tp1_R": metadata.get("tp1_R"),
                    "tp2_R": metadata.get("tp2_R"),
                    "sizing_error": sizing_error,  # Include if sizing failed
                },
            )
            logger.info(
                f"[SHADOW-LIVE] ORDER_INTENT logged: {intent.side.upper()} {sizing.quantity} {intent.symbol} "
                f"@ ${current_price:.2f} (risk: ${sizing.max_risk_usd:.2f}, equity: ${shadow_equity:.2f})"
            )
        else:
            # Live mode: enforce validations
            approved, reason = two_percent_rule.validate_trade(
                trade_risk=sizing.max_risk_usd,
                account_equity=equity,
            )
            if not approved:
                logger.warning(f"Trade rejected by 2% rule: {reason}")
                return None
            
            # 4. Validate Kraken minimum
            valid, reason = position_sizer.validate_minimum(sizing.position_size_usd)
            if not valid:
                logger.warning(f"Trade rejected: {reason}")
                return None
    
    # 5. Check daily loss limit (applies to both BUY and SELL)
    # TICKET-706: Skip in shadow mode (shadow balance tracks P&L separately)
    if not use_paper:
        daily_loss_limit = float(os.getenv("DAILY_LOSS_LIMIT", "10.0"))
        if account_tracker.daily_pnl <= -daily_loss_limit:
            logger.error(
                f"Daily loss limit reached: ${account_tracker.daily_pnl:.2f} "
                f"exceeds -${daily_loss_limit:.2f} limit. Halting."
            )
            return None
    
    if use_paper:
        # Paper mode execution via Kraken CLI — ORDER_INTENT already logged above.
        # Log stop/TP intents before placing the order.
        stop_loss_price = metadata.get("stop_loss_price") or sizing.stop_loss_price
        if stop_loss_price:
            # Use the actual position entry price (not current market price) for correct pct display
            _entry_for_log = position.entry_price if intent.side == "sell" and position else current_price
            log_activity(
                activity_type="STOP_INTENT",
                message=f"Stop-loss intent: SELL {sizing.quantity} {intent.symbol} @ ${stop_loss_price:.2f}",
                details={
                    "symbol": intent.symbol,
                    "stop_loss_price": stop_loss_price,
                    "stop_loss_pct": sizing.stop_loss_pct,
                    "entry_price": _entry_for_log,
                    "risk_usd": sizing.max_risk_usd,
                    "strategy": intent.strategy_id,
                    "mode": "paper",
                },
            )

        tp1_price = metadata.get("tp1_price")
        tp2_price = metadata.get("tp2_price")
        if tp1_price:
            log_activity(
                activity_type="TAKE_PROFIT_INTENT",
                message=f"Take-profit intent: TP1 @ ${tp1_price:.2f}, TP2 @ {f'${tp2_price:.2f}' if tp2_price else 'N/A'}",
                details={
                    "symbol": intent.symbol,
                    "tp1_price": tp1_price,
                    "tp2_price": tp2_price,
                    "tp1_R": metadata.get("tp1_R"),
                    "tp2_R": metadata.get("tp2_R"),
                    "entry_price": current_price,
                    "strategy": intent.strategy_id,
                    "mode": "paper",
                },
            )

        # Execute via Kraken CLI paper trading
        try:
            from backend.execution.models import Fill
            from backend.execution.kraken_cli import (
                paper_buy,
                paper_sell,
                paper_ensure_init,
                symbol_to_cli_pair,
                KrakenCLIError,
            )

            cli_pair = symbol_to_cli_pair(intent.symbol)
            _is_forced_exit = (intent.metadata or {}).get("forced_exit", False)

            # Ensure paper account is initialised (no-op if already done)
            await paper_ensure_init(balance=shadow_equity)

            if intent.side == "buy":
                # Always use market orders in paper/shadow mode — limit orders leave tokens
                # in a "pending" state in the CLI paper account, causing subsequent sell
                # attempts to fail with "Insufficient balance" until the order fills.
                paper_fill = await paper_buy(
                    pair=cli_pair,
                    quantity=sizing.quantity,
                    order_type="market",
                    price=None,
                )
            else:
                paper_fill = await paper_sell(pair=cli_pair, quantity=sizing.quantity)

            if paper_fill is None:
                _paper_err = (
                    "buy_skipped_zero_quantity"
                    if intent.side == "buy"
                    else "paper_sell_skipped_zero_quantity"
                )
                if intent.side == "sell":
                    get_position_tracker().purge_corrupted_position(
                        intent.symbol,
                        reason="paper_sell_zero_quantity",
                    )
                log_activity(
                    activity_type="error",
                    message=(
                        f"Paper order failed: {intent.side.upper()} {intent.symbol} — "
                        "quantity skipped (non-finite, rounds to zero at 8dp, or below "
                        "minimum viable base size); see runner logs"
                    ),
                    details={
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "error": _paper_err,
                        "mode": "paper",
                    },
                )
                return None

            fill = Fill(
                order_id=paper_fill.order_id,
                symbol=intent.symbol,
                side=intent.side,
                executed_price=paper_fill.price,
                quantity=paper_fill.volume,
                fees=paper_fill.fee,
                slippage=0.0,
                exchange_order_id=None,
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

            if intent.side == "buy":
                try:
                    from backend.analytics.store import capture_entry_snapshot

                    capture_entry_snapshot(
                        symbol=intent.symbol,
                        strategy=intent.strategy_id or "unknown",
                        entry_price=fill.executed_price,
                        quantity=fill.quantity,
                        metadata=intent.metadata,
                    )
                except Exception as e:
                    logger.debug(f"Trade analytics capture failed: {e}")

            # Record fill to Redis position tracker (dashboard reads from here)
            tracker = get_position_tracker()
            strategy_id = intent.strategy_id if intent.side == "buy" else None
            tracker.record_fill(
                fill,
                strategy_id=strategy_id,
                execution_live=live,
                strategy_canonical=strategy_canonical if intent.side == "buy" else None,
            )

            # BUG2/4: Persist stop_loss_price on the position so _check_stop_loss_exit
            # can enforce the stop level in paper/shadow mode (no real exchange stop order).
            if intent.side == "buy":
                _sl_price = metadata.get("stop_loss_price") or getattr(sizing, "stop_loss_price", None)
                if _sl_price:
                    _paper_pos = tracker.get_position(intent.symbol)
                    if _paper_pos:
                        _paper_pos.stop_loss_price = float(_sl_price)
                        from backend.redis import get_redis_client as _get_redis_sl
                        from backend.redis.keys import POSITION_KEY as _PK
                        _redis_sl = _get_redis_sl()
                        _redis_sl.hset(_PK.format(symbol=intent.symbol), mapping=_paper_pos.to_dict())
                        logger.debug(
                            f"[PAPER] Stored stop_loss_price=${float(_sl_price):.4f} for {intent.symbol}"
                        )

            # Store TP1 price in Redis for paper positions (monitor uses this)
            if intent.side == "buy" and tp1_price:
                from backend.redis import get_redis_client
                from backend.redis.keys import POSITION_TP1_PRICE_KEY
                _redis = get_redis_client()
                _redis.set(POSITION_TP1_PRICE_KEY.format(symbol=intent.symbol), str(tp1_price))
                logger.debug(f"[PAPER] Stored TP1 price for {intent.symbol}: ${tp1_price:.2f}")

            # BUG6: Capture exit_reason from intent metadata for TRADE_PLACED log
            _exit_reason = metadata.get("exit_reason")

            log_activity(
                activity_type="TRADE_PLACED",
                message=(
                    f"Trade placed: {intent.side.upper()} {paper_fill.volume} {intent.symbol} "
                    f"@ ${paper_fill.price:.2f} (fee=${paper_fill.fee:.4f})"
                ),
                details={
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": paper_fill.volume,
                    "price": paper_fill.price,
                    "cost": paper_fill.cost,
                    "fees": paper_fill.fee,
                    "order_id": paper_fill.order_id,
                    "strategy": intent.strategy_id,
                    "mode": "paper",
                    "exit_reason": _exit_reason,
                },
            )

            logger.info(
                f"[PAPER] Order filled: {intent.side.upper()} {paper_fill.volume} {intent.symbol} "
                f"@ ${paper_fill.price:.2f} via {paper_fill.order_id}"
            )
            return fill

        except KrakenCLIError as e:
            logger.error(f"[PAPER] CLI error executing {intent.side} {intent.symbol}: {e}", exc_info=True)
            log_activity(
                activity_type="error",
                message=f"Paper order failed: {intent.side.upper()} {intent.symbol} — {e}",
                details={"symbol": intent.symbol, "side": intent.side, "error": str(e), "mode": "paper"},
            )
            return None
        except Exception as e:
            logger.error(f"[PAPER] Unexpected error executing {intent.side} {intent.symbol}: {e}", exc_info=True)
            return None
    
    # 6. Execute on Kraken (live mode)
    logger.info(
        f"Placing order: {intent.side} {sizing.quantity} {intent.symbol} "
        f"@ ${current_price:.2f} (risk: ${sizing.max_risk_usd:.2f})"
    )
    
    # TICKET-610: Live Execution Preview (only in live mode, not paper mode)
    try:
        live_trading = os.getenv("LIVE_TRADING", "false").lower() == "true"

        if live_trading and live:
            # Log PREVIEW event before execution
            metadata = intent.metadata or {}
            soldier_scale_in = metadata.get("soldier_scale_in", False)
            position_type = "Soldier" if soldier_scale_in else "Scout"
            
            log_activity(
                activity_type="PREVIEW: LIVE_ORDER_PENDING",
                message=(
                    f"PREVIEW: LIVE_ORDER_PENDING - {intent.side.upper()} {sizing.quantity} {intent.symbol} "
                    f"@ ${current_price:.2f} ({position_type} sizing: ${sizing.position_size_usd:.2f}, "
                    f"risk: ${sizing.max_risk_usd:.2f})"
                ),
                details={
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": sizing.quantity,
                    "price": current_price,
                    "position_size_usd": sizing.position_size_usd,
                    "max_risk_usd": sizing.max_risk_usd,
                    "position_type": position_type,
                    "strategy": intent.strategy_id,
                    "preview_duration_seconds": 5,
                },
            )
            logger.info(
                f"PREVIEW: LIVE_ORDER_PENDING logged for {intent.symbol} "
                f"({position_type} ${sizing.position_size_usd:.2f}, risk ${sizing.max_risk_usd:.2f})"
            )
    except Exception as e:
        logger.warning(f"Failed to log PREVIEW event: {e}, proceeding with execution")
    
    # TICKET-604: Double-latch check - Query database for existing order on same candle
    metadata = intent.metadata or {}
    bar_timestamp = metadata.get("bar_timestamp") or metadata.get("timestamp")
    
    if bar_timestamp:
        try:
            from backend.db import get_session
            from backend.db.models import Order, Signal
            session = get_session()
            try:
                # Check if order exists for same strategy_id, symbol, and bar_timestamp
                # Query: Find orders linked to signals with matching strategy_id, symbol, and bar_timestamp
                existing_order_count = session.query(Order).join(
                    Signal, Order.signal_id == Signal.id
                ).filter(
                    Signal.strategy_id == intent.strategy_id,
                    Signal.symbol == intent.symbol,
                    Signal.signal_metadata['bar_timestamp'].astext == str(bar_timestamp)
                ).count()
                
                if existing_order_count > 0:
                    logger.warning(
                        f"EXECUTION_ALLOWED gate closed (DB check): order already exists for "
                        f"candle={bar_timestamp}, strategy={intent.strategy_id}, symbol={intent.symbol}. "
                        f"Skipping execution."
                    )
                    log_activity(
                        activity_type="warning",
                        message=(
                            f"Execution blocked: Order already exists for {intent.symbol} "
                            f"on candle {bar_timestamp}"
                        ),
                        details={
                            "symbol": intent.symbol,
                            "strategy": intent.strategy_id,
                            "bar_timestamp": bar_timestamp,
                            "reason": "double_latch_db_check",
                        },
                    )
                    return None
                
                logger.debug(
                    f"Double-latch check: DB gate passed for candle={bar_timestamp}, "
                    f"strategy={intent.strategy_id}, symbol={intent.symbol}"
                )
            finally:
                session.close()
        except Exception as e:
            logger.warning(
                f"Double-latch DB check failed (fail-open): {e}. Proceeding with execution."
            )
            # Fail-open: If DB check fails, proceed with execution (don't block on DB issues)
    
    # Serialize order execution (async lock — safe across awaits)
    async with _get_execution_lock():
        logger.debug("Acquired execution lock for live trade")

        try:
            # Convert to Kraken CLI pair format (BTC/USD → BTCUSD)
            kraken_pair = kraken_cli.symbol_to_cli_pair(intent.symbol)

            # Validate costmin — use Redis cache only ($0.50 default if not cached)
            try:
                from backend.redis import get_redis_client as _get_redis
                from backend.redis.keys import ASSET_PAIRS_CACHE_KEY
                _rc = _get_redis()
                _pair_cache = _rc.hgetall(ASSET_PAIRS_CACHE_KEY.format(pair=intent.symbol))
                costmin = float(_pair_cache.get(b"costmin", _pair_cache.get("costmin", 0.50))) if _pair_cache else 0.50
                
                # Validate position size meets costmin requirement
                if sizing.position_size_usd < costmin:
                    error_msg = f"below_costmin: ${sizing.position_size_usd:.2f} < ${costmin:.2f}"
                    logger.warning(f"Order rejected: {error_msg} (pair: {intent.symbol})")
                    log_activity(
                        activity_type="warning",
                        message=f"Order rejected for {intent.symbol}: {error_msg}",
                        details={
                            "symbol": intent.symbol,
                            "side": intent.side,
                            "position_size_usd": sizing.position_size_usd,
                            "costmin": costmin,
                            "reason": "below_costmin",
                            "strategy": intent.strategy_id,
                        },
                    )
                    return None
                
                logger.info(
                    f"Order validated: ${sizing.position_size_usd:.2f} >= ${costmin:.2f} "
                    f"(pair: {intent.symbol})"
                )
            except Exception as e:
                logger.warning(f"Failed to validate costmin: {e}, proceeding with order execution")
                # Don't block execution if costmin check fails - use default behavior
            
            # Use limit orders for BUY entries to qualify for maker fee (0.16% vs taker 0.26%).
            # Forced exits (stop-loss, invalidation, max_hold) stay as market orders for speed.
            is_forced_exit = (intent.metadata or {}).get("forced_exit", False)
            if intent.side == "buy" and not is_forced_exit:
                order_type = "limit"
                # Set limit at current price — fills immediately at maker rate if resting,
                # or as taker if the book crosses. Either way cheaper than pure market.
                limit_price = str(round(current_price, 8))
            else:
                order_type = "market"
                limit_price = None

            # Execute order via Kraken CLI
            try:
                order_result = await kraken_cli.place_order(
                    pair=kraken_pair,
                    side=intent.side,
                    quantity=float(sizing.quantity),
                    order_type=order_type,
                    price=float(limit_price) if limit_price is not None else None,
                )
            except kraken_cli.KrakenCLIError as e:
                error_str = str(e)
                from backend.execution.order_manager import classify_kraken_error
                error_type = classify_kraken_error(error_str)
                logger.error(f"Order failed: {intent.symbol} - {error_type}: {error_str}")
                log_activity(
                    activity_type="error",
                    message=f"Order failed: {intent.symbol} - {error_type}: {error_str}",
                    details={
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "strategy": intent.strategy_id,
                        "error_type": error_type,
                        "error_message": error_str,
                        "pair": kraken_pair,
                        "order_type": order_type,
                    },
                )
                raise

            # Generate internal order_id
            order_id = str(uuid.uuid4())
            txid_raw = order_result.get("txid", "")
            exchange_order_id = txid_raw[0] if isinstance(txid_raw, list) else str(txid_raw)

            # Query order status for execution details
            try:
                order_status_raw = await kraken_cli.query_order(exchange_order_id)
                order_info = order_status_raw.get(exchange_order_id, {})
                executed_price = float(order_info.get("price", current_price))
                fees = float(order_info.get("fee", 0.0))
            except Exception as e:
                logger.warning(f"Failed to query order status: {e}. Using market price.")
                executed_price = current_price
                fees = 0.0
            
            # Calculate slippage
            slippage = calculate_slippage(current_price, executed_price, intent.side)
            
            # TICKET-607: Check slippage threshold (>0.2% warning)
            if current_price > 0:
                slippage_pct = abs(executed_price - current_price) / current_price * 100.0
                if slippage_pct > 0.2:
                    logger.warning(
                        f"HIGH_SLIPPAGE_WARNING: signal_price=${current_price:.2f}, "
                        f"fill_price=${executed_price:.2f}, slippage={slippage_pct:.2f}%"
                    )
                    log_activity(
                        activity_type="HIGH_SLIPPAGE_WARNING",
                        message=(
                            f"High slippage detected: {intent.symbol} - "
                            f"signal=${current_price:.2f}, fill=${executed_price:.2f}, "
                            f"slippage={slippage_pct:.2f}%"
                        ),
                        details={
                            "symbol": intent.symbol,
                            "side": intent.side,
                            "signal_price": current_price,
                            "fill_price": executed_price,
                            "slippage_usd": abs(executed_price - current_price),
                            "slippage_pct": slippage_pct,
                            "strategy": intent.strategy_id,
                        },
                    )
            
            # 7. Create Fill
            fill = Fill(
                order_id=order_id,
                symbol=intent.symbol,
                side=intent.side,
                executed_price=executed_price,
                quantity=sizing.quantity,
                fees=fees,
                slippage=slippage,
                exchange_order_id=exchange_order_id,
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            
            logger.info(
                f"Order executed: order_id={order_id}, "
                f"exchange_order_id={exchange_order_id}, "
                f"qty={sizing.quantity}, price=${executed_price:.2f}, "
                f"risk=${sizing.max_risk_usd:.2f}"
            )
            
            # Log TRADE_PLACED to activity feed (always logged, no debouncing)
            log_activity(
                activity_type="TRADE_PLACED",
                message=f"Trade placed: {intent.side.upper()} {sizing.quantity} {intent.symbol} @ ${executed_price:.2f}",
                details={
                    "order_id": order_id,
                    "exchange_order_id": exchange_order_id,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": sizing.quantity,
                    "price": executed_price,
                    "fees": fees,
                    "strategy": intent.strategy_id,
                },
            )
            
            # Record fill to position tracker
            # Pass strategy_id for buy orders (opening/adding to positions)
            tracker = get_position_tracker()
            strategy_id = intent.strategy_id if intent.side == "buy" else None
            tracker.record_fill(
                fill,
                strategy_id=strategy_id,
                execution_live=live,
                strategy_canonical=strategy_canonical if intent.side == "buy" else None,
            )
            
            # Set scout_entry_price for Scout entries (equity < $50)
            if intent.side == "buy" and use_scout_sizing:
                position = tracker.get_position(intent.symbol)
                if position:
                    position.scout_entry_price = executed_price
                    # Re-save position with scout_entry_price
                    from backend.redis import get_redis_client
                    from backend.redis.keys import POSITION_KEY
                    redis_client = get_redis_client()
                    key = POSITION_KEY.format(symbol=intent.symbol)
                    redis_client.hset(key, mapping=position.to_dict())
                    
                    logger.info(
                        f"Scout entry: ${sizing.position_size_usd:.2f} @ ${executed_price:.2f}, "
                        f"stop: ${sizing.stop_loss_price:.2f} ({sizing.stop_loss_pct:.1f}%)"
                    )
            
            # Update performance metrics (for buy orders - new positions)
            if strategy_id and intent.side == "buy":
                try:
                    from backend.performance.monitor import get_performance_monitor
                    perf_monitor = get_performance_monitor()
                    # Initial P&L is 0 (just opened)
                    perf_monitor.update_trade_outcome(
                        strategy_id=strategy_id,
                        symbol=intent.symbol,
                        pnl=0.0,  # Will be updated when position closes or P&L updates
                        entry_time=datetime.now(timezone.utc),
                    )
                except Exception as e:
                    logger.debug(f"Failed to update performance metrics: {e}")
            
            # 8. Place stop-loss order for BUY orders (skip for Soldier scale-in, handled separately)
            if intent.side == "buy" and not soldier_scale_in:
                try:
                    # Get stop-loss price from metadata if available, otherwise calculate
                    stop_loss_price = metadata.get("stop_loss_price") or (executed_price * (1 - stop_loss_pct / 100.0))
                    
                    # Round stop-loss price to 3 decimal places (Kraken requirement)
                    # Different pairs may have different precision, but 3 decimals is safe for most USD pairs
                    stop_loss_price_rounded = round(stop_loss_price, 3)
                    
                    logger.info(
                        f"Placing stop-loss order for {intent.symbol}: "
                        f"trigger=${stop_loss_price_rounded:.3f} ({stop_loss_pct}% below entry ${executed_price:.4f}, "
                        f"rounded from ${stop_loss_price:.4f})"
                    )
                    
                    # Place stop-loss sell order via Kraken CLI
                    stop_loss_result = await kraken_cli.place_order(
                        pair=kraken_pair,
                        side="sell",
                        quantity=float(sizing.quantity),
                        order_type="stop-loss",
                        price=stop_loss_price_rounded,
                    )
                    txid_raw = stop_loss_result.get("txid", "")
                    stop_loss_txid = txid_raw[0] if isinstance(txid_raw, list) else str(txid_raw)
                    logger.info(f"Stop-loss order placed: txid={stop_loss_txid}")
                    
                    # Update position with stop-loss info
                    position = tracker.get_position(intent.symbol)
                    if position:
                        position.stop_loss_order_id = stop_loss_txid
                        position.stop_loss_price = stop_loss_price_rounded  # Store rounded price
                        # Re-save position with stop-loss info
                        from backend.redis import get_redis_client
                        from backend.redis.keys import POSITION_KEY
                        redis_client = get_redis_client()
                        key = POSITION_KEY.format(symbol=intent.symbol)
                        redis_client.hset(key, mapping=position.to_dict())
                        
                        # Store TP1 price in Redis if available in metadata
                        tp1_price = metadata.get("tp1_price")
                        if tp1_price:
                            from backend.redis.keys import POSITION_TP1_PRICE_KEY
                            tp1_key = POSITION_TP1_PRICE_KEY.format(symbol=intent.symbol)
                            redis_client.set(tp1_key, str(tp1_price))
                            logger.debug(f"Stored TP1 price for {intent.symbol}: ${tp1_price:.2f}")
                    
                    # Log STOP_PLACED to activity feed
                    log_activity(
                        activity_type="STOP_PLACED",
                        message=f"Stop-loss placed: SELL {sizing.quantity} {intent.symbol} @ ${stop_loss_price_rounded:.3f}",
                        details={
                            "symbol": intent.symbol,
                            "stop_loss_txid": stop_loss_txid,
                            "trigger_price": stop_loss_price_rounded,
                            "entry_price": executed_price,
                            "stop_loss_pct": stop_loss_pct,
                        },
                    )
                except Exception as e:
                    logger.error(f"Failed to place stop-loss order: {e}")
                    log_activity(
                        activity_type="error",
                        message=f"Stop-loss order failed for {intent.symbol}",
                        details={
                            "symbol": intent.symbol,
                            "error": str(e),
                        },
                    )
            
            return fill
            
        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            # Log error to activity feed
            log_activity(
                activity_type="error",
                message=f"Order execution failed for {intent.symbol}",
                details={
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "strategy": intent.strategy_id,
                    "error": str(e),
                },
            )
            raise
        finally:
            logger.debug("Released execution lock")


def get_trade_intent_from_signal(intent_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve TradeIntent data from Signal table using intent_id.
    
    Args:
        intent_id: Intent ID from RiskDecision (should map to Signal.id)
        
    Returns:
        Dictionary with TradeIntent fields, or None if not found
    """
    session = get_session()
    try:
        # Try to find signal by ID (intent_id should be Signal.id as string)
        try:
            signal_id = uuid.UUID(intent_id)
        except ValueError:
            logger.warning(f"Invalid intent_id format (not UUID): {intent_id}")
            return None
        
        signal = session.query(Signal).filter(Signal.id == signal_id).first()
        
        if signal is None:
            logger.warning(f"Signal not found for intent_id: {intent_id}")
            return None
        
        # Convert Signal to TradeIntent-like dictionary
        return {
            "strategy_id": str(signal.strategy_id),
            "symbol": signal.symbol,
            "side": signal.side,
            "intent_type": signal.intent_type,
            "notional_risk_pct": float(signal.notional_risk_pct),
            "metadata": signal.signal_metadata or {},
        }
    except Exception as e:
        logger.error(f"Failed to retrieve TradeIntent from Signal: {e}")
        return None
    finally:
        session.close()


async def execute_approved_intent(risk_decision: RiskDecision) -> Fill:
    """
    Execute an approved TradeIntent and return a Fill object (CLI-backed).

    Args:
        risk_decision: RiskDecision with approved=True

    Returns:
        Fill object with execution details
    """
    if not risk_decision.approved:
        raise ValueError(
            f"Cannot execute rejected intent. "
            f"intent_id={risk_decision.intent_id}, "
            f"rejection_reason={risk_decision.rejection_reason}"
        )

    logger.info(f"Executing approved intent: intent_id={risk_decision.intent_id}")

    trade_intent_data = get_trade_intent_from_signal(risk_decision.intent_id)
    if trade_intent_data is None:
        raise RuntimeError(
            f"Failed to retrieve TradeIntent for intent_id: {risk_decision.intent_id}"
        )

    async with _get_execution_lock():
        logger.debug("Acquired execution lock")
        try:
            current_price = trade_intent_data.get("metadata", {}).get("current_price") or 0.0
            total_equity = float(get_current_equity())
            risk_amount = (trade_intent_data["notional_risk_pct"] / 100.0) * total_equity
            if current_price <= 0:
                raise ValueError(f"Cannot calculate volume: current_price={current_price}")
            quantity = risk_amount / current_price

            kraken_pair = kraken_cli.symbol_to_cli_pair(trade_intent_data["symbol"])

            order_result = await kraken_cli.place_order(
                pair=kraken_pair,
                side=trade_intent_data["side"],
                quantity=quantity,
                order_type="market",
            )

            order_id = str(uuid.uuid4())
            txid_raw = order_result.get("txid", "")
            exchange_order_id = txid_raw[0] if isinstance(txid_raw, list) else str(txid_raw)

            try:
                order_status_raw = await kraken_cli.query_order(exchange_order_id)
                order_info = order_status_raw.get(exchange_order_id, {})
                executed_price = float(order_info.get("price", current_price))
                fees = float(order_info.get("fee", 0.0))
            except Exception as e:
                logger.warning(f"Failed to query order status: {e}. Using market price.")
                executed_price = current_price
                fees = 0.0

            slippage = calculate_slippage(current_price, executed_price, trade_intent_data["side"])

            fill = Fill(
                order_id=order_id,
                symbol=trade_intent_data["symbol"],
                side=trade_intent_data["side"],
                executed_price=executed_price,
                quantity=quantity,
                fees=fees,
                slippage=slippage,
                exchange_order_id=exchange_order_id,
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

            logger.info(
                f"Order executed: order_id={order_id}, exchange_order_id={exchange_order_id}, "
                f"qty={quantity}, price=${executed_price:.2f}"
            )
            log_activity(
                activity_type="order",
                message=f"Order filled: {trade_intent_data['side'].upper()} {quantity} "
                        f"{trade_intent_data['symbol']} @ ${executed_price:.2f}",
                details={
                    "order_id": order_id,
                    "exchange_order_id": exchange_order_id,
                    "symbol": trade_intent_data["symbol"],
                    "side": trade_intent_data["side"],
                    "quantity": quantity,
                    "price": executed_price,
                    "fees": fees,
                    "strategy": trade_intent_data["strategy_id"],
                },
            )

            tracker = get_position_tracker()
            strategy_id = (
                trade_intent_data["strategy_id"] if trade_intent_data["side"] == "buy" else None
            )
            _meta = trade_intent_data.get("metadata") or {}
            _sc = _meta.get("strategy_canonical")
            from backend.api.routes.trading import get_bot_mode as _gbm

            _legacy_live = _gbm() == "LIVE"
            tracker.record_fill(
                fill,
                strategy_id=strategy_id,
                execution_live=_legacy_live,
                strategy_canonical=_sc if trade_intent_data["side"] == "buy" else None,
            )
            return fill

        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            log_activity(
                activity_type="error",
                message=f"Order execution failed for {trade_intent_data['symbol']}",
                details={
                    "symbol": trade_intent_data["symbol"],
                    "side": trade_intent_data["side"],
                    "strategy": trade_intent_data["strategy_id"],
                    "error": str(e),
                },
            )
            raise
        finally:
            logger.debug("Released execution lock")
