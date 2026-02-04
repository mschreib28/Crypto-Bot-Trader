"""Signals endpoint."""

import logging
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List

from backend.db import get_session
from backend.db.models import Signal
from backend.api.models import SignalItem

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/signals", summary="List recent signals", response_model=List[SignalItem])
async def list_signals(
    limit: int = Query(default=50, ge=1, le=100, description="Maximum number of signals to return")
):
    """
    List recent signals.
    
    Returns an array of recent signals from the database, ordered by created_at descending.
    Returns an empty array if no signals exist.
    """
    session: Session = get_session()
    try:
        # Query signals ordered by created_at descending
        signals = (
            session.query(Signal)
            .order_by(Signal.created_at.desc())
            .limit(limit)
            .all()
        )
        
        # Convert to response models
        return [
            SignalItem(
                id=str(signal.id),
                strategy_id=str(signal.strategy_id),
                symbol=signal.symbol,
                side=signal.side,
                intent_type=signal.intent_type,
                status=signal.status,
                created_at=signal.created_at,
            )
            for signal in signals
        ]
        
    except Exception as e:
        logger.error(f"Error fetching signals: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching signals")
    finally:
        session.close()
