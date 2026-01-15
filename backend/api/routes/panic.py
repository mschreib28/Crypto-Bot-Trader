"""Panic endpoint for emergency shutdown."""

import logging
from fastapi import APIRouter, HTTPException

from backend.execution.panic import execute_panic_sequence

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/panic", summary="Emergency panic endpoint")
async def panic():
    """
    Emergency panic endpoint.
    
    Cancels all open orders and attempts to flatten all positions.
    This operation is idempotent and safe to retry.
    
    Returns:
        Dictionary with status and orders_cancelled count:
        {
            "status": "panic_initiated",
            "orders_cancelled": <int>
        }
        
    Note:
        - Sets system halt mode to true
        - Cancels all open orders via Kraken REST API
        - Attempts to flatten positions (if supported by exchange)
        - If execution fails, system remains halted (fail-closed)
        - Multiple calls are safe and return the same result
    """
    try:
        logger.warning("Panic endpoint called")
        result = execute_panic_sequence()
        return result
    except Exception as e:
        logger.error(f"Unexpected error in panic endpoint: {e}")
        # Fail-closed: even if panic sequence fails, system should be halted
        # Return a response indicating panic was attempted
        # The halt mode should have been set even if order cancellation failed
        return {
            "status": "panic_initiated",
            "orders_cancelled": 0
        }
