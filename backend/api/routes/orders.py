"""Orders endpoint."""

import logging
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import Optional

from backend.db import get_session
from backend.db.models import Order
from backend.api.models import OrderItem, OrderList

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/orders", summary="List recent orders", response_model=OrderList)
async def list_orders(
    limit: int = Query(default=20, ge=1, le=100, description="Maximum number of orders to return")
):
    """
    List recent orders.
    
    Returns recent executed orders from the database, ordered by executed_at descending.
    Includes strategy_id if the order was placed by a strategy.
    Returns an empty array if no orders exist.
    """
    session: Session = get_session()
    try:
        # Query orders with eager loading of signal relationship to get strategy_id
        orders = (
            session.query(Order)
            .options(joinedload(Order.signal))
            .order_by(Order.executed_at.desc().nullslast())
            .limit(limit)
            .all()
        )
        
        # Convert to response models
        order_items = []
        for order in orders:
            # Get strategy_id from signal if available
            strategy_id: Optional[str] = None
            if order.signal is not None:
                strategy_id = str(order.signal.strategy_id)
            
            order_items.append(
                OrderItem(
                    id=str(order.id),
                    symbol=order.symbol,
                    side=order.side,
                    quantity=float(order.quantity),
                    price=float(order.executed_price),
                    status=order.status,
                    strategy_id=strategy_id,
                    executed_at=order.executed_at,
                )
            )
        
        return OrderList(orders=order_items)
        
    except Exception as e:
        logger.error(f"Error fetching orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching orders")
    finally:
        session.close()
