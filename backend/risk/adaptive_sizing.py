"""Adaptive position sizing based on strategy performance."""

import logging
import os
from typing import Optional

from backend.performance.monitor import get_performance_monitor

logger = logging.getLogger(__name__)

# Configuration from environment
ADAPTIVE_SIZING_ENABLED = os.getenv("ADAPTIVE_SIZING_ENABLED", "true").lower() == "true"
TARGET_WIN_RATE = float(os.getenv("TARGET_WIN_RATE", "0.55"))  # 55%
MAX_SIZE_MULTIPLIER = float(os.getenv("MAX_SIZE_MULTIPLIER", "1.5"))  # 1.5x max
MIN_SIZE_MULTIPLIER = float(os.getenv("MIN_SIZE_MULTIPLIER", "0.1"))  # 10% minimum


class AdaptivePositionSizer:
    """Adjusts position sizes based on strategy performance."""
    
    def __init__(self):
        """Initialize adaptive position sizer."""
        self.perf_monitor = get_performance_monitor()
    
    def calculate_adaptive_size(
        self,
        strategy_id: str,
        base_size: float,
    ) -> float:
        """
        Calculate adaptive position size based on strategy performance.
        
        Args:
            strategy_id: Strategy ID
            base_size: Base position size (from 2% rule)
            
        Returns:
            Adjusted position size
        """
        if not ADAPTIVE_SIZING_ENABLED:
            return base_size
        
        try:
            # Get performance metrics
            perf = self.perf_monitor.get_performance(strategy_id)
            if perf is None:
                logger.debug(f"No performance data for {strategy_id}, using base size")
                return base_size
            
            # Need minimum trades for reliable sizing
            min_trades = int(os.getenv("MIN_TRADES_FOR_EVALUATION", "10"))
            if perf.total_trades < min_trades:
                logger.debug(
                    f"Strategy {strategy_id}: Only {perf.total_trades} trades "
                    f"(need {min_trades}), using base size"
                )
                return base_size
            
            # Calculate performance multiplier
            win_rate_decimal = perf.win_rate / 100.0
            
            if win_rate_decimal < TARGET_WIN_RATE:
                # Underperforming: reduce size
                multiplier = min(1.0, win_rate_decimal / TARGET_WIN_RATE)
                multiplier = max(MIN_SIZE_MULTIPLIER, multiplier)  # Enforce minimum
            else:
                # Well-performing: can increase size
                excess_rate = win_rate_decimal - TARGET_WIN_RATE
                multiplier = min(MAX_SIZE_MULTIPLIER, 1.0 + (excess_rate / TARGET_WIN_RATE))
            
            adjusted_size = base_size * multiplier
            
            logger.info(
                f"Strategy {strategy_id}: Adjusted position size "
                f"${base_size:.2f} -> ${adjusted_size:.2f} "
                f"(multiplier={multiplier:.2f}x, win_rate={perf.win_rate:.1f}%)"
            )
            
            return adjusted_size
            
        except Exception as e:
            logger.error(f"Failed to calculate adaptive size for {strategy_id}: {e}", exc_info=True)
            return base_size  # Fallback to base size


def get_adaptive_position_sizer() -> AdaptivePositionSizer:
    """Get global adaptive position sizer instance."""
    return AdaptivePositionSizer()
