"""Strategy metrics tracking for trade performance."""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from backend.redis import get_redis_client
from backend.redis.keys import (
    METRICS_OPEN_TRADES_KEY,
    METRICS_STRATEGY_STATS_KEY,
    STRATEGY_R_MULTIPLES_KEY,
    STRATEGY_R_MULTIPLES_MAX,
    STRATEGY_PEAK_EQUITY_KEY,
    STRATEGY_CURRENT_EQUITY_KEY,
    STRATEGY_DRAWDOWN_KEY,
    STRATEGY_DRAWDOWN_HISTORY_KEY,
    STRATEGY_DRAWDOWN_HISTORY_MAX,
    STRATEGY_DISABLE_REASON_KEY,
)

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Record of an open trade for metrics tracking."""

    trade_id: str
    strategy_id: str
    symbol: str
    side: str  # "buy" or "sell"
    entry_price: float
    quantity: float
    opened_at: str

    def to_json(self) -> str:
        """Serialize to JSON for Redis storage."""
        return json.dumps({
            "trade_id": self.trade_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "opened_at": self.opened_at,
        })

    @classmethod
    def from_json(cls, data: str) -> "TradeRecord":
        """Deserialize from JSON."""
        obj = json.loads(data)
        return cls(
            trade_id=obj["trade_id"],
            strategy_id=obj["strategy_id"],
            symbol=obj["symbol"],
            side=obj["side"],
            entry_price=float(obj["entry_price"]),
            quantity=float(obj["quantity"]),
            opened_at=obj["opened_at"],
        )


@dataclass
class StrategyStats:
    """Aggregated statistics for a single strategy."""

    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    open_count: int = 0

    @property
    def accuracy_pct(self) -> float:
        """Calculate win rate as percentage."""
        total_closed = self.wins + self.losses
        if total_closed == 0:
            return 0.0
        return (self.wins / total_closed) * 100.0


class StrategyMetrics:
    """
    Tracks trade performance metrics per strategy.

    Stores open trades and aggregated stats in Redis for persistence.
    """

    def __init__(self):
        """Initialize the metrics tracker."""
        self._redis = get_redis_client()

    def _get_strategy_key(self, strategy_id: str) -> str:
        """Get Redis key for strategy stats."""
        return METRICS_STRATEGY_STATS_KEY.format(strategy_id=strategy_id)

    def record_trade(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        trade_id: Optional[str] = None,
    ) -> str:
        """
        Record a new trade opening.

        Args:
            strategy_id: ID of the strategy that opened the trade
            symbol: Trading pair symbol (e.g., "ETH/USD")
            side: Trade direction ("buy" or "sell")
            entry_price: Entry price
            quantity: Trade quantity
            trade_id: Optional trade ID (generated if not provided)

        Returns:
            The trade_id for later reference when closing
        """
        if trade_id is None:
            trade_id = str(uuid.uuid4())

        record = TradeRecord(
            trade_id=trade_id,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            opened_at=datetime.now(timezone.utc).isoformat(),
        )

        # Store in open trades hash
        self._redis.hset(METRICS_OPEN_TRADES_KEY, trade_id, record.to_json())

        # Increment open count for strategy
        strategy_key = self._get_strategy_key(strategy_id)
        self._redis.hincrby(strategy_key, "open_count", 1)

        logger.info(
            f"Recorded trade opening: {trade_id} strategy={strategy_id} "
            f"{symbol} {side} qty={quantity} @ {entry_price}"
        )

        return trade_id

    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: Optional[str] = None,
        stop_loss_price: Optional[float] = None,
    ) -> Optional[float]:
        """
        Close a trade and calculate PNL and R-multiple.

        Args:
            trade_id: ID of the trade to close
            exit_price: Exit/closing price
            exit_reason: Reason for exit ("stop_loss", "take_profit", "time_stop", "invalidation_vwap", etc.)
            stop_loss_price: Stop-loss price used for R-multiple calculation (optional)

        Returns:
            Realized PNL for the trade, or None if trade not found
        """
        # Get open trade record
        trade_data = self._redis.hget(METRICS_OPEN_TRADES_KEY, trade_id)
        if trade_data is None:
            logger.warning(f"Trade not found for closing: {trade_id}")
            return None

        record = TradeRecord.from_json(trade_data)

        # Calculate PNL based on side
        # For buy (long): profit when exit > entry
        # For sell (short): profit when exit < entry
        if record.side == "buy":
            pnl = (exit_price - record.entry_price) * record.quantity
        else:
            pnl = (record.entry_price - exit_price) * record.quantity

        # Calculate R-multiple
        r_multiple = self._calculate_r_multiple(
            entry_price=record.entry_price,
            exit_price=exit_price,
            stop_loss_price=stop_loss_price,
            side=record.side,
        )

        # Determine win/loss
        is_win = pnl > 0

        # Update strategy stats
        strategy_key = self._get_strategy_key(record.strategy_id)
        pipe = self._redis.pipeline()

        if is_win:
            pipe.hincrby(strategy_key, "wins", 1)
        else:
            pipe.hincrby(strategy_key, "losses", 1)

        pipe.hincrbyfloat(strategy_key, "total_pnl", pnl)
        pipe.hincrby(strategy_key, "open_count", -1)

        # Remove from open trades
        pipe.hdel(METRICS_OPEN_TRADES_KEY, trade_id)

        pipe.execute()

        # Record R-multiple and exit reason
        self._record_trade_exit(
            strategy_id=record.strategy_id,
            r_multiple=r_multiple,
            exit_price=exit_price,
            entry_price=record.entry_price,
            exit_reason=exit_reason or "unknown",
        )

        logger.info(
            f"Closed trade: {trade_id} strategy={record.strategy_id} "
            f"pnl={pnl:.4f} R={r_multiple:.2f} {'WIN' if is_win else 'LOSS'} "
            f"reason={exit_reason or 'unknown'}"
        )

        try:
            from backend.analytics.store import finalize_trade

            finalize_trade(
                symbol=record.symbol,
                strategy=record.strategy_id,
                exit_price=exit_price,
                pnl_usd=pnl,
                r_multiple=r_multiple,
                is_win=is_win,
                exit_reason=exit_reason or "unknown",
            )
        except Exception as e:
            logger.debug(f"Trade analytics finalize failed: {e}")

        # Check if strategy should be auto-disabled due to drawdown
        try:
            self.check_strategy_drawdown(record.strategy_id)
        except Exception as e:
            logger.debug(f"Failed to check strategy drawdown after trade close: {e}")

        return pnl
    
    def _calculate_r_multiple(
        self,
        entry_price: float,
        exit_price: float,
        stop_loss_price: Optional[float],
        side: str,
    ) -> float:
        """
        Calculate R-multiple for a trade.
        
        R-multiple = (exit_price - entry_price) / (entry_price - stop_loss_price)
        For long: (exit - entry) / (entry - stop)
        For short: (entry - exit) / (stop - entry) [inverted]
        
        If stop_loss_price is missing, use default 5% stop.
        """
        if stop_loss_price is None:
            # Use default 5% stop
            if side == "buy":
                stop_loss_price = entry_price * 0.95  # 5% below entry
            else:
                stop_loss_price = entry_price * 1.05  # 5% above entry
        
        # Calculate risk distance
        if side == "buy":
            risk_distance = abs(entry_price - stop_loss_price)
            profit_distance = exit_price - entry_price
        else:
            risk_distance = abs(stop_loss_price - entry_price)
            profit_distance = entry_price - exit_price
        
        # Avoid division by zero
        if risk_distance == 0:
            logger.warning(f"Risk distance is zero, using default R-multiple calculation")
            risk_distance = entry_price * 0.05  # 5% default
        
        r_multiple = profit_distance / risk_distance
        
        return round(r_multiple, 2)
    
    def _record_trade_exit(
        self,
        strategy_id: str,
        r_multiple: float,
        exit_price: float,
        entry_price: float,
        exit_reason: str,
    ):
        """
        Record trade exit with R-multiple and exit reason.
        
        Stores in rolling window (last N trades).
        """
        try:
            key = STRATEGY_R_MULTIPLES_KEY.format(strategy_id=strategy_id)
            
            exit_record = {
                "r_multiple": r_multiple,
                "exit_price": exit_price,
                "entry_price": entry_price,
                "exit_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "exit_reason": exit_reason,
            }
            
            # LPUSH to add to front of list (newest first)
            self._redis.lpush(key, json.dumps(exit_record))
            
            # LTRIM to keep only last N entries
            self._redis.ltrim(key, 0, STRATEGY_R_MULTIPLES_MAX - 1)
            
            # Set TTL (7 days)
            self._redis.expire(key, 604800)
            
        except Exception as e:
            logger.warning(f"Failed to record trade exit R-multiple: {e}")
    
    def get_r_multiples(self, strategy_id: str, limit: int = 20) -> Dict:
        """
        Get R-multiples for a strategy.
        
        Args:
            strategy_id: Strategy identifier
            limit: Maximum number of trades to return
            
        Returns:
            Dict with:
            - r_multiples: List of R-multiple records
            - average_r: Average R-multiple
            - win_rate: Percentage of winning trades (R > 0)
            - avg_win_r: Average R for winning trades
            - avg_loss_r: Average R for losing trades
        """
        try:
            key = STRATEGY_R_MULTIPLES_KEY.format(strategy_id=strategy_id)
            records_data = self._redis.lrange(key, 0, limit - 1)
            
            r_multiples = []
            for record_json in records_data:
                try:
                    record = json.loads(record_json)
                    r_multiples.append(record)
                except (json.JSONDecodeError, KeyError):
                    continue
            
            if not r_multiples:
                return {
                    "r_multiples": [],
                    "average_r": 0.0,
                    "win_rate": 0.0,
                    "avg_win_r": 0.0,
                    "avg_loss_r": 0.0,
                }
            
            # Calculate statistics
            r_values = [r["r_multiple"] for r in r_multiples]
            average_r = sum(r_values) / len(r_values) if r_values else 0.0
            
            wins = [r for r in r_values if r > 0]
            losses = [r for r in r_values if r <= 0]
            
            win_rate = (len(wins) / len(r_values) * 100.0) if r_values else 0.0
            avg_win_r = sum(wins) / len(wins) if wins else 0.0
            avg_loss_r = sum(losses) / len(losses) if losses else 0.0
            
            return {
                "r_multiples": r_multiples,
                "average_r": round(average_r, 2),
                "win_rate": round(win_rate, 2),
                "avg_win_r": round(avg_win_r, 2),
                "avg_loss_r": round(avg_loss_r, 2),
            }
            
        except Exception as e:
            logger.error(f"Failed to get R-multiples for {strategy_id}: {e}")
            return {
                "r_multiples": [],
                "average_r": 0.0,
                "win_rate": 0.0,
                "avg_win_r": 0.0,
                "avg_loss_r": 0.0,
            }
    
    
    def get_strategy_drawdown(self, strategy_id: str) -> Dict:
        """Get current drawdown for a strategy."""
        try:
            peak_key = STRATEGY_PEAK_EQUITY_KEY.format(strategy_id=strategy_id)
            current_key = STRATEGY_CURRENT_EQUITY_KEY.format(strategy_id=strategy_id)
            drawdown_key = STRATEGY_DRAWDOWN_KEY.format(strategy_id=strategy_id)
            
            peak_equity = self._redis.get(peak_key)
            current_equity = self._redis.get(current_key)
            drawdown_data = self._redis.get(drawdown_key)
            
            peak_equity = float(peak_equity) if peak_equity else 0.0
            current_equity = float(current_equity) if current_equity else 0.0
            
            if peak_equity == 0:
                drawdown_pct = 0.0
            else:
                drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100.0
            
            drawdown_duration = 0
            if drawdown_data:
                try:
                    data = json.loads(drawdown_data)
                    peak_time_str = data.get("peak_time")
                    if peak_time_str:
                        peak_time = datetime.fromisoformat(peak_time_str.replace('Z', '+00:00'))
                        duration = (datetime.now(timezone.utc) - peak_time).total_seconds()
                        drawdown_duration = int(duration)
                except Exception:
                    pass
            
            return {
                "drawdown_pct": round(drawdown_pct, 2),
                "peak_equity": round(peak_equity, 2),
                "current_equity": round(current_equity, 2),
                "drawdown_duration": drawdown_duration,
            }
        except Exception as e:
            logger.error(f"Failed to get strategy drawdown for {strategy_id}: {e}")
            return {
                "drawdown_pct": 0.0,
                "peak_equity": 0.0,
                "current_equity": 0.0,
                "drawdown_duration": 0,
            }
    
    def check_strategy_drawdown(self, strategy_id: str) -> bool:
        """Suspend strategy when cumulative R loss breaches threshold (sticky until cleared)."""
        try:
            from backend.db import get_session
            from backend.db.models import Strategy
            from backend.api.routes.events import log_activity
            from backend.supervisor.store import (
                canonical_name,
                is_drawdown_suspended,
                set_drawdown_suspended,
                write_cumulative_r_loss,
                write_verdict,
            )
            from datetime import datetime, timezone
            import uuid as uuid_module

            session = get_session()
            try:
                try:
                    strategy_uuid = uuid_module.UUID(strategy_id)
                    strategy = session.query(Strategy).filter(Strategy.id == strategy_uuid).first()
                except ValueError:
                    strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()

                if not strategy or strategy.status != "active":
                    return False

                config = strategy.config or {}
                max_drawdown_r = config.get("max_drawdown_r") or config.get("parameters", {}).get("max_drawdown_r", -5.0)
                drawdown_window = config.get("drawdown_window_trades") or config.get("parameters", {}).get("drawdown_window_trades", 20)

                r_data = self.get_r_multiples(strategy_id, limit=drawdown_window)
                r_multiples = r_data.get("r_multiples", [])

                if len(r_multiples) < 5:
                    return False

                negative_r = [r["r_multiple"] for r in r_multiples if r["r_multiple"] < 0]
                cumulative_r_loss = sum(negative_r)
                canon = canonical_name(strategy.name)
                write_cumulative_r_loss(canon, cumulative_r_loss)

                if cumulative_r_loss <= max_drawdown_r:
                    already_suspended = is_drawdown_suspended(canon)

                    if not already_suspended:
                        set_drawdown_suspended(
                            canon,
                            reason=f"cumulative_r_loss={cumulative_r_loss:.2f}",
                        )
                        write_verdict(
                            canon,
                            {
                                "strategy": canon,
                                "status": "SUSPENDED",
                                "size_factor": 0.0,
                                "reason": "drawdown_breach",
                                "cumulative_r_loss": cumulative_r_loss,
                                "threshold": max_drawdown_r,
                                "last_evaluated": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        log_activity(
                            activity_type="RISK_WARNING",
                            message=(
                                f"Strategy SUSPENDED (drawdown): {strategy.name} — "
                                f"cumulative R loss {cumulative_r_loss:.2f} <= "
                                f"threshold {max_drawdown_r:.2f}; new entries halted "
                                f"until manual re-enable or backtest ACTIVE"
                            ),
                            details={
                                "strategy": strategy.name,
                                "strategy_id": strategy_id,
                                "canonical": canon,
                                "reason": "drawdown_threshold_exceeded",
                                "cumulative_r_loss": cumulative_r_loss,
                                "threshold": max_drawdown_r,
                                "trades_in_window": len(r_multiples),
                            },
                        )
                        logger.warning(
                            "Strategy %s SUSPENDED: cumulative R loss %.2f <= "
                            "threshold %.2f",
                            strategy.name,
                            cumulative_r_loss,
                            max_drawdown_r,
                        )
                    return True
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Failed to check strategy drawdown for {strategy_id}: {e}", exc_info=True)
        return False

    def get_strategy_metrics(self, strategy_id: str) -> Dict:
        """
        Get metrics for a single strategy.

        Args:
            strategy_id: Strategy identifier

        Returns:
            Dict with accuracy_pct, total_pnl, win_count, loss_count, open_count
        """
        strategy_key = self._get_strategy_key(strategy_id)
        data = self._redis.hgetall(strategy_key)

        wins = int(data.get("wins", 0))
        losses = int(data.get("losses", 0))
        total_pnl = float(data.get("total_pnl", 0.0))
        open_count = int(data.get("open_count", 0))

        # Ensure open_count doesn't go negative
        open_count = max(0, open_count)

        # Calculate accuracy
        total_closed = wins + losses
        accuracy_pct = (wins / total_closed * 100.0) if total_closed > 0 else 0.0

        return {
            "accuracy_pct": round(accuracy_pct, 2),
            "total_pnl": round(total_pnl, 4),
            "win_count": wins,
            "loss_count": losses,
            "open_count": open_count,
        }

    def get_all_metrics(self) -> Dict:
        """
        Get metrics for all strategies.

        Returns:
            Dict with strategies (keyed by strategy_id), total_pnl, overall_accuracy_pct
        """
        # Scan for all strategy keys
        strategies = {}
        total_wins = 0
        total_losses = 0
        total_pnl = 0.0

        cursor = 0
        pattern = "metrics:strategy:*"

        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=100)

            for key in keys:
                # Extract strategy_id from key
                # Key format: metrics:strategy:{strategy_id}
                parts = key.split(":")
                if len(parts) >= 3:
                    strategy_id = ":".join(parts[2:])  # Handle strategy_ids with colons
                    metrics = self.get_strategy_metrics(strategy_id)
                    strategies[strategy_id] = metrics

                    total_wins += metrics["win_count"]
                    total_losses += metrics["loss_count"]
                    total_pnl += metrics["total_pnl"]

            if cursor == 0:
                break

        # Calculate overall accuracy
        total_closed = total_wins + total_losses
        overall_accuracy = (total_wins / total_closed * 100.0) if total_closed > 0 else 0.0

        return {
            "strategies": strategies,
            "total_pnl": round(total_pnl, 4),
            "overall_accuracy_pct": round(overall_accuracy, 2),
        }

    def get_open_trades_for_strategy(self, strategy_id: str) -> list:
        """
        Get all open trades for a strategy.

        Args:
            strategy_id: Strategy identifier

        Returns:
            List of TradeRecord objects
        """
        trades = []
        all_trades = self._redis.hgetall(METRICS_OPEN_TRADES_KEY)

        for trade_id, trade_data in all_trades.items():
            record = TradeRecord.from_json(trade_data)
            if record.strategy_id == strategy_id:
                trades.append(record)

        return trades


# Global metrics instance
_metrics: Optional[StrategyMetrics] = None


def get_strategy_metrics() -> StrategyMetrics:
    """Get the global strategy metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = StrategyMetrics()
    return _metrics


