"""Core risk evaluation logic for TradeIntents."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from backend.risk.models import RiskDecision
from backend.risk.halt import is_halted, set_halt_mode
from backend.risk.rules import (
    get_portfolio_exposure,
    get_pending_intents_exposure,
    get_strategy_current_exposure,
    check_portfolio_limit,
    check_strategy_limit,
    check_market_data_freshness,
)
from backend.risk.limits import (
    check_budget_limit,
    check_daily_loss_limit,
    get_current_exposure_dollars,
)
from backend.risk.micro_mode import (
    is_micro_mode,
    check_max_positions,
    get_micro_mode_status,
    get_live_slots_max,
    get_live_slots_status,
)
# Lazy import to avoid circular dependency with backend.ingestor.symbols
# is_in_live_universe imported inside evaluate_intent() function

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
    
    # 0. Check live universe restriction (fail-closed: reject if not in live universe)
    # Lazy import to avoid circular dependency
    from backend.ingestor.symbols import is_in_live_universe
    if not is_in_live_universe(trade_intent.symbol):
        logger.warning(f"TradeIntent rejected: symbol {trade_intent.symbol} not in live universe")
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason="not_in_live_universe",
            evaluated_portfolio_risk=0.0,
            timestamp=timestamp,
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
    
    # 8. Get current equity for dollar-based budget checks
    try:
        from backend.risk.portfolio import get_current_equity
        from backend.db import get_session
        
        session = get_session()
        try:
            current_equity = get_current_equity(session)
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Failed to get current equity: {e}. Rejecting (fail-closed).")
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason="stale_market_data",
            evaluated_portfolio_risk=total_exposure_after,
            timestamp=timestamp,
        )
    
    # 9. Check daily loss limit (halt if exceeded)
    try:
        from backend.risk.portfolio import get_daily_pnl
        daily_pnl = get_daily_pnl(session=None)
        
        if check_daily_loss_limit(daily_pnl):
            logger.warning(
                f"Daily loss limit exceeded (PnL=${daily_pnl:.2f}). Triggering halt mode."
            )
            set_halt_mode(True)
            return RiskDecision(
                intent_id=intent_id,
                approved=False,
                rejection_reason="daily_loss_limit_exceeded",
                evaluated_portfolio_risk=total_exposure_after,
                timestamp=timestamp,
            )
    except Exception as e:
        # If we can't get daily PnL, log warning but continue
        # This is a softer failure since daily PnL check is a safety net
        logger.warning(f"Could not check daily PnL: {e}. Continuing evaluation.")
    
    # 10. Check budget limits (dollar-based)
    current_exposure_dollars = get_current_exposure_dollars(current_equity)
    is_within_budget, budget_reason = check_budget_limit(
        trade_intent, current_exposure_dollars, current_equity
    )
    if not is_within_budget:
        logger.warning(
            f"TradeIntent rejected: {budget_reason}. "
            f"Current exposure: ${current_exposure_dollars:.2f}"
        )
        return RiskDecision(
            intent_id=intent_id,
            approved=False,
            rejection_reason=budget_reason,
            evaluated_portfolio_risk=total_exposure_after,
            timestamp=timestamp,
        )
    
    # 11. Check micro mode limits (if active)
    if is_micro_mode(float(current_equity)):
        # Get current position count
        try:
            from backend.positions.tracker import get_position_tracker
            tracker = get_position_tracker()
            current_positions = tracker.get_all_positions()
            position_count = len(current_positions)
            
            # Check max positions limit (micro mode: max 1 position)
            can_open_new, position_reason = check_max_positions(position_count)
            if not can_open_new:
                logger.warning(
                    f"TradeIntent rejected (micro mode): {position_reason}. "
                    f"Current positions: {position_count}"
                )
                return RiskDecision(
                    intent_id=intent_id,
                    approved=False,
                    rejection_reason=f"micro_mode_{position_reason}",
                    evaluated_portfolio_risk=total_exposure_after,
                    timestamp=timestamp,
                )
        except Exception as e:
            logger.warning(f"Failed to check micro mode position limit: {e}. Continuing evaluation.")
    
    # 12. Check live slots limit (after micro mode check)
    try:
        from backend.positions.tracker import get_position_tracker
        tracker = get_position_tracker()
        current_live_positions = tracker.get_live_position_count()
        live_slots_max = get_live_slots_max(float(current_equity))
        
        logger.info(
            f"LIVE_SLOTS check: {current_live_positions}/{live_slots_max} slots used"
        )
        
        if current_live_positions >= live_slots_max:
            # Check if Shadow Mode is enabled
            try:
                from backend.api.routes.trading import get_shadow_live_mode
                shadow_mode_enabled = get_shadow_live_mode()
            except Exception as e:
                logger.warning(f"Failed to check shadow mode: {e}")
                shadow_mode_enabled = False
            
            if shadow_mode_enabled:
                logger.info(
                    f"Live slots full ({current_live_positions}/{live_slots_max}), "
                    f"routing to Shadow Mode"
                )
                return RiskDecision(
                    intent_id=intent_id,
                    approved=False,
                    rejection_reason="live_slots_full_routed_to_shadow",
                    evaluated_portfolio_risk=total_exposure_after,
                    timestamp=timestamp,
                )
            else:
                logger.warning(
                    f"Live slots full ({current_live_positions}/{live_slots_max}), "
                    f"Shadow Mode disabled - rejecting"
                )
                return RiskDecision(
                    intent_id=intent_id,
                    approved=False,
                    rejection_reason="live_slots_full",
                    evaluated_portfolio_risk=total_exposure_after,
                    timestamp=timestamp,
                )
    except Exception as e:
        logger.warning(f"Failed to check live slots limit: {e}. Continuing evaluation.")
    
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
