"""Strategy-specific column calculations for unified screener.

This module calculates VWAP Distance, HOD Distance, and HTF Trend columns
for symbols that pass the Score > 0.55 threshold (two-stage processing).
"""

import logging
import math
from typing import Any, Dict, List, Optional

from backend.redis import get_redis_client
from backend.redis.keys import MARKET_OHLCV_STREAM
from research.strategies.indicators import (
    calculate_ema,
    calculate_ema_series,
    calculate_ema_slope,
)
from research.strategies.types import MarketDataEvent

logger = logging.getLogger(__name__)

# Cache TTL for strategy column calculations (60 seconds)
STRATEGY_COLUMNS_CACHE_TTL = 60


def _get_cache_key(symbol: str, column_type: str) -> str:
    """Get Redis cache key for strategy column calculation."""
    return f"screener:strategy_columns:{column_type}:{symbol}"


def _fetch_bars_from_redis(symbol: str, interval: str, count: int) -> List[Dict[str, Any]]:
    """
    Fetch bars from Redis stream, using same approach as screener service.
    
    Tries target interval first, then aggregates from 1m bars (which ingestor stores).
    """
    try:
        from backend.screener.aggregator import aggregate_bars, INTERVAL_MINUTES
        
        client = get_redis_client()
        stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval)
        
        # Try target interval first
        entries = client.xrevrange(stream_key, count=count)
        bars = []
        for entry_id, data in entries:
            # Handle both bytes and string keys (Redis can return either)
            open_val = data.get(b"open") or data.get("open", 0)
            high_val = data.get(b"high") or data.get("high", 0)
            low_val = data.get(b"low") or data.get("low", 0)
            close_val = data.get(b"close") or data.get("close", 0)
            volume_val = data.get(b"volume") or data.get("volume", 0)
            timestamp_val = data.get(b"timestamp") or data.get("timestamp", "")
            
            bar = {
                "open": float(open_val) if open_val else 0.0,
                "high": float(high_val) if high_val else 0.0,
                "low": float(low_val) if low_val else 0.0,
                "close": float(close_val) if close_val else 0.0,
                "volume": float(volume_val) if volume_val else 0.0,
                "timestamp": timestamp_val.decode() if isinstance(timestamp_val, bytes) else str(timestamp_val),
            }
            bars.append(bar)
        
        bars.reverse()
        
        # Check if bars are valid
        valid_bars = [b for b in bars if b.get("volume", 0) > 0 and b.get("close", 0) > 0]
        
        # If we have enough valid bars for the requested count, return them
        # For 1h, we need at least 210 bars (200 EMA warmup + 10 slope buffer); for 15m, at least 20
        min_required = 210 if interval == "1h" else 20
        if valid_bars and len(valid_bars) >= min_required:
            return valid_bars[:count]  # Return only the requested count
        
        # Otherwise, aggregate from 1m bars (ingestor stores 1m bars)
        logger.info(f"[STRATEGY_COLUMNS] Insufficient valid {interval} bars for {symbol} ({len(valid_bars)}/{min_required}), aggregating from 1m bars")
        target_minutes = INTERVAL_MINUTES.get(interval, 15)
        
        # Fetch 1m bars (need more for aggregation to ensure we get enough complete bars)
        # Add 10% buffer to account for incomplete chunks
        bars_needed = int(count * target_minutes * 1.1)  # e.g., 200 * 15 * 1.1 = 3300 bars for 200 15m bars
        stream_key_1m = MARKET_OHLCV_STREAM.format(symbol=symbol, interval="1m")
        entries_1m = client.xrevrange(stream_key_1m, count=bars_needed)
        logger.info(f"[STRATEGY_COLUMNS] Fetched {len(entries_1m)} 1m entries for {symbol} (needed {bars_needed})")
        
        if entries_1m:
            bars_1m = []
            for entry_id, data in entries_1m:
                open_val = data.get(b"open") or data.get("open", 0)
                high_val = data.get(b"high") or data.get("high", 0)
                low_val = data.get(b"low") or data.get("low", 0)
                close_val = data.get(b"close") or data.get("close", 0)
                volume_val = data.get(b"volume") or data.get("volume", 0)
                timestamp_val = data.get(b"timestamp") or data.get("timestamp", "")
                
                bar = {
                    "symbol": symbol,
                    "interval": "1m",
                    "open": float(open_val) if open_val else 0.0,
                    "high": float(high_val) if high_val else 0.0,
                    "low": float(low_val) if low_val else 0.0,
                    "close": float(close_val) if close_val else 0.0,
                    "volume": float(volume_val) if volume_val else 0.0,
                    "timestamp": timestamp_val.decode() if isinstance(timestamp_val, bytes) else str(timestamp_val),
                }
                bars_1m.append(bar)
            
            bars_1m.reverse()
            logger.info(f"[STRATEGY_COLUMNS] Parsed {len(bars_1m)} 1m bars for {symbol}")
            
            # Filter for valid 1m bars
            valid_1m = [b for b in bars_1m if b.get("volume", 0) > 0 and b.get("close", 0) > 0]
            logger.info(f"[STRATEGY_COLUMNS] Found {len(valid_1m)} valid 1m bars out of {len(bars_1m)} total for {symbol}")
            
            if valid_1m:
                # Aggregate to target interval
                bars = aggregate_bars(valid_1m, interval, source_interval="1m")
                logger.info(f"[STRATEGY_COLUMNS] Aggregated {len(valid_1m)} valid 1m bars into {len(bars)} {interval} bars for {symbol}")
                # Return only the requested count (most recent bars)
                if len(bars) > count:
                    bars = bars[-count:]
                return bars
            else:
                logger.warning(f"[STRATEGY_COLUMNS] No valid 1m bars found for {symbol} (found {len(bars_1m)} total, all zeros)")
        else:
            logger.warning(f"[STRATEGY_COLUMNS] No 1m entries found for {symbol} at stream {stream_key_1m}")
        
        return []
    except Exception as e:
        logger.error(f"[STRATEGY_COLUMNS] Error fetching bars for {symbol} {interval}: {e}", exc_info=True)
        return []


