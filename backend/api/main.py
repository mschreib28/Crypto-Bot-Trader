"""FastAPI application initialization."""

import asyncio
import logging
import signal
import sys
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import CORS_ORIGINS, LOG_LEVEL
from backend.api.routes import health, panic, strategies, signals, orders, status, positions, account, screener, trading, events, metrics, history, supervisor, analytics
from backend.api import websocket
from backend.positions.tracker import get_position_tracker, SYNC_INTERVAL_SECONDS
from backend.positions.monitor import PositionMonitor
from backend.screener.service import ScreenerService
from backend.performance.monitor import get_performance_monitor

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Background task handle for periodic sync
_sync_task: Optional[asyncio.Task] = None

# Background task handle for daily risk recalculation
_risk_recalc_task: Optional[asyncio.Task] = None

# Screener service instance
_screener_service: Optional[ScreenerService] = None

# Position monitor service instance
_position_monitor: Optional[PositionMonitor] = None

# Performance monitor service instance
_performance_monitor = None

# Shutdown state
_shutdown_requested = False

# Create FastAPI app
app = FastAPI(
    title="Omni-Bot API",
    version="0.1.0",
    description="API for the Omni-Bot Trading Platform",
)

# Configure JSON encoder to include None values
from fastapi.encoders import jsonable_encoder
import json as json_module

# Override default JSON encoder to include None values
def custom_json_encoder(obj):
    """Custom JSON encoder that includes None values."""
    return json_module.dumps(obj, default=str, ensure_ascii=False)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(health.router, prefix="/api/v1", tags=["System"])
app.include_router(status.router, prefix="/api/v1", tags=["System"])
app.include_router(panic.router, prefix="/api/v1", tags=["System"])
app.include_router(strategies.router, prefix="/api/v1", tags=["Strategies"])
app.include_router(signals.router, prefix="/api/v1", tags=["Signals"])
app.include_router(orders.router, prefix="/api/v1", tags=["Orders"])
app.include_router(positions.router, prefix="/api/v1", tags=["Positions"])
app.include_router(websocket.router, prefix="/api/v1", tags=["WebSocket"])
app.include_router(account.router, prefix="/api/v1", tags=["Account"])
app.include_router(screener.router, prefix="/api/v1", tags=["Screener"])
app.include_router(trading.router, prefix="/api/v1", tags=["Trading"])
app.include_router(metrics.router, prefix="/api/v1", tags=["Strategies"])
app.include_router(events.router, prefix="/api/v1", tags=["Events"])
app.include_router(history.router, prefix="/api/v1", tags=["History"])
app.include_router(supervisor.router, prefix="/api/v1", tags=["Supervisor"])
app.include_router(analytics.router, prefix="/api/v1", tags=["Analytics"])

logger.info("FastAPI application initialized")

# TODO SECURITY (CRIT-2): Authentication middleware is required before live trading.
#   All private API routes must be protected. Consider FastAPI dependency injection
#   with JWT/API-key validation, or an external reverse-proxy auth layer (e.g. nginx
#   with basic auth). No real order should ever be reachable without authentication.
#
# TODO SECURITY (CRIT-3): Rate limiting middleware is required before live trading.
#   Without rate limiting, the API is vulnerable to abuse and accidental order floods.
#   Consider slowapi (Starlette rate-limiter) or a gateway-level solution.


