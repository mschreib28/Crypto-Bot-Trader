import os
import logging
import time
from dataclasses import dataclass
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Cache TTL for balance fetches (seconds)
BALANCE_CACHE_TTL = 60


@dataclass
class AccountState:
    initial_equity: float
    realized_pnl: float
    current_equity: float
    daily_pnl: float
    max_risk_per_trade: float


class AccountTracker:
    """
    Tracks account equity and P&L using live Kraken balance.
    
    Features:
    - Fetches live balance from Kraken API
    - Caches balance for 60 seconds to avoid rate limits
    - Falls back to last known balance if API fails
    - Works with $0 balance (new accounts)
    - Test mode: if initial_equity is provided, uses static equity (no API calls)
    """
    
    def __init__(self, initial_equity: float = None, kraken_client=None):
        """
        Initialize AccountTracker.
        
        Args:
            initial_equity: Override equity (for testing). If provided, uses static 
                          equity tracking without Kraken API calls.
            kraken_client: Optional KrakenClient instance (lazy-loaded if None).
        """
        self._kraken = kraken_client
        self._cached_balance: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[float] = None
        
        # Test mode: use static equity when initial_equity is explicitly provided
        self._use_static_equity = initial_equity is not None
        self._static_equity = initial_equity if initial_equity is not None else 0.0
        self._fallback_equity = float(os.getenv("ACCOUNT_EQUITY", "0.0"))
        
        self.realized_pnl = 0.0
        self.daily_pnl = 0.0
        self._risk_pct = float(os.getenv("RISK_PCT_PER_TRADE", "2.0")) / 100.0

    def _get_kraken_client(self):
        """Lazy-load KrakenClient to avoid circular imports."""
        if self._kraken is None:
            from backend.execution.kraken_rest import KrakenClient
            self._kraken = KrakenClient()
        return self._kraken

    def _get_cached_balance(self) -> Dict[str, Any]:
        """
        Get balance from cache or fetch from Kraken.
        
        Returns:
            Balance dict with total_usd, available_usd, holdings.
            Falls back to last known balance or zeros if API fails.
        """
        now = time.time()
        
        # Check if cache is still valid
        if (
            self._cached_balance is not None
            and self._cache_time is not None
            and (now - self._cache_time) < BALANCE_CACHE_TTL
        ):
            return self._cached_balance
        
        # Fetch fresh balance from Kraken
        try:
            client = self._get_kraken_client()
            balance = client.get_account_balance()
            
            # Update cache
            self._cached_balance = balance
            self._cache_time = now
            
            logger.info(
                f"Balance fetched from Kraken: total=${balance['total_usd']}, "
                f"available=${balance['available_usd']} (cached for {BALANCE_CACHE_TTL}s)"
            )
            
            return balance
            
        except Exception as e:
            logger.error(f"Failed to fetch balance from Kraken: {e}")
            
            # Fallback to last known balance
            if self._cached_balance is not None:
                logger.warning(
                    f"Using cached balance from {now - self._cache_time:.0f}s ago: "
                    f"${self._cached_balance['total_usd']}"
                )
                return self._cached_balance
            
            # Ultimate fallback: return zeros (safe for new accounts)
            logger.warning("No cached balance available. Using fallback equity.")
            return {
                "total_usd": self._fallback_equity,
                "available_usd": self._fallback_equity,
                "holdings": [],
            }

    def fetch_from_kraken(self) -> Dict[str, Any]:
        """
        Force fetch balance from Kraken (bypasses cache).
        
        Returns:
            Balance dict with total_usd, available_usd, holdings.
        """
        # Invalidate cache
        self._cache_time = None
        return self._get_cached_balance()

    @property
    def initial_equity(self) -> float:
        """Get initial equity (first balance fetch or fallback)."""
        if self._use_static_equity:
            return self._static_equity
        return self._fallback_equity

    @property
    def current_equity(self) -> float:
        """
        Get current equity.
        
        In production: fetches live balance from Kraken.
        In test mode (initial_equity provided): uses static equity + realized P&L.
        """
        if self._use_static_equity:
            return self._static_equity + self.realized_pnl
        return self._get_cached_balance()["total_usd"]

    @property
    def available_equity(self) -> float:
        """Get available equity (minus open orders)."""
        if self._use_static_equity:
            return self._static_equity + self.realized_pnl
        return self._get_cached_balance()["available_usd"]

    @property
    def max_risk_per_trade(self) -> float:
        """Calculate max risk per trade using 2% rule on live balance."""
        return self.current_equity * self._risk_pct

    def get_holdings(self) -> list:
        """Get list of current holdings."""
        if self._use_static_equity:
            return []
        return self._get_cached_balance()["holdings"]

    def record_pnl(self, pnl: float):
        """Record realized P&L."""
        self.realized_pnl += pnl
        self.daily_pnl += pnl
        logger.info(f"P&L recorded: ${pnl:.2f}, total: ${self.realized_pnl:.2f}")

    def reset_daily_pnl(self):
        """Reset daily P&L counter."""
        self.daily_pnl = 0.0

    def get_state(self) -> AccountState:
        """Get current account state."""
        return AccountState(
            initial_equity=self.initial_equity,
            realized_pnl=round(self.realized_pnl, 2),
            current_equity=round(self.current_equity, 2),
            daily_pnl=round(self.daily_pnl, 2),
            max_risk_per_trade=round(self.max_risk_per_trade, 2),
        )

    def recalculate_risk_capital(self) -> float:
        """
        Recalculate risk capital based on current equity and store in Redis.
        
        Risk capital = current_equity × RISK_PCT_PER_TRADE (default 2%)
        This value is used for Scout sizing to maintain consistent risk per trade.
        
        Returns:
            The calculated risk capital amount
        """
        try:
            from backend.redis import get_redis_client
            from backend.redis.keys import RISK_CAPITAL_KEY, RISK_CAPITAL_UPDATED_KEY
            
            redis_client = get_redis_client()
            
            # Get current equity
            equity = self.current_equity
            
            # Calculate risk capital: equity × risk percentage
            risk_pct = float(os.getenv("RISK_PCT_PER_TRADE", "2.0")) / 100.0
            new_risk_capital = equity * risk_pct
            
            # Get old risk capital for logging
            old_risk_capital_str = redis_client.get(RISK_CAPITAL_KEY)
            old_risk_capital = float(old_risk_capital_str) if old_risk_capital_str else None
            
            # Store in Redis
            redis_client.set(RISK_CAPITAL_KEY, str(new_risk_capital))
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            redis_client.set(RISK_CAPITAL_UPDATED_KEY, timestamp)
            
            # Log the recalculation
            if old_risk_capital is not None:
                logger.info(
                    f"Risk capital recalculated: ${old_risk_capital:.2f} -> ${new_risk_capital:.2f} "
                    f"(equity: ${equity:.2f})"
                )
            else:
                logger.info(
                    f"Risk capital initialized: ${new_risk_capital:.2f} "
                    f"(equity: ${equity:.2f})"
                )
            
            return new_risk_capital
            
        except Exception as e:
            logger.error(f"Failed to recalculate risk capital: {e}", exc_info=True)
            # Return calculated value even if Redis write fails
            equity = self.current_equity
            risk_pct = float(os.getenv("RISK_PCT_PER_TRADE", "2.0")) / 100.0
            return equity * risk_pct
