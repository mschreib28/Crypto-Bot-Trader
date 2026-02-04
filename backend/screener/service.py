"""Background service for running the screener at regular intervals.

This service scans all subscribed symbols, calculates indicators,
and stores results in Redis for consumption by the API and frontend.

With auto-execution enabled (T60 trading_enabled=True), high-confidence
signals are automatically sent to the risk evaluator and executed.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import ACCOUNT_EQUITY, RISK_PCT_PER_TRADE, CONFIDENCE_THRESHOLD_PCT
from backend.db import get_session
from backend.db.models import Strategy
from backend.ingestor.config import get_symbols
from backend.ingestor.symbols import normalize_symbol, is_stablecoin_pair, is_in_live_universe
from backend.redis import get_redis_client
from backend.redis.keys import (
    INGESTOR_ACTIVE_SYMBOLS_KEY,
    MARKET_OHLCV_STREAM,
    SCREENER_LAST_SCAN_KEY,
    SCREENER_RESULTS_KEY,
    SCREENER_RESULTS_TTL,
    SCREENER_SIGNALS_HISTORY_KEY,
    SCREENER_STRATEGY_RESULTS_KEY,
    SIGNAL_COOLDOWN_SECONDS,
    SIGNAL_EXECUTED_KEY,
    SIGNAL_EXECUTED_KEY_LEGACY,
    SIGNAL_LAST_LOGGED_KEY,
    SIGNAL_LOG_COOLDOWN_SECONDS,
    STRATEGY_LAST_EVAL_KEY,
    STRATEGY_LAST_EVAL_TTL,
    TRADING_ENABLED_KEY,
    EXECUTION_ALLOWED_LOGGED_KEY,
    EXECUTION_ALLOWED_TTL,
)
from backend.api.routes.events import log_activity
from backend.risk.evaluator import evaluate_intent, TradeIntent
from backend.execution.executor import execute_trade
from backend.positions.tracker import get_position_tracker
from backend.ingestor.historical import backfill_historical_bars
from backend.screener.aggregator import aggregate_bars, INTERVAL_MINUTES
from backend.screener.engine import ScreenerEngine, scan_with_strategy
from backend.screener.models import ScreenerResult, SignalResult

logger = logging.getLogger(__name__)

# Maximum number of signals to keep in history
SIGNALS_HISTORY_MAX = 100
# Maximum results to store per strategy
TOP_RESULTS_PER_STRATEGY = 5


def get_trading_enabled() -> bool:
    """
    Check if trading is enabled.
    
    This function integrates with T60's trading_enabled flag.
    Reads from Redis key: system:trading_enabled
    
    Returns:
        True if trading is enabled, False otherwise
    """
    try:
        client = get_redis_client()
        value = client.get(TRADING_ENABLED_KEY)
        if value is None:
            return False
        return str(value).lower() in ("true", "1")
    except Exception as e:
        logger.warning(f"Failed to read trading_enabled: {e}")
        # Fail-closed: assume trading disabled if we can't read
        return False


def _load_enabled_strategies() -> List[Strategy]:
    """
    Load all ENABLED (active) strategies from the database.
    Prioritizes better-performing strategies.
    
    Returns:
        List of Strategy objects with status='active', sorted by performance
    """
    session = get_session()
    try:
        # Check for auto-disabled strategies before fetching active ones
        try:
            from backend.risk.metrics import get_strategy_metrics
            metrics = get_strategy_metrics()
            all_strategies = session.query(Strategy).all()
            for strategy in all_strategies:
                if strategy.status == "active":
                    # Check drawdown periodically (every scan)
                    metrics.check_strategy_drawdown(str(strategy.id))
        except Exception as e:
            logger.debug(f"Failed to check strategy drawdowns: {e}")
        
        strategies = session.query(Strategy).filter(Strategy.status == "active").all()
        
        # Prioritize by performance if enabled
        if os.getenv("PERFORMANCE_PRIORITIZATION_ENABLED", "true").lower() == "true":
            try:
                from backend.performance.monitor import get_performance_monitor
                perf_monitor = get_performance_monitor()
                
                # Calculate performance scores
                strategy_scores = []
                for strategy in strategies:
                    perf = perf_monitor.get_performance(str(strategy.id))
                    if perf:
                        # Score = (win_rate * 0.7) + (normalized_pnl * 0.3)
                        normalized_pnl = min(1.0, perf.total_pnl / 100.0)  # Cap at $100
                        score = (perf.win_rate / 100.0 * 0.7) + (normalized_pnl * 0.3)
                    else:
                        score = 0.0  # New strategies evaluated last
                    strategy_scores.append((score, strategy))
                
                # Sort by score (descending)
                strategy_scores.sort(key=lambda x: x[0], reverse=True)
                strategies = [s for _, s in strategy_scores]
                
                logger.debug(f"Strategies prioritized by performance: {[s.name for s in strategies[:3]]}")
            except Exception as e:
                logger.debug(f"Performance prioritization failed: {e}, using default order")
        
        return strategies
    except Exception as e:
        logger.error(f"Failed to load strategies: {e}")
        return []
    finally:
        session.close()


class _StrategyEvaluateAdapter:
    """
    Adapter to provide evaluate() interface for strategies.
    
    Wraps strategies that use generate_signals() to provide the
    evaluate(symbol, bars) -> SignalResult interface expected by T62.
    """
    
    def __init__(self, strategy: Any, strategy_id: str):
        """
        Initialize the adapter.
        
        Args:
            strategy: Strategy instance with generate_signals() method
            strategy_id: Strategy identifier
        """
        self._strategy = strategy
        self.strategy_id = strategy_id
    
    def evaluate(self, symbol: str, bars: List[Dict[str, Any]]) -> Optional[SignalResult]:
        """
        Evaluate a symbol using the wrapped strategy.
        
        Tries to use the strategy's evaluate() method if available,
        otherwise falls back to generate_signals() for legacy strategies.
        
        Args:
            symbol: Trading pair symbol
            bars: List of OHLCV bar dictionaries
            
        Returns:
            SignalResult with signal info (including NONE signals)
        """
        from research.strategies.types import MarketDataEvent
        
        if not bars:
            logger.debug(f"[ADAPTER:{self.strategy_id}] {symbol}: No bars provided")
            return None
        
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Convert dict bars to MarketDataEvent objects
        bar_events = []
        for bar_data in bars:
            try:
                bar = MarketDataEvent(
                    symbol=symbol,
                    interval=bar_data.get("interval", "5m"),
                    open=float(bar_data.get("open", 0)),
                    high=float(bar_data.get("high", 0)),
                    low=float(bar_data.get("low", 0)),
                    close=float(bar_data.get("close", 0)),
                    volume=float(bar_data.get("volume", 0)),
                    timestamp=bar_data.get("timestamp", timestamp),
                )
                bar_events.append(bar)
            except Exception as e:
                logger.debug(f"[ADAPTER:{self.strategy_id}] {symbol}: Error converting bar: {e}")
                continue
        
        if not bar_events:
            return None
        
        # Try to use the strategy's evaluate() method directly (preferred)
        if hasattr(self._strategy, 'evaluate'):
            try:
                result = self._strategy.evaluate(symbol, bar_events)
                if result is not None:
                    return result
            except Exception as e:
                logger.debug(f"[ADAPTER:{self.strategy_id}] {symbol}: evaluate() failed: {e}")
        
        # Fall back to generate_signals() for legacy strategies
        logger.debug(f"[ADAPTER:{self.strategy_id}] {symbol}: Using generate_signals() fallback")
        intent = None
        for bar in bar_events:
            try:
                intent = self._strategy.generate_signals(bar)
            except Exception as e:
                logger.debug(f"[ADAPTER:{self.strategy_id}] {symbol}: Error in generate_signals: {e}")
                continue
        
        if intent is None:
            # Return a NONE signal result instead of None
            current_price = float(bars[-1].get("close", 0)) if bars else 0.0
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.strategy_id,
                indicators={
                    "current_price": current_price, 
                    "note": "no_signal_conditions_met",
                    # Include frontend indicators as None for consistency
                    "bb_position": None,
                    "adx": None,
                    "atr_ratio": None,
                },
                timestamp=timestamp,
            )
        
        # Convert TradeIntent to SignalResult with confidence
        confidence = self._calculate_confidence(intent)
        current_price = float(bars[-1].get("close", 0)) if bars else 0.0
        
        return SignalResult(
            symbol=symbol,
            signal_type=intent.side.upper(),  # "buy" -> "BUY"
            confidence=confidence,
            strategy_id=self.strategy_id,
            indicators={
                **intent.metadata,
                "current_price": current_price,
                "intent_type": intent.intent_type,
                "notional_risk_pct": intent.notional_risk_pct,
            },
            timestamp=timestamp,
        )
    
    def _calculate_confidence(self, intent: Any) -> float:
        """
        Calculate confidence score from TradeIntent metadata.
        
        Uses indicator values (RSI, band_position) to calculate
        a confidence score similar to the screener's signal_strength.
        
        Args:
            intent: TradeIntent with metadata
            
        Returns:
            Confidence score from 0-100
        """
        metadata = intent.metadata or {}
        
        # Try to get band_position and RSI from metadata
        band_position = metadata.get("band_position")
        rsi = metadata.get("rsi")
        
        if band_position is None or rsi is None:
            # Default confidence if indicators not available
            return 50.0
        
        confidence = 0.0
        side = intent.side.lower()
        
        if side == "buy":
            # Buy signal strength: lower RSI and band_position = higher confidence
            if rsi < 30:
                rsi_contrib = (30 - rsi) / 30 * 50
                confidence += rsi_contrib
            if band_position < 0.2:
                bb_contrib = (0.2 - band_position) / 0.2 * 50
                confidence += min(bb_contrib, 50)
                
        elif side == "sell":
            # Sell signal strength: higher RSI and band_position = higher confidence
            if rsi > 70:
                rsi_contrib = (rsi - 70) / 30 * 50
                confidence += rsi_contrib
            if band_position > 0.8:
                bb_contrib = (band_position - 0.8) / 0.2 * 50
                confidence += min(bb_contrib, 50)
        
        return round(min(confidence, 100), 2)


class ScreenerService:
    """
    Background service that runs the screener at regular intervals.
    
    Scans all subscribed symbols, calculates indicators, and stores
    results in Redis.
    """
    
    def __init__(
        self,
        scan_interval_seconds: float = 60.0,
        bars_to_fetch: int = 250,
        interval: str = "5m",
    ):
        """
        Initialize the screener service.
        
        Args:
            scan_interval_seconds: How often to run scans (default: 60s)
            bars_to_fetch: Number of recent bars to fetch per symbol (250 for EMA 200 + buffer)
            interval: OHLCV interval to use (default: "5m")
        """
        self.scan_interval = scan_interval_seconds
        self.bars_to_fetch = bars_to_fetch
        self.interval = interval
        self.engine = ScreenerEngine()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        logger.info(
            f"ScreenerService initialized: interval={scan_interval_seconds}s, "
            f"bars={bars_to_fetch}, timeframe={interval}"
        )
    
    def _get_stream_key(self, symbol: str, interval: str = "1m") -> str:
        """Get Redis stream key for a symbol."""
        return MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval)
    
    def _get_scan_symbols(self) -> List[str]:
        """
        Get symbols to scan, preferring Redis over env config.
        
        Reads active symbols published by the ingestor service.
        Falls back to env config if Redis data is unavailable.
        
        Normalizes all symbols to standard format to ensure consistency
        with bar data storage keys (e.g., XETHZ/USD -> ETH/USD).
        
        Returns:
            List of trading pair symbols in standard format
        """
        try:
            client = get_redis_client()
            symbols_json = client.get(INGESTOR_ACTIVE_SYMBOLS_KEY)
            if symbols_json:
                symbols = json.loads(symbols_json)
                # Normalize symbols to standard format (safety net)
                symbols = [normalize_symbol(s) for s in symbols]
                # Filter out stablecoins (USDC/USD, USDT/USD, etc.) - these shouldn't be traded
                filtered_symbols = [s for s in symbols if not is_stablecoin_pair(s)]
                if len(filtered_symbols) < len(symbols):
                    removed = [s for s in symbols if is_stablecoin_pair(s)]
                    logger.info(f"Filtered out {len(removed)} stablecoin(s) from symbol list: {removed}")
                logger.info(f"Using {len(filtered_symbols)} symbols from ingestor (after stablecoin filter)")
                return filtered_symbols
        except Exception as e:
            logger.warning(f"Failed to get symbols from Redis: {e}")
        
        # Fallback to config
        fallback_symbols = get_symbols()
        # Normalize fallback symbols as well
        fallback_symbols = [normalize_symbol(s) for s in fallback_symbols]
        # Filter out stablecoins from fallback as well
        filtered_fallback = [s for s in fallback_symbols if not is_stablecoin_pair(s)]
        if len(filtered_fallback) < len(fallback_symbols):
            removed = [s for s in fallback_symbols if is_stablecoin_pair(s)]
            logger.info(f"Filtered out {len(removed)} stablecoin(s) from fallback symbols: {removed}")
        logger.info(f"Using {len(filtered_fallback)} symbols from config (fallback, after stablecoin filter)")
        return filtered_fallback
    
    async def _get_recent_bars(
        self,
        symbol: str,
        count: int,
        target_interval: str | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent OHLCV bars from Redis stream.
        
        Always fetches 1m bars from Redis and aggregates to the target interval.
        This allows strategies to use any supported timeframe without requiring
        multiple ingestor subscriptions.
        
        Args:
            symbol: Trading pair symbol
            count: Number of bars to fetch (at target interval)
            target_interval: Target interval (e.g., '5m', '15m', '1h').
                           Defaults to self.interval if not specified.
            
        Returns:
            List of bar dictionaries at the target interval
        """
        if target_interval is None:
            target_interval = self.interval
        
        client = get_redis_client()
        
        # Try to fetch from target interval first (direct bars)
        # Fall back to 1m aggregation only if target interval doesn't exist
        stream_key = self._get_stream_key(symbol, interval=target_interval)
        
        try:
            # XREVRANGE gets messages in reverse order (newest first)
            # We need to reverse to get oldest-first for indicator calculation
            messages = client.xrevrange(stream_key, count=count)
            
            if messages:
                # Direct fetch from target interval
                bars = []
                for msg_id, data in reversed(messages):
                    bar = {
                        "symbol": data.get("symbol", symbol),
                        "interval": target_interval,
                        "open": float(data.get("open", 0)),
                        "high": float(data.get("high", 0)),
                        "low": float(data.get("low", 0)),
                        "close": float(data.get("close", 0)),
                        "volume": float(data.get("volume", 0)),
                        "timestamp": data.get("timestamp", ""),
                    }
                    bars.append(bar)
                
                logger.debug(
                    f"[REDIS] {symbol}: Fetched {len(bars)} {target_interval} bars from {stream_key}"
                )
                
                # If we have enough bars, return them
                if len(bars) >= 20:
                    return bars
                
                # Otherwise, fall through to backfill
                logger.debug(f"[REDIS] {symbol}: Only {len(bars)} direct bars, need backfill")
            
            # Fall back to 5m aggregation (ingestor stores 5m bars)
            # Only aggregate from smaller intervals to larger ones
            target_minutes = INTERVAL_MINUTES.get(target_interval, 1)
            
            for source_interval in ["5m", "1m"]:
                source_minutes = INTERVAL_MINUTES.get(source_interval, 1)
                
                # Can only aggregate UP (from smaller to larger intervals)
                # Skip if source is larger than target (can't aggregate 5m down to 1m)
                if source_minutes > target_minutes:
                    continue
                
                # If source == target, just fetch directly (no aggregation needed)
                if source_minutes == target_minutes:
                    bars_needed = count
                else:
                    bars_needed = count * (target_minutes // source_minutes)
                
                # Ensure bars_needed is at least 1
                if bars_needed < 1:
                    continue
                
                stream_key_source = self._get_stream_key(symbol, interval=source_interval)
                messages_source = client.xrevrange(stream_key_source, count=bars_needed)
                
                if messages_source:
                    bars_source = []
                    for msg_id, data in reversed(messages_source):
                        bar = {
                            "symbol": data.get("symbol", symbol),
                            "interval": source_interval,
                            "open": float(data.get("open", 0)),
                            "high": float(data.get("high", 0)),
                            "low": float(data.get("low", 0)),
                            "close": float(data.get("close", 0)),
                            "volume": float(data.get("volume", 0)),
                            "timestamp": data.get("timestamp", ""),
                        }
                        bars_source.append(bar)
                    
                    # Aggregate to target interval
                    bars = aggregate_bars(bars_source, target_interval)
                    
                    logger.debug(
                        f"[REDIS] {symbol}: Fetched {len(bars_source)} {source_interval} bars -> "
                        f"{len(bars)} {target_interval} bars (aggregated)"
                    )
                    
                    # If we have enough bars, return them
                    if len(bars) >= 20:
                        return bars
                    
                    # Otherwise, fall through to backfill
                    logger.debug(f"[REDIS] {symbol}: Only {len(bars)} aggregated bars, need backfill")
                    break  # Exit loop, proceed to backfill
            
            # If insufficient bars found, try historical backfill
            logger.info(f"[BACKFILL] Triggering historical backfill for {symbol} at {target_interval}")
            try:
                stored = await backfill_historical_bars(symbol, target_interval, 720)
                if stored > 0:
                    # Re-fetch from Redis after backfill
                    messages = client.xrevrange(stream_key, count=count)
                    if messages:
                        bars = []
                        for msg_id, data in reversed(messages):
                            bar = {
                                "symbol": data.get("symbol", symbol),
                                "interval": target_interval,
                                "open": float(data.get("open", 0)),
                                "high": float(data.get("high", 0)),
                                "low": float(data.get("low", 0)),
                                "close": float(data.get("close", 0)),
                                "volume": float(data.get("volume", 0)),
                                "timestamp": data.get("timestamp", ""),
                            }
                            bars.append(bar)
                        logger.info(f"[BACKFILL] {symbol}: Now have {len(bars)} bars after backfill")
                        return bars
            except Exception as e:
                logger.warning(f"[BACKFILL] Failed for {symbol}: {e}")
            
            logger.debug(f"[REDIS] {symbol}: No bars found at {target_interval}, 5m, or 1m")
            return []
            
        except Exception as e:
            logger.warning(f"[REDIS] Failed to fetch bars for {symbol} from {stream_key}: {e}")
            return []
    
    async def _get_all_symbols_bars(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch bars for all subscribed symbols.
        
        Returns:
            Dictionary mapping symbol to list of bars
        """
        symbols = self._get_scan_symbols()
        symbols_bars = {}
        
        logger.info(f"[BARS] Fetching bars for {len(symbols)} symbols (interval={self.interval}, max={self.bars_to_fetch})")
        
        # Fetch bars for each symbol concurrently
        tasks = []
        for symbol in symbols:
            tasks.append(self._get_recent_bars(symbol, self.bars_to_fetch))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        symbols_with_data = 0
        total_bars = 0
        for symbol, bars in zip(symbols, results):
            if isinstance(bars, Exception):
                logger.warning(f"[BARS] Error fetching bars for {symbol}: {bars}")
                symbols_bars[symbol] = []
            else:
                symbols_bars[symbol] = bars
                if bars:
                    symbols_with_data += 1
                    total_bars += len(bars)
        
        logger.info(
            f"[BARS] Fetched {total_bars} total bars across {symbols_with_data}/{len(symbols)} symbols"
        )
        
        return symbols_bars
    
    def _store_results(self, results: List[ScreenerResult]) -> None:
        """
        Store screener results in Redis with TTL.
        
        Results persist for SCREENER_RESULTS_TTL seconds (default 300s = 5 min).
        This ensures results survive if a scan temporarily fails or produces
        no data, rather than immediately disappearing.
        
        Results are sorted so symbols with actual data appear first,
        and insufficient_data symbols are at the bottom.
        
        Args:
            results: List of ScreenerResult objects
        """
        client = get_redis_client()
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Sort results: symbols with data first, insufficient_data at bottom
        # Then by signal_strength descending within each group
        def sort_key(r: ScreenerResult) -> tuple:
            has_insufficient_data = r.indicators.get("error") == "insufficient_data"
            # has_data=True (0) sorts before has_data=False (1)
            return (1 if has_insufficient_data else 0, -r.signal_strength)
        
        sorted_results = sorted(results, key=sort_key)
        
        # Convert results to JSON-serializable format
        results_data = [r.to_dict() for r in sorted_results]
        
        # Store as JSON string with TTL so results persist between scans
        # Use setex (SET with EXpire) for atomic set+ttl
        client.setex(SCREENER_RESULTS_KEY, SCREENER_RESULTS_TTL, json.dumps(results_data))
        
        # Store last scan timestamp with same TTL
        client.setex(SCREENER_LAST_SCAN_KEY, SCREENER_RESULTS_TTL, timestamp)
        
        # Count signal types for logging
        buy_count = sum(1 for r in results if r.signal_type == "BUY")
        sell_count = sum(1 for r in results if r.signal_type == "SELL")
        none_count = sum(1 for r in results if r.signal_type == "NONE")
        
        logger.info(
            f"[STORE] Stored {len(results)} results to Redis (TTL={SCREENER_RESULTS_TTL}s) "
            f"(BUY: {buy_count}, SELL: {sell_count}, NONE: {none_count}) "
            f"at {timestamp}"
        )
    
    def _log_and_store_signals(self, results: List[ScreenerResult]) -> None:
        """
        Log actionable signals (BUY/SELL) and store them in Redis history.
        
        This is LOG ONLY mode - no automatic execution.
        
        Args:
            results: List of ScreenerResult objects
        """
        # Filter for actionable signals
        actionable = [r for r in results if r.signal_type in ("BUY", "SELL")]
        
        if not actionable:
            return
        
        client = get_redis_client()
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Calculate recommended position size using 2% rule
        max_risk_usd = ACCOUNT_EQUITY * (RISK_PCT_PER_TRADE / 100.0)
        
        for result in actionable:
            # Log detailed signal info
            indicators_str = ", ".join(
                f"{k}: {v:.2f}" if isinstance(v, float) else f"{k}: {v}"
                for k, v in result.indicators.items()
            )
            
            logger.info(
                f"\n{'='*50}\n"
                f"SIGNAL DETECTED: {result.signal_type} {result.symbol}\n"
                f"Strength: {result.signal_strength:.1f}%\n"
                f"Indicators: {{{indicators_str}}}\n"
                f"Recommended: Position size ${max_risk_usd:.2f} (2% of ${ACCOUNT_EQUITY:.2f})\n"
                f"Action: LOG_ONLY (auto-execution disabled)\n"
                f"{'='*50}"
            )
            
            # REMOVED - only strategy-specific signals should be logged to activity
            # log_activity(
            #     activity_type="signal",
            #     message=f"{result.signal_type} signal for {result.symbol} [screener]",
            #     details={
            #         "symbol": result.symbol,
            #         "signal_type": result.signal_type,
            #         "confidence": result.signal_strength,
            #         "strategy": "screener",
            #     },
            # )
            
            # Store signal in Redis history
            signal_data = {
                "symbol": result.symbol,
                "signal_type": result.signal_type,
                "signal_strength": result.signal_strength,
                "indicators": result.indicators,
                "recommended_size_usd": max_risk_usd,
                "account_equity": ACCOUNT_EQUITY,
                "risk_pct": RISK_PCT_PER_TRADE,
                "timestamp": timestamp,
                "action": "LOG_ONLY",
            }
            
            # LPUSH to add to front of list, then LTRIM to keep only last N
            client.lpush(SCREENER_SIGNALS_HISTORY_KEY, json.dumps(signal_data))
            client.ltrim(SCREENER_SIGNALS_HISTORY_KEY, 0, SIGNALS_HISTORY_MAX - 1)
    
    def _store_strategy_results(
        self,
        strategy_id: str,
        results: List[SignalResult],
        total_scanned: int = 0,
        confidence_buy: float = 90.0,
        confidence_sell: float = 90.0,
    ) -> List[SignalResult]:
        """
        Store strategy-specific results in Redis with TTL.
        
        Results persist for SCREENER_RESULTS_TTL seconds. This ensures results
        survive if a scan temporarily fails, rather than immediately disappearing.
        
        New results are MERGED with existing results - symbols that weren't re-evaluated
        keep their previous results. This supports interval-based evaluation where not
        all symbols are evaluated every scan.
        
        Preserved results are re-filtered against current confidence thresholds to ensure
        signals reflect the current configuration (e.g., if threshold changed from 55% to 75%,
        preserved BUY signals with 73% confidence become NONE).
        
        Args:
            strategy_id: Strategy identifier
            results: List of SignalResult objects (already sorted by confidence)
            total_scanned: Total number of symbols scanned
            confidence_buy: Confidence threshold for BUY signals (default: 90.0)
            confidence_sell: Confidence threshold for SELL signals (default: 90.0)
            
        Returns:
            List of SignalResult for signals restored by threshold changes (for auto-execution)
        """
        client = get_redis_client()
        key = SCREENER_STRATEGY_RESULTS_KEY.format(strategy_id=strategy_id)
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Resolve strategy name for activity logging
        strategy_name = strategy_id
        try:
            session = get_session()
            strategy = session.query(Strategy).filter(Strategy.id == strategy_id).first()
            if strategy:
                strategy_name = strategy.name
            session.close()
        except Exception:
            pass  # Keep UUID as fallback
        
        # Load existing results to merge with
        existing_by_symbol = {}
        try:
            existing_data = client.get(key)
            if existing_data:
                existing = json.loads(existing_data)
                for r in existing.get("results", []):
                    symbol = r.get("symbol")
                    # Filter out stablecoins from preserved results
                    if symbol and is_stablecoin_pair(symbol):
                        logger.debug(f"[STORE] Filtering stablecoin from preserved results: {symbol}")
                        continue
                    existing_by_symbol[symbol] = r
        except Exception as e:
            logger.debug(f"[STORE] Could not load existing results: {e}")
        
        # Re-apply confidence thresholds to preserved results
        # This ensures signals reflect current threshold configuration using
        # the same logic as new results: confidence + direction determines signal
        filtered_down_count = 0
        restored_up_count = 0
        restored_signals: List[SignalResult] = []  # Signals to send for auto-execution
        
        for symbol, result in existing_by_symbol.items():
            original_signal = result.get("signal_type", "NONE")
            confidence = result.get("confidence", 0.0)
            indicators = result.get("indicators", {})
            
            # Get direction from indicators (calculated by engine)
            direction = indicators.get("direction", "neutral")
            
            # Determine new signal based on confidence vs threshold AND direction
            # This matches the logic in engine._apply_confidence_threshold()
            if direction == "bullish":
                threshold = confidence_buy
                new_signal = "BUY" if confidence >= threshold else "NONE"
            elif direction == "bearish":
                threshold = confidence_sell
                new_signal = "SELL" if confidence >= threshold else "NONE"
            else:
                # Neutral direction - keep original signal but filter if below threshold
                threshold = confidence_buy if original_signal == "BUY" else confidence_sell
                if original_signal in ("BUY", "SELL") and confidence < threshold:
                    new_signal = "NONE"
                else:
                    new_signal = original_signal
            
            # Debug logging for preserved results
            logger.info(
                f"Symbol {symbol} confidence {confidence:.1f}% vs threshold {threshold:.1f}% -> {new_signal} "
                f"(direction={direction}, original={original_signal}, preserved=True)"
            )
            
            # DEBUG: Always log to verify code execution
            logger.info(f"[DEBUG-PRESERVED] {symbol}: new_signal={new_signal!r}, original={original_signal!r}, direction={direction}")
            
            # DEBUG: Check if signal should be added
            if new_signal in ("BUY", "SELL"):
                logger.info(f"[DEBUG-BUYSELL] {symbol}: new_signal={new_signal!r}, type={type(new_signal)}, in check={new_signal in ('BUY', 'SELL')}")
            
            # Track changes
            if original_signal != new_signal:
                if new_signal == "NONE":
                    filtered_down_count += 1
                    indicators["threshold_filtered"] = True
                    indicators["original_signal"] = original_signal
                else:
                    restored_up_count += 1
                    indicators["threshold_filtered"] = False
                    
                    # Create SignalResult for auto-execution
                    # Ensure frontend indicators are present
                    if 'bb_position' not in indicators:
                        indicators['bb_position'] = None
                    if 'adx' not in indicators:
                        indicators['adx'] = None
                    if 'atr_ratio' not in indicators:
                        indicators['atr_ratio'] = None
                    
                    restored_signal = SignalResult(
                        symbol=symbol,
                        signal_type=new_signal,
                        confidence=confidence,
                        strategy_id=strategy_id,
                        indicators=indicators,
                        timestamp=timestamp,
                    )
                    restored_signals.append(restored_signal)
                    
                    # Log SIGNAL_CONFIRMED (restored by threshold change, will attempt execution)
                    # Use debouncing to prevent duplicate logs
                    bar_timestamp = result.get("timestamp") or indicators.get("bar_timestamp")
                    if self._should_log_signal(strategy_id, symbol, new_signal, bar_timestamp):
                        log_activity(
                            activity_type="SIGNAL_CONFIRMED",
                            message=f"{new_signal} signal confirmed for {symbol} [{strategy_name}]",
                            details={
                                "symbol": symbol,
                                "signal_type": new_signal,
                                "confidence": confidence,
                                "strategy": strategy_name,
                                "auto_execute": True,
                                "reason": "restored_by_threshold_change",
                            },
                        )
                        self._record_signal_logged(strategy_id, symbol, new_signal, bar_timestamp)
                
                result["signal_type"] = new_signal
            
            # Always include BUY/SELL signals that meet thresholds for auto-execution
            # This ensures existing signals are processed even when no new bar data
            if new_signal in ("BUY", "SELL"):
                # CRITICAL: Add signal to restored_signals for auto-execution processing
                logger.info(
                    f"[STORE] Adding {new_signal} signal for {symbol} to restored_signals "
                    f"(confidence={confidence:.1f}%, strategy={strategy_id}, new_signal={new_signal!r})"
                )
                restored_signal = SignalResult(
                    symbol=symbol,
                    signal_type=new_signal,
                    confidence=confidence,
                    strategy_id=strategy_id,
                    indicators=indicators,
                    timestamp=timestamp,
                )
                restored_signals.append(restored_signal)
                logger.debug(f"[STORE] After append: restored_signals length={len(restored_signals)}")
        
        if filtered_down_count > 0 or restored_up_count > 0:
            logger.info(
                f"[STORE] Strategy {strategy_id}: Threshold re-applied to preserved results "
                f"(filtered_down={filtered_down_count}, restored_up={restored_up_count}, "
                f"buy_threshold={confidence_buy}, sell_threshold={confidence_sell})"
            )
        
        # If no new results and no existing, nothing to store
        if not results and not existing_by_symbol:
            logger.info(
                f"[STORE] Strategy {strategy_id}: No results to store (scanned={total_scanned})"
            )
            return []
        
        all_results = results
        
        # Normalize results to ensure 'indicators' key with 'rsi' and 'price'
        normalized_results = []
        for r in all_results:
            result_dict = r.to_dict()
            
            # Handle 'metadata' from backend.screener.models.SignalResult
            # by normalizing to 'indicators' for frontend consistency
            if 'indicators' not in result_dict and 'metadata' in result_dict:
                result_dict['indicators'] = result_dict.pop('metadata')
            
            # Handle 'signal' vs 'signal_type' field name differences
            if 'signal_type' not in result_dict and 'signal' in result_dict:
                result_dict['signal_type'] = result_dict.pop('signal')
            
            # Ensure 'price' is present (normalize from 'current_price')
            indicators = result_dict.get('indicators', {})
            if indicators and 'price' not in indicators and 'current_price' in indicators:
                indicators['price'] = indicators['current_price']
            
            normalized_results.append(result_dict)
        
        # Merge: new results override existing, keep existing for non-evaluated symbols
        new_by_symbol = {r.get("symbol"): r for r in normalized_results}
        merged_results = []
        
        # Add all new results (filter out stablecoins)
        for symbol, result in new_by_symbol.items():
            if is_stablecoin_pair(symbol):
                logger.debug(f"[STORE] Filtering stablecoin from new merged results: {symbol}")
                continue
            merged_results.append(result)
        
        # Add existing results for symbols not in new results (filter out stablecoins)
        for symbol, result in existing_by_symbol.items():
            if is_stablecoin_pair(symbol):
                logger.debug(f"[STORE] Filtering stablecoin from preserved merged results: {symbol}")
                continue
            if symbol not in new_by_symbol:
                merged_results.append(result)
        
        # Sort by confidence descending for display
        merged_results.sort(key=lambda r: r.get("confidence", 0), reverse=True)
        
        data = {
            "strategy_id": strategy_id,
            "results": merged_results,
            "last_scan": timestamp,
            "count": len(merged_results),
            "total_scanned": total_scanned,
        }
        
        # Store with TTL so results persist between scans even if a scan fails
        client.setex(key, SCREENER_RESULTS_TTL, json.dumps(data))
        
        # Log stored results summary
        buy_count = sum(1 for r in merged_results if r.get('signal_type', 'NONE') == 'BUY')
        sell_count = sum(1 for r in merged_results if r.get('signal_type', 'NONE') == 'SELL')
        none_count = sum(1 for r in merged_results if r.get('signal_type', 'NONE') == 'NONE')
        new_count = len(normalized_results)
        preserved_count = len(merged_results) - new_count
        logger.info(
            f"[STORE] Strategy {strategy_id}: Stored {len(merged_results)} results to {key} "
            f"(new={new_count}, preserved={preserved_count}, BUY={buy_count}, SELL={sell_count}, NONE={none_count}, TTL={SCREENER_RESULTS_TTL}s)"
        )
        
        # Return restored signals for auto-execution processing
        if restored_signals:
            signal_list = ", ".join([f"{s.symbol} {getattr(s, 'signal_type', getattr(s, 'signal', 'NONE'))}" 
                                    for s in restored_signals[:5]])
            logger.info(
                f"[STORE] Strategy {strategy_id}: {len(restored_signals)} signals restored for auto-execution: {signal_list}"
            )
        return restored_signals
    
    async def _process_auto_execution(
        self,
        signal: SignalResult,
        trading_enabled: bool,
    ) -> None:
        """
        Process a signal for potential auto-execution.
        
        If trading is enabled and confidence meets threshold, create TradeIntent,
        send to risk evaluator, and execute if approved.
        
        Args:
            signal: SignalResult with confidence
            trading_enabled: Whether trading is currently enabled
        """
        confidence = signal.confidence
        
        # Get signal type (handle both signal and signal_type attributes)
        signal_type = getattr(signal, 'signal_type', None) or getattr(signal, 'signal', 'NONE')
        
        # Only process actionable signals (BUY/SELL)
        # NONE signals have already been filtered by strategy threshold
        if signal_type.upper() not in ("BUY", "SELL"):
            return
        
        # Get human-readable strategy name for logging (fallback to UUID)
        strategy_name = signal.strategy_id
        try:
            session = get_session()
            strategy = session.query(Strategy).filter(Strategy.id == signal.strategy_id).first()
            if strategy:
                strategy_name = strategy.name
            session.close()
        except Exception:
            pass  # Keep UUID as fallback
        
        # Get bar timestamp from signal (used for debouncing)
        bar_timestamp = signal.timestamp or None
        indicators = getattr(signal, 'indicators', None) or getattr(signal, 'metadata', {})
        # Try to get bar timestamp from indicators if available
        if not bar_timestamp and isinstance(indicators, dict):
            bar_timestamp = indicators.get("bar_timestamp") or indicators.get("timestamp")
        
        # Check if shadow mode is active (needed for execution path)
        from backend.api.routes.trading import get_shadow_live_mode
        shadow_mode = get_shadow_live_mode()
        
        if not trading_enabled and not shadow_mode:
            # Trading disabled AND not shadow mode - just log signal and return
            # Check debouncing before logging
            if not self._should_log_signal(signal.strategy_id, signal.symbol, signal_type, bar_timestamp):
                logger.debug(
                    f"[DEBOUNCE] Skipping activity log for {signal_type} {signal.symbol} "
                    f"(trading-off, debounced)"
                )
                return
            
            # Log SIGNAL_CONFIRMED but don't execute (trading disabled)
            logger.info(
                f"SIGNAL_CONFIRMED (trading-off): {signal_type} {signal.symbol} "
                f"confidence={confidence:.1f}% strategy={strategy_name}"
            )
            log_activity(
                activity_type="SIGNAL_CONFIRMED",
                message=f"{signal_type} signal confirmed for {signal.symbol} [{strategy_name}]",
                details={
                    "symbol": signal.symbol,
                    "signal_type": signal_type,
                    "confidence": confidence,
                    "strategy": strategy_name,
                    "auto_execute": False,
                    "reason": "trading_disabled",
                },
            )
            # Record that we logged this signal
            self._record_signal_logged(signal.strategy_id, signal.symbol, signal_type, bar_timestamp)
            return
        
        # Signal is BUY/SELL (already passed strategy threshold)
        # Proceed to execution checks (both live and shadow mode)
        
        # Live universe check: Skip live execution if symbol not in live universe
        # Shadow mode can still proceed (evaluation only)
        if trading_enabled and not shadow_mode:
            if not is_in_live_universe(signal.symbol):
                logger.info(
                    f"Signal {signal.symbol} skipped: Not in live universe (shadow mode only)"
                )
                # Log SIGNAL_CONFIRMED but don't execute (not in live universe)
                if self._should_log_signal(signal.strategy_id, signal.symbol, signal_type, bar_timestamp):
                    log_activity(
                        activity_type="SIGNAL_CONFIRMED",
                        message=f"{signal_type} signal confirmed for {signal.symbol} [{strategy_name}] - not in live universe",
                        details={
                            "symbol": signal.symbol,
                            "signal_type": signal_type,
                            "confidence": confidence,
                            "strategy": strategy_name,
                            "auto_execute": False,
                            "reason": "not_in_live_universe",
                        },
                    )
                    self._record_signal_logged(signal.strategy_id, signal.symbol, signal_type, bar_timestamp)
                return
        
        # No-shorting: Only execute SELL signals if we own the asset
        if signal_type.upper() == "SELL":
            tracker = get_position_tracker()
            
            # Check if we have a position to sell (no shorting)
            if not tracker.has_position(signal.symbol):
                # Check debouncing before logging SIGNAL_CONFIRMED
                if self._should_log_signal(signal.strategy_id, signal.symbol, signal_type, bar_timestamp):
                    logger.info(f"SELL signal ignored for {signal.symbol}: no position (no shorting)")
                    log_activity(
                        activity_type="SIGNAL_CONFIRMED",
                        message=f"SELL signal confirmed for {signal.symbol} [{strategy_name}] - no position (no shorting)",
                        details={
                            "reason": "no_shorting",
                            "symbol": signal.symbol,
                            "signal_type": signal_type,
                            "confidence": confidence,
                            "strategy": strategy_name,
                        },
                    )
                    self._record_signal_logged(signal.strategy_id, signal.symbol, signal_type, bar_timestamp)
                return
            
            # T72: Ownership enforcement for SELL signals
            position = tracker.get_position(signal.symbol)
            
            if position is not None:
                # Check opened_by_strategy_id (T71 field, may not exist on legacy positions)
                owner_strategy_id = getattr(position, "opened_by_strategy_id", None)
                
                if owner_strategy_id is not None and owner_strategy_id != signal.strategy_id:
                    # Position owned by different strategy - skip SELL
                    logger.info(
                        f"Skipping SELL for {signal.symbol} - owned by different strategy "
                        f"(owner={owner_strategy_id}, signal_from={signal.strategy_id})"
                    )
                    return
                # If owner_strategy_id is None (legacy) or matches - allow SELL
        
        # Position check for BUY signals: skip if position already exists
        if signal_type.upper() == "BUY":
            tracker = get_position_tracker()
            
            if tracker.has_position(signal.symbol):
                logger.info(
                    f"BUY signal skipped for {signal.symbol}: position already exists "
                    f"(strategy={signal.strategy_id})"
                )
                # Log SIGNAL_CONFIRMED (signal met threshold but position exists)
                log_activity(
                    activity_type="SIGNAL_CONFIRMED",
                    message=f"BUY signal confirmed for {signal.symbol} [{strategy_name}] - position exists",
                    details={
                        "reason": "position_exists",
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "confidence": confidence,
                        "strategy": strategy_name,
                    },
                )
                return
            
            # Cooldown check: skip if signal was recently executed FOR THIS CANDLE
            # Cooldown is per-candle (includes bar_timestamp) so new candles can execute
            client = get_redis_client()
            if bar_timestamp:
                # Per-candle cooldown: expires when new candle opens
                cooldown_key = SIGNAL_EXECUTED_KEY.format(
                    strategy_id=signal.strategy_id, 
                    symbol=signal.symbol,
                    bar_timestamp=bar_timestamp
                )
            else:
                # Fallback to legacy key if no bar_timestamp (shouldn't happen in normal flow)
                cooldown_key = SIGNAL_EXECUTED_KEY_LEGACY.format(
                    strategy_id=signal.strategy_id, 
                    symbol=signal.symbol
                )
            
            if client.exists(cooldown_key):
                # Check debouncing before logging SIGNAL_CONFIRMED
                if self._should_log_signal(signal.strategy_id, signal.symbol, signal_type, bar_timestamp):
                    logger.info(
                        f"BUY signal skipped for {signal.symbol}: cooldown active for candle {bar_timestamp} "
                        f"(strategy={signal.strategy_id})"
                    )
                    log_activity(
                        activity_type="SIGNAL_CONFIRMED",
                        message=f"BUY signal confirmed for {signal.symbol} [{strategy_name}] - cooldown active",
                        details={
                            "reason": "cooldown_active",
                            "symbol": signal.symbol,
                            "signal_type": signal_type,
                            "confidence": confidence,
                            "strategy": strategy_name,
                            "bar_timestamp": bar_timestamp,
                        },
                    )
                    self._record_signal_logged(signal.strategy_id, signal.symbol, signal_type, bar_timestamp)
                return
        
        # Confidence meets threshold and trading is enabled - attempt execution
        logger.info(
            f"Signal approved: {signal.symbol} {signal_type} confidence={confidence:.1f}%"
        )
        
        try:
            # Create TradeIntent from signal
            side = signal_type.lower()  # "buy" or "sell"
            # Get metadata/indicators (handle both attribute names)
            signal_data = getattr(signal, 'indicators', None) or getattr(signal, 'metadata', {})
            
            # Get strategy interval for metadata
            strategy_interval = "15m"  # Default
            try:
                session = get_session()
                strategy = session.query(Strategy).filter(Strategy.id == signal.strategy_id).first()
                if strategy:
                    config = strategy.config or {}
                    strategy_interval = config.get("interval") or config.get("parameters", {}).get("interval", "15m")
                session.close()
            except Exception:
                pass
            
            trade_intent = TradeIntent(
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                side=side,
                intent_type="enter",
                notional_risk_pct=RISK_PCT_PER_TRADE,
                metadata={
                    "confidence": confidence,
                    "source": "screener_auto_execute",
                    "bar_timestamp": bar_timestamp,
                    "timeframe": strategy_interval,
                    "interval": strategy_interval,
                    **signal_data,
                },
            )
            
            # Send to risk evaluator
            decision = evaluate_intent(trade_intent)
            
            if not decision.approved:
                # Handle live slots overflow routing to Shadow Mode
                if decision.rejection_reason == "live_slots_full_routed_to_shadow":
                    # Get live slots status for logging
                    try:
                        from backend.risk.micro_mode import get_live_slots_status
                        from backend.risk.portfolio import get_current_equity
                        from backend.db import get_session
                        
                        session = get_session()
                        try:
                            current_equity = get_current_equity(session)
                        finally:
                            session.close()
                        
                        slots_status = get_live_slots_status(current_equity)
                        current_slots = slots_status["current_slots"]
                        max_slots = slots_status["max_slots"]
                    except Exception as e:
                        logger.warning(f"Failed to get live slots status: {e}")
                        current_slots = 0
                        max_slots = 0
                    
                    logger.info(
                        f"Signal routed to Shadow Mode: Live slots full ({current_slots}/{max_slots})"
                    )
                    
                    # Get current price for simulated execution
                    current_price = signal_data.get("current_price") or signal_data.get("price")
                    if current_price is None:
                        logger.error(
                            f"LIVE_SLOTS_ROUTING FAILED: No current_price in signal data "
                            f"for {signal.symbol}"
                        )
                        # Still log SIGNAL_CONFIRMED
                        if self._should_log_signal(signal.strategy_id, signal.symbol, signal_type, bar_timestamp):
                            log_activity(
                                activity_type="SIGNAL_CONFIRMED",
                                message=f"{signal_type} signal confirmed for {signal.symbol} [{strategy_name}] - live_slots_full_routed_to_shadow (no price)",
                                details={
                                    "reason": "live_slots_full_routed_to_shadow",
                                    "symbol": signal.symbol,
                                    "signal_type": signal_type,
                                    "confidence": confidence,
                                    "strategy": strategy_name,
                                    "rejection_reason": "live_slots_full_routed_to_shadow",
                                },
                            )
                            self._record_signal_logged(signal.strategy_id, signal.symbol, signal_type, bar_timestamp)
                        return
                    
                    # Calculate position sizing (same as normal execution)
                    try:
                        from backend.risk.sizing import PositionSizer
                        from backend.risk.portfolio import get_current_equity
                        from backend.db import get_session
                        # RISK_PCT_PER_TRADE already imported at module level (line 18)
                        
                        session = get_session()
                        try:
                            current_equity = get_current_equity(session)
                        finally:
                            session.close()
                        
                        position_sizer = PositionSizer()
                        # Get ATR and stop loss from metadata if available
                        metadata = trade_intent.metadata or {}
                        atr_value = metadata.get("atr")
                        explicit_stop_loss_price = metadata.get("stop_loss_price")
                        stop_loss_pct = metadata.get("stop_loss_pct")
                        
                        # Check if equity < $50: Use Scout sizing
                        use_scout_sizing = current_equity < 50.0
                        
                        sizing = position_sizer.calculate(
                            account_equity=float(current_equity),
                            risk_pct=RISK_PCT_PER_TRADE,
                            entry_price=current_price,
                            stop_loss_pct=stop_loss_pct,
                            strategy_id=trade_intent.strategy_id,
                            atr=atr_value,
                            stop_loss_price=explicit_stop_loss_price,
                            use_scout_sizing=use_scout_sizing,
                        )
                    except Exception as e:
                        logger.error(f"Failed to calculate position size for shadow routing: {e}", exc_info=True)
                        sizing = None
                    
                    if sizing and sizing.quantity > 0:
                        # Log ORDER_INTENT to Shadow Mode
                        log_activity(
                            activity_type="ORDER_INTENT",
                            message=f"Order intent (shadow): {signal_type} {sizing.quantity} {signal.symbol} @ ${current_price:.2f} [{strategy_name}]",
                            details={
                                "symbol": signal.symbol,
                                "side": side,
                                "quantity": sizing.quantity,
                                "price": current_price,
                                "notional": sizing.quantity * current_price,
                                "risk_pct": trade_intent.notional_risk_pct,
                                "strategy": strategy_name,
                                "strategy_id": signal.strategy_id,
                                "mode": "shadow_live",
                                "reason": "live_slots_full_routed_to_shadow",
                                "live_slots": f"{current_slots}/{max_slots}",
                            },
                        )
                        
                        # Create simulated Fill and position (shadow position)
                        try:
                            from backend.execution.models import Fill
                            from datetime import datetime, timezone
                            
                            simulated_fill = Fill(
                                order_id=f"shadow_slots_{trade_intent.symbol}_{trade_intent.side}_{datetime.now(timezone.utc).timestamp()}",
                                symbol=trade_intent.symbol,
                                side=trade_intent.side,
                                executed_price=current_price,
                                quantity=sizing.quantity,
                                fees=0.0,  # No fees in shadow mode
                                slippage=0.0,  # No slippage in shadow mode
                                exchange_order_id=None,  # No exchange order in shadow mode
                                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            )
                            
                            # Record fill to position tracker (creates shadow position)
                            tracker = get_position_tracker()
                            strategy_id = trade_intent.strategy_id if trade_intent.side == "buy" else None
                            tracker.record_fill(simulated_fill, strategy_id=strategy_id)
                            
                            logger.info(
                                f"[SHADOW-LIVE] Simulated position created (live slots overflow): "
                                f"{trade_intent.side} {sizing.quantity} {trade_intent.symbol} @ ${current_price:.2f}"
                            )
                        except Exception as e:
                            logger.error(f"[SHADOW-LIVE] Failed to create simulated position: {e}", exc_info=True)
                    
                    # Log SIGNAL_CONFIRMED with routing info
                    if self._should_log_signal(signal.strategy_id, signal.symbol, signal_type, bar_timestamp):
                        log_activity(
                            activity_type="SIGNAL_CONFIRMED",
                            message=f"{signal_type} signal confirmed for {signal.symbol} [{strategy_name}] - routed to Shadow Mode (live slots full)",
                            details={
                                "reason": "live_slots_full_routed_to_shadow",
                                "symbol": signal.symbol,
                                "signal_type": signal_type,
                                "confidence": confidence,
                                "strategy": strategy_name,
                                "rejection_reason": "live_slots_full_routed_to_shadow",
                                "live_slots": f"{current_slots}/{max_slots}",
                            },
                        )
                        self._record_signal_logged(signal.strategy_id, signal.symbol, signal_type, bar_timestamp)
                    
                    return
                
                # Other rejection reasons - log SIGNAL_CONFIRMED and return
                logger.warning(
                    f"AUTO-EXECUTE REJECTED: {side} {signal.symbol} "
                    f"reason={decision.rejection_reason} strategy={signal.strategy_id}"
                )
                # Log SIGNAL_CONFIRMED even if rejected by risk evaluator (shows why it didn't execute)
                if self._should_log_signal(signal.strategy_id, signal.symbol, signal_type, bar_timestamp):
                    rejection_reason = decision.rejection_reason or "risk_rejected"
                    log_activity(
                        activity_type="SIGNAL_CONFIRMED",
                        message=f"{signal_type} signal confirmed for {signal.symbol} [{strategy_name}] - {rejection_reason}",
                        details={
                            "reason": rejection_reason,
                            "symbol": signal.symbol,
                            "signal_type": signal_type,
                            "confidence": confidence,
                            "strategy": strategy_name,
                            "rejection_reason": rejection_reason,
                        },
                    )
                    self._record_signal_logged(signal.strategy_id, signal.symbol, signal_type, bar_timestamp)
                return
            
            # Get current price from signal data
            current_price = signal_data.get("current_price") or signal_data.get("price")
            
            if current_price is None:
                logger.error(
                    f"AUTO-EXECUTE FAILED: No current_price in signal data "
                    f"for {signal.symbol}"
                )
                return
            
            # EXECUTION_ALLOWED is a stateful latch: one-and-only gate that is candle-idempotent
            # Check if EXECUTION_ALLOWED was already logged for this candle BEFORE proceeding
            client = get_redis_client()
            if bar_timestamp:
                execution_allowed_key = EXECUTION_ALLOWED_LOGGED_KEY.format(
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    bar_timestamp=bar_timestamp
                )
                if client.exists(execution_allowed_key):
                    # EXECUTION_ALLOWED already logged for this candle - gate is closed
                    logger.info(
                        f"EXECUTION_ALLOWED gate closed for {signal.symbol} "
                        f"(candle={bar_timestamp}, strategy={signal.strategy_id}). "
                        f"One execution opportunity per candle max - skipping."
                    )
                    return  # Gate closed - do not proceed to execution
            
            # Get strategy interval for candle tagging
            strategy_interval = "15m"  # Default
            try:
                session = get_session()
                strategy = session.query(Strategy).filter(Strategy.id == signal.strategy_id).first()
                if strategy:
                    config = strategy.config or {}
                    strategy_interval = config.get("interval") or config.get("parameters", {}).get("interval", "15m")
                session.close()
            except Exception:
                pass
            
            # Log EXECUTION_ALLOWED (passed all gates: risk, cooldown, position checks)
            # This is the ONE-AND-ONLY gate that enforces candle-idempotency
            candle_tag = f"candle={bar_timestamp} tf={strategy_interval}" if bar_timestamp else ""
            log_activity(
                activity_type="EXECUTION_ALLOWED",
                message=f"Execution allowed: {signal_type} {signal.symbol} [{strategy_name}] - passed all gates {candle_tag}".strip(),
                details={
                    "symbol": signal.symbol,
                    "signal_type": signal_type,
                    "confidence": confidence,
                    "strategy": strategy_name,
                    "strategy_id": signal.strategy_id,
                    "bar_timestamp": bar_timestamp,
                    "timeframe": strategy_interval,
                    "risk_approved": True,
                    "mode": "shadow_live" if shadow_mode else "live",
                },
            )
            # Set the latch: mark this candle as having passed EXECUTION_ALLOWED gate
            # This prevents any further execution attempts for this candle
            if bar_timestamp:
                client.setex(execution_allowed_key, EXECUTION_ALLOWED_TTL, "1")
            
            # Set execution cooldown BEFORE attempting execution (prevents duplicate orders)
            # Cooldown is per-candle: expires when new candle opens (TTL based on timeframe)
            if side == "buy" and bar_timestamp:
                # Per-candle cooldown: key includes bar_timestamp
                cooldown_key = SIGNAL_EXECUTED_KEY.format(
                    strategy_id=signal.strategy_id, 
                    symbol=signal.symbol,
                    bar_timestamp=bar_timestamp
                )
                # Check if cooldown already exists (another execution in progress)
                if client.exists(cooldown_key):
                    logger.warning(
                        f"Execution cooldown already active for {signal.symbol} "
                        f"(strategy={signal.strategy_id}, candle={bar_timestamp}). Skipping duplicate execution."
                    )
                    return
                
                # Calculate TTL based on timeframe (cooldown expires when new candle opens)
                # Use timeframe duration + buffer to ensure it expires cleanly
                from backend.screener.aggregator import INTERVAL_MINUTES
                timeframe_minutes = INTERVAL_MINUTES.get(strategy_interval, 15)
                # TTL = timeframe duration + 60s buffer (ensures cleanup after candle close)
                cooldown_ttl = (timeframe_minutes * 60) + 60
                
                # Set cooldown immediately (before execution) to prevent race conditions
                client.setex(cooldown_key, cooldown_ttl, "1")
                logger.info(
                    f"Execution cooldown set for {signal.symbol} (strategy={signal.strategy_id}, "
                    f"candle={bar_timestamp}, TTL={cooldown_ttl}s) - prevents duplicate orders per candle"
                )
            
            # Execute the trade
            fill = await execute_trade(trade_intent, float(current_price))
            
            if fill is not None:
                logger.info(
                    f"AUTO-EXECUTE SUCCESS: {side} {signal.symbol} "
                    f"qty={fill.quantity} price=${fill.executed_price:.2f} "
                    f"confidence={confidence:.1f}% strategy={signal.strategy_id}"
                )
                # Cooldown already set above (before execution) to prevent duplicates
            else:
                logger.warning(
                    f"AUTO-EXECUTE FAILED: {side} {signal.symbol} "
                    f"(execution returned None) strategy={signal.strategy_id}"
                )
                
        except Exception as e:
            logger.error(
                f"AUTO-EXECUTE ERROR: {signal_type} {signal.symbol} "
                f"strategy={signal.strategy_id}: {e}",
                exc_info=True,
            )
    
    def _should_log_signal(
        self,
        strategy_id: str,
        symbol: str,
        signal_type: str,
        current_bar_timestamp: Optional[str] = None,
    ) -> bool:
        """
        Check if an actionable signal should be logged to activity log.
        
        Debouncing logic:
        - Log once per candle close (when bar timestamp changes)
        - Cooldown after logging (don't log again until cooldown expires OR new candle)
        - This prevents duplicate activity log entries while allowing:
          * Screener to refresh every minute (informational confidence updates)
          * Actionable signals to be logged once per candle close
        
        Args:
            strategy_id: Strategy identifier
            symbol: Trading pair symbol
            signal_type: Signal type ("BUY" or "SELL")
            current_bar_timestamp: Timestamp of current bar (for candle close detection)
            
        Returns:
            True if signal should be logged (new candle OR cooldown expired), False otherwise
        """
        client = get_redis_client()
        key = SIGNAL_LAST_LOGGED_KEY.format(
            strategy_id=strategy_id,
            symbol=symbol,
            signal_type=signal_type.upper()
        )
        
        # Get last logged timestamp and bar timestamp
        last_logged_data = client.get(key)
        
        if last_logged_data is None:
            # Never logged before - allow logging
            return True
        
        try:
            data = json.loads(last_logged_data)
            last_logged_ts = data.get("timestamp")
            last_bar_ts = data.get("bar_timestamp")
        except (json.JSONDecodeError, KeyError):
            # Invalid data - allow logging
            return True
        
        # Check if this is a new candle close (bar timestamp changed)
        if current_bar_timestamp and current_bar_timestamp != last_bar_ts:
            logger.debug(
                f"[DEBOUNCE] New candle detected for {symbol} {signal_type} "
                f"(last_bar={last_bar_ts}, current_bar={current_bar_timestamp})"
            )
            return True
        
        # Check if cooldown has expired
        if last_logged_ts:
            try:
                last_logged_dt = datetime.fromisoformat(last_logged_ts.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                elapsed = (now - last_logged_dt).total_seconds()
                
                if elapsed >= SIGNAL_LOG_COOLDOWN_SECONDS:
                    logger.debug(
                        f"[DEBOUNCE] Cooldown expired for {symbol} {signal_type} "
                        f"(elapsed={elapsed:.0f}s, cooldown={SIGNAL_LOG_COOLDOWN_SECONDS}s)"
                    )
                    return True
            except (ValueError, AttributeError):
                # Invalid timestamp - allow logging
                return True
        
        # Signal was logged recently and no new candle - skip logging
        logger.debug(
            f"[DEBOUNCE] Skipping log for {symbol} {signal_type} "
            f"(last_logged={last_logged_ts}, bar_unchanged={current_bar_timestamp == last_bar_ts if current_bar_timestamp else 'unknown'})"
        )
        return False
    
    def _record_signal_logged(
        self,
        strategy_id: str,
        symbol: str,
        signal_type: str,
        bar_timestamp: Optional[str] = None,
    ) -> None:
        """
        Record that a signal was logged to activity log.
        
        Stores timestamp and bar timestamp for debouncing checks.
        
        Args:
            strategy_id: Strategy identifier
            symbol: Trading pair symbol
            signal_type: Signal type ("BUY" or "SELL")
            bar_timestamp: Timestamp of bar that triggered the signal
        """
        client = get_redis_client()
        key = SIGNAL_LAST_LOGGED_KEY.format(
            strategy_id=strategy_id,
            symbol=symbol,
            signal_type=signal_type.upper()
        )
        
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "bar_timestamp": bar_timestamp,
        }
        
        # Store with TTL slightly longer than cooldown to ensure cleanup
        client.setex(key, SIGNAL_LOG_COOLDOWN_SECONDS + 300, json.dumps(data))
    
    def _should_evaluate(
        self,
        strategy_id: str,
        symbol: str,
        bars: List[Dict[str, Any]],
        interval: str,
    ) -> bool:
        """
        Check if we should evaluate this symbol for this strategy.
        
        This implements interval-based evaluation (debouncing per candle close):
        - Screener ticks every 60s for status + previews
        - But strategies only evaluate on candle close boundaries
        - Prevents signal spam: actionable signals emitted once per candle close
        
        Returns True if:
        - No previous evaluation recorded (first time)
        - Latest bar timestamp is newer than last evaluation (new candle closed)
        
        This ensures:
        - SETUP signals (informational) can be emitted anytime
        - ACTIONABLE SIGNALS (BUY/SELL) only emitted once per candle close
        - Cooldown prevents duplicate orders until trade placed or invalidation
        
        Args:
            strategy_id: Strategy identifier
            symbol: Trading pair symbol
            bars: List of OHLCV bar dictionaries
            interval: Strategy interval (e.g., '15m', '1h', '4h')
            
        Returns:
            True if evaluation should proceed (new candle closed), False if bar data unchanged
        """
        if not bars:
            return False
        
        client = get_redis_client()
        key = STRATEGY_LAST_EVAL_KEY.format(strategy_id=strategy_id, symbol=symbol)
        
        # Get latest bar timestamp
        latest_bar_ts = bars[-1].get("timestamp")
        if not latest_bar_ts:
            return True  # Can't compare, evaluate anyway
        
        # Get last evaluation timestamp
        last_eval_ts = client.get(key)
        
        if last_eval_ts is None:
            logger.debug(
                f"[EVAL] {symbol}: First evaluation for strategy {strategy_id} ({interval})"
            )
            return True  # First evaluation
        
        # Compare timestamps - only evaluate if new data
        if latest_bar_ts > last_eval_ts:
            logger.info(
                f"Evaluating {symbol}: new {interval} bar detected "
                f"(last={last_eval_ts}, current={latest_bar_ts})"
            )
            return True
        
        logger.debug(
            f"Skipping evaluation for {symbol}: no new bar data "
            f"(last={last_eval_ts}, current={latest_bar_ts})"
        )
        return False
    
    def _record_evaluation(
        self,
        strategy_id: str,
        symbol: str,
        bar_timestamp: str,
    ) -> None:
        """
        Record that we evaluated this symbol at this bar timestamp.
        
        Args:
            strategy_id: Strategy identifier
            symbol: Trading pair symbol
            bar_timestamp: Timestamp of the bar that was evaluated
        """
        client = get_redis_client()
        key = STRATEGY_LAST_EVAL_KEY.format(strategy_id=strategy_id, symbol=symbol)
        # Set with TTL of 7 days (cleanup old keys)
        client.setex(key, STRATEGY_LAST_EVAL_TTL, bar_timestamp)
        logger.debug(
            f"[EVAL] Recorded evaluation for {symbol} strategy={strategy_id} at {bar_timestamp}"
        )
    
    async def _run_strategy_scan(
        self,
        strategy: Any,
        symbols_bars: Dict[str, List[Dict[str, Any]]],
        interval: str = "5m",
        confidence_buy: float = 90.0,
        confidence_sell: float = 90.0,
    ) -> List[SignalResult]:
        """
        Run a single strategy scan and handle auto-execution.
        
        Only evaluates symbols that have new bar data since last evaluation.
        
        Args:
            strategy: Strategy object with evaluate() method
            symbols_bars: Dictionary of symbol -> bars
            interval: Strategy interval for bar-based evaluation check
            confidence_buy: Confidence threshold for BUY signals (default: 90.0)
            confidence_sell: Confidence threshold for SELL signals (default: 90.0)
            
        Returns:
            List of SignalResult for this strategy
        """
        strategy_id = getattr(strategy, "strategy_id", str(strategy.id) if hasattr(strategy, "id") else "unknown")
        
        # Filter symbols to only those with new bar data (interval-based evaluation)
        symbols_to_evaluate = {}
        skipped_count = 0
        no_data_symbols = []  # Symbols with no bar data at all
        
        for symbol, bars in symbols_bars.items():
            if not bars:
                # No bar data for this symbol - will create "waiting for data" placeholder
                no_data_symbols.append(symbol)
                skipped_count += 1
            elif self._should_evaluate(strategy_id, symbol, bars, interval):
                symbols_to_evaluate[symbol] = bars
            else:
                skipped_count += 1
        
        # Build candles per symbol info
        candles_per_symbol = {}
        for symbol, bars in symbols_bars.items():
            candles_per_symbol[symbol] = len(bars) if bars else 0
        
        # Determine if evaluation is on candle close
        evaluated_on_close = len(symbols_to_evaluate) > 0
        
        if skipped_count > 0 or evaluated_on_close:
            logger.info(
                f"[EVAL] Strategy {strategy_id} ({interval}): "
                f"evaluated_on_close={evaluated_on_close}, "
                f"evaluating={len(symbols_to_evaluate)} symbols, "
                f"skipped={skipped_count} (no new bar data), "
                f"no_data={len(no_data_symbols)}, "
                f"candles_per_symbol={candles_per_symbol}"
            )
        
        # Scan symbols with new bar data
        results = await scan_with_strategy(
            strategy, symbols_to_evaluate, confidence_buy, confidence_sell
        ) if symbols_to_evaluate else []
        
        # Create "waiting for data" placeholders for symbols with no bar data
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for symbol in no_data_symbols:
            placeholder = SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=strategy_id,
                indicators={
                    "note": "waiting_for_data", 
                    "interval": interval,
                    # Include frontend indicators as None for consistency
                    "bb_position": None,
                    "adx": None,
                    "atr_ratio": None,
                },
                timestamp=timestamp,
            )
            results.append(placeholder)
        
        # Record evaluation timestamps for symbols that were evaluated
        for symbol, bars in symbols_to_evaluate.items():
            if bars:
                bar_timestamp = bars[-1].get("timestamp")
                if bar_timestamp:
                    self._record_evaluation(strategy_id, symbol, bar_timestamp)
        
        # Store results in Redis (includes placeholders for symbols without data)
        # Also returns any signals restored by threshold changes for auto-execution
        restored_signals = self._store_strategy_results(
            strategy_id,
            results or [],
            total_scanned=len(symbols_bars),
            confidence_buy=confidence_buy,
            confidence_sell=confidence_sell,
        )
        
        # Log strategy scan completion with all requested fields
        logger.info(
            f"[SCREENER-EVAL] Strategy {strategy_id} ({interval}): "
            f"evaluated_on_close={evaluated_on_close}, "
            f"candles_per_symbol={candles_per_symbol}, "
            f"symbols_evaluated={len(symbols_to_evaluate)}, "
            f"symbols_skipped={skipped_count}, "
            f"signals_generated={len([r for r in results if getattr(r, 'signal_type', 'NONE') in ('BUY', 'SELL')])}"
        )
        
        # Filter out placeholders before processing for execution
        actionable_results = [r for r in results if r.indicators.get("note") != "waiting_for_data"]
        
        # Combine new actionable results with restored signals
        all_signals_to_execute = actionable_results + restored_signals
        
        if not all_signals_to_execute:
            return results  # Return all results (including placeholders) for frontend
        
        # Sort signals by confidence (descending) before processing
        # This ensures higher-confidence signals are processed first, which is important
        # when position limits (e.g., micro mode max 1 position) are active
        all_signals_to_execute.sort(key=lambda s: getattr(s, 'confidence', 0.0), reverse=True)
        
        # Log signals being processed for auto-execution (now sorted by confidence)
        if all_signals_to_execute:
            signal_summary = ", ".join([f"{s.symbol} {getattr(s, 'signal_type', getattr(s, 'signal', 'NONE'))} ({getattr(s, 'confidence', 0.0):.1f}%)" 
                                       for s in all_signals_to_execute[:5]])
            logger.info(
                f"[AUTO-EXECUTE] Processing {len(all_signals_to_execute)} signal(s) for strategy {strategy_id} (sorted by confidence): {signal_summary}"
            )
        
        # Check trading status
        trading_enabled = get_trading_enabled()
        
        # Process actionable signals and restored signals for potential auto-execution
        # Signals are now processed in confidence order (highest first)
        for signal in all_signals_to_execute:
            await self._process_auto_execution(signal, trading_enabled)
        
        return results
    
    async def run_scan(self) -> List[ScreenerResult]:
        """
        Execute a single scan of all symbols.
        
        This method:
        1. Runs the default indicator-based scan
        2. Loads enabled strategies from database
        3. Runs each strategy's evaluate() method
        4. Stores per-strategy results
        5. Handles auto-execution for high-confidence signals
        
        Returns:
            List of ScreenerResult objects
        """
        start_time = time.monotonic()
        
        logger.info("=" * 60)
        logger.info(f"[SCAN] Starting screener scan (interval={self.interval})")
        logger.info("=" * 60)
        
        # Fetch bars for all symbols
        symbols_bars = await self._get_all_symbols_bars()
        
        # Run the default indicator-based scan
        results = await self.engine.scan_all(symbols_bars)
        
        # Store results in Redis
        self._store_results(results)
        
        # Count signals from default scan
        buy_signals = sum(1 for r in results if r.signal_type == "BUY")
        sell_signals = sum(1 for r in results if r.signal_type == "SELL")
        insufficient_data = sum(1 for r in results if r.indicators.get("error") == "insufficient_data")
        
        # Log and store actionable signals (LOG ONLY - no execution for default scan)
        self._log_and_store_signals(results)
        
        # Run strategy-based scans with auto-execution
        await self._run_strategy_scans(symbols_bars)
        
        elapsed = time.monotonic() - start_time
        
        logger.info("=" * 60)
        logger.info(
            f"[SCAN] Complete in {elapsed:.2f}s: {len(results)} symbols "
            f"(BUY: {buy_signals}, SELL: {sell_signals}, insufficient_data: {insufficient_data})"
        )
        logger.info("=" * 60)
        
        return results
    
    async def _run_strategy_scans(
        self,
        symbols_bars: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        """
        Run scans for all enabled strategies.
        
        Loads strategies from database and runs their evaluate() method
        on all symbols, handling auto-execution for qualifying signals.
        
        Each strategy uses its configured interval for bar fetching and
        interval-based evaluation (only re-evaluate when new bars form).
        
        Args:
            symbols_bars: Dictionary of symbol -> bars (used as fallback/for symbol list)
        """
        # Load enabled strategies from database
        db_strategies = _load_enabled_strategies()
        
        if not db_strategies:
            logger.debug("No enabled strategies found for strategy-based scanning")
            return
        
        logger.info(f"Running strategy scans for {len(db_strategies)} enabled strategies")
        
        # Import strategy implementations dynamically
        # Production strategies
        from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy
        from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig
        from research.strategies.volatility_breakout.strategy import VolatilityBreakoutStrategy
        from research.strategies.volatility_breakout.config import VolatilityBreakoutConfig
        from research.strategies.htf_trend.strategy import HTFTrendStrategy
        from research.strategies.htf_trend.config import HTFTrendConfig
        
        # Get symbols to scan (use keys from symbols_bars)
        symbols = list(symbols_bars.keys())
        
        for db_strategy in db_strategies:
            strategy_name = db_strategy.name.lower()
            strategy_id = str(db_strategy.id)
            
            try:
                # Create strategy instance based on name
                config_data = db_strategy.config or {}
                strategy = None
                strategy_wrapper = None  # Some strategies use specialized wrappers
                
                # Get strategy's configured interval (default to 5m)
                strategy_interval = config_data.get("interval", "5m")
                
                # Strategy 1: VWAP Mean Reversion
                if "vwap" in strategy_name and "mean" in strategy_name:
                    params = config_data.get("parameters", {})
                    config = VWAPMeanReversionConfig(
                        strategy_id=strategy_id,
                        symbol=config_data.get("symbol", "BTC/USD"),
                        interval=config_data.get("interval", "15m"),
                        htf_interval=config_data.get("htf_interval", "1h"),
                        notional_risk_pct=config_data.get("max_risk_pct", 1.0),
                        dev_threshold_ATR=params.get("dev_threshold_ATR", 0.5),
                        rsi_oversold=params.get("rsi_oversold", 30.0),
                        rsi_overbought=params.get("rsi_overbought", 70.0),
                        atr_stop_mult=params.get("atr_stop_mult", 1.5),
                        tp1_R=params.get("tp1_R", 1.2),
                        tp2_R=params.get("tp2_R", 2.5),
                    )
                    strategy = VWAPMeanReversionStrategy(config)
                
                # Strategy 2: Volatility Breakout
                elif "volatility" in strategy_name and "breakout" in strategy_name:
                    params = config_data.get("parameters", {})
                    config = VolatilityBreakoutConfig(
                        strategy_id=strategy_id,
                        symbol=config_data.get("symbol", "BTC/USD"),
                        interval=config_data.get("interval", "15m"),
                        htf_interval=config_data.get("htf_interval", "1h"),
                        notional_risk_pct=config_data.get("max_risk_pct", 1.0),
                        squeeze_percentile=params.get("squeeze_percentile", 10.0),
                        vol_breakout_mult=params.get("vol_breakout_mult", 1.5),
                        retest_window_bars=params.get("retest_window_bars", 6),
                        atr_stop_mult=params.get("atr_stop_mult", 1.8),
                        atr_target1_mult=params.get("atr_target1_mult", 2.0),
                        atr_target2_mult=params.get("atr_target2_mult", 3.5),
                    )
                    strategy = VolatilityBreakoutStrategy(config)
                
                # Strategy 3: HTF Trend Pullback
                elif "htf" in strategy_name and "trend" in strategy_name:
                    params = config_data.get("parameters", {})
                    config = HTFTrendConfig(
                        strategy_id=strategy_id,
                        symbol=config_data.get("symbol", "BTC/USD"),
                        interval=config_data.get("interval", "1h"),
                        htf_interval=config_data.get("htf_interval", "4h"),
                        notional_risk_pct=config_data.get("max_risk_pct", 1.0),
                        pullback_max_ATR=params.get("pullback_max_ATR", 1.5),
                        atr_stop_mult=params.get("atr_stop_mult", 1.5),
                        tp1_R=params.get("tp1_R", 1.5),
                        tp2_R=params.get("tp2_R", 3.0),
                        max_hours_in_trade=params.get("max_hours_in_trade", 24),
                    )
                    strategy = HTFTrendStrategy(config)
                
                if strategy is not None:
                    # Wrap the strategy with an evaluate adapter
                    if strategy_wrapper is None:
                        strategy_wrapper = _StrategyEvaluateAdapter(strategy, strategy_id)
                    
                    # Get confidence thresholds from strategy filters (default: 90)
                    filters = config_data.get("filters", {})
                    confidence_buy = filters.get("confidence_buy", 90)
                    confidence_sell = filters.get("confidence_sell", 90)
                    
                    # Validate and clamp thresholds to valid range (50-100)
                    def clamp_threshold(value: float, name: str) -> float:
                        if value < 50 or value > 100:
                            logger.warning(
                                f"Strategy {strategy_name}: {name}={value} outside valid range 50-100, clamping"
                            )
                            return max(50, min(100, value))
                        return float(value)
                    
                    confidence_buy = clamp_threshold(confidence_buy, "confidence_buy")
                    confidence_sell = clamp_threshold(confidence_sell, "confidence_sell")
                    
                    # Fetch bars at strategy's configured interval
                    strategy_symbols_bars = {}
                    for symbol in symbols:
                        bars = await self._get_recent_bars(
                            symbol, self.bars_to_fetch, target_interval=strategy_interval
                        )
                        strategy_symbols_bars[symbol] = bars
                    
                    logger.info(
                        f"[STRATEGY] {strategy_name} (id={strategy_id}): "
                        f"interval={strategy_interval}, symbols={len(strategy_symbols_bars)}, "
                        f"confidence_buy={confidence_buy}, confidence_sell={confidence_sell}"
                    )
                    
                    # Run the scan with strategy-specific interval bars and thresholds
                    await self._run_strategy_scan(
                        strategy_wrapper, strategy_symbols_bars, interval=strategy_interval,
                        confidence_buy=confidence_buy, confidence_sell=confidence_sell,
                    )
                else:
                    logger.warning(f"Unknown strategy type: {strategy_name} (id={strategy_id})")
                    
            except Exception as e:
                logger.error(f"Error running strategy {strategy_name}: {e}", exc_info=True)
    
    async def _run_loop(self) -> None:
        """Main scan loop."""
        logger.info("Screener service started")
        
        while self._running:
            try:
                await self.run_scan()
            except Exception as e:
                logger.error(f"Scan error: {e}", exc_info=True)
            
            # Wait for next scan interval
            await asyncio.sleep(self.scan_interval)
        
        logger.info("Screener service stopped")
    
    async def start(self) -> None:
        """Start the background scan loop."""
        if self._running:
            logger.warning("Screener service already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Screener service starting...")
    
    async def stop(self) -> None:
        """Stop the background scan loop."""
        if not self._running:
            return
        
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
        logger.info("Screener service stopped")
    
    def get_results(self) -> List[ScreenerResult]:
        """
        Get the latest cached results from Redis.
        
        Returns:
            List of ScreenerResult objects
        """
        client = get_redis_client()
        
        try:
            data = client.get(SCREENER_RESULTS_KEY)
            if data:
                results_data = json.loads(data)
                return [ScreenerResult.from_dict(r) for r in results_data]
        except Exception as e:
            logger.error(f"Failed to get results from Redis: {e}")
        
        return []
    
    def get_last_scan_time(self) -> Optional[str]:
        """
        Get the timestamp of the last scan.
        
        Returns:
            ISO8601 timestamp or None
        """
        client = get_redis_client()
        
        try:
            return client.get(SCREENER_LAST_SCAN_KEY)
        except Exception as e:
            logger.error(f"Failed to get last scan time: {e}")
        
        return None
    
    def get_strategy_results(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached results for a specific strategy.
        
        Args:
            strategy_id: Strategy identifier
            
        Returns:
            Dictionary with strategy results or None if not found
        """
        client = get_redis_client()
        
        try:
            key = SCREENER_STRATEGY_RESULTS_KEY.format(strategy_id=strategy_id)
            data = client.get(key)
            if data:
                result = json.loads(data)
                # Ensure frontend indicators are present in all results (even if None)
                # This handles cases where cached results were created before indicators were added
                if "results" in result:
                    for res in result["results"]:
                        indicators = res.get("indicators", {})
                        if "bb_position" not in indicators:
                            indicators["bb_position"] = None
                        if "adx" not in indicators:
                            indicators["adx"] = None
                        if "atr_ratio" not in indicators:
                            indicators["atr_ratio"] = None
                # Add trading_enabled status
                result["trading_enabled"] = get_trading_enabled()
                return result
        except Exception as e:
            logger.error(f"Failed to get strategy results for {strategy_id}: {e}")
        
        return None
