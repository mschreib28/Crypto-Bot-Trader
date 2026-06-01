"""Position monitoring service for updating P&L and monitoring stop-loss orders."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Update interval in seconds (10 seconds for near-real-time P&L updates)
# Default: 10s, max: 30s (configured via backend.intervals.config)
from backend.intervals.config import POSITION_MONITOR_INTERVAL_SECONDS
UPDATE_INTERVAL_SECONDS = POSITION_MONITOR_INTERVAL_SECONDS

# Rate limit: max 1 Kraken API call per second
KRAKEN_RATE_LIMIT_SECONDS = 1.0

# Hybrid exit: cross-strategy close when opener is silent or override confidence (see plan)
HYBRID_SILENT_MIN = 10
HYBRID_SECONDARY_SELL_PCT = 50.0
HYBRID_OVERRIDE_SELL_PCT = 75.0


def hybrid_bearish_exit_confidence(result_row: Optional[Dict[str, Any]]) -> Optional[float]:
    """
    Effective bearish exit strength for hybrid valves.
    Counts explicit SELL or threshold-downgraded NONE with original_signal SELL.
    """
    if not result_row:
        return None
    st = (result_row.get("signal_type") or "NONE").upper()
    conf = float(result_row.get("confidence") or 0.0)
    indicators = result_row.get("indicators") or {}
    if st == "SELL":
        return conf
    if st == "NONE" and str(indicators.get("original_signal") or "").upper() == "SELL":
        return conf
    return None


def bump_hybrid_silence_if_opener_missing_symbol(
    redis_client,
    opener_strategy_id: str,
    symbol: str,
    opener_screener_blob: Dict[str, Any],
) -> None:
    """
    When the opener's Redis results omit this symbol but last_scan advanced,
    increment opener silence once (orphan MACD / no row for SAHARA).
    """
    if not opener_strategy_id or not symbol or not opener_screener_blob:
        return
    from backend.redis.keys import (
        STRATEGY_HYBRID_LAST_OPENER_SCAN_KEY,
        STRATEGY_HYBRID_LAST_OPENER_SCAN_TTL,
        STRATEGY_SILENCE_COUNT_KEY,
        STRATEGY_SILENCE_COUNT_TTL,
    )

    results = opener_screener_blob.get("results") or []
    present = {r.get("symbol") for r in results if r.get("symbol")}
    if symbol in present:
        return

    last_scan = opener_screener_blob.get("last_scan") or ""
    if not last_scan:
        return

    meta_key = STRATEGY_HYBRID_LAST_OPENER_SCAN_KEY.format(
        strategy_id=opener_strategy_id, symbol=symbol
    )
    prev = redis_client.get(meta_key)
    prev_s = prev.decode() if isinstance(prev, bytes) else (prev or "")
    if last_scan == prev_s:
        return

    count_key = STRATEGY_SILENCE_COUNT_KEY.format(
        strategy_id=opener_strategy_id, symbol=symbol
    )
    redis_client.incr(count_key)
    redis_client.expire(count_key, STRATEGY_SILENCE_COUNT_TTL)
    redis_client.setex(meta_key, STRATEGY_HYBRID_LAST_OPENER_SCAN_TTL, last_scan)


class PositionMonitor:
    """
    Background service that periodically updates positions with current prices
    and calculates unrealized P&L.
    
    Also monitors stop-loss orders and checks for threshold breaches.
    """
    
    def __init__(self, update_interval: float = UPDATE_INTERVAL_SECONDS):
        """
        Initialize position monitor.
        
        Args:
            update_interval: Seconds between updates (default: 60s)
        """
        self.update_interval = update_interval
        self.tracker = None  # Lazy-loaded to avoid circular import
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        logger.info(f"PositionMonitor initialized: update_interval={update_interval}s")
    
    def _get_tracker(self):
        """Lazy-load position tracker to avoid circular imports."""
        if self.tracker is None:
            from backend.positions.tracker import get_position_tracker
            self.tracker = get_position_tracker()
        return self.tracker
    
    async def start(self):
        """Start the position monitor service."""
        if self._running:
            logger.warning("PositionMonitor already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("PositionMonitor started")
    
    async def stop(self):
        """Stop the position monitor service."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PositionMonitor stopped")
    
    async def _run_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._update_all_positions()
                await asyncio.sleep(self.update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in position monitor loop: {e}", exc_info=True)
                # Continue running even if one update fails
                await asyncio.sleep(self.update_interval)
    
    async def _update_all_positions(self):
        """
        Update all open positions with current prices and calculate P&L.
        
        Fetches current prices from Kraken ticker API and updates each position.
        """
        tracker = self._get_tracker()
        positions = tracker.get_all_positions()
        
        if not positions:
            logger.debug("No positions to update")
            return
        
        logger.info(f"Updating P&L for {len(positions)} position(s)")
        
        updated_count = 0
        error_count = 0
        
        for position in positions:
            try:
                # Rate limit: wait 1 second between API calls
                if updated_count > 0:
                    await asyncio.sleep(KRAKEN_RATE_LIMIT_SECONDS)
                
                # Get current price from Kraken
                current_price = await self._get_current_price(position.symbol)
                
                if current_price is None:
                    logger.warning(f"Could not fetch price for {position.symbol}, skipping")
                    error_count += 1
                    continue
                
                # Update position P&L
                tracker = self._get_tracker()
                updated = tracker.update_position_pnl(position.symbol, current_price)
                
                if updated:
                    updated_count += 1
                    logger.info(
                        f"Updated P&L for {position.symbol}: "
                        f"${updated.unrealized_pnl:.2f} "
                        f"(price: ${current_price:.2f}, entry: ${updated.entry_price:.2f})"
                    )
                    
                    # Check for Scout scale-in trigger (Soldier entry)
                    if updated.scout_entry_price and not updated.scale_in_triggered:
                        await self._check_scale_in_trigger(updated, current_price)
                    
                    # Check for TP1 hit
                    await self._check_tp1_hit(updated, current_price)
                    
                    # Check breakeven guard (before forced exits and trailing stop)
                    await self._check_breakeven_guard(updated, current_price)
                    
                    # Check for forced exits (max hold, invalidation) after P&L update
                    if updated.opened_by_strategy_id:
                        await self._check_hybrid_exit(updated, current_price)
                        await self._check_forced_exits(updated, current_price)
                        
                        # Check 48-hour opportunity filter
                        await self._check_48h_opportunity_filter(updated, current_price)
                        
                        # Check ATR trailing stop
                        await self._check_atr_trailing_stop(updated, current_price)
                    
                    # Update performance metrics when P&L changes
                    try:
                        from backend.performance.monitor import get_performance_monitor
                        perf_monitor = get_performance_monitor()
                        
                        if updated.opened_by_strategy_id:
                            # Get entry time
                            entry_time_str = updated.entry_time
                            try:
                                entry_time = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
                            except Exception:
                                entry_time = datetime.now()
                            
                            # Update performance (tracking unrealized P&L changes)
                            perf_monitor.update_trade_outcome(
                                strategy_id=updated.opened_by_strategy_id,
                                symbol=updated.symbol,
                                pnl=updated.unrealized_pnl,
                                entry_time=entry_time,
                            )
                            
                            # Update strategy equity for drawdown tracking
                            try:
                                from backend.risk.metrics import get_strategy_metrics
                                metrics = get_strategy_metrics()
                                metrics.update_strategy_equity(
                                    strategy_id=updated.opened_by_strategy_id,
                                    unrealized_pnl=updated.unrealized_pnl,
                                )
                            except Exception as e:
                                logger.debug(f"Failed to update strategy equity: {e}")
                    except Exception as e:
                        logger.debug(f"Failed to update performance metrics: {e}")
                else:
                    logger.warning(f"Position {position.symbol} not found or closed, skipping")
                    error_count += 1
                    
            except Exception as e:
                logger.error(f"Error updating position {position.symbol}: {e}", exc_info=True)
                error_count += 1
        
        logger.info(
            f"Position update complete: {updated_count} updated, {error_count} errors"
        )
    
    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current market price for a symbol via Kraken CLI ticker.

        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")

        Returns:
            Current price (last trade) as float, or None on error
        """
        try:
            from backend.execution.kraken_cli import (
                get_ticker,
                symbol_to_cli_pair,
                KrakenCLIError,
            )
            cli_pair = symbol_to_cli_pair(symbol)  # "BTC/USD" → "BTCUSD"
            ticker = await get_ticker(cli_pair)
            return ticker.last
        except Exception as e:
            logger.error(f"Failed to get current price for {symbol}: {e}")
            return None
    
    async def _check_scale_in_trigger(self, position, current_price: float):
        """
        Check if Scout position has reached +1.5% profit to trigger Soldier scale-in.
        
        Args:
            position: Position object with scout_entry_price
            current_price: Current market price
        """
        if not position.scout_entry_price or position.scale_in_triggered:
            return  # No scout entry or already triggered
        
        # Calculate profit percentage: (current_price - scout_entry_price) / scout_entry_price × 100
        profit_pct = ((current_price - position.scout_entry_price) / position.scout_entry_price) * 100.0
        
        scale_in_trigger_pct = float(os.getenv("SCALE_IN_PROFIT_TRIGGER_PCT", "1.5"))
        
        if profit_pct >= scale_in_trigger_pct:
            logger.info(
                f"Scale-in trigger reached for {position.symbol}: "
                f"profit={profit_pct:.2f}% >= {scale_in_trigger_pct}% "
                f"(scout_entry=${position.scout_entry_price:.2f}, current=${current_price:.2f})"
            )
            await self._execute_soldier_scale_in(position, current_price)
        else:
            logger.debug(
                f"Scale-in check for {position.symbol}: "
                f"profit={profit_pct:.2f}% < {scale_in_trigger_pct}% (not triggered)"
            )
    
    async def _execute_soldier_scale_in(self, position, current_price: float):
        """
        Execute Soldier scale-in: Buy $2.00 additional position and move stop to breakeven.
        
        Args:
            position: Position object with scout_entry_price
            current_price: Current market price (Soldier entry price)
        """
        try:
            soldier_scale_in_size_usd = float(os.getenv("SOLDIER_SCALE_IN_SIZE_USD", "2.00"))
            
            # Create TradeIntent for Soldier scale-in
            from backend.risk.evaluator import TradeIntent
            from backend.execution.executor import execute_trade

            trade_intent = TradeIntent(
                strategy_id=position.opened_by_strategy_id or "scout_soldier",
                symbol=position.symbol,
                side="buy",
                intent_type="enter",
                notional_risk_pct=2.0,  # Not used for Soldier sizing, but required
                metadata={
                    "soldier_scale_in": True,
                    "scout_entry_price": position.scout_entry_price,
                    "scale_in_size_usd": soldier_scale_in_size_usd,
                    "strategy_canonical": position.strategy_canonical,
                },
            )
            
            # Execute Soldier scale-in trade (live vs paper frozen at position open)
            fill = await execute_trade(
                trade_intent,
                current_price,
                live=position.execution_live,
            )
            
            if fill is None:
                logger.warning(f"Soldier scale-in failed for {position.symbol}: execute_trade returned None")
                return
            
            # Update position with Soldier entry details
            tracker = self._get_tracker()
            updated_position = tracker.get_position(position.symbol)
            
            if not updated_position:
                logger.warning(f"Position {position.symbol} not found after Soldier scale-in")
                return
            
            # Calculate breakeven stop price: scout_entry_price + fees
            # Estimate fees: typically 0.26% for Kraken maker/taker
            estimated_fees_pct = 0.0026  # 0.26%
            fees_per_unit = position.scout_entry_price * estimated_fees_pct
            breakeven_stop_price = position.scout_entry_price + fees_per_unit
            
            # Update position fields
            updated_position.soldier_entry_price = current_price
            updated_position.scale_in_triggered = True
            updated_position.breakeven_guard_active = True
            updated_position.breakeven_stop_price = breakeven_stop_price
            
            # Update stop-loss order on Kraken to breakeven price
            if updated_position.stop_loss_order_id:
                try:
                    from backend.execution import kraken_cli as _kraken_cli
                    await _kraken_cli.cancel_order(updated_position.stop_loss_order_id)
                    logger.info(f"Cancelled old stop-loss order {updated_position.stop_loss_order_id}")

                    kraken_pair = _kraken_cli.symbol_to_cli_pair(position.symbol)
                    breakeven_stop_rounded = round(breakeven_stop_price, 3)

                    sl_result = await _kraken_cli.place_order(
                        pair=kraken_pair,
                        side="sell",
                        quantity=float(updated_position.quantity),
                        order_type="stop-loss",
                        price=breakeven_stop_rounded,
                    )
                    txid_raw = sl_result.get("txid", "")
                    new_txid = txid_raw[0] if isinstance(txid_raw, list) else str(txid_raw)
                    updated_position.stop_loss_order_id = new_txid
                    updated_position.stop_loss_price = breakeven_stop_rounded

                    logger.info(
                        f"Updated stop-loss to breakeven: ${breakeven_stop_rounded:.3f} "
                        f"(new txid: {new_txid})"
                    )
                except Exception as e:
                    logger.error(f"Failed to update stop-loss to breakeven: {e}")
                    # Continue even if stop-loss update fails
            
            # Save updated position to Redis
            from backend.redis import get_redis_client
            from backend.redis.keys import POSITION_KEY
            redis_client = get_redis_client()
            key = POSITION_KEY.format(symbol=position.symbol)
            redis_client.hset(key, mapping=updated_position.to_dict())
            
            logger.info(
                f"Soldier scale-in (TICKET-602): $2.00 @ ${current_price:.2f}, "
                f"stop moved to breakeven ${breakeven_stop_price:.3f}"
            )
            
            # Log SOLDIER_SCALE_IN activity
            from backend.api.routes.events import log_activity
            log_activity(
                activity_type="SOLDIER_SCALE_IN",
                message=(
                    f"Soldier scale-in: {position.symbol} - "
                    f"${soldier_scale_in_size_usd:.2f} @ ${current_price:.2f}, "
                    f"stop moved to breakeven ${breakeven_stop_price:.3f}"
                ),
                details={
                    "symbol": position.symbol,
                    "scout_entry_price": position.scout_entry_price,
                    "soldier_entry_price": current_price,
                    "scale_in_size_usd": soldier_scale_in_size_usd,
                    "breakeven_stop_price": breakeven_stop_price,
                    "strategy": position.opened_by_strategy_id,
                },
            )
            
        except Exception as e:
            logger.error(
                f"Error executing Soldier scale-in for {position.symbol}: {e}",
                exc_info=True
            )
    
    async def _check_forced_exits(self, position, current_price: float):
        """
        Check if position should be force-exited (max hold, invalidation).
        
        Args:
            position: Position object with entry_time, opened_by_strategy_id
            current_price: Current market price
        """
        if not position.opened_by_strategy_id:
            return  # Only check strategy-owned positions
        
        # Check explicit stop-loss price breach first (hard floor)
        await self._check_stop_loss_exit(position, current_price)

        # Check max hold (time-based exit)
        await self._check_max_hold_exit(position, current_price)

        # Check structural invalidation (VWAP, RSI, HTF regime)
        await self._check_invalidation_exit(position, current_price)
    
    async def _check_stop_loss_exit(self, position, current_price: float):
        """
        Check if current price has breached the stop-loss price stored on the position.

        Paper/shadow mode has no real stop-loss orders on the exchange, so the monitor
        must enforce the stop level explicitly on every price tick.
        """
        if not position.stop_loss_price or position.stop_loss_price <= 0:
            return

        triggered = False
        if position.side == "long" and current_price <= position.stop_loss_price:
            triggered = True
        elif position.side == "short" and current_price >= position.stop_loss_price:
            triggered = True

        if not triggered:
            return

        logger.warning(
            f"Stop-loss triggered for {position.symbol}: "
            f"price ${current_price:.4f} {'<=' if position.side == 'long' else '>='} "
            f"stop ${position.stop_loss_price:.4f}"
        )

        # Resolve strategy name for the log entry
        try:
            from backend.db import get_session
            from backend.db.models import Strategy
            import uuid as uuid_module

            session = get_session()
            try:
                strategy_id = position.opened_by_strategy_id
                try:
                    strategy_uuid = uuid_module.UUID(strategy_id)
                    strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                except ValueError:
                    strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
                strategy_name = strategy.name if strategy else strategy_id
            finally:
                session.close()
        except Exception:
            strategy_name = position.opened_by_strategy_id or "unknown"

        entry_time = datetime.fromisoformat(position.entry_time.replace("Z", "+00:00"))
        time_diff_minutes = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60.0
        candles_held = time_diff_minutes / 15.0  # approximate

        reason = position.stop_exit_reason()

        await self._force_exit_position(
            position=position,
            current_price=current_price,
            reason=reason,
            candles_held=candles_held,
            strategy_name=strategy_name,
        )

    async def _check_max_hold_exit(self, position, current_price: float):
        """
        Check if position has exceeded max hold duration and force exit if needed.
        
        Args:
            position: Position object
            current_price: Current market price
        """
        try:
            # Get strategy config to determine max hold and interval
            from backend.db import get_session
            from backend.db.models import Strategy
            import uuid as uuid_module
            
            session = get_session()
            try:
                # Try to find strategy by ID (UUID or name)
                strategy_id = position.opened_by_strategy_id
                try:
                    strategy_uuid = uuid_module.UUID(strategy_id)
                    strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                except ValueError:
                    # Not a UUID, try by name
                    strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
                
                if not strategy:
                    logger.debug(f"Strategy {strategy_id} not found, skipping max hold check")
                    return
                
                # Get strategy config
                config = strategy.config or {}
                strategy_name = strategy.name
                
                # Get interval from config
                interval_str = config.get("interval") or config.get("parameters", {}).get("interval", "5m")
                
                # Get max hold candles from config or use defaults
                max_hold_candles = config.get("max_hold_candles")
                if max_hold_candles is None:
                    # Use defaults based on strategy name
                    strategy_name_lower = strategy_name.lower()
                    if "vwap" in strategy_name_lower or "mean" in strategy_name_lower:
                        max_hold_candles = 12  # 3h at 15m (was 6 — too short to cover fees)
                    elif "volatility" in strategy_name_lower or "breakout" in strategy_name_lower:
                        max_hold_candles = 4  # 20 min for 5m interval
                    elif "htf" in strategy_name_lower or "trend" in strategy_name_lower or "pullback" in strategy_name_lower:
                        # HTF strategies use HTF interval (e.g., 1h = 3 candles = 3h)
                        max_hold_candles = 3
                    else:
                        max_hold_candles = 6  # Safe default
                
                # Parse interval string to minutes (e.g., "5m" -> 5, "1h" -> 60, "15m" -> 15)
                interval_minutes = self._parse_interval_to_minutes(interval_str)
                
                # Calculate candles held
                entry_time = datetime.fromisoformat(position.entry_time.replace('Z', '+00:00'))
                current_time = datetime.now(timezone.utc)
                time_diff_minutes = (current_time - entry_time).total_seconds() / 60.0
                candles_held = time_diff_minutes / interval_minutes
                
                if candles_held >= max_hold_candles:
                    logger.warning(
                        f"Max hold exceeded for {position.symbol}: "
                        f"held {candles_held:.1f} candles (limit: {max_hold_candles}, "
                        f"interval: {interval_str}, time: {time_diff_minutes:.1f} min)"
                    )
                    await self._force_exit_position(
                        position=position,
                        current_price=current_price,
                        reason="max_hold",
                        candles_held=candles_held,
                        strategy_name=strategy_name,
                    )
                else:
                    logger.debug(
                        f"Max hold check for {position.symbol}: "
                        f"{candles_held:.1f}/{max_hold_candles} candles (OK)"
                    )
                    
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error checking max hold for {position.symbol}: {e}", exc_info=True)
    
    def _parse_interval_to_minutes(self, interval_str: str) -> float:
        """
        Parse interval string to minutes.
        
        Examples:
            "5m" -> 5
            "15m" -> 15
            "1h" -> 60
            "4h" -> 240
        """
        interval_str = interval_str.lower().strip()
        
        if interval_str.endswith('m'):
            return float(interval_str[:-1])
        elif interval_str.endswith('h'):
            return float(interval_str[:-1]) * 60.0
        elif interval_str.endswith('d'):
            return float(interval_str[:-1]) * 1440.0
        else:
            # Try to parse as number (assume minutes)
            try:
                return float(interval_str)
            except ValueError:
                logger.warning(f"Unknown interval format: {interval_str}, defaulting to 5 minutes")
                return 5.0

    def _strategy_display_name(self, strategy_id: str) -> str:
        """Resolve DB strategy name for logging (UUID or legacy name)."""
        try:
            from backend.db import get_session
            from backend.db.models import Strategy
            import uuid as uuid_module

            session = get_session()
            try:
                try:
                    strategy_uuid = uuid_module.UUID(strategy_id)
                    strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                except ValueError:
                    strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
                return strategy.name if strategy else strategy_id
            finally:
                session.close()
        except Exception:
            return strategy_id

    def _opener_strategy_interval_minutes(self, opener_id: str) -> float:
        """Chart bar length in minutes for the opening strategy (default 5m if unknown)."""
        try:
            from backend.db import get_session
            from backend.db.models import Strategy
            import uuid as uuid_module

            session = get_session()
            try:
                try:
                    strategy_uuid = uuid_module.UUID(opener_id)
                    strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                except ValueError:
                    strategy = session.query(Strategy).filter(Strategy.name == opener_id).first()
                if not strategy:
                    return 5.0
                config = strategy.config or {}
                interval_str = config.get("interval") or config.get("parameters", {}).get(
                    "interval", "5m"
                )
                return self._parse_interval_to_minutes(str(interval_str))
            finally:
                session.close()
        except Exception:
            return 5.0

    async def _check_hybrid_exit(self, position, current_price: float) -> None:
        """
        Cross-strategy exit when opener is silent (valve 1) or any strategy SELL >= 75% (valve 2).
        """
        from backend.supervisor.config import MIN_HYBRID_EXIT_HOLD_BARS

        opener_id = position.opened_by_strategy_id
        if not opener_id or position.quantity <= 0:
            return

        try:
            from backend.redis import get_redis_client
            from backend.redis.keys import SCREENER_STRATEGY_RESULTS_KEY, STRATEGY_SILENCE_COUNT_KEY

            rc = get_redis_client()
            opener_key = SCREENER_STRATEGY_RESULTS_KEY.format(strategy_id=opener_id)
            raw = rc.get(opener_key)
            opener_blob: Dict[str, Any] = {}
            if raw:
                try:
                    opener_blob = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                except json.JSONDecodeError:
                    opener_blob = {}

            bump_hybrid_silence_if_opener_missing_symbol(rc, opener_id, position.symbol, opener_blob)

            entry_dt = datetime.fromisoformat(position.entry_time.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            interval_min = self._opener_strategy_interval_minutes(opener_id)
            if interval_min <= 0:
                interval_min = 5.0
            bars_elapsed = (now_utc - entry_dt).total_seconds() / 60.0 / interval_min
            if bars_elapsed < float(MIN_HYBRID_EXIT_HOLD_BARS):
                logger.debug(
                    "[HYBRID_EXIT] %s skipped — only %.2f bars since entry, minimum is %s",
                    position.symbol,
                    bars_elapsed,
                    MIN_HYBRID_EXIT_HOLD_BARS,
                )
                return

            silence_key = STRATEGY_SILENCE_COUNT_KEY.format(
                strategy_id=opener_id, symbol=position.symbol
            )
            silence_raw = rc.get(silence_key)
            try:
                silence_n = int(silence_raw) if silence_raw is not None else 0
            except (TypeError, ValueError):
                silence_n = 0

            from backend.db import get_session
            from backend.db.models import Strategy

            session = get_session()
            try:
                rows = session.query(Strategy).filter(Strategy.status == "active").all()
                strategy_rows: List[Tuple[str, str]] = [(str(s.id), s.name) for s in rows]
            finally:
                session.close()

            def _row_for_symbol(blob: Dict[str, Any], sym: str) -> Optional[Dict[str, Any]]:
                for r in blob.get("results") or []:
                    if r.get("symbol") == sym:
                        return r
                return None

            valve2: List[Tuple[float, str, str]] = []
            valve1: List[Tuple[float, str, str]] = []

            for sid, sname in strategy_rows:
                sk = SCREENER_STRATEGY_RESULTS_KEY.format(strategy_id=sid)
                data = rc.get(sk)
                if not data:
                    continue
                try:
                    blob = json.loads(data.decode() if isinstance(data, bytes) else data)
                except json.JSONDecodeError:
                    continue
                row = _row_for_symbol(blob, position.symbol)
                if not row:
                    continue
                conf = hybrid_bearish_exit_confidence(row)
                if conf is None:
                    continue
                if conf >= HYBRID_OVERRIDE_SELL_PCT:
                    valve2.append((conf, sid, sname))
                elif (
                    conf >= HYBRID_SECONDARY_SELL_PCT
                    and sid != opener_id
                    and silence_n >= HYBRID_SILENT_MIN
                ):
                    valve1.append((conf, sid, sname))

            pick: Optional[Tuple[float, str, str]] = None
            hybrid_kind: Optional[str] = None
            if valve2:
                pick = max(valve2, key=lambda x: (x[0], x[1]))
                hybrid_kind = "high_confidence"
            elif valve1:
                pick = max(valve1, key=lambda x: (x[0], x[1]))
                hybrid_kind = "silent_opener"

            if not pick or not hybrid_kind:
                return

            conf, closer_id, closer_name = pick
            opener_dn = self._strategy_display_name(opener_id)

            candles_held = bars_elapsed

            if hybrid_kind == "high_confidence":
                logger.info(
                    f"[HYBRID_EXIT] {position.symbol} closed by {closer_name} — "
                    f"high confidence override {conf:.1f}%"
                )
                reason = (
                    f"hybrid_exit_high_confidence|closer={closer_name}|closer_id={closer_id}|conf={conf:.1f}"
                )
            else:
                logger.info(
                    f"[HYBRID_EXIT] {position.symbol} closed by {closer_name} — "
                    f"opener {opener_dn} silent for {silence_n} bars"
                )
                reason = (
                    f"hybrid_exit_silent_opener|closer={closer_name}|closer_id={closer_id}|n={silence_n}"
                )

            await self._force_exit_position(
                position=position,
                current_price=current_price,
                reason=reason,
                candles_held=candles_held,
                strategy_name=opener_dn,
                closing_strategy_id=closer_id,
                closing_strategy_name=closer_name,
                hybrid_kind=hybrid_kind,
            )
        except Exception as e:
            logger.error(f"Error in hybrid exit check for {position.symbol}: {e}", exc_info=True)
    
    async def _force_exit_position(
        self,
        position,
        current_price: float,
        reason: str,
        candles_held: float,
        strategy_name: str,
        *,
        closing_strategy_id: Optional[str] = None,
        closing_strategy_name: Optional[str] = None,
        hybrid_kind: Optional[str] = None,
    ):
        """
        Force exit a position due to max hold or invalidation.
        
        Args:
            position: Position object to exit
            current_price: Current market price
            reason: Exit reason ("max_hold", "invalidation_vwap", etc.)
            candles_held: Number of candles position was held
            strategy_name: Strategy name for logging
        """
        try:
            # Check if position still exists (may have been closed already)
            tracker = self._get_tracker()
            current_position = tracker.get_position(position.symbol)
            if not current_position or current_position.quantity <= 0:
                logger.debug(f"Position {position.symbol} already closed, skipping forced exit")
                return

            from backend.positions.quantity import is_valid_position_quantity
            from backend.redis.keys import (
                POSITION_EXIT_FAIL_COUNT_KEY,
                POSITION_EXIT_FAIL_MAX,
            )

            if not is_valid_position_quantity(current_position.quantity):
                tracker.purge_corrupted_position(
                    position.symbol, reason="invalid_quantity_before_force_exit"
                )
                return

            # Debounce: skip if we already attempted an exit within the last 60s.
            # This prevents the monitor from flooding the log with repeated sell
            # failures when the paper CLI account temporarily lacks balance.
            from backend.redis import get_redis_client as _get_redis_for_debounce
            from backend.redis.keys import POSITION_EXIT_ATTEMPT_KEY, POSITION_EXIT_ATTEMPT_TTL
            _rc_debounce = _get_redis_for_debounce()
            _exit_attempt_key = POSITION_EXIT_ATTEMPT_KEY.format(symbol=position.symbol)
            if _rc_debounce.exists(_exit_attempt_key):
                logger.debug(
                    f"[exit-debounce] Skipping forced exit for {position.symbol} "
                    f"— attempted within last {POSITION_EXIT_ATTEMPT_TTL}s"
                )
                return
            _rc_debounce.setex(_exit_attempt_key, POSITION_EXIT_ATTEMPT_TTL, "1")

            # Calculate P&L percentage
            pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100.0
            
            logger.info(
                f"Forcing exit for {position.symbol}: reason={reason}, "
                f"candles_held={candles_held:.1f}, pnl={pnl_pct:.1f}%"
            )
            
            # Create TradeIntent for exit
            from backend.risk.evaluator import TradeIntent
            from backend.execution.executor import execute_trade

            exit_metadata: Dict[str, Any] = {
                "forced_exit": True,
                "exit_reason": reason,
                "candles_held": candles_held,
                "entry_price": position.entry_price,
                "entry_time": position.entry_time,
                "strategy_canonical": position.strategy_canonical,
            }
            if hybrid_kind and closing_strategy_id:
                exit_metadata["hybrid_exit"] = True
                exit_metadata["hybrid_kind"] = hybrid_kind
                exit_metadata["closing_strategy_id"] = closing_strategy_id
                exit_metadata["closing_strategy_name"] = (
                    closing_strategy_name or closing_strategy_id
                )

            trade_intent = TradeIntent(
                strategy_id=position.opened_by_strategy_id,
                symbol=position.symbol,
                side="sell",
                intent_type="exit",
                notional_risk_pct=2.0,  # Not used for exits, but required
                metadata=exit_metadata,
            )
            
            # Store exit reason temporarily for metrics tracking
            from backend.redis import get_redis_client
            from backend.redis.keys import POSITION_EXIT_REASON_KEY, POSITION_EXIT_REASON_TTL
            redis_client = get_redis_client()
            exit_reason_key = POSITION_EXIT_REASON_KEY.format(symbol=position.symbol)
            exit_reason_data: Dict[str, Any] = {
                "reason": reason,
                "candles_held": candles_held,
                "stop_loss_price": position.stop_loss_price,
            }
            if hybrid_kind and closing_strategy_id:
                exit_reason_data["hybrid_kind"] = hybrid_kind
                exit_reason_data["closing_strategy_id"] = closing_strategy_id
                exit_reason_data["closing_strategy"] = (
                    closing_strategy_name or closing_strategy_id
                )
            redis_client.setex(exit_reason_key, POSITION_EXIT_REASON_TTL, json.dumps(exit_reason_data))
            
            # Execute the exit trade (live vs paper frozen at position open)
            fill = await execute_trade(
                trade_intent,
                current_price,
                live=position.execution_live,
            )

            if fill is None:
                _fail_key = POSITION_EXIT_FAIL_COUNT_KEY.format(symbol=position.symbol)
                fails = int(redis_client.incr(_fail_key))
                redis_client.expire(_fail_key, 86400)
                if fails >= POSITION_EXIT_FAIL_MAX:
                    tracker.purge_corrupted_position(
                        position.symbol,
                        reason=f"exit_failed_{fails}_times",
                    )
                    redis_client.delete(_fail_key)
                return

            redis_client.delete(
                POSITION_EXIT_FAIL_COUNT_KEY.format(symbol=position.symbol)
            )

            # Log EXIT_FORCED activity
            from backend.api.routes.events import log_activity
            _paper = not position.execution_live

            _details: Dict[str, Any] = {
                "symbol": position.symbol,
                "strategy": strategy_name,
                "strategy_id": position.opened_by_strategy_id,
                "reason": reason,
                "candles_held": candles_held,
                "pnl_pct": pnl_pct,
                "exit_price": current_price,
                "entry_price": position.entry_price,
                "entry_time": position.entry_time,
                "unrealized_pnl": position.unrealized_pnl,
                "mode": "paper" if _paper else "live",
            }
            if hybrid_kind and closing_strategy_id:
                _details["hybrid_exit"] = True
                _details["hybrid_kind"] = hybrid_kind
                _details["closing_strategy_id"] = closing_strategy_id
                _details["closing_strategy_name"] = (
                    closing_strategy_name or closing_strategy_id
                )

            log_activity(
                activity_type="EXIT_FORCED",
                message=(
                    f"Forced exit: {position.symbol} [{strategy_name}] - {reason} "
                    f"(hold: {candles_held:.1f} candles, P&L: {pnl_pct:.1f}%)"
                ),
                details=_details,
            )

            logger.info(
                f"Forced exit executed: {position.symbol} sold at ${current_price:.2f} "
                f"(reason: {reason}, candles: {candles_held:.1f})"
            )

            from backend.redis.keys import (
                SIGNAL_EXECUTED_KEY_LEGACY,
                SIGNAL_COOLDOWN_SECONDS,
                FORCED_EXIT_COOLDOWN_KEY,
                FORCED_EXIT_COOLDOWN_TTL,
            )
            strategy_id = position.opened_by_strategy_id or "unknown"

            forced_key = FORCED_EXIT_COOLDOWN_KEY.format(
                symbol=position.symbol, strategy_id=strategy_id
            )
            redis_client.setex(forced_key, FORCED_EXIT_COOLDOWN_TTL, "1")
            tracker.set_cooldown(position.symbol, FORCED_EXIT_COOLDOWN_TTL)

            if reason.startswith("invalidation_"):
                inv_cooldown_key = SIGNAL_EXECUTED_KEY_LEGACY.format(
                    strategy_id=strategy_id, symbol=position.symbol
                )
                redis_client.setex(inv_cooldown_key, SIGNAL_COOLDOWN_SECONDS, "1")

            logger.info(
                f"Post-exit cooldown set for {position.symbol} "
                f"(reason={reason}, duration={FORCED_EXIT_COOLDOWN_TTL}s)"
            )

        except Exception as e:
            logger.error(
                f"Error forcing exit for {position.symbol}: {e}",
                exc_info=True
            )
    
    async def _check_invalidation_exit(self, position, current_price: float):
        """
        Check if position should be force-exited due to structural invalidation.
        
        Invalidation conditions:
        - VWAP Mean Reversion: Price closes > N ATR away from VWAP
        - RSI Mean Reversion: RSI fails to mean-revert after M candles
        - HTF Trend Pullback: HTF regime flips against trade direction
        
        Args:
            position: Position object
            current_price: Current market price
        """
        if not position.opened_by_strategy_id:
            return  # Only check strategy-owned positions
        
        try:
            # Get strategy config to determine invalidation rules
            from backend.db import get_session
            from backend.db.models import Strategy
            import uuid as uuid_module
            from backend.redis import get_redis_client
            from backend.redis.keys import SCREENER_STRATEGY_RESULTS_KEY
            
            session = get_session()
            try:
                # Find strategy
                strategy_id = position.opened_by_strategy_id
                try:
                    strategy_uuid = uuid_module.UUID(strategy_id)
                    strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                except ValueError:
                    strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
                
                if not strategy:
                    logger.debug(f"Strategy {strategy_id} not found, skipping invalidation check")
                    return
                
                config = strategy.config or {}
                strategy_name = strategy.name
                strategy_name_lower = strategy_name.lower()
                
                # Get indicators from screener results (cached in Redis)
                client = get_redis_client()
                screener_key = SCREENER_STRATEGY_RESULTS_KEY.format(strategy_id=strategy_id)
                screener_data = client.get(screener_key)
                
                if not screener_data:
                    logger.debug(f"No screener results found for {strategy_id}, skipping invalidation check")
                    return
                
                screener_results = json.loads(screener_data)
                results = screener_results.get("results", [])
                
                # Find indicators for this symbol
                symbol_indicators = None
                for result in results:
                    if result.get("symbol") == position.symbol:
                        symbol_indicators = result.get("indicators", {})
                        break
                
                if not symbol_indicators:
                    logger.debug(f"No indicators found for {position.symbol} in screener results, skipping invalidation check")
                    return
                
                # Check invalidation conditions based on strategy type
                if "vwap" in strategy_name_lower or "mean" in strategy_name_lower:
                    # VWAP Mean Reversion: Check if price > N ATR away from VWAP.
                    # Minimum grace period: 1 full candle must elapse before this check
                    # fires. This prevents stale pre-entry screener indicators (which
                    # showed price far from VWAP — exactly the entry signal) from
                    # immediately invalidating the position.
                    vwap = symbol_indicators.get("vwap")
                    atr = symbol_indicators.get("atr")
                    invalidation_atr_mult = config.get("invalidation_vwap_atr_mult") or config.get("parameters", {}).get("invalidation_vwap_atr_mult", 2.0)

                    # Calculate actual candles held (same method as RSI block below)
                    entry_time_vwap = datetime.fromisoformat(position.entry_time.replace('Z', '+00:00'))
                    current_time_vwap = datetime.now(timezone.utc)
                    interval_str_vwap = config.get("interval") or config.get("parameters", {}).get("interval", "5m")
                    interval_minutes_vwap = self._parse_interval_to_minutes(interval_str_vwap)
                    time_diff_vwap = (current_time_vwap - entry_time_vwap).total_seconds() / 60.0
                    candles_held_vwap = time_diff_vwap / interval_minutes_vwap if interval_minutes_vwap > 0 else 0.0

                    if candles_held_vwap < 2.0:
                        logger.debug(
                            f"VWAP invalidation skipped for {position.symbol}: "
                            f"only {candles_held_vwap:.2f} candles elapsed (grace period: 2 candles)"
                        )
                    elif vwap is not None and atr is not None and atr > 0:
                        deviation = abs(current_price - vwap)
                        deviation_atr = deviation / atr

                        if deviation_atr > invalidation_atr_mult:
                            # Min-profit gate: only suppress VWAP invalidation exit for near-flat winners.
                            # A 0.1% gain exited via invalidation is a net loss after ~0.52% round-trip fees.
                            # IMPORTANT: Only suppress when P&L is between 0% and the min threshold.
                            # Losing positions (pnl_pct < 0) must still exit — stop-loss may be missing.
                            if not position.entry_price or position.entry_price <= 0:
                                logger.warning(
                                    f"VWAP invalidation: invalid entry_price for {position.symbol}, "
                                    f"skipping min-profit gate and proceeding with exit"
                                )
                            else:
                                min_profit_pct = float(os.environ.get("MIN_PROFIT_FOR_INVALIDATION_PCT", "0.6"))
                                if position.side == "long":
                                    pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100.0
                                else:
                                    pnl_pct = ((position.entry_price - current_price) / position.entry_price) * 100.0

                                # BUG4: Extend suppression to 1.5R (1.5 × stop_loss_pct).
                                # A winner that hasn't reached 1.5R yet is being cut early by
                                # invalidation, degrading R:R. Losers (pnl_pct < 0) still exit.
                                _stop_pct = float(os.environ.get("STOP_LOSS_PCT", "5.0"))
                                min_profit_threshold = max(min_profit_pct, _stop_pct * 1.5)
                                if 0.0 < pnl_pct < min_profit_threshold:
                                    logger.info(
                                        f"VWAP invalidation suppressed for {position.symbol}: "
                                        f"P&L {pnl_pct:.2f}% < 1.5R threshold {min_profit_threshold:.1f}% "
                                        f"(stop={_stop_pct}%, deviation_atr={deviation_atr:.2f}). "
                                        f"Stop-loss will protect downside."
                                    )
                                    return

                            logger.warning(
                                f"VWAP invalidation for {position.symbol}: "
                                f"price ${current_price:.2f} is {deviation_atr:.2f} ATR from VWAP ${vwap:.2f} "
                                f"(threshold: {invalidation_atr_mult} ATR, held {candles_held_vwap:.1f} candles, "
                                f"P&L {pnl_pct:.2f}%)"
                            )
                            await self._force_exit_position(
                                position=position,
                                current_price=current_price,
                                reason="invalidation_vwap",
                                candles_held=candles_held_vwap,
                                strategy_name=strategy_name,
                            )
                            return
                
                if "mean" in strategy_name_lower and "rsi" in symbol_indicators:
                    # RSI Mean Reversion: Check if RSI fails to mean-revert after M candles
                    rsi = symbol_indicators.get("rsi")
                    invalidation_rsi_candles = config.get("invalidation_rsi_candles") or config.get("parameters", {}).get("invalidation_rsi_candles", 4)
                    
                    if rsi is not None:
                        # Calculate candles since entry
                        entry_time = datetime.fromisoformat(position.entry_time.replace('Z', '+00:00'))
                        current_time = datetime.now(timezone.utc)
                        interval_str = config.get("interval") or config.get("parameters", {}).get("interval", "5m")
                        interval_minutes = self._parse_interval_to_minutes(interval_str)
                        time_diff_minutes = (current_time - entry_time).total_seconds() / 60.0
                        candles_held = time_diff_minutes / interval_minutes

                        # Minimum 2-candle hold before RSI invalidation can fire
                        if candles_held < 2.0:
                            logger.debug(
                                f"RSI invalidation skipped for {position.symbol}: "
                                f"only {candles_held:.2f} candles elapsed (grace period: 2 candles)"
                            )
                        # Check if RSI still oversold/overbought after M candles
                        # For long positions: RSI should have reverted from oversold (< 30) to neutral (> 40)
                        # For short positions: RSI should have reverted from overbought (> 70) to neutral (< 60)
                        elif position.side == "long":
                            # Long position: RSI should have reverted from oversold
                            if candles_held >= invalidation_rsi_candles and rsi < 40:
                                logger.warning(
                                    f"RSI invalidation for {position.symbol}: "
                                    f"RSI={rsi:.1f} still oversold after {candles_held:.1f} candles "
                                    f"(threshold: {invalidation_rsi_candles} candles)"
                                )
                                await self._force_exit_position(
                                    position=position,
                                    current_price=current_price,
                                    reason="invalidation_rsi",
                                    candles_held=candles_held,
                                    strategy_name=strategy_name,
                                )
                                return
                        elif position.side == "short":
                            # Short position: RSI should have reverted from overbought
                            if candles_held >= invalidation_rsi_candles and rsi > 60:
                                logger.warning(
                                    f"RSI invalidation for {position.symbol}: "
                                    f"RSI={rsi:.1f} still overbought after {candles_held:.1f} candles "
                                    f"(threshold: {invalidation_rsi_candles} candles)"
                                )
                                await self._force_exit_position(
                                    position=position,
                                    current_price=current_price,
                                    reason="invalidation_rsi",
                                    candles_held=candles_held,
                                    strategy_name=strategy_name,
                                )
                                return
                
                if "htf" in strategy_name_lower or ("trend" in strategy_name_lower and "pullback" in strategy_name_lower):
                    # HTF Trend Pullback: Check if HTF regime flips against trade
                    # This requires HTF trend direction indicator (not currently in screener results)
                    # For now, skip HTF invalidation check (can be added later)
                    logger.debug(f"HTF invalidation check not yet implemented for {position.symbol}")
                    pass
                    
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error checking invalidation for {position.symbol}: {e}", exc_info=True)
    
    async def _check_tp1_hit(self, position, current_price: float):
        """
        Check if TP1 has been hit and update Redis tracking.
        
        Args:
            position: Position object
            current_price: Current market price
        """
        try:
            from backend.redis import get_redis_client
            from backend.redis.keys import POSITION_TP1_PRICE_KEY, POSITION_TP1_HIT_KEY
            
            redis_client = get_redis_client()
            
            # Get TP1 price from Redis
            tp1_key = POSITION_TP1_PRICE_KEY.format(symbol=position.symbol)
            tp1_price_str = redis_client.get(tp1_key)
            
            if tp1_price_str is None:
                return  # No TP1 configured for this position
            
            try:
                tp1_price = float(tp1_price_str.decode() if isinstance(tp1_price_str, bytes) else tp1_price_str)
            except (ValueError, AttributeError):
                logger.warning(f"Invalid TP1 price format for {position.symbol}: {tp1_price_str}")
                return
            
            # Check if TP1 was already hit
            tp1_hit_key = POSITION_TP1_HIT_KEY.format(symbol=position.symbol)
            tp1_hit = redis_client.get(tp1_hit_key)
            
            if tp1_hit:
                return  # TP1 already hit, no need to check again
            
            # Check if TP1 is hit based on position side
            tp1_hit_now = False
            if position.side == "long":
                tp1_hit_now = current_price >= tp1_price
            elif position.side == "short":
                tp1_hit_now = current_price <= tp1_price
            
            if tp1_hit_now:
                redis_client.set(tp1_hit_key, "1")
                logger.info(
                    f"TP1 hit: {position.symbol} @ ${current_price:.2f} >= ${tp1_price:.2f}"
                    if position.side == "long"
                    else f"TP1 hit: {position.symbol} @ ${current_price:.2f} <= ${tp1_price:.2f}"
                )
                from backend.config import BREAKEVEN_REQUIRES_TP1
                if BREAKEVEN_REQUIRES_TP1:
                    await self._activate_breakeven_guard(
                        position, current_price, triggered_by="tp1"
                    )
        except Exception as e:
            logger.error(f"Error checking TP1 hit for {position.symbol}: {e}", exc_info=True)

    def _breakeven_reference_price(self, position) -> float:
        """Entry reference for breakeven (scout price when Scout+Soldier)."""
        if position.scout_entry_price:
            return position.scout_entry_price
        return position.entry_price

    def _profit_pct_vs_reference(self, position, current_price: float) -> float:
        ref = self._breakeven_reference_price(position)
        if position.side == "short":
            return ((ref - current_price) / ref) * 100.0
        return ((current_price - ref) / ref) * 100.0

    def _is_tp1_hit(self, symbol: str) -> bool:
        from backend.redis import get_redis_client
        from backend.redis.keys import POSITION_TP1_HIT_KEY

        redis_client = get_redis_client()
        tp1_hit_key = POSITION_TP1_HIT_KEY.format(symbol=symbol)
        return bool(redis_client.get(tp1_hit_key))

    async def _activate_breakeven_guard(
        self,
        position,
        current_price: float,
        *,
        triggered_by: str = "profit_pct",
    ):
        """
        Move stop to entry+fees. Called on TP1 hit (default) or legacy +profit% trigger.
        """
        if position.breakeven_guard_active:
            return

        from backend.config import KRAKEN_FEE_PCT

        breakeven_reference_price = self._breakeven_reference_price(position)
        profit_pct = self._profit_pct_vs_reference(position, current_price)

        fee_pct = KRAKEN_FEE_PCT / 100.0
        fees_per_unit = breakeven_reference_price * fee_pct
        if position.side == "short":
            breakeven_price = breakeven_reference_price - fees_per_unit
        else:
            breakeven_price = breakeven_reference_price + fees_per_unit

        logger.info(
            f"Breakeven guard activated ({triggered_by}): {position.symbol} "
            f"stop moved to ${breakeven_price:.3f} "
            f"(entry: ${breakeven_reference_price:.2f} + fees: ${fees_per_unit:.4f}, "
            f"profit: {profit_pct:.2f}%)"
        )

        position.breakeven_guard_active = True
        position.breakeven_stop_price = breakeven_price

        effective_stop_price = breakeven_price
        if position.trailing_stop_active and position.trailing_stop_price is not None:
            if position.side == "long":
                effective_stop_price = max(breakeven_price, position.trailing_stop_price)
            else:
                effective_stop_price = min(breakeven_price, position.trailing_stop_price)
            logger.info(
                f"Breakeven guard: Using effective stop ${effective_stop_price:.3f} "
                f"(breakeven: ${breakeven_price:.3f}, trailing: ${position.trailing_stop_price:.3f})"
            )

        await self._update_kraken_stop_loss(position, effective_stop_price)
        position.stop_loss_price = effective_stop_price

        from backend.redis import get_redis_client
        from backend.redis.keys import POSITION_KEY

        redis_client = get_redis_client()
        key = POSITION_KEY.format(symbol=position.symbol)
        redis_client.hset(key, mapping=position.to_dict())

        from backend.api.routes.events import log_activity

        log_activity(
            activity_type="BREAKEVEN_GUARD_ACTIVATED",
            message=(
                f"Breakeven guard activated ({triggered_by}): {position.symbol} - "
                f"stop moved to ${breakeven_price:.3f} "
                f"(entry: ${breakeven_reference_price:.2f} + fees, profit: {profit_pct:.2f}%)"
            ),
            details={
                "symbol": position.symbol,
                "breakeven_stop_price": breakeven_price,
                "entry_price": breakeven_reference_price,
                "fees_per_unit": fees_per_unit,
                "profit_pct": profit_pct,
                "triggered_by": triggered_by,
                "scout_entry_price": position.scout_entry_price,
                "effective_stop_price": effective_stop_price,
                "trailing_stop_active": position.trailing_stop_active,
                "strategy": position.opened_by_strategy_id,
            },
        )

    async def _check_breakeven_guard(self, position, current_price: float):
        """
        Activate breakeven guard after TP1 (default) or at +profit% (legacy mode).

        When BREAKEVEN_REQUIRES_TP1 is true, breakeven is activated from _check_tp1_hit
        only; this method skips the profit-percent trigger.
        """
        if position.breakeven_guard_active:
            return

        try:
            from backend.config import BREAKEVEN_GUARD_TRIGGER_PCT, BREAKEVEN_REQUIRES_TP1

            if BREAKEVEN_REQUIRES_TP1:
                if not self._is_tp1_hit(position.symbol):
                    logger.debug(
                        f"Breakeven guard check for {position.symbol}: "
                        "TP1 not hit yet (BREAKEVEN_REQUIRES_TP1)"
                    )
                    return
                # TP1 hit but guard not active (e.g. flag set before deploy)
                await self._activate_breakeven_guard(
                    position, current_price, triggered_by="tp1"
                )
                return

            profit_pct = self._profit_pct_vs_reference(position, current_price)
            if profit_pct < BREAKEVEN_GUARD_TRIGGER_PCT:
                logger.debug(
                    f"Breakeven guard check for {position.symbol}: "
                    f"profit={profit_pct:.2f}% < {BREAKEVEN_GUARD_TRIGGER_PCT}% (not triggered)"
                )
                return

            await self._activate_breakeven_guard(
                position, current_price, triggered_by="profit_pct"
            )

        except Exception as e:
            logger.error(f"Error checking breakeven guard for {position.symbol}: {e}", exc_info=True)
    
    async def _check_48h_opportunity_filter(self, position, current_price: float):
        """
        Check if position should be force-exited due to 48-hour opportunity filter.
        
        Positions held > 48 hours without TP1 hit are auto-closed.
        
        Args:
            position: Position object
            current_price: Current market price
        """
        if not position.opened_by_strategy_id:
            return  # Only check strategy-owned positions
        
        try:
            from backend.config import OPPORTUNITY_FILTER_HOURS
            from backend.redis import get_redis_client
            from backend.redis.keys import POSITION_TP1_HIT_KEY
            
            # Calculate hours since entry
            entry_time = datetime.fromisoformat(position.entry_time.replace('Z', '+00:00'))
            current_time = datetime.now(timezone.utc)
            hours_held = (current_time - entry_time).total_seconds() / 3600.0
            
            if hours_held < OPPORTUNITY_FILTER_HOURS:
                return  # Not yet 48 hours
            
            # Check if TP1 was hit
            redis_client = get_redis_client()
            tp1_hit_key = POSITION_TP1_HIT_KEY.format(symbol=position.symbol)
            tp1_hit = redis_client.get(tp1_hit_key)
            
            if tp1_hit:
                logger.debug(
                    f"48-hour filter check for {position.symbol}: "
                    f"held {hours_held:.1f}h, TP1 hit (OK)"
                )
                return  # TP1 was hit, position is OK
            
            # TP1 not hit and held > 48 hours - force exit
            logger.warning(
                f"48-hour opportunity filter: Closing {position.symbol} "
                f"(held {hours_held:.1f}h, TP1 not hit)"
            )
            
            # Get strategy name for logging
            from backend.db import get_session
            from backend.db.models import Strategy
            import uuid as uuid_module
            
            session = get_session()
            try:
                strategy_id = position.opened_by_strategy_id
                try:
                    strategy_uuid = uuid_module.UUID(strategy_id)
                    strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                except ValueError:
                    strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
                
                strategy_name = strategy.name if strategy else strategy_id
            finally:
                session.close()
            
            await self._force_exit_position(
                position=position,
                current_price=current_price,
                reason="opportunity_filter_48h",
                candles_held=hours_held / 24.0,  # Approximate candles (24h = 1 day)
                strategy_name=strategy_name,
            )
            
        except Exception as e:
            logger.error(f"Error checking 48h opportunity filter for {position.symbol}: {e}", exc_info=True)
    
    async def _check_atr_trailing_stop(self, position, current_price: float):
        """
        Check and manage ATR trailing stop for positions.
        
        Activation: When profit >= 3.0%, activate trailing stop at current_price - (2.0 × ATR)
        Update: Trailing stop only moves UP (never down) as price increases
        Exit: When current_price <= trailing_stop_price, execute forced exit
        
        Args:
            position: Position object
            current_price: Current market price
        """
        if not position.opened_by_strategy_id:
            return  # Only check strategy-owned positions
        
        try:
            # Get ATR from screener results or position metadata
            atr = await self._get_atr_for_position(position)
            
            if atr is None or atr <= 0:
                logger.debug(f"ATR unavailable for {position.symbol}, skipping trailing stop check")
                return
            
            # Get environment variables
            trigger_pct = float(os.getenv("ATR_TRAILING_STOP_TRIGGER_PCT", "3.0"))
            multiplier = float(os.getenv("ATR_TRAILING_STOP_MULTIPLIER", "2.0"))
            
            # Calculate profit percentage
            profit_pct = ((current_price - position.entry_price) / position.entry_price) * 100.0
            
            if not position.trailing_stop_active:
                # Check if we should activate trailing stop
                if profit_pct >= trigger_pct:
                    # Activate trailing stop
                    trailing_stop_price = current_price - (multiplier * atr)
                    
                    logger.info(
                        f"ATR trailing stop activated: {position.symbol} @ ${current_price:.2f}, "
                        f"trailing stop: ${trailing_stop_price:.2f} "
                        f"(profit: {profit_pct:.2f}%, ATR: {atr:.4f})"
                    )
                    
                    # Update position
                    position.trailing_stop_active = True
                    position.trailing_stop_price = trailing_stop_price
                    
                    # Determine effective stop: use wider stop (breakeven or trailing)
                    effective_stop_price = trailing_stop_price
                    if position.breakeven_guard_active and position.breakeven_stop_price is not None:
                        if position.side == "long":
                            # Long: use the higher stop (more protective)
                            effective_stop_price = max(trailing_stop_price, position.breakeven_stop_price)
                        else:
                            # Short: use the lower stop (more protective)
                            effective_stop_price = min(trailing_stop_price, position.breakeven_stop_price)
                        
                        logger.info(
                            f"Trailing stop activation: Using effective stop ${effective_stop_price:.3f} "
                            f"(trailing: ${trailing_stop_price:.3f}, breakeven: ${position.breakeven_stop_price:.3f})"
                        )
                    
                    # Update Kraken stop-loss order
                    await self._update_kraken_stop_loss(position, effective_stop_price)
                    position.stop_loss_price = effective_stop_price
                    
                    # Save to Redis
                    tracker = self._get_tracker()
                    from backend.redis import get_redis_client
                    from backend.redis.keys import POSITION_KEY
                    redis_client = get_redis_client()
                    key = POSITION_KEY.format(symbol=position.symbol)
                    redis_client.hset(key, mapping=position.to_dict())
            else:
                # Trailing stop is active - check for update or exit
                # Check if price dropped to trailing stop (exit condition)
                if current_price <= position.trailing_stop_price:
                    _be_str = f"${position.breakeven_stop_price:.2f}" if position.breakeven_stop_price else "N/A"
                    logger.info(
                        f"ATR trailing stop triggered: {position.symbol} @ ${current_price:.2f} <= ${position.trailing_stop_price:.2f} "
                        f"(trailing: ${position.trailing_stop_price:.2f}, breakeven: {_be_str})"
                    )
                    
                    # Get strategy name for logging
                    from backend.db import get_session
                    from backend.db.models import Strategy
                    import uuid as uuid_module
                    
                    session = get_session()
                    try:
                        strategy_id = position.opened_by_strategy_id
                        try:
                            strategy_uuid = uuid_module.UUID(strategy_id)
                            strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                        except ValueError:
                            strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
                        
                        strategy_name = strategy.name if strategy else strategy_id
                    finally:
                        session.close()
                    
                    # Calculate candles held (approximate)
                    entry_time = datetime.fromisoformat(position.entry_time.replace('Z', '+00:00'))
                    current_time = datetime.now(timezone.utc)
                    time_diff_minutes = (current_time - entry_time).total_seconds() / 60.0
                    candles_held = time_diff_minutes / 5.0  # Approximate 5m candles
                    
                    # Execute forced exit
                    await self._force_exit_position(
                        position=position,
                        current_price=current_price,
                        reason="atr_trailing_stop",
                        candles_held=candles_held,
                        strategy_name=strategy_name,
                    )
                    return
                
                # Calculate new trailing stop price
                new_trailing_stop = current_price - (multiplier * atr)
                
                # Only update if new stop > current stop (trailing stop only moves UP)
                if new_trailing_stop > position.trailing_stop_price:
                    old_stop = position.trailing_stop_price
                    position.trailing_stop_price = new_trailing_stop
                    
                    # Determine effective stop: use wider stop (breakeven or trailing)
                    effective_stop_price = new_trailing_stop
                    if position.breakeven_guard_active and position.breakeven_stop_price is not None:
                        if position.side == "long":
                            # Long: use the higher stop (more protective)
                            effective_stop_price = max(new_trailing_stop, position.breakeven_stop_price)
                        else:
                            # Short: use the lower stop (more protective)
                            effective_stop_price = min(new_trailing_stop, position.breakeven_stop_price)
                        
                        logger.info(
                            f"Trailing stop updated: Using effective stop ${effective_stop_price:.3f} "
                            f"(trailing: ${new_trailing_stop:.3f}, breakeven: ${position.breakeven_stop_price:.3f})"
                        )
                    else:
                        logger.info(
                            f"Trailing stop updated: {position.symbol} stop moved to ${new_trailing_stop:.2f} "
                            f"(was ${old_stop:.2f})"
                        )
                    
                    # Update Kraken stop-loss order
                    await self._update_kraken_stop_loss(position, effective_stop_price)
                    position.stop_loss_price = effective_stop_price
                    
                    # Save to Redis
                    tracker = self._get_tracker()
                    from backend.redis import get_redis_client
                    from backend.redis.keys import POSITION_KEY
                    redis_client = get_redis_client()
                    key = POSITION_KEY.format(symbol=position.symbol)
                    redis_client.hset(key, mapping=position.to_dict())
                    
        except Exception as e:
            logger.error(f"Error checking ATR trailing stop for {position.symbol}: {e}", exc_info=True)
    
    async def _get_atr_for_position(self, position) -> Optional[float]:
        """
        Get ATR value for a position from screener results or metadata.
        
        Args:
            position: Position object
            
        Returns:
            ATR value or None if unavailable
        """
        try:
            from backend.redis import get_redis_client
            from backend.redis.keys import SCREENER_STRATEGY_RESULTS_KEY
            
            # Try to get ATR from cached screener results
            client = get_redis_client()
            screener_key = SCREENER_STRATEGY_RESULTS_KEY.format(strategy_id=position.opened_by_strategy_id)
            screener_data = client.get(screener_key)
            
            if screener_data:
                screener_results = json.loads(screener_data)
                results = screener_results.get("results", [])
                
                # Find indicators for this symbol
                for result in results:
                    if result.get("symbol") == position.symbol:
                        indicators = result.get("indicators", {})
                        atr = indicators.get("atr")
                        if atr is not None and atr > 0:
                            return float(atr)
            
            # ATR not found in screener results
            logger.debug(f"ATR not found in screener results for {position.symbol}")
            return None
            
        except Exception as e:
            logger.debug(f"Error retrieving ATR for {position.symbol}: {e}")
            return None
    
    async def _update_kraken_stop_loss(self, position, new_stop_price: float):
        """
        Update stop-loss order on Kraken to new price.
        
        Args:
            position: Position object
            new_stop_price: New stop-loss price
        """
        try:
            if not position.stop_loss_order_id:
                logger.warning(f"No stop-loss order ID for {position.symbol}, cannot update")
                return

            from backend.execution import kraken_cli as _kraken_cli

            await _kraken_cli.cancel_order(position.stop_loss_order_id)
            logger.info(f"Cancelled old stop-loss order {position.stop_loss_order_id}")

            stop_price_rounded = round(new_stop_price, 3)
            kraken_pair = _kraken_cli.symbol_to_cli_pair(position.symbol)

            sl_result = await _kraken_cli.place_order(
                pair=kraken_pair,
                side="sell",
                quantity=float(position.quantity),
                order_type="stop-loss",
                price=stop_price_rounded,
            )
            txid_raw = sl_result.get("txid", "")
            new_txid = txid_raw[0] if isinstance(txid_raw, list) else str(txid_raw)

            position.stop_loss_order_id = new_txid
            position.stop_loss_price = stop_price_rounded

            logger.info(
                f"Updated stop-loss to ${stop_price_rounded:.3f} (new txid: {new_txid})"
            )

        except Exception as e:
            logger.error(f"Failed to update Kraken stop-loss for {position.symbol}: {e}", exc_info=True)
            # Continue even if stop-loss update fails