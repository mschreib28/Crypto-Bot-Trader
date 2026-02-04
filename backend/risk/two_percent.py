import os
import logging

logger = logging.getLogger(__name__)


class TwoPercentRule:
    def __init__(self, risk_pct: float = None):
        self.risk_pct = (risk_pct or float(os.getenv("RISK_PCT_PER_TRADE", "2.0"))) / 100.0

    def calculate_max_risk(self, account_equity: float) -> float:
        """Returns max USD to risk on a single trade."""
        return account_equity * self.risk_pct

    def validate_trade(self, trade_risk: float, account_equity: float) -> tuple[bool, str]:
        """Returns (approved, rejection_reason)"""
        max_risk = self.calculate_max_risk(account_equity)

        # Use a small epsilon for floating point comparison to avoid precision issues
        epsilon = 0.01  # 1 cent tolerance
        if trade_risk > max_risk + epsilon:
            reason = f"exceeds_2pct_rule (${trade_risk:.2f} > ${max_risk:.2f})"
            logger.warning(f"Trade rejected: {reason}")
            return False, reason

        logger.info(f"2% rule passed: risk ${trade_risk:.2f} <= max ${max_risk:.2f}")
        return True, ""
