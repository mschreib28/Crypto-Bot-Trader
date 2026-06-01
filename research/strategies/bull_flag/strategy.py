"""Bull Flag + Momentum Pullback (I1) — long-only intraday strategy."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from research.strategies.base import BaseStrategy
from research.strategies.bull_flag.config import BullFlagConfig
from research.strategies.indicators import (
    calculate_atr,
    calculate_ema_series,
    calculate_rsi,
    calculate_vwap,
)
from research.strategies.types import MarketDataEvent, SignalResult

logger = logging.getLogger(__name__)

# D4 — BTC not down more than 4% over last 4h step (matches pipeline.py)
D4_MAX_BTC_DROP = -4.0

# Redis MTF confluence: 1m + 5m pattern alignment (TTL seconds)
_BULL_FLAG_MTF_KEY = "bull_flag:mtf:{symbol}"
_BULL_FLAG_MTF_TTL = 900


def _typical(high: float, low: float, close: float) -> float:
    return (high + low + close) / 3.0


def _macd_histogram_state(
    closes: List[float], fast: int, slow: int, sig: int
) -> Optional[Tuple[float, Optional[float]]]:
    """Return (current_histogram, previous_histogram) or None."""
    if len(closes) < slow + sig + 5:
        return None

    def _ema_series(prices: List[float], period: int) -> List[float]:
        s = calculate_ema_series(prices, period)
        return s if s else []

    fe = _ema_series(closes, fast)
    se = _ema_series(closes, slow)
    if len(fe) < len(closes) or len(se) < len(closes):
        return None
    macd_line = [fe[i] - se[i] for i in range(len(closes))]
    valid_start = slow - 1
    mfs = macd_line[valid_start:]
    if len(mfs) < sig + 2:
        return None
    sig_e = calculate_ema_series(mfs, sig)
    if not sig_e or len(sig_e) < len(mfs):
        return None
    off = len(mfs) - len(sig_e)
    hist = [mfs[i + off] - sig_e[i] for i in range(len(sig_e))]
    if len(hist) < 2:
        return None
    return hist[-1], hist[-2]


def _session_vwap(
    highs: List[float], lows: List[float], closes: List[float], volumes: List[float]
) -> Optional[float]:
    if len(closes) < 5:
        return None
    typ = [_typical(highs[i], lows[i], closes[i]) for i in range(len(closes))]
    return calculate_vwap(typ, volumes, anchor_index=0)


def _is_reversal_long(open_: float, high: float, low: float, close: float) -> bool:
    """Hammer-like, bullish engulfing (vs prev), or doji — coarse heuristics."""
    rng = high - low
    if rng <= 0:
        return False
    body = abs(close - open_)
    lower_wick = min(open_, close) - low
    upper_wick = high - max(open_, close)
    # Hammer: long lower wick, small body at top of range
    if lower_wick >= body * 2 and upper_wick <= rng * 0.15 and close > open_:
        return True
    # Doji: very small body
    if body <= rng * 0.1 and close >= low + rng * 0.4:
        return True
    return False


def _is_reversal_engulfing(prev_o: float, prev_h: float, prev_l: float, prev_c: float, o: float, h: float, l: float, c: float) -> bool:
    prev_bear = prev_c < prev_o
    curr_bull = c > o
    return prev_bear and curr_bull and c >= prev_o and o <= prev_c


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


def daily_close_above_daily_ema(
    daily_timestamps: List[Any],
    daily_closes: List[float],
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
    seg = daily_closes[: last_idx + 1]
    ema = calculate_ema_series(seg, ema_period)
    if not ema or last_idx >= len(ema):
        return False
    return seg[last_idx] > ema[last_idx]


def passes_btc_d4_from_two_closes(prev_close: float, last_close: float) -> bool:
    if prev_close <= 0:
        return False
    chg = (last_close - prev_close) / prev_close * 100.0
    return chg >= D4_MAX_BTC_DROP


def _swing_hard_gates_preflight(
    cfg: BullFlagConfig,
    daily_timestamps: Optional[List[Any]],
    daily_closes: Optional[List[float]],
    btc_closes: Optional[List[float]],
    btc_4h_change_pct: Optional[float],
    entry_timestamp: Optional[Any],
) -> bool:
    """Return False if a required swing gate fails (caller returns None)."""
    if cfg.require_daily_ema200:
        if (
            daily_timestamps is None
            or daily_closes is None
            or entry_timestamp is None
            or not daily_close_above_daily_ema(
                daily_timestamps, daily_closes, entry_timestamp, cfg.daily_ema_period
            )
        ):
            return False
    if cfg.require_btc_d4_gate:
        if btc_4h_change_pct is not None:
            if btc_4h_change_pct < D4_MAX_BTC_DROP:
                return False
        elif btc_closes is not None and len(btc_closes) >= 2:
            if not passes_btc_d4_from_two_closes(btc_closes[-2], btc_closes[-1]):
                return False
        # No BTC reading (API/Redis) — fail-open like pipeline D4 when unknown
    return True


@dataclass
class BullFlagEntrySnapshot:
    """Serializable entry snapshot for backtests and Redis MTF."""

    is_strong: bool
    stop: float
    tp1: float
    tp2: float
    pole_height: float
    confidence: float
    breakdown: Dict[str, Any]


def analyze_bull_flag_last_bar(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
    cfg: BullFlagConfig,
    *,
    daily_timestamps: Optional[List[Any]] = None,
    daily_closes: Optional[List[float]] = None,
    btc_closes: Optional[List[float]] = None,
    btc_4h_change_pct: Optional[float] = None,
    entry_timestamp: Optional[Any] = None,
) -> Optional[BullFlagEntrySnapshot]:
    """
    Detect STRONG (bull flag) or MILD (momentum pullback) on the final bar.

    Returns None if no valid long entry on last bar.
    """
    n = len(closes)
    min_n = max(80, cfg.pole_max_candles + cfg.flag_max_candles + cfg.volume_sma_period + cfg.macd_slow + cfg.macd_signal + 10)
    if n < min_n:
        return None

    i = n - 1
    ema9 = calculate_ema_series(closes, cfg.ema_fast)
    ema20 = calculate_ema_series(closes, cfg.ema_slow)
    if not ema9 or not ema20 or len(ema9) < n or len(ema20) < n:
        return None

    ts_entry = entry_timestamp if entry_timestamp is not None else i
    if not _swing_hard_gates_preflight(
        cfg,
        daily_timestamps,
        daily_closes,
        btc_closes,
        btc_4h_change_pct,
        ts_entry,
    ):
        return None

    vwap = _session_vwap(highs, lows, closes, volumes)
    rsi = calculate_rsi(closes, period=cfg.rsi_period)
    macd_h = _macd_histogram_state(closes, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)

    def vol_sma(end_idx: int) -> float:
        lo = max(0, end_idx - cfg.volume_sma_period)
        seg = volumes[lo:end_idx]
        if not seg:
            return 0.0
        return sum(seg) / len(seg)

    # --- STRONG: try flag lengths ---
    strong_snap: Optional[BullFlagEntrySnapshot] = None
    for flag_len in range(cfg.flag_min_candles, cfg.flag_max_candles + 1):
        brk = i
        flag_start = brk - flag_len
        if flag_start <= 0:
            continue
        pole_end = flag_start - 1
        if pole_end < 1:
            continue

        best_pole: Optional[Tuple[int, int, float, float]] = None  # start, end, pct, pole_hi
        for pole_len in range(1, cfg.pole_max_candles):
            pole_start = pole_end - pole_len + 1
            if pole_start < 0:
                break
            c0 = closes[pole_start]
            c1 = closes[pole_end]
            if c0 <= 0:
                continue
            pct = (c1 - c0) / c0 * 100.0
            if pct < cfg.pole_min_pct:
                continue
            pole_hi = max(highs[pole_start : pole_end + 1])
            pole_lo = min(lows[pole_start : pole_end + 1])
            pole_h = max(pole_hi - c0, c1 - pole_lo, 1e-12)
            sma0 = vol_sma(pole_start)
            pole_vols = volumes[pole_start : pole_end + 1]
            if sma0 <= 0 or not pole_vols:
                continue
            if max(pole_vols) < cfg.pole_volume_multiplier * sma0:
                continue
            best_pole = (pole_start, pole_end, pct, pole_h)
            break
        if best_pole is None:
            continue

        pole_start, pole_end, _pole_pct, pole_height = best_pole
        flag_lo = lows[flag_start:brk]
        flag_hi = highs[flag_start:brk]
        flag_cl = closes[flag_start:brk]
        flag_vo = volumes[flag_start:brk]
        if len(flag_lo) != flag_len:
            continue

        # Higher lows (or flat)
        ok_hl = all(lows[flag_start + k] >= lows[flag_start + k - 1] * 0.9999 for k in range(1, flag_len))
        if not ok_hl:
            continue

        # Declining volume each bar in flag
        ok_vol = all(flag_vo[k] < flag_vo[k - 1] for k in range(1, len(flag_vo)))
        if not ok_vol:
            continue

        # Retracement < 50% of pole (measured from pole trough to peak)
        pole_peak_close = max(closes[pole_start : pole_end + 1])
        pole_trough_close = min(closes[pole_start : pole_end + 1])
        trough_flag = min(flag_cl)
        pole_range = max(pole_peak_close - pole_trough_close, 1e-12)
        retr = (pole_peak_close - trough_flag) / pole_range
        if retr > cfg.flag_max_retracement:
            continue

        # During flag: close above 9 EMA
        ok_ema_flag = all(
            flag_cl[k] >= ema9[flag_start + k] * 0.999 for k in range(len(flag_cl))
        )
        if not ok_ema_flag:
            continue

        flag_high = max(highs[flag_start:brk])
        if closes[brk] <= flag_high:
            continue

        avg_flag_vol = sum(flag_vo) / len(flag_vo)
        if volumes[brk] <= avg_flag_vol * max(cfg.entry_volume_multiplier, 1.0):
            continue
        if flag_len >= 1 and volumes[brk] <= volumes[brk - 1]:
            continue

        # VWAP: flag holds above VWAP (session VWAP at flag_end)
        vwap_f = _session_vwap(highs[: pole_end + 1], lows[: pole_end + 1], closes[: pole_end + 1], volumes[: pole_end + 1])
        if vwap_f is None:
            continue
        if min(closes[flag_start:brk]) < vwap_f * 0.998:
            continue

        if closes[brk] <= ema20[brk]:
            continue
        if rsi is not None and rsi > cfg.rsi_overbought:
            continue
        if macd_h is None or macd_h[0] <= 0:
            continue
        if macd_h[1] is not None and macd_h[0] <= macd_h[1]:
            continue

        flag_low = min(lows[flag_start:brk])
        stop_cand = min(flag_low, ema9[brk] * 0.997)
        entry = closes[brk]
        if entry <= stop_cand:
            continue
        risk = entry - stop_cand
        pole_meas = max(closes[pole_end] - closes[pole_start], entry * 0.01)
        tp2 = entry + pole_meas
        tp1 = entry + risk * cfg.tp1_R

        vw_ok = vwap is not None and closes[brk] >= (vwap or 0) * 0.999
        conf, brkdown = _build_confidence(
            cfg,
            is_strong=True,
            vol_ok=True,
            ind_ok=(
                vw_ok,
                closes[brk] > ema9[brk],
                closes[brk] > ema20[brk],
                macd_h[0] > 0 and (macd_h[1] is None or macd_h[0] > macd_h[1]),
                rsi is not None and rsi < cfg.rsi_overbought,
            ),
        )
        strong_snap = BullFlagEntrySnapshot(
            is_strong=True,
            stop=stop_cand,
            tp1=min(tp1, tp2),
            tp2=tp2,
            pole_height=pole_meas,
            confidence=conf,
            breakdown=brkdown,
        )
        break

    if strong_snap:
        return strong_snap

    # --- MILD ---
    if not cfg.allow_mild_pullback:
        return None

    if closes[i] <= ema9[i] or closes[i] <= ema20[i]:
        return None

    e9, e20 = ema9[i], ema20[i]
    touch_9 = lows[i] <= e9 <= highs[i] or abs(closes[i] - e9) / max(e9, 1e-12) * 100 <= 0.15
    touch_vwap = False
    if vwap and vwap > 0:
        touch_vwap = abs(closes[i] - vwap) / vwap * 100 <= cfg.vwap_touch_pct
    if not (touch_9 or touch_vwap):
        return None

    lb = max(1, i - cfg.mild_initial_vol_lookback)
    init_move_vol = max(volumes[lb:i])
    if volumes[i] >= init_move_vol:
        return None

    prev_o, prev_h, prev_l, prev_c = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
    rev = _is_reversal_long(opens[i], highs[i], lows[i], closes[i]) or _is_reversal_engulfing(
        prev_o, prev_h, prev_l, prev_c, opens[i], highs[i], lows[i], closes[i]
    )
    if not rev:
        return None

    if vwap is not None and closes[i] < vwap * 0.995:
        return None
    if rsi is not None and rsi > cfg.rsi_overbought:
        return None
    if macd_h is None or macd_h[0] <= 0 or (macd_h[1] is not None and macd_h[0] <= macd_h[1]):
        return None

    swing_hi = max(highs[max(0, i - 20) : i])
    stop_cand = min(lows[i], ema9[i] * 0.997)
    entry = closes[i]
    if entry <= stop_cand:
        return None
    risk = entry - stop_cand
    tp2_r = entry + risk * cfg.tp2_R
    tp2_swing = swing_hi
    tp2 = max(tp2_r, tp2_swing)
    tp1 = entry + risk * cfg.tp1_R

    conf, brkdown = _build_confidence(
        cfg,
        is_strong=False,
        vol_ok=True,
        ind_ok=(
            vwap is None or closes[i] >= vwap,
            closes[i] > ema9[i],
            closes[i] > ema20[i],
            macd_h[0] > 0,
            rsi is not None and rsi < cfg.rsi_overbought,
        ),
    )
    return BullFlagEntrySnapshot(
        is_strong=False,
        stop=stop_cand,
        tp1=tp1,
        tp2=tp2,
        pole_height=0.0,
        confidence=conf,
        breakdown=brkdown,
    )


def _build_confidence(
    cfg: BullFlagConfig,
    is_strong: bool,
    vol_ok: bool,
    ind_ok: Tuple[bool, bool, bool, bool, bool],
) -> Tuple[float, Dict[str, Any]]:
    # Mild base 40%; strong base 60% (includes +20 for pole+flag vs mild).
    pts = (cfg.confidence_base_strong if is_strong else cfg.confidence_base_mild) * 100.0
    bd: Dict[str, Any] = {"base_pct": pts, "strong": is_strong}
    if vol_ok:
        pts += 15.0
        bd["volume_bonus"] = 15.0
    each = sum(1 for x in ind_ok if x)
    ind_pts = each * 3.0
    pts += ind_pts
    bd["indicator_hits"] = each
    bd["indicator_bonus"] = ind_pts
    pts = min(100.0, pts)
    bd["total"] = pts
    return pts, bd


def _read_mtf_bonus(symbol: str, cfg: BullFlagConfig) -> float:
    """If 5m instance and Redis shows recent 1m pattern flag, add multi_tf_bonus (as 0–100 points)."""
    if cfg.interval != "5m":
        return 0.0
    try:
        from backend.redis import get_redis_client

        r = get_redis_client()
        raw = r.get(_BULL_FLAG_MTF_KEY.format(symbol=symbol))
        if not raw:
            return 0.0
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        if data.get("1m_active"):
            return cfg.multi_tf_bonus * 100.0
    except Exception:
        return 0.0
    return 0.0


def _write_mtf_1m(symbol: str) -> None:
    try:
        from backend.redis import get_redis_client

        r = get_redis_client()
        key = _BULL_FLAG_MTF_KEY.format(symbol=symbol)
        r.setex(key, _BULL_FLAG_MTF_TTL, json.dumps({"1m_active": True}))
    except Exception:
        pass


class BullFlagStrategy(BaseStrategy):
    """Long-only bull flag (strong) or momentum pullback (mild)."""

    def __init__(self, config: Optional[BullFlagConfig] = None):
        if config is None:
            config = BullFlagConfig()
        super().__init__(config.strategy_id)
        self.config = config

    def generate_signals(self, bar: MarketDataEvent) -> Any:
        return None

    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        min_bars = max(
            80,
            self.config.pole_max_candles
            + self.config.flag_max_candles
            + self.config.volume_sma_period
            + self.config.macd_slow
            + self.config.macd_signal
            + 10,
        )
        if not bars or len(bars) < min_bars:
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.strategy_id,
                indicators={"note": "insufficient_bars"},
                timestamp=ts,
            )

        o = [b.open for b in bars]
        h = [b.high for b in bars]
        l = [b.low for b in bars]
        c = [b.close for b in bars]
        v = [b.volume for b in bars]

        entry_timestamp = bars[-1].timestamp
        daily_timestamps: Optional[List[Any]] = None
        daily_closes: Optional[List[float]] = None
        btc_4h_change_pct: Optional[float] = None

        if self.config.require_daily_ema200:
            try:
                from backend.screener.strategy_columns import _fetch_bars_from_redis

                raw = _fetch_bars_from_redis(symbol, "1d", max(self.config.daily_ema_period + 30, 230))
                entry_dt = _coerce_ts(entry_timestamp)
                if raw and entry_dt is not None:
                    filt_ts: List[Any] = []
                    filt_c: List[float] = []
                    for row in raw:
                        t_raw = row.get("timestamp")
                        cd = _coerce_ts(t_raw)
                        if cd is not None and cd <= entry_dt:
                            filt_ts.append(t_raw)
                            filt_c.append(float(row.get("close", 0.0)))
                    if filt_ts:
                        daily_timestamps, daily_closes = filt_ts, filt_c
            except Exception:
                daily_timestamps, daily_closes = None, None

        if self.config.require_btc_d4_gate:
            try:
                from backend.screener.pipeline import fetch_btc_4h_change

                btc_4h_change_pct = fetch_btc_4h_change()
            except Exception:
                btc_4h_change_pct = None

        snap = analyze_bull_flag_last_bar(
            o,
            h,
            l,
            c,
            v,
            self.config,
            daily_timestamps=daily_timestamps,
            daily_closes=daily_closes,
            btc_closes=None,
            btc_4h_change_pct=btc_4h_change_pct,
            entry_timestamp=entry_timestamp,
        )
        if snap is None:
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.strategy_id,
                indicators={"direction": "neutral"},
                timestamp=ts,
            )

        conf = snap.confidence
        if self.config.interval == "1m":
            _write_mtf_1m(symbol)
        elif self.config.interval == "5m":
            conf = min(100.0, conf + _read_mtf_bonus(symbol, self.config))

        return SignalResult(
            symbol=symbol,
            signal_type="BUY",
            confidence=conf,
            strategy_id=self.strategy_id,
            indicators={
                "direction": "long",
                "grade": "STRONG" if snap.is_strong else "MILD",
                "stop": snap.stop,
                "tp1": snap.tp1,
                "tp2": snap.tp2,
                "confidence_breakdown": snap.breakdown,
            },
            timestamp=ts,
        )
