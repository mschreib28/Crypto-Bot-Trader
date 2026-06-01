"""3-Stage 5-Pillar screening pipeline implementing CLAUDE.md criteria.

Stage 1 (static, cached 20h per symbol):
  S1 — Circulating Supply < 5 billion tokens
  S2 — Price $0.005 – $10.00
  S3 — Listed > 30 days, volume on ≥ 20 of last 30 days

Hard floor (absolute, not a pillar): 24h volume > $100K

Stage 2 (dynamic, every scan cycle):
  D1 — RVOL > 3× 30-day average
  D2 — Price momentum +8%/24h OR +5%/4h
  D3 — 24h volume $500K – $50M
  D4 — BTC not down > 4% in last 4h

Grade:
  A+ — all 3 static + all 4 dynamic
  A  — all 3 static + 3/4 dynamic
  B  — all 3 static + 2/4 dynamic
  C  — all 3 static + 1/4 dynamic
  F  — failed any static OR 0 dynamic
"""

import json
import logging
import requests
from typing import Any, Dict, List, Optional, Tuple

from backend.redis import get_redis_client
from backend.redis.keys import (
    PIPELINE_BTC_4H_KEY,
    PIPELINE_BTC_4H_TTL,
    PIPELINE_BTC_DAILY_CLOSES_KEY,
    PIPELINE_BTC_DAILY_CLOSES_TTL,
    PIPELINE_STAGE1_KEY,
    PIPELINE_STAGE1_TTL,
)

logger = logging.getLogger(__name__)

# Hard floor — absolute minimum, checked before Stage 1
HARD_FLOOR_VOLUME_USD = 100_000  # $100K 24h volume

# Stage 1 thresholds
S1_MAX_CIRCULATING_SUPPLY = 5_000_000_000  # 5 billion tokens
S2_MIN_PRICE = 0.005
S2_MAX_PRICE = 10.0
S3_MIN_LISTING_DAYS = 30
S3_MIN_ACTIVE_DAYS = 20

# Stage 2 thresholds
D1_MIN_RVOL = 3.0       # 3× 30-day average (300%)
D2_MIN_24H_PCT = 8.0    # +8% in 24h
D2_MIN_4H_PCT = 5.0     # +5% in last 4h
D3_MIN_VOLUME = 500_000      # $500K
D3_MAX_VOLUME = 50_000_000   # $50M
D4_MAX_BTC_DROP = -4.0       # BTC not down more than -4% in 4h

# E1 — float proxy: ≥5% of market cap traded in 24h (soft grade gate)
FLOAT_PROXY_MIN_TURNOVER = 0.05

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"


def _symbol_to_kraken_pair(symbol: str) -> str:
    pair = symbol.replace("/", "")
    if pair.startswith("BTC"):
        pair = "XBT" + pair[3:]
    return pair


# ── Hard floor ────────────────────────────────────────────────────────────────

def check_hard_floor(volume_24h: Optional[float]) -> bool:
    """True if 24h volume meets the $100K absolute minimum."""
    return volume_24h is not None and volume_24h >= HARD_FLOOR_VOLUME_USD


# ── Stage 1 helpers ───────────────────────────────────────────────────────────

