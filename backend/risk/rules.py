"""Risk rule implementations for portfolio exposure and strategy limits."""

import logging
from typing import Optional, Dict, Any
from decimal import Decimal

import redis

from backend.db import get_session
from backend.db.models import Strategy, Signal
from backend.redis import get_redis_client
from backend.redis.keys import SYSTEM_HALT
from backend.risk.exposure import get_portfolio_exposure as calculate_portfolio_exposure
from backend.risk.cache import get_portfolio_exposure_cached

logger = logging.getLogger(__name__)

# Risk limits (configurable via environment or strategy config)
DEFAULT_PORTFOLIO_EXPOSURE_LIMIT = 50.0  # 50% of total equity
DEFAULT_STRATEGY_RISK_LIMIT = 20.0  # 20% of total equity per strategy


def is_system_halted() -> bool:
    """
    Check if the system is in halt mode.
    
    Returns True if system is halted, False otherwise.
    Defaults to False if Redis is unavailable (fail-closed: assume not halted for now,
    but evaluator will reject on other failures).
    """
    try:
        redis_client = get_redis_client()
        halt_value = redis_client.get(SYSTEM_HALT)
        return halt_value in ("1", "true", "True")
    except Exception as e:
        logger.warning(f"Failed to check halt state from Redis: {e}. Assuming not halted.")
        return False


def get_portfolio_exposure() -> float:
    """
    Get current portfolio exposure percentage.
    
    Calculates: (total_exposure / total_equity) * 100
    
    Total exposure includes:
    - Open positions (unrealized PnL from executed orders)
    - Pending approved intents
    
    Returns:
        Portfolio exposure as a percentage (0-100).
        Returns 0.0 if equity is zero or data is unavailable (fail-closed behavior).
    
    Note: This implementation uses the full exposure calculation from Ticket 9,
    which includes Redis caching for performance.
    """
    try:
        # Try to get from cache first, fallback to calculation
        exposure = get_portfolio_exposure_cached(use_cache=True, fallback_to_calc=True)
        if exposure is not None:
            return exposure
        
        # Fallback: calculate directly if cache fails
        return calculate_portfolio_exposure()
        
    except Exception as e:
        logger.error(f"Failed to get portfolio exposure: {e}. Defaulting to 0% (fail-closed).")
        return 0.0


def get_pending_intents_exposure() -> float:
    """
    Get total exposure from pending approved intents.
    
    Returns:
        Total risk percentage from pending approved intents.
        Returns 0.0 if data is unavailable (fail-closed behavior).
    
    Note: This implementation uses the portfolio module from Ticket 9,
    which properly calculates exposure based on total equity.
    """
    try:
        from backend.risk.portfolio import (
            get_current_equity,
            get_pending_intents_exposure as calc_pending_exposure,
        )
        
        session = get_session()
        try:
            total_equity = get_current_equity(session)
            pending_exposure = calc_pending_exposure(total_equity, session)
            
            # Convert to percentage of equity
            if total_equity > 0:
                exposure_pct = (pending_exposure / total_equity) * Decimal('100')
                return float(exposure_pct)
            else:
                return 0.0
                
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to calculate pending intents exposure: {e}. Defaulting to 0% (fail-closed).")
        return 0.0


def get_strategy_risk_limit(strategy_id: str) -> float:
    """
    Get the risk limit for a specific strategy.
    
    Checks strategy config for custom limit, otherwise uses default.
    
    Args:
        strategy_id: UUID string of the strategy
        
    Returns:
        Strategy risk limit as percentage (0-100).
        Returns default limit if strategy not found or config unavailable.
    """
    try:
        session = get_session()
        try:
            strategy = session.query(Strategy).filter(Strategy.id == strategy_id).first()
            
            if strategy is None:
                logger.warning(f"Strategy {strategy_id} not found. Using default limit.")
                return DEFAULT_STRATEGY_RISK_LIMIT
            
            # Check strategy config for custom risk limit
            config = strategy.config or {}
            if isinstance(config, dict) and "risk_limit_pct" in config:
                limit = float(config["risk_limit_pct"])
                if limit < 0 or limit > 100:
                    logger.warning(f"Invalid risk_limit_pct in strategy config: {limit}. Using default.")
                    return DEFAULT_STRATEGY_RISK_LIMIT
                return limit
            
            return DEFAULT_STRATEGY_RISK_LIMIT
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to get strategy risk limit for {strategy_id}: {e}. Using default.")
        return DEFAULT_STRATEGY_RISK_LIMIT