def _aggregate_bars(bars: List[Dict[str, Any]], target_interval: str) -> List[Dict[str, Any]]:
    """
    Aggregate smaller interval bars into larger interval bars.
    
    Args:
        bars: List of bars from smaller interval (e.g., 5m bars)
        target_interval: Target interval (e.g., "15m")
        
    Returns:
        List of aggregated bars
    """
    if not bars:
        return []
    
    # Determine aggregation ratio
    if target_interval == "15m":
        ratio = 3  # 3 x 5m = 15m
    else:
        # For other intervals, return as-is (not implemented)
        return bars
    
    aggregated = []
    for i in range(0, len(bars), ratio):
        chunk = bars[i:i + ratio]
        if not chunk:
            break
        
        # Aggregate OHLCV
        agg_bar = {
            "open": chunk[0]["open"],
            "high": max(bar["high"] for bar in chunk),
            "low": min(bar["low"] for bar in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(bar["volume"] for bar in chunk),
            "timestamp": chunk[0]["timestamp"],  # Use first bar's timestamp
        }
        aggregated.append(agg_bar)
    
    return aggregated


def calculate_vwap_distance(symbol: str, bars: Optional[List[Dict[str, Any]]] = None) -> Optional[float]:
    """
    Calculate VWAP and return distance % from current price.
    
    Args:
        symbol: Trading pair symbol
        bars: Optional pre-fetched bars (15m interval)
        
    Returns:
        Distance percentage (positive = above VWAP, negative = below VWAP) or None
    """
    try:
        # Check cache first
        client = get_redis_client()
        cache_key = _get_cache_key(symbol, "vwap_dist")
        cached = client.get(cache_key)
        if cached:
            try:
                return float(cached)
            except (ValueError, TypeError):
                pass
        
        # Fetch bars if not provided
        if bars is None:
            bars = _fetch_bars_from_redis(symbol, "15m", 200)
        
        if len(bars) < 20:
            logger.debug(f"VWAP: Insufficient bars for {symbol}: {len(bars)} < 20")
            return None
        
        # Validate bars have valid data
        valid_bars = [b for b in bars if b.get("volume", 0) > 0 and b.get("close", 0) > 0]
        if len(valid_bars) < 20:
            logger.debug(f"VWAP: Insufficient valid bars for {symbol}: {len(valid_bars)} < 20 (total: {len(bars)})")
            return None
        
        # Calculate VWAP (24h session VWAP)
        # VWAP = sum(price * volume) / sum(volume) for session
        total_pv = 0.0
        total_volume = 0.0
        
        # Use last 24 hours of 15m bars (96 bars = 24 hours)
        session_bars = bars[-96:] if len(bars) >= 96 else bars
        
        for bar in session_bars:
            # Use typical price (HLC/3) for VWAP calculation
            typical_price = (bar["high"] + bar["low"] + bar["close"]) / 3.0
            volume = bar["volume"]
            total_pv += typical_price * volume
            total_volume += volume
        
        if total_volume == 0:
            return None
        
        vwap = total_pv / total_volume
        current_price = bars[-1]["close"]
        
        # Calculate distance percentage
        distance_pct = ((current_price - vwap) / vwap) * 100.0
        
        # Cache result
        try:
            client.setex(cache_key, STRATEGY_COLUMNS_CACHE_TTL, str(distance_pct))
        except Exception as e:
            logger.debug(f"Failed to cache VWAP distance: {e}")
        
        return distance_pct
    except Exception as e:
        logger.warning(f"Error calculating VWAP distance for {symbol}: {e}", exc_info=True)
        return None


def calculate_hod_distance(symbol: str, bars: Optional[List[Dict[str, Any]]] = None) -> Optional[float]:
    """
    Calculate High of Day (HOD) and return distance % from current price.
    
    Args:
        symbol: Trading pair symbol
        bars: Optional pre-fetched bars (15m interval)
        
    Returns:
        Distance percentage (positive = below HOD, negative = above HOD) or None
    """
    try:
        # Check cache first
        client = get_redis_client()
        cache_key = _get_cache_key(symbol, "hod_dist")
        cached = client.get(cache_key)
        if cached:
            try:
                return float(cached)
            except (ValueError, TypeError):
                pass
        
        # Fetch bars if not provided
        if bars is None:
            bars = _fetch_bars_from_redis(symbol, "15m", 200)
        
        if len(bars) < 1:
            logger.debug(f"HOD: No bars for {symbol}")
            return None
        
        # Validate bars have valid data
        valid_bars = [b for b in bars if b.get("high", 0) > 0 and b.get("close", 0) > 0]
        if len(valid_bars) < 1:
            logger.debug(f"HOD: No valid bars for {symbol} (total: {len(bars)})")
            return None
        
        # Calculate High of Day from all bars
        hod = max(bar["high"] for bar in bars)
        current_price = bars[-1]["close"]
        
        # Calculate distance percentage
        distance_pct = ((hod - current_price) / hod) * 100.0
        
        # Cache result
        try:
            client.setex(cache_key, STRATEGY_COLUMNS_CACHE_TTL, str(distance_pct))
        except Exception as e:
            logger.debug(f"Failed to cache HOD distance: {e}")
        
        return distance_pct
    except Exception as e:
        logger.warning(f"Error calculating HOD distance for {symbol}: {e}", exc_info=True)
        return None


def calculate_htf_trend(symbol: str, htf_bars: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """
    Calculate HTF trend direction using 1h EMA200 slope.
    
    Args:
        symbol: Trading pair symbol
        htf_bars: Optional pre-fetched 1h bars
        
    Returns:
        "UP" if bullish, "DOWN" if bearish, or None if insufficient data
    """
    try:
        # Check cache first
        client = get_redis_client()
        cache_key = _get_cache_key(symbol, "htf_trend")
        cached = client.get(cache_key)
        if cached:
            try:
                trend = cached.decode() if isinstance(cached, bytes) else str(cached)
                if trend in ("UP", "DOWN"):
                    return trend
            except (ValueError, TypeError):
                pass
        
        # Fetch bars if not provided — need 250 so EMA200 has ≥50 series points for slope
        if htf_bars is None:
            htf_bars = _fetch_bars_from_redis(symbol, "1h", 250)

        if len(htf_bars) < 210:
            return None
        
        # Extract closes
        closes = [bar["close"] for bar in htf_bars]
        
        # Calculate EMA200 series
        ema_series = calculate_ema_series(closes, 200)
        if len(ema_series) < 10:
            return None
        
        # Calculate slope over last 5 bars
        slope = calculate_ema_slope(ema_series, bars=5)
        if slope is None:
            return None
        
        # Determine trend direction
        trend = "UP" if slope > 0 else "DOWN"
        
        # Cache result
        try:
            client.setex(cache_key, STRATEGY_COLUMNS_CACHE_TTL, trend)
        except Exception as e:
            logger.debug(f"Failed to cache HTF trend: {e}")
        
        return trend
    except Exception as e:
        logger.debug(f"Error calculating HTF trend for {symbol}: {e}")
        return None


def read_cached_vwap_distance(symbol: str) -> Optional[float]:
    """Read screener VWAP distance % from Redis cache (no recalculation)."""
    try:
        client = get_redis_client()
        cached = client.get(_get_cache_key(symbol, "vwap_dist"))
        if cached is None:
            return None
        text = cached.decode() if isinstance(cached, bytes) else str(cached)
        val = float(text)
        if math.isfinite(val):
            return val
    except (ValueError, TypeError, OSError):
        pass
    return None


def read_cached_htf_trend(symbol: str) -> Optional[str]:
    """Read screener HTF trend direction from Redis cache (UP/DOWN)."""
    try:
        client = get_redis_client()
        cached = client.get(_get_cache_key(symbol, "htf_trend"))
        if cached is None:
            return None
        trend = cached.decode() if isinstance(cached, bytes) else str(cached)
        if trend in ("UP", "DOWN"):
            return trend
    except (ValueError, TypeError, OSError):
        pass
    return None
