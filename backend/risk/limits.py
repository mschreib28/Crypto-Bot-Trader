"""Budget and risk limits for trade evaluation.

This module enforces dollar-based budget constraints:
- MAX_BUDGET: Maximum total portfolio exposure ($100 default)
- MAX_TRADE_SIZE: Maximum single trade size ($25 default)
- MIN_TRADE_SIZE: Minimum trade size to meet exchange requirements ($1 default)
- DAILY_LOSS_LIMIT: Daily loss threshold that triggers halt ($10 default)
"""

import logging
import os
from decimal import Decimal
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from backend.risk.evaluator import TradeIntent

logger = logging.getLogger(__name__)

# Budget limits from environment (all in USD)
MAX_BUDGET: float = float(os.getenv("MAX_BUDGET", "100.0"))
MAX_TRADE_SIZE: float = float(os.getenv("MAX_TRADE_SIZE", "25.0"))
MIN_TRADE_SIZE: float = float(os.getenv("MIN_TRADE_SIZE", "0.50"))
DAILY_LOSS_LIMIT: float = float(os.getenv("DAILY_LOSS_LIMIT", "10.0"))


def calculate_intent_value(intent: "TradeIntent", current_equity: Decimal) -> float:
    """
    Calculate the dollar value of a TradeIntent.
    
    Converts notional_risk_pct to absolute dollar value based on current equity.
    
    Args:
        intent: The TradeIntent to calculate value for
        current_equity: Current portfolio equity in dollars
        
    Returns:
        Dollar value of the intent
    """
    if current_equity <= 0:
        return 0.0
    
    # notional_risk_pct is percentage of equity
    value = (Decimal(str(intent.notional_risk_pct)) / Decimal("100")) * current_equity
    return float(value)


def check_budget_limit(
    intent: "TradeIntent",
    current_exposure: float,
    current_equity: Decimal
) -> Tuple[bool, str]:
    """
    Check if a TradeIntent passes all budget constraints.
    
    Evaluates:
    1. Trade size >= MIN_TRADE_SIZE (Kraken minimum)
    2. Trade size <= MAX_TRADE_SIZE (per-trade cap)
    3. Total exposure after trade <= MAX_BUDGET
    
    Args:
        intent: The TradeIntent to evaluate
        current_exposure: Current total portfolio exposure in dollars
        current_equity: Current portfolio equity in dollars
        
    Returns:
        Tuple of (approved, rejection_reason)
        - approved: True if all checks pass, False otherwise
        - rejection_reason: Empty string if approved, otherwise reason code
    """
    intent_value = calculate_intent_value(intent, current_equity)
    
    logger.debug(
        f"Budget check: intent_value=${intent_value:.2f}, "
        f"current_exposure=${current_exposure:.2f}, "
        f"limits: min=${MIN_TRADE_SIZE}, max=${MAX_TRADE_SIZE}, budget=${MAX_BUDGET}"
    )
    
    # Check minimum trade size (Kraken requires minimum order sizes)
    if intent_value < MIN_TRADE_SIZE:
        logger.warning(
            f"Trade rejected: ${intent_value:.2f} below minimum trade size ${MIN_TRADE_SIZE}"
        )
        return False, "below_minimum_trade_size"
    
    # Check maximum single trade size
    if intent_value > MAX_TRADE_SIZE:
        logger.warning(
            f"Trade rejected: ${intent_value:.2f} exceeds max trade size ${MAX_TRADE_SIZE}"
        )
        return False, "exceeds_max_trade_size"
    
    # Check total budget limit
    total_exposure_after = current_exposure + intent_value
    if total_exposure_after > MAX_BUDGET:
        logger.warning(
            f"Trade rejected: total exposure ${total_exposure_after:.2f} "
            f"would exceed budget limit ${MAX_BUDGET}"
        )
        return False, "exceeds_budget_limit"
    
    logger.debug(f"Budget check passed: intent_value=${intent_value:.2f}")
    return True, ""


def check_daily_loss_limit(daily_pnl: float) -> bool:
    """
    Check if daily loss limit has been exceeded.
    
    When daily PnL drops to or below -DAILY_LOSS_LIMIT, the system should halt.
    
    Args:
        daily_pnl: Today's profit/loss in dollars (negative means loss)
        
    Returns:
        True if system should halt (loss limit exceeded), False otherwise
    """
    should_halt = daily_pnl <= -DAILY_LOSS_LIMIT
    
    if should_halt:
        logger.warning(
            f"Daily loss limit exceeded: PnL=${daily_pnl:.2f}, "
            f"limit=-${DAILY_LOSS_LIMIT}"
        )
    
    return should_halt


def get_current_exposure_dollars(current_equity: Decimal) -> float:
    """
    Get current portfolio exposure in dollars.
    
    This calculates the dollar value of current open positions and pending intents.
    
    Args:
        current_equity: Current portfolio equity in dollars
        
    Returns:
        Current exposure in dollars
    """
    from backend.risk.rules import get_portfolio_exposure
    
    # get_portfolio_exposure returns percentage
    exposure_pct = get_portfolio_exposure()
    
    if current_equity <= 0:
        return 0.0
    
    # Convert percentage to dollars
    exposure_dollars = (Decimal(str(exposure_pct)) / Decimal("100")) * current_equity
    return float(exposure_dollars)