def _signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT for graceful shutdown."""
    global _shutdown_requested
    if _shutdown_requested:
        logger.warning("Shutdown already in progress, forcing exit...")
        sys.exit(1)
    _shutdown_requested = True
    logger.info("Received shutdown signal, cleaning up...")


# Register signal handlers
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


async def _periodic_kraken_sync():
    """
    Background task that syncs positions from Kraken every 10 seconds (default).
    
    Runs indefinitely until cancelled.
    Updates positions from Kraken to keep local state in sync with exchange.
    Provides near-real-time awareness for stops, fills, and risk limits.
    
    In shadow mode, sync is skipped to prevent real exchange positions from
    interfering with simulated shadow trading positions.
    """
    while True:
        try:
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)
            
            # Check shadow mode before syncing
            try:
                from backend.api.routes.trading import get_shadow_live_mode
                if get_shadow_live_mode():
                    logger.debug("Skipping periodic Kraken sync (shadow mode active)")
                    continue
            except Exception as e:
                logger.warning(f"Failed to check shadow mode, proceeding with sync: {e}")
            
            logger.info(f"Running periodic Kraken position sync (every {SYNC_INTERVAL_SECONDS}s)")
            tracker = get_position_tracker()
            result = await tracker.sync_from_kraken()
            logger.info(f"Periodic sync result: {result}")
        except asyncio.CancelledError:
            logger.info("Periodic Kraken sync task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in periodic Kraken sync: {e}", exc_info=True)
            # Continue running despite errors


async def _daily_risk_recalculation():
    """
    Background task that recalculates risk capital daily at midnight UTC.
    
    Risk capital = current_equity × RISK_PCT_PER_TRADE (default 2%)
    This ensures Scout sizing adjusts to maintain consistent risk per trade
    as account equity changes.
    
    Runs indefinitely until cancelled.
    """
    from datetime import datetime, timezone, timedelta
    from backend.risk.account import AccountTracker
    from backend.redis import get_redis_client
    from backend.redis.keys import RISK_CAPITAL_UPDATED_KEY
    
    # Run initial recalculation on startup
    try:
        account_tracker = AccountTracker()
        account_tracker.recalculate_risk_capital()
        logger.info("Initial risk capital recalculation completed on startup")
    except Exception as e:
        logger.error(f"Failed initial risk capital recalculation: {e}", exc_info=True)
    
    while True:
        try:
            # Calculate seconds until next midnight UTC
            now = datetime.now(timezone.utc)
            # Next midnight UTC
            next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            seconds_until_midnight = (next_midnight - now).total_seconds()
            
            logger.info(
                f"Daily risk recalculation scheduled for {next_midnight.isoformat()} "
                f"(in {seconds_until_midnight/3600:.1f} hours)"
            )
            
            # Sleep until midnight UTC
            await asyncio.sleep(seconds_until_midnight)
            
            # Recalculate risk capital
            try:
                account_tracker = AccountTracker()
                account_tracker.recalculate_risk_capital()
                logger.info("Daily risk capital recalculation completed")
            except Exception as e:
                logger.error(f"Failed daily risk capital recalculation: {e}", exc_info=True)
                # Continue running despite errors
            
        except asyncio.CancelledError:
            logger.info("Daily risk recalculation task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in daily risk recalculation task: {e}", exc_info=True)
            # Sleep 1 hour before retrying on error
            await asyncio.sleep(3600)


@app.on_event("startup")
async def startup_event():
    """
    Startup event handler.
    
    - Syncs positions from Kraken on startup
    - Starts periodic sync background task
    - Starts daily risk capital recalculation task
    - Starts position monitor service (P&L updates)
    - Starts screener background service
    """
    global _sync_task, _risk_recalc_task, _screener_service, _position_monitor
    
    logger.info("API server starting up")

    try:
        from backend.startup.validation import run_startup_validation

        run_startup_validation()
    except Exception as e:
        logger.error(f"Startup validation failed: {e}", exc_info=True)

    # Start persistent audit writer (PostgreSQL background thread)
    try:
        from backend.db.audit import init_audit_writer
        init_audit_writer()
        logger.info("Audit writer started")
    except Exception as e:
        logger.error(f"Failed to start audit writer: {e}", exc_info=True)
    
    # Sync positions from Kraken on startup (skip in shadow mode)
    try:
        from backend.api.routes.trading import get_shadow_live_mode
        if not get_shadow_live_mode():
            logger.info("Running initial Kraken position sync...")
            tracker = get_position_tracker()
            result = await tracker.sync_from_kraken()
            logger.info(f"Initial Kraken sync complete: {result}")
        else:
            logger.info("Skipping initial Kraken sync (shadow mode active)")
    except Exception as e:
        logger.error(f"Failed to sync positions on startup: {e}", exc_info=True)
    
    # Start periodic sync task
    _sync_task = asyncio.create_task(_periodic_kraken_sync())
    logger.info(f"Started periodic Kraken sync task (interval={SYNC_INTERVAL_SECONDS}s)")
    
    # Start daily risk capital recalculation task
    _risk_recalc_task = asyncio.create_task(_daily_risk_recalculation())
    logger.info("Started daily risk capital recalculation task (runs at midnight UTC)")
    
    # Start position monitor service (P&L updates)
    logger.info("Starting position monitor service...")
    try:
        _position_monitor = PositionMonitor()
        await _position_monitor.start()
        logger.info("Position monitor service started")
    except Exception as e:
        logger.error(f"Failed to start position monitor: {e}", exc_info=True)
    
    # Start performance monitor service
    logger.info("Starting performance monitor service...")
    try:
        _performance_monitor = get_performance_monitor()
        await _performance_monitor.start()
        logger.info("Performance monitor service started")
    except Exception as e:
        logger.error(f"Failed to start performance monitor: {e}", exc_info=True)
    
    # Start screener background service
    logger.info("Starting screener background service...")
    try:
        from backend.runner.config import SCREENER_INTERVAL_SECONDS
        _screener_service = ScreenerService(scan_interval_seconds=SCREENER_INTERVAL_SECONDS)
        await _screener_service.start()
        
        logger.info(f"Screener background service started (interval={SCREENER_INTERVAL_SECONDS}s)")
    except Exception as e:
        logger.error(f"Failed to start screener service: {e}", exc_info=True)


@app.on_event("shutdown")
async def shutdown_event():
    """
    Shutdown event handler.
    
    - Cancels periodic sync task
    - Cancels daily risk recalculation task
    - Stops position monitor service
    - Stops screener background service
    """
    global _sync_task, _risk_recalc_task, _screener_service, _position_monitor
    
    logger.info("API server shutting down")
    
    # Cancel periodic sync task
    if _sync_task is not None:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        logger.info("Periodic Kraken sync task stopped")
    
    # Cancel daily risk recalculation task
    if _risk_recalc_task is not None:
        _risk_recalc_task.cancel()
        try:
            await _risk_recalc_task
        except asyncio.CancelledError:
            pass
        logger.info("Daily risk recalculation task stopped")
    
    # Stop position monitor service
    if _position_monitor is not None:
        await _position_monitor.stop()
        logger.info("Position monitor service stopped")
    
    # Stop performance monitor service
    if _performance_monitor is not None:
        await _performance_monitor.stop()
        logger.info("Performance monitor service stopped")
    
    # Stop screener service
    if _screener_service is not None:
        await _screener_service.stop()
        logger.info("Screener background service stopped")
    
    # Flush and stop audit writer
    try:
        from backend.db.audit import shutdown_audit_writer
        shutdown_audit_writer()
        logger.info("Audit writer stopped")
    except Exception as e:
        logger.error(f"Failed to stop audit writer: {e}", exc_info=True)

    logger.info("Shutdown complete")
