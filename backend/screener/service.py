"""Background service for running the screener at regular intervals.

This service scans all subscribed symbols, calculates indicators,
and stores results in Redis for consumption by the API and frontend.

With auto-execution enabled (T60 trading_enabled=True), high-confidence
signals are automatically sent to the risk evaluator and executed.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.config import ACCOUNT_EQUITY, RISK_PCT_PER_TRADE, CONFIDENCE_THRESHOLD_PCT
from backend.db import get_session
from backend.db.models import Strategy
from backend.ingestor.config import (
    get_symbols,
    get_max_spread_bps,
    get_min_24h_volume_usd,
    get_enforce_whitelist_in_shadow,
)
from backend.ingestor.symbols import (
    normalize_symbol,
    get_symbol_spread,
    get_symbol_volume,
    is_in_live_universe,
)
from backend.redis import get_redis_client
from backend.redis.keys import (
    INGESTOR_ACTIVE_SYMBOLS_KEY,
    MARKET_OHLCV_STREAM,
    SCREENER_LAST_SCAN_KEY,
    SCREENER_RESULTS_KEY,
    SCREENER_RESULTS_TTL,
    SCREENER_SIGNALS_HISTORY_KEY,
    SCREENER_STRATEGY_RESULTS_KEY,
    SHADOW_LIVE_MODE_KEY,
    SIGNAL_COOLDOWN_SECONDS,
    SIGNAL_EXECUTED_KEY,
    STRATEGY_LAST_EVAL_KEY,
    STRATEGY_LAST_EVAL_TTL,
    TRADING_ENABLED_KEY,
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
    
    Returns:
        List of Strategy objects with status='active'
    """
    session = get_session()
    try:
        strategies = session.query(Strategy).filter(Strategy.status == "active").all()
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
                indicators={"current_price": current_price, "note": "no_signal_conditions_met"},
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
                logger.info(f"Using {len(symbols)} symbols from ingestor")
                return symbols
        except Exception as e:
            logger.warning(f"Failed to get symbols from Redis: {e}")
        
        # Fallback to config
        fallback_symbols = get_symbols()
        # Normalize fallback symbols as well
        fallback_symbols = [normalize_symbol(s) for s in fallback_symbols]
        logger.info(f"Using {len(fallback_symbols)} symbols from config (fallback)")
        return fallback_symbols
    
    async def _apply_global_filters(
        self,
        symbols: List[str],
        strategy_id: str,
    ) -> Tuple[List[str], Dict[str, str]]:
        """
        Apply global filters: whitelist → liquidity → spread (fail-fast).
        
        Filters symbols before strategy evaluation to ensure only tradeable
        symbols are evaluated according to Global Crypto Pillars:
        - Pillar 1: Liquidity (24h volume >= $10M)
        - Pillar 2: Spread Efficiency (bid-ask spread <= 15 bps)
        - Pillar 3: Tier 1 Whitelist (only high-liquidity assets in shadow mode)
        
        Args:
            symbols: List of symbols to filter
            strategy_id: Strategy identifier for logging
            
        Returns:
            Tuple of (filtered_symbols, skip_reasons_dict) where skip_reasons_dict
            maps symbol -> reason string for filtered symbols
        """
        client = get_redis_client()
        
        # Check if in shadow mode
        try:
            shadow_mode_value = client.get(SHADOW_LIVE_MODE_KEY)
            shadow_mode = shadow_mode_value == b"shadow" if shadow_mode_value else False
        except Exception as e:
            logger.debug(f"Failed to check shadow mode: {e}, defaulting to False")
            shadow_mode = False
        
        # Get filter thresholds
        max_spread_bps = get_max_spread_bps()
        min_volume_usd = get_min_24h_volume_usd()
        enforce_whitelist = get_enforce_whitelist_in_shadow()
        
        filtered = []
        skip_reasons = {}
        skip_counts = {"whitelist": 0, "liquidity": 0, "spread": 0}
        
        for symbol in symbols:
            skip_reason = None
            
            # Filter 1: Whitelist (if enabled in shadow mode)
            if shadow_mode and enforce_whitelist:
                if not is_in_live_universe(symbol):
                    skip_reason = "not in whitelist"
                    skip_counts["whitelist"] += 1
                    logger.debug(
                        f"[FILTER] SKIP: {symbol} [whitelist_filter] - not in live universe"
                    )
                    log_activity(
                        activity_type="signal",
                        message=f"SKIP: {symbol} [whitelist_filter] - not in live universe",
                        details={
                            "symbol": symbol,
                            "filter": "whitelist",
                            "strategy": strategy_id,
                            "reason": skip_reason,
                        },
                    )
            
            # Filter 2: Liquidity threshold
            if not skip_reason:
                volume = get_symbol_volume(symbol)
                if volume is not None and volume < min_volume_usd:
                    skip_reason = f"volume ${volume:,.0f} < threshold ${min_volume_usd:,.0f}"
                    skip_counts["liquidity"] += 1
                    logger.debug(
                        f"[FILTER] SKIP: {symbol} [liquidity_filter] - volume ${volume:,.0f} < threshold ${min_volume_usd:,.0f}"
                    )
                    log_activity(
                        activity_type="signal",
                        message=f"SKIP: {symbol} [liquidity_filter] - volume ${volume:,.0f}",
                        details={
                            "symbol": symbol,
                            "filter": "liquidity",
                            "volume": volume,
                            "threshold": min_volume_usd,
                            "strategy": strategy_id,
                            "reason": skip_reason,
                        },
                    )
            
            # Filter 3: Spread threshold
            if not skip_reason:
                spread_bps = get_symbol_spread(symbol)
                if spread_bps is not None and spread_bps > max_spread_bps:
                    skip_reason = f"spread {spread_bps:.1f} bps > threshold {max_spread_bps:.1f} bps"
                    skip_counts["spread"] += 1
                    logger.debug(
                        f"[FILTER] SKIP: {symbol} [spread_filter] - spread {spread_bps:.1f} bps > threshold {max_spread_bps:.1f} bps"
                    )
                    log_activity(
                        activity_type="signal",
                        message=f"SKIP: {symbol} [spread_filter] - spread {spread_bps:.1f} bps",
                        details={
                            "symbol": symbol,
                            "filter": "spread",
                            "spread_bps": spread_bps,
                            "threshold": max_spread_bps,
                            "strategy": strategy_id,
                            "reason": skip_reason,
                        },
                    )
            
            if skip_reason:
                skip_reasons[symbol] = skip_reason
            else:
                filtered.append(symbol)
        
        # Log filter summary
        total_skipped = len(skip_reasons)
        if total_skipped > 0:
            logger.info(
                f"[FILTER] Strategy {strategy_id}: Filtered {len(symbols)} → {len(filtered)} symbols "
                f"(skipped: {total_skipped}, whitelist={skip_counts['whitelist']}, "
                f"liquidity={skip_counts['liquidity']}, spread={skip_counts['spread']})"
            )
        
        return filtered, skip_reasons
    
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
    ) -> None:
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
                    existing_by_symbol[r.get("symbol")] = r
        except Exception as e:
            logger.debug(f"[STORE] Could not load existing results: {e}")
        
        # Re-apply confidence thresholds to preserved results
        # This ensures signals reflect current threshold configuration
        # Both filtering DOWN (BUY→NONE) and restoring UP (NONE→BUY) are handled
        filtered_down_count = 0
        restored_up_count = 0
        
        for symbol, result in existing_by_symbol.items():
            signal_type = result.get("signal_type", "NONE")
            confidence = result.get("confidence", 0.0)
            indicators = result.get("indicators", {})
            
            # Get trigger conditions from indicators (strategy-specific)
            crossover_detected = indicators.get("crossover_detected", False)
            roc_meets_threshold = indicators.get("roc_meets_threshold", False)
            is_ranging = indicators.get("is_ranging", False)
            direction = indicators.get("direction", "neutral")
            
            # Determine if signal conditions are met (based on strategy type)
            has_buy_condition = (crossover_detected or roc_meets_threshold or is_ranging) and direction == "bullish"
            has_sell_condition = (crossover_detected or roc_meets_threshold or is_ranging) and direction == "bearish"
            
            # Filter DOWN: BUY/SELL → NONE when below threshold
            if signal_type == "BUY" and confidence < confidence_buy:
                result["signal_type"] = "NONE"
                indicators["threshold_filtered"] = True
                indicators["original_signal"] = "BUY"
                filtered_down_count += 1
            elif signal_type == "SELL" and confidence < confidence_sell:
                result["signal_type"] = "NONE"
                indicators["threshold_filtered"] = True
                indicators["original_signal"] = "SELL"
                filtered_down_count += 1
            
            # Restore UP: NONE → BUY/SELL when conditions met AND above threshold
            elif signal_type == "NONE":
                if has_buy_condition and confidence >= confidence_buy:
                    result["signal_type"] = "BUY"
                    indicators["threshold_filtered"] = False
                    # Log restored signal to activity
                    log_activity(
                        activity_type="signal",
                        message=f"BUY signal for {symbol} [{strategy_name}]",
                        details={
                            "symbol": symbol,
                            "signal_type": "BUY",
                            "confidence": confidence,
                            "strategy": strategy_name,
                            "auto_execute": False,
                            "reason": "restored_by_threshold_change",
                        },
                    )
                    restored_up_count += 1
                elif has_sell_condition and confidence >= confidence_sell:
                    result["signal_type"] = "SELL"
                    indicators["threshold_filtered"] = False
                    # Log restored signal to activity
                    log_activity(
                        activity_type="signal",
                        message=f"SELL signal for {symbol} [{strategy_name}]",
                        details={
                            "symbol": symbol,
                            "signal_type": "SELL",
                            "confidence": confidence,
                            "strategy": strategy_name,
                            "auto_execute": False,
                            "reason": "restored_by_threshold_change",
                        },
                    )
                    restored_up_count += 1
        
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
            return
        
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
        
        # Add all new results
        for symbol, result in new_by_symbol.items():
            merged_results.append(result)
        
        # Add existing results for symbols not in new results
        for symbol, result in existing_by_symbol.items():
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
    
    async def _process_auto_execution(
        self,
        signal: SignalResult,
        trading_enabled: bool,
        confidence_buy: float = CONFIDENCE_THRESHOLD_PCT,
        confidence_sell: float = CONFIDENCE_THRESHOLD_PCT,
    ) -> None:
        """
        Process a signal for potential auto-execution.
        
        If trading is enabled and confidence meets threshold, create TradeIntent,
        send to risk evaluator, and execute if approved.
        
        Args:
            signal: SignalResult with confidence
            trading_enabled: Whether trading is currently enabled
            confidence_buy: Confidence threshold for BUY signals (default: 90%)
            confidence_sell: Confidence threshold for SELL signals (default: 90%)
        """
        confidence = signal.confidence
        # Get signal type (handle both signal and signal_type attributes)
        signal_type = getattr(signal, 'signal_type', None) or getattr(signal, 'signal', 'NONE')
        # Use strategy-specific threshold based on signal direction
        threshold = confidence_buy if signal_type.upper() == "BUY" else confidence_sell
        
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
        
        if not trading_enabled:
            # Log signal but don't execute (only actionable BUY/SELL signals)
            if signal_type.upper() in ("BUY", "SELL"):
                logger.info(
                    f"SIGNAL (trading-off): {signal_type} {signal.symbol} "
                    f"confidence={confidence:.1f}% strategy={signal.strategy_id}"
                )
                # Log to activity feed for strategy signals
                log_activity(
                    activity_type="signal",
                    message=f"{signal_type} signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": "trading_disabled",
                    },
                )
            else:
                # NONE signals logged at DEBUG level for debugging purposes
                logger.debug(
                    f"SIGNAL (trading-off): {signal_type} {signal.symbol} "
                    f"confidence={confidence:.1f}% strategy={signal.strategy_id}"
                )
            return
        
        if confidence < threshold:
            # Below threshold - log rejection (INFO level - this is normal behavior)
            logger.info(
                f"Signal rejected: {signal.symbol} confidence {confidence:.1f}% < threshold {threshold}%"
            )
            # Log to activity feed for below-threshold signals (only log non-NONE signals)
            if signal_type.upper() != "NONE":
                log_activity(
                    activity_type="signal",
                    message=f"{signal_type} signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": f"below_threshold ({confidence:.1f}% < {threshold}%)",
                    },
                )
            return
        
        # No-shorting: Only execute SELL signals if we own the asset
        if signal_type.upper() == "SELL":
            tracker = get_position_tracker()
            
            # Check if we have a position to sell (no shorting)
            if not tracker.has_position(signal.symbol):
                logger.info(f"SELL signal ignored for {signal.symbol}: no position (no shorting)")
                log_activity(
                    activity_type="signal",
                    message=f"SELL signal ignored for {signal.symbol} [{strategy_name}] - no position",
                    details={
                        "reason": "no_shorting",
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "confidence": confidence,
                        "strategy": strategy_name,
                    },
                )
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
                log_activity(
                    activity_type="signal",
                    message=f"BUY signal skipped for {signal.symbol} [{strategy_name}] - position exists",
                    details={
                        "reason": "position_exists",
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "confidence": confidence,
                        "strategy": strategy_name,
                    },
                )
                return
            
            # Cooldown check: skip if signal was recently executed
            client = get_redis_client()
            # Get bar_timestamp from signal data for per-candle cooldown
            signal_data = getattr(signal, 'indicators', None) or getattr(signal, 'metadata', {})
            bar_timestamp = signal_data.get("bar_timestamp") or signal_data.get("timestamp") or ""
            
            # Use per-candle cooldown key if bar_timestamp is available, otherwise use legacy key
            if bar_timestamp:
                cooldown_key = SIGNAL_EXECUTED_KEY.format(
                    strategy_id=signal.strategy_id, symbol=signal.symbol, bar_timestamp=bar_timestamp
                )
            else:
                # Fallback to legacy key format if bar_timestamp is missing
                from backend.redis.keys import SIGNAL_EXECUTED_KEY_LEGACY
                cooldown_key = SIGNAL_EXECUTED_KEY_LEGACY.format(
                    strategy_id=signal.strategy_id, symbol=signal.symbol
                )
                logger.warning(
                    f"Missing bar_timestamp for {signal.symbol}, using legacy cooldown key"
                )
            
            if client.exists(cooldown_key):
                logger.info(
                    f"BUY signal skipped for {signal.symbol}: cooldown active "
                    f"(strategy={signal.strategy_id})"
                )
                log_activity(
                    activity_type="signal",
                    message=f"BUY signal skipped for {signal.symbol} [{strategy_name}] - cooldown active",
                    details={
                        "reason": "cooldown_active",
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "confidence": confidence,
                        "strategy": strategy_name,
                    },
                )
                return
        
        # Confidence meets threshold and trading is enabled - attempt execution
        logger.info(
            f"Signal approved: {signal.symbol} {signal_type} confidence={confidence:.1f}% "
            f"threshold={threshold:.1f}% strategy={signal.strategy_id}"
        )
        
        try:
            # Create TradeIntent from signal
            side = signal_type.lower()  # "buy" or "sell"
            # Get metadata/indicators (handle both attribute names)
            signal_data = getattr(signal, 'indicators', None) or getattr(signal, 'metadata', {})
            
            trade_intent = TradeIntent(
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                side=side,
                intent_type="enter",
                notional_risk_pct=RISK_PCT_PER_TRADE,
                metadata={
                    "confidence": confidence,
                    "source": "screener_auto_execute",
                    **signal_data,
                },
            )
            
            # Send to risk evaluator
            decision = evaluate_intent(trade_intent)
            
            if not decision.approved:
                logger.warning(
                    f"AUTO-EXECUTE REJECTED: {side} {signal.symbol} "
                    f"reason={decision.rejection_reason} strategy={signal.strategy_id} "
                    f"confidence={confidence:.1f}%"
                )
                # Log rejection to activity feed for visibility
                log_activity(
                    activity_type="signal",
                    message=f"{side.upper()} signal rejected for {signal.symbol} [{strategy_name}] - {decision.rejection_reason}",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": decision.rejection_reason,
                    },
                )
                return
            
            # TICKET-705: Log EXECUTION_ALLOWED before calling execute_trade()
            bar_timestamp = signal_data.get("bar_timestamp") or signal_data.get("timestamp")
            strategy_interval = signal_data.get("timeframe") or signal_data.get("interval") or "15m"
            candle_tag = f"candle={bar_timestamp} tf={strategy_interval}" if bar_timestamp else ""
            
            log_activity(
                activity_type="EXECUTION_ALLOWED",
                message=f"Execution allowed: {side.upper()} {signal.symbol} [{signal.strategy_id}] - passed all gates {candle_tag}".strip(),
                details={
                    "symbol": signal.symbol,
                    "side": side,
                    "strategy": signal.strategy_id,
                    "strategy_id": signal.strategy_id,
                    "confidence": confidence,
                    "bar_timestamp": bar_timestamp,
                    "timeframe": strategy_interval,
                    "intent_id": decision.intent_id,
                },
            )
            
            # Get current price from signal data
            current_price = signal_data.get("current_price") or signal_data.get("price")
            
            if current_price is None:
                logger.error(
                    f"AUTO-EXECUTE FAILED: No current_price in signal data "
                    f"for {signal.symbol}"
                )
                return
            
            # Execute the trade
            fill = await execute_trade(trade_intent, float(current_price))
            
            if fill is not None:
                logger.info(
                    f"AUTO-EXECUTE SUCCESS: {side} {signal.symbol} "
                    f"qty={fill.quantity} price=${fill.executed_price:.2f} "
                    f"confidence={confidence:.1f}% strategy={signal.strategy_id}"
                )
                
                # Set cooldown key after successful BUY to prevent repeated buys
                if side == "buy":
                    client = get_redis_client()
                    cooldown_key = SIGNAL_EXECUTED_KEY.format(
                        strategy_id=signal.strategy_id, symbol=signal.symbol
                    )
                    client.setex(cooldown_key, SIGNAL_COOLDOWN_SECONDS, "1")
                    logger.info(
                        f"Cooldown set for {signal.symbol} (strategy={signal.strategy_id}, "
                        f"TTL={SIGNAL_COOLDOWN_SECONDS}s)"
                    )
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
    
    def _should_evaluate(
        self,
        strategy_id: str,
        symbol: str,
        bars: List[Dict[str, Any]],
        interval: str,
    ) -> bool:
        """
        Check if we should evaluate this symbol for this strategy.
        
        Returns True if:
        - No previous evaluation recorded (first time)
        - Latest bar timestamp is newer than last evaluation
        
        Args:
            strategy_id: Strategy identifier
            symbol: Trading pair symbol
            bars: List of OHLCV bar dictionaries
            interval: Strategy interval (e.g., '5m', '1h', '4h')
            
        Returns:
            True if evaluation should proceed, False if bar data unchanged
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
        
        if skipped_count > 0:
            logger.info(
                f"[EVAL] Strategy {strategy_id} ({interval}): "
                f"Evaluating {len(symbols_to_evaluate)} symbols, "
                f"skipped {skipped_count} (no new bar data), "
                f"no_data={len(no_data_symbols)}"
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
                indicators={"note": "waiting_for_data", "interval": interval},
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
        self._store_strategy_results(
            strategy_id,
            results or [],
            total_scanned=len(symbols_bars),
            confidence_buy=confidence_buy,
            confidence_sell=confidence_sell,
        )
        
        # Filter out placeholders before processing for execution
        actionable_results = [r for r in results if r.indicators.get("note") != "waiting_for_data"]
        
        # Log actionable signals for debugging
        buy_signals = [r for r in actionable_results if getattr(r, 'signal_type', None) == 'BUY' or getattr(r, 'signal', None) == 'BUY']
        sell_signals = [r for r in actionable_results if getattr(r, 'signal_type', None) == 'SELL' or getattr(r, 'signal', None) == 'SELL']
        none_signals = [r for r in actionable_results if getattr(r, 'signal_type', None) == 'NONE' or (getattr(r, 'signal_type', None) is None and getattr(r, 'signal', None) == 'NONE')]
        
        logger.info(
            f"[EVAL] Strategy {strategy_id} ({interval}): "
            f"Results breakdown: {len(buy_signals)} BUY, {len(sell_signals)} SELL, {len(none_signals)} NONE "
            f"(total actionable: {len(actionable_results)}, total results: {len(results)})"
        )
        
        if buy_signals or sell_signals:
            for sig in buy_signals + sell_signals:
                sig_type = getattr(sig, 'signal_type', None) or getattr(sig, 'signal', 'UNKNOWN')
                conf = getattr(sig, 'confidence', 0.0)
                logger.info(
                    f"[EVAL] Actionable signal: {sig_type} {sig.symbol} "
                    f"confidence={conf:.1f}% (threshold: {confidence_buy if sig_type == 'BUY' else confidence_sell}%)"
                )
        
        if not actionable_results:
            logger.debug(
                f"[EVAL] Strategy {strategy_id}: No actionable results "
                f"(total results: {len(results)}, filtered: {len([r for r in results if r.indicators.get('note') == 'waiting_for_data'])})"
            )
            return results  # Return all results (including placeholders) for frontend
        
        # Check trading status
        trading_enabled = get_trading_enabled()
        
        if not trading_enabled:
            logger.info(
                f"[EVAL] Strategy {strategy_id}: Trading disabled, skipping auto-execution "
                f"({len(buy_signals)} BUY, {len(sell_signals)} SELL actionable signals)"
            )
        else:
            logger.info(
                f"[EVAL] Strategy {strategy_id}: Trading enabled, processing "
                f"{len(buy_signals)} BUY and {len(sell_signals)} SELL signals for auto-execution"
            )
        
        # Process only actionable signals for potential auto-execution
        # (skip placeholders and waiting_for_data results)
        for signal in actionable_results:
            await self._process_auto_execution(signal, trading_enabled, confidence_buy, confidence_sell)
        
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
        
        # Debug: Log that scan is starting
        logger.info(f"[SCAN] run_scan() called at {datetime.now(timezone.utc).isoformat()}")
        
        # Fetch bars for all symbols
        symbols_bars = await self._get_all_symbols_bars()
        logger.info(f"[SCAN] Fetched bars for {len(symbols_bars)} symbols")
        
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
        logger.info("[SCAN] Starting strategy-based scans...")
        try:
            await self._run_strategy_scans(symbols_bars)
            logger.info("[SCAN] Strategy-based scans completed")
        except Exception as e:
            logger.error(f"[SCAN] Error in strategy-based scans: {e}", exc_info=True)
        
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
        logger.info(f"Strategy names: {[s.name for s in db_strategies]}")
        
        # Import strategy implementations dynamically
        logger.info("[STRATEGY_SCANS] Importing strategy classes...")
        # Production strategies (new)
        from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy
        from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig
        from research.strategies.htf_trend.strategy import HTFTrendStrategy
        from research.strategies.htf_trend.config import HTFTrendConfig
        from research.strategies.volatility_breakout.strategy import VolatilityBreakoutStrategy
        from research.strategies.volatility_breakout.config import VolatilityBreakoutConfig
        logger.info("[STRATEGY_SCANS] Strategy classes imported successfully")
        # Legacy strategies
        from research.strategies.meanrev.strategy import MeanReversionStrategy
        from research.strategies.meanrev.config import MeanReversionConfig
        from research.strategies.momentum.strategy import MomentumStrategy
        from research.strategies.momentum.config import MomentumConfig
        from research.strategies.macd.strategy import MACDStrategy
        from research.strategies.macd.config import MACDConfig
        
        # Get symbols to scan (use keys from symbols_bars)
        symbols = list(symbols_bars.keys())
        
        for db_strategy in db_strategies:
            strategy_name = db_strategy.name.lower()
            strategy_id = str(db_strategy.id)
            
            try:
                # Create strategy instance based on name
                config_data = db_strategy.config or {}
                strategy = None
                
                # Get strategy's configured interval (default to 5m)
                strategy_interval = config_data.get("interval", "5m")
                
                # Production strategies (check most specific first)
                if "vwap_meanrev" in strategy_name or "vwap_meanreversion" in strategy_name:
                    # Flatten config_data: merge parameters dict into top level, exclude filters, strategy_id, name, and other non-config fields
                    excluded_top_level = ("filters", "parameters", "strategy_id", "name", "max_risk_pct", "volume_threshold")
                    flat_config = {k: v for k, v in config_data.items() if k not in excluded_top_level}
                    if "parameters" in config_data:
                        flat_config.update({k: v for k, v in config_data["parameters"].items() if k != "strategy_id"})
                    config = VWAPMeanReversionConfig(strategy_id=strategy_id, **flat_config)
                    strategy = VWAPMeanReversionStrategy(config)
                    
                elif "htf_trend" in strategy_name or "htf_trend_pullback" in strategy_name:
                    # Flatten config_data: merge parameters dict into top level, exclude filters, strategy_id, name, and other non-config fields
                    # Valid HTFTrendConfig fields: strategy_id, symbol, interval, htf_interval, notional_risk_pct, and all parameters
                    excluded_top_level = ("filters", "parameters", "strategy_id", "name", "max_risk_pct", "volume_threshold")
                    flat_config = {k: v for k, v in config_data.items() if k not in excluded_top_level}
                    if "parameters" in config_data:
                        flat_config.update({k: v for k, v in config_data["parameters"].items() if k != "strategy_id"})
                    config = HTFTrendConfig(strategy_id=strategy_id, **flat_config)
                    strategy = HTFTrendStrategy(config)
                    
                elif "volatility_breakout" in strategy_name:
                    # Flatten config_data: merge parameters dict into top level, exclude filters, strategy_id, name, and other non-config fields
                    excluded_top_level = ("filters", "parameters", "strategy_id", "name", "max_risk_pct", "volume_threshold")
                    flat_config = {k: v for k, v in config_data.items() if k not in excluded_top_level}
                    if "parameters" in config_data:
                        flat_config.update({k: v for k, v in config_data["parameters"].items() if k != "strategy_id"})
                    config = VolatilityBreakoutConfig(strategy_id=strategy_id, **flat_config)
                    strategy = VolatilityBreakoutStrategy(config)
                
                # Legacy strategies
                elif "meanrev" in strategy_name or "mean-rev" in strategy_name or "mean_rev" in strategy_name or "mean_reversion" in strategy_name:
                    config = MeanReversionConfig(
                        strategy_id=strategy_id,
                        symbol=config_data.get("symbol", "ETH/USD"),
                        lookback_period=config_data.get("lookback_period", 20),
                        rsi_period=config_data.get("rsi_period", 14),
                    )
                    strategy = MeanReversionStrategy(config)
                    
                elif "momentum" in strategy_name or "trend_follow" in strategy_name or "trend-follow" in strategy_name:
                    config = MomentumConfig(
                        strategy_id=strategy_id,
                        symbol=config_data.get("symbol", "BTC/USD"),
                        lookback_period=config_data.get("lookback_period", 14),
                    )
                    strategy = MomentumStrategy(config)
                
                elif "macd" in strategy_name:
                    params = config_data.get("parameters", {})
                    config = MACDConfig(
                        strategy_id=strategy_id,
                        fast_period=params.get("fast_period", 12),
                        slow_period=params.get("slow_period", 26),
                        signal_period=params.get("signal_period", 9),
                    )
                    strategy = MACDStrategy(config)
                
                if strategy is not None:
                    # Wrap the strategy with an evaluate adapter
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
                    
                    # Apply global filters before fetching bars (whitelist → liquidity → spread)
                    filtered_symbols, skip_reasons = await self._apply_global_filters(
                        symbols, strategy_id
                    )
                    
                    # Fetch bars at strategy's configured interval (only for filtered symbols)
                    strategy_symbols_bars = {}
                    for symbol in filtered_symbols:
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
        logger.info(f"[SCAN-LOOP] Starting scan loop (interval={self.scan_interval}s)")
        
        scan_count = 0
        while self._running:
            try:
                scan_count += 1
                logger.info(f"[SCAN-LOOP] Starting scan #{scan_count} at {datetime.now(timezone.utc).isoformat()}")
                await self.run_scan()
                logger.info(f"[SCAN-LOOP] Completed scan #{scan_count}")
            except Exception as e:
                logger.error(f"Scan error: {e}", exc_info=True)
            
            # Wait for next scan interval
            logger.debug(f"[SCAN-LOOP] Sleeping for {self.scan_interval}s until next scan")
            await asyncio.sleep(self.scan_interval)
        
        logger.info("Screener service stopped")
    
    async def start(self) -> None:
        """Start the background scan loop."""
        if self._running:
            logger.warning("Screener service already running")
            return
        
        self._running = True
        logger.info("Screener service starting...")
        logger.info(f"[SCAN-START] Creating scan loop task (interval={self.scan_interval}s)")
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"[SCAN-START] Scan loop task created: {self._task}")
        
        # Log task state after a brief delay to see if it's running
        async def check_task():
            await asyncio.sleep(1)
            if self._task:
                logger.info(f"[SCAN-START] Task state after 1s: done={self._task.done()}, cancelled={self._task.cancelled()}")
        asyncio.create_task(check_task())
    
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
                # Add trading_enabled status
                result["trading_enabled"] = get_trading_enabled()
                return result
        except Exception as e:
            logger.error(f"Failed to get strategy results for {strategy_id}: {e}")
        
        return None
