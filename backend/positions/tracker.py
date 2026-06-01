"""Position tracking service."""

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

# Lazy import KrakenClient to avoid circular dependency (imported inside sync_from_kraken method)
# from backend.execution.kraken_rest import KrakenClient
from backend.positions.models import Position
from backend.redis import get_redis_client
from backend.redis.keys import (
    POSITION_KEY,
    POSITION_STATUS_KEY,
    POSITION_COOLDOWN_KEY,
    POSITION_PENDING_ORDER_KEY,
    POSITION_EXIT_REASON_KEY,
    POSITION_EXIT_ATTEMPT_KEY,
    POSITION_EXIT_FAIL_COUNT_KEY,
    POSITION_TP1_PRICE_KEY,
    POSITION_TP1_HIT_KEY,
)
from backend.positions.quantity import floor_qty_8dp, is_valid_position_quantity

logger = logging.getLogger(__name__)

# Kraken currency code mapping to standard symbols
# Kraken prefixes with X for crypto, Z for fiat
KRAKEN_CURRENCY_MAP = {
    "XXBT": "BTC",
    "XBT": "BTC",
    "XETH": "ETH",
    "ETH": "ETH",
    "XXRP": "XRP",
    "XRP": "XRP",
    "XLTC": "LTC",
    "LTC": "LTC",
    "XXLM": "XLM",
    "XLM": "XLM",
    "XDOT": "DOT",
    "DOT": "DOT",
    "XADA": "ADA",
    "ADA": "ADA",
    "XSOL": "SOL",
    "SOL": "SOL",
}

# Fiat currencies to skip (not positions)
FIAT_CURRENCIES = {"ZUSD", "USD", "ZEUR", "EUR", "ZGBP", "GBP", "ZCAD", "CAD", "ZJPY", "JPY"}

# Sync interval in seconds (10 seconds for near-real-time position updates)
# Default: 10s, max: 30s (configured via backend.intervals.config)
from backend.intervals.config import POSITION_SYNC_INTERVAL_SECONDS
SYNC_INTERVAL_SECONDS = POSITION_SYNC_INTERVAL_SECONDS


