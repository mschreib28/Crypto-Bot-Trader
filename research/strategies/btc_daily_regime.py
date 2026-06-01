"""BTC daily regime helpers (200d EMA bull gate) shared by live strategies and backtests."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Optional, Sequence

from backend.redis.keys import MARKET_OHLCV_STREAM

from research.strategies.indicators import calculate_ema_series
from research.strategies.types import MarketDataEvent

logger = logging.getLogger(__name__)

BTC_DAILY_STREAM_SYMBOL = "BTC/USD"
BTC_DAILY_INTERVAL = "1d"


def _coerce_ts(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        s = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


def btc_daily_close_above_ema_at_ts(
    daily_timestamps: Sequence[Any],
    daily_closes: Sequence[float],
    entry_ts: Any,
    ema_period: int,
) -> bool:
    """Last completed daily bar at or before entry_ts must close above EMA(ema_period)."""
    if len(daily_timestamps) != len(daily_closes) or len(daily_closes) < ema_period:
        return False
    entry_dt = _coerce_ts(entry_ts)
    if entry_dt is None:
        return False
    last_idx = -1
    for idx in range(len(daily_timestamps)):
        dt = _coerce_ts(daily_timestamps[idx])
        if dt is not None and dt <= entry_dt:
            last_idx = idx
    if last_idx < ema_period - 1:
        return False
    seg = list(daily_closes[: last_idx + 1])
    ema = calculate_ema_series(seg, ema_period)
    if not ema or last_idx >= len(ema):
        return False
    return seg[last_idx] > ema[last_idx]


def slice_btc_daily_bars_up_to_ts(
    daily_bars: Sequence[MarketDataEvent],
    entry_ts: Any,
) -> List[MarketDataEvent]:
    """Return BTC daily bars with bar time <= entry_ts (chronological order preserved)."""
    entry_dt = _coerce_ts(entry_ts)
    if entry_dt is None:
        return []
    out: List[MarketDataEvent] = []
    for b in daily_bars:
        dt = _coerce_ts(b.timestamp)
        if dt is not None and dt <= entry_dt:
            out.append(b)
    return out


def btc_daily_bars_pass_bull_filter_at_ts(
    daily_bars: Sequence[Any],
    entry_ts: Any,
    ema_period: int,
) -> bool:
    """Extract timestamps/closes from bar-like objects (Bar, MarketDataEvent, etc.)."""
    if not daily_bars:
        return False
    ts_list = [getattr(b, "timestamp", None) for b in daily_bars]
    cl_list = [float(getattr(b, "close", 0.0)) for b in daily_bars]
    return btc_daily_close_above_ema_at_ts(ts_list, cl_list, entry_ts, ema_period)


def fetch_btc_daily_bars(strategy_id: str, count: int = 400) -> List[MarketDataEvent]:
    """
    Load BTC/USD 1d bars from Redis (oldest first), same stream shape as fetch_htf_bars.
    """
    stream_key = MARKET_OHLCV_STREAM.format(
        symbol=BTC_DAILY_STREAM_SYMBOL, interval=BTC_DAILY_INTERVAL
    )
    consumer_name = f"{strategy_id}_btc1d"
    try:
        from backend.redis import get_redis_client

        redis_client = get_redis_client()
        messages = redis_client.xrange(stream_key, count=count)
        events: List[MarketDataEvent] = []
        for msg_id, data in messages:
            try:
                events.append(
                    MarketDataEvent(
                        symbol=data.get("symbol", BTC_DAILY_STREAM_SYMBOL),
                        interval=data.get("interval", BTC_DAILY_INTERVAL),
                        open=float(data.get("open", 0)),
                        high=float(data.get("high", 0)),
                        low=float(data.get("low", 0)),
                        close=float(data.get("close", 0)),
                        volume=float(data.get("volume", 0)),
                        timestamp=str(data.get("timestamp", "")),
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                logger.debug("Failed to parse BTC daily bar %s: %s", msg_id, e)
                continue
        logger.debug(
            "Fetched %s BTC daily bars for %s", len(events), stream_key
        )
        return events
    except Exception as e:
        logger.warning(
            "Failed to fetch BTC daily bars from %s (%s): %s",
            stream_key,
            consumer_name,
            e,
        )
        return []
