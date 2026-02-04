"""Adaptive parameter adjustment based on performance."""

import logging
import os
from typing import Optional, Tuple

from backend.db import get_session
from backend.db.models import Strategy
from backend.performance.monitor import get_performance_monitor
from backend.performance.models import StrategyPerformance

logger = logging.getLogger(__name__)

# Configuration from environment
ADAPTIVE_ENABLED = os.getenv("ADAPTIVE_ENABLED", "true").lower() == "true"
TARGET_WIN_RATE = float(os.getenv("TARGET_WIN_RATE", "0.55"))  # 55%
ADAPTIVE_CONFIDENCE_STEP = float(os.getenv("ADAPTIVE_CONFIDENCE_STEP", "5.0"))  # 5%
MIN_TRADES_FOR_EVALUATION = int(os.getenv("MIN_TRADES_FOR_EVALUATION", "10"))
DEAD_ZONE_PCT = 2.0  # ±2% dead zone to prevent oscillation


class AdaptiveThresholdManager:
    """Manages adaptive confidence threshold adjustments."""
    
    def __init__(self):
        """Initialize adaptive threshold manager."""
        self.perf_monitor = get_performance_monitor()
    
    def adjust_confidence_thresholds(self, strategy_id: str) -> Optional[Tuple[float, float]]:
        """
        Adjust confidence thresholds for a strategy based on performance.
        
        Args:
            strategy_id: Strategy ID to adjust
            
        Returns:
            Tuple of (new_confidence_buy, new_confidence_sell) if adjusted, None otherwise
        """
        if not ADAPTIVE_ENABLED:
            return None
        
        try:
            # Get current performance
            perf = self.perf_monitor.get_performance(strategy_id)
            if perf is None:
                logger.debug(f"No performance data for strategy {strategy_id}, skipping adjustment")
                return None
            
            # Check minimum trades requirement
            if perf.total_trades < MIN_TRADES_FOR_EVALUATION:
                logger.debug(
                    f"Strategy {strategy_id}: Only {perf.total_trades} trades "
                    f"(need {MIN_TRADES_FOR_EVALUATION}), skipping adjustment"
                )
                return None
            
            # Get current thresholds from database
            session = get_session()
            try:
                strategy = session.query(Strategy).filter(Strategy.id == strategy_id).first()
                if not strategy:
                    logger.warning(f"Strategy {strategy_id} not found in database")
                    return None
                
                config = strategy.config or {}
                filters = config.get("filters", {})
                current_buy = float(filters.get("confidence_buy", 90.0))
                current_sell = float(filters.get("confidence_sell", 90.0))
                
                # Calculate win rate as decimal (0-1)
                win_rate_decimal = perf.win_rate / 100.0
                
                # Check dead zone (prevent oscillation)
                if abs(win_rate_decimal - TARGET_WIN_RATE) < (DEAD_ZONE_PCT / 100.0):
                    logger.debug(
                        f"Strategy {strategy_id}: Win rate {perf.win_rate:.1f}% "
                        f"within dead zone (±{DEAD_ZONE_PCT}%), no adjustment"
                    )
                    return None
                
                # Apply global profitability adjustments
                try:
                    from backend.performance.profitability import get_profitability_manager
                    profitability_manager = get_profitability_manager()
                    global_multiplier = profitability_manager.get_global_adjustment_multiplier()
                except Exception:
                    global_multiplier = 1.0
                
                # Calculate adjustments
                new_buy = current_buy
                new_sell = current_sell
                
                if win_rate_decimal < TARGET_WIN_RATE:
                    # Win rate too low: increase thresholds (tighter filters)
                    adjustment = ADAPTIVE_CONFIDENCE_STEP * global_multiplier
                    new_buy = min(100.0, current_buy + adjustment)
                    new_sell = min(100.0, current_sell + adjustment)
                    reason = f"win_rate={perf.win_rate:.1f}% < target={TARGET_WIN_RATE*100:.1f}%"
                else:
                    # Win rate too high: decrease thresholds (more opportunities)
                    adjustment = ADAPTIVE_CONFIDENCE_STEP * (1.0 / global_multiplier)  # Relax less if losing
                    new_buy = max(50.0, current_buy - adjustment)
                    new_sell = max(50.0, current_sell - adjustment)
                    reason = f"win_rate={perf.win_rate:.1f}% > target={TARGET_WIN_RATE*100:.1f}%"
                
                # Check if adjustment needed
                if abs(new_buy - current_buy) < 0.1 and abs(new_sell - current_sell) < 0.1:
                    return None
                
                # Update database
                if "filters" not in config:
                    config["filters"] = {}
                config["filters"]["confidence_buy"] = new_buy
                config["filters"]["confidence_sell"] = new_sell
                strategy.config = config
                session.commit()
                
                logger.info(
                    f"Strategy {strategy_id}: Adjusted confidence thresholds "
                    f"(buy: {current_buy:.1f}% -> {new_buy:.1f}%, "
                    f"sell: {current_sell:.1f}% -> {new_sell:.1f}%) "
                    f"due to {reason}"
                )
                
                return (new_buy, new_sell)
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to adjust thresholds for {strategy_id}: {e}", exc_info=True)
            return None


def get_adaptive_threshold_manager() -> AdaptiveThresholdManager:
    """Get global adaptive threshold manager instance."""
    return AdaptiveThresholdManager()
