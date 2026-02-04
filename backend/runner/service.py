"""Strategy Runner service implementation.

Continuously consumes market data from Redis streams and feeds it to strategies.
Also runs the screener service as a background task.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from backend.config import LOG_LEVEL
from backend.execution.persistence import persist_fill_with_intent_id
from backend.redis.streams import consume_stream
from backend.risk.evaluator import evaluate_intent, TradeIntent as BackendTradeIntent
from backend.runner.config import (
    RUNNER_BLOCK_MS,
    RUNNER_CONSUMER_GROUP,
    RUNNER_CONSUMER_NAME,
    RUNNER_HEALTH_FILE,
    RUNNER_INTERVAL,
    RUNNER_STRATEGY_ID,
    RUNNER_SYMBOL,
    SCREENER_INTERVAL_SECONDS,
    get_stream_key,
)
from backend.screener.service import ScreenerService
from research.strategies.meanrev.config import MeanReversionConfig
from research.strategies.meanrev.strategy import MeanReversionStrategy
from research.strategies.types import MarketDataEvent

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


class StrategyRunner:
    """
    Runner that consumes market data and feeds it to strategies.
    
    Implements the workflow from MSSD § 7:
    1. Consume OHLCV bar from Redis stream
    2. Feed bar to strategy.generate_signals()
    3. If TradeIntent generated, send to Risk Manager
    4. Log all bars processed and signals generated
    """
    
    def __init__(
        self,
        strategy_id: str = RUNNER_STRATEGY_ID,
        symbol: str = RUNNER_SYMBOL,
        interval: str = RUNNER_INTERVAL,
        consumer_group: str = RUNNER_CONSUMER_GROUP,
        consumer_name: str = RUNNER_CONSUMER_NAME,
        block_ms: int = RUNNER_BLOCK_MS,
    ):
        """
        Initialize the StrategyRunner.
        
        Args:
            strategy_id: Strategy identifier (e.g., "mean_reversion")
            symbol: Trading pair (e.g., "ETH/USD")
            interval: Time interval (e.g., "4h")
            consumer_group: Redis consumer group name
            consumer_name: Redis consumer name
            block_ms: Milliseconds to block waiting for new messages
        """
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.interval = interval
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.block_ms = block_ms
        
        self.stream_key = get_stream_key(symbol, interval)
        self._running = False
        self._strategy: Optional[MeanReversionStrategy] = None
        self._last_price: Optional[float] = None  # Track latest price for execution
        
        logger.info(
            f"StrategyRunner initialized: strategy_id={strategy_id}, "
            f"symbol={symbol}, interval={interval}, stream_key={self.stream_key}"
        )
    
    def _init_strategy(self) -> MeanReversionStrategy:
        """
        Initialize the MeanReversionStrategy with configuration.
        
        Returns:
            Configured MeanReversionStrategy instance
        """
        config = MeanReversionConfig(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
        )
        
        strategy = MeanReversionStrategy(config)
        logger.info(
            f"Initialized MeanReversionStrategy: "
            f"lookback={config.lookback_period}, rsi_period={config.rsi_period}, "
            f"risk_pct={config.notional_risk_pct}"
        )
        
        return strategy
    
    def _parse_bar(self, msg_data: dict) -> Optional[MarketDataEvent]:
        """
        Parse a Redis stream message into a MarketDataEvent.
        
        Args:
            msg_data: Dictionary from Redis stream message
            
        Returns:
            MarketDataEvent or None if parsing fails
        """
        try:
            return MarketDataEvent(
                symbol=msg_data.get("symbol", self.symbol),
                interval=msg_data.get("interval", self.interval),
                open=float(msg_data["open"]),
                high=float(msg_data["high"]),
                low=float(msg_data["low"]),
                close=float(msg_data["close"]),
                volume=float(msg_data["volume"]),
                timestamp=msg_data["timestamp"],
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse bar from message: {e}. Data: {msg_data}")
            return None
    
    def _convert_intent(self, intent) -> BackendTradeIntent:
        """
        Convert research TradeIntent to backend TradeIntent.
        
        Args:
            intent: TradeIntent from research.strategies.types
            
        Returns:
            TradeIntent compatible with backend.risk.evaluator
        """
        return BackendTradeIntent(
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            side=intent.side,
            intent_type=intent.intent_type,
            notional_risk_pct=intent.notional_risk_pct,
            metadata=intent.metadata,
        )
    
    async def _consume_next_bar(self) -> Optional[MarketDataEvent]:
        """
        Consume the next bar from the Redis stream.
        
        Returns:
            MarketDataEvent or None if no message available
        """
        try:
            messages = consume_stream(
                stream_key=self.stream_key,
                consumer_group=self.consumer_group,
                consumer_name=self.consumer_name,
                count=1,
                block=self.block_ms,
            )
            
            if not messages:
                return None
            
            msg = messages[0]
            return self._parse_bar(msg["data"])
            
        except Exception as e:
            logger.error(f"Error consuming from stream {self.stream_key}: {e}")
            return None
    
    async def _process_bar(self, bar: MarketDataEvent) -> None:
        """
        Process a single market data bar.
        
        Args:
            bar: MarketDataEvent to process
        """
        logger.info(f"Processing bar: {bar.timestamp}")
        
        # Track latest price for execution
        self._last_price = bar.close
        
        # Generate signals from strategy
        intent = self._strategy.generate_signals(bar)
        
        if intent is not None:
            logger.info(f"Signal generated: {intent.side}")
            
            # Convert to backend TradeIntent and evaluate with Risk Manager
            backend_intent = self._convert_intent(intent)
            decision = evaluate_intent(backend_intent)
            
            if decision.approved:
                logger.info(
                    f"Signal approved by Risk Manager: intent_id={decision.intent_id}, "
                    f"portfolio_risk={decision.evaluated_portfolio_risk}%"
                )
                
                # Execute via Kraken (live trading)
                # TODO: Integrate with execute_approved_intent from backend.execution
                logger.warning(
                    f"Live execution not yet wired. "
                    f"Intent {decision.intent_id} approved but not executed."
                )
            else:
                logger.warning(
                    f"Signal rejected by Risk Manager: reason={decision.rejection_reason}"
                )
    
    async def run(self) -> None:
        """
        Run the strategy runner main loop.
        
        Continuously consumes bars from Redis stream and processes them.
        """
        logger.info(f"Starting StrategyRunner for {self.strategy_id}")
        
        # Initialize strategy
        self._strategy = self._init_strategy()
        self._running = True
        
        # Create health check file
        health_file = Path(RUNNER_HEALTH_FILE)
        try:
            health_file.parent.mkdir(parents=True, exist_ok=True)
            health_file.touch()
            logger.info(f"Health check file created: {health_file}")
        except Exception as e:
            logger.warning(f"Could not create health check file: {e}")
        
        bars_processed = 0
        signals_generated = 0
        consecutive_errors = 0
        
        try:
            while self._running:
                try:
                    # Update health check file periodically
                    if bars_processed % 10 == 0:
                        try:
                            health_file.touch()
                        except Exception:
                            pass
                    
                    # Consume next bar
                    bar = await self._consume_next_bar()
                    
                    if bar is None:
                        # No message available, continue waiting
                        continue
                    
                    # Process the bar
                    await self._process_bar(bar)
                    bars_processed += 1
                    
                    # Reset consecutive errors on success
                    consecutive_errors = 0
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Error in runner loop (consecutive: {consecutive_errors}): {e}")
                    
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
            logger.error(f"Error in runner main loop: {e}", exc_info=True)
            raise
        finally:
            # Cleanup health file
            try:
                if health_file.exists():
                    health_file.unlink()
                    logger.info("Health check file removed")
            except Exception as e:
                logger.warning(f"Could not remove health check file: {e}")
            
            logger.info(
                f"StrategyRunner stopped. Bars processed: {bars_processed}"
            )
    
    async def stop(self) -> None:
        """Stop the strategy runner gracefully."""
        logger.info("Stopping StrategyRunner...")
        self._running = False


async def run_strategy_runner() -> None:
    """
    Run the strategy runner with signal handling for graceful shutdown.
    Also starts the screener service as a background task.
    """
    runner = StrategyRunner()
    screener = ScreenerService(scan_interval_seconds=SCREENER_INTERVAL_SECONDS)
    shutdown_requested = False
    
    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Shutdown already in progress, forcing exit...")
            sys.exit(1)
        shutdown_requested = True
        logger.info("Received shutdown signal, cleaning up...")
        loop = asyncio.get_event_loop()
        loop.create_task(runner.stop())
        loop.create_task(screener.stop())
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Start screener service as background task
        await screener.start()
        logger.info(f"Screener service started (interval={SCREENER_INTERVAL_SECONDS}s)")
        
        # Run strategy runner in foreground
        await runner.run()
    except Exception as e:
        logger.error(f"Fatal error in StrategyRunner: {e}", exc_info=True)
        raise
    finally:
        # Ensure screener is stopped
        await screener.stop()
        logger.info("Shutdown complete")


def main():
    """Main entry point for the Strategy Runner service."""
    logger.info("Starting Strategy Runner service")
    
    try:
        asyncio.run(run_strategy_runner())
    except KeyboardInterrupt:
        logger.info("Strategy Runner stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
