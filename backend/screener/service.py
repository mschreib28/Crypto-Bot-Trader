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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from backend.config import ACCOUNT_EQUITY, RISK_PCT_PER_TRADE, CONFIDENCE_THRESHOLD_PCT, MIN_EXECUTION_CONFIDENCE
from backend.db import get_session
from backend.db.models import Strategy, get_strategy_display_name
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
    BAR_REFRESH_COOLDOWN_KEY,
    BAR_REFRESH_COOLDOWN_TTL,
    INGESTOR_ACTIVE_SYMBOLS_KEY,
    MARKET_OHLCV_STREAM,
    NO_SHORTING_LOG_KEY,
    NO_SHORTING_LOG_TTL,
    SCREENER_LAST_SCAN_KEY,
    SCREENER_RESULTS_KEY,
    SCREENER_RESULTS_TTL,
    SCREENER_SCAN_STATUS_KEY,
    SCREENER_SCAN_STATUS_TTL,
    SCREENER_SIGNALS_HISTORY_KEY,
    SCREENER_STRATEGY_RESULTS_KEY,
    SHADOW_LIVE_MODE_KEY,
    SIGNAL_COOLDOWN_SECONDS,
    SIGNAL_EXECUTED_KEY,
    STRATEGY_LAST_EVAL_KEY,
    STRATEGY_LAST_EVAL_TTL,
    STRATEGY_SILENCE_COUNT_KEY,
    STRATEGY_SILENCE_COUNT_TTL,
    TRADING_ENABLED_KEY,
)
from backend.api.routes.events import log_activity
from backend.api.routes.trading import get_bot_mode
from backend.risk.evaluator import evaluate_intent, TradeIntent
from backend.execution.executor import execute_trade
from backend.positions.tracker import get_position_tracker
from backend.ingestor.historical import backfill_historical_bars
from backend.screener.aggregator import aggregate_bars, INTERVAL_MINUTES
from backend.screener.engine import ScreenerEngine, scan_with_strategy
from backend.screener.models import ScreenerResult, SignalResult
from backend.screener.data_collector import fetch_market_data
from backend.screener.scoring import calculate_granular_rvol, score_to_grade, grade_to_min_score
from backend.screener.pipeline import (
    apply_float_proxy_soft_grade,
    check_float_proxy,
    check_hard_floor,
    check_stage1_static,
    check_stage2_dynamic,
    compute_pipeline_grade,
    d2_momentum_passes,
    fetch_btc_4h_change,
    float_proxy_turnover,
    grade_to_score,
    strategy_requires_d2_momentum,
)
from backend.ingestor.symbols import fetch_usd_pairs, is_stablecoin_pair
from backend.redis.keys import TOP_10_OBVIOUS_KEY, TOP_10_OBVIOUS_TTL, APLUS_SCORES_KEY, APLUS_SCORES_TTL

logger = logging.getLogger(__name__)

# Maximum number of signals to keep in history
SIGNALS_HISTORY_MAX = 100
# Maximum results to store per strategy
TOP_RESULTS_PER_STRATEGY = 5

# Minimum bars before early return (Volatility needs 100 at 15m)
_MIN_BARS_RETURN = {"5m": 20, "15m": 100, "1h": 210, "4h": 150}


def _update_hybrid_silence_counter(redis_client, strategy_id: str, result_dict: Dict[str, Any]) -> None:
    """Bar-aligned silence count for hybrid exit: reset on actionable rows, incr on pure NONE / placeholders."""
    symbol = result_dict.get("symbol")
    if not symbol:
        return
    st = (result_dict.get("signal_type") or "NONE").upper()
    conf = float(result_dict.get("confidence") or 0.0)
    indicators = result_dict.get("indicators") or {}
    note = indicators.get("note")
    key = STRATEGY_SILENCE_COUNT_KEY.format(strategy_id=strategy_id, symbol=symbol)
    ttl = STRATEGY_SILENCE_COUNT_TTL

    not_silent = st in ("BUY", "SELL") or (st == "NONE" and conf > 1e-6)
    if not_silent:
        redis_client.setex(key, ttl, "0")
        return

    is_placeholder_silent = note in ("waiting_for_data", "insufficient_data")
    is_pure_none = st == "NONE" and abs(conf) <= 1e-6
    if is_placeholder_silent or is_pure_none:
        redis_client.incr(key)
        redis_client.expire(key, ttl)


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


def _get_enabled_strategy_display_names() -> Set[str]:
    """Return display names of enabled (status='active') strategies."""
    strategies = _load_enabled_strategies()
    return {get_strategy_display_name(s) for s in strategies}


