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
)
from backend.risk.cache import (
    get_cached_exposure,
    update_exposure_cache,
    get_portfolio_exposure_cached,
    clear_exposure_cache,
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
    "get_cached_exposure",
    "update_exposure_cache",
    "get_portfolio_exposure_cached",
    "clear_exposure_cache",
]
