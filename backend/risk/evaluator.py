"""Core risk evaluation logic for TradeIntents."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from backend.risk.models import RiskDecision
from backend.risk.halt import is_halted
from backend.risk.rules import (
    get_portfolio_exposure,
    get_pending_intents_exposure,
    get_strategy_current_exposure,
    check_portfolio_limit,
    check_strategy_limit,
    check_market_data_freshness,
)

logger = logging.getLogger(__name__)


@dataclass
class TradeIntent:
    """
    TradeIntent model matching contract schema from contracts/types.md.
    
    This is a backend representation of the contract type.
    """
    strategy_id: str
    symbol: str
    side: str  # "buy" | "sell"
    intent_type: str  # "enter" | "exit" | "reduce"
    notional_risk_pct: float
    metadata: Dict[str, Any]
    
    def __post_init__(self):
        """Validate TradeIntent fields."""
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got: {self.side}")
        if self.intent_type not in ("enter", "exit", "reduce"):
            raise ValueError(f"intent_type must be 'enter', 'exit', or 'reduce', got: {self.intent_type}")
        if self.notional_risk_pct <= 0:
            raise ValueError(f"notional_risk_pct must be positive, got: {self.notional_risk_pct}")


def evaluate_intent(trade_intent: TradeIntent) -> RiskDecision:
    """
    Evaluate a TradeIntent against risk rules and return a RiskDecision.
    
    This function implements the core risk evaluation logic:
    1. Check system halt state
    2. Check market data freshness
    3. Calculate current portfolio exposure
    4. Calculate pending intents exposure
    5. Check portfolio exposure limit
    6. Check per-strategy risk limit
    7. Default to fail-closed (reject if uncertain)
    
    Args:
        trade_intent: The TradeIntent to evaluate
        
    Returns:
        RiskDecision with approved=True if all checks pass, approved=False otherwise
        
    Notes:
        - Fail-closed behavior: If any check fails or data is unavailable,
          the intent is rejected with an appropriate reason.
        - evaluated_portfolio_risk reflects the state *after* this intent
          would be applied (includes pending intents and this intent).
    """
    # Generate intent_id for traceability
    intent_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    logger.info(
        f"Evaluating TradeIntent: strategy_id={trade_intent.strategy_id}, "
        f"symbol={trade_intent.symbol}, side={trade_intent.side}, "
        f"intent_type={trade_intent.intent_type}, notional_risk_pct={trade_intent.notional_risk_pct}"
    )
    
    # 1. Check system halt state (fail-closed: reject if halted)
    if is_halted():
        logger.warning(f"TradeIntent rejected: system is halted")
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason="system_halted",
            evaluated_portfolio_risk=0.0,  # Cannot evaluate if halted
            timestamp=timestamp,
        )
    
    # 2. Check market data freshness (fail-closed: reject if stale)
    is_fresh, market_data_reason = check_market_data_freshness(trade_intent.symbol)
    if not is_fresh:
        logger.warning(f"TradeIntent rejected: {market_data_reason}")
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason=market_data_reason,
            evaluated_portfolio_risk=0.0,  # Cannot evaluate if market data is stale
            timestamp=timestamp,
        )
    
    # 3. Get current portfolio exposure
    try:
        current_exposure = get_portfolio_exposure()
    except Exception as e:
        logger.error(f"Failed to get portfolio exposure: {e}. Rejecting (fail-closed).")
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason="stale_market_data",  # Generic failure reason
            evaluated_portfolio_risk=0.0,
            timestamp=timestamp,
        )
    
    # 4. Get pending intents exposure
    try:
        pending_exposure = get_pending_intents_exposure()
    except Exception as e:
        logger.error(f"Failed to get pending intents exposure: {e}. Rejecting (fail-closed).")
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason="stale_market_data",  # Generic failure reason
            evaluated_portfolio_risk=current_exposure,
            timestamp=timestamp,
        )
    
    # 5. Get strategy current exposure
    try:
        strategy_exposure = get_strategy_current_exposure(trade_intent.strategy_id)
    except Exception as e:
        logger.error(f"Failed to get strategy exposure: {e}. Rejecting (fail-closed).")
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason="stale_market_data",  # Generic failure reason
            evaluated_portfolio_risk=current_exposure + pending_exposure,
            timestamp=timestamp,
        )
    
    # Calculate total exposure after this intent
    intent_risk = trade_intent.notional_risk_pct
    total_exposure_after = current_exposure + pending_exposure + intent_risk
    
    # 6. Check portfolio exposure limit
    is_within_portfolio_limit, portfolio_reason = check_portfolio_limit(
        current_exposure, pending_exposure, intent_risk
    )
    if not is_within_portfolio_limit:
        logger.warning(
            f"TradeIntent rejected: {portfolio_reason}. "
            f"Current exposure: {current_exposure}%, "
            f"Pending: {pending_exposure}%, "
            f"Intent risk: {intent_risk}%, "
            f"Total after: {total_exposure_after}%"
        )
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason=portfolio_reason,
            evaluated_portfolio_risk=total_exposure_after,
            timestamp=timestamp,
        )
    
    # 7. Check per-strategy risk limit
    is_within_strategy_limit, strategy_reason = check_strategy_limit(
        trade_intent.strategy_id, strategy_exposure, intent_risk
    )
    if not is_within_strategy_limit:
        logger.warning(
            f"TradeIntent rejected: {strategy_reason}. "
            f"Strategy {trade_intent.strategy_id} current exposure: {strategy_exposure}%, "
            f"Intent risk: {intent_risk}%"
        )
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason=strategy_reason,
            evaluated_portfolio_risk=total_exposure_after,
            timestamp=timestamp,
        )
    
    # All checks passed - approve the intent
    logger.info(
        f"TradeIntent approved. "
        f"Portfolio exposure after: {total_exposure_after}%, "
        f"Strategy exposure after: {strategy_exposure + intent_risk}%"
    )
    return RiskDecision(
        intent_id=intent_id,
        approved=True,
        rejection_reason=None,
        evaluated_portfolio_risk=total_exposure_after,
        timestamp=timestamp,
    )