class PositionTracker:
    """
    Tracks current trading positions.
    
    Stores position state in Redis for persistence across restarts.
    Position key format: position:{symbol}
    """
    
    def __init__(self):
        """Initialize the position tracker."""
        self._redis = get_redis_client()
    
    def _get_key(self, symbol: str) -> str:
        """Get Redis key for a symbol's position."""
        return POSITION_KEY.format(symbol=symbol)
    
    def record_fill(
        self,
        fill,
        strategy_id: Optional[str] = None,
        execution_live: bool = False,
        strategy_canonical: Optional[str] = None,
    ) -> Position:
        """
        Update position from a fill.
        
        - Buy fills increase long position or reduce short position
        - Sell fills increase short position or reduce long position
        
        Args:
            fill: The executed fill to record (Fill object from backend.execution.models)
            strategy_id: ID of the strategy that initiated this trade (for new positions)
            execution_live: True if this position was opened with Kraken live execution (Task 3).
            strategy_canonical: If set on BUY open, per-strategy SIM balance is used (Task 3).
            
        Returns:
            The updated Position
        """
        # Lazy import to avoid circular dependency
        from backend.execution.models import Fill
        if not isinstance(fill, Fill):
            raise TypeError(f"fill must be a Fill instance, got {type(fill)}")
        key = self._get_key(fill.symbol)
        existing = self.get_position(fill.symbol)
        
        if existing is None:
            # New position
            side = "long" if fill.side == "buy" else "short"
            position = Position(
                symbol=fill.symbol,
                side=side,
                quantity=fill.quantity,
                entry_price=fill.executed_price,
                entry_time=fill.timestamp,
                unrealized_pnl=0.0,
                opened_by_strategy_id=strategy_id,
                execution_live=bool(execution_live),
                strategy_canonical=strategy_canonical,
            )
            
            # TICKET-612 / Task 3: shadow balance vs per-strategy SIM balance
            if fill.side == "buy":
                try:
                    position_cost = fill.quantity * fill.executed_price + fill.fees
                    if strategy_canonical:
                        from backend.supervisor.store import apply_strategy_sim_buy

                        apply_strategy_sim_buy(strategy_canonical, position_cost)
                        logger.info(
                            f"SIM balance updated: BUY ${position_cost:.2f} strategy={strategy_canonical}"
                        )
                    else:
                        from backend.api.routes.account import update_shadow_balance

                        updated_balance = update_shadow_balance(position_cost, "deduct")
                        if updated_balance:
                            logger.info(
                                f"Shadow balance updated: BUY ${position_cost:.2f}, "
                                f"new balance: ${updated_balance.get('total_usd', 0):.2f}"
                            )
                except Exception as e:
                    logger.warning(f"Failed to update balance for BUY: {e}")
            
            # Record trade opening in metrics if strategy provided
            if strategy_id:
                try:
                    # Lazy import to avoid circular dependency
                    from backend.risk.metrics import get_strategy_metrics
                    metrics = get_strategy_metrics()
                    # Use symbol as trade_id for tracking (one position per symbol)
                    metrics.record_trade(
                        strategy_id=strategy_id,
                        symbol=fill.symbol,
                        side=fill.side,
                        entry_price=fill.executed_price,
                        quantity=fill.quantity,
                        trade_id=fill.symbol,  # Use symbol as trade ID
                    )
                except Exception as e:
                    logger.warning(f"Failed to record trade in metrics: {e}")
        else:
            # Update existing position
            position = self._update_position(existing, fill, strategy_id=strategy_id)
            
            # Check if position was closed and record in metrics
            if position.quantity == 0 and existing.opened_by_strategy_id:
                # TICKET-612: Update shadow balance when position closed (SELL)
                if fill.side == "sell":
                    try:
                        exit_proceeds = fill.executed_price * existing.quantity - fill.fees
                        realized_pnl = (
                            (fill.executed_price - existing.entry_price) * existing.quantity
                            if existing.side == "long"
                            else (existing.entry_price - fill.executed_price) * existing.quantity
                        )
                        if existing.strategy_canonical:
                            from backend.supervisor.store import apply_strategy_sim_sell

                            apply_strategy_sim_sell(
                                existing.strategy_canonical,
                                exit_proceeds,
                                realized_pnl,
                                r_multiple=None,
                            )
                            logger.info(
                                f"SIM balance updated: SELL proceeds=${exit_proceeds:.2f} "
                                f"(P&L=${realized_pnl:.2f}) strategy={existing.strategy_canonical}"
                            )
                        else:
                            from backend.api.routes.account import update_shadow_balance

                            updated_balance = update_shadow_balance(exit_proceeds, "add")
                            if updated_balance:
                                logger.info(
                                    f"Shadow balance updated: SELL proceeds=${exit_proceeds:.2f} "
                                    f"(P&L=${realized_pnl:.2f}), "
                                    f"new balance: ${updated_balance.get('total_usd', 0):.2f}"
                                )
                    except Exception as e:
                        logger.warning(f"Failed to update balance for SELL: {e}")
                
                try:
                    # Lazy import to avoid circular dependency
                    from backend.risk.metrics import get_strategy_metrics
                    from backend.redis.keys import POSITION_EXIT_REASON_KEY
                    import json
                    
                    metrics = get_strategy_metrics()
                    
                    # Get exit reason from temporary storage (set by forced exit logic)
                    exit_reason = None
                    stop_loss_price = existing.stop_loss_price
                    
                    exit_reason_key = POSITION_EXIT_REASON_KEY.format(symbol=fill.symbol)
                    exit_reason_data = self._redis.get(exit_reason_key)
                    if exit_reason_data:
                        try:
                            exit_data = json.loads(exit_reason_data)
                            exit_reason = exit_data.get("reason")
                            # Use stop_loss_price from exit data if available, otherwise use position's
                            if exit_data.get("stop_loss_price"):
                                stop_loss_price = exit_data.get("stop_loss_price")
                            # Clear the temporary key
                            self._redis.delete(exit_reason_key)
                        except (json.JSONDecodeError, KeyError):
                            pass
                    
                    # Default exit reason if not found
                    if exit_reason is None:
                        # Try to infer from context (stop-loss vs take-profit vs manual)
                        if existing.stop_loss_price:
                            # Check if exit was near stop-loss
                            if abs(fill.executed_price - existing.stop_loss_price) / existing.stop_loss_price < 0.01:
                                exit_reason = existing.stop_exit_reason()
                            else:
                                exit_reason = "manual"  # Default to manual if unknown
                        else:
                            exit_reason = "unknown"
                    
                    pnl_result = metrics.close_trade(
                        trade_id=fill.symbol,  # Use symbol as trade ID
                        exit_price=fill.executed_price,
                        exit_reason=exit_reason,
                        stop_loss_price=stop_loss_price,
                    )
                    
                    # Ross Cameron spec: Blacklist symbol for 30 minutes after a loss
                    if pnl_result is not None and pnl_result < 0:
                        # Position closed at a loss - set cooldown
                        from backend.redis.keys import SIGNAL_EXECUTED_KEY_LEGACY, SIGNAL_COOLDOWN_SECONDS
                        cooldown_key = SIGNAL_EXECUTED_KEY_LEGACY.format(
                            strategy_id=existing.opened_by_strategy_id or "unknown",
                            symbol=fill.symbol
                        )
                        self._redis.setex(cooldown_key, SIGNAL_COOLDOWN_SECONDS, "1")
                        logger.info(
                            f"Loss cooldown set for {fill.symbol} after loss of ${abs(pnl_result):.2f} "
                            f"(strategy={existing.opened_by_strategy_id}, TTL={SIGNAL_COOLDOWN_SECONDS}s)"
                        )

                        # BUG5: Accumulate per-symbol losses; block symbol for 48h if > $1.50
                        try:
                            from backend.redis.keys import (
                                SYMBOL_CUMULATIVE_LOSS_KEY,
                                SYMBOL_BLOCKED_KEY,
                                SYMBOL_BLOCKED_TTL,
                            )
                            loss_key = SYMBOL_CUMULATIVE_LOSS_KEY.format(symbol=fill.symbol)
                            current_loss = float(self._redis.get(loss_key) or 0.0)
                            new_loss = current_loss + abs(pnl_result)
                            self._redis.set(loss_key, str(new_loss))
                            if new_loss >= 1.50:
                                blocked_key = SYMBOL_BLOCKED_KEY.format(symbol=fill.symbol)
                                self._redis.setex(blocked_key, SYMBOL_BLOCKED_TTL, f"cumulative_loss=${new_loss:.2f}")
                                logger.warning(
                                    f"Symbol {fill.symbol} blocked for 48h: cumulative loss ${new_loss:.2f}"
                                )
                        except Exception as _loss_err:
                            logger.warning(f"Failed to update symbol loss tracking: {_loss_err}")

                except Exception as e:
                    logger.warning(f"Failed to close trade in metrics: {e}")

                # BUG2: Always set FORCED_EXIT_COOLDOWN_KEY (15 min) on ANY position close so
                # that stop-loss fills and manual closes also enforce the re-entry cooldown.
                # _force_exit_position sets this too (45 min), so this acts as the minimum floor.
                try:
                    from backend.redis.keys import FORCED_EXIT_COOLDOWN_KEY
                    _strategy_id_cd = existing.opened_by_strategy_id or "unknown"
                    _forced_key = FORCED_EXIT_COOLDOWN_KEY.format(
                        symbol=fill.symbol, strategy_id=_strategy_id_cd
                    )
                    # Only set if no longer cooldown active (don't shorten a 45-min forced-exit cooldown)
                    existing_ttl = self._redis.ttl(_forced_key)
                    if existing_ttl < 900:  # 15 minutes minimum
                        self._redis.setex(_forced_key, 900, "1")
                        logger.info(
                            f"Post-exit cooldown set for {fill.symbol} "
                            f"(strategy={_strategy_id_cd}, TTL=900s)"
                        )
                except Exception as _cd_err:
                    logger.warning(f"Failed to set post-exit cooldown: {_cd_err}")
        
        # Store in Redis
        if position.quantity > 0:
            self._redis.hset(key, mapping=position.to_dict())
            logger.info(
                f"Position updated: {position.symbol} {position.side} "
                f"qty={position.quantity} @ {position.entry_price}"
            )
        else:
            # Position closed, remove from Redis and clean up TP1 keys
            self._redis.delete(key)
            from backend.redis.keys import POSITION_TP1_PRICE_KEY, POSITION_TP1_HIT_KEY
            tp1_price_key = POSITION_TP1_PRICE_KEY.format(symbol=fill.symbol)
            tp1_hit_key = POSITION_TP1_HIT_KEY.format(symbol=fill.symbol)
            self._redis.delete(tp1_price_key)
            self._redis.delete(tp1_hit_key)
            logger.info(f"Position closed: {fill.symbol}")
        
        return position
    
    def _update_position(
        self, existing: Position, fill: Fill, strategy_id: Optional[str] = None
    ) -> Position:
        """
        Update an existing position with a new fill.
        
        Handles:
        - Adding to position (same direction)
        - Reducing position (opposite direction)
        - Flipping position (opposite direction, larger quantity)
        """
        fill_is_buy = fill.side == "buy"
        existing_is_long = existing.side == "long"
        
        if fill_is_buy == existing_is_long:
            # Adding to position - calculate new average entry
            # Keep the original strategy_id (who opened the position)
            total_cost = (existing.quantity * existing.entry_price) + (fill.quantity * fill.executed_price)
            new_quantity = existing.quantity + fill.quantity
            new_entry_price = total_cost / new_quantity if new_quantity > 0 else 0

            return replace(
                existing,
                quantity=new_quantity,
                entry_price=new_entry_price,
                unrealized_pnl=0.0,
            )
        else:
            # Reducing or flipping position
            if fill.quantity < existing.quantity:
                # Partial close - keep original strategy_id
                new_quantity = existing.quantity - fill.quantity
                return replace(
                    existing,
                    quantity=new_quantity,
                    unrealized_pnl=0.0,
                )
            elif fill.quantity == existing.quantity:
                # Full close - return zero quantity position
                return replace(
                    existing,
                    quantity=0.0,
                    unrealized_pnl=0.0,
                )
            else:
                # Flip position - this is a new position, use new strategy_id
                new_quantity = fill.quantity - existing.quantity
                new_side = "long" if fill.side == "buy" else "short"
                return replace(
                    existing,
                    side=new_side,
                    quantity=new_quantity,
                    entry_price=fill.executed_price,
                    entry_time=fill.timestamp,
                    opened_by_strategy_id=strategy_id,
                    unrealized_pnl=0.0,
                )
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get current position for a symbol.
        
        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")
            
        Returns:
            Position if one exists, None otherwise
        """
        key = self._get_key(symbol)
        data = self._redis.hgetall(key)
        
        if not data:
            return None
        
        return Position.from_dict(data)
    
    def has_position(self, symbol: str) -> bool:
        """
        Check if we have an open position for this symbol.
        
        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")
            
        Returns:
            True if position exists with quantity > 0, False otherwise
        """
        position = self.get_position(symbol)
        return position is not None and position.quantity > 0
    
    def get_all_positions(self) -> list[Position]:
        """
        Get all current positions.
        
        Returns:
            List of all open positions (may be empty)
        """
        positions = []
        
        # Scan for all position keys
        cursor = 0
        pattern = "position:*"
        
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=100)
            
            for key in keys:
                # Skip keys that aren't position hashes (e.g., position:exit_reason:*)
                # Check if key matches the exact position pattern (position:{symbol})
                # Exit reason keys use format position:exit_reason:{symbol}, so skip those
                # Redis keys are bytes, so decode for string operations
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                if ':exit_reason:' in key_str or key_str.startswith('position:exit_reason:'):
                    logger.debug(f"Skipping exit reason key {key_str}")
                    continue
                
                try:
                    # Try to get as hash - will fail if not a hash type
                    data = self._redis.hgetall(key)
                    if data:
                        try:
                            position = Position.from_dict(data)
                            if position.quantity > 0:
                                positions.append(position)
                        except (KeyError, ValueError) as e:
                            logger.warning(f"Invalid position data in {key_str}: {e}")
                except Exception as e:
                    # Key exists but isn't a hash (wrong type) - skip it
                    logger.debug(f"Skipping non-hash key {key_str}: {e}")
                    continue
            
            if cursor == 0:
                break
        
        return positions
    
    def get_live_position_count(self) -> int:
        """
        Get count of live positions (excludes shadow positions).
        
        Live positions are those opened by strategies (opened_by_strategy_id is not None).
        Shadow positions are excluded from this count.
        
        Returns:
            Number of live positions (int)
        """
        all_positions = self.get_all_positions()
        
        # Count positions where opened_by_strategy_id is not None
        # This excludes shadow positions (which have opened_by_strategy_id=None)
        # and positions synced from Kraken without strategy tracking
        live_count = sum(
            1 for pos in all_positions
            if pos.opened_by_strategy_id is not None
        )
        
        return live_count
    
    def get_position_status(self, symbol: str) -> str:
        """
        Get position status for a symbol.
        
        Status values:
        - SCANNING: No position, no pending orders
        - PENDING: Strategy signal fired, limit order sent but not filled
        - LIVE: Order filled, position active
        - EXITING: TP/SL hit or 20m time-exit triggered, sell order live
        - COOLDOWN: Trade closed, banned from symbol for 30m
        - ERROR: Order rejected or API timeout
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Status string
        """
        try:
            # Check for explicit status override (set by executor)
            status_key = POSITION_STATUS_KEY.format(symbol=symbol)
            explicit_status = self._redis.get(status_key)
            if explicit_status:
                status = explicit_status.decode() if isinstance(explicit_status, bytes) else str(explicit_status)
                if status in ("SCANNING", "PENDING", "LIVE", "EXITING", "COOLDOWN", "ERROR"):
                    return status
            
            # Check for cooldown
            cooldown_key = POSITION_COOLDOWN_KEY.format(symbol=symbol)
            cooldown_end = self._redis.get(cooldown_key)
            if cooldown_end:
                try:
                    from datetime import datetime, timezone
                    cooldown_timestamp = cooldown_end.decode() if isinstance(cooldown_end, bytes) else str(cooldown_end)
                    cooldown_time = datetime.fromisoformat(cooldown_timestamp.replace('Z', '+00:00'))
                    if datetime.now(timezone.utc) < cooldown_time:
                        return "COOLDOWN"
                except (ValueError, TypeError):
                    pass
            
            # Check for pending order
            pending_key = POSITION_PENDING_ORDER_KEY.format(symbol=symbol)
            pending_order = self._redis.get(pending_key)
            if pending_order:
                return "PENDING"
            
            # Check for active position
            position = self.get_position(symbol)
            if position and position.quantity > 0:
                # Check if position is in exiting state (would be set by executor)
                # For now, assume LIVE if position exists
                return "LIVE"
            
            # Default: SCANNING
            return "SCANNING"
        except Exception as e:
            logger.debug(f"Error getting position status for {symbol}: {e}")
            return "SCANNING"
    
    def set_position_status(self, symbol: str, status: str) -> None:
        """
        Set explicit position status.
        
        Args:
            symbol: Trading pair symbol
            status: Status string (SCANNING, PENDING, LIVE, EXITING, COOLDOWN, ERROR)
        """
        try:
            status_key = POSITION_STATUS_KEY.format(symbol=symbol)
            if status in ("SCANNING", "PENDING", "LIVE", "EXITING", "COOLDOWN", "ERROR"):
                self._redis.set(status_key, status)
            else:
                logger.warning(f"Invalid status value: {status}")
        except Exception as e:
            logger.debug(f"Error setting position status for {symbol}: {e}")
    
    def set_cooldown(self, symbol: str, duration_seconds: int = 1800) -> None:
        """
        Set cooldown period for a symbol (30 minutes default).
        
        Args:
            symbol: Trading pair symbol
            duration_seconds: Cooldown duration in seconds (default: 1800 = 30 minutes)
        """
        try:
            from datetime import datetime, timezone, timedelta
            cooldown_end = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            cooldown_key = POSITION_COOLDOWN_KEY.format(symbol=symbol)
            self._redis.set(cooldown_key, cooldown_end.isoformat())
            logger.info(f"Cooldown set for {symbol} until {cooldown_end.isoformat()}")
        except Exception as e:
            logger.debug(f"Error setting cooldown for {symbol}: {e}")
    
    def close_position(self, symbol: str) -> bool:
        """
        Close a position (set quantity to 0 and remove from Redis).
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            True if position was closed, False if no position existed
        """
        key = self._get_key(symbol)
        
        if self._redis.exists(key):
            self._redis.delete(key)
            # Clean up TP1 tracking keys
            from backend.redis.keys import POSITION_TP1_PRICE_KEY, POSITION_TP1_HIT_KEY
            tp1_price_key = POSITION_TP1_PRICE_KEY.format(symbol=symbol)
            tp1_hit_key = POSITION_TP1_HIT_KEY.format(symbol=symbol)
            self._redis.delete(tp1_price_key)
            self._redis.delete(tp1_hit_key)
            logger.info(f"Position manually closed: {symbol}")
            return True
        
        return False
    
    def _normalize_kraken_currency(self, kraken_code: str) -> Optional[str]:
        """
        Convert Kraken currency code to standard symbol.
        
        Args:
            kraken_code: Kraken currency code (e.g., "XXBT", "XETH")
            
        Returns:
            Standard symbol (e.g., "BTC", "ETH") or None if fiat/unknown
        """
        # Skip fiat currencies
        if kraken_code in FIAT_CURRENCIES:
            return None
        
        # Check mapping
        if kraken_code in KRAKEN_CURRENCY_MAP:
            return KRAKEN_CURRENCY_MAP[kraken_code]
        
        # Try stripping X prefix for unknown cryptos
        if kraken_code.startswith("X") and len(kraken_code) > 1:
            return kraken_code[1:]
        
        # Return as-is for unknown codes
        return kraken_code
    
    def update_position_from_holding(
        self, 
        symbol: str, 
        quantity: float,
        entry_price: float = 0.0
    ) -> Optional[Position]:
        """
        Create or update a position from Kraken holding data.
        
        Since Kraken balance endpoint doesn't provide entry price,
        we use 0.0 as a placeholder to indicate it was synced from Kraken.
        
        Args:
            symbol: Trading pair (e.g., "ETH/USD")
            quantity: Holding quantity
            entry_price: Entry price (0.0 if unknown from sync)
            
        Returns:
            Created/updated Position or None if quantity is 0
        """
        if quantity <= 0:
            return None
        
        key = self._get_key(symbol)
        existing = self.get_position(symbol)
        
        if existing is not None:
            # Update quantity if changed
            if abs(existing.quantity - quantity) > 1e-8:
                position = Position(
                    symbol=symbol,
                    side=existing.side,
                    quantity=quantity,
                    entry_price=existing.entry_price,  # Keep existing entry price
                    entry_time=existing.entry_time,
                    unrealized_pnl=existing.unrealized_pnl,
                )
                self._redis.hset(key, mapping=position.to_dict())
                logger.info(
                    f"Position quantity updated from Kraken sync: {symbol} "
                    f"qty {existing.quantity} -> {quantity}"
                )
                return position
            return existing
        
        # Create new position (assume long for holdings from Kraken)
        now = datetime.now(timezone.utc).isoformat()
        position = Position(
            symbol=symbol,
            side="long",
            quantity=quantity,
            entry_price=entry_price if entry_price > 0 else 1.0,  # Use 1.0 as placeholder
            entry_time=now,
            unrealized_pnl=0.0,
        )
        
        self._redis.hset(key, mapping=position.to_dict())
        logger.info(
            f"Position created from Kraken sync: {symbol} long qty={quantity}"
        )
        
        return position
    
    async def sync_from_kraken(self) -> dict:
        """
        Fetch actual holdings from Kraken and update local state.
        
        Called on startup and every 5 minutes to:
        - Create positions for holdings we don't have locally
        - Update quantities for existing positions
        - Mark positions as closed if Kraken doesn't have them
        
        In shadow mode, this sync is skipped to prevent real Kraken positions
        from interfering with simulated shadow trading positions.
        
        Returns:
            Dict with sync results: {created: int, updated: int, closed: int, errors: list}
        """
        result = {
            "created": 0,
            "updated": 0, 
            "closed": 0,
            "errors": [],
            "synced_symbols": [],
        }
        
        # Skip Kraken sync in shadow mode - shadow trading should only track
        # positions created by shadow trades, not real exchange positions
        try:
            from backend.api.routes.trading import get_shadow_live_mode
            if get_shadow_live_mode():
                logger.debug("Skipping Kraken sync (shadow mode active - only shadow positions tracked)")
                return result
        except Exception as e:
            logger.warning(f"Failed to check shadow mode, proceeding with sync: {e}")
        
        logger.info("Starting Kraken position sync...")
        
        # Get balance from Kraken CLI
        try:
            from backend.execution.kraken_cli import get_balance as cli_get_balance
            balance = await cli_get_balance()
        except Exception as e:
            error_msg = f"Failed to fetch Kraken balance: {e}"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            return result
        
        if balance is None:
            error_msg = "Kraken returned empty balance"
            logger.warning(error_msg)
            result["errors"].append(error_msg)
            return result
        
        # Track which symbols we see from Kraken
        kraken_symbols = set()
        
        # Process each holding
        for currency_code, balance_str in balance.items():
            try:
                quantity = float(balance_str)
            except (ValueError, TypeError):
                logger.warning(f"Invalid balance value for {currency_code}: {balance_str}")
                continue
            
            # Skip zero or very small balances (dust)
            # Use a higher threshold to filter out essentially zero positions
            MIN_BALANCE_THRESHOLD = 0.001  # Minimum balance to consider a valid position
            if quantity < MIN_BALANCE_THRESHOLD:
                logger.debug(f"Skipping dust balance: {currency_code} qty={quantity}")
                continue
            
            # Convert currency code to symbol
            base_currency = self._normalize_kraken_currency(currency_code)
            if base_currency is None:
                # Fiat currency, skip
                continue
            
            # Build trading pair (assume USD quote)
            symbol = f"{base_currency}/USD"
            kraken_symbols.add(symbol)
            
            # Get existing position
            existing = self.get_position(symbol)
            
            # Update or create position
            position = self.update_position_from_holding(symbol, quantity)
            
            if position:
                result["synced_symbols"].append(symbol)
                if existing is None:
                    result["created"] += 1
                    logger.info(f"SYNC: Created position {symbol} qty={quantity}")
                elif abs(existing.quantity - quantity) > 1e-8:
                    result["updated"] += 1
                    logger.info(f"SYNC: Updated position {symbol} qty={quantity}")
        
        # Find positions we have locally but Kraken doesn't (or has dust balances)
        MIN_POSITION_QUANTITY = 0.01  # Minimum quantity to consider a valid position
        local_positions = self.get_all_positions()
        for pos in local_positions:
            # Close position if:
            # 1. Not found in Kraken symbols, OR
            # 2. Has very small quantity (dust) that we're filtering out
            if pos.symbol not in kraken_symbols:
                # Position exists locally but not on Kraken - mark as closed
                self.close_position(pos.symbol)
                result["closed"] += 1
                logger.info(f"SYNC: Closed position {pos.symbol} (not on Kraken)")
            elif pos.quantity < MIN_POSITION_QUANTITY:
                # Position has dust quantity - close it
                self.close_position(pos.symbol)
                result["closed"] += 1
                logger.info(f"SYNC: Closed position {pos.symbol} (dust quantity: {pos.quantity})")
        
        logger.info(
            f"Kraken sync complete: created={result['created']}, "
            f"updated={result['updated']}, closed={result['closed']}"
        )
        
        return result
    
    def update_position_pnl(self, symbol: str, current_price: float) -> Optional[Position]:
        """
        Update position with current market price and calculate unrealized P&L.
        
        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")
            current_price: Current market price from ticker
            
        Returns:
            Updated Position if found, None otherwise
        """
        position = self.get_position(symbol)
        if position is None or position.quantity <= 0:
            return None
        
        # Calculate unrealized P&L based on position side
        if position.side == "long":
            unrealized_pnl = (current_price - position.entry_price) * position.quantity
        else:  # short
            unrealized_pnl = (position.entry_price - current_price) * position.quantity
        
        # Update position with new price and P&L
        position.current_price = current_price
        position.unrealized_pnl = unrealized_pnl
        
        # Save to Redis
        key = self._get_key(symbol)
        self._redis.hset(key, mapping=position.to_dict())
        
        logger.debug(
            f"Updated P&L for {symbol}: ${unrealized_pnl:.2f} "
            f"(price: ${current_price:.2f}, entry: ${position.entry_price:.2f})"
        )
        
        return position

    def purge_corrupted_position(self, symbol: str, reason: str) -> bool:
        """
        Remove a position and related Redis keys when quantity is invalid or unsellable.

        Returns True if keys were deleted.
        """
        keys = [
            self._get_key(symbol),
            POSITION_STATUS_KEY.format(symbol=symbol),
            POSITION_COOLDOWN_KEY.format(symbol=symbol),
            POSITION_PENDING_ORDER_KEY.format(symbol=symbol),
            POSITION_EXIT_REASON_KEY.format(symbol=symbol),
            POSITION_EXIT_ATTEMPT_KEY.format(symbol=symbol),
            POSITION_EXIT_FAIL_COUNT_KEY.format(symbol=symbol),
            POSITION_TP1_PRICE_KEY.format(symbol=symbol),
            POSITION_TP1_HIT_KEY.format(symbol=symbol),
        ]
        deleted = int(self._redis.delete(*keys))
        if deleted:
            logger.warning(
                "CORRUPTED_POSITION_PURGED symbol=%s reason=%s keys_deleted=%d",
                symbol,
                reason,
                deleted,
            )
            try:
                from backend.api.routes.events import log_activity

                log_activity(
                    activity_type="warning",
                    message=f"Corrupted position purged: {symbol} ({reason})",
                    details={"symbol": symbol, "reason": reason},
                )
            except Exception:
                pass
        return deleted > 0

    def list_all_position_symbols(self) -> list[str]:
        """Return symbols with a position hash key."""
        symbols: list[str] = []
        prefix = "position:"
        for raw in self._redis.scan_iter(match="position:*"):
            key = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            if key.startswith(prefix) and ":status:" not in key and ":cooldown:" not in key:
                if key.count(":") == 1:
                    sym = key[len(prefix):]
                    if sym:
                        symbols.append(sym)
        return symbols


def purge_all_position_redis_keys(redis) -> int:
    """
    Delete every Redis key under the position:* namespace (hashes, status,
    cooldown, TP1, exit_reason, pending_order, etc.). Used on shadow account reset.
    """
    batch: list[str] = []
    total_deleted = 0
    batch_size = 500

    for raw in redis.scan_iter(match="position:*"):
        key = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        batch.append(key)
        if len(batch) >= batch_size:
            total_deleted += int(redis.delete(*batch))
            batch.clear()

    if batch:
        total_deleted += int(redis.delete(*batch))

    if total_deleted:
        logger.info(f"purge_all_position_redis_keys: deleted {total_deleted} key(s)")
    return total_deleted


# Global tracker instance
_tracker: Optional[PositionTracker] = None


def get_position_tracker() -> PositionTracker:
    """Get the global position tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = PositionTracker()
    return _tracker