class _StrategyEvaluateAdapter:
    """
    Adapter to provide evaluate() interface for strategies.
    
    Wraps strategies that use generate_signals() to provide the
    evaluate(symbol, bars) -> SignalResult interface expected by T62.
    """
    
    def __init__(self, strategy: Any, strategy_id: str, strategy_name: Optional[str] = None):
        """
        Initialize the adapter.
        
        Args:
            strategy: Strategy instance with generate_signals() method
            strategy_id: Strategy identifier
            strategy_name: Human-readable strategy name (for activity log display)
        """
        self._strategy = strategy
        self.strategy_id = strategy_id
        self.strategy_name = strategy_name
    
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
        self._scan_task: Optional[asyncio.Task] = None
        self._scan_counter: int = 0
        
        logger.info(
            f"ScreenerService initialized: interval={scan_interval_seconds}s, "
            f"bars={bars_to_fetch}, timeframe={interval}"
        )
    
    def _get_stream_key(self, symbol: str, interval: str = "1m") -> str:
        """Get Redis stream key for a symbol."""
        return MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval)
    
    async def _maybe_refresh_stale_bars(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
        target_interval: str,
    ) -> List[Dict[str, Any]]:
        """
        If bars are stale (last bar older than 1.5x candle period), refresh from Kraken.
        Uses cooldown to avoid hammering the API (max once per 10 min per symbol/interval).
        """
        if not bars:
            return bars
        last_ts = bars[-1].get("timestamp")
        if not last_ts:
            return bars
        try:
            # Parse ISO timestamp
            if last_ts.endswith("Z"):
                last_ts = last_ts.replace("Z", "+00:00")
            last_dt = datetime.fromisoformat(last_ts)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return bars
        interval_min = INTERVAL_MINUTES.get(target_interval, 15)
        staleness_min = interval_min * 1.5  # e.g. 15m bars: refresh if last bar > 22.5 min old
        age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60.0
        if age_min <= staleness_min:
            return bars
        cooldown_key = BAR_REFRESH_COOLDOWN_KEY.format(symbol=symbol, interval=target_interval)
        client = get_redis_client()
        if client.exists(cooldown_key):
            return bars
        try:
            logger.info(
                f"[REFRESH] {symbol} {target_interval}: Last bar {age_min:.0f}min old, "
                f"refreshing from Kraken"
            )
            stored = await backfill_historical_bars(symbol, target_interval, 100)
            client.setex(cooldown_key, BAR_REFRESH_COOLDOWN_TTL, "1")
            if stored > 0:
                stream_key = self._get_stream_key(symbol, interval=target_interval)
                messages = client.xrevrange(stream_key, count=min(len(bars), 200))
                if messages:
                    bars = []
                    for msg_id, data in reversed(messages):
                        bars.append({
                            "symbol": data.get("symbol", symbol),
                            "interval": target_interval,
                            "open": float(data.get("open", 0)),
                            "high": float(data.get("high", 0)),
                            "low": float(data.get("low", 0)),
                            "close": float(data.get("close", 0)),
                            "volume": float(data.get("volume", 0)),
                            "timestamp": data.get("timestamp", ""),
                        })
                    logger.info(f"[REFRESH] {symbol}: Now have {len(bars)} fresh bars")
        except Exception as e:
            logger.warning(f"[REFRESH] Failed for {symbol}: {e}")
        return bars
    
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
        Apply global filters using the 3-stage pipeline grades stored in Redis.

        Filter order (fail-fast):
          1. Whitelist  — shadow mode only
          2. Hard floor — 24h volume > $100K
          3. Spread     — configurable cap (untradeable pairs)
          4. Pipeline grade — F-grade pairs (failed Stage 1 static) are skipped

        Stage 3 (strategy signal) runs on all pairs that cleared Stage 1 static
        checks; the grade affects position sizing, not whether to scan.

        Returns:
            Tuple of (filtered_symbols, skip_reasons_dict)
        """
        try:
            shadow_mode = get_bot_mode() == "SHADOW"
        except Exception as e:
            logger.debug(f"Failed to check bot mode: {e}, defaulting to shadow/paper")
            shadow_mode = True

        max_spread_bps = get_max_spread_bps()
        enforce_whitelist = get_enforce_whitelist_in_shadow()

        # Read pipeline scores once for all Stage 1 gate checks
        _pipeline_cache: Dict[str, Any] = {}
        try:
            _rc = get_redis_client()
            _raw = _rc.hgetall(APLUS_SCORES_KEY)
            for _k, _v in (_raw or {}).items():
                try:
                    _k = _k.decode() if isinstance(_k, bytes) else _k
                    _pipeline_cache[_k] = json.loads(_v)
                except Exception:
                    pass
        except Exception:
            _pipeline_cache = {}

        filtered = []
        skip_reasons = {}
        skip_counts = {"whitelist": 0, "hard_floor": 0, "spread": 0, "pipeline_f": 0}

        for symbol in symbols:
            skip_reason = None

            # 1. Whitelist (shadow mode only)
            if shadow_mode and enforce_whitelist:
                if not is_in_live_universe(symbol):
                    skip_reason = "not in whitelist"
                    skip_counts["whitelist"] += 1
                    logger.info(f"[FILTER] SKIP: {symbol} [whitelist]")

            # 2. Hard floor: $100K 24h volume
            if not skip_reason:
                volume = get_symbol_volume(symbol)
                if volume is not None and volume < 100_000:
                    skip_reason = f"volume ${volume:,.0f} < $100K hard floor"
                    skip_counts["hard_floor"] += 1
                    logger.info(f"[FILTER] SKIP: {symbol} [hard_floor] vol=${volume:,.0f}")

            # 3. Spread cap (untradeable pairs)
            if not skip_reason:
                spread_bps = get_symbol_spread(symbol)
                if spread_bps is not None and spread_bps > max_spread_bps:
                    skip_reason = f"spread {spread_bps:.1f}bps > {max_spread_bps:.1f}bps"
                    skip_counts["spread"] += 1
                    logger.info(f"[FILTER] SKIP: {symbol} [spread] {spread_bps:.1f}bps")

            # 4. Pipeline grade F/D or failed Stage 1 static → skip strategy eval
            if not skip_reason:
                _scored = _pipeline_cache.get(symbol, {})
                _grade = str(_scored.get("grade") or "").upper()
                _stage1 = _scored.get("stage1_pass")
                if _stage1 is False or _grade in ("F", "D"):
                    skip_reason = f"pipeline_grade_F (stage1_fail={_stage1}, grade={_grade})"
                    skip_counts["pipeline_f"] += 1
                    logger.info(f"[FILTER] SKIP: {symbol} [pipeline_F] grade={_grade} stage1={_stage1}")

            if skip_reason:
                skip_reasons[symbol] = skip_reason
            else:
                filtered.append(symbol)

        total_skipped = len(skip_reasons)
        logger.info(
            f"[FILTER] Strategy {strategy_id}: {len(symbols)} → {len(filtered)} symbols "
            f"(skipped: {total_skipped} — whitelist={skip_counts['whitelist']}, "
            f"hard_floor={skip_counts['hard_floor']}, spread={skip_counts['spread']}, "
            f"pipeline_F={skip_counts['pipeline_f']})"
        )

        return filtered, skip_reasons

    def _meanrev_4h_gate_from_bars(self, bars: List[Dict[str, Any]]) -> bool:
        """Screener gate for meanrev: 4h RSI<40, close at/below lower BB, ADX<30 (replaces D2 intent)."""
        if not bars or len(bars) < 35:
            return False
        try:
            from research.strategies.indicators import (
                calculate_adx,
                calculate_bollinger_bands,
                calculate_rsi,
            )

            closes = [float(b["close"]) for b in bars]
            highs = [float(b["high"]) for b in bars]
            lows = [float(b["low"]) for b in bars]
            rsi = calculate_rsi(closes, period=14)
            bb = calculate_bollinger_bands(closes, 20, 2.0)
            adx = calculate_adx(highs, lows, closes, period=14)
            if rsi is None or bb is None or adx is None:
                return False
            lower = bb["lower"]
            return bool(rsi < 40.0 and closes[-1] <= lower and adx < 30.0)
        except Exception:
            return False
    
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

        min_bars = _MIN_BARS_RETURN.get(target_interval, 20)
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
                
                # If we have enough bars, check freshness and return
                if len(bars) >= min_bars:
                    bars = await self._maybe_refresh_stale_bars(symbol, bars, target_interval)
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
                    
                    # If we have enough bars, check freshness and return
                    if len(bars) >= min_bars:
                        bars = await self._maybe_refresh_stale_bars(symbol, bars, target_interval)
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
                        bars = await self._maybe_refresh_stale_bars(symbol, bars, target_interval)
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
        symbols_in_scan_universe: Optional[Iterable[str]] = None,
    ) -> List[SignalResult]:
        """
        Store strategy-specific results in Redis with TTL.
        
        Results persist for SCREENER_RESULTS_TTL seconds. This ensures results
        survive if a scan temporarily fails, rather than immediately disappearing.
        
        New results are MERGED with existing results - symbols that weren't re-evaluated
        keep their previous results ONLY if still in the scan universe. Symbols that
        dropped out of the universe (e.g. no longer in A+ or ingestor) are NOT preserved,
        preventing stale signals from persisting indefinitely.
        
        Preserved results are re-filtered against current confidence thresholds to ensure
        signals reflect the current configuration (e.g., if threshold changed from 55% to 75%,
        preserved BUY signals with 73% confidence become NONE).
        
        Args:
            strategy_id: Strategy identifier
            results: List of SignalResult objects (already sorted by confidence)
            total_scanned: Total number of symbols scanned
            confidence_buy: Confidence threshold for BUY signals (default: 90.0)
            confidence_sell: Confidence threshold for SELL signals (default: 90.0)
            symbols_in_scan_universe: Symbols in current scan (only preserve existing for these)
            
        Returns:
            List of restored signals (NONE→BUY/SELL) that need execution. Only includes
            signals with current_price in indicators; skips and logs when missing.
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
        restored_signals: List[SignalResult] = []
        
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
                    restored_up_count += 1
                    # Collect for execution only if current_price available
                    current_price = indicators.get("current_price") or indicators.get("price")
                    if current_price is not None:
                        restored_signals.append(SignalResult(
                            symbol=symbol,
                            signal_type="BUY",
                            confidence=confidence,
                            strategy_id=strategy_id,
                            indicators=dict(indicators),
                            timestamp=result.get("timestamp", timestamp),
                        ))
                    else:
                        logger.debug(
                            f"[STORE] Skipping restored BUY {symbol}: no current_price in indicators"
                        )
                elif has_sell_condition and confidence >= confidence_sell:
                    result["signal_type"] = "SELL"
                    indicators["threshold_filtered"] = False
                    restored_up_count += 1
                    current_price = indicators.get("current_price") or indicators.get("price")
                    if current_price is not None:
                        restored_signals.append(SignalResult(
                            symbol=symbol,
                            signal_type="SELL",
                            confidence=confidence,
                            strategy_id=strategy_id,
                            indicators=dict(indicators),
                            timestamp=result.get("timestamp", timestamp),
                        ))
                    else:
                        logger.debug(
                            f"[STORE] Skipping restored SELL {symbol}: no current_price in indicators"
                        )
        
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

        for result_dict in normalized_results:
            _update_hybrid_silence_counter(client, strategy_id, result_dict)
        
        # Merge: new results override existing, keep existing for non-evaluated symbols
        # ONLY preserve symbols still in scan universe - prevents stale signals from persisting
        # when symbols drop out of A+ or ingestor
        new_by_symbol = {r.get("symbol"): r for r in normalized_results}
        merged_results = []
        universe_set = set(symbols_in_scan_universe) if symbols_in_scan_universe is not None else None
        
        # Add all new results
        for symbol, result in new_by_symbol.items():
            merged_results.append(result)
        
        # Add existing results for symbols not in new results, only if still in scan universe
        for symbol, result in existing_by_symbol.items():
            if symbol not in new_by_symbol:
                if universe_set is None or symbol in universe_set:
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
        
        return restored_signals
    
    async def _process_auto_execution(
        self,
        signal: SignalResult,
        trading_enabled: bool,
        confidence_buy: float = CONFIDENCE_THRESHOLD_PCT,
        confidence_sell: float = CONFIDENCE_THRESHOLD_PCT,
        min_allowed_grade: str = "A+",
    ) -> None:
        """
        Process a signal for potential auto-execution.
        
        If trading is enabled and confidence meets threshold, create TradeIntent,
        send to risk evaluator, and execute if approved.
        
        Args:
            signal: SignalResult with confidence
            trading_enabled: Whether trading is currently enabled
            confidence_buy: Min signal strength for BUY (default: 90%)
            confidence_sell: Min signal strength for SELL (default: 90%)
            min_allowed_grade: Minimum A+ grade required (default: "A+")
        """
        confidence = signal.confidence
        # Get signal type (handle both signal and signal_type attributes)
        signal_type = getattr(signal, 'signal_type', None) or getattr(signal, 'signal', 'NONE')
        # Use strategy-specific threshold based on signal direction
        threshold = confidence_buy if signal_type.upper() == "BUY" else confidence_sell
        # Extract direction + signal_data early so all log branches can use it
        _signal_data_early = getattr(signal, 'indicators', None) or getattr(signal, 'metadata', {}) or {}
        _direction = _signal_data_early.get("direction")
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
                # Log to activity feed - CRITICAL: if you see this for BUY, enable Shadow or Live trading
                _reason = "trading_disabled"
                log_activity(
                    activity_type="signal",
                    message=f"[{_reason}] {signal_type} signal for {signal.symbol} [{strategy_name}] - enable Shadow or Live trading",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": _reason,
                    },
                )
            else:
                # NONE signals logged at DEBUG level for debugging purposes
                logger.debug(
                    f"SIGNAL (trading-off): {signal_type} {signal.symbol} "
                    f"confidence={confidence:.1f}% strategy={signal.strategy_id}"
                )
            return
        
        # Check against strategy-specific threshold
        if confidence < threshold:
            # Below threshold - log rejection (INFO level - this is normal behavior)
            logger.info(
                f"Signal rejected: {signal.symbol} confidence {confidence:.1f}% < strategy threshold {threshold}%"
            )
            # Log to activity feed for below-threshold signals (only log non-NONE signals)
            if signal_type.upper() != "NONE":
                _reason = f"below_strategy_threshold ({confidence:.1f}% < {threshold}%)"
                log_activity(
                    activity_type="signal",
                    message=f"[{_reason}] {signal_type} signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": _reason,
                    },
                )
            return
        
        # Execution threshold = Buy/Sell Strength % from Screener Settings (confidence_buy/confidence_sell)
        # No separate global MIN_EXECUTION_CONFIDENCE - strategy filters define the threshold.
        
        # Grade gate (fail closed): only A+/A/B/C may open new positions (BUY).
        # SELL with open position is handled below. Same rules in SHADOW and LIVE.
        if signal_type.upper() == "BUY":
            aplus_data = self._get_aplus_score(signal.symbol)
            symbol_grade = (aplus_data.get("grade") if aplus_data else None) or ""
            norm_grade = str(symbol_grade).strip().upper()
            _passing = frozenset({"A+", "A", "B", "C"})
            if not norm_grade or norm_grade in ("D", "F") or norm_grade not in _passing:
                _reason = f"grade_gate_fail_closed (grade={symbol_grade or 'missing'})"
                logger.info(
                    "grade_gate_fail_closed symbol=%s reason=%s strategy=%s",
                    signal.symbol,
                    _reason,
                    strategy_name,
                )
                log_activity(
                    activity_type="signal",
                    message=f"[{_reason}] BUY signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": _reason,
                    },
                )
                return

            min_score = grade_to_min_score(min_allowed_grade or "A+")
            symbol_score = float(aplus_data.get("score", 0.0)) if aplus_data else 0.0
            if symbol_score < min_score:
                _reason = f"below_min_grade ({symbol_grade} < {min_allowed_grade})"
                logger.info(
                    "grade_gate_fail_closed symbol=%s reason=%s score=%.2f min=%.2f",
                    signal.symbol,
                    _reason,
                    symbol_score,
                    min_score,
                )
                log_activity(
                    activity_type="signal",
                    message=f"[{_reason}] {signal_type} signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": _reason,
                    },
                )
                return

            if strategy_requires_d2_momentum(strategy_name) and not d2_momentum_passes(
                aplus_data
            ):
                _reason = "d2_momentum_fail"
                logger.info(
                    "d2_momentum_gate_fail_closed symbol=%s strategy=%s",
                    signal.symbol,
                    strategy_name,
                )
                log_activity(
                    activity_type="signal",
                    message=f"[{_reason}] BUY signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": _reason,
                    },
                )
                return
        
        # No-shorting: Only execute SELL signals if we own the asset
        if signal_type.upper() == "SELL":
            tracker = get_position_tracker()
            
            # Check if we have a position to sell (no shorting)
            if not tracker.has_position(signal.symbol):
                logger.debug(f"SELL signal ignored for {signal.symbol}: no position (no shorting)")
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
                logger.debug(
                    f"BUY signal skipped for {signal.symbol}: position already exists "
                    f"(strategy={signal.strategy_id})"
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
                    message=f"[cooldown_active] BUY signal skipped for {signal.symbol} [{strategy_name}]",
                    details={
                        "reason": "cooldown_active",
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                    },
                )
                return

            # BUG3: Check forced-exit cooldown (set by monitor after ANY forced/stop exit)
            from backend.redis.keys import FORCED_EXIT_COOLDOWN_KEY, FORCED_EXIT_COOLDOWN_TTL
            forced_exit_key = FORCED_EXIT_COOLDOWN_KEY.format(
                symbol=signal.symbol, strategy_id=signal.strategy_id
            )
            if client.exists(forced_exit_key):
                ttl = client.ttl(forced_exit_key)
                logger.info(
                    f"BUY signal skipped for {signal.symbol}: post-exit cooldown active "
                    f"({ttl}s remaining, strategy={signal.strategy_id})"
                )
                log_activity(
                    activity_type="signal",
                    message=f"[post_exit_cooldown] BUY signal skipped for {signal.symbol} [{strategy_name}]",
                    details={
                        "reason": "post_exit_cooldown",
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "cooldown_ttl_remaining": ttl,
                    },
                )
                return

            from backend.supervisor.store import canonical_name as sup_canon, is_drawdown_suspended

            _drawdown_canon = sup_canon(strategy_name)
            if is_drawdown_suspended(_drawdown_canon):
                logger.info(
                    "BUY signal skipped for %s: strategy drawdown suspended (%s)",
                    signal.symbol,
                    _drawdown_canon,
                )
                log_activity(
                    activity_type="signal",
                    message=f"[drawdown_suspended] BUY signal skipped for {signal.symbol} [{strategy_name}]",
                    details={
                        "reason": "drawdown_suspended",
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "canonical": _drawdown_canon,
                    },
                )
                return

            # BUG5: Check per-symbol block (explicit BLOCKED_SYMBOLS env var OR loss circuit breaker)
            from backend.redis.keys import SYMBOL_BLOCKED_KEY
            _blocked_env = os.getenv("BLOCKED_SYMBOLS", "")
            _blocked_list = [s.strip() for s in _blocked_env.split(",") if s.strip()]
            if signal.symbol in _blocked_list:
                logger.info(f"BUY skipped: {signal.symbol} in BLOCKED_SYMBOLS env var")
                return
            _symbol_blocked_key = SYMBOL_BLOCKED_KEY.format(symbol=signal.symbol)
            if client.exists(_symbol_blocked_key):
                _sym_ttl = client.ttl(_symbol_blocked_key)
                logger.info(
                    f"BUY skipped: {signal.symbol} blocked by loss circuit breaker "
                    f"({_sym_ttl}s remaining)"
                )
                return
        
        # Confidence meets threshold and trading is enabled - attempt execution
        logger.info(
            f"Signal approved: {signal.symbol} {signal_type} confidence={confidence:.1f}% "
            f"threshold={threshold:.1f}% strategy={signal.strategy_id}"
        )
        
        # Trace: passed grade check, about to create TradeIntent (throttled)
        if signal_type.upper() == "BUY":
            _trace_key = f"trace:buy:passed:{signal.symbol}"
            if not get_redis_client().exists(_trace_key):
                get_redis_client().setex(_trace_key, 300, "1")
                log_activity(
                    activity_type="system",
                    message=f"BUY trace {signal.symbol}: passed grade, sending to risk evaluator",
                    details={"symbol": signal.symbol, "confidence": confidence},
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
                    message=f"[{decision.rejection_reason}] {side.upper()} signal rejected for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": decision.rejection_reason,
                    },
                )
                return

            from backend.supervisor.store import canonical_name as sup_canon, get_effective_mode

            _canon = sup_canon(strategy_name)
            _eff_mode, _eff_factor = get_effective_mode(_canon)
            trade_intent.metadata = dict(trade_intent.metadata or {})
            if get_bot_mode() == "LIVE" and _eff_mode == "LIVE":
                trade_intent.metadata["supervisor_size_factor"] = float(_eff_factor)
            else:
                trade_intent.metadata["supervisor_size_factor"] = 1.0
            if get_bot_mode() == "LIVE" and _eff_mode == "SIM":
                trade_intent.metadata["strategy_canonical"] = _canon
            _live_exec = get_bot_mode() == "LIVE" and _eff_mode == "LIVE"
            
            # TICKET-705: Log EXECUTION_ALLOWED before calling execute_trade()
            bar_timestamp = signal_data.get("bar_timestamp") or signal_data.get("timestamp")
            strategy_interval = signal_data.get("timeframe") or signal_data.get("interval") or "15m"
            candle_tag = f"candle={bar_timestamp} tf={strategy_interval}" if bar_timestamp else ""
            mode_tag = "shadow" if get_bot_mode() == "SHADOW" else "live"
            
            _direction = signal_data.get("direction")
            try:
                _shadow_mode = get_bot_mode() == "SHADOW"
                if _shadow_mode:
                    import json as _json
                    from backend.redis.keys import SHADOW_BALANCE_KEY
                    _sb_raw = get_redis_client().get(SHADOW_BALANCE_KEY)
                    if _sb_raw:
                        _equity_val = float(_json.loads(_sb_raw).get("total_usd", 0.0))
                    else:
                        _equity_val = 0.0
                else:
                    from backend.risk.portfolio import get_current_equity
                    from backend.db import get_session as _get_session
                    _sess = _get_session()
                    try:
                        _equity_val = float(get_current_equity(_sess))
                    finally:
                        _sess.close()
            except Exception:
                _equity_val = 0.0

            log_activity(
                activity_type="EXECUTION_ALLOWED",
                message=f"Execution allowed: {side.upper()} {signal.symbol} [{strategy_name}] ({mode_tag}) - passed all gates {candle_tag}".strip(),
                details={
                    "symbol": signal.symbol,
                    "side": side,
                    "direction": _direction,
                    "strategy": signal.strategy_id,
                    "strategy_id": signal.strategy_id,
                    "confidence": confidence,
                    "bar_timestamp": bar_timestamp,
                    "timeframe": strategy_interval,
                    "intent_id": decision.intent_id,
                    "equity": _equity_val,
                },
            )
            
            # Get current price from signal data
            current_price = signal_data.get("current_price") or signal_data.get("price")
            
            if current_price is None:
                logger.error(
                    f"AUTO-EXECUTE FAILED: No current_price in signal data "
                    f"for {signal.symbol}"
                )
                log_activity(
                    activity_type="signal",
                    message=f"[no_current_price] {side.upper()} signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": "no_current_price",
                    },
                )
                return
            
            # Execute the trade
            fill = await execute_trade(
                trade_intent,
                float(current_price),
                live=_live_exec,
            )
            
            if fill is not None:
                logger.info(
                    f"AUTO-EXECUTE SUCCESS: {side} {signal.symbol} "
                    f"qty={fill.quantity} price=${fill.executed_price:.2f} "
                    f"confidence={confidence:.1f}% strategy={signal.strategy_id}"
                )
                
                # Note: Cooldown is now only set after losses (Ross Cameron spec)
                # Removed automatic cooldown after BUY - cooldown only applies after position closes at a loss
            else:
                logger.warning(
                    f"AUTO-EXECUTE FAILED: {side} {signal.symbol} "
                    f"(execution returned None) strategy={signal.strategy_id}"
                )
                log_activity(
                    activity_type="signal",
                    message=f"[execution_returned_none] {side.upper()} signal for {signal.symbol} [{strategy_name}]",
                    details={
                        "symbol": signal.symbol,
                        "signal_type": signal_type,
                        "direction": _direction,
                        "confidence": confidence,
                        "strategy": strategy_name,
                        "auto_execute": False,
                        "reason": "execution_returned_none",
                    },
                )
                
        except Exception as e:
            logger.error(
                f"AUTO-EXECUTE ERROR: {signal_type} {signal.symbol} "
                f"strategy={signal.strategy_id}: {e}",
                exc_info=True,
            )
            log_activity(
                activity_type="signal",
                message=f"[auto_execute_error] {signal_type} {signal.symbol}: {type(e).__name__} - {str(e)[:80]}",
                details={
                    "symbol": signal.symbol,
                    "signal_type": signal_type,
                    "strategy": strategy_name,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
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
        
        Returns True when bars exist. Evaluation runs every scan; duplicate
        execution is prevented by position check (no duplicate BUY) and
        per-candle cooldown.
        
        Args:
            strategy_id: Strategy identifier
            symbol: Trading pair symbol
            bars: List of OHLCV bar dictionaries
            interval: Strategy interval (e.g., '5m', '1h', '4h')
            
        Returns:
            True if evaluation should proceed
        """
        if not bars:
            return False
        return True
    
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
        min_allowed_grade: str = "A+",
    ) -> List[SignalResult]:
        """
        Run a single strategy scan and handle auto-execution.
        
        Only evaluates symbols that have new bar data since last evaluation.
        
        Args:
            strategy: Strategy object with evaluate() method
            symbols_bars: Dictionary of symbol -> bars
            interval: Strategy interval for bar-based evaluation check
            confidence_buy: Min signal strength for BUY (default: 90.0)
            confidence_sell: Min signal strength for SELL (default: 90.0)
            min_allowed_grade: Minimum A+ grade required (default: "A+")
            
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
        # Pass symbols_bars keys so we only preserve results for symbols still in scan universe
        restored_signals = self._store_strategy_results(
            strategy_id,
            results or [],
            total_scanned=len(symbols_bars),
            confidence_buy=confidence_buy,
            confidence_sell=confidence_sell,
            symbols_in_scan_universe=symbols_bars.keys(),
        )
        
        # Filter out placeholders before processing for execution
        actionable_results = [r for r in results if r.indicators.get("note") != "waiting_for_data"]
        
        # Add restored signals (preserved from Redis, not in fresh evaluation)
        # Dedupe: prefer fresh over restored for same symbol
        fresh_symbols = {r.symbol for r in actionable_results}
        restored_to_add = [r for r in restored_signals if r.symbol not in fresh_symbols]
        all_actionable = actionable_results + restored_to_add
        
        if restored_to_add:
            logger.info(
                f"[EVAL] Strategy {strategy_id}: {len(restored_to_add)} restored signals for execution "
                f"(symbols: {[r.symbol for r in restored_to_add]})"
            )
        
        # Log actionable signals for debugging
        buy_signals = [r for r in all_actionable if getattr(r, 'signal_type', None) == 'BUY' or getattr(r, 'signal', None) == 'BUY']
        sell_signals = [r for r in all_actionable if getattr(r, 'signal_type', None) == 'SELL' or getattr(r, 'signal', None) == 'SELL']
        none_signals = [r for r in all_actionable if getattr(r, 'signal_type', None) == 'NONE' or (getattr(r, 'signal_type', None) is None and getattr(r, 'signal', None) == 'NONE')]
        
        logger.info(
            f"[EVAL] Strategy {strategy_id} ({interval}): "
            f"Results breakdown: {len(buy_signals)} BUY, {len(sell_signals)} SELL, {len(none_signals)} NONE "
            f"(total actionable: {len(all_actionable)}, fresh={len(actionable_results)}, restored={len(restored_to_add)}, total results: {len(results)})"
        )
        
        if buy_signals or sell_signals:
            for sig in buy_signals + sell_signals:
                sig_type = getattr(sig, 'signal_type', None) or getattr(sig, 'signal', 'UNKNOWN')
                conf = getattr(sig, 'confidence', 0.0)
                logger.info(
                    f"[EVAL] Actionable signal: {sig_type} {sig.symbol} "
                    f"confidence={conf:.1f}% (threshold: {confidence_buy if sig_type == 'BUY' else confidence_sell}%)"
                )
        if not all_actionable:
            logger.debug(
                f"[EVAL] Strategy {strategy_id}: No actionable results "
                f"(total results: {len(results)}, filtered: {len([r for r in results if r.indicators.get('note') == 'waiting_for_data'])})"
            )
            return results  # Return all results (including placeholders) for frontend
        
        # SHADOW and LIVE both allow auto-execution (paper vs real is decided in execute_trade).
        trading_enabled = True
        _bm = get_bot_mode()
        logger.info(
            f"[EVAL] Strategy {strategy_id}: bot_mode={_bm}, processing "
            f"{len(buy_signals)} BUY and {len(sell_signals)} SELL signals for auto-execution"
        )
        
        # Process actionable signals (fresh + restored) for potential auto-execution
        for signal in all_actionable:
            await self._process_auto_execution(
                signal, trading_enabled, confidence_buy, confidence_sell, min_allowed_grade
            )
        
        return results
    
    def _set_scan_status(self, **fields: Any) -> None:
        """Merge-update screener scan status in Redis."""
        try:
            client = get_redis_client()
            existing: Dict[str, Any] = {}
            raw = client.get(SCREENER_SCAN_STATUS_KEY)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                existing = json.loads(raw)
            existing.update(fields)
            client.setex(
                SCREENER_SCAN_STATUS_KEY,
                SCREENER_SCAN_STATUS_TTL,
                json.dumps(existing),
            )
        except Exception as e:
            logger.debug(f"Failed to update scan status: {e}")

    async def _execute_scan(self, scan_number: int) -> None:
        """Run a single scan cycle with Redis status tracking."""
        started_at = datetime.now(timezone.utc).isoformat()
        start_time = time.monotonic()
        self._set_scan_status(
            in_progress=True,
            scan_number=scan_number,
            started_at=started_at,
            stage="starting",
            last_error=None,
            progress=None,
        )
        try:
            await self.run_scan()
            elapsed = time.monotonic() - start_time
            self._set_scan_status(
                in_progress=False,
                stage="done",
                completed_at=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=round(elapsed, 2),
                last_error=None,
            )
            logger.info(f"[SCAN-LOOP] Completed scan #{scan_number} in {elapsed:.2f}s")
        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.error(f"[SCAN-LOOP] Scan #{scan_number} failed: {e}", exc_info=True)
            self._set_scan_status(
                in_progress=False,
                stage="error",
                completed_at=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=round(elapsed, 2),
                last_error=str(e),
            )

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
        
        # Calculate A+ scores and update top 10 obvious cache
        self._set_scan_status(stage="aplus_scoring")
        try:
            await self._calculate_aplus_scores()
        except Exception as e:
            logger.error(f"[SCAN] Error in A+ scoring: {e}", exc_info=True)
        
        # Fetch bars for all symbols
        self._set_scan_status(stage="bars")
        symbols_bars = await self._get_all_symbols_bars()
        logger.info(f"[SCAN] Fetched bars for {len(symbols_bars)} symbols")
        
        # Run the default indicator-based scan
        self._set_scan_status(stage="engine", progress={"current": 0, "total": len(symbols_bars)})
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
        self._set_scan_status(stage="strategies")
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
    
    async def _calculate_aplus_scores(self) -> None:
        """
        Calculate A+ scores for all Kraken pairs and store top 10 in Redis.
        
        This method:
        1. Fetches all USD pairs from Kraken
        2. Filters out blacklisted pairs (stablecoins)
        3. Collects market data for each pair
        4. Calculates A+ scores
        5. Ranks pairs by score
        6. Stores top 10 pairs with score > 0.85 in Redis
        """
        # Prevent concurrent execution using a lock
        if not hasattr(self, '_aplus_scoring_lock'):
            self._aplus_scoring_lock = asyncio.Lock()
        
        # Try to acquire lock, but don't wait - skip if already running
        lock_acquired = False
        try:
            lock_acquired = await asyncio.wait_for(self._aplus_scoring_lock.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
            logger.warning("[A+] A+ scoring already in progress, skipping this cycle")
            return
        
        try:
            logger.info("[A+] Starting A+ scoring calculation...")
            
            # Get all Kraken USD pairs
            try:
                all_pairs = await fetch_usd_pairs()
            except Exception as e:
                logger.error(f"[A+] Failed to fetch USD pairs: {e}")
                return
            
            # Also get symbols from screener to ensure coverage
            screener_symbols = self._get_scan_symbols()
            
            # Merge and deduplicate
            all_symbols = list(set(all_pairs + screener_symbols))
            
            logger.info(f"[A+] Evaluating {len(all_symbols)} pairs for A+ scoring (Kraken: {len(all_pairs)}, Screener: {len(screener_symbols)})")
            
            # ── STAGE 1: Static gate ──────────────────────────────────────────────
            # Collect active positions — these always bypass the static gate.
            _active_position_symbols: set[str] = set()
            try:
                _tracker = get_position_tracker()
                for _pos in _tracker.get_all_positions():
                    if getattr(_pos, "quantity", 0) and _pos.quantity > 0:
                        _active_position_symbols.add(_pos.symbol)
            except Exception as _e:
                logger.debug(f"[A+] Could not fetch active positions: {_e}")

            # Batch-fetch static market data (market_cap, supply_ratio, change_24h_pct) for ALL symbols.
            # /coins/markets is cached 24h in Redis — one batch call covers up to 50 IDs per HTTP request.
            from backend.screener.coingecko import build_symbol_to_coin_id, batch_get_market_data
            from backend.screener.filters import static_filter_symbols

            logger.info(f"[A+] Stage 1: resolving CoinGecko IDs for {len(all_symbols)} symbols")
            symbol_to_coin_id = await asyncio.to_thread(build_symbol_to_coin_id, all_symbols)

            logger.info(f"[A+] Stage 1: batch-fetching static data for {len(symbol_to_coin_id)} symbols")
            batch_static: Dict[str, Any] = {}
            try:
                batch_static = await asyncio.to_thread(batch_get_market_data, symbol_to_coin_id)
                got_mcap = sum(1 for d in batch_static.values() if d.get("market_cap") is not None)
                got_supply = sum(1 for d in batch_static.values() if d.get("supply_ratio") is not None)
                logger.info(f"[A+] Stage 1 batch complete: {got_mcap} market_caps, {got_supply} supply_ratios")
            except Exception as e:
                logger.error(f"[A+] Stage 1 batch fetch failed: {e}", exc_info=True)

            # Batch-fetch spread data from Redis (single hgetall, no per-symbol calls).
            # Merge into batch_static so static_filter_symbols can gate on spread.
            try:
                _vol_client = get_redis_client()
                from backend.redis.keys import SYMBOL_VOLUME_KEY
                _vol_raw = _vol_client.hgetall(SYMBOL_VOLUME_KEY)
                for _sym, _raw in (_vol_raw or {}).items():
                    _sym = _sym.decode() if isinstance(_sym, bytes) else _sym
                    try:
                        _vol_data = json.loads(_raw.decode() if isinstance(_raw, bytes) else _raw)
                        _spread = _vol_data.get("spread_bps")
                        if _spread is not None:
                            batch_static.setdefault(_sym, {})["spread_bps"] = _spread
                    except Exception:
                        pass
            except Exception as _e:
                logger.debug(f"[A+] Could not batch-fetch spread data: {_e}")

            # Apply static gate — eliminates stablecoins, memes, low-mcap, low-supply, wide-spread pairs.
            stage1_survivors, stage1_skipped = static_filter_symbols(
                all_symbols, batch_static, _active_position_symbols
            )
            reason_counts: Dict[str, int] = {}
            for r in stage1_skipped.values():
                reason_counts[r] = reason_counts.get(r, 0) + 1
            logger.info(
                f"[A+] Stage 1: {len(stage1_survivors)}/{len(all_symbols)} survivors "
                f"({len(stage1_skipped)} filtered: {reason_counts})"
            )

            # ── STAGE 2: Dynamic scoring (survivors only) ─────────────────────────
            # Read previous RVOL values BEFORE touching the hash (for flickering prevention).
            _prev_rvol: Dict[str, Optional[float]] = {}
            try:
                _rc = get_redis_client()
                _raw = _rc.hgetall(APLUS_SCORES_KEY)
                for _k, _v in (_raw or {}).items():
                    try:
                        _prev_rvol[_k] = json.loads(_v).get("rvol")
                    except Exception:
                        pass
            except Exception:
                pass

            from backend.screener.data_collector import fetch_daily_sma_50d
            from backend.ingestor.symbols import get_symbol_volume, get_symbol_spread, get_symbol_price, get_symbol_change_24h_pct

            # Fetch BTC 4h change once — shared across all D4 checks (cached 5 min)
            btc_4h_change = await asyncio.to_thread(fetch_btc_4h_change)
            logger.info(f"[PIPELINE] BTC 4h change: {btc_4h_change:.2f}%" if btc_4h_change is not None else "[PIPELINE] BTC 4h change: N/A")

            scored_pairs = []
            BATCH_SIZE = 50
            for i in range(0, len(stage1_survivors), BATCH_SIZE):
                batch = stage1_survivors[i:i + BATCH_SIZE]
                for symbol in batch:
                    try:
                        static = batch_static.get(symbol, {})
                        market_cap = static.get("market_cap")
                        supply_ratio = static.get("supply_ratio")
                        circulating_supply = static.get("circulating_supply")
                        change_24h_pct = static.get("change_24h_pct")
                        # Fallback: use Kraken ticker data (Redis) when CoinGecko has no mapping
                        if change_24h_pct is None:
                            change_24h_pct = get_symbol_change_24h_pct(symbol)

                        # Volume + RVOL — Kraken volume (Redis) / 50d SMA (Kraken OHLC, 24h cached)
                        volume_24h = get_symbol_volume(symbol)
                        daily_sma_50d = await asyncio.to_thread(fetch_daily_sma_50d, symbol)
                        rvol: Optional[float] = None
                        if volume_24h and volume_24h > 0 and daily_sma_50d and daily_sma_50d > 0:
                            rvol = volume_24h / daily_sma_50d
                        else:
                            rvol = _prev_rvol.get(symbol)  # preserve previous value

                        # Spread + price — Redis reads, free
                        spread_bps = get_symbol_spread(symbol)
                        last_price = get_symbol_price(symbol)

                        # Hard floor: $100K 24h volume
                        if not check_hard_floor(volume_24h):
                            logger.debug(f"[PIPELINE] {symbol}: below hard floor vol=${volume_24h}")
                            continue

                        # ── Stage 1 static pipeline check (cached 20h) ──────────────
                        stage1 = check_stage1_static(
                            symbol,
                            current_price=last_price,
                            circulating_supply=circulating_supply,
                        )

                        # ── Stage 2 dynamic pipeline check ───────────────────────────
                        stage2 = check_stage2_dynamic(
                            symbol,
                            rvol_ratio=rvol,
                            change_24h_pct=change_24h_pct,
                            volume_24h=volume_24h,
                            bars_1h=None,  # bars not available at this stage; 4h uses cached BTC data
                            btc_4h_change=btc_4h_change,
                        )

                        # Merge S1 + S2 pillars into a single dict for storage
                        all_pillars = {
                            **stage1["pillars"],
                            **stage2["pillars"],
                        }

                        base_grade = compute_pipeline_grade(
                            stage1["all_pass"], stage2["dynamic_passes"]
                        )
                        float_ok = check_float_proxy(volume_24h, market_cap)
                        grade = apply_float_proxy_soft_grade(base_grade, float_ok)
                        turnover = float_proxy_turnover(volume_24h, market_cap)
                        score = grade_to_score(grade)

                        logger.debug(
                            "[PIPELINE] %s float_proxy=%s turnover=%s base_grade=%s grade=%s",
                            symbol,
                            float_ok,
                            f"{turnover:.4f}" if turnover is not None else "N/A",
                            base_grade,
                            grade,
                        )

                        scored_pairs.append({
                            "symbol": symbol,
                            "score": score,
                            "grade": grade,
                            "rvol": rvol,
                            "market_cap": market_cap,
                            "supply_ratio": supply_ratio,
                            "circulating_supply": circulating_supply,
                            "spread_bps": spread_bps,
                            "change_24h_pct": change_24h_pct,
                            "pillars": all_pillars,
                            "price": last_price,
                            "stage1_pass": stage1["all_pass"],
                            "dynamic_passes": stage2["dynamic_passes"],
                            "float_proxy_pass": float_ok,
                            "float_turnover": turnover,
                            "pipeline_base_grade": base_grade,
                        })
                    except Exception as e:
                        logger.debug(f"[PIPELINE] Error scoring {symbol}: {e}")

                await asyncio.sleep(0)
                if (i // BATCH_SIZE + 1) % 4 == 0:
                    logger.info(f"[PIPELINE] Stage 2: scored {min(i + BATCH_SIZE, len(stage1_survivors))}/{len(stage1_survivors)}")

            logger.info(f"[PIPELINE] Stage 2 complete: {len(scored_pairs)} symbols scored")

            # Rank by score (descending)
            scored_pairs.sort(key=lambda x: x["score"], reverse=True)

            # Store pipeline results in Redis hash (delete + rewrite to remove stale entries).
            # Use a pipeline with RENAME-swap to avoid a brief empty-hash window.
            try:
                client = get_redis_client()
                pipe = client.pipeline()
                _tmp_key = APLUS_SCORES_KEY + ":tmp"
                pipe.delete(_tmp_key)
                for pair in scored_pairs:
                    score_data = {
                        "score": pair["score"],
                        "grade": pair["grade"],
                        "rvol": pair["rvol"],
                        "market_cap": pair["market_cap"],
                        "supply_ratio": pair["supply_ratio"],
                        "circulating_supply": pair.get("circulating_supply"),
                        "spread_bps": pair["spread_bps"],
                        "change_24h_pct": pair["change_24h_pct"],
                        "pillars": pair.get("pillars"),
                        "price": pair.get("price"),
                        "stage1_pass": pair.get("stage1_pass"),
                        "dynamic_passes": pair.get("dynamic_passes"),
                        "float_proxy_pass": pair.get("float_proxy_pass"),
                        "float_turnover": pair.get("float_turnover"),
                        "pipeline_base_grade": pair.get("pipeline_base_grade"),
                    }
                    pipe.hset(_tmp_key, pair["symbol"], json.dumps(score_data))
                pipe.expire(_tmp_key, APLUS_SCORES_TTL)
                pipe.rename(_tmp_key, APLUS_SCORES_KEY)
                pipe.execute()
                logger.info(f"[PIPELINE] Stored {len(scored_pairs)} pipeline-graded pairs in Redis hash")
            except Exception as e:
                logger.error(f"[PIPELINE] Failed to store scores: {e}", exc_info=True)

            # Top-10: pairs with A+ or A grade
            top_pairs = [pair for pair in scored_pairs if pair["grade"] in ("A+", "A")][:10]
            
            aplus_count = sum(1 for p in scored_pairs if p["grade"] == "A+")
            a_count = sum(1 for p in scored_pairs if p["grade"] == "A")
            logger.info(
                f"[PIPELINE] Scored {len(scored_pairs)} pairs — "
                f"A+: {aplus_count}, A: {a_count}, top10: {len(top_pairs)}"
            )

            if top_pairs:
                top_symbols = [p["symbol"] for p in top_pairs]
                logger.info(f"[PIPELINE] Top pairs: {top_symbols}")

                try:
                    client = get_redis_client()
                    client.setex(
                        TOP_10_OBVIOUS_KEY,
                        TOP_10_OBVIOUS_TTL,
                        json.dumps(top_pairs),
                    )
                    logger.info(f"[PIPELINE] Stored top {len(top_pairs)} pairs in Redis cache")
                except Exception as e:
                    logger.error(f"[PIPELINE] Failed to store top pairs in Redis: {e}")
            else:
                logger.warning("[PIPELINE] No pairs with grade A+ or A")
                # Store empty list to clear cache
                try:
                    client = get_redis_client()
                    client.setex(
                        TOP_10_OBVIOUS_KEY,
                        TOP_10_OBVIOUS_TTL,
                        json.dumps([]),
                    )
                except Exception as e:
                    logger.debug(f"[A+] Failed to clear cache: {e}")
                    
        except Exception as e:
            logger.error(f"[A+] Error in A+ scoring calculation: {e}", exc_info=True)
        finally:
            if lock_acquired:
                self._aplus_scoring_lock.release()
    
    def _get_aplus_score(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get A+ score data for a specific symbol from Redis.
        
        Args:
            symbol: Trading pair symbol (e.g., "BTC/USD")
            
        Returns:
            Dictionary with score, grade, rvol, market_cap, supply_ratio, spread_bps, change_24h_pct,
            or None if score not available
        """
        try:
            client = get_redis_client()
            score_data_json = client.hget(APLUS_SCORES_KEY, symbol)
            
            if not score_data_json:
                return None
            
            if isinstance(score_data_json, bytes):
                score_data_json = score_data_json.decode()
            
            score_data = json.loads(score_data_json)
            return score_data
        except Exception as e:
            logger.debug(f"[A+] Error fetching A+ score for {symbol}: {e}")
            return None
    
    def _get_signal_lead(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get the highest confidence strategy signal for a symbol (Signal Lead).
        
        Queries all strategy results from Redis and finds the highest confidence signal.
        Only considers BUY and SELL signals (excludes NONE).
        
        Args:
            symbol: Trading pair symbol (e.g., "BTC/USD")
            
        Returns:
            Dict with format {"confidence": float, "signal_type": str} (e.g., {"confidence": 92.0, "signal_type": "BUY"}) or None
        """
        try:
            from backend.db import get_session
            from backend.db.models import Strategy, get_strategy_display_name
            
            # Get all enabled strategies (use display name for UI consistency)
            session = get_session()
            try:
                strategies = session.query(Strategy).filter(Strategy.status == 'active').all()
                strategy_map = {str(s.id): get_strategy_display_name(s) for s in strategies}
            finally:
                session.close()
            
            client = get_redis_client()
            best_confidence = 0.0
            best_signal_type = None
            best_strategy_name = None
            best_strategy_id: Optional[str] = None
            # Fallback: when no BUY/SELL, use highest-confidence NONE so UI shows strongest strategy
            best_none_confidence = 0.0
            best_none_strategy_name = None
            best_none_strategy_id: Optional[str] = None
            all_signals_found = []  # Debug: track all signals found
            
            # Check each strategy's results
            for strategy_id, strategy_name in strategy_map.items():
                try:
                    key = SCREENER_STRATEGY_RESULTS_KEY.format(strategy_id=strategy_id)
                    data = client.get(key)
                    if not data:
                        continue
                    
                    result = json.loads(data)
                    results = result.get("results", [])
                    
                    # Find this symbol in the results
                    symbol_found = False
                    for r in results:
                        if r.get("symbol") == symbol:
                            symbol_found = True
                            confidence = r.get("confidence", 0.0)
                            signal_type = r.get("signal_type", "NONE")
                            
                            # Debug: track all signals
                            all_signals_found.append({
                                "strategy": strategy_name,
                                "signal_type": signal_type,
                                "confidence": confidence
                            })
                            
                            # Prefer BUY and SELL; fallback to highest-confidence NONE for UI display
                            if signal_type in ("BUY", "SELL") and confidence > best_confidence:
                                best_confidence = confidence
                                best_signal_type = signal_type
                                best_strategy_name = strategy_name
                                best_strategy_id = strategy_id
                            elif signal_type == "NONE" and confidence >= best_none_confidence:
                                best_none_confidence = confidence
                                best_none_strategy_name = strategy_name
                                best_none_strategy_id = strategy_id
                            break
                    
                except Exception as e:
                    logger.debug(f"Error checking strategy {strategy_id} for signal lead: {e}")
                    continue
            
            # Debug logging for A+ pairs
            if symbol in ["OP/USD", "ASTER/USD", "SCRT/USD", "AZTEC/USD", "SENT/USD", "XPL/USD"]:
                best_signal_str = f"{best_signal_type} {best_confidence}%" if best_signal_type else "None"
                logger.info(f"[SIGNAL_LEAD] {symbol}: Found {len(all_signals_found)} strategy results, best: {best_signal_str}")
                if len(all_signals_found) == 0:
                    logger.warning(f"[SIGNAL_LEAD] {symbol}: NO strategy results found! Checked {len(strategy_map)} strategies")
                else:
                    for sig in all_signals_found:
                        logger.info(f"[SIGNAL_LEAD]   {sig['strategy']}: {sig['signal_type']} {sig['confidence']}%")
            
            # Return dict with confidence, signal_type, and strategy_name
            # Prefer BUY/SELL; if none, fall back to highest-confidence NONE so UI always shows strongest strategy
            if best_signal_type is not None:
                conf, sig_type, strat = best_confidence, best_signal_type, best_strategy_name
                winning_strategy_id = best_strategy_id
            elif best_none_strategy_name is not None:
                conf, sig_type, strat = best_none_confidence, "NONE", best_none_strategy_name
                winning_strategy_id = best_none_strategy_id
            else:
                # No strategy results for this symbol at all
                return None
            
            # Build all_signals for UI transparency (show all strategies, not just winner)
            all_signals = [
                {"strategy_name": s["strategy"], "signal_type": s["signal_type"], "confidence": s["confidence"]}
                for s in sorted(all_signals_found, key=lambda x: -x["confidence"])
            ]
            
            # Use winning strategy's confidence thresholds for meets_execution_threshold
            exec_threshold = MIN_EXECUTION_CONFIDENCE
            if winning_strategy_id is not None and sig_type in ("BUY", "SELL"):
                try:
                    strat_session = get_session()
                    strat_obj = strat_session.query(Strategy).filter(Strategy.id == winning_strategy_id).first()
                    if strat_obj and strat_obj.config:
                        filters = strat_obj.config.get("filters", {})
                        exec_threshold = float(
                            filters.get("confidence_buy", 90) if sig_type == "BUY"
                            else filters.get("confidence_sell", 90)
                        )
                    strat_session.close()
                except Exception as e:
                    logger.debug(f"Could not load strategy config for signal_lead threshold: {e}")
            
            # Build result - always use actual strategy name (never "Low Conviction" in strategy_name)
            is_low_conviction = conf < 50.0
            base_result = {
                "confidence": conf,
                "signal_type": sig_type,
                "strategy_name": strat,
                "all_signals": all_signals,  # All strategies for UI transparency
            }
            if is_low_conviction:
                result = {
                    **base_result,
                    "signal_type": "NONE",
                    "is_low_conviction": True,
                    "meets_execution_threshold": False,
                    "original_signal_type": sig_type,
                    "original_strategy_name": strat
                }
            elif sig_type == "NONE" or conf < exec_threshold:
                result = {**base_result, "meets_execution_threshold": False}
            else:
                result = {**base_result, "meets_execution_threshold": True}
            return result
        except Exception as e:
            logger.debug(f"Error getting signal lead for {symbol}: {e}")
            return None
    
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
        from research.strategies.bull_flag.strategy import BullFlagStrategy
        from research.strategies.bull_flag.config import BullFlagConfig
        logger.info("[STRATEGY_SCANS] Strategy classes imported successfully")
        # Legacy strategies
        from research.strategies.meanrev.strategy import MeanReversionStrategy
        from research.strategies.meanrev.config import MeanReversionConfig
        from research.strategies.momentum.strategy import MomentumStrategy
        from research.strategies.momentum.config import MomentumConfig
        
        # Get symbols to scan: prioritize Top 10 Obvious pairs (optimization - reduces CPU usage)
        # Fallback to A+ and A pairs if Top 10 Obvious unavailable
        client = get_redis_client()
        top_10_symbols = []
        aplus_symbols = []  # Fallback list
        
        # Try to get Top 10 Obvious pairs first
        try:
            top_10_data = client.get(TOP_10_OBVIOUS_KEY)
            if top_10_data:
                if isinstance(top_10_data, bytes):
                    top_10_data = top_10_data.decode()
                top_10_list = json.loads(top_10_data)
                if isinstance(top_10_list, list):
                    top_10_symbols = [item.get('symbol') for item in top_10_list if item.get('symbol')]
                    logger.info(f"[STRATEGY_SCANS] Found {len(top_10_symbols)} Top 10 Obvious pairs")
                    if len(top_10_symbols) > 0:
                        logger.info(f"[STRATEGY_SCANS] Top 10 Obvious pairs: {', '.join(sorted(top_10_symbols))}")
                else:
                    logger.warning(f"[STRATEGY_SCANS] Top 10 Obvious data is not a list: {type(top_10_list)}")
        except Exception as e:
            logger.warning(f"[STRATEGY_SCANS] Failed to get Top 10 Obvious from Redis: {e}, falling back to A+ pairs", exc_info=True)
        
        # Always fetch A+ and A pairs (score >= 0.70) for strategy evaluation
        try:
            aplus_scores = client.hgetall(APLUS_SCORES_KEY)
            for symbol_bytes, score_data_json in aplus_scores.items():
                symbol = symbol_bytes.decode() if isinstance(symbol_bytes, bytes) else str(symbol_bytes)
                try:
                    if isinstance(score_data_json, bytes):
                        score_data_json = score_data_json.decode()
                    score_data = json.loads(score_data_json)
                    score = score_data.get("score")
                    if score is not None and float(score) >= 0.70:  # A+ and A grades
                        aplus_symbols.append(symbol)
                except Exception as e:
                    logger.debug(f"Error parsing A+ score for {symbol}: {e}")
                    continue
            logger.info(f"[STRATEGY_SCANS] Found {len(aplus_symbols)} A+ and A pairs (score >= 0.70)")
            if len(aplus_symbols) > 0:
                logger.info(f"[STRATEGY_SCANS] A+ and A pairs: {', '.join(sorted(aplus_symbols)[:20])}{'...' if len(aplus_symbols) > 20 else ''}")
        except Exception as e:
            logger.warning(f"[STRATEGY_SCANS] Failed to get A+ pairs from Redis: {e}, using Top 10 only", exc_info=True)
            aplus_symbols = []
        
        # Evaluate both Top 10 Obvious and all A+ and A symbols
        symbols_to_evaluate = list(set(top_10_symbols + aplus_symbols))
        
        # Combine with ingestor symbols and deduplicate
        ingestor_symbols = list(symbols_bars.keys())
        all_symbols = list(set(ingestor_symbols + symbols_to_evaluate))
        
        source_name = "Top 10 + A+/A" if (top_10_symbols and aplus_symbols) else ("Top 10 Obvious" if top_10_symbols else ("A+ and A" if aplus_symbols else "ingestor only"))
        logger.info(f"[STRATEGY_SCANS] Evaluating strategies for {len(all_symbols)} total symbols ({len(ingestor_symbols)} from ingestor + {len(symbols_to_evaluate)} from {source_name})")
        logger.info(f"[STRATEGY_SCANS] All symbols to evaluate: {', '.join(sorted(all_symbols))}")
        
        for db_strategy in db_strategies:
            strategy_name = db_strategy.name.lower()
            strategy_id = str(db_strategy.id)
            is_meanrev_strategy = (
                "meanrev" in strategy_name
                or "mean-rev" in strategy_name
                or "mean_rev" in strategy_name
                or "mean_reversion" in strategy_name
            )

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

                elif "bull_flag" in strategy_name:
                    excluded_top_level = ("filters", "parameters", "strategy_id", "name", "max_risk_pct", "volume_threshold")
                    flat_config = {k: v for k, v in config_data.items() if k not in excluded_top_level}
                    if "parameters" in config_data:
                        flat_config.update({k: v for k, v in config_data["parameters"].items() if k != "strategy_id"})
                    mhc = config_data.get("max_hold_candles")
                    if mhc is not None and "max_hold_candles" not in flat_config:
                        flat_config["max_hold_candles"] = mhc
                    config = BullFlagConfig(strategy_id=strategy_id, **flat_config)
                    strategy = BullFlagStrategy(config)
                
                # Legacy strategies
                elif "meanrev" in strategy_name or "mean-rev" in strategy_name or "mean_rev" in strategy_name or "mean_reversion" in strategy_name:
                    from dataclasses import fields as _dc_fields

                    excluded_top_level = (
                        "filters", "parameters", "strategy_id", "name", "max_risk_pct", "volume_threshold"
                    )
                    flat_mr = {k: v for k, v in config_data.items() if k not in excluded_top_level}
                    if "parameters" in config_data:
                        flat_mr.update(
                            {k: v for k, v in config_data["parameters"].items() if k != "strategy_id"}
                        )
                    _valid = {f.name for f in _dc_fields(MeanReversionConfig)}
                    safe_mr = {k: v for k, v in flat_mr.items() if k in _valid}
                    config = MeanReversionConfig(strategy_id=strategy_id, **safe_mr)
                    strategy = MeanReversionStrategy(config)
                    
                elif "momentum" in strategy_name or "trend_follow" in strategy_name or "trend-follow" in strategy_name:
                    config = MomentumConfig(
                        strategy_id=strategy_id,
                        symbol=config_data.get("symbol", "BTC/USD"),
                        lookback_period=config_data.get("lookback_period", 14),
                    )
                    strategy = MomentumStrategy(config)
                
                if strategy is not None:
                    # Wrap the strategy with an evaluate adapter
                    strategy_wrapper = _StrategyEvaluateAdapter(strategy, strategy_id, strategy_name)
                    
                    # Get screener settings from strategy filters
                    filters = config_data.get("filters", {})
                    confidence_buy = filters.get("confidence_buy", 90)
                    confidence_sell = filters.get("confidence_sell", 90)
                    min_allowed_grade = filters.get("min_allowed_grade", "A+") or "A+"
                    
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

                    if is_meanrev_strategy:
                        try:
                            raw_ap = client.hgetall(APLUS_SCORES_KEY)
                            extra_syms = [
                                (kb.decode() if isinstance(kb, bytes) else str(kb))
                                for kb in (raw_ap or {}).keys()
                            ]
                        except Exception:
                            extra_syms = []
                        ste = list(set(top_10_symbols + ingestor_symbols + extra_syms))
                        all_sym = list(set(ingestor_symbols + ste))
                    else:
                        ste = symbols_to_evaluate
                        all_sym = all_symbols
                    
                    # For Top 10 Obvious / A+ pairs, skip liquidity filter since they're already scored and shown in unified screener
                    # Only apply filters to ingestor symbols that aren't in the evaluated symbol list
                    ingestor_only_symbols = [s for s in all_sym if s not in ste]
                    evaluated_only_symbols = [s for s in all_sym if s in ste]
                    
                    # Apply filters only to non-A+ symbols
                    if ingestor_only_symbols:
                        filtered_ingestor, skip_reasons_ingestor = await self._apply_global_filters(
                            ingestor_only_symbols, strategy_id
                        )
                    else:
                        filtered_ingestor, skip_reasons_ingestor = [], {}
                    
                    # Top 10 Obvious / A+ pairs bypass liquidity filter (they're already scored)
                    # Only apply whitelist filter if in shadow mode
                    filtered_evaluated = []
                    skip_reasons_evaluated = {}
                    if evaluated_only_symbols:
                        try:
                            shadow_mode = get_bot_mode() == "SHADOW"
                            enforce_whitelist = get_enforce_whitelist_in_shadow()
                            if shadow_mode and enforce_whitelist:
                                from backend.ingestor.symbols import is_in_live_universe
                                for symbol in evaluated_only_symbols:
                                    if is_in_live_universe(symbol):
                                        filtered_evaluated.append(symbol)
                                    else:
                                        skip_reasons_evaluated[symbol] = "not in whitelist"
                            else:
                                filtered_evaluated = evaluated_only_symbols
                        except Exception as e:
                            logger.debug(f"Error filtering evaluated pairs: {e}")
                            filtered_evaluated = evaluated_only_symbols
                    
                    # Combine filtered symbols
                    filtered_symbols = filtered_ingestor + filtered_evaluated
                    skip_reasons = {**skip_reasons_ingestor, **skip_reasons_evaluated}
                    
                    if evaluated_only_symbols:
                        logger.info(f"[STRATEGY_SCANS] Top 10 Obvious / A+ pairs ({len(filtered_evaluated)}/{len(evaluated_only_symbols)}) bypass liquidity filter for strategy evaluation")
                    
                    # Fetch bars at strategy's configured interval (only for filtered symbols)
                    strategy_symbols_bars = {}
                    for symbol in filtered_symbols:
                        if is_meanrev_strategy:
                            bars_4h = await self._get_recent_bars(
                                symbol, self.bars_to_fetch, target_interval="4h"
                            )
                            if not self._meanrev_4h_gate_from_bars(bars_4h):
                                continue
                        bars = await self._get_recent_bars(
                            symbol, self.bars_to_fetch, target_interval=strategy_interval
                        )
                        strategy_symbols_bars[symbol] = bars
                    
                    logger.info(
                        f"[STRATEGY] {strategy_name} (id={strategy_id}): "
                        f"interval={strategy_interval}, symbols={len(strategy_symbols_bars)}, "
                        f"confidence_buy={confidence_buy}, confidence_sell={confidence_sell}, "
                        f"min_allowed_grade={min_allowed_grade}"
                    )
                    # Run the scan with strategy-specific interval bars and thresholds
                    await self._run_strategy_scan(
                        strategy_wrapper, strategy_symbols_bars, interval=strategy_interval,
                        confidence_buy=confidence_buy, confidence_sell=confidence_sell,
                        min_allowed_grade=min_allowed_grade,
                    )
                else:
                    logger.warning(f"Unknown strategy type: {strategy_name} (id={strategy_id})")
                    
            except Exception as e:
                logger.error(f"Error running strategy {strategy_name}: {e}", exc_info=True)
    
    async def _run_loop(self) -> None:
        """Main scan loop."""
        logger.info("Screener service started")
        logger.info(f"[SCAN-LOOP] Starting scan loop (interval={self.scan_interval}s)")
        
        # Delay first scan to allow uvicorn to start listening before heavy A+ scoring begins
        # This prevents 502 Bad Gateway errors during startup
        INITIAL_SCAN_DELAY = 10.0  # seconds
        logger.info(f"[SCAN-LOOP] Delaying first scan by {INITIAL_SCAN_DELAY}s to allow API server to start listening")
        await asyncio.sleep(INITIAL_SCAN_DELAY)
        logger.info(f"[SCAN-LOOP] Initial delay complete, starting first scan")
        
        scan_count = 0
        while self._running:
            try:
                if self._scan_task and not self._scan_task.done():
                    logger.info("[SCAN-LOOP] Previous scan still running, skipping")
                else:
                    scan_count += 1
                    logger.info(
                        f"[SCAN-LOOP] Firing scan #{scan_count} at "
                        f"{datetime.now(timezone.utc).isoformat()}"
                    )
                    self._scan_counter = scan_count
                    self._scan_task = asyncio.create_task(self._execute_scan(scan_count))
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

        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None
        
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
    
    def get_scan_status(self) -> Dict[str, Any]:
        """Get current scan status from Redis."""
        client = get_redis_client()
        try:
            raw = client.get(SCREENER_SCAN_STATUS_KEY)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                return json.loads(raw)
        except Exception as e:
            logger.debug(f"Failed to get scan status: {e}")
        return {
            "in_progress": False,
            "stage": "idle",
            "last_error": None,
        }
    
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
                result["trading_enabled"] = get_bot_mode() == "LIVE"
                return result
        except Exception as e:
            logger.error(f"Failed to get strategy results for {strategy_id}: {e}")
        
        return None
