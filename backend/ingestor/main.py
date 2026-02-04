"""Entry point for the data ingestor service.

Orchestrates the full pipeline: Kraken WebSocket → Raw Ticks → OHLCV Bars → Redis Streams.
Runs both WebSocket client and normalizer concurrently in a single process.
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from backend.ingestor.config import (
    get_health_check_file,
    get_intervals,
    get_symbols,
    get_symbol_refresh_interval,
    get_universe_refresh_interval,
)
from backend.ingestor.kraken_ws import MultiConnectionManager
from backend.ingestor.normalizer import Normalizer
from backend.ingestor.symbols import (
    fetch_usd_pairs,
    fetch_top_usd_pairs_by_volume,
    get_dynamic_symbols,
    get_dynamic_symbols_by_rvol,
    get_dynamic_symbols_by_rvol_with_replacements,
    normalize_symbol,
    check_symbol_has_data,
    mark_symbol_failed,
    unmark_symbol_failed,
    get_failed_symbols,
    refresh_ticker_data,
    update_universe_with_hysteresis,
    fetch_symbols_by_rvol,
)
from backend.config import LOG_LEVEL
from backend.redis import get_redis_client
from backend.redis.keys import INGESTOR_ACTIVE_SYMBOLS_KEY

# Error recovery settings
MAX_CONSECUTIVE_ERRORS = 10
ERROR_COOLDOWN_SECONDS = 5 * 60  # 5 minutes

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


def _publish_active_symbols(symbols: List[str]) -> None:
    """
    Publish active symbols to Redis for other services to consume.
    
    Normalizes symbols to standard format before publishing to ensure
    consistency between ingestor (symbol list) and bar data (OHLCV streams).
    
    Args:
        symbols: List of active trading pair symbols
    """
    try:
        # Normalize all symbols to standard format (e.g., XETHZ/USD -> ETH/USD)
        normalized_symbols = [normalize_symbol(s) for s in symbols]
        
        client = get_redis_client()
        client.set(INGESTOR_ACTIVE_SYMBOLS_KEY, json.dumps(normalized_symbols))
        logger.info(f"Published {len(normalized_symbols)} active symbols to Redis")
    except Exception as e:
        logger.warning(f"Failed to publish active symbols to Redis: {e}")


async def run_pipeline(symbols: List[str], intervals: List[str], use_dynamic_symbols: bool = False) -> None:
    """
    Run the complete ingestor pipeline: WebSocket client + Normalizer.
    
    Args:
        symbols: List of trading pairs to ingest (initial list)
        intervals: List of intervals for OHLCV aggregation
        use_dynamic_symbols: If True, refresh symbols hourly using RVOL ranking
    """
    logger.info(
        f"Starting ingestor pipeline: symbols={len(symbols)} pairs, intervals={intervals}"
    )
    logger.info(f"Symbols: {symbols[:10]}{'...' if len(symbols) > 10 else ''}")
    
    # Publish initial symbols to Redis for other services (screener, etc.)
    _publish_active_symbols(symbols)
    
    # Create health check file
    health_file = Path(get_health_check_file())
    try:
        health_file.parent.mkdir(parents=True, exist_ok=True)
        health_file.touch()
        logger.info(f"Health check file created: {health_file}")
    except Exception as e:
        logger.warning(f"Could not create health check file: {e}")
    
    # Track current symbols and shared state
    current_symbols = list(symbols)
    shutdown_requested = False
    reconnect_requested = False
    ws_manager: Optional[MultiConnectionManager] = None
    normalizer: Optional[Normalizer] = None
    
    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Shutdown already in progress, forcing exit...")
            sys.exit(1)
        shutdown_requested = True
        logger.info("Received shutdown signal, cleaning up...")
        # Schedule stop tasks
        if ws_manager:
            asyncio.get_event_loop().create_task(ws_manager.stop())
        if normalizer:
            asyncio.get_event_loop().create_task(normalizer.stop())
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Health check updater task
    async def update_health_check():
        """Periodically update health check file timestamp."""
        while not shutdown_requested:
            try:
                health_file.touch()
                await asyncio.sleep(30)
            except Exception as e:
                logger.warning(f"Could not update health check file: {e}")
                await asyncio.sleep(30)
    
    # RVOL refresh interval (expensive operation - fetches OHLC data)
    rvol_refresh_interval = get_symbol_refresh_interval()  # Default: 1 hour
    
    # Universe refresh interval (fast operation - single REST API call for ticker data)
    universe_refresh_interval = get_universe_refresh_interval()  # Default: 15 minutes
    
    # Failed symbol check interval (check every 15 minutes for symbols with no data)
    FAILED_CHECK_INTERVAL = 15 * 60  # 15 minutes
    
    def get_seconds_until_next_15min_boundary() -> int:
        """Calculate seconds until next :00, :15, :30, or :45 UTC."""
        now = datetime.now(timezone.utc)
        current_minute = now.minute
        # Find next boundary (0, 15, 30, 45)
        if current_minute < 15:
            next_boundary_minute = 15
            next_hour = now.hour
        elif current_minute < 30:
            next_boundary_minute = 30
            next_hour = now.hour
        elif current_minute < 45:
            next_boundary_minute = 45
            next_hour = now.hour
        else:
            # Next hour :00
            next_boundary_minute = 0
            next_hour = (now.hour + 1) % 24
        
        # Create target datetime
        target = now.replace(hour=next_hour, minute=next_boundary_minute, second=0, microsecond=0)
        
        # Calculate seconds until target
        delta = (target - now).total_seconds()
        # Handle case where we're exactly on boundary (run in 15 minutes)
        if delta <= 0:
            delta = 900  # 15 minutes
        return int(delta)
    
    def get_seconds_until_next_hour_boundary() -> int:
        """Calculate seconds until next :00 UTC."""
        now = datetime.now(timezone.utc)
        # Next hour :00
        target = now.replace(minute=0, second=0, microsecond=0)
        if now.minute > 0 or now.second > 0:
            target = target.replace(hour=(now.hour + 1) % 24)
        
        # Calculate seconds until target
        delta = (target - now).total_seconds()
        # Handle case where we're exactly on boundary (run immediately)
        if delta <= 0:
            delta = 3600  # 1 hour
        return int(delta)
    
    async def check_failed_symbols():
        """Periodically check for symbols with no data and mark them as failed."""
        nonlocal current_symbols, reconnect_requested
        
        while not shutdown_requested:
            await asyncio.sleep(FAILED_CHECK_INTERVAL)
            
            if shutdown_requested:
                break
            
            if not current_symbols:
                continue
            
            logger.info(f"Checking {len(current_symbols)} symbols for data availability...")
            try:
                failed_detected = []
                recovered_symbols = []
                
                # Check each current symbol for data
                for symbol in current_symbols:
                    has_data = check_symbol_has_data(symbol)
                    is_failed = symbol in get_failed_symbols()
                    
                    if not has_data and not is_failed:
                        # Symbol has no data and isn't marked as failed yet
                        mark_symbol_failed(symbol)
                        failed_detected.append(symbol)
                    elif has_data and is_failed:
                        # Symbol now has data but was previously marked as failed
                        unmark_symbol_failed(symbol)
                        recovered_symbols.append(symbol)
                
                if failed_detected:
                    logger.warning(
                        f"Detected {len(failed_detected)} symbols with no data: {failed_detected}. "
                        f"Will replace on next refresh."
                    )
                    # Trigger immediate refresh to replace failed symbols
                    reconnect_requested = True
                
                if recovered_symbols:
                    logger.info(f"Recovered {len(recovered_symbols)} symbols (data now available): {recovered_symbols}")
                    
            except Exception as e:
                logger.error(f"Error checking failed symbols: {e}")
    
    async def refresh_universe():
        """
        Fast refresh of universe using ticker data (every 15 minutes at :00, :15, :30, :45 UTC).
        
        Updates ticker data (24h change, volume) and applies hysteresis
        to update the symbol universe without full RVOL recalculation.
        
        On startup: Runs immediately if cached data is stale (> 20 minutes old),
        otherwise waits until next clock boundary.
        """
        nonlocal current_symbols, reconnect_requested
        
        from backend.ingestor.symbols import get_last_universe_refresh_time, mark_universe_refresh_time
        
        # Check if cached data is stale on startup
        last_refresh = get_last_universe_refresh_time()
        now = datetime.now(timezone.utc)
        stale_threshold_seconds = 20 * 60  # 20 minutes
        
        if last_refresh is None:
            # Never refreshed - run immediately
            logger.info("Universe refresh: no previous refresh found, running immediately on startup")
            run_immediately = True
        else:
            age_seconds = (now - last_refresh.replace(tzinfo=timezone.utc)).total_seconds()
            if age_seconds > stale_threshold_seconds:
                # Data is stale - run immediately
                logger.info(
                    f"Universe refresh: cached data is stale ({age_seconds/60:.1f} minutes old), "
                    f"running immediately on startup"
                )
                run_immediately = True
            else:
                # Data is fresh - wait until next boundary
                initial_delay = get_seconds_until_next_15min_boundary()
                logger.info(
                    f"Universe refresh: cached data is fresh ({age_seconds/60:.1f} minutes old), "
                    f"waiting {initial_delay}s until next 15-minute boundary (UTC)"
                )
                await asyncio.sleep(initial_delay)
                run_immediately = False
        
        while not shutdown_requested:
            
            if shutdown_requested:
                break
            
            # Generate run ID and timestamp for this refresh
            import uuid
            run_id = str(uuid.uuid4())[:8]
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            symbols_before_count = len(current_symbols)
            start_time = time.time()
            
            logger.info(f"Universe refresh (run_id={run_id}): updating ticker data and applying hysteresis...")
            try:
                # Step 1: Refresh ticker data (fast - single REST API call)
                refresh_ticker_data()
                
                # Step 2: Get ranked candidates by volume (from Redis ticker data)
                client = get_redis_client()
                from backend.redis.keys import SYMBOL_VOLUME_KEY
                all_volume_data = client.hgetall(SYMBOL_VOLUME_KEY)
                
                # Build ranked candidates from volume data
                ranked_candidates = []
                for symbol_bytes, data_bytes in all_volume_data.items():
                    try:
                        symbol = symbol_bytes.decode() if isinstance(symbol_bytes, bytes) else symbol_bytes
                        data = json.loads(data_bytes) if isinstance(data_bytes, bytes) else data_bytes
                        volume = data.get("volume_24h", 0.0)
                        if volume > 0:
                            ranked_candidates.append((symbol, volume))
                    except Exception:
                        continue
                
                # Sort by volume descending
                ranked_candidates.sort(key=lambda x: x[1], reverse=True)
                
                # Step 3: Apply hysteresis to update universe
                new_symbols, stats = update_universe_with_hysteresis(
                    current_universe=current_symbols,
                    ranked_candidates=ranked_candidates,
                )
                
                time_taken_ms = int((time.time() - start_time) * 1000)
                symbols_after_count = len(new_symbols)
                
                if not new_symbols:
                    logger.warning(f"Universe refresh (run_id={run_id}): returned empty list, keeping current symbols")
                    continue
                
                # Log comprehensive statistics
                logger.info(
                    f"Universe refresh (run_id={run_id}): "
                    f"timestamp={timestamp}, "
                    f"symbols_before={symbols_before_count}, "
                    f"symbols_after={symbols_after_count}, "
                    f"adds={stats['adds']}, "
                    f"drops={stats['drops']}, "
                    f"adds_confirmed={stats['adds_confirmed_count']}, "
                    f"drops_confirmed={stats['drops_confirmed_count']}, "
                    f"time_taken_ms={time_taken_ms}"
                )
                
                # Update current symbols
                if set(new_symbols) != set(current_symbols):
                    current_symbols = new_symbols
                    _publish_active_symbols(current_symbols)
                    reconnect_requested = True
                
                # Mark refresh time after successful refresh
                mark_universe_refresh_time()
                    
            except Exception as e:
                logger.error(f"Error refreshing universe: {e}", exc_info=True)
            
            # Wait until next 15-minute boundary (:00, :15, :30, :45 UTC)
            await asyncio.sleep(900)  # 15 minutes = 900 seconds
    
    async def refresh_symbols_rvol():
        """
        Full RVOL refresh (every hour at :00 UTC).
        
        This is the expensive operation that fetches OHLC data to calculate
        relative volume. Used for initial symbol selection and periodic re-ranking.
        
        On startup: Runs immediately if cached data is stale (> 90 minutes old),
        otherwise waits until next hour boundary.
        """
        nonlocal current_symbols, reconnect_requested
        
        from backend.ingestor.symbols import get_last_rvol_refresh_time, mark_rvol_refresh_time
        
        # Check if cached data is stale on startup
        last_refresh = get_last_rvol_refresh_time()
        now = datetime.now(timezone.utc)
        stale_threshold_seconds = 90 * 60  # 90 minutes
        
        if last_refresh is None:
            # Never refreshed - run immediately
            logger.info("RVOL refresh: no previous refresh found, running immediately on startup")
            run_immediately = True
        else:
            age_seconds = (now - last_refresh.replace(tzinfo=timezone.utc)).total_seconds()
            if age_seconds > stale_threshold_seconds:
                # Data is stale - run immediately
                logger.info(
                    f"RVOL refresh: cached data is stale ({age_seconds/60:.1f} minutes old), "
                    f"running immediately on startup"
                )
                run_immediately = True
            else:
                # Data is fresh - wait until next boundary
                initial_delay = get_seconds_until_next_hour_boundary()
                logger.info(
                    f"RVOL refresh: cached data is fresh ({age_seconds/60:.1f} minutes old), "
                    f"waiting {initial_delay}s until next hour boundary (:00 UTC)"
                )
                await asyncio.sleep(initial_delay)
                run_immediately = False
        
        while not shutdown_requested:
            
            if shutdown_requested:
                break
            
            # Generate run ID and timestamp for this refresh
            import uuid
            run_id = str(uuid.uuid4())[:8]
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            symbols_before_count = len(current_symbols)
            start_time = time.time()
            
            logger.info(f"RVOL refresh (run_id={run_id}): fetching RVOL-ranked symbols (expensive operation)...")
            try:
                # Get RVOL-ranked symbols (includes OHLC fetching)
                rvol_pairs = fetch_symbols_by_rvol(limit=50)
                symbols_scored = len(rvol_pairs)
                top_ranked_symbols = [symbol for symbol, _ in rvol_pairs[:20]]  # Top 20 for logging
                
                # Convert to ranked candidates format for hysteresis
                ranked_candidates = [(symbol, rvol) for symbol, rvol in rvol_pairs]
                
                # Apply hysteresis to update universe based on RVOL rankings
                new_symbols, stats = update_universe_with_hysteresis(
                    current_universe=current_symbols,
                    ranked_candidates=ranked_candidates,
                )
                
                time_taken_ms = int((time.time() - start_time) * 1000)
                symbols_after_count = len(new_symbols)
                
                if not new_symbols:
                    logger.warning(f"RVOL refresh (run_id={run_id}): returned empty list, keeping current symbols")
                    continue
                
                # Log comprehensive statistics
                logger.info(
                    f"RVOL refresh (run_id={run_id}): "
                    f"timestamp={timestamp}, "
                    f"symbols_scored={symbols_scored}, "
                    f"top_ranked_symbols={top_ranked_symbols}, "
                    f"symbols_before={symbols_before_count}, "
                    f"symbols_after={symbols_after_count}, "
                    f"adds={stats['adds']}, "
                    f"drops={stats['drops']}, "
                    f"adds_confirmed={stats['adds_confirmed_count']}, "
                    f"drops_confirmed={stats['drops_confirmed_count']}, "
                    f"time_taken_ms={time_taken_ms}"
                )
                
                # Update current symbols
                if set(new_symbols) != set(current_symbols):
                    current_symbols = new_symbols
                    _publish_active_symbols(current_symbols)
                    reconnect_requested = True
                
                # Mark refresh time after successful refresh
                mark_rvol_refresh_time()
                    
            except Exception as e:
                logger.error(f"Error refreshing symbols (RVOL): {e}", exc_info=True)
            
            # Wait until next hour boundary (:00 UTC)
            await asyncio.sleep(3600)  # 1 hour = 3600 seconds
    
    # Initialize task variables to ensure they're always defined
    health_task = None
    universe_refresh_task = None
    rvol_refresh_task = None
    failed_check_task = None
    
    # Create background tasks
    try:
        health_task = asyncio.create_task(update_health_check())
        if use_dynamic_symbols:
            universe_refresh_task = asyncio.create_task(refresh_universe())
            rvol_refresh_task = asyncio.create_task(refresh_symbols_rvol())
            failed_check_task = asyncio.create_task(check_failed_symbols())
    except Exception as e:
        logger.error(f"Failed to create background tasks: {e}", exc_info=True)
        raise
    
    # Error recovery tracking
    consecutive_errors = 0
    
    try:
        # Main loop: run pipeline, restart on symbol changes
        while not shutdown_requested:
            reconnect_requested = False
            
            try:
                # Create managers for current symbols
                ws_manager = MultiConnectionManager(symbols=current_symbols, intervals=intervals)
                normalizer = Normalizer(symbols=current_symbols, intervals=intervals)
                
                logger.info(
                    f"Subscribed to {len(current_symbols)} USD pairs across "
                    f"{ws_manager.get_connection_count()} WebSocket connection(s)"
                )
                
                # Run until shutdown or reconnect requested
                ws_task = asyncio.create_task(_run_websocket_manager(ws_manager))
                norm_task = asyncio.create_task(_run_normalizer(normalizer))
                
                # Wait for either completion, shutdown, or reconnect
                while not shutdown_requested and not reconnect_requested:
                    done, _ = await asyncio.wait(
                        [ws_task, norm_task],
                        timeout=5.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    
                    if done:
                        # One of the tasks completed (likely error)
                        for task in done:
                            try:
                                task.result()
                                # Reset consecutive errors on success
                                consecutive_errors = 0
                            except Exception as e:
                                consecutive_errors += 1
                                logger.error(f"Pipeline task error (consecutive: {consecutive_errors}): {e}")
                                
                                # Cooldown after too many consecutive errors
                                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                    logger.warning(
                                        f"{consecutive_errors} consecutive errors, "
                                        f"pausing for {ERROR_COOLDOWN_SECONDS // 60} minutes..."
                                    )
                                    await asyncio.sleep(ERROR_COOLDOWN_SECONDS)
                                    consecutive_errors = 0
                        break
                
                # Stop current managers
                logger.info("Stopping current WebSocket and normalizer...")
                await ws_manager.stop()
                await normalizer.stop()
                
                # Cancel and wait for tasks
                ws_task.cancel()
                norm_task.cancel()
                try:
                    await ws_task
                except asyncio.CancelledError:
                    pass
                try:
                    await norm_task
                except asyncio.CancelledError:
                    pass
                
                if reconnect_requested and not shutdown_requested:
                    logger.info(f"Reconnecting with {len(current_symbols)} symbols...")
                    await asyncio.sleep(1)  # Brief pause before reconnect
                    
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in pipeline loop (consecutive: {consecutive_errors}): {e}")
                
                # Cooldown after too many consecutive errors
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.warning(
                        f"{consecutive_errors} consecutive errors, "
                        f"pausing for {ERROR_COOLDOWN_SECONDS // 60} minutes..."
                    )
                    await asyncio.sleep(ERROR_COOLDOWN_SECONDS)
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(1)  # Brief pause before retry
                
    except Exception as e:
        logger.error(f"Fatal error in pipeline: {e}", exc_info=True)
        raise
    finally:
        # Cancel background tasks (check for None to handle initialization failures)
        if health_task:
            health_task.cancel()
        if universe_refresh_task:
            universe_refresh_task.cancel()
        if rvol_refresh_task:
            rvol_refresh_task.cancel()
        if failed_check_task:
            failed_check_task.cancel()
        
        # Wait for tasks to complete cancellation
        if health_task:
            try:
                await health_task
            except asyncio.CancelledError:
                pass
        
        if universe_refresh_task:
            try:
                await universe_refresh_task
            except asyncio.CancelledError:
                pass
        
        if rvol_refresh_task:
            try:
                await rvol_refresh_task
            except asyncio.CancelledError:
                pass
        
        if failed_check_task:
            try:
                await failed_check_task
            except asyncio.CancelledError:
                pass
        
        # Cleanup managers
        if ws_manager:
            try:
                await ws_manager.stop()
            except Exception as e:
                logger.warning(f"Error stopping ws_manager: {e}")
        
        if normalizer:
            try:
                await normalizer.stop()
            except Exception as e:
                logger.warning(f"Error stopping normalizer: {e}")
        
        # Remove health check file
        try:
            if health_file.exists():
                health_file.unlink()
                logger.info("Health check file removed")
        except Exception as e:
            logger.warning(f"Could not remove health check file: {e}")
        
        logger.info("Shutdown complete")


async def _run_websocket_manager(manager: MultiConnectionManager) -> None:
    """Run the WebSocket connection manager until shutdown."""
    try:
        await manager.run()
    except Exception as e:
        logger.error(f"WebSocket manager error: {e}", exc_info=True)
        raise


async def _run_normalizer(normalizer: Normalizer) -> None:
    """Run the normalizer until shutdown."""
    try:
        await normalizer.run()
    except Exception as e:
        logger.error(f"Normalizer error: {e}", exc_info=True)
        raise


def main():
    """Main entry point for the ingestor service."""
    parser = argparse.ArgumentParser(
        description="Kraken WebSocket Data Ingestor with OHLCV Normalization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # RVOL-ranked symbol selection (top by relative volume + owned, hourly refresh)
  python -m backend.ingestor.main

  # Custom symbols (no refresh)
  python -m backend.ingestor.main --symbols BTC/USD ETH/USD --intervals 1m 5m

  # Use environment variables
  INGESTOR_SYMBOLS="BTC/USD,ETH/USD" INGESTOR_INTERVALS="1m,5m" python -m backend.ingestor.main
        """,
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Trading pairs to subscribe to (default: RVOL-ranked selection with hourly refresh)",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=None,
        choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
        help="Time intervals for OHLCV bars (default: from INGESTOR_INTERVALS env or 1m 5m)",
    )
    
    args = parser.parse_args()
    
    # Get configuration (command line args override env vars)
    symbols = args.symbols if args.symbols else get_symbols()
    intervals = args.intervals if args.intervals else get_intervals()
    
    # Determine if using dynamic symbols (no explicit symbols provided)
    use_dynamic_symbols = not symbols
    
    # Fetch symbols dynamically if not provided or empty
    if not symbols:
        logger.info("No symbols specified, using RVOL-based symbol selection...")
        try:
            symbols = get_dynamic_symbols_by_rvol_with_replacements()
            logger.info(f"Initial RVOL-ranked symbols: {len(symbols)} pairs")
        except Exception as e:
            logger.error(f"Failed to fetch RVOL-ranked symbols: {e}")
            # Fallback to defaults
            symbols = ["BTC/USD", "ETH/USD", "SOL/USD", "ADA/USD", "XRP/USD"]
            logger.warning(f"Using fallback symbols: {symbols}")
            use_dynamic_symbols = False  # Don't try refresh if initial fetch failed
    
    if not symbols:
        logger.error("No symbols to subscribe to")
        sys.exit(1)
    
    if not intervals:
        logger.error("At least one interval must be specified")
        sys.exit(1)
    
    if use_dynamic_symbols:
        universe_interval = get_universe_refresh_interval()
        rvol_interval = get_symbol_refresh_interval()
        logger.info(
            f"Starting ingestor with dynamic symbols: {len(symbols)} initial symbols, "
            f"intervals={', '.join(intervals)}, "
            f"universe refresh every {universe_interval // 60} minutes, "
            f"RVOL refresh every {rvol_interval // 60} minutes"
        )
    else:
        logger.info(
            f"Starting ingestor with static symbols: {len(symbols)} symbols, "
            f"intervals={', '.join(intervals)}"
        )
    
    try:
        asyncio.run(run_pipeline(symbols, intervals, use_dynamic_symbols=use_dynamic_symbols))
    except KeyboardInterrupt:
        logger.info("Ingestor stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
