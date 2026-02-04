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

import logging
import os
import threading
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
from backend.execution.nonce import get_next_nonce
from backend.execution.order_manager import (
    convert_intent_to_order_params,
    execute_order,
    calculate_slippage,
    _convert_symbol_to_kraken_pair,
)
from backend.execution.kraken_interface import KrakenClientInterface, KrakenClientStub, KrakenOrderResponse
from backend.execution.kraken_rest import KrakenClient as RealKrakenClient
from backend.db import get_session
from backend.db.models import Signal
from backend.positions.tracker import get_position_tracker
from backend.api.routes.events import log_activity

logger = logging.getLogger(__name__)

# Global lock for order serialization (only one order at a time)
_execution_lock = threading.Lock()

# Global Kraken client instance (will be set when Ticket 11 is implemented)
_kraken_client: Optional[KrakenClientInterface] = None


class KrakenClientAdapter(KrakenClientInterface):
    """
    Adapter that wraps RealKrakenClient to implement KrakenClientInterface.
    
    Translates parameter names and return types between the interface
    (used by order_manager) and the actual Kraken REST client.
    """
    
    def __init__(self):
        self._client = RealKrakenClient()
    
    def add_order(
        self,
        pair: str,
        type: str,
        ordertype: str,
        volume: float,
        **kwargs
    ) -> KrakenOrderResponse:
        """
        Place an order on Kraken.
        
        Translates interface parameters to KrakenClient parameters:
        - pair -> symbol (with format conversion)
        - type -> side
        - ordertype -> order_type
        """
        # Convert Kraken pair format back to standard format
        # XBTUSD -> BTC/USD, ETHUSD -> ETH/USD
        symbol = self._convert_pair_to_symbol(pair)
        
        # Call the real client with translated params
        result = self._client.add_order(
            symbol=symbol,
            side=type,  # "buy" or "sell"
            order_type=ordertype,  # "market", "limit"
            volume=float(volume),
            **kwargs
        )
        
        # Convert dict response to KrakenOrderResponse
        error = result.get("error", [])
        txid_list = result.get("result", {}).get("txid", [])
        txid = txid_list[0] if txid_list else ""
        descr = result.get("result", {}).get("descr", {})
        
        return KrakenOrderResponse(
            txid=txid,
            descr=descr,
            error=error if error else None,
        )
    
    def cancel_order(self, txid: str) -> Dict[str, Any]:
        """Cancel an order on Kraken."""
        return self._client.cancel_order(txid)
    
    def query_orders(self, txid: Optional[str] = None) -> Dict[str, Any]:
        """Query order status from Kraken."""
        result = self._client.query_orders(txid=txid)
        return result.get("result", {})
    
    def _convert_pair_to_symbol(self, pair: str) -> str:
        """Convert Kraken pair format to standard symbol format."""
        # XBTUSD -> BTC/USD
        if pair.startswith("XBT"):
            pair = pair.replace("XBT", "BTC", 1)
        
        # Insert slash: ETHUSD -> ETH/USD
        if len(pair) >= 6 and "/" not in pair:
            base = pair[:-3]
            quote = pair[-3:]
            return f"{base}/{quote}"
        
        return pair


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


def set_kraken_client(client: KrakenClientInterface) -> None:
    """
    Set the Kraken client instance.
    
    This should be called by Ticket 11 implementation to register the client.
    
    Args:
        client: Kraken REST API client instance
    """
    global _kraken_client
    _kraken_client = client
    logger.info("Kraken client registered")


def get_kraken_client() -> KrakenClientInterface:
    """
    Get the Kraken client instance.
    
    Returns:
        Kraken client instance (adapter wrapping the real KrakenClient)
    """
    global _kraken_client
    if _kraken_client is None:
        # Auto-initialize with adapter wrapping the real Kraken client
        _kraken_client = KrakenClientAdapter()
        logger.info("Kraken client initialized with KrakenClientAdapter")
    return _kraken_client


