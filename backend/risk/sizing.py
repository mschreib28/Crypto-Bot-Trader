import os
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from backend.risk.micro_mode import (
    is_micro_mode,
    check_min_stop_distance,
    check_min_notional,
)

logger = logging.getLogger(__name__)


@dataclass
class PositionSize:
    max_risk_usd: float
    position_size_usd: float
    quantity: float
    stop_loss_price: float
    stop_loss_pct: float


class PositionSizer:
    KRAKEN_MIN_ORDER_USD = 1.0

    def __init__(self, default_stop_loss_pct: float = None):
        self.default_stop_loss_pct = default_stop_loss_pct or float(os.getenv("STOP_LOSS_PCT", "5.0"))

    def calculate(
        self,
        account_equity: float,
        risk_pct: float,
        entry_price: float,
        stop_loss_pct: float = None,
        strategy_id: str = None,
        atr: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        symbol: Optional[str] = None,
    ) -> Optional[PositionSize]:
        """
        Calculate position size using: Position = Risk / Stop-Loss Distance

        Example ($100 equity, 2% risk, $3200 ETH, 5% stop):
        - Max Risk: $2.00
        - Position: $2 / 0.05 = $40
        - Quantity: $40 / $3200 = 0.0125 ETH
        
        In micro mode (equity < $250):
        - Enforces minimum stop distance (2.0 ATR minimum)
        - Enforces minimum notional ($5.0 minimum)
        - Returns None if trade should be skipped
        
        Args:
            account_equity: Current account equity
            risk_pct: Risk percentage per trade (default: 2%)
            entry_price: Entry price
            stop_loss_pct: Stop loss percentage (optional, uses default if None)
            strategy_id: Strategy ID for adaptive sizing (optional)
            atr: ATR value for micro mode stop distance check (optional)
            stop_loss_price: Explicit stop loss price (optional, calculated if None)
            symbol: Pair symbol for logs (optional).

        Returns:
            PositionSize if trade is valid, None if skipped (micro mode or below min notional).
        """
        stop_loss_pct = stop_loss_pct or self.default_stop_loss_pct
        
        # Calculate stop loss price if not provided
        if stop_loss_price is None:
            stop_loss_price = entry_price * (1 - stop_loss_pct / 100.0)

        # Check micro mode minimum stop distance
        if is_micro_mode(account_equity):
            stop_valid, stop_reason = check_min_stop_distance(entry_price, stop_loss_price, atr)
            if not stop_valid:
                logger.warning(
                    f"Micro mode: Trade skipped - {stop_reason}. "
                    f"Equity=${account_equity:.2f}, entry=${entry_price:.2f}, stop=${stop_loss_price:.2f}"
                )
                return None

        max_risk_usd = account_equity * (risk_pct / 100.0)
        position_size_usd = max_risk_usd / (stop_loss_pct / 100.0)
        
        # Apply adaptive sizing if enabled and strategy_id provided
        if strategy_id:
            try:
                from backend.risk.adaptive_sizing import get_adaptive_position_sizer
                adaptive_sizer = get_adaptive_position_sizer()
                position_size_usd = adaptive_sizer.calculate_adaptive_size(
                    strategy_id=strategy_id,
                    base_size=position_size_usd,
                )
                # Recalculate max_risk_usd based on adjusted size
                max_risk_usd = position_size_usd * (stop_loss_pct / 100.0)
            except Exception as e:
                logger.debug(f"Adaptive sizing failed for {strategy_id}: {e}, using base size")
        
        # Check micro mode minimum notional
        if is_micro_mode(account_equity):
            should_proceed, adjusted_size, notional_reason = check_min_notional(
                position_size_usd, account_equity
            )
            if not should_proceed:
                logger.warning(
                    f"Micro mode: Trade skipped - {notional_reason}. "
                    f"Equity=${account_equity:.2f}, calculated_size=${position_size_usd:.2f}"
                )
                return None
            if adjusted_size != position_size_usd:
                # Use fixed minimal size
                position_size_usd = adjusted_size
                # Recalculate max_risk_usd based on adjusted size
                max_risk_usd = position_size_usd * (stop_loss_pct / 100.0)

        if entry_price <= 0:
            logger.warning(
                "[sizing] Rejected %s: invalid entry_price=%.6f",
                symbol or "?",
                entry_price,
            )
            return None

        stop_distance_pct = abs(entry_price - stop_loss_price) / entry_price
        risk_budget = max_risk_usd
        raw_quantity = position_size_usd / entry_price
        sym = symbol or "?"

        # Runner must set LOG_LEVEL=DEBUG to see this on corpus.
        logger.debug(
            "[sizing] %s entry=%.6f stop=%.6f stop_dist_pct=%.4f "
            "risk_budget=%.4f raw_qty=%.10f",
            sym,
            entry_price,
            stop_loss_price,
            stop_distance_pct,
            risk_budget,
            raw_quantity,
        )

        if raw_quantity * entry_price < self.KRAKEN_MIN_ORDER_USD:
            logger.warning(
                "[sizing] Rejected %s: notional $%.4f below minimum $%.2f "
                "(entry=%.6f qty=%.10f stop_dist=%.4f%%)",
                sym,
                raw_quantity * entry_price,
                self.KRAKEN_MIN_ORDER_USD,
                entry_price,
                raw_quantity,
                stop_distance_pct * 100,
            )
            return None

        quantity = raw_quantity

        result = PositionSize(
            max_risk_usd=round(max_risk_usd, 2),
            position_size_usd=round(position_size_usd, 2),
            quantity=round(quantity, 8),
            stop_loss_price=round(stop_loss_price, 2),
            stop_loss_pct=stop_loss_pct,
        )

        logger.info(f"Position sizing: equity=${account_equity}, risk={risk_pct}%, "
                    f"price=${entry_price}, stop={stop_loss_pct}% -> "
                    f"size=${result.position_size_usd}, qty={result.quantity}")

        return result

    def validate_minimum(self, position_size_usd: float) -> tuple[bool, str]:
        """Check against Kraken minimum order size."""
        if position_size_usd < self.KRAKEN_MIN_ORDER_USD:
            return False, f"below_kraken_minimum (${position_size_usd:.2f} < ${self.KRAKEN_MIN_ORDER_USD})"
        return True, ""
