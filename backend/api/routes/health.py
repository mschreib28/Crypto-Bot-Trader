"""Health check endpoints."""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.db import get_engine
from backend.redis import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter()

# Service start time for uptime calculation
_SERVICE_START_TIME: float = time.time()

# Redis keys
INGESTOR_HEARTBEAT_KEY = "ingestor:heartbeat"
INGESTOR_SYMBOLS_KEY = "ingestor:symbols_count"

# Ingestor heartbeat max age (seconds)
INGESTOR_HEARTBEAT_MAX_AGE_SECONDS = 60


# --- Response Models ---


class RedisHealth(BaseModel):
    """Redis component health."""
    status: str = Field(..., description="Redis connection status")
    latency_ms: float = Field(..., description="Redis ping latency in milliseconds")


class DatabaseHealth(BaseModel):
    """Database component health."""
    status: str = Field(..., description="Database connection status")
    latency_ms: float = Field(..., description="Database query latency in milliseconds")


class IngestorHealth(BaseModel):
    """Ingestor component health."""
    status: str = Field(..., description="Ingestor service status")
    symbols_count: int = Field(..., description="Number of symbols being ingested")


class WebSocketHealth(BaseModel):
    """Kraken WebSocket data feed health (via ingestor)."""
    status: str = Field(..., description="Kraken data feed status: connected, stale, disconnected, error")
    last_message: str = Field(..., description="Last ingestor heartbeat timestamp or N/A")


class HealthComponents(BaseModel):
    """Health status of all components."""
    redis: RedisHealth
    database: DatabaseHealth
    ingestor: IngestorHealth
    websocket: WebSocketHealth


class HealthDetailedResponse(BaseModel):
    """Detailed health check response."""
    status: str = Field(..., description="Overall system health status")
    components: HealthComponents
    uptime_seconds: float = Field(..., description="System uptime in seconds")


# --- Health Check Functions ---


def _check_redis_health() -> tuple[str, float]:
    """
    Check Redis connectivity and measure latency.
    
    Returns:
        Tuple of (status, latency_ms)
    """
    try:
        client = get_redis_client()
        start = time.perf_counter()
        client.ping()
        latency_ms = (time.perf_counter() - start) * 1000
        return ("connected", round(latency_ms, 2))
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")
        return ("disconnected", 0.0)


def _check_database_health() -> tuple[str, float]:
    """
    Check database connectivity and measure latency.
    
    Returns:
        Tuple of (status, latency_ms)
    """
    try:
        engine = get_engine()
        start = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000
        return ("connected", round(latency_ms, 2))
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return ("disconnected", 0.0)


def _check_ingestor_health(redis_client) -> tuple[str, int]:
    """
    Check ingestor status via Redis heartbeat.
    
    Returns:
        Tuple of (status, symbols_count)
    """
    try:
        # Always get symbols count (even if heartbeat is missing)
        symbols_count = _get_ingestor_symbols_count(redis_client)
        
        heartbeat = redis_client.get(INGESTOR_HEARTBEAT_KEY)
        
        if heartbeat is None:
            # No heartbeat key - check if there are active streams as indicator
            if symbols_count > 0:
                # Streams exist, so ingestor is likely running but not publishing heartbeat
                return ("running", symbols_count)
            return ("unknown", symbols_count)
        
        # Parse timestamp and check age
        heartbeat_time = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_seconds = (now - heartbeat_time).total_seconds()
        
        if age_seconds > INGESTOR_HEARTBEAT_MAX_AGE_SECONDS:
            status = "stale"
        else:
            status = "running"
        
        return (status, symbols_count)
    except Exception as e:
        logger.warning(f"Ingestor health check failed: {e}")
        return ("error", 0)


def _get_ingestor_symbols_count(redis_client) -> int:
    """
    Get the number of symbols being ingested.
    
    Tries dedicated key first, then falls back to counting market:raw:* keys.
    """
    try:
        # Try dedicated key first
        count = redis_client.get(INGESTOR_SYMBOLS_KEY)
        if count is not None:
            return int(count)
        
        # Fallback: count market:raw:* stream keys
        keys = redis_client.keys("market:raw:*")
        return len(keys) if keys else 0
    except Exception as e:
        logger.warning(f"Failed to get ingestor symbols count: {e}")
        return 0