def delete_keys_by_pattern(redis, pattern: str) -> int:
    """
    Scan Redis for keys matching pattern and delete them in batches.

    Returns:
        Total number of keys removed (sum of redis.delete return values).
    """
    batch: list[str] = []
    total_deleted = 0
    batch_size = 500

    for raw in redis.scan_iter(match=pattern):
        key = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        batch.append(key)
        if len(batch) >= batch_size:
            total_deleted += int(redis.delete(*batch))
            batch.clear()

    if batch:
        total_deleted += int(redis.delete(*batch))

    return total_deleted


def clear_all_strategy_metrics_and_r_multiples(redis) -> int:
    """Delete metrics:strategy:*, strategy:r_multiples:*, and open-trades hash."""
    n = delete_keys_by_pattern(redis, "metrics:strategy:*")
    n += delete_keys_by_pattern(redis, "strategy:r_multiples:*")
    n += int(redis.delete(METRICS_OPEN_TRADES_KEY))
    if n:
        logger.info(f"clear_all_strategy_metrics_and_r_multiples: deleted {n} key(s)")
    return n


def reset_strategy_metrics_for_ids(strategy_ids: list) -> int:
    """
    Clear all performance metrics Redis keys for the given strategy IDs or names.

    Deletes legacy stats, R-multiples, drawdown data, disable reason, and
    performance-monitor data so the widget starts fresh.

    Args:
        strategy_ids: List of strategy UUIDs and/or names to reset.

    Returns:
        Number of Redis keys deleted.
    """
    from backend.performance.monitor import PERFORMANCE_KEY_PATTERN

    redis = get_redis_client()
    keys_to_delete = []
    for sid in set(strategy_ids):
        keys_to_delete.extend([
            METRICS_STRATEGY_STATS_KEY.format(strategy_id=sid),
            STRATEGY_R_MULTIPLES_KEY.format(strategy_id=sid),
            STRATEGY_DRAWDOWN_KEY.format(strategy_id=sid),
            STRATEGY_DISABLE_REASON_KEY.format(strategy_id=sid),
            STRATEGY_PEAK_EQUITY_KEY.format(strategy_id=sid),
            STRATEGY_CURRENT_EQUITY_KEY.format(strategy_id=sid),
            PERFORMANCE_KEY_PATTERN.format(strategy_id=sid),
        ])

    if not keys_to_delete:
        return 0

    deleted = redis.delete(*keys_to_delete)
    logger.info(f"Reset metrics: deleted {deleted} keys for {len(set(strategy_ids))} strategy ID(s)")
    return deleted