def _fetch_s3_listing_data(symbol: str) -> Tuple[bool, Optional[int]]:
    """
    Fetch Kraken daily OHLCV to check listing age and activity.

    Returns (passes, active_days_count).
    Fails open (returns True, None) when the API is unreachable so a single
    Kraken outage doesn't filter good pairs.
    """
    try:
        pair = _symbol_to_kraken_pair(symbol)
        resp = requests.get(
            KRAKEN_OHLC_URL,
            params={"pair": pair, "interval": 1440},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            return True, None

        result_key = next(
            (k for k in data.get("result", {}) if k != "last"), None
        )
        if not result_key:
            return True, None

        bars = data["result"][result_key]
        # Kraken daily format: [time, open, high, low, close, vwap, volume, count]
        recent = bars[-S3_MIN_LISTING_DAYS:]
        if len(recent) < S3_MIN_LISTING_DAYS:
            return False, len(recent)

        active_days = sum(1 for bar in recent if float(bar[6]) > 0)
        return active_days >= S3_MIN_ACTIVE_DAYS, active_days

    except Exception as e:
        logger.debug(f"[PIPELINE] S3 fetch failed for {symbol}: {e}")
        return True, None  # fail-open


# ── Stage 1 main ─────────────────────────────────────────────────────────────

def check_stage1_static(
    symbol: str,
    current_price: Optional[float] = None,
    circulating_supply: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run Stage 1 static checks with 20h Redis cache.

    Returns:
        {
            "all_pass": bool,
            "pillars": {
                "s1_supply":  {"pass": bool, "value": float|None},
                "s2_price":   {"pass": bool, "value": float|None},
                "s3_listing": {"pass": bool, "value": int|None},
            },
            "cached": bool,
        }
    """
    cache_key = PIPELINE_STAGE1_KEY.format(symbol=symbol)

    # Return cached result, refreshing the price check with fresh data.
    try:
        client = get_redis_client()
        raw = client.get(cache_key)
        if raw:
            cached = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            if current_price is not None:
                s2_pass = S2_MIN_PRICE <= current_price <= S2_MAX_PRICE
                cached["pillars"]["s2_price"] = {"pass": s2_pass, "value": current_price}
                cached["all_pass"] = all(p["pass"] for p in cached["pillars"].values())
            else:
                # Missing price: fail closed — do not treat stale cache as passing S2
                cached["pillars"]["s2_price"] = {"pass": False, "value": None}
                cached["all_pass"] = False
            cached["cached"] = True
            return cached
    except Exception as e:
        logger.debug(f"[PIPELINE] Stage1 cache read failed for {symbol}: {e}")

    # S1: circulating supply (missing data → fail closed)
    if circulating_supply is not None:
        s1_pass = circulating_supply < S1_MAX_CIRCULATING_SUPPLY
        s1_value: Optional[float] = circulating_supply
    else:
        s1_pass = False
        s1_value = None

    # S2: price range (missing data → fail closed)
    if current_price is not None:
        s2_pass = S2_MIN_PRICE <= current_price <= S2_MAX_PRICE
        s2_value: Optional[float] = current_price
    else:
        s2_pass = False
        s2_value = None

    # S3: listing age + activity (API call, cached below)
    s3_pass, s3_active_days = _fetch_s3_listing_data(symbol)

    pillars: Dict[str, Any] = {
        "s1_supply":  {"pass": s1_pass, "value": s1_value},
        "s2_price":   {"pass": s2_pass, "value": s2_value},
        "s3_listing": {"pass": s3_pass, "value": s3_active_days},
    }
    all_pass = all(p["pass"] for p in pillars.values())

    result = {"all_pass": all_pass, "pillars": pillars, "cached": False}

    try:
        client = get_redis_client()
        client.setex(cache_key, PIPELINE_STAGE1_TTL, json.dumps(result))
    except Exception as e:
        logger.debug(f"[PIPELINE] Stage1 cache write failed for {symbol}: {e}")

    return result


# ── BTC 4h change ─────────────────────────────────────────────────────────────

def compute_4h_change_from_1h_bars(bars_1h: List[Dict[str, Any]]) -> Optional[float]:
    """Compute 4h price change from the last 4 hourly bars."""
    if len(bars_1h) < 4:
        return None
    last_4 = bars_1h[-4:]
    open_price = float(last_4[0].get("open") or 0)
    close_price = float(last_4[-1].get("close") or 0)
    if open_price <= 0:
        return None
    return (close_price - open_price) / open_price * 100.0


def fetch_btc_4h_change() -> Optional[float]:
    """
    Fetch BTC 4h price change from Kraken public OHLC. Cached 5 min.

    Uses Kraken's 4h (240-min) interval endpoint; computes change from
    the most recent completed 4h bar (open → close).
    """
    try:
        client = get_redis_client()
        raw = client.get(PIPELINE_BTC_4H_KEY)
        if raw:
            return float(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:
        pass

    try:
        resp = requests.get(
            KRAKEN_OHLC_URL,
            params={"pair": "XBTUSD", "interval": 240},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            return None

        result_key = next(
            (k for k in data.get("result", {}) if k != "last"), None
        )
        if not result_key:
            return None

        bars = data["result"][result_key]
        if len(bars) < 2:
            return None

        # Use the second-to-last bar (last bar is always incomplete)
        bar = bars[-2]
        open_p = float(bar[1])
        close_p = float(bar[4])
        if open_p <= 0:
            return None

        change = (close_p - open_p) / open_p * 100.0

        try:
            client = get_redis_client()
            client.setex(PIPELINE_BTC_4H_KEY, PIPELINE_BTC_4H_TTL, str(change))
        except Exception:
            pass

        return change

    except Exception as e:
        logger.debug(f"[PIPELINE] BTC 4h fetch failed: {e}")
        return None


def fetch_btc_daily_closes(limit: int = 210) -> Optional[List[float]]:
    """
    Fetch BTC daily closes from Kraken public OHLC (newest last). Cached ~1h.

    interval 1440 = daily. Returns the last ``limit`` closes, or None on failure
    or if fewer than ``limit`` bars are available.
    """
    if limit < 2:
        return None

    try:
        client = get_redis_client()
        raw = client.get(PIPELINE_BTC_DAILY_CLOSES_KEY)
        if raw:
            text = raw.decode() if isinstance(raw, bytes) else raw
            closes = json.loads(text)
            if isinstance(closes, list) and len(closes) >= limit:
                return [float(x) for x in closes[-limit:]]
    except Exception:
        pass

    try:
        resp = requests.get(
            KRAKEN_OHLC_URL,
            params={"pair": "XBTUSD", "interval": 1440},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            return None

        result_key = next(
            (k for k in data.get("result", {}) if k != "last"), None
        )
        if not result_key:
            return None

        bars = data["result"][result_key]
        if len(bars) < limit:
            return None

        closes = [float(b[4]) for b in bars]
        try:
            client = get_redis_client()
            client.setex(
                PIPELINE_BTC_DAILY_CLOSES_KEY,
                PIPELINE_BTC_DAILY_CLOSES_TTL,
                json.dumps(closes),
            )
        except Exception:
            pass

        return closes[-limit:]
    except Exception as e:
        logger.debug(f"[PIPELINE] BTC daily fetch failed: {e}")
        return None


# ── Stage 2 dynamic ───────────────────────────────────────────────────────────

def check_stage2_dynamic(
    symbol: str,
    rvol_ratio: Optional[float],
    change_24h_pct: Optional[float],
    volume_24h: Optional[float],
    bars_1h: Optional[List[Dict[str, Any]]] = None,
    btc_4h_change: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run Stage 2 dynamic checks (no caching — runs every scan cycle).

    Returns:
        {
            "dynamic_passes": int,  # 0–4
            "pillars": {
                "d1_rvol":     {"pass": bool, "value": float|None},
                "d2_momentum": {"pass": bool, "value": float|None, "value_4h": float|None},
                "d3_volume":   {"pass": bool, "value": float|None},
                "d4_btc":      {"pass": bool, "value": float|None},
            }
        }
    """
    # D1: RVOL > 3×
    d1_pass = rvol_ratio is not None and rvol_ratio >= D1_MIN_RVOL

    # D2: +8%/24h OR +5%/4h
    momentum_4h = compute_4h_change_from_1h_bars(bars_1h) if bars_1h else None
    d2_pass = (
        (change_24h_pct is not None and change_24h_pct >= D2_MIN_24H_PCT)
        or (momentum_4h is not None and momentum_4h >= D2_MIN_4H_PCT)
    )

    # D3: volume $500K–$50M
    d3_pass = (
        volume_24h is not None
        and D3_MIN_VOLUME <= volume_24h <= D3_MAX_VOLUME
    )

    # D4: BTC not down > 4% in 4h (unknown → pass by default)
    if btc_4h_change is None:
        d4_pass = True
    else:
        d4_pass = btc_4h_change >= D4_MAX_BTC_DROP

    pillars: Dict[str, Any] = {
        "d1_rvol":     {"pass": d1_pass, "value": rvol_ratio},
        "d2_momentum": {
            "pass": d2_pass,
            "value": change_24h_pct,
            "value_4h": momentum_4h,
        },
        "d3_volume":   {"pass": d3_pass, "value": volume_24h},
        "d4_btc":      {"pass": d4_pass, "value": btc_4h_change},
    }
    dynamic_passes = sum(1 for p in pillars.values() if p["pass"])

    return {"dynamic_passes": dynamic_passes, "pillars": pillars}


# ── Grade computation ─────────────────────────────────────────────────────────

def compute_pipeline_grade(stage1_all_pass: bool, dynamic_passes: int) -> str:
    """
    Compute pipeline grade per CLAUDE.md:
      A+ — all 3 static + all 4 dynamic
      A  — all 3 static + 3/4 dynamic
      B  — all 3 static + 2/4 dynamic
      C  — all 3 static + 1/4 dynamic
      F  — failed any static OR 0 dynamic
    """
    if not stage1_all_pass or dynamic_passes == 0:
        return "F"
    if dynamic_passes >= 4:
        return "A+"
    if dynamic_passes == 3:
        return "A"
    if dynamic_passes == 2:
        return "B"
    return "C"  # dynamic_passes == 1


def grade_to_score(grade: str) -> float:
    """Map pipeline grade to a 0.0–1.0 numeric score for sort compatibility."""
    return {"A+": 1.0, "A": 0.85, "B": 0.70, "C": 0.55}.get(grade, 0.0)


def check_float_proxy(
    volume_24h_usd: Optional[float],
    market_cap_usd: Optional[float],
) -> bool:
    """
    Float proxy: did we trade ≥5% of market cap in 24h?

    volume_tokens / circulating_supply = volume_usd / market_cap
    """
    if not volume_24h_usd or not market_cap_usd or market_cap_usd <= 0:
        return False
    return (volume_24h_usd / market_cap_usd) >= FLOAT_PROXY_MIN_TURNOVER


def float_proxy_turnover(
    volume_24h_usd: Optional[float],
    market_cap_usd: Optional[float],
) -> Optional[float]:
    """Return 24h turnover ratio (volume / market cap), or None if not computable."""
    if not volume_24h_usd or not market_cap_usd or market_cap_usd <= 0:
        return None
    return volume_24h_usd / market_cap_usd


_GRADE_DOWN = {"A+": "A", "A": "B", "B": "C", "C": "C", "F": "F", "D": "D"}


def apply_float_proxy_soft_grade(grade: str, float_proxy_pass: bool) -> str:
    """Soft gate: failing float proxy drops one letter grade (not a hard blocker)."""
    if float_proxy_pass:
        return grade
    return _GRADE_DOWN.get(grade, grade)


# ── D2 momentum BUY gate (mean-reversion strategies exempt) ───────────────────

D2_EXEMPT_STRATEGY_NAMES = frozenset(
    {
        "vwap_meanrev",
        "vwap_meanreversion",
        "vwap_meanrev_1h",
        "meanrev",
        "mean-rev",
        "mean_rev",
        "mean_reversion",
    }
)


def _strategy_name_matches_d2_exempt(name_lower: str) -> bool:
    """True if strategy is mean-reversion and may enter without positive 24h momentum."""
    if name_lower in D2_EXEMPT_STRATEGY_NAMES:
        return True
    if "vwap_meanrev" in name_lower or "vwap_meanreversion" in name_lower:
        return True
    if name_lower == "meanrev" or name_lower.startswith("meanrev_"):
        return True
    if "mean-rev" in name_lower or "mean_rev" in name_lower or "mean_reversion" in name_lower:
        return True
    return False


def strategy_requires_d2_momentum(strategy_name: str) -> bool:
    """Momentum strategies must pass pipeline D2 at BUY; mean-reversion strategies are exempt."""
    name_lower = (strategy_name or "").strip().lower()
    if not name_lower:
        return True
    return not _strategy_name_matches_d2_exempt(name_lower)


def d2_momentum_passes(aplus_data: Optional[Dict[str, Any]]) -> bool:
    """Fail closed: require pillars.d2_momentum.pass when D2 gate applies."""
    if not aplus_data or not isinstance(aplus_data, dict):
        return False
    pillars = aplus_data.get("pillars")
    if not isinstance(pillars, dict):
        return False
    d2 = pillars.get("d2_momentum")
    if not isinstance(d2, dict):
        return False
    return bool(d2.get("pass"))


# ── Criteria definitions (served by /screener/criteria) ──────────────────────

PIPELINE_CRITERIA: Dict[str, Any] = {
    "hard_floor": {
        "label": "Hard Floor",
        "description": "24h volume > $100K",
        "rationale": "Below this, the pair is too illiquid to trade.",
    },
    "stage1": {
        "label": "Stage 1 — Static Filters (cached 20h)",
        "pillars": [
            {
                "id": "s1_supply",
                "label": "Circulating Supply",
                "description": "< 5 billion tokens",
                "rationale": (
                    "High supply dilutes demand impact. "
                    "No squeeze is possible above 5B tokens in circulation."
                ),
            },
            {
                "id": "s2_price",
                "label": "Price Range",
                "description": "$0.005 – $10.00",
                "rationale": (
                    "Eliminates dead/micro coins (below $0.005) and "
                    "large-caps with low volatility potential (above $10)."
                ),
            },
            {
                "id": "s3_listing",
                "label": "Market Activity",
                "description": "Listed > 30 days, volume on ≥ 20 of last 30 days",
                "rationale": (
                    "Filters zombie coins and brand-new listings "
                    "with no track record."
                ),
            },
        ],
    },
    "stage2": {
        "label": "Stage 2 — Dynamic Filters (every scan cycle)",
        "pillars": [
            {
                "id": "d1_rvol",
                "label": "Relative Volume",
                "description": "Current volume > 3× 30-day average",
                "rationale": (
                    "Core demand signal. Mirrors Ross Cameron's 5× RVOL "
                    "requirement adapted for crypto."
                ),
            },
            {
                "id": "d2_momentum",
                "label": "Price Momentum",
                "description": "Up 8%+ in 24h OR up 5%+ in last 4h",
                "rationale": (
                    "Already moving = confirmed demand entering the asset."
                ),
            },
            {
                "id": "d3_volume",
                "label": "Liquidity Sweet Spot",
                "description": "24h volume $500K – $50M",
                "rationale": (
                    "Too low = manipulation / slippage. "
                    "Too high = BTC-tier, insufficient edge."
                ),
            },
            {
                "id": "d4_btc",
                "label": "BTC Health",
                "description": "BTC not down more than 4% in last 4h",
                "rationale": (
                    "BTC dumps drag all crypto down regardless of "
                    "individual strength. One call covers all pairs."
                ),
            },
        ],
    },
    "enhancements": {
        "label": "Enhancement pillars (soft gates)",
        "pillars": [
            {
                "id": "e1_float_proxy",
                "label": "Float Proxy",
                "description": "24h volume ≥ 5% of market cap",
                "rationale": (
                    "Crypto equivalent of 5% of float traded — real "
                    "interest, not only whale flow. Failing drops one grade."
                ),
            },
        ],
    },
    "grades": [
        {
            "grade": "A+",
            "condition": "All 3 static + all 4 dynamic",
            "action": "Trade immediately, full size",
        },
        {
            "grade": "A",
            "condition": "All 3 static + 3 of 4 dynamic",
            "action": "Trade, normal size",
        },
        {
            "grade": "B",
            "condition": "All 3 static + 2 of 4 dynamic",
            "action": "Trade at 50% size",
        },
        {
            "grade": "C",
            "condition": "All 3 static + 1 of 4 dynamic",
            "action": "Watch only — no trade",
        },
        {
            "grade": "F",
            "condition": "Failed any static pillar OR 0 dynamic pillars",
            "action": "Ignore",
        },
    ],
}
