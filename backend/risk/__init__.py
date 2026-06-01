"""Risk Manager module for evaluating TradeIntents against risk rules."""

from backend.risk.evaluator import evaluate_intent, TradeIntent
from backend.risk.models import RiskDecision
from backend.risk.exposure import get_portfolio_exposure, calculate_portfolio_exposure
from backend.risk.portfolio import (
    get_current_equity,
    get_open_positions,
    get_open_positions_value,
    get_pending_approved_intents,
    get_pending_intents_exposure,
    get_daily_pnl,
)
from backend.risk.cache import (
    get_cached_exposure,
    update_exposure_cache,
    get_portfolio_exposure_cached,
    clear_exposure_cache,
)
from backend.risk.limits import (
    MAX_BUDGET,
    MAX_TRADE_SIZE,
    MIN_TRADE_SIZE,
    DAILY_LOSS_LIMIT,
    calculate_intent_value,
    check_budget_limit,
    check_daily_loss_limit,
    get_current_exposure_dollars,
)
# 2% Risk Rule (Tickets 33, 34)
from backend.risk.two_percent import TwoPercentRule
from backend.risk.account import AccountTracker, AccountState
from backend.risk.sizing import PositionSizer, PositionSize
from backend.risk.micro_mode import (
    is_micro_mode,
    check_min_stop_distance,
    check_min_notional,
    check_max_positions,
    check_entry_position_limits,
    get_micro_mode_status,
)

__all__ = [
    "evaluate_intent",
    "TradeIntent",
    "RiskDecision",
    "get_portfolio_exposure",
    "calculate_portfolio_exposure",
    "get_current_equity",
    "get_open_positions",
    "get_open_positions_value",
    "get_pending_approved_intents",
    "get_pending_intents_exposure",
    "get_daily_pnl",
    "get_cached_exposure",
    "update_exposure_cache",
    "get_portfolio_exposure_cached",
    "clear_exposure_cache",
    # Budget limits
    "MAX_BUDGET",
    "MAX_TRADE_SIZE",
    "MIN_TRADE_SIZE",
    "DAILY_LOSS_LIMIT",
    "calculate_intent_value",
    "check_budget_limit",
    "check_daily_loss_limit",
    "get_current_exposure_dollars",
    # 2% Risk Rule (Tickets 33, 34)
    "TwoPercentRule",
    "AccountTracker",
    "AccountState",
    "PositionSizer",
    "PositionSize",
    # Micro mode
    "is_micro_mode",
    "check_min_stop_distance",
    "check_min_notional",
    "check_max_positions",
    "check_entry_position_limits",
    "get_micro_mode_status",
]
