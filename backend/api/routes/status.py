"""System status endpoint."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from backend.api.models import SystemStatus
from backend.db import get_engine, get_session
from backend.db.models import Strategy
from backend.ingestor.health import is_ingestor_healthy
from backend.redis import get_redis_client
from backend.redis.keys import SYSTEM_HALT, PORTFOLIO_EXPOSURE_TOTAL

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_redis_connected() -> bool:
    """Check if Redis is reachable."""
    try:
        client = get_redis_client()
        client.ping()
        return True
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")
        return False


def _check_db_connected() -> bool:
    """Check if database connection is healthy."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return False


def _get_halted_status(client) -> bool:
    """Get system halt status from Redis."""
    try:
        value = client.get(SYSTEM_HALT)
        if value is None:
            return False
        # Treat "true", "1", or "True" as halted
        return str(value).lower() in ("true", "1")
    except Exception as e:
        logger.warning(f"Failed to read halt status: {e}")
        # Fail closed: assume halted if we can't read the status
        return True


def _get_portfolio_exposure(client) -> float:
    """Get portfolio exposure from Redis."""
    try:
        value = client.get(PORTFOLIO_EXPOSURE_TOTAL)
        if value is None:
            return 0.0
        return float(value)
    except Exception as e:
        logger.warning(f"Failed to read portfolio exposure: {e}")
        return 0.0


def _get_active_strategies_count() -> int:
    """Count active strategies from database."""
    session = None
    try:
        session = get_session()
        count = session.query(Strategy).filter(Strategy.status == "active").count()
        return count
    except Exception as e:
        logger.warning(f"Failed to count active strategies: {e}")
        return 0
    finally:
        if session:
            session.close()


@router.get("/status", summary="System status overview")
async def get_status() -> SystemStatus:
    """
    Get comprehensive system status.
    
    Returns system halt state, portfolio exposure, active strategies count,
    and health status of dependent services (Redis, DB, Ingestor).
    """
    # Check Redis connectivity first
    redis_connected = _check_redis_connected()
    
    # Get Redis-dependent values
    halted = False
    portfolio_exposure = 0.0
    ingestor_healthy = True
    
    if redis_connected:
        try:
            client = get_redis_client()
            halted = _get_halted_status(client)
            portfolio_exposure = _get_portfolio_exposure(client)
            ingestor_healthy = is_ingestor_healthy(client)
        except Exception as e:
            logger.error(f"Error fetching Redis data: {e}")
    
    # Check database and get active strategies count
    db_connected = _check_db_connected()
    active_strategies = _get_active_strategies_count() if db_connected else 0
    
    return SystemStatus(
        halted=halted,
        portfolio_exposure=portfolio_exposure,
        active_strategies=active_strategies,
        redis_connected=redis_connected,
        db_connected=db_connected,
        ingestor_healthy=ingestor_healthy,
        last_updated=datetime.now(timezone.utc),
    )