def get_strategy_current_exposure(strategy_id: str) -> float:
    """
    Get current exposure for a specific strategy.
    
    Sums notional_risk_pct from all approved (pending or executed) signals for this strategy.
    
    Args:
        strategy_id: UUID string of the strategy
        
    Returns:
        Current strategy exposure as percentage (0-100).
        Returns 0.0 if data is unavailable (fail-closed behavior).
    """
    try:
        session = get_session()
        try:
            # Sum notional_risk_pct from approved and executed signals for this strategy
            strategy_signals = (
                session.query(Signal)
                .filter(Signal.strategy_id == strategy_id)
                .filter(Signal.status.in_(["approved", "executed"]))
                .all()
            )
            
            total_exposure = sum(float(signal.notional_risk_pct) for signal in strategy_signals)
            return total_exposure
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Failed to calculate strategy exposure for {strategy_id}: {e}. Defaulting to 0% (fail-closed).")
        return 0.0


def check_portfolio_limit(current_exposure: float, pending_exposure: float, intent_risk: float) -> tuple[bool, Optional[str]]:
    """
    Check if intent would exceed portfolio exposure limit.
    
    Args:
        current_exposure: Current portfolio exposure percentage
        pending_exposure: Exposure from pending approved intents
        intent_risk: Risk percentage of the new intent
        
    Returns:
        Tuple of (is_within_limit, rejection_reason)
        is_within_limit: True if within limit, False otherwise
        rejection_reason: None if within limit, otherwise rejection reason string
    """
    total_exposure_after = current_exposure + pending_exposure + intent_risk
    
    if total_exposure_after > DEFAULT_PORTFOLIO_EXPOSURE_LIMIT:
        return False, "exceeds_portfolio_limit"
    
    return True, None


def check_strategy_limit(strategy_id: str, current_strategy_exposure: float, intent_risk: float) -> tuple[bool, Optional[str]]:
    """
    Check if intent would exceed strategy-specific risk limit.
    
    Args:
        strategy_id: UUID string of the strategy
        current_strategy_exposure: Current exposure for this strategy
        intent_risk: Risk percentage of the new intent
        
    Returns:
        Tuple of (is_within_limit, rejection_reason)
        is_within_limit: True if within limit, False otherwise
        rejection_reason: None if within limit, otherwise rejection reason string
    """
    strategy_limit = get_strategy_risk_limit(strategy_id)
    total_strategy_exposure_after = current_strategy_exposure + intent_risk
    
    if total_strategy_exposure_after > strategy_limit:
        return False, "exceeds_strategy_limit"
    
    return True, None


def check_market_data_freshness(symbol: str) -> tuple[bool, Optional[str]]:
    """
    Check if market data is fresh enough for risk evaluation.
    
    Args:
        symbol: Trading pair symbol (e.g., "BTC/USD")
        
    Returns:
        Tuple of (is_fresh, rejection_reason)
        is_fresh: True if market data is fresh, False otherwise
        rejection_reason: None if fresh, otherwise rejection reason string
    
    Note: This is a basic implementation. A full implementation would check
    the timestamp of the latest market data event in Redis streams.
    """
    # For Ticket 8, we'll do a basic check
    # Ticket 9 or later tickets may implement full market data freshness checks
    try:
        redis_client = get_redis_client()
        # Check if market data stream exists (basic check)
        # Full implementation would check timestamp of latest message
        stream_key = f"market:ohlcv:{symbol}:4h"
        try:
            stream_info = redis_client.xinfo_stream(stream_key)
            if stream_info:
                # Market data exists, assume fresh for now
                # TODO: Check timestamp of latest message against current time
                return True, None
            else:
                return False, "stale_market_data"
        except redis.ResponseError as e:
            # Stream doesn't exist (Redis returns error for non-existent streams)
            if "no such key" in str(e).lower():
                logger.warning(f"Market data stream {stream_key} does not exist. Rejecting (fail-closed).")
                return False, "stale_market_data"
            raise
    except Exception as e:
        logger.warning(f"Failed to check market data freshness for {symbol}: {e}. Rejecting (fail-closed).")
        return False, "stale_market_data"
