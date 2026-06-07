#!/usr/bin/env python3
"""
Crypto Strategy Backtester

Two modes:
  Single-symbol:  python backtest.py --symbol SNX/USD --days 60
  Pipeline mode:  python backtest.py --days 60  (full Kraken USD universe, default)

Pipeline mode is the DEFAULT when --symbol is omitted. The full Kraken USD universe is
fetched automatically; OHLCV is Binance USDT klines (api.binance.com → api.binance.us on 451/refused).
Universe naming stays Kraken (e.g. XBT/USD). Live trading stays Kraken; backtests map prices as USDT.
For each bar, all pairs are graded via the 5-pillar scanner logic
(OHLCV only — no Redis, no live APIs), the top-graded pair is selected, the active
strategy evaluates it for an entry, and the position is held until exit.

Strategies (--strategy): vwap_meanrev (default), vwap_meanrev_1h, pullback_vwap, htf_trend,
                          volatility_breakout, meanrev, bull_flag (alias → 5m I1),
                          bull_flag_1m, bull_flag_5m, bull_flag_1h, swing_bull_flag (W2, 4h)

Skipped (live-data-only): HTF regime filter, 1m green candle check, VWAP slope guard.
Optional HTF RSI gate for VWAP backtests: pass --htf-rsi-long-max and 4h (or --htf-rsi-bars-interval) data.

Usage:
    python backtest.py --days 30                              # all-pairs pipeline, vwap_meanrev
    python backtest.py --days 30 --strategy htf_trend        # all-pairs pipeline, htf_trend
    python backtest.py --symbol SNX/USD --days 60            # single-symbol
    python backtest.py --universe SNX/USD,AXS/USD --days 60  # pipeline, explicit universe
"""

import argparse
import csv
import errno
import json
import os
import sys
import time
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
import urllib.error
import urllib.parse
import urllib.request

# Allow imports from research/strategies/indicators.py without installing the package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from research.strategies.btc_daily_regime import btc_daily_bars_pass_bull_filter_at_ts
from research.strategies.indicators import (
    calculate_adx,
    calculate_atr,
    calculate_atr_ratio,
    calculate_ema,
    calculate_rsi,
    calculate_vwap,
    detect_swing_highs_lows,
)

# ─── Constants ────────────────────────────────────────────────────────────────

BINANCE_REST_COM           = "https://api.binance.com"
BINANCE_REST_US            = "https://api.binance.us"
BINANCE_KLINES_PATH        = "/api/v3/klines"
BINANCE_PAGE_LIMIT         = 1000
YEARS_APPROX_DAYS_PER_YEAR = 365.2425

# Locks to whichever Binance REST host succeeds first (.com preferred).
_BINANCE_LOCKED_BASE: Optional[str] = None
# After first HTTP 451 from api.binance.com, skip probing .com again (same process).
_BINANCE_COM_SKIP: bool = False

KRAKEN_ASSET_PAIRS_URL = "https://api.kraken.com/0/public/AssetPairs"

# Kraken wsname base ticker → Binance base symbol (paired with USDT).
_KRAKEN_BASE_ASSET_ALIAS: dict[str, str] = {
    "XBT": "BTC",
    "XDG": "DOGE",
}

# Tokens that make no sense to trade with this strategy: stablecoins (near 1.0,
# no reversion signal) and wrapped tokens (correlated to their underlying).
_SKIP_BASE_TOKENS: frozenset[str] = frozenset({
    "USDC", "USDT", "DAI", "BUSD", "TUSD", "USDP", "USDD", "FRAX", "LUSD",
    "WBTC", "WETH",
})

INTERVAL_MAP = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}

MAKER_FEE = 0.0016   # 0.16%  BUY fee
TAKER_FEE = 0.0026   # 0.26%  SELL fee

WARMUP_BARS = 60     # Bars consumed before first signal is eligible

# Strategy defaults — mirrors VWAPMeanReversionConfig
DEFAULT_CONFIG: dict = {
    "dev_threshold_pct":          2.0,   # % price must deviate from VWAP
    "rsi_period":                 14,
    "rsi_oversold":               30.0,
    "rsi_overbought":             70.0,
    "atr_period":                 14,
    "atr_stop_mult":              1.5,
    "swing_lookback_bars":        5,
    "stop_buffer_atr":            0.15,
    "tp1_R":                      1.0,
    "tp2_R":                      2.0,
    "tp1_partial_pct":            0.6,   # 60% closed at TP1
    "max_bars_in_trade":          6,
    "volume_sma_period":          20,
    "volume_max_mult":            1.5,
    "anchored_vwap_lookback":     20,
    "reversal_body_pct":          0.6,
    "reversal_close_position":    0.25,
    "momentum_exclusion_bars":    3,
    "momentum_body_pct_threshold":0.6,
    "long_only":                  True,   # Mirrors VWAPMeanReversionConfig; gate shorts in check_entry_signal
    "invalidation_vwap_atr_mult": 2.0,
    "invalidation_rsi_candles":   4,
    "min_stop_pct":               5.0,   # for min-profit gate: 1.5 × stop_pct
    "volume_filter_mode":         "conservative",
    "long_min_volume_ratio":      None,  # e.g. 2.0 = require ≥2× volume SMA on signal bar (long)
    "htf_rsi_long_max":           None,  # e.g. 40.0 = require HTF RSI ≤ 40 (see htf_rsi_bars_interval)
    "htf_rsi_bars_interval":      "4h",  # Binance interval for HTF RSI when htf_rsi_long_max is set
    "breakeven_requires_tp1":     True,   # Move stop to breakeven only after TP1 partial fill
}

# Strategy 6 defaults — mirrors PullbackVWAPConfig
PULLBACK_VWAP_DEFAULT_CONFIG: dict = {
    "initial_move_min_pct":        8.0,    # Initial move must be ≥8%
    "initial_move_lookback_bars":  96,     # 96 × 15m = 24h lookback for initial move
    "initial_move_rvol_min":       2.0,    # Initial move bar volume ≥ 2× 20-bar avg
    "pullback_threshold_pct":      0.5,    # Price within 0.5% of VWAP to qualify
    "volume_absorption_check":     True,   # Require pullback vol < initial move vol
    "absorption_vs_sma_max":       1.5,    # Pullback vol < SMA × this (secondary gate)
    "tp1_R":                       1.0,
    "tp2_R":                       2.0,
    "tp1_partial_pct":             0.6,
    "max_bars_in_trade":           12,
    "atr_stop_mult":               0,    # Stop = pullback bar low − ATR × this
    "rsi_period":                  14,
    "atr_period":                  14,
    "volume_sma_period":           20,
    "anchored_vwap_lookback":      20,
    # Shared exit config (required by check_exits)
    "invalidation_vwap_atr_mult":  10.0,  # Effectively disabled for this strategy
    "invalidation_rsi_candles":    4,
    "min_stop_pct":                5.0,
}

# Strategy mean_reversion defaults — mirrors MeanReversionConfig
MEANREV_DEFAULT_CONFIG: dict = {
    # Bollinger Bands
    "lookback_period":             20,     # BB SMA period
    "std_dev_multiplier":          2.0,    # BB band width (σ)
    # RSI
    "rsi_period":                  14,
    "rsi_oversold_threshold":      40.0,   # Mirrors MeanReversionConfig (Task B iter)
    "rsi_overbought_threshold":    75.0,
    # A+ filters (mirrors MeanReversionConfig)
    "adx_max_threshold":           30.0,   # Ranging market: ADX < threshold
    "atr_min_ratio":               0.8,    # Min ATR as fraction of avg ATR
    # Stop-loss
    "atr_stop_mult":               1.5,    # Min stop = ATR × this
    "atr_period":                  14,
    "stop_buffer_atr":             0.15,   # Extra buffer below lower band (in ATR)
    # Take-profit
    "tp1_R":                       1.0,
    "tp2_R":                       2.0,
    "tp1_partial_pct":             0.6,
    # Time management
    "max_bars_in_trade":           12,
    # Shared exit config (required by check_exits)
    "anchored_vwap_lookback":      20,
    "invalidation_vwap_atr_mult":  10.0,  # Effectively disabled — not applicable to meanrev
    "invalidation_rsi_candles":    4,
    # Failed-recovery invalidation: only exit if RSI recovered above recovery level then dropped
    # back below floor. Prevents exiting while still oversold (entry thesis still active).
    "invalidation_rsi_requires_recovery": True,
    "invalidation_rsi_recovery_level":    45.0,
    "invalidation_rsi_long_floor":        40.0,
    "min_stop_pct":                5.0,
    "long_only":                   True,   # Mirrors MeanReversionConfig; live bot is long-only by design
}

# Strategy I1 — Bull Flag + Momentum Pullback (mirrors BullFlagConfig + shared exits)
BULL_FLAG_DEFAULT_CONFIG: dict = {
    "interval":                    "5m",
    "pole_min_pct":                5.0,
    "pole_max_candles":            10,
    "pole_volume_multiplier":      3.0,
    "flag_min_candles":            3,
    "flag_max_candles":            8,
    "flag_max_retracement":        0.5,
    "entry_volume_multiplier":     1.0,
    "rsi_overbought":              75.0,
    "macd_fast":                   12,
    "macd_slow":                   26,
    "macd_signal":                 9,
    "ema_fast":                    9,
    "ema_slow":                    20,
    "rsi_period":                  14,
    "volume_sma_period":           20,
    "vwap_touch_pct":              0.5,
    "mild_initial_vol_lookback":   15,
    "confidence_base_mild":        0.40,
    "confidence_base_strong":      0.60,
    "multi_tf_bonus":              0.15,
    "atr_period":                  14,
    "notional_risk_pct":           2.0,
    "max_bars_in_trade":           18,
    "tp1_R":                       1.0,
    "tp2_R":                       2.0,
    "tp1_partial_pct":             0.6,
    "anchored_vwap_lookback":      20,
    "invalidation_vwap_atr_mult":  10.0,
    "invalidation_rsi_candles":    4,
    "min_stop_pct":                5.0,
}


def bull_flag_backtest_cfg(strategy: str) -> dict:
    """Return merged bull-flag dict for a named instance (1m / 5m / 1h)."""
    c = BULL_FLAG_DEFAULT_CONFIG.copy()
    if strategy == "bull_flag_1m":
        c["interval"] = "1m"
        c["max_bars_in_trade"] = 6
    elif strategy == "bull_flag_5m":
        c["interval"] = "5m"
        c["max_bars_in_trade"] = 18
    elif strategy == "bull_flag_1h":
        c["interval"] = "1h"
        c["max_bars_in_trade"] = 8
    return c


def swing_bull_flag_backtest_cfg() -> dict:
    """W2 — Swing Bull Flag on 4h (mirrors BullFlagConfig + config_swing preset)."""
    from research.strategies.bull_flag.config_swing import SWING_BULL_FLAG_BACKTEST_OVERRIDES

    c = BULL_FLAG_DEFAULT_CONFIG.copy()
    c.update(SWING_BULL_FLAG_BACKTEST_OVERRIDES)
    return c


# Strategy volatility_breakout defaults — mirrors VolatilityBreakoutConfig
VOLATILITY_BREAKOUT_DEFAULT_CONFIG: dict = {
    # Compression detection
    "squeeze_lookback_N":           100,   # bars to assess percentile over
    "squeeze_percentile":           10.0,  # bottom 10% BB-width = compressed
    "vol_compress_mult":             0.9,  # volume ≤ 0.9× SMA during squeeze
    "atr_compress_threshold":        0.7,  # ATR ratio ≤ 0.7 during squeeze
    # Breakout detection
    "vol_breakout_mult":             1.5,  # volume ≥ 1.5× 5-bar SMA on breakout bar
    "breakout_body_pct":             0.55, # breakout candle body ≥ 55% of range
    "breakout_close_position":       0.7,  # close in top 30% of range (≥ 0.7)
    "volume_sma_period":             5,    # 5-bar SMA for volume comparison
    # Stop / take-profit
    "retest_buffer_ATR":             0.15, # stop buffer below bar low (ATR units)
    "tp1_R":                         1.0,
    "tp2_R":                         2.0,
    "tp1_partial_pct":               0.6,
    "atr_period":                    14,
    "bb_period":                     20,
    "bb_std_dev":                    2.0,
    "breakout_range_bars":           10,  # bars defining the compression box ceiling/floor
    # Shared exit config (required by check_exits)
    "rsi_period":                    14,
    "max_bars_in_trade":             12,
    "anchored_vwap_lookback":        20,
    "invalidation_vwap_atr_mult":    10.0, # effectively disabled
    "invalidation_rsi_candles":      4,
    "min_stop_pct":                  5.0,
    # BTC macro (pipeline uses Binance 1d XBT/USD; live uses Kraken daily in strategy)
    "require_btc_bull_market":       True,
    "btc_ema_period":                200,
    "long_only":                     True,   # Mirrors VolatilityBreakoutConfig; live bot is long-only by design
}


def _merge_vb_btc_bull_env_into_cfg(cfg: dict) -> None:
    raw = os.environ.get("VB_REQUIRE_BTC_BULL")
    if raw is not None:
        cfg["require_btc_bull_market"] = raw.strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )


def _merge_htf_btc_bull_env_into_cfg(cfg: dict) -> None:
    raw = os.environ.get("HTF_REQUIRE_BTC_BULL")
    if raw is not None:
        cfg["require_btc_bull_market"] = raw.strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )


# Strategy htf_trend defaults — mirrors HTFTrendConfig
HTF_TREND_DEFAULT_CONFIG: dict = {
    # Trend filter (EMA200 on 1h bars as HTF proxy — approximate 4h EMA50)
    "htf_ema_period":               200,
    # Pullback zone (1h EMAs)
    "etf_ema_fast":                  20,   # EMA20: pullback target
    "etf_ema_slow":                  50,   # EMA50: trend support floor
    "pullback_max_ATR":               1.5, # max distance from EMA20 (ATR units)
    "break_bps":                     50.0, # max close below EMA50 before invalidating (bps)
    # Entry confirmation
    "reversal_body_pct":              0.5, # body ≥ 50% of candle range
    "reversal_close_position":        0.7, # close in top 30% of range (long)
    # Stop / take-profit
    "atr_stop_mult":                  1.5,
    "swing_lookback_bars":            3,
    "swing_buffer_ATR":               0.15,
    "tp1_R":                          1.0,
    "tp2_R":                          2.0,
    "tp1_partial_pct":                0.6,
    "atr_period":                     14,
    # Shared exit config (required by check_exits)
    "rsi_period":                     14,
    "max_bars_in_trade":              24,   # 24 bars (24h at 1h interval)
    "anchored_vwap_lookback":         20,
    "invalidation_vwap_atr_mult":     10.0, # effectively disabled
    "invalidation_rsi_candles":       6,    # min bars held before RSI invalidation can fire
    "invalidation_rsi_long_floor":    35,   # longs exit when rsi < this (was 40)
    "min_stop_pct":                   5.0,
    # BTC macro bull gate (daily close > BTC EMA(btc_ema_period))
    "require_btc_bull_market":        True,
    "btc_ema_period":                 200,
    "long_only":                      True,   # Mirrors HTFTrendConfig; live bot is long-only by design
}

# ─── Pipeline grader constants (mirrors pipeline.py — no Redis required) ──────

HARD_FLOOR_VOLUME_USD  = 100_000         # $100K 24h volume absolute minimum
S1_MAX_SUPPLY          = 5_000_000_000   # 5B tokens
S2_MIN_PRICE           = 0.005
S2_MAX_PRICE           = 10.0
S3_MIN_ACTIVE_DAYS     = 20
S3_LOOKBACK_DAYS       = 30
D1_MIN_RVOL            = 3.0             # 3× 30-day average
D2_MIN_24H_PCT         = 8.0             # +8% in 24h
D2_MIN_4H_PCT          = 5.0             # +5% in last 4h
D3_MIN_VOLUME          = 500_000         # $500K
D3_MAX_VOLUME          = 50_000_000      # $50M
D4_MAX_BTC_DROP        = -4.0            # BTC not down > 4% in 4h

PIPELINE_DEFAULT_INTERVAL = "1h"        # Pipeline default; OHLC depth from Binance
PIPELINE_RVOL_DAYS        = 30           # Target RVOL baseline (days); capped adaptively

GRADE_ORDER             = {"A+": 4, "A": 3, "B": 2, "C": 1, "F": 0}
GRADE_SIZE_FACTOR       = {"A+": 1.0, "A": 1.0, "B": 0.5,  "C": 0.0, "F": 0.0}
GRADE_SIZE_FACTOR_RELAX = {"A+": 1.0, "A": 1.0, "B": 0.5,  "C": 0.25, "F": 0.0}

