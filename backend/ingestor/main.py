"""Entry point for the data ingestor service.

Orchestrates the full pipeline: Kraken WebSocket → Raw Ticks → OHLCV Bars → Redis Streams.
Runs both WebSocket client and normalizer concurrently in a single process.
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import List

from backend.ingestor.config import get_health_check_file, get_intervals, get_symbols
from backend.ingestor.kraken_ws import KrakenWebSocketClient
from backend.ingestor.normalizer import Normalizer
from backend.config import LOG_LEVEL

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def run_pipeline(symbols: List[str], intervals: List[str]) -> None:
    """
    Run the complete ingestor pipeline: WebSocket client + Normalizer.
    
    Args:
        symbols: List of trading pairs to ingest
        intervals: List of intervals for OHLCV aggregation
    """
    logger.info(
        f"Starting ingestor pipeline: symbols={symbols}, intervals={intervals}"
    )
    
    # Create WebSocket client and normalizer
    ws_client = KrakenWebSocketClient(symbols=symbols)
    normalizer = Normalizer(symbols=symbols, intervals=intervals)
    
    # Create health check file
    health_file = Path(get_health_check_file())
    try:
        health_file.parent.mkdir(parents=True, exist_ok=True)
        health_file.touch()
        logger.info(f"Health check file created: {health_file}")
    except Exception as e:
        logger.warning(f"Could not create health check file: {e}")
    
    # Setup signal handlers for graceful shutdown
    shutdown_requested = False
    
    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Shutdown already in progress, forcing exit...")
            sys.exit(1)
        shutdown_requested = True
        logger.info(f"Received signal {sig}, initiating graceful shutdown...")
        # Schedule stop tasks
        loop = asyncio.get_event_loop()
        loop.create_task(ws_client.stop())
        loop.create_task(normalizer.stop())
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start health check updater task
    async def update_health_check():
        """Periodically update health check file timestamp."""
        while not shutdown_requested:
            try:
                health_file.touch()
                await asyncio.sleep(30)  # Update every 30 seconds
            except Exception as e:
                logger.warning(f"Could not update health check file: {e}")
                await asyncio.sleep(30)
    
    health_task = asyncio.create_task(update_health_check())
    
    try:
        # Run WebSocket client and normalizer concurrently
        await asyncio.gather(
            _run_websocket_client(ws_client),
            _run_normalizer(normalizer),
            return_exceptions=True,
        )
    except Exception as e:
        logger.error(f"Fatal error in pipeline: {e}", exc_info=True)
        raise
    finally:
        # Cancel health check updater
        health_task.cancel()
        try:
            await health_task
        except asyncio.CancelledError:
            pass
        
        # Cleanup
        try:
            await ws_client.stop()
            await normalizer.stop()
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
        
        # Remove health check file
        try:
            if health_file.exists():
                health_file.unlink()
                logger.info("Health check file removed")
        except Exception as e:
            logger.warning(f"Could not remove health check file: {e}")


async def _run_websocket_client(client: KrakenWebSocketClient) -> None:
    """Run the WebSocket client until shutdown."""
    try:
        await client.run()
    except Exception as e:
        logger.error(f"WebSocket client error: {e}", exc_info=True)
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
  # Use defaults (BTC/USD, ETH/USD, intervals: 4h, 1d)
  python -m backend.ingestor.main

  # Custom symbols and intervals
  python -m backend.ingestor.main --symbols BTC/USD ETH/USD --intervals 4h 1d

  # Use environment variables
  INGESTOR_SYMBOLS="BTC/USD,ETH/USD" INGESTOR_INTERVALS="4h,1d" python -m backend.ingestor.main
        """,
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Trading pairs to subscribe to (default: from INGESTOR_SYMBOLS env or BTC/USD ETH/USD)",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=None,
        choices=["4h", "1d"],
        help="Time intervals for OHLCV bars (default: from INGESTOR_INTERVALS env or 4h 1d)",
    )
    
    args = parser.parse_args()
    
    # Get configuration (command line args override env vars)
    symbols = args.symbols if args.symbols else get_symbols()
    intervals = args.intervals if args.intervals else get_intervals()
    
    if not symbols:
        logger.error("At least one symbol must be specified")
        sys.exit(1)
    
    if not intervals:
        logger.error("At least one interval must be specified")
        sys.exit(1)
    
    logger.info(
        f"Starting ingestor: symbols={', '.join(symbols)}, intervals={', '.join(intervals)}"
    )
    
    try:
        asyncio.run(run_pipeline(symbols, intervals))
    except KeyboardInterrupt:
        logger.info("Ingestor stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
