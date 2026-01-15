"""Strategies endpoint."""

import logging
from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_session
from backend.db.models import Strategy
from backend.api.models import StrategyList, StrategyItem

logger = logging.getLogger(__name__)

router = APIRouter()


def _map_status(db_status: str) -> str:
    """
    Map database status to API contract status.
    
    Database has: active, inactive, paused
    API contract expects: active, paused, stopped
    Maps 'inactive' -> 'stopped'
    """
    status_map = {
        "active": "active",
        "inactive": "stopped",
        "paused": "paused",
    }
    return status_map.get(db_status, db_status)


@router.get("/strategies", summary="List registered strategies")
async def list_strategies():
    """
    List all registered strategies.
    
    Returns a list of all strategies from the strategies table,
    including their id, name, status, and created_at timestamp.
    """
    session: Session = get_session()
    try:
        # Query all strategies from the database
        strategies = session.query(Strategy).all()
        
        # Convert to response models
        strategy_items = [
            StrategyItem(
                id=str(strategy.id),
                name=strategy.name,
                status=_map_status(strategy.status),
                created_at=strategy.created_at,
            )
            for strategy in strategies
        ]
        
        return StrategyList(strategies=strategy_items)
        
    except Exception as e:
        logger.error(f"Error fetching strategies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching strategies")
    finally:
        session.close()
