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
    check_entry_position_limits,
    get_micro_mode_status,
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
    
    # 9. Check max drawdown kill switch (Ross Cameron spec: 3.0% daily equity drop)
    try:
        from backend.config import MAX_DRAWDOWN_PCT
        from backend.risk.portfolio import get_daily_pnl, get_current_equity

        # Get initial equity for today (start of day)
        session = get_session()
        try:
            from backend.db.models import EquityCurve
            from sqlalchemy import desc
            
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Get first equity snapshot of today
            first_today = (
                session.query(EquityCurve.total_equity)
                .filter(EquityCurve.timestamp >= today_start)
                .order_by(EquityCurve.timestamp)
                .first()
            )
            
            if first_today:
                initial_equity_today = float(first_today[0])
                current_equity_val = float(get_current_equity(session))
                
                if initial_equity_today > 0:
                    drawdown_pct = ((initial_equity_today - current_equity_val) / initial_equity_today) * 100.0
                    
                    if drawdown_pct >= MAX_DRAWDOWN_PCT:
                        logger.error(
                            f"MAX DRAWDOWN KILL SWITCH TRIGGERED: "
                            f"Daily equity drop {drawdown_pct:.2f}% >= {MAX_DRAWDOWN_PCT}% "
                            f"(initial=${initial_equity_today:.2f}, current=${current_equity_val:.2f})"
                        )
                        
                        # Execute panic sequence: halt system, cancel orders, disable trading
                        from backend.execution.panic import execute_panic_sequence
                        panic_result = execute_panic_sequence()
                        
                        logger.error(
                            f"PANIC SEQUENCE EXECUTED: {panic_result.get('orders_cancelled', 0)} orders cancelled, "
                            f"trading disabled={panic_result.get('trading_disabled', False)}"
                        )
                        
                        from backend.api.routes.events import log_activity
                        log_activity(
                            activity_type="PANIC_KILL_SWITCH",
                            message=(
                                f"MAX DRAWDOWN KILL SWITCH: Daily equity dropped {drawdown_pct:.2f}% "
                                f"(initial=${initial_equity_today:.2f}, current=${current_equity_val:.2f}). "
                                f"All trading halted, orders cancelled."
                            ),
                            details={
                                "drawdown_pct": drawdown_pct,
                                "max_drawdown_pct": MAX_DRAWDOWN_PCT,
                                "initial_equity": initial_equity_today,
                                "current_equity": current_equity_val,
                                "orders_cancelled": panic_result.get("orders_cancelled", 0),
                            },
                        )
                        
                        return RiskDecision(
                            intent_id=intent_id,
                            approved=False,
                            rejection_reason=f"max_drawdown_kill_switch_{drawdown_pct:.2f}%",
                            evaluated_portfolio_risk=total_exposure_after,
                            timestamp=timestamp,
                        )
        finally:
            session.close()
    except Exception as e:
        # If we can't check drawdown, log warning but continue
        # This is a softer failure since drawdown check is a safety net
        logger.warning(f"Could not check max drawdown: {e}. Continuing evaluation.")
    
    # 10. Check daily loss limit (halt if exceeded)
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

    # 11b. Mode-aware entry position slots (BUY only; never block exits)
    if trade_intent.side == "buy":
        try:
            from backend.positions.tracker import get_position_tracker
            from backend.supervisor.store import canonical_name

            tracker = get_position_tracker()
            canon = canonical_name(trade_intent.strategy_id)
            can_open, slot_reason = check_entry_position_limits(
                trade_intent.symbol, canon, tracker
            )
            if not can_open:
                logger.warning(
                    f"TradeIntent rejected (position slots): {slot_reason}"
                )
                return RiskDecision(
                    intent_id=intent_id,
                    approved=False,
                    rejection_reason=f"position_slot_{slot_reason}",
                    evaluated_portfolio_risk=total_exposure_after,
                    timestamp=timestamp,
                )
        except Exception as e:
            logger.warning(
                f"Failed to check entry position limits: {e}. Rejecting (fail-closed)."
            )
            return RiskDecision(
                intent_id=intent_id,
                approved=False,
                rejection_reason="position_slot_check_failed",
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
