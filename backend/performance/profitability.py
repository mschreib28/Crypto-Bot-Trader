"""Profitability consistency and daily target management."""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.redis import get_redis_client
from backend.positions.tracker import get_position_tracker

logger = logging.getLogger(__name__)

# Configuration from environment
PROFITABILITY_TARGET_USD = float(os.getenv("PROFITABILITY_TARGET_USD", "1.0"))  # $1/day
PROFITABILITY_COOLDOWN_ENABLED = os.getenv("PROFITABILITY_COOLDOWN_ENABLED", "true").lower() == "true"
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))

# Redis keys
DAILY_PNL_KEY_PATTERN = "performance:daily_pnl:{date}"
CONSECUTIVE_DAYS_KEY = "performance:consecutive_days"


class ProfitabilityManager:
    """Manages daily profitability targets and consistency."""
    
    def __init__(self):
        """Initialize profitability manager."""
        self._redis = None
        self._cooldown_active = False
        self._emergency_pause = False
    
    def _get_redis(self):
        """Get Redis client."""
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis
    
    def _get_today_key(self) -> str:
        """Get Redis key for today's P&L."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return DAILY_PNL_KEY_PATTERN.format(date=today)
    
    def calculate_daily_pnl(self) -> float:
        """
        Calculate today's P&L from all positions.
        
        Returns:
            Daily P&L in USD
        """
        try:
            tracker = get_position_tracker()
            positions = tracker.get_all_positions()
            
            daily_pnl = 0.0
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            
            for position in positions:
                # Check if position was opened today
                try:
                    entry_time_str = position.entry_time
                    entry_dt = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                    
                    # Include P&L if position opened today or is still open
                    if entry_dt >= today_start or position.quantity > 0:
                        daily_pnl += position.unrealized_pnl
                except Exception as e:
                    logger.debug(f"Failed to parse entry_time for {position.symbol}: {e}")
                    # Include anyway if position exists
                    daily_pnl += position.unrealized_pnl
            
            return daily_pnl
            
        except Exception as e:
            logger.error(f"Failed to calculate daily P&L: {e}", exc_info=True)
            return 0.0
    
    def update_daily_metrics(self) -> dict:
        """
        Update daily P&L metrics and check profitability targets.
        
        Returns:
            Dict with daily_pnl, target_reached, cooldown_active, etc.
        """
        try:
            redis = self._get_redis()
            daily_pnl = self.calculate_daily_pnl()
            today_key = self._get_today_key()
            
            # Store daily P&L
            redis.set(today_key, str(daily_pnl), ex=7 * 24 * 60 * 60)  # 7 days TTL
            
            # Check profitability target
            target_reached = daily_pnl >= PROFITABILITY_TARGET_USD
            
            # Check consecutive days
            consecutive_data = redis.get(CONSECUTIVE_DAYS_KEY)
            if consecutive_data:
                consecutive_str = consecutive_data.decode() if isinstance(consecutive_data, bytes) else consecutive_data
                try:
                    consecutive_profitable, consecutive_losing = map(int, consecutive_str.split(':'))
                except Exception:
                    consecutive_profitable, consecutive_losing = 0, 0
            else:
                consecutive_profitable, consecutive_losing = 0, 0
            
            # Update consecutive days
            if daily_pnl > 0:
                consecutive_profitable += 1
                consecutive_losing = 0
            elif daily_pnl < 0:
                consecutive_losing += 1
                consecutive_profitable = 0
            # If daily_pnl == 0, don't change consecutive counts
            
            redis.set(CONSECUTIVE_DAYS_KEY, f"{consecutive_profitable}:{consecutive_losing}", ex=30 * 24 * 60 * 60)
            
            # Check emergency pause
            if consecutive_losing >= MAX_CONSECUTIVE_LOSSES:
                self._emergency_pause = True
                logger.error(
                    f"EMERGENCY PAUSE: {consecutive_losing} consecutive losing days detected. "
                    f"Daily P&L: ${daily_pnl:.2f}. All trading paused."
                )
                # TODO: Implement actual trading pause (set trading_enabled=false in Redis)
            
            # Check cooldown mode
            if target_reached and PROFITABILITY_COOLDOWN_ENABLED:
                self._cooldown_active = True
                logger.info(
                    f"Daily target reached: ${daily_pnl:.2f} >= ${PROFITABILITY_TARGET_USD:.2f}. "
                    f"Entering cooldown mode (reduced trading frequency)."
                )
            else:
                self._cooldown_active = False
            
            return {
                "daily_pnl": daily_pnl,
                "target": PROFITABILITY_TARGET_USD,
                "target_reached": target_reached,
                "cooldown_active": self._cooldown_active,
                "emergency_pause": self._emergency_pause,
                "consecutive_profitable": consecutive_profitable,
                "consecutive_losing": consecutive_losing,
            }
            
        except Exception as e:
            logger.error(f"Failed to update daily metrics: {e}", exc_info=True)
            return {}
    
    def get_global_adjustment_multiplier(self) -> float:
        """
        Get global adjustment multiplier for confidence thresholds based on profitability.
        
        Returns:
            Multiplier (1.0 = no change, >1.0 = tighten filters, <1.0 = relax filters)
        """
        if self._emergency_pause:
            return 2.0  # Double thresholds (very tight)
        
        if self._cooldown_active:
            return 1.1  # 10% tighter filters
        
        metrics = self.update_daily_metrics()
        daily_pnl = metrics.get("daily_pnl", 0.0)
        
        if daily_pnl < 0:
            # Losing day: tighten filters
            return 1.15  # 15% tighter
        elif daily_pnl >= PROFITABILITY_TARGET_USD:
            # Target reached: maintain or slightly relax
            return 0.95  # 5% more relaxed
        
        return 1.0  # Normal operation


def get_profitability_manager() -> ProfitabilityManager:
    """Get global profitability manager instance."""
    return ProfitabilityManager()
