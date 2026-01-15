"""Persistence layer for Execution Engine.

Handles database writes for Fill objects and signal status updates.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db import get_session
from backend.db.models import Order, Signal
from backend.execution.models import Fill

logger = logging.getLogger(__name__)


def persist_fill(fill: Fill, signal_id: Optional[str] = None) -> bool:
    """
    Persist a Fill object to the orders table and update signal status.
    
    This function:
    - Inserts or updates the order record (idempotent via exchange_order_id)
    - Updates the associated signal status to "executed"
    - Uses a transaction to ensure atomicity
    - Handles errors gracefully (logs but doesn't raise)
    
    Args:
        fill: Fill object to persist
        signal_id: Optional UUID string of the signal to update.
                   If None, will attempt to find signal by matching order_id
                   or other criteria. If provided, should be the UUID from
                   RiskDecision.intent_id.
    
    Returns:
        True if persistence succeeded, False otherwise
    """
    session: Optional[Session] = None
    try:
        session = get_session()
        
        # Convert signal_id string to UUID if provided
        signal_uuid: Optional[uuid.UUID] = None
        if signal_id:
            try:
                signal_uuid = uuid.UUID(signal_id)
            except ValueError:
                logger.warning(f"Invalid signal_id format: {signal_id}, skipping signal update")
        
        # Parse timestamp from ISO8601 string
        try:
            executed_at = datetime.fromisoformat(fill.timestamp.replace('Z', '+00:00'))
        except ValueError as e:
            logger.error(f"Invalid timestamp format in Fill: {fill.timestamp}, error: {e}")
            executed_at = datetime.utcnow()
        
        # Upsert order (idempotent via exchange_order_id unique constraint)
        # First, try to find existing order by exchange_order_id
        existing_order = session.query(Order).filter(
            Order.exchange_order_id == fill.exchange_order_id
        ).first()
        
        if existing_order:
            # Update existing order (idempotency)
            logger.debug(f"Updating existing order with exchange_order_id: {fill.exchange_order_id}")
            existing_order.symbol = fill.symbol
            existing_order.side = fill.side
            existing_order.executed_price = fill.executed_price
            existing_order.quantity = fill.quantity
            existing_order.fees = fill.fees
            existing_order.slippage = fill.slippage
            existing_order.status = "executed"
            existing_order.executed_at = executed_at
            # Update signal_id if provided and different
            if signal_uuid and existing_order.signal_id != signal_uuid:
                existing_order.signal_id = signal_uuid
            order_id = existing_order.id
        else:
            # Create new order
            new_order = Order(
                signal_id=signal_uuid,
                symbol=fill.symbol,
                side=fill.side,
                executed_price=fill.executed_price,
                quantity=fill.quantity,
                fees=fill.fees,
                slippage=fill.slippage,
                exchange_order_id=fill.exchange_order_id,
                status="executed",
                executed_at=executed_at,
            )
            session.add(new_order)
            session.flush()  # Flush to get the ID
            order_id = new_order.id
            logger.debug(f"Created new order with exchange_order_id: {fill.exchange_order_id}")
        
        # Update signal status to "executed" if signal_id is provided
        if signal_uuid:
            signal = session.query(Signal).filter(Signal.id == signal_uuid).first()
            if signal:
                if signal.status != "executed":
                    signal.status = "executed"
                    logger.debug(f"Updated signal {signal_uuid} status to 'executed'")
                else:
                    logger.debug(f"Signal {signal_uuid} already has status 'executed'")
            else:
                logger.warning(f"Signal with id {signal_uuid} not found, cannot update status")
        
        # Commit transaction
        session.commit()
        logger.info(f"Successfully persisted Fill for order_id: {fill.order_id}, exchange_order_id: {fill.exchange_order_id}")
        return True
        
    except IntegrityError as e:
        # Handle unique constraint violations (shouldn't happen with upsert logic, but safe)
        if session:
            session.rollback()
        logger.error(f"Integrity error persisting Fill {fill.order_id}: {e}")
        return False
    except Exception as e:
        # Log error but don't fail execution (eventual consistency)
        if session:
            session.rollback()
        logger.error(f"Error persisting Fill {fill.order_id}: {e}", exc_info=True)
        return False
    finally:
        if session:
            session.close()


def persist_fill_with_intent_id(fill: Fill, intent_id: str) -> bool:
    """
    Convenience wrapper that uses intent_id (from RiskDecision) as signal_id.
    
    Args:
        fill: Fill object to persist
        intent_id: UUID string from RiskDecision.intent_id (maps to Signal.id)
    
    Returns:
        True if persistence succeeded, False otherwise
    """
    return persist_fill(fill, signal_id=intent_id)
