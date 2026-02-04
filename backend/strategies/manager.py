"""Strategy lifecycle management based on performance."""

import logging
import os
from typing import List

from backend.db import get_session
from backend.db.models import Strategy
from backend.performance.monitor import get_performance_monitor
from backend.performance.models import StrategyPerformance

logger = logging.getLogger(__name__)

# Configuration from environment
ADAPTIVE_ENABLED = os.getenv("ADAPTIVE_ENABLED", "true").lower() == "true"
MIN_WIN_RATE_THRESHOLD = float(os.getenv("MIN_WIN_RATE_THRESHOLD", "0.40"))  # 40%
MIN_TRADES_FOR_EVALUATION = int(os.getenv("MIN_TRADES_FOR_EVALUATION", "10"))


class StrategyLifecycleManager:
    """Manages strategy enable/disable based on performance."""
    
    def __init__(self):
        """Initialize strategy lifecycle manager."""
        self.perf_monitor = get_performance_monitor()
    
    def evaluate_and_disable_poor_performers(self) -> List[str]:
        """
        Evaluate all active strategies and disable underperformers.
        
        Returns:
            List of strategy IDs that were auto-disabled
        """
        if not ADAPTIVE_ENABLED:
            return []
        
        disabled_strategies = []
        
        try:
            session = get_session()
            try:
                # Get all active strategies
                active_strategies = session.query(Strategy).filter(
                    Strategy.status == "active"
                ).all()
                
                for strategy in active_strategies:
                    strategy_id = str(strategy.id)
                    
                    # Get performance metrics
                    perf = self.perf_monitor.get_performance(strategy_id)
                    if perf is None:
                        logger.debug(f"No performance data for strategy {strategy.name} ({strategy_id})")
                        continue
                    
                    # Check evaluation criteria
                    if perf.total_trades < MIN_TRADES_FOR_EVALUATION:
                        logger.debug(
                            f"Strategy {strategy.name}: Only {perf.total_trades} trades "
                            f"(need {MIN_TRADES_FOR_EVALUATION}), skipping evaluation"
                        )
                        continue
                    
                    # Check win rate threshold
                    win_rate_decimal = perf.win_rate / 100.0
                    if win_rate_decimal < MIN_WIN_RATE_THRESHOLD:
                        # Auto-disable strategy
                        strategy.status = "paused"
                        session.commit()
                        
                        disabled_strategies.append(strategy_id)
                        
                        logger.warning(
                            f"Strategy '{strategy.name}' ({strategy_id}) auto-paused: "
                            f"win_rate={perf.win_rate:.1f}% < {MIN_WIN_RATE_THRESHOLD*100:.0f}% "
                            f"after {perf.total_trades} trades (total_pnl=${perf.total_pnl:.2f})"
                        )
                        
                        # Log to activity feed
                        try:
                            from backend.api.routes.events import log_activity
                            log_activity(
                                activity_type="strategy",
                                message=f"Strategy '{strategy.name}' auto-paused due to poor performance",
                                details={
                                    "strategy_id": strategy_id,
                                    "strategy_name": strategy.name,
                                    "win_rate": perf.win_rate,
                                    "total_trades": perf.total_trades,
                                    "total_pnl": perf.total_pnl,
                                    "reason": "win_rate_below_threshold",
                                },
                            )
                        except Exception as e:
                            logger.debug(f"Failed to log activity: {e}")
                    else:
                        logger.debug(
                            f"Strategy {strategy.name}: Win rate {perf.win_rate:.1f}% "
                            f">= {MIN_WIN_RATE_THRESHOLD*100:.0f}%, keeping active"
                        )
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to evaluate strategies: {e}", exc_info=True)
        
        return disabled_strategies


def get_strategy_lifecycle_manager() -> StrategyLifecycleManager:
    """Get global strategy lifecycle manager instance."""
    return StrategyLifecycleManager()
