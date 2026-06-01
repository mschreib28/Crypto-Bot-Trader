import os
import logging
import time
import json
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
            kraken_client: Ignored (kept for API compatibility). CLI is used instead.
        """
        self._cached_balance: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[float] = None
        
        # Test mode: use static equity when initial_equity is explicitly provided
        self._use_static_equity = initial_equity is not None
        self._static_equity = initial_equity if initial_equity is not None else 0.0
        self._fallback_equity = float(os.getenv("ACCOUNT_EQUITY", "0.0"))
        
        self.realized_pnl = 0.0
        self.daily_pnl = 0.0
        self._risk_pct = float(os.getenv("RISK_PCT_PER_TRADE", "2.0")) / 100.0

    def _fetch_live_balance_dict(self) -> dict:
        """Fetch balance dict using the Kraken CLI sync helper.

        Only counts USD/ZUSD holdings toward total_usd for simplicity
        (crypto holdings are excluded from the live equity calculation here;
        use the /api/v1/account/balance endpoint for full portfolio value).
        """
        from backend.execution.kraken_cli import get_balance_sync, _normalize_kraken_asset, _USD_ASSETS
        raw = get_balance_sync()
        holdings = []
        total_usd = 0.0
        for asset, bal_str in raw.items():
            try:
                quantity = float(bal_str)
            except (TypeError, ValueError):
                continue
            if quantity <= 0:
                continue
            symbol = _normalize_kraken_asset(asset)
            if asset in _USD_ASSETS or symbol == "USD":
                value_usd = quantity
                total_usd += value_usd
                holdings.append({
                    "symbol": symbol,
                    "quantity": round(quantity, 8),
                    "value_usd": round(value_usd, 2),
                })
        return {
            "total_usd": round(total_usd, 2),
            "available_usd": round(total_usd, 2),
            "holdings": holdings,
        }

    def _get_shadow_balance(self) -> Optional[Dict[str, Any]]:
        """
        Get shadow balance from Redis.
        
        Returns:
            Shadow balance dict with total_usd, available_usd, holdings, or None if not found.
        """
        try:
            from backend.redis import get_redis_client
            from backend.redis.keys import SHADOW_BALANCE_KEY
            
            redis_client = get_redis_client()
            shadow_balance_json = redis_client.get(SHADOW_BALANCE_KEY)
            
            if shadow_balance_json:
                return json.loads(shadow_balance_json)
            return None
        except Exception as e:
            logger.warning(f"Failed to get shadow balance: {e}")
            return None

    def _get_cached_balance(self) -> Dict[str, Any]:
        """
        Get balance from cache or fetch from Kraken.
        
        In shadow mode: returns shadow balance from Redis.
        In live mode: fetches from Kraken API with caching.
        
        Returns:
            Balance dict with total_usd, available_usd, holdings.
            Falls back to last known balance or zeros if API fails.
        """
        # Check shadow mode first
        try:
            from backend.api.routes.trading import get_shadow_live_mode
            if get_shadow_live_mode():
                # Shadow mode: use shadow balance from Redis
                shadow_balance = self._get_shadow_balance()
                if shadow_balance:
                    logger.debug(f"Using shadow balance: total=${shadow_balance.get('total_usd', 0)}")
                    return shadow_balance
                else:
                    # Shadow balance not set, return default
                    logger.info("Shadow mode enabled but no balance set, returning default $1000")
                    return {
                        "total_usd": 1000.0,
                        "available_usd": 1000.0,
                        "holdings": [{"symbol": "USD", "quantity": 1000.0, "value_usd": 1000.0}]
                    }
        except Exception as e:
            logger.warning(f"Failed to check shadow mode: {e}, falling back to Kraken API")
        
        # Live mode: fetch from Kraken API
        now = time.time()
        
        # Check if cache is still valid
        if (
            self._cached_balance is not None
            and self._cache_time is not None
            and (now - self._cache_time) < BALANCE_CACHE_TTL
        ):
            return self._cached_balance
        
        # Fetch fresh balance from Kraken CLI
        try:
            balance = self._fetch_live_balance_dict()

            # Update cache
            self._cached_balance = balance
            self._cache_time = now

            logger.info(
                f"Balance fetched via CLI: total=${balance['total_usd']}, "
                f"available=${balance['available_usd']} (cached for {BALANCE_CACHE_TTL}s)"
            )

            return balance

        except Exception as e:
            logger.error(f"Failed to fetch balance from Kraken CLI: {e}")
            
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
        """
        Get initial equity (first balance fetch or fallback).
        
        In shadow mode: returns stored initial shadow equity from Redis.
        In live mode: returns fallback equity from env var.
        """
        if self._use_static_equity:
            return self._static_equity
        
        # Check shadow mode for initial equity
        try:
            from backend.api.routes.trading import get_shadow_live_mode
            if get_shadow_live_mode():
                from backend.redis import get_redis_client
                from backend.redis.keys import SHADOW_INITIAL_EQUITY_KEY
                
                redis_client = get_redis_client()
                initial_equity_str = redis_client.get(SHADOW_INITIAL_EQUITY_KEY)
                
                if initial_equity_str:
                    return float(initial_equity_str)
                else:
                    # Initial equity not stored yet, use current shadow balance as initial (first-time setup)
                    shadow_balance = self._get_shadow_balance()
                    if shadow_balance:
                        initial_equity = shadow_balance.get("total_usd", 0.0)
                        logger.info(f"Using current shadow balance as initial equity: ${initial_equity}")
                        return initial_equity
        except Exception as e:
            logger.warning(f"Failed to get shadow initial equity: {e}, using fallback")
        
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
