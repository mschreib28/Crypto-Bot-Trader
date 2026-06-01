"""Health check endpoints."""

import logging
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.db import get_engine
from backend.ingestor.health import check_ingestor_health, check_websocket_health
from backend.redis import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter()

# Service start time for uptime calculation
_SERVICE_START_TIME: float = time.time()


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
            ingestor_status, symbols_count = check_ingestor_health(client)
            ws_status, ws_last_message = check_websocket_health(client)
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
