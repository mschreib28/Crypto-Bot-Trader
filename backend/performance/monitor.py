"""Real-time performance monitoring service.

Tracks strategy performance metrics continuously and updates them after each trade.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from backend.performance.models import StrategyPerformance
from backend.redis import get_redis_client
from backend.redis.keys import POSITION_KEY

logger = logging.getLogger(__name__)

# Redis key pattern for performance metrics
PERFORMANCE_KEY_PATTERN = "performance:strategy:{strategy_id}"
PERFORMANCE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Update interval
UPDATE_INTERVAL_SECONDS = 5 * 60  # 5 minutes


class PerformanceMonitor:
    """Monitors strategy performance in real-time."""
    
    def __init__(self, update_interval: float = UPDATE_INTERVAL_SECONDS):
        """
        Initialize performance monitor.
        
        Args:
            update_interval: Seconds between metric updates (default: 5 minutes)
        """
        self.update_interval = update_interval
        self._redis = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        logger.info(f"PerformanceMonitor initialized: update_interval={update_interval}s")
    
    def _get_redis(self):
        """Get Redis client (lazy initialization)."""
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis
    
    def _get_performance_key(self, strategy_id: str) -> str:
        """Get Redis key for strategy performance metrics."""
        return PERFORMANCE_KEY_PATTERN.format(strategy_id=strategy_id)
    
    def update_trade_outcome(
        self,
        strategy_id: str,
        symbol: str,
        pnl: float,
        entry_time: datetime,
    ) -> None:
        """
        Update performance metrics after a trade outcome.
        
        Called immediately after trade execution or position update.
        
        Args:
            strategy_id: Strategy that executed the trade
            symbol: Trading pair symbol
            pnl: Profit/loss for this trade
            entry_time: When the trade was entered
        """
        try:
            redis = self._get_redis()
            key = self._get_performance_key(strategy_id)
            
            # Get existing metrics or create new
            existing_data = redis.hgetall(key)
            if existing_data:
                # Decode bytes to strings
                decoded_data = {
                    k.decode() if isinstance(k, bytes) else k: 
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in existing_data.items()
                }
                perf = StrategyPerformance.from_dict(decoded_data)
            else:
                # New strategy - initialize metrics
                perf = StrategyPerformance(
                    strategy_id=strategy_id,
                    win_rate=0.0,
                    total_trades=0,
                    winning_trades=0,
                    losing_trades=0,
                    total_pnl=0.0,
                    recent_pnl_24h=0.0,
                    average_win=0.0,
                    average_loss=0.0,
                    last_updated=datetime.now(timezone.utc).isoformat(),
                )
            
            # Update metrics
            perf.total_trades += 1
            perf.total_pnl += pnl
            
            # Check if trade is within last 24 hours
            entry_dt = entry_time
            if isinstance(entry_time, str):
                entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            
            now = datetime.now(timezone.utc)
            if (now - entry_dt).total_seconds() < 24 * 60 * 60:
                perf.recent_pnl_24h += pnl
            
            # Update win/loss counts
            if pnl > 0:
                perf.winning_trades += 1
            elif pnl < 0:
                perf.losing_trades += 1
            
            # Recalculate win rate
            if perf.total_trades > 0:
                perf.win_rate = (perf.winning_trades / perf.total_trades) * 100.0
            
            # Recalculate averages
            if perf.winning_trades > 0:
                # Need to track sum of wins - approximate from total_pnl
                # For exact calculation, we'd need to store individual trade P&Ls
                # For now, approximate: average_win = (total_pnl - sum_of_losses) / winning_trades
                # Simplified: use previous average_win if available, otherwise estimate
                if perf.average_win == 0.0 and perf.winning_trades > 0:
                    # Estimate: assume average win is slightly more than average loss
                    perf.average_win = abs(perf.total_pnl / perf.total_trades) * 1.5
            else:
                perf.average_win = 0.0
            
            if perf.losing_trades > 0:
                if perf.average_loss == 0.0:
                    # Estimate average loss
                    perf.average_loss = abs(perf.total_pnl / perf.total_trades)
            else:
                perf.average_loss = 0.0
            
            perf.last_updated = datetime.now(timezone.utc).isoformat()
            
            # Save to Redis
            redis.hset(key, mapping=perf.to_dict())
            redis.expire(key, PERFORMANCE_TTL_SECONDS)
            
            logger.info(
                f"Updated performance for strategy {strategy_id}: "
                f"trades={perf.total_trades}, win_rate={perf.win_rate:.1f}%, "
                f"total_pnl=${perf.total_pnl:.2f}, recent_24h=${perf.recent_pnl_24h:.2f}"
            )
            
        except Exception as e:
            logger.error(f"Failed to update trade outcome for {strategy_id}: {e}", exc_info=True)
    
    def get_performance(self, strategy_id: str) -> Optional[StrategyPerformance]:
        """
        Get current performance metrics for a strategy.
        
        Args:
            strategy_id: Strategy ID
            
        Returns:
            StrategyPerformance if found, None otherwise
        """
        try:
            redis = self._get_redis()
            key = self._get_performance_key(strategy_id)
            data = redis.hgetall(key)
            
            if not data:
                return None
            
            # Decode bytes to strings
            decoded_data = {
                k.decode() if isinstance(k, bytes) else k: 
                v.decode() if isinstance(v, bytes) else v
                for k, v in data.items()
            }
            decoded_data["strategy_id"] = strategy_id
            
            return StrategyPerformance.from_dict(decoded_data)
            
        except Exception as e:
            logger.error(f"Failed to get performance for {strategy_id}: {e}", exc_info=True)
            return None
    
    def recalculate_all_metrics(self) -> Dict[str, StrategyPerformance]:
        """
        Recalculate performance metrics from all positions.
        
        Called periodically to ensure metrics are accurate.
        
        Returns:
            Dict mapping strategy_id to StrategyPerformance
        """
        try:
            redis = self._get_redis()
            
            # Get all positions
            positions_by_strategy: Dict[str, list] = {}
            
            for key_bytes in redis.scan_iter(match="position:*"):
                key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
                try:
                    data = redis.hgetall(key)
                    if data:
                        decoded_data = {
                            k.decode() if isinstance(k, bytes) else k: 
                            v.decode() if isinstance(v, bytes) else v
                            for k, v in data.items()
                        }
                        
                        strategy_id = decoded_data.get("opened_by_strategy_id", "unknown")
                        pnl = float(decoded_data.get("unrealized_pnl", 0.0))
                        entry_time_str = decoded_data.get("entry_time", "")
                        
                        if strategy_id not in positions_by_strategy:
                            positions_by_strategy[strategy_id] = []
                        
                        positions_by_strategy[strategy_id].append({
                            "pnl": pnl,
                            "entry_time": entry_time_str,
                        })
                except Exception as e:
                    logger.warning(f"Failed to process position {key}: {e}")
                    continue
            
            # Recalculate metrics for each strategy
            results = {}
            for strategy_id, trades in positions_by_strategy.items():
                if not trades:
                    continue
                
                total_trades = len(trades)
                winning_trades = sum(1 for t in trades if t["pnl"] > 0)
                losing_trades = sum(1 for t in trades if t["pnl"] < 0)
                total_pnl = sum(t["pnl"] for t in trades)
                
                # Calculate 24h P&L
                now = datetime.now(timezone.utc)
                recent_pnl_24h = 0.0
                for trade in trades:
                    try:
                        entry_time_str = trade["entry_time"]
                        entry_dt = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
                        if entry_dt.tzinfo is None:
                            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                        
                        if (now - entry_dt).total_seconds() < 24 * 60 * 60:
                            recent_pnl_24h += trade["pnl"]
                    except Exception:
                        pass
                
                win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
                
                winning_pnls = [t["pnl"] for t in trades if t["pnl"] > 0]
                losing_pnls = [t["pnl"] for t in trades if t["pnl"] < 0]
                
                average_win = sum(winning_pnls) / len(winning_pnls) if winning_pnls else 0.0
                average_loss = abs(sum(losing_pnls) / len(losing_pnls)) if losing_pnls else 0.0
                
                perf = StrategyPerformance(
                    strategy_id=strategy_id,
                    win_rate=win_rate,
                    total_trades=total_trades,
                    winning_trades=winning_trades,
                    losing_trades=losing_trades,
                    total_pnl=total_pnl,
                    recent_pnl_24h=recent_pnl_24h,
                    average_win=average_win,
                    average_loss=average_loss,
                    last_updated=datetime.now(timezone.utc).isoformat(),
                )
                
                # Save to Redis
                perf_key = self._get_performance_key(strategy_id)
                redis.hset(perf_key, mapping=perf.to_dict())
                redis.expire(perf_key, PERFORMANCE_TTL_SECONDS)
                
                results[strategy_id] = perf
            
            logger.info(f"Recalculated metrics for {len(results)} strategies")
            return results
            
        except Exception as e:
            logger.error(f"Failed to recalculate metrics: {e}", exc_info=True)
            return {}
    
    async def start(self) -> None:
        """Start the performance monitoring service."""
        if self._running:
            logger.warning("PerformanceMonitor already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("PerformanceMonitor started")
    
    async def stop(self) -> None:
        """Stop the performance monitoring service."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PerformanceMonitor stopped")
    
    async def _run_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                # Recalculate all metrics periodically
                self.recalculate_all_metrics()
                
                # Trigger adaptive adjustments
                try:
                    from backend.performance.adaptation import get_adaptive_threshold_manager
                    from backend.strategies.manager import get_strategy_lifecycle_manager
                    
                    # Get all strategies and adjust thresholds
                    from backend.db import get_session
                    from backend.db.models import Strategy
                    session = get_session()
                    try:
                        strategies = session.query(Strategy).filter(Strategy.status == "active").all()
                        adaptive_manager = get_adaptive_threshold_manager()
                        for strategy in strategies:
                            adaptive_manager.adjust_confidence_thresholds(str(strategy.id))
                    finally:
                        session.close()
                    
                    # Evaluate and disable poor performers (less frequently)
                    # Only check every 10 minutes (2x update interval)
                    import time
                    if not hasattr(self, '_last_evaluation_time'):
                        self._last_evaluation_time = 0
                    
                    if time.time() - self._last_evaluation_time >= 600:  # 10 minutes
                        lifecycle_manager = get_strategy_lifecycle_manager()
                        disabled = lifecycle_manager.evaluate_and_disable_poor_performers()
                        if disabled:
                            logger.info(f"Auto-disabled {len(disabled)} underperforming strategies")
                        self._last_evaluation_time = time.time()
                        
                except Exception as e:
                    logger.debug(f"Adaptive adjustments failed: {e}")
                
                # Wait for next update
                await asyncio.sleep(self.update_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in performance monitor loop: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait 1 minute before retrying


# Global monitor instance
_monitor: Optional[PerformanceMonitor] = None


def get_performance_monitor() -> PerformanceMonitor:
    """Get global performance monitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = PerformanceMonitor()
    return _monitor