def _check_websocket_health(redis_client) -> tuple[str, str]:
    """
    Check Kraken WebSocket data feed status via ingestor heartbeat.
    
    This checks whether the ingestor is actively receiving market data
    from Kraken WebSocket, NOT the frontend WS client connections.
    
    Returns:
        Tuple of (status, last_heartbeat_timestamp)
        - "connected" if ingestor heartbeat is fresh (within 60s)
        - "stale" if heartbeat exists but is older than 60s
        - "disconnected" if no heartbeat found
    """
    try:
        heartbeat = redis_client.get(INGESTOR_HEARTBEAT_KEY)
        
        if heartbeat is None:
            return ("disconnected", "N/A")
        
        # Parse timestamp and check age
        heartbeat_time = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_seconds = (now - heartbeat_time).total_seconds()
        
        if age_seconds > INGESTOR_HEARTBEAT_MAX_AGE_SECONDS:
            status = "stale"
        else:
            status = "connected"
        
        return (status, heartbeat)
    except Exception as e:
        logger.warning(f"WebSocket health check failed: {e}")
        return ("error", "N/A")


def _determine_overall_status(
    redis_status: str,
    db_status: str,
    ingestor_status: str,
    ws_status: str,
) -> str:
    """
    Determine overall system health status.
    
    Returns:
        "healthy" if all critical components up
        "degraded" if some non-critical components down
        "unhealthy" if critical components down
    """
    # Critical components: Redis and Database
    critical_down = redis_status != "connected" or db_status != "connected"
    
    if critical_down:
        return "unhealthy"
    
    # If ingestor is running, data is flowing - ws_status doesn't matter
    if ingestor_status == "running":
        return "healthy"
    
    # Ingestor issues or websocket problems when ingestor not confirmed running
    non_critical_issues = (
        ingestor_status not in ("running", "unknown") or
        ws_status in ("stale", "disconnected", "error")
    )
    
    if non_critical_issues:
        return "degraded"
    
    return "healthy"


# --- Endpoints ---


@router.get("/health", summary="Health check")
async def health_check():
    """
    Health check endpoint.
    
    Returns the health status of the API service.
    """
    return {"status": "healthy"}


@router.get(
    "/health/detailed",
    summary="Detailed health check with component status",
    response_model=HealthDetailedResponse,
)
async def health_detailed() -> HealthDetailedResponse:
    """
    Detailed health check with component status.
    
    Returns detailed health status including individual component health,
    latency metrics, and system uptime.
    """
    # Check Redis first (needed for other checks)
    redis_status, redis_latency = _check_redis_health()
    
    # Check database
    db_status, db_latency = _check_database_health()
    
    # Check ingestor and websocket (require Redis)
    if redis_status == "connected":
        try:
            client = get_redis_client()
            ingestor_status, symbols_count = _check_ingestor_health(client)
            ws_status, ws_last_message = _check_websocket_health(client)
        except Exception as e:
            logger.error(f"Error checking Redis-dependent health: {e}")
            ingestor_status, symbols_count = ("error", 0)
            ws_status, ws_last_message = ("error", "N/A")
    else:
        ingestor_status, symbols_count = ("unknown", 0)
        ws_status, ws_last_message = ("unknown", "N/A")
    
    # Calculate uptime
    uptime_seconds = round(time.time() - _SERVICE_START_TIME, 1)
    
    # Determine overall status
    overall_status = _determine_overall_status(
        redis_status, db_status, ingestor_status, ws_status
    )
    
    return HealthDetailedResponse(
        status=overall_status,
        components=HealthComponents(
            redis=RedisHealth(status=redis_status, latency_ms=redis_latency),
            database=DatabaseHealth(status=db_status, latency_ms=db_latency),
            ingestor=IngestorHealth(status=ingestor_status, symbols_count=symbols_count),
            websocket=WebSocketHealth(status=ws_status, last_message=ws_last_message),
        ),
        uptime_seconds=uptime_seconds,
    )