# Relaxed thresholds for --relax-pillars (small paper account, low-volume pairs)
RELAX_HARD_FLOOR = 10_000     # $10K (vs $100K) — sufficient for $5 paper positions
RELAX_D1_MIN_RVOL = 1.5      # 1.5× (vs 3.0×)
RELAX_D3_MIN_VOLUME = 100_000 # $100K (vs $500K)
RELAX_D3_MAX_VOLUME = 100_000_000  # $100M (vs $50M)

BTC_KRAKEN = "XBT/USD"                   # Kraken's canonical BTC symbol

# Approximate circulating supplies (mid-2025). Fail-open for unknowns.
_SUPPLY_TABLE: dict[str, float] = {
    "SNX/USD":     332_000_000,
    "AXS/USD":      68_000_000,
    "HNT/USD":     162_000_000,
    "ORCA/USD":    100_000_000,
    "BLUR/USD":  1_590_000_000,
    "CTC/USD":     200_000_000,
    "SOL/USD":     465_000_000,
    "AVAX/USD":    400_000_000,
    "LINK/USD":    600_000_000,
    "DOT/USD":   1_400_000_000,
    "NEAR/USD":    900_000_000,
    "INJ/USD":     100_000_000,
    "GRT/USD":  10_800_000_000,   # > 5B → S1 FAIL
    "OCEAN/USD":   613_000_000,
    "LRC/USD":   1_374_000_000,
    "ENJ/USD":   1_000_000_000,
    "MANA/USD":  1_893_000_000,
    "SAND/USD":  2_400_000_000,
    "CHR/USD":     674_000_000,
    "BTC/USD":      19_700_000,
    "XBT/USD":      19_700_000,
    "ETH/USD":     120_000_000,
    "ADA/USD":  35_000_000_000,   # > 5B → S1 FAIL
    "XRP/USD":  54_000_000_000,   # > 5B → S1 FAIL
    "DOGE/USD": 144_000_000_000,  # > 5B → S1 FAIL
    "MATIC/USD":  9_300_000_000,  # > 5B → S1 FAIL
}


def _get_supply(symbol: str, overrides: dict[str, float]) -> Optional[float]:
    """Return circulating supply; None means unknown (grader will fail-open)."""
    if symbol in overrides:
        return overrides[symbol]
    return _SUPPLY_TABLE.get(symbol)