async def execute_trade(
    intent: "TradeIntent",
    current_price: float,
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
        
    Returns:
        Fill object if trade executed, None if rejected
    """
    # Get risk components
    two_percent_rule = _get_two_percent_rule()
    account_tracker = _get_account_tracker()
    position_sizer = _get_position_sizer()
    
    # 1. Get account equity
    equity = account_tracker.current_equity
    risk_pct = float(os.getenv("RISK_PCT_PER_TRADE", "2.0"))
    stop_loss_pct = float(os.getenv("STOP_LOSS_PCT", "5.0"))
    
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
                client = get_kraken_client()
                client.cancel_order(position.stop_loss_order_id)
                logger.info(f"Stop-loss order cancelled: {position.stop_loss_order_id}")
            except Exception as e:
                logger.warning(f"Failed to cancel stop-loss order: {e} (may already be filled/cancelled)")
        
        # Use actual held quantity for sell
        sell_quantity = position.quantity
        position_value_usd = sell_quantity * current_price
        
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
            sizing = position_sizer.calculate(
                account_equity=equity,
                risk_pct=risk_pct,
                entry_price=current_price,
                stop_loss_pct=stop_loss_pct,
                strategy_id=intent.strategy_id,  # Pass strategy_id for adaptive sizing
                atr=atr_value,  # Pass ATR for micro mode stop distance check
                stop_loss_price=explicit_stop_loss_price,  # Use explicit stop if provided
                use_scout_sizing=use_scout_sizing,  # Use Scout sizing if equity < $50
            )
        
        # Micro mode may return None to skip trade
        if sizing is None:
            logger.warning(
                f"Trade skipped (micro mode): equity=${equity:.2f} < $250 threshold. "
                f"Check stop distance or notional requirements."
            )
            log_activity(
                activity_type="warning",
                message=f"BUY {intent.symbol} skipped - Micro mode active (equity=${equity:.2f})",
                details={
                    "symbol": intent.symbol,
                    "strategy": intent.strategy_id,
                    "reason": "micro_mode_skip",
                    "equity": equity,
                },
            )
            return None
        
        # 2.5 Check available USD balance - can't buy more than we have
        try:
            from backend.execution.kraken_rest import KrakenClient
            kraken = KrakenClient()
            balance = kraken.get_balance()
            # Get USD balance (Kraken uses ZUSD)
            available_usd = float(balance.get("ZUSD", balance.get("USD", 0)))
            
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
    daily_loss_limit = float(os.getenv("DAILY_LOSS_LIMIT", "10.0"))
    if account_tracker.daily_pnl <= -daily_loss_limit:
        logger.error(
            f"Daily loss limit reached: ${account_tracker.daily_pnl:.2f} "
            f"exceeds -${daily_loss_limit:.2f} limit. Halting."
        )
        return None
    
    # Check for shadow-live mode (simulate execution without placing orders)
    # Read from Redis (set via frontend toggle) with env var fallback
    try:
        from backend.redis import get_redis_client
        from backend.redis.keys import SHADOW_LIVE_MODE_KEY
        redis_client = get_redis_client()
        shadow_value = redis_client.get(SHADOW_LIVE_MODE_KEY)
        if shadow_value is not None:
            shadow_live_mode = shadow_value.lower() == "true"
        else:
            # Fallback to env var if Redis key not set
            shadow_live_mode = os.getenv("SHADOW_LIVE_MODE", "false").lower() == "true"
    except Exception as e:
        logger.warning(f"Failed to read shadow-live mode from Redis: {e}. Falling back to env var.")
        shadow_live_mode = os.getenv("SHADOW_LIVE_MODE", "false").lower() == "true"
    
    if shadow_live_mode:
        # TICKET-612: Check shadow balance before execution in shadow mode
        if intent.side == "buy":
            try:
                from backend.redis import get_redis_client
                from backend.redis.keys import SHADOW_BALANCE_KEY
                import json
                redis_client = get_redis_client()
                shadow_balance_json = redis_client.get(SHADOW_BALANCE_KEY)
                
                if shadow_balance_json:
                    shadow_balance = json.loads(shadow_balance_json)
                    available_usd = shadow_balance.get("available_usd", 0.0)
                    position_cost = sizing.position_size_usd + (sizing.position_size_usd * 0.0026)  # Add estimated fees
                    
                    if available_usd < position_cost:
                        logger.warning(
                            f"Shadow balance insufficient: available=${available_usd:.2f}, "
                            f"required=${position_cost:.2f}. Rejecting trade."
                        )
                        log_activity(
                            activity_type="warning",
                            message=f"Trade rejected: Insufficient shadow balance for {intent.symbol}",
                            details={
                                "symbol": intent.symbol,
                                "available_usd": available_usd,
                                "required_usd": position_cost,
                                "reason": "insufficient_shadow_balance",
                            },
                        )
                        return None
            except Exception as e:
                logger.warning(f"Failed to check shadow balance: {e}, proceeding")
        
        # Shadow-live mode: Log order intents without executing
        logger.info(
            f"[SHADOW-LIVE] ORDER_INTENT: {intent.side.upper()} {sizing.quantity} {intent.symbol} "
            f"@ ${current_price:.2f} (risk: ${sizing.max_risk_usd:.2f}, equity: ${equity:.2f})"
        )
        
        # Get metadata (must be before using it)
        metadata = intent.metadata or {}
        
        # Get bar timestamp and timeframe from metadata for candle tagging
        bar_timestamp = metadata.get("bar_timestamp") or metadata.get("timestamp")
        strategy_interval = metadata.get("timeframe") or metadata.get("interval") or "15m"
        
        # Log ORDER_INTENT to activity feed with full execution details
        # Include candle boundary tagging for shadow mode
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
                "equity": equity,
                "mode": "shadow_live",
                "bar_timestamp": bar_timestamp,
                "timeframe": strategy_interval,
                "tp1_price": metadata.get("tp1_price"),
                "tp2_price": metadata.get("tp2_price"),
                "tp1_R": metadata.get("tp1_R"),
                "tp2_R": metadata.get("tp2_R"),
            },
        )
        
        # Log STOP_INTENT if stop-loss is configured
        stop_loss_price = metadata.get("stop_loss_price") or sizing.stop_loss_price
        if stop_loss_price:
            log_activity(
                activity_type="STOP_INTENT",
                message=f"Stop-loss intent: SELL {sizing.quantity} {intent.symbol} @ ${stop_loss_price:.2f}",
                details={
                    "symbol": intent.symbol,
                    "stop_loss_price": stop_loss_price,
                    "stop_loss_pct": sizing.stop_loss_pct,
                    "entry_price": current_price,
                    "risk_usd": sizing.max_risk_usd,
                    "strategy": intent.strategy_id,
                    "mode": "shadow_live",
                },
            )
        
        # Log TAKE_PROFIT_INTENT if configured
        tp1_price = metadata.get("tp1_price")
        tp2_price = metadata.get("tp2_price")
        if tp1_price:
            log_activity(
                activity_type="TAKE_PROFIT_INTENT",
                message=f"Take-profit intent: TP1 @ ${tp1_price:.2f}, TP2 @ ${tp2_price:.2f if tp2_price else 'N/A'}",
                details={
                    "symbol": intent.symbol,
                    "tp1_price": tp1_price,
                    "tp2_price": tp2_price,
                    "tp1_R": metadata.get("tp1_R"),
                    "tp2_R": metadata.get("tp2_R"),
                    "entry_price": current_price,
                    "strategy": intent.strategy_id,
                    "mode": "shadow_live",
                },
            )
        
        # In shadow mode, create simulated position even though no real execution happens
        # This ensures shadow positions are created on ORDER_INTENT (as required)
        try:
            from backend.execution.models import Fill
            from datetime import datetime, timezone
            
            # Create a simulated Fill for shadow position tracking
            simulated_fill = Fill(
                order_id=f"shadow_{intent.symbol}_{intent.side}_{datetime.now(timezone.utc).timestamp()}",
                symbol=intent.symbol,
                side=intent.side,
                executed_price=current_price,
                quantity=sizing.quantity,
                fees=0.0,  # No fees in shadow mode
                slippage=0.0,  # No slippage in shadow mode
                exchange_order_id=None,  # No exchange order in shadow mode
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            
            # Record fill to position tracker (creates shadow position)
            tracker = get_position_tracker()
            strategy_id = intent.strategy_id if intent.side == "buy" else None
            tracker.record_fill(simulated_fill, strategy_id=strategy_id)
            
            # Store TP1 price in Redis if available in metadata (for shadow mode)
            if intent.side == "buy":
                metadata = intent.metadata or {}
                tp1_price = metadata.get("tp1_price")
                if tp1_price:
                    from backend.redis import get_redis_client
                    from backend.redis.keys import POSITION_TP1_PRICE_KEY
                    redis_client = get_redis_client()
                    tp1_key = POSITION_TP1_PRICE_KEY.format(symbol=intent.symbol)
                    redis_client.set(tp1_key, str(tp1_price))
                    logger.debug(f"[SHADOW-LIVE] Stored TP1 price for {intent.symbol}: ${tp1_price:.2f}")
            
            logger.info(
                f"[SHADOW-LIVE] Simulated position created: {intent.side} {sizing.quantity} {intent.symbol} "
                f"@ ${current_price:.2f}"
            )
            
            # Return the simulated fill (for consistency with live mode)
            return simulated_fill
        except Exception as e:
            logger.error(f"[SHADOW-LIVE] Failed to create simulated position: {e}", exc_info=True)
            # Still return None on error (don't break execution flow)
            return None
    
    # 6. Execute on Kraken (live mode)
    logger.info(
        f"Placing order: {intent.side} {sizing.quantity} {intent.symbol} "
        f"@ ${current_price:.2f} (risk: ${sizing.max_risk_usd:.2f})"
    )
    
    # TICKET-610: Live Execution Preview (only in live mode, not shadow mode)
    # Check if we're in live trading mode (not shadow mode)
    try:
        from backend.redis import get_redis_client
        from backend.redis.keys import SHADOW_LIVE_MODE_KEY
        redis_client = get_redis_client()
        shadow_value = redis_client.get(SHADOW_LIVE_MODE_KEY)
        is_shadow_mode = shadow_value is not None and shadow_value.lower() == "true"
        live_trading = os.getenv("LIVE_TRADING", "false").lower() == "true"
        
        if live_trading and not is_shadow_mode:
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
    
    # Serialize order execution
    with _execution_lock:
        logger.debug("Acquired execution lock for 2% rule trade")
        
        try:
            # Get next nonce
            nonce = get_next_nonce()
            logger.debug(f"Generated nonce: {nonce}")
            
            # Convert to Kraken order params
            kraken_pair = _convert_symbol_to_kraken_pair(intent.symbol)
            
            # Validate costmin before executing order
            try:
                from backend.execution.kraken_rest import KrakenClient
                kraken_client = KrakenClient()
                costmin = kraken_client.get_costmin(intent.symbol)
                
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
            
            order_params = {
                "pair": kraken_pair,
                "type": intent.side,
                "ordertype": "market",
                "volume": str(sizing.quantity),
            }
            
            # Execute order
            client = get_kraken_client()
            try:
                order_response = execute_order(client, order_params)
            except Exception as e:
                # TICKET-605: Classify and log order failure
                error_str = str(e)
                error_parts = error_str.split(":", 1)
                if len(error_parts) == 2:
                    error_type = error_parts[0].strip()
                    error_message = error_parts[1].strip()
                else:
                    from backend.execution.order_manager import classify_kraken_error
                    error_type = classify_kraken_error(error_str)
                    error_message = error_str
                
                logger.error(f"Order failed: {intent.symbol} - {error_type}: {error_message}")
                log_activity(
                    activity_type="error",
                    message=f"Order failed: {intent.symbol} - {error_type}: {error_message}",
                    details={
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "strategy": intent.strategy_id,
                        "error_type": error_type,
                        "error_message": error_message,
                        "order_params": order_params,
                    },
                )
                raise
            
            # Generate internal order_id
            order_id = str(uuid.uuid4())
            exchange_order_id = order_response.txid
            
            # Query order status for execution details
            try:
                order_status = client.query_orders(txid=exchange_order_id)
                executed_price = order_status.get("price", current_price)
                fees = order_status.get("fee", 0.0)
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
            tracker.record_fill(fill, strategy_id=strategy_id)
            
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
                    from datetime import datetime
                    perf_monitor = get_performance_monitor()
                    # Initial P&L is 0 (just opened)
                    perf_monitor.update_trade_outcome(
                        strategy_id=strategy_id,
                        symbol=intent.symbol,
                        pnl=0.0,  # Will be updated when position closes or P&L updates
                        entry_time=datetime.now(),
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
                    
                    # Place stop-loss sell order on Kraken
                    stop_loss_response = client.add_order(
                        pair=kraken_pair,
                        type="sell",
                        ordertype="stop-loss",
                        volume=float(sizing.quantity),
                        price=stop_loss_price_rounded,  # Trigger price for stop-loss (rounded to 3 decimals)
                    )
                    
                    stop_loss_txid = stop_loss_response.txid
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


def execute_approved_intent(risk_decision: RiskDecision) -> Fill:
    """
    Execute an approved TradeIntent and return a Fill object.
    
    This function:
    1. Validates that the RiskDecision is approved
    2. Retrieves TradeIntent from Signal table using intent_id
    3. Converts TradeIntent to Kraken order parameters
    4. Executes order with serialized nonce handling (prevents collisions)
    5. Creates and returns Fill object matching contract schema
    
    Args:
        risk_decision: RiskDecision with approved=True
        
    Returns:
        Fill object with execution details
        
    Raises:
        ValueError: If RiskDecision is not approved
        RuntimeError: If TradeIntent cannot be retrieved
        Exception: If order execution fails
        
    Notes:
        - Order execution is serialized (only one order at a time)
        - Nonce is generated atomically using Redis
        - Handles partial fills and order rejections gracefully
    """
    # Validate that decision is approved
    if not risk_decision.approved:
        raise ValueError(
            f"Cannot execute rejected intent. "
            f"intent_id={risk_decision.intent_id}, "
            f"rejection_reason={risk_decision.rejection_reason}"
        )
    
    logger.info(f"Executing approved intent: intent_id={risk_decision.intent_id}")
    
    # Retrieve TradeIntent from Signal table
    trade_intent_data = get_trade_intent_from_signal(risk_decision.intent_id)
    if trade_intent_data is None:
        raise RuntimeError(
            f"Failed to retrieve TradeIntent for intent_id: {risk_decision.intent_id}"
        )
    
    # Serialize order execution (only one order at a time)
    with _execution_lock:
        logger.debug("Acquired execution lock")
        
        try:
            # Get next nonce (atomic operation)
            nonce = get_next_nonce()
            logger.debug(f"Generated nonce for order: {nonce}")
            
            # Get total equity for volume calculation
            total_equity_decimal = get_current_equity()
            total_equity = float(total_equity_decimal)
            
            # Get current price from metadata or market data
            # For market orders, we don't need it upfront, but it's useful for volume calculation
            current_price = trade_intent_data.get("metadata", {}).get("current_price")
            
            # Convert TradeIntent to Kraken order parameters
            order_params = convert_intent_to_order_params(
                symbol=trade_intent_data["symbol"],
                side=trade_intent_data["side"],
                intent_type=trade_intent_data["intent_type"],
                notional_risk_pct=trade_intent_data["notional_risk_pct"],
                current_price=current_price,
                total_equity=total_equity,
            )
            
            # Validate that volume was calculated
            if "volume" not in order_params or float(order_params["volume"]) <= 0:
                raise ValueError(
                    f"Cannot calculate order volume. "
                    f"Required: current_price and total_equity. "
                    f"Got: current_price={current_price}, total_equity={total_equity}"
                )
            
            # Execute order
            client = get_kraken_client()
            order_response = execute_order(client, order_params)
            
            # Generate internal order_id
            order_id = str(uuid.uuid4())
            
            # Extract exchange order ID
            exchange_order_id = order_response.txid
            
            # Query order status to get execution details
            # Note: This is a placeholder - actual implementation will parse Kraken response
            # For market orders, we need to query the order status to get executed price
            try:
                order_status = client.query_orders(txid=exchange_order_id)
                # Parse order status to extract executed_price, quantity, fees
                # This is a placeholder - actual parsing depends on Kraken API response format
                executed_price = order_status.get("price", 0.0)  # Placeholder
                quantity = float(order_params.get("volume", 0))  # Use requested volume for now
                fees = order_status.get("fee", 0.0)  # Placeholder
                
                # If order status doesn't have execution details, we'll need to wait or poll
                if executed_price == 0.0:
                    logger.warning(
                        f"Order {exchange_order_id} executed but execution details not yet available. "
                        f"Using placeholder values. This should be replaced with actual order status parsing."
                    )
                    # For now, use a reasonable default (this should be replaced)
                    executed_price = current_price if current_price else 0.0
            except Exception as e:
                logger.warning(
                    f"Failed to query order status for {exchange_order_id}: {e}. "
                    f"Using placeholder values."
                )
                # Fallback to placeholder values
                executed_price = current_price if current_price else 0.0
                quantity = float(order_params.get("volume", 0))
                fees = 0.0
            
            # Calculate slippage (only if we have both intended and executed prices)
            intended_price = current_price
            slippage = calculate_slippage(intended_price, executed_price, trade_intent_data["side"])
            
            # Create Fill object
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
                f"Order executed successfully: order_id={order_id}, "
                f"exchange_order_id={exchange_order_id}, "
                f"quantity={quantity}, price={executed_price}"
            )
            
            # Log to activity feed
            log_activity(
                activity_type="order",
                message=f"Order filled: {trade_intent_data['side'].upper()} {quantity} {trade_intent_data['symbol']} @ ${executed_price:.2f}",
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
            
            # Record fill to position tracker
            # Pass strategy_id for buy orders (opening/adding to positions)
            tracker = get_position_tracker()
            strategy_id = (
                trade_intent_data["strategy_id"]
                if trade_intent_data["side"] == "buy"
                else None
            )
            tracker.record_fill(fill, strategy_id=strategy_id)
            
            return fill
            
        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            # Log error to activity feed
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
