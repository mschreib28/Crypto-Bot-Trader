"""Portfolio exposure calculation for risk management.

This module calculates the total portfolio exposure as a percentage of total equity.
Exposure includes:
- Open positions (unrealized PnL)
- Pending approved intents
"""

import logging
from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session

from backend.risk.portfolio import (
    get_current_equity,
    get_open_positions_value,
    get_pending_intents_exposure,
)

logger = logging.getLogger(__name__)


def calculate_portfolio_exposure(session: Optional[Session] = None) -> float:
    """
    Calculate total portfolio exposure as a percentage of total equity.
    
    Exposure = (open_positions_value + pending_intents_exposure) / total_equity * 100
    
    Args:
        session: Optional database session. If None, creates a new session.
        
    Returns:
        Portfolio exposure percentage (0-100), or 0.0 if equity is zero or missing.
    """
    if session is None:
        from backend.db import get_session
        session = get_session()
        try:
            return _calculate_portfolio_exposure_impl(session)
        finally:
            session.close()
    else:
        return _calculate_portfolio_exposure_impl(session)


def _calculate_portfolio_exposure_impl(session: Session) -> float:
    """Internal implementation of calculate_portfolio_exposure."""
    # Get current equity
    total_equity = get_current_equity(session)
    
    # Handle edge case: zero equity or missing data
    if total_equity == 0:
        logger.warning("Total equity is zero, returning 0% exposure")
        return 0.0
    
    # Get open positions value (unrealized PnL exposure)
    open_positions_value = get_open_positions_value(session)
    
    # Get pending approved intents exposure
    pending_intents_exposure = get_pending_intents_exposure(total_equity, session)
    
    # Calculate total exposure
    total_exposure = open_positions_value + pending_intents_exposure
    
    # Calculate exposure percentage
    exposure_pct = (total_exposure / total_equity) * Decimal('100')
    
    logger.debug(
        f"Portfolio exposure calculation: "
        f"equity={total_equity}, "
        f"open_positions={open_positions_value}, "
        f"pending_intents={pending_intents_exposure}, "
        f"total_exposure={total_exposure}, "
        f"exposure_pct={exposure_pct}%"
    )
    
    return float(exposure_pct)


def get_portfolio_exposure(session: Optional[Session] = None) -> float:
    """
    Get portfolio exposure percentage.
    
    This is a convenience wrapper around calculate_portfolio_exposure()
    that matches the expected API from the ticket requirements.
    
    Args:
        session: Optional database session. If None, creates a new session.
        
    Returns:
        Portfolio exposure percentage (0-100).
    """
    return calculate_portfolio_exposure(session)