def fetch_kraken_usd_universe() -> list[str]:
    """
    Fetch all online spot USD pairs from the Kraken AssetPairs endpoint.
    Returns pairs in SNX/USD format, sorted alphabetically.
    Excludes stablecoins and wrapped tokens (see _SKIP_BASE_TOKENS).
    """
    req = urllib.request.Request(
        KRAKEN_ASSET_PAIRS_URL,
        headers={"User-Agent": "crypto-backtester/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    if data.get("error"):
        raise RuntimeError(f"Kraken AssetPairs error: {data['error']}")

    seen: set[str] = set()
    pairs: list[str] = []
    for pair_info in data["result"].values():
        wsname: str = pair_info.get("wsname", "")
        if not wsname.endswith("/USD"):
            continue
        if pair_info.get("status", "") != "online":
            continue
        base = wsname.split("/")[0]
        if base in _SKIP_BASE_TOKENS:
            continue
        if wsname not in seen:
            seen.add(wsname)
            pairs.append(wsname)

    return sorted(pairs)


# ─── Data types ───────────────────────────────────────────────────────────────

@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Trade:
    trade_num: int
    side: str                        # "long" | "short"
    entry_bar: int
    entry_time: datetime
    entry_price: float
    stop_loss: float
    tp1_price: float
    tp2_price: float
    qty: float                       # Full position quantity
    qty_remaining: float             # Remaining after TP1 partial exit
    tp1_hit: bool = False
    rsi_recovery_seen: bool = False  # RSI crossed recovery level during trade (failed-recovery invalidation)
    breakeven_stop: Optional[float] = None
    exit_bar: Optional[int] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_usd: float = 0.0
    bars_held: int = 0
    symbol: str = ""                 # pipeline mode only
    grade: str = ""                  # pipeline mode only
    # Entry-time factor snapshot (CSV logging only; empty when unavailable)
    entry_rvol: Optional[float] = None
    entry_change_24h_pct: Optional[float] = None
    entry_change_4h_pct: Optional[float] = None
    entry_volume_24h_usd: Optional[float] = None
    entry_market_cap_usd: Optional[float] = None
    entry_float_turnover: Optional[float] = None
    entry_btc_4h_change_pct: Optional[float] = None
    entry_supply_pct: Optional[float] = None
    entry_vwap_distance_pct: Optional[float] = None
    entry_htf_rsi: Optional[float] = None
    entry_atr_pct: Optional[float] = None
    entry_utc_hour: Optional[int] = None
    entry_day_of_week: Optional[int] = None
    entry_year_month: Optional[str] = None


# ─── OHLCV Fetcher (Binance klines → USDT bars; universe symbols stay Kraken) ─

def _symbol_to_kraken_pair(symbol: str) -> str:
    """'SNX/USD' → 'SNXUSD'"""
    return symbol.replace("/", "")


def kraken_usd_to_binance_symbol(kraken_pair: str) -> str:
    """'XBT/USD' → BTCUSDT, 'SNX/USD' → SNXUSDT."""
    pair = kraken_pair.strip().upper()
    if "/" not in pair:
        raise ValueError(f"Expected Kraken pair like XXX/USD, got {kraken_pair!r}")
    base, quote = pair.split("/", 1)
    quote = quote.strip()
    base = base.strip().upper()
    if quote != "USD":
        raise ValueError(f"Only USD-quote Kraken pairs supported, got {kraken_pair!r}")
    b = _KRAKEN_BASE_ASSET_ALIAS.get(base, base)
    return f"{b}USDT"


def _urllib_is_connection_refused(err: urllib.error.URLError) -> bool:
    r = getattr(err, "reason", None)
    return isinstance(r, OSError) and r.errno == errno.ECONNREFUSED


def _unpack_binance_cache_payload(payload: Any) -> list:
    """Cache may be bare list rows or {\"rows\": [...]}."""
    if isinstance(payload, dict) and "rows" in payload:
        return payload["rows"]
    if isinstance(payload, list):
        return payload
    raise ValueError("Invalid Binance OHLC cache file shape")


def _parse_binance_kline_rows(rows: list) -> list[Bar]:
    """Binance kline row: openTime(ms), open, high, low, close, volume, ..."""
    return [
        Bar(
            timestamp=datetime.fromtimestamp(row[0] / 1000.0, tz=timezone.utc),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in rows
    ]


def _dedupe_sort_raw_klines(rows: list) -> list:
    """Rows are Binance kline arrays indexed by ms openTime."""
    by_ts: dict[int, list] = {}
    for row in rows:
        by_ts[int(row[0])] = row
    return [by_ts[k] for k in sorted(by_ts)]


def _binance_rest_get_json(path: str, query: dict[str, str]) -> Any:
    """GET JSON from locked Binance host; first success locks host to .com or .us."""
    global _BINANCE_LOCKED_BASE, _BINANCE_COM_SKIP

    qs = urllib.parse.urlencode(query)
    last_err: Optional[BaseException] = None

    if _BINANCE_LOCKED_BASE:
        bases = [_BINANCE_LOCKED_BASE]
    elif _BINANCE_COM_SKIP:
        bases = [BINANCE_REST_US]
    else:
        bases = [BINANCE_REST_COM, BINANCE_REST_US]

    for base_url in bases:
        url = f"{base_url}{path}?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": "crypto-backtester/1.2"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            if _BINANCE_LOCKED_BASE is None:
                _BINANCE_LOCKED_BASE = base_url
                if base_url != BINANCE_REST_COM:
                    print(f"  [binance] Using {_BINANCE_LOCKED_BASE}")
            return json.loads(raw)

        except urllib.error.HTTPError as e:
            last_err = e
            body = ""
            try:
                body = (e.fp.read().decode(errors="ignore")[:200]).replace("\n", " ")
            except Exception:
                pass

            fallback_ok = (
                base_url == BINANCE_REST_COM
                and _BINANCE_LOCKED_BASE is None
                and e.code == 451
            )
            if fallback_ok:
                _BINANCE_COM_SKIP = True
                print(
                    "  [binance] api.binance.com returned HTTP 451; "
                    "falling back to api.binance.us (skipping .com for later requests)"
                )
                continue

            msg = getattr(e, "reason", "") or ""
            hint = " " + body if body else ""
            raise RuntimeError(f"Binance HTTP {e.code} {msg!s}{hint}") from None

        except urllib.error.URLError as e:
            last_err = e
            if (
                base_url == BINANCE_REST_COM
                and _BINANCE_LOCKED_BASE is None
                and _urllib_is_connection_refused(e)
            ):
                _BINANCE_COM_SKIP = True
                print(
                    "  [binance] Connection refused on api.binance.com; "
                    "falling back to api.binance.us (skipping .com for later requests)"
                )
                continue
            raise RuntimeError(f"Binance URLError: {e}") from e

    raise RuntimeError(f"Binance request failed after fallbacks: {last_err}") from None


def _download_binance_klines_backwards_raw(
    binance_symbol: str, interval_label: str, cutoff_ms: int, now_ms: int
) -> list:
    """Paginate backwards with endTime/limit until data reaches cutoff or API runs out."""
    all_rows: list = []
    end_ms = now_ms

    while True:
        page = _binance_rest_get_json(
            BINANCE_KLINES_PATH,
            {
                "symbol": binance_symbol,
                "interval": interval_label,
                "limit": str(BINANCE_PAGE_LIMIT),
                "endTime": str(end_ms),
            },
        )
        if not isinstance(page, list) or len(page) == 0:
            break

        all_rows.extend(page)
        first_open = int(page[0][0])

        reached_cutoff = first_open <= cutoff_ms
        exhausted = len(page) < BINANCE_PAGE_LIMIT

        if reached_cutoff or exhausted:
            break

        next_end = first_open - 1
        if next_end >= end_ms:
            break
        end_ms = next_end
        time.sleep(0.08)

    return _dedupe_sort_raw_klines(all_rows)


def fetch_binance_ohlcv(
    kraken_symbol: str,
    interval_label: str,
    span_days: float,
    no_cache: bool = False,
) -> tuple[list[Bar], str]:
    """
    Load OHLCV for a Kraken-style pair symbol using Binance klines (paginated backwards).

    Caches maximal downloaded history under backtest_cache/{PAIR}_{interval}_full.json.
    Returns (bars in [now - span_days, now], human-readable OHLC mapping note).
    """
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_cache")
    os.makedirs(cache_dir, exist_ok=True)

    pair_slug = _symbol_to_kraken_pair(kraken_symbol)
    cache_file = os.path.join(cache_dir, f"{pair_slug}_{interval_label}_full.json")

    cutoff = datetime.now(timezone.utc) - timedelta(days=float(span_days))
    cutoff_ms = int(cutoff.timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    try:
        binance_symbol = kraken_usd_to_binance_symbol(kraken_symbol)
    except ValueError:
        raise

    hint = f"{kraken_symbol} → Binance `{binance_symbol}` ({interval_label})"

    cached_rows: Optional[list] = None
    if not no_cache and os.path.exists(cache_file):
        with open(cache_file) as f:
            cached_rows = _unpack_binance_cache_payload(json.load(f))

    if cached_rows is not None:
        raw_full = cached_rows
    else:
        print(f"  Fetching OHLC from Binance: {hint}…")
        raw_full = _download_binance_klines_backwards_raw(
            binance_symbol, interval_label, cutoff_ms, now_ms
        )

        envelope = {
            "version": 1,
            "kraken_wsname": kraken_symbol,
            "binance_symbol": binance_symbol,
            "interval": interval_label,
            "first_open_ms": raw_full[0][0] if raw_full else None,
            "last_open_ms": raw_full[-1][0] if raw_full else None,
            "rows": raw_full,
        }
        with open(cache_file, "w") as f:
            json.dump(envelope, f)
        print(f"  Wrote Binance OHLC cache: {cache_file} ({len(raw_full)} bars)")

    bars_all = _parse_binance_kline_rows(raw_full)

    interval_min = INTERVAL_MAP[interval_label]
    slack_ms = interval_min * 60 * 1000 * 2
    did_extend = False
    if (
        cached_rows is not None
        and bars_all
        and int(bars_all[0].timestamp.timestamp() * 1000) > cutoff_ms + slack_ms
    ):
        did_extend = True
        print(
            f"  [cache refresh] Oldest bar {bars_all[0].timestamp.date()} is after window start "
            f"→ re-fetch for {hint}"
        )
        print(f"  Fetching OHLC from Binance (extend history): {hint}…")
        raw_full = _download_binance_klines_backwards_raw(
            binance_symbol, interval_label, cutoff_ms, now_ms
        )
        envelope = {
            "version": 1,
            "kraken_wsname": kraken_symbol,
            "binance_symbol": binance_symbol,
            "interval": interval_label,
            "first_open_ms": raw_full[0][0] if raw_full else None,
            "last_open_ms": raw_full[-1][0] if raw_full else None,
            "rows": raw_full,
        }
        with open(cache_file, "w") as f:
            json.dump(envelope, f)
        print(f"  Updated Binance OHLC cache ({len(raw_full)} bars)")
        bars_all = _parse_binance_kline_rows(raw_full)

    utc_now = datetime.now(timezone.utc)
    trimmed = [b for b in bars_all if cutoff <= b.timestamp <= utc_now]

    src = "[cache]" if (cached_rows is not None and not did_extend) else "[network]"
    if trimmed:
        print(
            f"  {src} Using {len(trimmed)} bars in window "
            f"({trimmed[0].timestamp.date()} → {trimmed[-1].timestamp.date()})"
        )
    else:
        print(f"  {src} No bars in requested window.")

    return trimmed, hint


def fetch_binance_ohlcv_for_backtest(
    kraken_symbol: str,
    interval_label: str,
    span_days: float,
    no_cache: bool = False,
) -> list[Bar]:
    """Convenience: only bar list."""
    bars, _ = fetch_binance_ohlcv(
        kraken_symbol, interval_label, span_days, no_cache=no_cache
    )
    return bars


# ─── Indicator helpers ────────────────────────────────────────────────────────

def _compute_vwap(bars: list[Bar], lookback: int) -> tuple[Optional[float], Optional[float]]:
    """Session VWAP + anchored VWAP.  Identical logic to strategy._calculate_vwap_values."""
    typical = [(b.high + b.low + b.close) / 3.0 for b in bars]
    volumes = [b.volume for b in bars]

    session_vwap = calculate_vwap(typical, volumes, anchor_index=None)

    bar_dicts = [{"high": b.high, "low": b.low, "close": b.close} for b in bars]
    swing = detect_swing_highs_lows(bar_dicts, lookback=max(2, lookback // 4))

    if swing["low_indices"]:
        anchor = swing["low_indices"][-1]
    elif swing["high_indices"]:
        anchor = swing["high_indices"][-1]
    else:
        anchor = max(0, len(bars) - lookback)

    anchored_vwap = calculate_vwap(typical, volumes, anchor_index=anchor)
    return session_vwap, anchored_vwap


def _reversal_confirmed(bar: Bar, vwap: float, side: str, cfg: dict) -> bool:
    """Mirror of strategy._check_reversal_confirmation."""
    body = abs(bar.close - bar.open)
    rng = bar.high - bar.low

    if side == "buy":
        if bar.close > vwap:
            return True
        if rng > 0:
            return (
                body / rng >= cfg["reversal_body_pct"] and
                (bar.close - bar.low) / rng >= (1.0 - cfg["reversal_close_position"])
            )
    else:
        if bar.close < vwap:
            return True
        if rng > 0:
            return (
                body / rng >= cfg["reversal_body_pct"] and
                (bar.close - bar.low) / rng <= cfg["reversal_close_position"]
            )
    return False


def _momentum_excluded(bars: list[Bar], side: str, cfg: dict) -> bool:
    """True if last N bars all large-bodied in the same direction (knife-catch exclusion)."""
    n = cfg["momentum_exclusion_bars"]
    if len(bars) < n:
        return False

    all_bullish = all_bearish = True
    for b in bars[-n:]:
        rng = b.high - b.low
        body = abs(b.close - b.open)
        if rng == 0 or body / rng < cfg["momentum_body_pct_threshold"]:
            return False  # Not all large-bodied
        if b.close <= b.open:
            all_bullish = False
        if b.close >= b.open:
            all_bearish = False

    return (side == "buy" and all_bearish) or (side == "sell" and all_bullish)


def _compute_stop_and_targets(
    entry: float, side: str, bars: list[Bar], atr: float, cfg: dict
) -> dict:
    """Mirror of strategy._calculate_stop_and_targets."""
    bar_dicts = [{"high": b.high, "low": b.low, "close": b.close} for b in bars]
    swing = detect_swing_highs_lows(bar_dicts, lookback=cfg["swing_lookback_bars"])

    if side == "buy":
        swing_stop = min(swing["lows"]) if swing["lows"] else entry * 0.95
        atr_stop   = entry - atr * cfg["atr_stop_mult"]
        stop       = min(swing_stop, atr_stop) - atr * cfg["stop_buffer_atr"]
        risk       = entry - stop
        tp1        = entry + risk * cfg["tp1_R"]
        tp2        = entry + risk * cfg["tp2_R"]
    else:
        swing_stop = max(swing["highs"]) if swing["highs"] else entry * 1.05
        atr_stop   = entry + atr * cfg["atr_stop_mult"]
        stop       = max(swing_stop, atr_stop) + atr * cfg["stop_buffer_atr"]
        risk       = stop - entry
        tp1        = entry - risk * cfg["tp1_R"]
        tp2        = entry - risk * cfg["tp2_R"]

    return {"stop": stop, "tp1": tp1, "tp2": tp2, "risk": risk}


# ─── Signal detection ─────────────────────────────────────────────────────────

def _vwap_htf_rsi_at(
    htf_bars: Optional[list[Bar]], rsi_period: int, as_of: datetime,
) -> Optional[float]:
    """RSI on HTF closes ending at ``as_of`` (inclusive). None if insufficient data."""
    if not htf_bars:
        return None
    sub = [b for b in htf_bars if b.timestamp <= as_of]
    need = rsi_period + 5
    if len(sub) < need:
        return None
    closes = [b.close for b in sub]
    return calculate_rsi(closes, period=rsi_period)


def check_entry_signal(
    bars: list[Bar], cfg: dict, equity: float, risk_pct: float, long_only: bool,
    debug: bool = False,
    htf_bars: Optional[list[Bar]] = None,
) -> Optional[Trade]:
    """
    Evaluate the most-recent bar for a VWAP mean-reversion entry signal.

    Faithfully ports generate_signals() core conditions.
    Skipped (require live data): HTF regime filter, 1m green candle, VWAP slope guard.
    Optional ``htf_bars``: when ``cfg['htf_rsi_long_max']`` is set, used for HTF RSI gate.
    """
    if len(bars) < WARMUP_BARS:
        return None

    closes  = [b.close  for b in bars]
    highs   = [b.high   for b in bars]
    lows    = [b.low    for b in bars]
    volumes = [b.volume for b in bars]

    session_vwap, anchored_vwap = _compute_vwap(bars, cfg["anchored_vwap_lookback"])
    if session_vwap is None:
        return None
    vwap = anchored_vwap if anchored_vwap else session_vwap

    rsi = calculate_rsi(closes, period=cfg["rsi_period"])
    if rsi is None:
        return None

    atr = calculate_atr(highs, lows, closes, period=cfg["atr_period"])
    if not atr:
        return None

    vol_sma = sum(volumes[-cfg["volume_sma_period"]:]) / cfg["volume_sma_period"]
    vol_ratio = volumes[-1] / vol_sma if vol_sma > 0 else 1.0
    long_min = cfg.get("long_min_volume_ratio")
    vol_mode = cfg.get("volume_filter_mode", "conservative")
    if vol_mode == "conservative" and vol_ratio > cfg["volume_max_mult"]:
        skip_high_vol_cap = long_min is not None
        if not skip_high_vol_cap:
            if debug:
                print(
                    f"REJECT {bars[-1].timestamp.strftime('%Y-%m-%d %H:%M')} | "
                    f"VOL_HIGH={vol_ratio:.2f}x(max={cfg['volume_max_mult']}x)"
                )
            return None

    price = bars[-1].close
    bar   = bars[-1]

    htf_rsi_val: Optional[float] = None
    htf_max = cfg.get("htf_rsi_long_max")
    if htf_max is not None:
        htf_rsi_val = _vwap_htf_rsi_at(htf_bars, cfg["rsi_period"], bar.timestamp)

    long_vol_ok = long_min is None or vol_ratio >= long_min
    htf_long_ok = htf_max is None or (
        htf_rsi_val is not None and htf_rsi_val <= htf_max
    )

    def _build_trade(side: str, entry: float, levels: dict) -> Optional[Trade]:
        risk = levels["risk"]
        if risk <= 0:
            return None
        risk_dollars = equity * risk_pct / 100.0
        qty = risk_dollars / risk
        return Trade(
            trade_num=0,
            side=side,
            entry_bar=len(bars) - 1,
            entry_time=bar.timestamp,
            entry_price=entry,
            stop_loss=levels["stop"],
            tp1_price=levels["tp1"],
            tp2_price=levels["tp2"],
            qty=qty,
            qty_remaining=qty,
        )

    # ── LONG ──
    dev_long  = (vwap - price) / vwap * 100.0 if vwap > 0 else 0.0
    long_dev  = dev_long >= cfg["dev_threshold_pct"]
    long_rsi  = rsi <= cfg["rsi_oversold"]
    long_rev  = _reversal_confirmed(bar, vwap, "buy", cfg)
    long_mom  = not _momentum_excluded(bars, "buy", cfg)

    if debug and not (
        long_dev and long_rsi and long_rev and long_mom and long_vol_ok and htf_long_ok
    ):
        ts = bar.timestamp.strftime("%Y-%m-%d %H:%M")
        reasons: list[str] = []
        if not long_dev:
            reasons.append(f"DEV={dev_long:.2f}%(need>={cfg['dev_threshold_pct']})")
        if not long_rsi:
            reasons.append(f"RSI={rsi:.1f}(need<={cfg['rsi_oversold']})")
        if not long_rev:
            reasons.append("REVERSAL=fail")
        if not long_mom:
            reasons.append("MOMENTUM_EXCLUDED")
        if not long_vol_ok:
            if long_min is not None:
                reasons.append(f"VOL_SPIKE={vol_ratio:.2f}x(need>={long_min}x)")
            else:
                reasons.append("VOL_LONG=fail")
        if not htf_long_ok:
            if htf_rsi_val is not None:
                reasons.append(f"HTF_RSI={htf_rsi_val:.1f}(need<={htf_max})")
            else:
                reasons.append("HTF_RSI=unavailable")
        print(f"REJECT {ts} | {' | '.join(reasons)}")

    if (
        long_dev
        and long_rsi
        and long_rev
        and long_mom
        and long_vol_ok
        and htf_long_ok
    ):
        entry  = min(price, vwap + atr * 0.05)
        levels = _compute_stop_and_targets(entry, "buy", bars, atr, cfg)
        return _build_trade("long", entry, levels)

    # ── SHORT ── (gated by strategy config; live bot is long-only by default)
    if cfg.get("long_only", True):
        return None

    dev_short = (price - vwap) / vwap * 100.0 if vwap > 0 else 0.0
    if (
        dev_short >= cfg["dev_threshold_pct"] and
        rsi >= cfg["rsi_overbought"] and
        _reversal_confirmed(bar, vwap, "sell", cfg) and
        not _momentum_excluded(bars, "sell", cfg)
    ):
        entry  = max(price, vwap - atr * 0.05)
        levels = _compute_stop_and_targets(entry, "sell", bars, atr, cfg)
        return _build_trade("short", entry, levels)

    return None


# ─── Pullback to VWAP signal detection ───────────────────────────────────────

def _find_initial_move_bar(
    bars: list[Bar], cfg: dict
) -> tuple[Optional[int], Optional[float]]:
    """
    Scan backward for the most recent bar that represents an initial momentum move.

    Criteria:
    - Bar's close ≥ initial_move_min_pct% above close from initial_move_lookback_bars ago
    - That bar's volume ≥ initial_move_rvol_min × 20-bar volume SMA ending just before it

    Returns (bar_index, bar_volume) or (None, None) if not found.
    """
    lookback = min(cfg["initial_move_lookback_bars"], len(bars) - 1)
    vol_sma_period = cfg["volume_sma_period"]

    for offset in range(1, lookback + 1):
        idx = len(bars) - 1 - offset
        if idx < vol_sma_period + 1:
            break

        candidate = bars[idx]

        ref_offset = min(cfg["initial_move_lookback_bars"], idx)
        ref_idx = idx - ref_offset
        if ref_idx < 0:
            continue
        ref_close = bars[ref_idx].close
        if ref_close <= 0:
            continue

        move_pct = (candidate.close - ref_close) / ref_close * 100.0
        if move_pct < cfg["initial_move_min_pct"]:
            continue

        vol_window = bars[max(0, idx - vol_sma_period):idx]
        if len(vol_window) < vol_sma_period // 2:
            continue
        avg_vol = sum(b.volume for b in vol_window) / len(vol_window)
        if avg_vol <= 0:
            continue

        if candidate.volume / avg_vol >= cfg["initial_move_rvol_min"]:
            return idx, candidate.volume

    return None, None


def check_pullback_vwap_entry_signal(
    bars: list[Bar], cfg: dict, equity: float, risk_pct: float, debug: bool = False
) -> Optional[Trade]:
    """
    Evaluate the most-recent bar for a Pullback to VWAP entry signal.

    Logic:
    1. Find the initial 8%+ RVOL move in the lookback window
    2. Check current price is within 0.5% of VWAP
    3. Confirm current bar volume < initial move bar volume (absorption)
    4. Enter long with stop below current bar low

    Long only — never generates short signals.
    """
    if len(bars) < WARMUP_BARS:
        return None

    closes  = [b.close  for b in bars]
    highs   = [b.high   for b in bars]
    lows    = [b.low    for b in bars]
    volumes = [b.volume for b in bars]

    bar = bars[-1]

    # Find initial move
    move_idx, move_volume = _find_initial_move_bar(bars, cfg)
    if move_idx is None or move_volume is None:
        if debug:
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | NO_INITIAL_MOVE(need {cfg['initial_move_min_pct']}%+ with {cfg['initial_move_rvol_min']}x RVOL in last {cfg['initial_move_lookback_bars']} bars)")
        return None

    # Must enter on a bar AFTER the initial move
    if move_idx >= len(bars) - 1:
        if debug:
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | MOVE_TOO_RECENT(move_idx={move_idx}, current={len(bars)-1})")
        return None

    # VWAP
    session_vwap, anchored_vwap = _compute_vwap(bars, cfg["anchored_vwap_lookback"])
    if session_vwap is None:
        return None
    vwap = anchored_vwap if anchored_vwap else session_vwap

    # Pullback to VWAP check
    if vwap <= 0:
        return None
    deviation_pct = abs(bar.close - vwap) / vwap * 100.0
    if deviation_pct > cfg["pullback_threshold_pct"]:
        if debug:
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | DEV_FROM_VWAP={deviation_pct:.3f}%(need<={cfg['pullback_threshold_pct']})")
        return None

    # ATR
    atr = calculate_atr(highs, lows, closes, period=cfg["atr_period"])
    if not atr or atr == 0:
        return None

    # Volume absorption check
    if cfg["volume_absorption_check"]:
        vol_sma = sum(volumes[-cfg["volume_sma_period"]:]) / cfg["volume_sma_period"]
        if bar.volume >= move_volume:
            if debug:
                print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | VOL_NOT_ABSORBED(bar={bar.volume:.0f}>=move={move_volume:.0f})")
            return None
        if vol_sma > 0 and bar.volume >= vol_sma * cfg["absorption_vs_sma_max"]:
            if debug:
                mult = bar.volume / vol_sma
                print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | VOL_HIGH={mult:.2f}x(max={cfg['absorption_vs_sma_max']}x SMA)")
            return None

    # Entry and levels
    entry_price = bar.close
    stop_loss   = bar.low - atr * cfg["atr_stop_mult"]
    risk        = entry_price - stop_loss

    if risk <= 0 or risk / entry_price < 0.005:
        return None

    tp1 = entry_price + risk * cfg["tp1_R"]
    tp2 = entry_price + risk * cfg["tp2_R"]

    risk_dollars = equity * risk_pct / 100.0
    qty = risk_dollars / risk

    return Trade(
        trade_num=0,
        side="long",
        entry_bar=len(bars) - 1,
        entry_time=bar.timestamp,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp1_price=tp1,
        tp2_price=tp2,
        qty=qty,
        qty_remaining=qty,
    )


# ─── Bull Flag + Momentum Pullback (I1) ───────────────────────────────────────


def check_bull_flag_entry_signal(
    bars: list[Bar],
    cfg: dict,
    equity: float,
    risk_pct: float,
    debug: bool = False,
    *,
    daily_timestamps: Optional[list[Any]] = None,
    daily_closes: Optional[list[float]] = None,
    btc_closes: Optional[list[float]] = None,
    btc_4h_change_pct: Optional[float] = None,
) -> Optional[Trade]:
    """Long-only bull flag / mild pullback; mirrors research.strategies.bull_flag.strategy."""
    from research.strategies.bull_flag.config import BullFlagConfig
    from research.strategies.bull_flag.strategy import analyze_bull_flag_last_bar

    _names = {f.name for f in fields(BullFlagConfig)}
    bf_kw: dict = {"strategy_id": "backtest", "interval": cfg.get("interval", "5m")}
    for k, v in cfg.items():
        if k in _names and k != "strategy_id":
            bf_kw[k] = v
    if cfg.get("max_bars_in_trade") is not None:
        bf_kw["max_hold_candles"] = int(cfg["max_bars_in_trade"])
    bf = BullFlagConfig(**bf_kw)

    opens = [b.open for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    snap = analyze_bull_flag_last_bar(
        opens,
        highs,
        lows,
        closes,
        vols,
        bf,
        daily_timestamps=daily_timestamps,
        daily_closes=daily_closes,
        btc_closes=btc_closes,
        btc_4h_change_pct=btc_4h_change_pct,
        entry_timestamp=bars[-1].timestamp,
    )
    if snap is None:
        if debug:
            print(f"REJECT {bars[-1].timestamp} | bull_flag no_setup")
        return None

    bar = bars[-1]
    entry_price = bar.close
    stop_loss = snap.stop
    risk = entry_price - stop_loss
    if risk <= 0 or risk / entry_price < 0.005:
        return None

    tp1 = snap.tp1
    tp2 = snap.tp2
    risk_dollars = equity * risk_pct / 100.0
    qty = risk_dollars / risk

    return Trade(
        trade_num=0,
        side="long",
        entry_bar=len(bars) - 1,
        entry_time=bar.timestamp,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp1_price=tp1,
        tp2_price=tp2,
        qty=qty,
        qty_remaining=qty,
    )


# ─── Mean-reversion (BB + RSI + ADX) signal detection ────────────────────────

def _calc_bb_from_closes(closes: list[float], period: int, std_mult: float) -> tuple[float, float, float]:
    """Return (upper, middle, lower) Bollinger Bands for the last `period` bars."""
    recent = closes[-period:]
    sma = sum(recent) / len(recent)
    variance = sum((p - sma) ** 2 for p in recent) / len(recent)
    std = variance ** 0.5
    return sma + std_mult * std, sma, sma - std_mult * std


def check_meanrev_entry_signal(
    bars: list[Bar], cfg: dict, equity: float, risk_pct: float, debug: bool = False
) -> Optional[Trade]:
    """
    Evaluate the most-recent bar for a Mean-Reversion entry signal.

    Entry criteria (long only):
    - Price in bottom 20% of BB range (band_position < 0.2)
    - RSI < rsi_oversold_threshold (mirrors MeanReversionConfig, default 40)
    - ADX < adx_max_threshold (mirrors MeanReversionConfig, default 30)
    - ATR ratio >= atr_min_ratio (mirrors MeanReversionConfig, default 0.8)

    Stop: min(lower_band − atr × stop_buffer_atr, entry − atr × atr_stop_mult)
    TP1/TP2: entry + risk × tp1_R / tp2_R
    """
    min_bars = max(cfg["lookback_period"], 30) + cfg["atr_period"] + 5
    if len(bars) < min_bars:
        return None

    closes  = [b.close for b in bars]
    highs   = [b.high  for b in bars]
    lows    = [b.low   for b in bars]
    bar     = bars[-1]

    # Bollinger Bands
    upper, middle, lower = _calc_bb_from_closes(closes, cfg["lookback_period"], cfg["std_dev_multiplier"])
    band_range = upper - lower
    if band_range == 0:
        return None
    band_position = (bar.close - lower) / band_range

    # RSI
    rsi = calculate_rsi(closes, period=cfg["rsi_period"])
    if rsi is None:
        return None

    # ADX — ranging market filter (A+ condition)
    adx = calculate_adx(highs, lows, closes, period=14)

    # ATR ratio — market must be active (A+ condition)
    atr_ratio = calculate_atr_ratio(highs, lows, closes, atr_period=cfg["atr_period"], avg_period=20)

    # Evaluate all signal conditions
    bb_pass  = band_position < 0.2
    rsi_pass = rsi < cfg["rsi_oversold_threshold"]
    adx_pass = adx is None or adx < cfg["adx_max_threshold"]
    atr_pass = atr_ratio is None or atr_ratio >= cfg["atr_min_ratio"]

    if debug and not (bb_pass and rsi_pass and adx_pass and atr_pass):
        ts        = bar.timestamp.strftime("%Y-%m-%d %H:%M")
        adx_str   = f"{adx:.1f}"   if adx       is not None else "n/a"
        ratio_str = f"{atr_ratio:.2f}" if atr_ratio is not None else "n/a"
        reasons: list[str] = []
        if not bb_pass:
            reasons.append(f"BB_pos={band_position:.3f}(need<0.2)")
        if not rsi_pass:
            reasons.append(f"RSI={rsi:.1f}(need<{cfg['rsi_oversold_threshold']})")
        if not adx_pass:
            reasons.append(f"ADX={adx_str}(need<{cfg['adx_max_threshold']})")
        if not atr_pass:
            reasons.append(f"ATR_ratio={ratio_str}(need>={cfg['atr_min_ratio']})")
        print(f"REJECT {ts} | {' | '.join(reasons)}")

    if not (bb_pass and rsi_pass and adx_pass and atr_pass):
        return None

    # ATR for stop sizing
    atr = calculate_atr(highs, lows, closes, period=cfg["atr_period"])
    if not atr or atr == 0:
        return None

    entry_price = bar.close

    # Stop: below lower band with buffer; at least atr_stop_mult away from entry
    stop_below_band  = lower - atr * cfg["stop_buffer_atr"]
    min_stop         = entry_price - atr * cfg["atr_stop_mult"]
    stop_loss        = min(stop_below_band, min_stop)
    risk             = entry_price - stop_loss

    if risk <= 0 or risk / entry_price < 0.005:
        return None

    tp1 = entry_price + risk * cfg["tp1_R"]
    tp2 = entry_price + risk * cfg["tp2_R"]

    risk_dollars = equity * risk_pct / 100.0
    qty = risk_dollars / risk

    return Trade(
        trade_num=0,
        side="long",
        entry_bar=len(bars) - 1,
        entry_time=bar.timestamp,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp1_price=tp1,
        tp2_price=tp2,
        qty=qty,
        qty_remaining=qty,
    )


# ─── Volatility Breakout signal detection ────────────────────────────────────

def check_volatility_breakout_entry_signal(
    bars: list[Bar],
    cfg: dict,
    equity: float,
    risk_pct: float,
    debug: bool = False,
    vb_btc_daily_bars: Optional[list[Bar]] = None,
) -> Optional[Trade]:
    """
    Volatility compression → breakout entry signal (long only).

    1. Compression: BB Width (normalized) in bottom squeeze_percentile% over lookback.
    2. Breakout: current bar closes above the highest high of the compression range.
    3. Volume spike: bar volume ≥ vol_breakout_mult × 5-bar SMA.
    4. Candle quality: body ≥ breakout_body_pct, close in top (1−breakout_close_position) of range.

    Stop: bar.low − ATR × retest_buffer_ATR.
    TP1/TP2: entry + risk × tp1_R / tp2_R.
    """
    squeeze_n  = cfg["squeeze_lookback_N"]
    bb_period  = cfg["bb_period"]
    bb_std_dev = cfg["bb_std_dev"]
    min_bars   = squeeze_n + bb_period + cfg["atr_period"] + 5
    if len(bars) < min_bars:
        return None

    closes  = [b.close  for b in bars]
    highs   = [b.high   for b in bars]
    lows    = [b.low    for b in bars]
    volumes = [b.volume for b in bars]
    bar     = bars[-1]

    # ── BB widths for all lookback bars (excluding current) ────────────────
    # Checking current bar's BB width AND asking it to break out is contradictory:
    # if the current bar is tight (low BB), it can't be breaking out simultaneously.
    # Correct approach: check whether the RECENT box_n bars were compressed, then
    # see if the CURRENT bar breaks above that compressed box.
    box_n     = cfg["breakout_range_bars"]
    start_idx = len(bars) - squeeze_n
    bw_all: list[float] = []
    for i in range(start_idx, len(bars) - 1):   # all lookback bars, excluding current
        if i < bb_period:
            continue
        u, m, l = _calc_bb_from_closes(closes[:i + 1], bb_period, bb_std_dev)
        if m > 0:
            bw_all.append((u - l) / m)

    if len(bw_all) < box_n + 10:
        return None

    # ── Recent compression: avg BW of last box_n bars vs historical ────────
    recent_bw   = bw_all[-box_n:]
    hist_bw     = bw_all[:-box_n]   # baseline excludes the recent box
    if len(hist_bw) < 10:
        return None

    recent_avg  = sum(recent_bw) / len(recent_bw)
    pct_rank    = sum(1 for w in hist_bw if w <= recent_avg) / len(hist_bw) * 100.0
    if pct_rank > cfg["squeeze_percentile"]:
        if debug:
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | NOT_COMPRESSED(pct_rank={pct_rank:.1f}>={cfg['squeeze_percentile']})")
        return None   # Recent bars not tighter than historical

    # ── Compression box ceiling (the range the current bar must break above) ──
    range_bars = bars[-(box_n + 1):-1]
    if not range_bars:
        return None
    compression_high = max(b.high for b in range_bars)

    # ── Breakout: close above the compression range high ──────────────────
    if bar.close <= compression_high:
        if debug:
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | NO_BREAKOUT(close={bar.close:.6f}<=high={compression_high:.6f})")
        return None

    # ── Volume spike on breakout bar ───────────────────────────────────────
    vol_sma_p = cfg["volume_sma_period"]
    if len(volumes) < vol_sma_p + 1:
        return None
    vol_sma = sum(volumes[-(vol_sma_p + 1):-1]) / vol_sma_p
    if vol_sma <= 0 or bar.volume / vol_sma < cfg["vol_breakout_mult"]:
        if debug:
            ratio = bar.volume / vol_sma if vol_sma > 0 else 0.0
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | VOL_SPIKE={ratio:.2f}x(need>={cfg['vol_breakout_mult']}x)")
        return None

    # ── Candle quality ─────────────────────────────────────────────────────
    candle_range = bar.high - bar.low
    if candle_range > 0:
        body_pct       = abs(bar.close - bar.open) / candle_range
        close_position = (bar.close - bar.low) / candle_range
        if body_pct < cfg["breakout_body_pct"] or close_position < cfg["breakout_close_position"]:
            if debug:
                reasons: list[str] = []
                if body_pct < cfg["breakout_body_pct"]:
                    reasons.append(f"BODY={body_pct:.2f}(need>={cfg['breakout_body_pct']})")
                if close_position < cfg["breakout_close_position"]:
                    reasons.append(f"CLOSE_POS={close_position:.2f}(need>={cfg['breakout_close_position']})")
                print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | CANDLE: {' | '.join(reasons)}")
            return None

    if cfg.get("require_btc_bull_market", True):
        from research.strategies.bull_flag.strategy import daily_close_above_daily_ema

        ema_p = int(cfg.get("btc_ema_period", 200))
        if not vb_btc_daily_bars:
            if debug:
                print(
                    f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                    "BTC_MACRO(no_daily_btc_series)"
                )
            return None
        t_end = bar.timestamp
        sub_d = [b for b in vb_btc_daily_bars if b.timestamp <= t_end]
        if not sub_d or len(sub_d) < ema_p:
            if debug:
                print(
                    f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                    f"BTC_MACRO(insufficient_daily_bars={len(sub_d)} need>={ema_p})"
                )
            return None
        d_ts = [b.timestamp for b in sub_d]
        d_cl = [b.close for b in sub_d]
        if not daily_close_above_daily_ema(d_ts, d_cl, t_end, ema_p):
            if debug:
                print(
                    f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                    f"BTC_BELOW_EMA{ema_p}"
                )
            return None

    # ── ATR + sizing ───────────────────────────────────────────────────────
    atr = calculate_atr(highs, lows, closes, period=cfg["atr_period"])
    if not atr or atr == 0:
        return None

    entry_price = bar.close
    stop_loss   = bar.low - atr * cfg["retest_buffer_ATR"]
    risk        = entry_price - stop_loss

    if risk <= 0 or risk / entry_price < 0.005:
        return None

    tp1 = entry_price + risk * cfg["tp1_R"]
    tp2 = entry_price + risk * cfg["tp2_R"]

    risk_dollars = equity * risk_pct / 100.0
    qty = risk_dollars / risk

    return Trade(
        trade_num=0,
        side="long",
        entry_bar=len(bars) - 1,
        entry_time=bar.timestamp,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp1_price=tp1,
        tp2_price=tp2,
        qty=qty,
        qty_remaining=qty,
    )


# ─── HTF Trend Pullback signal detection ─────────────────────────────────────

def check_htf_trend_entry_signal(
    bars: list[Bar],
    cfg: dict,
    equity: float,
    risk_pct: float,
    debug: bool = False,
    btc_daily_bars: Optional[list[Bar]] = None,
) -> Optional[Trade]:
    """
    HTF trend pullback continuation entry signal (long only).

    Backtestable single-timeframe simplification (all 1h bars):
    - HTF trend : price above EMA200 (approximates 4h EMA50 on 1h data)
    - Pullback  : current bar's low ≤ EMA20 AND close > EMA20 (touched then bounced)
                  OR price within ±pullback_max_ATR of EMA20 with bullish reversal body
    - EMA50 floor: price must not have closed >break_bps below EMA50
    - Confirmation: body ≥ reversal_body_pct, close in top reversal_close_position of range

    Stop : min(swing_low, entry − ATR × atr_stop_mult) − ATR × swing_buffer_ATR
    TP1/2: entry + risk × tp1_R / tp2_R
    """
    htf_period = cfg["htf_ema_period"]
    etf_fast   = cfg["etf_ema_fast"]
    etf_slow   = cfg["etf_ema_slow"]
    min_bars   = htf_period + cfg["atr_period"] + 10
    if len(bars) < min_bars:
        return None

    closes = [b.close for b in bars]
    highs  = [b.high  for b in bars]
    lows   = [b.low   for b in bars]
    bar    = bars[-1]

    if cfg.get("require_btc_bull_market", False):
        if not btc_daily_bars:
            if debug:
                print(
                    f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                    "BTC_BULL_GATE(no_daily_btc_series)"
                )
            return None
        if not btc_daily_bars_pass_bull_filter_at_ts(
            btc_daily_bars,
            bar.timestamp,
            int(cfg.get("btc_ema_period", 200)),
        ):
            if debug:
                print(
                    f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | "
                    "BTC_BULL_GATE(close_not_above_btc_daily_ema)"
                )
            return None

    # ── HTF trend filter: price above EMA200 ──────────────────────────────
    ema200 = calculate_ema(closes, htf_period)
    if ema200 is None or bar.close <= ema200:
        if debug and ema200 is not None:
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | BELOW_EMA200(close={bar.close:.6f}<=ema200={ema200:.6f})")
        return None

    # ── Entry-timeframe EMAs ───────────────────────────────────────────────
    ema20 = calculate_ema(closes, etf_fast)
    ema50 = calculate_ema(closes, etf_slow)
    if ema20 is None or ema50 is None:
        return None

    # ── ATR ────────────────────────────────────────────────────────────────
    atr = calculate_atr(highs, lows, closes, period=cfg["atr_period"])
    if not atr or atr == 0:
        return None

    # ── Pullback zone: bar touched or is close to EMA20, and above EMA50 ──
    # Classic signal: candle low dipped to/below EMA20 then closed back above it
    touched_ema20 = bar.low <= ema20
    bounced_above = bar.close > ema20
    # Alternatively: within pullback_max_ATR of EMA20 on either side
    near_ema20 = abs(bar.close - ema20) / atr <= cfg["pullback_max_ATR"]
    in_pullback_zone = (touched_ema20 and bounced_above) or near_ema20

    if not in_pullback_zone:
        if debug:
            dist_atr = abs(bar.close - ema20) / atr if atr else 0.0
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | NOT_NEAR_EMA20(dist={dist_atr:.2f}ATR,max={cfg['pullback_max_ATR']}ATR,touched={touched_ema20},bounced={bounced_above})")
        return None

    # EMA50 floor: not more than break_bps below EMA50
    ema50_floor = ema50 * (1.0 - cfg["break_bps"] / 10_000.0)
    if bar.close < ema50_floor:
        if debug:
            print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | BELOW_EMA50_FLOOR(close={bar.close:.6f}<floor={ema50_floor:.6f})")
        return None

    # ── Entry confirmation: bullish reversal candle ────────────────────────
    candle_range = bar.high - bar.low
    if candle_range > 0:
        body_pct       = abs(bar.close - bar.open) / candle_range
        close_position = (bar.close - bar.low) / candle_range
        if body_pct < cfg["reversal_body_pct"] or close_position < cfg["reversal_close_position"]:
            if debug:
                reasons: list[str] = []
                if body_pct < cfg["reversal_body_pct"]:
                    reasons.append(f"BODY={body_pct:.2f}(need>={cfg['reversal_body_pct']})")
                if close_position < cfg["reversal_close_position"]:
                    reasons.append(f"CLOSE_POS={close_position:.2f}(need>={cfg['reversal_close_position']})")
                print(f"REJECT {bar.timestamp.strftime('%Y-%m-%d %H:%M')} | CANDLE: {' | '.join(reasons)}")
            return None

    # ── Stop: below swing low or ATR-based minimum ────────────────────────
    bar_dicts  = [{"high": b.high, "low": b.low, "close": b.close} for b in bars]
    swing      = detect_swing_highs_lows(bar_dicts, lookback=cfg["swing_lookback_bars"])
    swing_stop = min(swing["lows"]) if swing["lows"] else bar.close * 0.95
    atr_stop   = bar.close - atr * cfg["atr_stop_mult"]
    stop_loss  = min(swing_stop, atr_stop) - atr * cfg["swing_buffer_ATR"]

    risk = bar.close - stop_loss
    if risk <= 0 or risk / bar.close < 0.005:
        return None

    entry_price = bar.close
    tp1 = entry_price + risk * cfg["tp1_R"]
    tp2 = entry_price + risk * cfg["tp2_R"]

    risk_dollars = equity * risk_pct / 100.0
    qty = risk_dollars / risk

    return Trade(
        trade_num=0,
        side="long",
        entry_bar=len(bars) - 1,
        entry_time=bar.timestamp,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp1_price=tp1,
        tp2_price=tp2,
        qty=qty,
        qty_remaining=qty,
    )


# ─── Exit logic ───────────────────────────────────────────────────────────────

def check_exits(
    trade: Trade,
    current_bar: Bar,
    bars_so_far: list[Bar],
    bar_idx: int,
    cfg: dict,
) -> Optional[str]:
    """
    Check all exit conditions at current_bar close price.

    Modifies `trade` in-place on TP1 hit (partial close, breakeven stop).
    Returns exit reason string when position should fully close, else None.

    Exit priority (mirrors monitor.py):
      1. Hard stop-loss (or breakeven stop after TP1)
      2. TP1 partial exit (side effect only, no full close)
      3. TP2 full close
      4. Max bars held
      5. VWAP invalidation (after 2-bar grace, min-profit gate applied)
      6. RSI invalidation (after N candles)
    """
    price      = current_bar.close
    bars_held  = bar_idx - trade.entry_bar
    active_stop = trade.breakeven_stop if (trade.tp1_hit and trade.breakeven_stop) else trade.stop_loss

    # 1. Stop-loss
    if trade.side == "long"  and price <= active_stop:
        return "stop_loss"
    if trade.side == "short" and price >= active_stop:
        return "stop_loss"

    # 2. TP1 partial exit (update trade state, don't close)
    if not trade.tp1_hit:
        tp1_reached = (
            (trade.side == "long"  and price >= trade.tp1_price) or
            (trade.side == "short" and price <= trade.tp1_price)
        )
        if tp1_reached:
            trade.tp1_hit = True
            trade.qty_remaining = trade.qty * (1.0 - cfg["tp1_partial_pct"])
            if cfg.get("breakeven_requires_tp1", True):
                fee_buf = MAKER_FEE + TAKER_FEE
                if trade.side == "long":
                    trade.breakeven_stop = trade.entry_price * (1.0 + fee_buf)
                else:
                    trade.breakeven_stop = trade.entry_price * (1.0 - fee_buf)

    # 3. TP2 full close
    tp2_reached = (
        (trade.side == "long"  and price >= trade.tp2_price) or
        (trade.side == "short" and price <= trade.tp2_price)
    )
    if tp2_reached:
        return "tp2"

    # 4. Max bars held
    if bars_held >= cfg["max_bars_in_trade"]:
        return "max_hold"

    # 5. VWAP invalidation (2-bar grace period)
    if bars_held >= 2:
        highs  = [b.high  for b in bars_so_far]
        lows   = [b.low   for b in bars_so_far]
        closes = [b.close for b in bars_so_far]
        atr    = calculate_atr(highs, lows, closes, period=cfg["atr_period"])
        s_vwap, anchored_vwap = _compute_vwap(bars_so_far, cfg["anchored_vwap_lookback"])
        vwap = anchored_vwap if anchored_vwap else s_vwap

        if atr and atr > 0 and vwap:
            dev_atr = abs(price - vwap) / atr
            if dev_atr > cfg["invalidation_vwap_atr_mult"]:
                # Min-profit gate (mirrors monitor._check_invalidation_exit)
                if trade.side == "long":
                    pnl_pct = (price - trade.entry_price) / trade.entry_price * 100.0
                else:
                    pnl_pct = (trade.entry_price - price) / trade.entry_price * 100.0

                min_threshold = max(0.6, cfg["min_stop_pct"] * 1.5)
                if 0.0 < pnl_pct < min_threshold:
                    pass  # Suppress: let breakeven/trailing stop handle it
                else:
                    return "invalidation_vwap"

    # 6. RSI invalidation
    if bars_held >= cfg["invalidation_rsi_candles"]:
        closes = [b.close for b in bars_so_far]
        rsi    = calculate_rsi(closes, period=cfg["rsi_period"])
        if rsi is not None:
            rsi_long_floor  = cfg.get("invalidation_rsi_long_floor",  40)
            rsi_short_floor = cfg.get("invalidation_rsi_short_floor", 60)
            requires_recovery = cfg.get("invalidation_rsi_requires_recovery", False)
            recovery_level = cfg.get("invalidation_rsi_recovery_level", 45)
            short_recovery = cfg.get("invalidation_rsi_recovery_level_short", 55)

            if requires_recovery:
                if trade.side == "long" and rsi >= recovery_level:
                    trade.rsi_recovery_seen = True
                elif trade.side == "short" and rsi <= short_recovery:
                    trade.rsi_recovery_seen = True

            if trade.side == "long" and rsi < rsi_long_floor:
                if not requires_recovery or trade.rsi_recovery_seen:
                    return "invalidation_rsi"
            if trade.side == "short" and rsi > rsi_short_floor:
                if not requires_recovery or trade.rsi_recovery_seen:
                    return "invalidation_rsi"

    return None  # Hold


# ─── P&L calculation ─────────────────────────────────────────────────────────

def _exit_fill_price(trade: Trade, reason: str, bar: Bar) -> float:
    """Return the correct simulated fill price for a completed exit.
    Stop and limit orders fill at their trigger price, not at bar close.
    All other exits (invalidation, max_hold) fill at bar close.
    """
    if reason == "stop_loss":
        return (
            trade.breakeven_stop
            if (trade.tp1_hit and trade.breakeven_stop is not None)
            else trade.stop_loss
        )
    if reason == "tp2":
        return trade.tp2_price
    return bar.close


def _compute_pnl(trade: Trade, cfg: dict) -> float:
    """
    Realized P&L including fees.
    TP1 partial exit at tp1_price, remainder at exit_price.
    """
    entry  = trade.entry_price
    exit_p = trade.exit_price  # type: ignore[assignment]

    if trade.tp1_hit:
        tp1_qty  = trade.qty * cfg["tp1_partial_pct"]
        rem_qty  = trade.qty - tp1_qty

        if trade.side == "long":
            tp1_gross  = (trade.tp1_price - entry) * tp1_qty
            rem_gross  = (exit_p - entry) * rem_qty
        else:
            tp1_gross  = (entry - trade.tp1_price) * tp1_qty
            rem_gross  = (entry - exit_p) * rem_qty

        tp1_fees = entry * tp1_qty * MAKER_FEE + trade.tp1_price * tp1_qty * TAKER_FEE
        rem_fees = entry * rem_qty * MAKER_FEE + exit_p * rem_qty * TAKER_FEE
        return (tp1_gross - tp1_fees) + (rem_gross - rem_fees)
    else:
        if trade.side == "long":
            gross = (exit_p - entry) * trade.qty
        else:
            gross = (entry - exit_p) * trade.qty
        fees  = entry * trade.qty * MAKER_FEE + exit_p * trade.qty * TAKER_FEE
        return gross - fees


# ─── Single-symbol backtester engine ─────────────────────────────────────────

def run_backtest(
    bars: list[Bar],
    cfg: dict,
    starting_equity: float,
    risk_pct: float,
    long_only: bool,
    strategy: str = "vwap_meanrev",
    debug_signal: bool = False,
    swing_daily_bars: Optional[list[Bar]] = None,
    swing_btc_bars: Optional[list[Bar]] = None,
    vb_btc_daily_bars: Optional[list[Bar]] = None,
    htf_btc_daily_bars: Optional[list[Bar]] = None,
    vwap_htf_bars: Optional[list[Bar]] = None,
    symbol: str = "",
    btc_bars: Optional[list[Bar]] = None,
    supply: Optional[float] = None,
    interval_min: int = 15,
    rvol_lb_override: Optional[int] = None,
) -> tuple[list[Trade], list[float]]:
    """
    Bar-by-bar replay for a single symbol.

    Returns:
        completed_trades: All closed trades (including end-of-data close).
        equity_curve:     Equity value after each bar (length = len(bars)).
    """
    equity       = starting_equity
    equity_curve = [equity]
    completed:   list[Trade] = []
    open_trade:  Optional[Trade] = None
    counter      = 0

    for i in range(WARMUP_BARS, len(bars)):
        bar          = bars[i]
        bars_so_far  = bars[: i + 1]

        if open_trade is not None:
            reason = check_exits(open_trade, bar, bars_so_far, i, cfg)
            if reason:
                open_trade.exit_bar    = i
                open_trade.exit_time   = bar.timestamp
                open_trade.exit_price  = _exit_fill_price(open_trade, reason, bar)
                open_trade.exit_reason = reason
                open_trade.bars_held   = i - open_trade.entry_bar
                open_trade.pnl_usd     = _compute_pnl(open_trade, cfg)
                equity                += open_trade.pnl_usd
                completed.append(open_trade)
                open_trade = None
        else:
            if strategy == "pullback_vwap":
                signal = check_pullback_vwap_entry_signal(bars_so_far, cfg, equity, risk_pct, debug=debug_signal)
            elif strategy == "meanrev":
                signal = check_meanrev_entry_signal(bars_so_far, cfg, equity, risk_pct, debug=debug_signal)
            elif strategy in ("bull_flag_1m", "bull_flag_5m", "bull_flag_1h"):
                signal = check_bull_flag_entry_signal(bars_so_far, cfg, equity, risk_pct, debug=debug_signal)
            elif strategy == "swing_bull_flag":
                d_ts: Optional[list[Any]] = None
                d_cl: Optional[list[float]] = None
                if swing_daily_bars:
                    t_end = bars_so_far[-1].timestamp
                    sub_d = [b for b in swing_daily_bars if b.timestamp <= t_end]
                    if sub_d:
                        d_ts = [b.timestamp for b in sub_d]
                        d_cl = [b.close for b in sub_d]
                btc_c: Optional[list[float]] = None
                if swing_btc_bars:
                    upto = min(len(bars_so_far), len(swing_btc_bars))
                    btc_c = [swing_btc_bars[j].close for j in range(upto)]
                signal = check_bull_flag_entry_signal(
                    bars_so_far,
                    cfg,
                    equity,
                    risk_pct,
                    debug=debug_signal,
                    daily_timestamps=d_ts,
                    daily_closes=d_cl,
                    btc_closes=btc_c,
                )
            elif strategy == "volatility_breakout":
                signal = check_volatility_breakout_entry_signal(
                    bars_so_far,
                    cfg,
                    equity,
                    risk_pct,
                    debug=debug_signal,
                    vb_btc_daily_bars=vb_btc_daily_bars,
                )
            elif strategy == "htf_trend":
                signal = check_htf_trend_entry_signal(
                    bars_so_far,
                    cfg,
                    equity,
                    risk_pct,
                    debug=debug_signal,
                    btc_daily_bars=htf_btc_daily_bars,
                )
            else:
                signal = check_entry_signal(
                    bars_so_far,
                    cfg,
                    equity,
                    risk_pct,
                    long_only,
                    debug=debug_signal,
                    htf_bars=vwap_htf_bars,
                )
            if signal is not None:
                counter       += 1
                signal.trade_num = counter
                if symbol:
                    signal.symbol = symbol
                _populate_trade_entry_log(
                    signal,
                    bars_so_far,
                    btc_bars=btc_bars,
                    interval_min=interval_min,
                    supply=supply,
                    cfg=cfg,
                    strategy=strategy,
                    rvol_lb_override=rvol_lb_override,
                    htf_bars=vwap_htf_bars,
                )
                open_trade    = signal

        equity_curve.append(equity)

    # Force-close any open position at end of data
    if open_trade is not None:
        last = bars[-1]
        open_trade.exit_bar    = len(bars) - 1
        open_trade.exit_time   = last.timestamp
        open_trade.exit_price  = last.close
        open_trade.exit_reason = "end_of_data"
        open_trade.bars_held   = open_trade.exit_bar - open_trade.entry_bar
        open_trade.pnl_usd     = _compute_pnl(open_trade, cfg)
        equity                += open_trade.pnl_usd
        completed.append(open_trade)

    return completed, equity_curve


# ─── Pipeline grader (no Redis, no live APIs) ─────────────────────────────────

def _meanrev_pipeline_d2_substitute(
    closes: list[float], highs: list[float], lows: list[float]
) -> bool:
    """When pipeline strategy is meanrev, D2 momentum is replaced by oversold/ranging gate."""
    lp = MEANREV_DEFAULT_CONFIG["lookback_period"]
    std_m = MEANREV_DEFAULT_CONFIG["std_dev_multiplier"]
    rsi_p = MEANREV_DEFAULT_CONFIG["rsi_period"]
    adx_max = MEANREV_DEFAULT_CONFIG["adx_max_threshold"]
    min_len = max(lp + 3, 35)
    if len(closes) < min_len or len(highs) < min_len or len(lows) < min_len:
        return False
    rsi = calculate_rsi(closes, period=rsi_p)
    _upper, _mid, lower = _calc_bb_from_closes(closes, lp, std_m)
    adx = calculate_adx(highs, lows, closes, period=14)
    if rsi is None or adx is None:
        return False
    return bool(rsi < 40.0 and closes[-1] <= lower and adx < adx_max)


def _grade_pair_at_bar(
    bars: list[Bar],
    btc_bars: list[Bar],
    interval_min: int,
    supply: Optional[float],
    relax: bool = False,
    rvol_lb_override: Optional[int] = None,
    pipeline_strategy: Optional[str] = None,
) -> dict:
    """
    Compute the 5-pillar pipeline grade from historical OHLCV bars only.

    All metrics are derived from the provided bar windows — no API calls made.
    relax=True uses lower thresholds suited for small paper accounts / low-volume pairs.
    rvol_lb_override: if set, use this lookback length instead of bpd × PIPELINE_RVOL_DAYS.
    Returns a dict with 'grade', 'dynamic_passes', 'hard_floor', 'stage1_pass',
    and per-pillar 'pillars' details.
    """
    if not bars:
        return {"grade": "F", "dynamic_passes": 0, "hard_floor": False,
                "stage1_pass": False, "pillars": {}}

    bpd        = max(1, 1440 // interval_min)    # bars per calendar day
    rvol_lb    = rvol_lb_override if rvol_lb_override is not None else bpd * PIPELINE_RVOL_DAYS
    bars_per_4h = max(1, 240 // interval_min)    # bars in a 4-hour window

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    close  = closes[-1]

    # ── Hard floor: 24h USD volume ──────────────────────────────────────────
    # Volume is base units; approximate USD notionals as volume × close (USDT)
    vol_24h_usd = sum(b.volume * b.close for b in bars[-bpd:]) if len(bars) >= bpd else 0.0
    floor_thresh = RELAX_HARD_FLOOR if relax else HARD_FLOOR_VOLUME_USD
    hard_floor_pass = vol_24h_usd >= floor_thresh

    # ── S1: Circulating supply ───────────────────────────────────────────────
    if supply is not None:
        s1_pass  = supply < S1_MAX_SUPPLY
        s1_value: Optional[float] = supply
    else:
        s1_pass  = True   # fail-open: no supply data for historical bars
        s1_value = None

    # ── S2: Price range ──────────────────────────────────────────────────────
    s2_pass = S2_MIN_PRICE <= close <= S2_MAX_PRICE

    # ── S3: Listing activity (≥20 active days in last 30) ────────────────────
    lookback_bars = bars[-(bpd * S3_LOOKBACK_DAYS):] if len(bars) >= bpd * S3_LOOKBACK_DAYS else bars
    days_with_vol: set[str] = set()
    for b in lookback_bars:
        if b.volume > 0:
            days_with_vol.add(b.timestamp.strftime("%Y-%m-%d"))
    s3_active_days = len(days_with_vol)
    s3_pass = s3_active_days >= S3_MIN_ACTIVE_DAYS

    stage1_pass = s1_pass and s2_pass and s3_pass

    # ── D1: Relative volume (current bar vs 30-day average) ─────────────────
    vols = [b.volume for b in bars]
    if len(vols) >= rvol_lb + 1:
        avg_vol = sum(vols[-rvol_lb - 1:-1]) / rvol_lb
    elif len(vols) >= 2:
        avg_vol = sum(vols[:-1]) / (len(vols) - 1)
    else:
        avg_vol = 0.0
    rvol: Optional[float] = (bars[-1].volume / avg_vol) if avg_vol > 0 else None
    rvol_thresh = RELAX_D1_MIN_RVOL if relax else D1_MIN_RVOL
    d1_pass = rvol is not None and rvol >= rvol_thresh

    # ── D2: Price momentum (24h and 4h) ─────────────────────────────────────
    if len(closes) > bpd:
        mom_24h: Optional[float] = (closes[-1] - closes[-bpd - 1]) / closes[-bpd - 1] * 100.0
    else:
        mom_24h = None

    if len(closes) > bars_per_4h:
        mom_4h: Optional[float] = (closes[-1] - closes[-bars_per_4h - 1]) / closes[-bars_per_4h - 1] * 100.0
    else:
        mom_4h = None

    d2_pass = (
        (mom_24h is not None and mom_24h >= D2_MIN_24H_PCT) or
        (mom_4h  is not None and mom_4h  >= D2_MIN_4H_PCT)
    )
    if pipeline_strategy == "meanrev":
        d2_pass = _meanrev_pipeline_d2_substitute(closes, highs, lows)

    # ── D3: Volume sweet spot ────────────────────────────────────────────────
    d3_min = RELAX_D3_MIN_VOLUME if relax else D3_MIN_VOLUME
    d3_max = RELAX_D3_MAX_VOLUME if relax else D3_MAX_VOLUME
    d3_pass = d3_min <= vol_24h_usd <= d3_max

    # ── D4: BTC 4h health ────────────────────────────────────────────────────
    if btc_bars and len(btc_bars) > bars_per_4h:
        btc_closes = [b.close for b in btc_bars]
        btc_4h: Optional[float] = (
            (btc_closes[-1] - btc_closes[-bars_per_4h - 1])
            / btc_closes[-bars_per_4h - 1] * 100.0
        )
        d4_pass = btc_4h >= D4_MAX_BTC_DROP
    else:
        btc_4h = None
        d4_pass = True  # fail-open when BTC data insufficient

    # ── Grade ─────────────────────────────────────────────────────────────────
    dynamic_passes = sum([d1_pass, d2_pass, d3_pass, d4_pass])

    if not hard_floor_pass or not stage1_pass or dynamic_passes == 0:
        grade = "F"
    elif dynamic_passes >= 4:
        grade = "A+"
    elif dynamic_passes == 3:
        grade = "A"
    elif dynamic_passes == 2:
        grade = "B"
    else:
        grade = "C"

    return {
        "grade": grade,
        "dynamic_passes": dynamic_passes,
        "hard_floor": hard_floor_pass,
        "stage1_pass": stage1_pass,
        "pillars": {
            "s1_supply":  {"pass": s1_pass,  "value": s1_value},
            "s2_price":   {"pass": s2_pass,  "value": round(close, 6)},
            "s3_activity":{"pass": s3_pass,  "value": s3_active_days},
            "d1_rvol":    {"pass": d1_pass,  "value": round(rvol, 3) if rvol is not None else None},
            "d2_momentum":{"pass": d2_pass,
                           "value":    round(mom_24h, 2) if mom_24h is not None else None,
                           "value_4h": round(mom_4h,  2) if mom_4h  is not None else None},
            "d3_volume":  {"pass": d3_pass,  "value": round(vol_24h_usd)},
            "d4_btc":     {"pass": d4_pass,  "value": round(btc_4h, 2) if btc_4h is not None else None},
        },
    }


# ─── Pipeline backtester engine ───────────────────────────────────────────────

def run_pipeline_backtest(
    all_bars: dict[str, list[Bar]],
    btc_bars: list[Bar],
    universe: list[str],
    supplies: dict[str, float],
    cfg: dict,
    starting_equity: float,
    risk_pct: float,
    long_only: bool,
    interval_min: int,
    relax: bool = False,
    strategy: str = "vwap_meanrev",
    debug_signal: bool = False,
    daily_fetcher: Optional[Callable[[str], list[Bar]]] = None,
    vb_btc_daily_bars: Optional[list[Bar]] = None,
    htf_btc_daily_bars: Optional[list[Bar]] = None,
    vwap_htf_bars_by_symbol: Optional[dict[str, list[Bar]]] = None,
    min_grade: str = "B",
) -> tuple[list[Trade], list[float]]:
    """
    Pipeline bar-by-bar replay.

    At each bar:
      - If in a trade: check exits on the active pair.
      - If flat: grade all universe pairs, pick the highest-graded pair at or
        above min_grade, look for a strategy entry signal.
    relax=True lowers pillar thresholds and includes C-grade entries at 0.25× size.

    Grade-based position sizing:
      A+/A → full risk_pct  |  B → 50% risk_pct  |  C/F → skip
    """
    min_grade_score = GRADE_ORDER.get(min_grade, GRADE_ORDER["B"])
    bpd         = max(1, 1440 // interval_min)

    # Anchor the window on BTC bars (always has full data), falling back to
    # the longest universe series if BTC is missing.
    anchor_series = btc_bars or max(all_bars.values(), key=len, default=[])
    if not anchor_series:
        return [], [starting_equity]
    anchor_len = len(anchor_series)

    ideal_rvol_lb = bpd * PIPELINE_RVOL_DAYS
    max_rvol_lb   = max(bpd * 7, int(anchor_len * 0.5))
    rvol_lb       = min(ideal_rvol_lb, max_rvol_lb)
    pipeline_warmup = max(WARMUP_BARS, rvol_lb + 10)

    if anchor_len < pipeline_warmup + 10:
        print(f"\n  WARNING: Only {anchor_len} bars available; pipeline warmup needs {pipeline_warmup}.")
        print(f"  Try: --days 30 --interval 1h  OR  --days 60 --interval 4h")
        return [], [starting_equity]

    # Drop universe pairs that don't have enough bars to participate —
    # a handful of sparse/newly-listed tokens shouldn't abort the whole run.
    min_bars_needed = pipeline_warmup + 10
    sparse = [sym for sym, bars in all_bars.items() if len(bars) < min_bars_needed]
    if sparse:
        print(f"\n  Dropping {len(sparse)} pair(s) with < {min_bars_needed} bars: "
              f"{', '.join(sparse[:10])}{'…' if len(sparse) > 10 else ''}")
        all_bars = {sym: bars for sym, bars in all_bars.items() if sym not in sparse}

    if not all_bars:
        print("  No pairs remain after filtering sparse data.")
        return [], [starting_equity]

    rvol_days = rvol_lb // bpd
    print(f"\n  RVOL baseline: {rvol_days}d ({rvol_lb} bars)  |  "
          f"warmup: {pipeline_warmup} bars")
    print(f"  Active trading window: {anchor_len - pipeline_warmup} bars "
          f"({(anchor_len - pipeline_warmup) * interval_min // 1440:.0f} days)")

    equity        = starting_equity
    equity_curve  = [equity]
    completed: list[Trade] = []
    active_trade: Optional[Trade] = None
    active_symbol: Optional[str]  = None
    counter       = 0

    for i in range(pipeline_warmup, anchor_len):
        # ── Check exits on active trade ───────────────────────────────────────
        if active_trade is not None and active_symbol is not None:
            sym_bars = all_bars.get(active_symbol, [])
            if i >= len(sym_bars):
                # Active pair ran out of bars — force-close at last known bar
                last_bar = sym_bars[-1] if sym_bars else None
                if last_bar:
                    active_trade.exit_bar    = i - 1
                    active_trade.exit_time   = last_bar.timestamp
                    active_trade.exit_price  = last_bar.close
                    active_trade.exit_reason = "max_hold"
                    active_trade.bars_held   = (i - 1) - active_trade.entry_bar
                    active_trade.pnl_usd     = _compute_pnl(active_trade, cfg)
                    equity                  += active_trade.pnl_usd
                    completed.append(active_trade)
                active_trade  = None
                active_symbol = None
            else:
                bar         = sym_bars[i]
                bars_so_far = sym_bars[:i + 1]

                reason = check_exits(active_trade, bar, bars_so_far, i, cfg)
                if reason:
                    active_trade.exit_bar    = i
                    active_trade.exit_time   = bar.timestamp
                    active_trade.exit_price  = _exit_fill_price(active_trade, reason, bar)
                    active_trade.exit_reason = reason
                    active_trade.bars_held   = i - active_trade.entry_bar
                    active_trade.pnl_usd     = _compute_pnl(active_trade, cfg)
                    equity                  += active_trade.pnl_usd
                    completed.append(active_trade)
                    active_trade  = None
                    active_symbol = None

        # ── Look for a new entry when flat ────────────────────────────────────
        if active_trade is None:
            best_symbol: Optional[str] = None
            best_score  = -1
            best_grade  = "F"

            for sym in universe:
                sym_bars = all_bars.get(sym)
                if not sym_bars or len(sym_bars) <= i:
                    continue
                result = _grade_pair_at_bar(
                    sym_bars[:i + 1],
                    btc_bars[:i + 1] if btc_bars else [],
                    interval_min,
                    supplies.get(sym),
                    relax=relax,
                    rvol_lb_override=rvol_lb,
                    pipeline_strategy=strategy,
                )
                score = GRADE_ORDER.get(result["grade"], 0)
                if score >= min_grade_score and score > best_score:
                    best_score  = score
                    best_symbol = sym
                    best_grade  = result["grade"]

            sf_table    = GRADE_SIZE_FACTOR_RELAX if relax else GRADE_SIZE_FACTOR
            size_factor = sf_table.get(best_grade, 0.0)
            if (
                best_symbol
                and size_factor > 0
                and GRADE_ORDER.get(best_grade, 0) >= min_grade_score
            ):
                sym_bars   = all_bars[best_symbol]
                _eq        = equity * size_factor
                _sl        = sym_bars[:i + 1]
                if strategy == "pullback_vwap":
                    signal = check_pullback_vwap_entry_signal(_sl, cfg, _eq, risk_pct, debug=debug_signal)
                elif strategy == "meanrev":
                    signal = check_meanrev_entry_signal(_sl, cfg, _eq, risk_pct, debug=debug_signal)
                elif strategy in ("bull_flag_1m", "bull_flag_5m", "bull_flag_1h"):
                    signal = check_bull_flag_entry_signal(_sl, cfg, _eq, risk_pct, debug=debug_signal)
                elif strategy == "swing_bull_flag":
                    d_ts: Optional[list[Any]] = None
                    d_cl: Optional[list[float]] = None
                    if daily_fetcher and best_symbol:
                        try:
                            dfull = daily_fetcher(best_symbol)
                            t_end = _sl[-1].timestamp
                            sub_d = [b for b in dfull if b.timestamp <= t_end]
                            if sub_d:
                                d_ts = [b.timestamp for b in sub_d]
                                d_cl = [b.close for b in sub_d]
                        except Exception:
                            d_ts, d_cl = None, None
                    btc_c: Optional[list[float]] = None
                    if btc_bars:
                        btc_seg = btc_bars[: i + 1]
                        btc_c = [b.close for b in btc_seg]
                    signal = check_bull_flag_entry_signal(
                        _sl,
                        cfg,
                        _eq,
                        risk_pct,
                        debug=debug_signal,
                        daily_timestamps=d_ts,
                        daily_closes=d_cl,
                        btc_closes=btc_c,
                    )
                elif strategy == "volatility_breakout":
                    signal = check_volatility_breakout_entry_signal(
                        _sl,
                        cfg,
                        _eq,
                        risk_pct,
                        debug=debug_signal,
                        vb_btc_daily_bars=vb_btc_daily_bars,
                    )
                elif strategy == "htf_trend":
                    signal = check_htf_trend_entry_signal(
                        _sl,
                        cfg,
                        _eq,
                        risk_pct,
                        debug=debug_signal,
                        btc_daily_bars=htf_btc_daily_bars,
                    )
                else:
                    htf_seg: Optional[list[Bar]] = None
                    if vwap_htf_bars_by_symbol and best_symbol:
                        raw_htf = vwap_htf_bars_by_symbol.get(best_symbol)
                        if raw_htf:
                            t_end = _sl[-1].timestamp
                            htf_seg = [b for b in raw_htf if b.timestamp <= t_end]
                    signal = check_entry_signal(
                        _sl,
                        cfg,
                        _eq,
                        risk_pct,
                        long_only,
                        debug=debug_signal,
                        htf_bars=htf_seg,
                    )
                if signal is not None:
                    counter          += 1
                    signal.trade_num  = counter
                    signal.symbol     = best_symbol
                    signal.grade      = best_grade
                    htf_for_log: Optional[list[Bar]] = None
                    if strategy in ("vwap_meanrev", "vwap_meanrev_1h"):
                        if vwap_htf_bars_by_symbol and best_symbol:
                            raw_htf = vwap_htf_bars_by_symbol.get(best_symbol)
                            if raw_htf:
                                t_end = _sl[-1].timestamp
                                htf_for_log = [b for b in raw_htf if b.timestamp <= t_end]
                    _populate_trade_entry_log(
                        signal,
                        _sl,
                        btc_bars=btc_bars[: i + 1] if btc_bars else None,
                        interval_min=interval_min,
                        supply=supplies.get(best_symbol),
                        cfg=cfg,
                        strategy=strategy,
                        rvol_lb_override=rvol_lb,
                        htf_bars=htf_for_log,
                        relax=relax,
                    )
                    active_trade      = signal
                    active_symbol     = best_symbol

        equity_curve.append(equity)

    # Force-close any open position at end of data
    if active_trade is not None and active_symbol is not None:
        sym_final = all_bars[active_symbol]
        last_idx  = min(anchor_len - 1, len(sym_final) - 1)
        last_bar  = sym_final[last_idx]
        active_trade.exit_bar    = last_idx
        active_trade.exit_time   = last_bar.timestamp
        active_trade.exit_price  = last_bar.close
        active_trade.exit_reason = "end_of_data"
        active_trade.bars_held   = active_trade.exit_bar - active_trade.entry_bar
        active_trade.pnl_usd     = _compute_pnl(active_trade, cfg)
        equity                  += active_trade.pnl_usd
        completed.append(active_trade)

    return completed, equity_curve


# ─── Metrics & output ─────────────────────────────────────────────────────────

def _effective_span_days_and_hist_label(ns: argparse.Namespace) -> tuple[float, str]:
    """If --years set, spans ~365.2425d per year and overrides plain --days labeling."""
    if ns.years is not None:
        d = ns.years * YEARS_APPROX_DAYS_PER_YEAR
        lbl = f"{ns.years:g} years (~{d:.1f}d)"
        return d, lbl
    return float(ns.days), f"{ns.days} days"


def _max_drawdown(equity_curve: list[float]) -> float:
    peak  = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        peak   = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return max_dd


def print_metrics(
    trades: list[Trade],
    equity_curve: list[float],
    starting_equity: float,
    symbol: str,
    hist_display: str,
    interval: str,
    strategy: str = "vwap_meanrev",
) -> None:
    if not trades:
        print("\nNo trades generated.")
        return

    wins   = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    total  = len(trades)

    win_rate = len(wins) / total * 100.0
    avg_win  = sum(t.pnl_usd for t in wins)  / len(wins)  if wins   else 0.0
    avg_loss = sum(t.pnl_usd for t in losses) / len(losses) if losses else 0.0
    total_pnl = sum(t.pnl_usd for t in trades)
    rr        = abs(avg_win / avg_loss) if avg_loss else float("inf")
    max_dd    = _max_drawdown(equity_curve)

    reason_counts: dict[str, int] = {}
    for t in trades:
        r = t.exit_reason or "unknown"
        reason_counts[r] = reason_counts.get(r, 0) + 1

    _hdr_labels = {
        "vwap_meanrev": "VWAP MEAN REVERSION",
        "vwap_meanrev_1h": "VWAP MEAN REVERSION (1H)",
        "pullback_vwap": "PULLBACK TO VWAP",
        "meanrev": "MEAN REVERSION (BB+RSI+ADX)",
        "volatility_breakout": "VOLATILITY BREAKOUT",
        "htf_trend": "HTF TREND PULLBACK",
        "bull_flag_1m": "BULL FLAG + MOMENTUM PULLBACK (1M)",
        "bull_flag_5m": "BULL FLAG + MOMENTUM PULLBACK (5M)",
        "bull_flag_1h": "BULL FLAG + MOMENTUM PULLBACK (1H)",
        "swing_bull_flag": "SWING BULL FLAG (W2, 4H)",
    }
    hdr = _hdr_labels.get(strategy, strategy.upper().replace("_", " "))
    w = 60
    print("\n" + "═" * w)
    print(f"  {hdr} BACKTEST RESULTS")
    print("═" * w)
    print(f"  Symbol   : {symbol}   |  History: {hist_display}  |  Interval: {interval}")
    print(f"  Bars     : {len(equity_curve) - 1:,}  (warmup: {WARMUP_BARS})")
    print(f"  NOTE     : Live-only filters skipped where not modeled (see module docstring)")
    print("─" * w)
    print(f"  Trades   : {total}  ({len(wins)} wins / {len(losses)} losses)")
    print(f"  Win rate : {win_rate:.1f}%")
    print(f"  Avg win  : ${avg_win:+.4f}")
    print(f"  Avg loss : ${avg_loss:+.4f}")
    print(f"  R:R      : {rr:.2f}:1")
    print(f"  Total P&L: ${total_pnl:+.4f}")
    print(f"  Max DD   : -${max_dd:.4f}")
    print(f"  Equity   : ${equity_curve[-1]:.2f}  (started ${starting_equity:.2f})")
    print("─" * w)
    print("  Exit breakdown:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    {reason:<28} {count:>4}")
    print("═" * w)


def print_pipeline_metrics(
    trades: list[Trade],
    equity_curve: list[float],
    starting_equity: float,
    universe: list[str],
    hist_display: str,
    interval: str,
    strategy: str = "vwap_meanrev",
) -> None:
    if not trades:
        print("\nNo trades generated.")
        print("  The screener correctly rejected all pairs for this period.")
        print("  Hint: pairs need RVOL > 3× + momentum > 8%/24h + 24h vol > $500K.")
        print("  Try: --relax-pillars  (lowers thresholds for small paper accounts)")
        print("  Or:  use more volatile pairs / a larger --universe")
        return

    wins   = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    total  = len(trades)

    win_rate  = len(wins) / total * 100.0
    avg_win   = sum(t.pnl_usd for t in wins)  / len(wins)  if wins   else 0.0
    avg_loss  = sum(t.pnl_usd for t in losses) / len(losses) if losses else 0.0
    total_pnl = sum(t.pnl_usd for t in trades)
    rr        = abs(avg_win / avg_loss) if avg_loss else float("inf")
    max_dd    = _max_drawdown(equity_curve)

    _strategy_labels = {
        "vwap_meanrev":        "VWAP MEAN REVERSION",
        "vwap_meanrev_1h":     "VWAP MEAN REVERSION (1H)",
        "pullback_vwap":       "PULLBACK TO VWAP",
        "meanrev":             "MEAN REVERSION (BB+RSI+ADX)",
        "volatility_breakout": "VOLATILITY BREAKOUT",
        "htf_trend":           "HTF TREND PULLBACK",
        "bull_flag_1m":        "BULL FLAG + MOMENTUM PULLBACK (1M)",
        "bull_flag_5m":        "BULL FLAG + MOMENTUM PULLBACK (5M)",
        "bull_flag_1h":        "BULL FLAG + MOMENTUM PULLBACK (1H)",
        "swing_bull_flag":     "SWING BULL FLAG (W2, 4H)",
    }
    strategy_label = _strategy_labels.get(strategy, strategy.upper())
    w = 68
    print("\n" + "═" * w)
    print(f"  PIPELINE BACKTEST — {strategy_label}")
    print("═" * w)
    print(f"  Universe : {', '.join(universe)}")
    print(f"  History  : {hist_display}  |  Interval: {interval}  |  Pairs: {len(universe)}")
    print(f"  NOTE     : OHLC from Binance (USDT); Kraken wsname universe; live bot unchanged")
    print(f"  NOTE     : HTF regime filter, 1m candle check, VWAP slope guard skipped")
    print(f"  NOTE     : S1 supply from hardcoded table (fail-open for unknown pairs)")
    print("─" * w)
    print(f"  Trades   : {total}  ({len(wins)} wins / {len(losses)} losses)")
    print(f"  Win rate : {win_rate:.1f}%")
    print(f"  Avg win  : ${avg_win:+.4f}")
    print(f"  Avg loss : ${avg_loss:+.4f}")
    print(f"  R:R      : {rr:.2f}:1")
    print(f"  Total P&L: ${total_pnl:+.4f}")
    print(f"  Max DD   : -${max_dd:.4f}")
    print(f"  Equity   : ${equity_curve[-1]:.2f}  (started ${starting_equity:.2f})")
    print("─" * w)

    # Grade distribution
    grade_counts: dict[str, int] = {}
    for t in trades:
        g = t.grade or "?"
        grade_counts[g] = grade_counts.get(g, 0) + 1
    print("  Entry grades:")
    for g in ("A+", "A", "B", "C", "?"):
        if g in grade_counts:
            bar = "█" * grade_counts[g]
            print(f"    {g:<4} {grade_counts[g]:>3}  {bar}")
    print("─" * w)

    # Per-pair breakdown
    pair_trades: dict[str, list[Trade]] = {}
    for t in trades:
        pair_trades.setdefault(t.symbol or "?", []).append(t)

    print("  Per-pair breakdown:")
    print(f"  {'Symbol':<14} {'Trades':>6} {'Win%':>7} {'Avg P&L':>10} {'Total P&L':>11}")
    print("  " + "─" * 52)
    for sym in sorted(pair_trades):
        pts = pair_trades[sym]
        pw  = [t for t in pts if t.pnl_usd > 0]
        pwr = len(pw) / len(pts) * 100.0
        avg = sum(t.pnl_usd for t in pts) / len(pts)
        tot = sum(t.pnl_usd for t in pts)
        print(f"  {sym:<14} {len(pts):>6} {pwr:>6.1f}% {avg:>+10.4f} {tot:>+11.4f}")
    print("─" * w)

    # Exit breakdown
    reason_counts: dict[str, int] = {}
    for t in trades:
        r = t.exit_reason or "unknown"
        reason_counts[r] = reason_counts.get(r, 0) + 1
    print("  Exit breakdown:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    {reason:<28} {count:>4}")
    print("═" * w)


def print_pipeline_calendar_year_breakdown(trades: list[Trade]) -> None:
    """Per-calendar-year stats by exit_time (UTC). Labels avoid repeating 'Win rate' lines (supervisor parser)."""
    by_year: dict[int, list[Trade]] = {}
    for t in trades:
        et = t.exit_time
        if et is None:
            continue
        by_year.setdefault(et.year, []).append(t)

    years = sorted(by_year.keys())
    if not years:
        return

    w = 74
    print("\n  " + "─" * w)
    print("  Calendar-year breakdown (UTC, by exit)")
    print("  " + "─" * w)
    print(
        f"  {'Year':<6}"
        f"{'Trades':>8}"
        f"{'W/L':>10}"
        f"{'Pct_wr':>9}"
        f"{'PnL_$':>12}"
        f"{'R:R':>10}"
    )
    print("  " + "─" * w)
    for y in years:
        pts = by_year[y]
        wins_y = [t for t in pts if t.pnl_usd > 0]
        loss_y = [t for t in pts if t.pnl_usd <= 0]
        n = len(pts)
        pw = len(wins_y) / n * 100.0 if n else 0.0
        aw = sum(t.pnl_usd for t in wins_y) / len(wins_y) if wins_y else 0.0
        al = sum(t.pnl_usd for t in loss_y) / len(loss_y) if loss_y else 0.0
        rr_y = abs(aw / al) if al else float("inf")
        tot = sum(t.pnl_usd for t in pts)
        rr_disp = "inf:1" if rr_y == float("inf") else f"{rr_y:.2f}:1"
        print(
            f"  {y:<6}"
            f"{n:>8}"
            f"{len(wins_y):>5}/{len(loss_y):>4}"
            f"{pw:>8.1f}%"
            f"{tot:>+12.4f}"
            f"{rr_disp:>10}"
        )
    print("  " + "─" * w)


# CSV columns appended after bars_held (entry-time factor snapshot)
ENTRY_LOG_CSV_COLUMNS = [
    "entry_rvol",
    "entry_change_24h_pct",
    "entry_change_4h_pct",
    "entry_volume_24h_usd",
    "entry_market_cap_usd",
    "entry_float_turnover",
    "entry_btc_4h_change_pct",
    "entry_supply_pct",
    "entry_vwap_distance_pct",
    "entry_htf_rsi",
    "entry_atr_pct",
    "entry_utc_hour",
    "entry_day_of_week",
    "entry_year_month",
]

_VWAP_LOG_STRATEGIES = frozenset({"vwap_meanrev", "vwap_meanrev_1h", "pullback_vwap"})


def _format_entry_log_cell(value: Any) -> str:
    """Format one entry-log field for CSV; missing → empty string."""
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:
            return ""
        return f"{value:.6g}"
    if isinstance(value, int):
        return str(value)
    return str(value)


def _signed_vwap_distance_pct(bars: list[Bar], cfg: dict) -> Optional[float]:
    """Signed % distance from VWAP (positive = above VWAP), screener convention."""
    if len(bars) < 2:
        return None
    lookback = cfg.get("anchored_vwap_lookback", DEFAULT_CONFIG["anchored_vwap_lookback"])
    session_vwap, anchored_vwap = _compute_vwap(bars, lookback)
    vwap = anchored_vwap if anchored_vwap else session_vwap
    if not vwap or vwap <= 0:
        return None
    price = bars[-1].close
    return (price - vwap) / vwap * 100.0


def _entry_atr_pct(bars: list[Bar], entry_price: float, cfg: dict) -> Optional[float]:
    if not entry_price or entry_price <= 0 or len(bars) < 3:
        return None
    period = cfg.get("atr_period", DEFAULT_CONFIG["atr_period"])
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    atr = calculate_atr(highs, lows, closes, period=period)
    if not atr:
        return None
    return atr / entry_price * 100.0


def _apply_entry_time_derivatives(trade: Trade) -> None:
    et = trade.entry_time
    if et.tzinfo is None:
        et = et.replace(tzinfo=timezone.utc)
    else:
        et = et.astimezone(timezone.utc)
    trade.entry_utc_hour = et.hour
    trade.entry_day_of_week = et.weekday()
    trade.entry_year_month = et.strftime("%Y-%m")


def _populate_trade_entry_log(
    trade: Trade,
    bars: list[Bar],
    *,
    btc_bars: Optional[list[Bar]],
    interval_min: int,
    supply: Optional[float],
    cfg: dict,
    strategy: str,
    rvol_lb_override: Optional[int] = None,
    htf_bars: Optional[list[Bar]] = None,
    relax: bool = False,
) -> None:
    """
    Fill Trade entry-log fields from OHLCV + pipeline grader (logging only).
    market_cap, supply_ratio, float_turnover need external static data → left empty.
    """
    graded = _grade_pair_at_bar(
        bars,
        btc_bars or [],
        interval_min,
        supply,
        relax=relax,
        rvol_lb_override=rvol_lb_override,
        pipeline_strategy=strategy if strategy == "meanrev" else None,
    )
    pillars = graded.get("pillars") or {}
    d1 = pillars.get("d1_rvol") or {}
    d2 = pillars.get("d2_momentum") or {}
    d3 = pillars.get("d3_volume") or {}
    d4 = pillars.get("d4_btc") or {}

    trade.entry_rvol = d1.get("value")
    trade.entry_change_24h_pct = d2.get("value")
    trade.entry_change_4h_pct = d2.get("value_4h")
    vol = d3.get("value")
    trade.entry_volume_24h_usd = float(vol) if vol is not None else None
    trade.entry_btc_4h_change_pct = d4.get("value")

    trade.entry_market_cap_usd = None
    trade.entry_supply_pct = None
    trade.entry_float_turnover = None

    if strategy in _VWAP_LOG_STRATEGIES:
        trade.entry_vwap_distance_pct = _signed_vwap_distance_pct(bars, cfg)
    if strategy in ("vwap_meanrev", "vwap_meanrev_1h"):
        rsi_period = cfg.get("rsi_period", DEFAULT_CONFIG["rsi_period"])
        trade.entry_htf_rsi = _vwap_htf_rsi_at(htf_bars, rsi_period, trade.entry_time)

    trade.entry_atr_pct = _entry_atr_pct(bars, trade.entry_price, cfg)
    _apply_entry_time_derivatives(trade)


def write_csv(trades: list[Trade], output_path: str, cfg: dict) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "trade_num", "symbol", "grade", "side", "entry_time", "exit_time",
            "entry_price", "exit_price", "stop_loss", "tp1_price", "tp2_price",
            "qty", "tp1_hit", "pnl_usd", "pnl_pct", "exit_reason", "bars_held",
            *ENTRY_LOG_CSV_COLUMNS,
        ])
        if not trades:
            print(f"\n  Trades CSV: {output_path} (header only — no trades)")
            return
        for t in trades:
            notional = t.entry_price * t.qty
            pnl_pct  = (t.pnl_usd / notional * 100.0) if notional else 0.0
            writer.writerow([
                t.trade_num,
                t.symbol,
                t.grade,
                t.side,
                t.entry_time.isoformat(),
                t.exit_time.isoformat() if t.exit_time else "",
                round(t.entry_price, 8),
                round(t.exit_price, 8) if t.exit_price else "",
                round(t.stop_loss,   8),
                round(t.tp1_price,   8),
                round(t.tp2_price,   8),
                round(t.qty,         8),
                t.tp1_hit,
                round(t.pnl_usd,     6),
                round(pnl_pct,       4),
                t.exit_reason,
                t.bars_held,
                *[_format_entry_log_cell(getattr(t, col)) for col in ENTRY_LOG_CSV_COLUMNS],
            ])
    print(f"\n  Trades CSV: {output_path}")


def _merge_vwap_cli_overrides(cfg: dict, args: Any, strategy: str) -> None:
    """Apply CLI overrides for VWAP mean-reversion strategies."""
    if strategy not in ("vwap_meanrev", "vwap_meanrev_1h"):
        return
    if getattr(args, "rsi_oversold", None) is not None:
        cfg["rsi_oversold"] = args.rsi_oversold
    if getattr(args, "long_min_volume_ratio", None) is not None:
        cfg["long_min_volume_ratio"] = args.long_min_volume_ratio
    if getattr(args, "htf_rsi_long_max", None) is not None:
        cfg["htf_rsi_long_max"] = args.htf_rsi_long_max
    if getattr(args, "htf_rsi_bars_interval", None) is not None:
        cfg["htf_rsi_bars_interval"] = args.htf_rsi_bars_interval
    if getattr(args, "atr_stop_mult", None) is not None:
        cfg["atr_stop_mult"] = args.atr_stop_mult
    if getattr(args, "breakeven_requires_tp1", None) is not None:
        cfg["breakeven_requires_tp1"] = args.breakeven_requires_tp1
    if getattr(args, "long_only", None) is not None:
        cfg["long_only"] = args.long_only


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=True)
            sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Crypto Strategy Backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python backtest.py --days 30                              # all-pairs pipeline (Binance OHLC)\n"
            "  python backtest.py --interval 4h --years 5 --strategy htf_trend --all-pairs\n"
            "  python backtest.py --symbol SNX/USD --days 60            # single-symbol\n"
            "  python backtest.py --universe SNX/USD,AXS/USD --days 60  # pipeline, explicit universe\n"
            "  python backtest.py --days 30 --relax-pillars --debug-signal"
        ),
    )
    parser.add_argument("--symbol",
                        help="Single trading pair for single-symbol mode. "
                             "Omit to run pipeline mode over the full Kraken USD universe (default).")
    parser.add_argument("--universe",
                        help="Comma-separated pairs for pipeline mode (explicit subset), "
                             "e.g. SNX/USD,AXS/USD,HNT/USD. Omit for the full Kraken universe.")
    parser.add_argument("--days",             type=int,   default=60,
                        help="History window in days (default: 60). Ignored when --years is set.")
    parser.add_argument(
        "--years",
        type=float,
        default=None,
        help="History window in years (~365.2425 d/year). When set, overrides --days.",
    )
    parser.add_argument("--interval",         default=None,
                        choices=list(INTERVAL_MAP.keys()),
                        help="Bar interval (default: 15m for --symbol, 4h for --universe)")
    parser.add_argument("--starting-equity",  type=float, default=500.0,
                        help="Starting paper balance in USD (default: 500)")
    parser.add_argument("--risk-pct",         type=float, default=1.0,
                        help="Risk per trade as %% of equity (default: 1.0)")
    parser.add_argument("--output",           default="backtest_trades.csv",
                        help="CSV output path (default: backtest_trades.csv)")
    parser.add_argument("--no-cache",         action="store_true",
                        help="Force re-fetch OHLCV (ignore disk cache)")
    parser.add_argument(
        "--no-long-only",
        dest="long_only",
        action="store_false",
        default=None,
        help="Allow short signals (default: long-only enforced by strategy config)",
    )
    parser.add_argument("--supply",
                        help="Override circulating supply (e.g. SNX/USD=332000000,AXS/USD=68000000)")
    parser.add_argument("--relax-pillars",  action="store_true",
                        help=(
                            "Lower pillar thresholds for small paper accounts / low-volume pairs: "
                            "hard floor $10K, RVOL ≥ 1.5×, D3 min $100K, C-grade entries at 0.25× size"
                        ))
    parser.add_argument("--dev-threshold",  type=float, default=None,
                        help="Override dev_threshold_pct (default: 2.0). "
                             "Lower values = more entry signals (e.g. 1.0, 1.5, 2.5)")
    parser.add_argument(
        "--rsi-oversold",
        type=float,
        default=None,
        help="Override rsi_oversold for vwap_meanrev / vwap_meanrev_1h (default: 30).",
    )
    parser.add_argument(
        "--long-min-volume-ratio",
        type=float,
        default=None,
        dest="long_min_volume_ratio",
        help="Require signal-bar volume >= this × volume SMA for VWAP long entries (Task L option B).",
    )
    parser.add_argument(
        "--htf-rsi-long-max",
        type=float,
        default=None,
        dest="htf_rsi_long_max",
        help="Require HTF RSI <= this for VWAP longs; uses --htf-rsi-bars-interval klines (Task L option C).",
    )
    parser.add_argument(
        "--htf-rsi-bars-interval",
        default=None,
        dest="htf_rsi_bars_interval",
        choices=list(INTERVAL_MAP.keys()),
        help="HTF kline interval for --htf-rsi-long-max (default: 4h from cfg).",
    )
    parser.add_argument(
        "--atr-stop-mult",
        type=float,
        default=None,
        dest="atr_stop_mult",
        help="Override atr_stop_mult for VWAP mean-reversion strategies (default: 1.5).",
    )
    parser.add_argument(
        "--min-grade",
        default="B",
        choices=["A+", "A", "B", "C"],
        help="Minimum pipeline grade required for new entries (default: B).",
    )
    parser.add_argument(
        "--breakeven-requires-tp1",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="breakeven_requires_tp1",
        help="Move stop to breakeven only after TP1 partial fill (default: true).",
    )
    parser.add_argument("--all-pairs",      action="store_true",
                        help="Explicit flag to use the full Kraken USD universe in pipeline mode. "
                             "This is now the DEFAULT when --symbol and --universe are both omitted.")
    parser.add_argument("--strategy",       default="vwap_meanrev",
                        choices=["vwap_meanrev", "vwap_meanrev_1h", "pullback_vwap", "meanrev",
                                 "volatility_breakout", "htf_trend",
                                 "bull_flag", "bull_flag_1m", "bull_flag_5m", "bull_flag_1h",
                                 "swing_bull_flag"],
                        help="Strategy to backtest (default: vwap_meanrev). "
                             "vwap_meanrev_1h is the 1h-chart instance (same logic as vwap_meanrev). "
                             "bull_flag is an alias for bull_flag_5m (primary I1). "
                             "Works in both single-symbol and pipeline mode.")
    parser.add_argument("--debug-signal",  action="store_true",
                        help="Print per-bar rejection reasons for the selected pair. "
                             "vwap_meanrev: DEV/RSI/REVERSAL  meanrev: BB/RSI/ADX/ATR  "
                             "pullback_vwap: INITIAL_MOVE/DEV/VOL  "
                             "volatility_breakout: COMPRESS/BREAKOUT/VOL/CANDLE  "
                             "htf_trend: EMA200/PULLBACK/EMA50/CANDLE")
    args = parser.parse_args()

    # Primary I1 name from docs / TASK D — canonical internal id is bull_flag_5m
    if args.strategy == "bull_flag":
        args.strategy = "bull_flag_5m"

    if args.symbol is None and args.universe is None:
        args.all_pairs = True  # default: full Kraken USD universe

    span_days, hist_label = _effective_span_days_and_hist_label(args)

    pipeline_mode = args.universe is not None or args.all_pairs

    # ── Pipeline mode ──────────────────────────────────────────────────────────
    if pipeline_mode:
        interval = args.interval or PIPELINE_DEFAULT_INTERVAL
        if args.interval is None:
            print(f"  [pipeline] Defaulting to --interval {interval} "
                  f"(full depth from Binance; use explicit --interval when needed)")
        interval_min = INTERVAL_MAP[interval]

        if args.all_pairs:
            print("\nFetching universe from Kraken AssetPairs endpoint…")
            universe = fetch_kraken_usd_universe()
            print(f"  Found {len(universe)} online USD pairs after filtering stablecoins/wrapped tokens")
        else:
            universe = [s.strip() for s in args.universe.split(",")]

        # Build supply overrides from --supply flag and hardcoded table
        supply_overrides: dict[str, float] = {}
        if args.supply:
            for item in args.supply.split(","):
                sym, val = item.split("=", 1)
                supply_overrides[sym.strip()] = float(val.strip())
        supplies: dict[str, float] = {}
        for sym in universe:
            s = _get_supply(sym, supply_overrides)
            if s is not None:
                supplies[sym] = s

        relax = args.relax_pillars
        _pipeline_labels = {
            "vwap_meanrev":        "VWAP Mean Reversion",
            "vwap_meanrev_1h":     "VWAP Mean Reversion (1h)",
            "pullback_vwap":       "Pullback to VWAP",
            "meanrev":             "Mean Reversion (BB+RSI+ADX)",
            "volatility_breakout": "Volatility Breakout",
            "htf_trend":           "HTF Trend Pullback",
            "bull_flag_1m":        "Bull Flag + Momentum Pullback (1m)",
            "bull_flag_5m":        "Bull Flag + Momentum Pullback (5m)",
            "bull_flag_1h":        "Bull Flag + Momentum Pullback (1h)",
            "swing_bull_flag":     "Swing Bull Flag (W2, 4h)",
        }
        pipeline_strategy_label = _pipeline_labels.get(args.strategy, args.strategy)
        print(f"\n{pipeline_strategy_label} — PIPELINE BACKTEST")
        if args.all_pairs:
            print(f"Universe : {len(universe)} pairs (--all-pairs)")
        else:
            print(f"Universe : {', '.join(universe)}")
        print(f"Strategy : {args.strategy}")
        print(f"History  : {hist_label}  |  Interval: {interval}")
        print(f"Equity   : ${args.starting_equity:.2f}  |  Risk/trade: {args.risk_pct}%")
        if relax:
            print(f"Mode     : RELAXED pillars (floor $10K, RVOL≥1.5×, D3≥$100K, C=0.25×)")

        # Fetch OHLCV for all universe pairs + BTC (for D4)
        all_bar_data: dict[str, list[Bar]] = {}
        fetch_list = list(universe) + ([BTC_KRAKEN] if BTC_KRAKEN not in universe else [])

        for i, sym in enumerate(fetch_list):
            print(f"\n[{sym}]")
            try:
                bars = fetch_binance_ohlcv_for_backtest(
                    sym, interval, span_days, no_cache=args.no_cache
                )
                all_bar_data[sym] = bars
                if bars:
                    print(f"  Date range: {bars[0].timestamp.date()} → {bars[-1].timestamp.date()}")
            except Exception as e:
                print(f"  ERROR fetching {sym}: {e}")
                all_bar_data[sym] = []
            # Polite rate-limit pause between live fetches in bulk mode
            if args.all_pairs and i < len(fetch_list) - 1:
                time.sleep(0.25)

        btc_bars = all_bar_data.get(BTC_KRAKEN, [])
        universe_bars = {sym: all_bar_data[sym] for sym in universe if all_bar_data.get(sym)}

        vb_btc_daily_bars: Optional[list[Bar]] = None
        htf_btc_daily_bars: Optional[list[Bar]] = None
        if args.strategy == "volatility_breakout":
            print(f"\n[{BTC_KRAKEN}] (1d — BTC EMA macro gate)")
            try:
                vb_btc_daily_bars = fetch_binance_ohlcv_for_backtest(
                    BTC_KRAKEN, "1d", span_days, no_cache=args.no_cache
                )
                if vb_btc_daily_bars:
                    print(
                        f"  Date range: {vb_btc_daily_bars[0].timestamp.date()} → "
                        f"{vb_btc_daily_bars[-1].timestamp.date()} ({len(vb_btc_daily_bars)} bars)"
                    )
                else:
                    print("  WARNING: no daily BTC bars — VB entries will be blocked (fail-closed)")
            except Exception as e:
                print(f"  ERROR fetching daily BTC: {e}")
        elif args.strategy == "htf_trend":
            print(f"\n[{BTC_KRAKEN}] (1d — HTF BTC EMA macro gate)")
            try:
                htf_btc_daily_bars = fetch_binance_ohlcv_for_backtest(
                    BTC_KRAKEN, "1d", span_days, no_cache=args.no_cache
                )
                if htf_btc_daily_bars:
                    print(
                        f"  Date range: {htf_btc_daily_bars[0].timestamp.date()} → "
                        f"{htf_btc_daily_bars[-1].timestamp.date()} ({len(htf_btc_daily_bars)} bars)"
                    )
                else:
                    print("  WARNING: no daily BTC bars — HTF entries will be blocked (fail-closed)")
            except Exception as e:
                print(f"  ERROR fetching daily BTC: {e}")

        if args.strategy == "pullback_vwap":
            cfg = PULLBACK_VWAP_DEFAULT_CONFIG.copy()
        elif args.strategy == "meanrev":
            cfg = MEANREV_DEFAULT_CONFIG.copy()
        elif args.strategy == "volatility_breakout":
            cfg = VOLATILITY_BREAKOUT_DEFAULT_CONFIG.copy()
            _merge_vb_btc_bull_env_into_cfg(cfg)
        elif args.strategy == "htf_trend":
            cfg = HTF_TREND_DEFAULT_CONFIG.copy()
            _merge_htf_btc_bull_env_into_cfg(cfg)
        elif args.strategy in ("bull_flag_1m", "bull_flag_5m", "bull_flag_1h"):
            cfg = bull_flag_backtest_cfg(args.strategy)
        elif args.strategy == "swing_bull_flag":
            cfg = swing_bull_flag_backtest_cfg()
        elif args.strategy == "vwap_meanrev_1h":
            cfg = DEFAULT_CONFIG.copy()
            cfg["max_bars_in_trade"] = 12
        else:
            cfg = DEFAULT_CONFIG.copy()
            if args.dev_threshold is not None:
                cfg["dev_threshold_pct"] = args.dev_threshold
                print(f"  dev_threshold_pct overridden → {args.dev_threshold}%")

        _merge_vwap_cli_overrides(cfg, args, args.strategy)
        cfg["breakeven_requires_tp1"] = args.breakeven_requires_tp1

        vwap_htf_by_sym: Optional[dict[str, list[Bar]]] = None
        if (
            args.strategy in ("vwap_meanrev", "vwap_meanrev_1h")
            and cfg.get("htf_rsi_long_max") is not None
        ):
            htf_iv = cfg.get("htf_rsi_bars_interval") or "4h"
            vwap_htf_by_sym = {}
            print(f"\n[VWAP HTF RSI] Fetching {htf_iv} bars ({hist_label}) for HTF RSI gate…")
            for j, sym in enumerate(fetch_list):
                print(f"  [{sym}] {htf_iv}")
                try:
                    h_rows = fetch_binance_ohlcv_for_backtest(
                        sym, htf_iv, span_days, no_cache=args.no_cache
                    )
                    vwap_htf_by_sym[sym] = h_rows
                except Exception as e:
                    print(f"    ERROR: {e}")
                    vwap_htf_by_sym[sym] = []
                if args.all_pairs and j < len(fetch_list) - 1:
                    time.sleep(0.25)

        _daily_ohlcv_cache: dict[str, list[Bar]] = {}

        def _pipeline_daily_fetch(sym: str) -> list[Bar]:
            if sym not in _daily_ohlcv_cache:
                _daily_ohlcv_cache[sym] = fetch_binance_ohlcv_for_backtest(
                    sym, "1d", span_days, no_cache=args.no_cache
                )
            return _daily_ohlcv_cache[sym]

        _daily_fetcher: Optional[Callable[[str], list[Bar]]] = (
            _pipeline_daily_fetch if args.strategy == "swing_bull_flag" else None
        )

        trades, equity_curve = run_pipeline_backtest(
            universe_bars,
            btc_bars,
            universe,
            supplies,
            cfg,
            args.starting_equity,
            args.risk_pct,
            cfg.get("long_only", True),
            interval_min,
            relax=relax,
            strategy=args.strategy,
            debug_signal=args.debug_signal,
            daily_fetcher=_daily_fetcher,
            vb_btc_daily_bars=vb_btc_daily_bars,
            htf_btc_daily_bars=htf_btc_daily_bars,
            vwap_htf_bars_by_symbol=vwap_htf_by_sym,
            min_grade=args.min_grade,
        )

        print_pipeline_metrics(
            trades, equity_curve, args.starting_equity,
            universe, hist_label, interval,
            strategy=args.strategy,
        )
        if trades:
            print_pipeline_calendar_year_breakdown(trades)
        write_csv(trades, args.output, cfg)

    # ── Single-symbol mode ─────────────────────────────────────────────────────
    else:
        if args.interval is not None:
            interval = args.interval
        elif args.strategy == "swing_bull_flag":
            interval = "4h"
        elif args.strategy == "bull_flag_1m":
            interval = "1m"
        elif args.strategy == "bull_flag_5m":
            interval = "5m"
        elif args.strategy == "bull_flag_1h":
            interval = "1h"
        elif args.strategy == "vwap_meanrev_1h":
            interval = "1h"
        else:
            interval = "15m"

        strategy_label = {
            "vwap_meanrev":        "VWAP Mean Reversion",
            "vwap_meanrev_1h":     "VWAP Mean Reversion (1h)",
            "pullback_vwap":       "Pullback to VWAP",
            "meanrev":             "Mean Reversion (BB + RSI + ADX)",
            "volatility_breakout": "Volatility Breakout (Compression → Expansion)",
            "htf_trend":           "HTF Trend Pullback Continuation",
            "bull_flag_1m":        "Bull Flag + Momentum Pullback (1m)",
            "bull_flag_5m":        "Bull Flag + Momentum Pullback (5m)",
            "bull_flag_1h":        "Bull Flag + Momentum Pullback (1h)",
            "swing_bull_flag":     "Swing Bull Flag (W2, 4h)",
        }.get(args.strategy, args.strategy)

        print(f"\n{strategy_label} Backtester")
        print(f"Symbol: {args.symbol}  |  History: {hist_label}  |  Interval: {interval}")
        print(f"Equity: ${args.starting_equity:.2f}  |  Risk/trade: {args.risk_pct}%\n")

        bars = fetch_binance_ohlcv_for_backtest(
            args.symbol, interval, span_days, no_cache=args.no_cache
        )

        if len(bars) < WARMUP_BARS + 10:
            print(f"ERROR: Only {len(bars)} bars fetched — need at least {WARMUP_BARS + 10}.")
            sys.exit(1)

        print(f"  Date range : {bars[0].timestamp.date()} → {bars[-1].timestamp.date()}")

        if args.strategy == "pullback_vwap":
            cfg = PULLBACK_VWAP_DEFAULT_CONFIG.copy()
            base_cfg_for_csv = PULLBACK_VWAP_DEFAULT_CONFIG
        elif args.strategy == "meanrev":
            cfg = MEANREV_DEFAULT_CONFIG.copy()
            base_cfg_for_csv = MEANREV_DEFAULT_CONFIG
        elif args.strategy == "volatility_breakout":
            cfg = VOLATILITY_BREAKOUT_DEFAULT_CONFIG.copy()
            _merge_vb_btc_bull_env_into_cfg(cfg)
            base_cfg_for_csv = VOLATILITY_BREAKOUT_DEFAULT_CONFIG
        elif args.strategy == "htf_trend":
            cfg = HTF_TREND_DEFAULT_CONFIG.copy()
            base_cfg_for_csv = HTF_TREND_DEFAULT_CONFIG
            _merge_htf_btc_bull_env_into_cfg(cfg)
        elif args.strategy in ("bull_flag_1m", "bull_flag_5m", "bull_flag_1h"):
            cfg = bull_flag_backtest_cfg(args.strategy)
            base_cfg_for_csv = cfg
        elif args.strategy == "swing_bull_flag":
            cfg = swing_bull_flag_backtest_cfg()
            base_cfg_for_csv = cfg
        elif args.strategy == "vwap_meanrev_1h":
            cfg = DEFAULT_CONFIG.copy()
            cfg["max_bars_in_trade"] = 12
            base_cfg_for_csv = cfg
        else:
            cfg = DEFAULT_CONFIG.copy()
            base_cfg_for_csv = DEFAULT_CONFIG
            if args.dev_threshold is not None:
                cfg["dev_threshold_pct"] = args.dev_threshold

        _merge_vwap_cli_overrides(cfg, args, args.strategy)
        cfg["breakeven_requires_tp1"] = args.breakeven_requires_tp1

        swing_daily: Optional[list[Bar]] = None
        swing_btc: Optional[list[Bar]] = None
        vb_btc_daily_s: Optional[list[Bar]] = None
        htf_btc_daily_s: Optional[list[Bar]] = None
        if args.strategy == "swing_bull_flag":
            swing_daily = fetch_binance_ohlcv_for_backtest(
                args.symbol, "1d", span_days, no_cache=args.no_cache
            )
            swing_btc = fetch_binance_ohlcv_for_backtest(
                BTC_KRAKEN, interval, span_days, no_cache=args.no_cache
            )
        elif args.strategy == "volatility_breakout":
            try:
                vb_btc_daily_s = fetch_binance_ohlcv_for_backtest(
                    BTC_KRAKEN, "1d", span_days, no_cache=args.no_cache
                )
            except Exception as e:
                print(f"  WARNING: daily BTC fetch failed: {e}")
        elif args.strategy == "htf_trend":
            try:
                htf_btc_daily_s = fetch_binance_ohlcv_for_backtest(
                    BTC_KRAKEN, "1d", span_days, no_cache=args.no_cache
                )
            except Exception as e:
                print(f"  WARNING: daily BTC fetch failed: {e}")

        vwap_htf_single: Optional[list[Bar]] = None
        if (
            args.strategy in ("vwap_meanrev", "vwap_meanrev_1h")
            and cfg.get("htf_rsi_long_max") is not None
        ):
            htf_iv = cfg.get("htf_rsi_bars_interval") or "4h"
            try:
                vwap_htf_single = fetch_binance_ohlcv_for_backtest(
                    args.symbol, htf_iv, span_days, no_cache=args.no_cache
                )
                nhtf = len(vwap_htf_single or [])
                print(f"  HTF ({htf_iv}) for RSI gate: {nhtf} bars")
            except Exception as e:
                print(f"  WARNING: HTF fetch for RSI gate failed: {e}")

        btc_bars_single: Optional[list[Bar]] = None
        try:
            btc_bars_single = fetch_binance_ohlcv_for_backtest(
                BTC_KRAKEN, interval, span_days, no_cache=args.no_cache
            )
        except Exception as e:
            print(f"  WARNING: BTC bars for entry-log D4 failed: {e}")

        bpd_single = max(1, 1440 // INTERVAL_MAP[interval])
        ideal_rvol_lb = bpd_single * PIPELINE_RVOL_DAYS
        max_rvol_lb = max(bpd_single * 7, int(len(bars) * 0.5))
        rvol_lb_single = min(ideal_rvol_lb, max_rvol_lb)

        trades, equity_curve = run_backtest(
            bars,
            cfg,
            args.starting_equity,
            args.risk_pct,
            cfg.get("long_only", True),
            strategy=args.strategy,
            debug_signal=args.debug_signal,
            swing_daily_bars=swing_daily,
            swing_btc_bars=swing_btc,
            vb_btc_daily_bars=vb_btc_daily_s,
            htf_btc_daily_bars=htf_btc_daily_s,
            vwap_htf_bars=vwap_htf_single,
            symbol=args.symbol,
            btc_bars=btc_bars_single,
            supply=_get_supply(args.symbol, {}),
            interval_min=INTERVAL_MAP[interval],
            rvol_lb_override=rvol_lb_single,
        )

        print_metrics(
            trades, equity_curve, args.starting_equity,
            args.symbol, hist_label, interval,
            strategy=args.strategy,
        )
        write_csv(trades, args.output, base_cfg_for_csv)


if __name__ == "__main__":
    main()
