"""Portfolio state queries for risk management.

This module provides functions to query the current portfolio state:
- Current equity (from equity_curve)
- Open positions (from orders table)
- Pending approved intents (from signals table)
"""

import logging
from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from backend.db import get_session
from backend.db.models import Order, Signal, EquityCurve

logger = logging.getLogger(__name__)


def get_current_equity(session: Optional[Session] = None) -> Decimal:
    """
    Get the latest total equity from the equity_curve table.
    
    Args:
        session: Optional database session. If None, creates a new session.
        
    Returns:
        Latest total_equity value, or Decimal('0') if no data exists.
    """
    if session is None:
        session = get_session()
        try:
            return _get_current_equity_impl(session)
        finally:
            session.close()
    else:
        return _get_current_equity_impl(session)


def _get_current_equity_impl(session: Session) -> Decimal:
    """Internal implementation of get_current_equity."""
    latest_equity = (
        session.query(EquityCurve.total_equity)
        .order_by(desc(EquityCurve.timestamp))
        .first()
    )
    
    if latest_equity is None:
        logger.warning("No equity curve data found, defaulting to 0")
        return Decimal('0')
    
    return Decimal(str(latest_equity[0]))


def get_open_positions(session: Optional[Session] = None) -> List[Order]:
    """
    Get all open positions (executed orders that haven't been closed).
    
    For now, we consider all executed orders as open positions.
    In a more sophisticated system, we would track position netting
    (buy orders vs sell orders per symbol).
    
    Args:
        session: Optional database session. If None, creates a new session.
        
    Returns:
        List of Order objects with status='executed'.
    """
    if session is None:
        session = get_session()
        try:
            return _get_open_positions_impl(session)
        finally:
            session.close()
    else:
        return _get_open_positions_impl(session)


def _get_open_positions_impl(session: Session) -> List[Order]:
    """Internal implementation of get_open_positions."""
    open_orders = (
        session.query(Order)
        .filter(Order.status == 'executed')
        .all()
    )
    
    return open_orders


def get_open_positions_value(session: Optional[Session] = None) -> Decimal:
    """
    Calculate the total notional value of open positions.
    
    This is the sum of (executed_price * quantity) for all executed orders.
    This represents unrealized PnL exposure.
    
    Args:
        session: Optional database session. If None, creates a new session.
        
    Returns:
        Total notional value of open positions, or Decimal('0') if none.
    """
    if session is None:
        session = get_session()
        try:
            return _get_open_positions_value_impl(session)
        finally:
            session.close()
    else:
        return _get_open_positions_value_impl(session)


def _get_open_positions_value_impl(session: Session) -> Decimal:
    """Internal implementation of get_open_positions_value."""
    result = (
        session.query(func.sum(Order.executed_price * Order.quantity))
        .filter(Order.status == 'executed')
        .scalar()
    )
    
    if result is None:
        return Decimal('0')
    
    return Decimal(str(result))


def get_pending_approved_intents(session: Optional[Session] = None) -> List[Signal]:
    """
    Get all pending approved intents (signals with status='approved').
    
    These are TradeIntents that have been approved by the Risk Manager
    but not yet executed.
    
    Args:
        session: Optional database session. If None, creates a new session.
        
    Returns:
        List of Signal objects with status='approved'.
    """
    if session is None:
        session = get_session()
        try:
            return _get_pending_approved_intents_impl(session)
        finally:
            session.close()
    else:
        return _get_pending_approved_intents_impl(session)


def _get_pending_approved_intents_impl(session: Session) -> List[Signal]:
    """Internal implementation of get_pending_approved_intents."""
    approved_signals = (
        session.query(Signal)
        .filter(Signal.status == 'approved')
        .all()
    )
    
    return approved_signals


def get_pending_intents_exposure(
    total_equity: Decimal,
    session: Optional[Session] = None
) -> Decimal:
    """
    Calculate the total exposure from pending approved intents.
    
    This is the sum of notional_risk_pct for all approved intents,
    converted to absolute notional value based on total equity.
    
    Args:
        total_equity: Current total equity to use for calculation.
        session: Optional database session. If None, creates a new session.
        
    Returns:
        Total exposure from pending intents, or Decimal('0') if none.
    """
    if session is None:
        session = get_session()
        try:
            return _get_pending_intents_exposure_impl(total_equity, session)
        finally:
            session.close()
    else:
        return _get_pending_intents_exposure_impl(total_equity, session)


def _get_pending_intents_exposure_impl(
    total_equity: Decimal,
    session: Session
) -> Decimal:
    """Internal implementation of get_pending_intents_exposure."""
    if total_equity == 0:
        return Decimal('0')
    
    result = (
        session.query(func.sum(Signal.notional_risk_pct))
        .filter(Signal.status == 'approved')
        .scalar()
    )
    
    if result is None:
        return Decimal('0')
    
    # Convert percentage to absolute value
    risk_pct_sum = Decimal(str(result))
    exposure = (total_equity * risk_pct_sum) / Decimal('100')
    
    return exposure
