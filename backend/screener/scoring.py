"""A+ Scoring System for ranking trading pairs.

Crypto equivalent of Ross Cameron's 5 Pillars of Stock Selection.
Each pillar maps directly to a stock-market concept reframed for crypto:

  Ross's Pillar              Crypto Equivalent                      Weight
  ───────────────────────────────────────────────────────────────────────
  5× Relative Volume   →  RVOL (daily vs 50-day avg)                  30%
  Up 10%+ on the day   →  24h % Change (momentum)                     25%
  Low Float (<20M)     →  High Circ Supply (>80% = less dump risk)    20%
  $1–$20 price range   →  Market Cap sweet spot ($10M–$300M)          15%
  Execution quality    →  Bid-ask Spread (<10 bps)                    10%

Supply note: In crypto, HIGH circulating ratio = GOOD (most supply already
in circulation → less risk of sudden dump from unlocking locked tokens).
This is the inverse of traditional "low float" — we want tokens where the
market has already absorbed most of the supply.

Grade thresholds:
  A+ ≥ 0.85  — All 5 pillars strongly met (high-conviction setup)
  A  ≥ 0.70  — 4+ pillars met
  B  ≥ 0.55  — 3 pillars met
  C  ≥ 0.40  — 2 pillars met
  D  ≥ 0.25  — 1 pillar met
  F  < 0.25  — No meaningful signal
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Pillar 1: RVOL — Relative Volume vs 50-day daily average ─────────────────
RVOL_WEIGHT = 0.30
RVOL_FULL_THRESHOLD = 5.0    # ≥ 5×  (500%) — exceptional crowding
RVOL_HALF_THRESHOLD = 2.0    # ≥ 2×  (200%) — elevated interest

# ── Pillar 2: Momentum — 24h % price change ───────────────────────────────────
MOMENTUM_WEIGHT = 0.25
MOMENTUM_FULL_THRESHOLD = 10.0    # ≥ +10% on the day ("in play")
MOMENTUM_HALF_THRESHOLD = 5.0     # ≥ +5%  building momentum

# ── Pillar 3: Supply Health — Circulating / Total supply ──────────────────────
# HIGH circulating ratio = GOOD (tokens already in market = less dump risk)
# LOW circulating ratio = BAD (large locked supply that can unlock and dump)
SUPPLY_WEIGHT = 0.20
SUPPLY_FULL_THRESHOLD = 0.80    # > 80% circulating → minimal unlock risk
SUPPLY_HALF_THRESHOLD = 0.60    # > 60% circulating → moderate risk

# ── Pillar 4: Market Cap — right size for explosive moves ─────────────────────
MCAP_WEIGHT = 0.15
MCAP_FULL_LOW  = 10_000_000      # $10M floor  (below = too illiquid)
MCAP_FULL_HIGH = 300_000_000     # $300M ceil  (sweet spot for big % moves)
MCAP_HALF_MAX  = 2_000_000_000   # $2B ceiling (mid-large, slower but moves)
MCAP_MIN       = 5_000_000       # $5M hard minimum (below = not tradeable)

# ── Pillar 5: Spread — execution quality ──────────────────────────────────────
SPREAD_WEIGHT = 0.10
SPREAD_FULL_THRESHOLD = 10.0    # < 10 bps — tight, can trade efficiently
SPREAD_HALF_THRESHOLD = 20.0    # < 20 bps — acceptable slippage

# ── Grade boundaries ──────────────────────────────────────────────────────────
GRADE_APLUS_MIN = 0.85
GRADE_A_MIN     = 0.70
GRADE_B_MIN     = 0.55
GRADE_C_MIN     = 0.40
GRADE_D_MIN     = 0.25

# A pillar "passes" if its score is at least half-credit
PILLAR_PASS_THRESHOLD = 0.5


def score_pillars(
    rvol: Optional[float],
    supply_ratio: Optional[float],
    market_cap: Optional[float],
    spread_bps: Optional[float],
    change_24h_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate A+ score with full per-pillar breakdown.

    Args:
        rvol:           Daily volume / 50-day avg daily volume (e.g. 5.0 = 500%)
        supply_ratio:   Circulating supply / Total supply (0.0 to 1.0)
        market_cap:     Market capitalization in USD
        spread_bps:     Bid-ask spread in basis points
        change_24h_pct: 24-hour price change percentage (e.g. 10.5 = +10.5%)

    Returns:
        {
            "score":  float,   # composite 0.0–1.0
            "grade":  str,     # "A+", "A", "B", "C", "D", or "F"
            "pillars": {
                "rvol":       {"score": float, "pass": bool, "value": float|None},
                "momentum":   {"score": float, "pass": bool, "value": float|None},
                "supply":     {"score": float, "pass": bool, "value": float|None},
                "market_cap": {"score": float, "pass": bool, "value": float|None},
                "spread":     {"score": float, "pass": bool, "value": float|None},
            }
        }
    """
    # ── Pillar 1: RVOL ────────────────────────────────────────────────────────
    rvol_score = 0.0
    if rvol is not None:
        if rvol > RVOL_FULL_THRESHOLD:
            rvol_score = 1.0
        elif rvol > RVOL_HALF_THRESHOLD:
            rvol_score = 0.5

    # ── Pillar 2: Momentum ────────────────────────────────────────────────────
    change_score = 0.0
    if change_24h_pct is not None:
        if change_24h_pct >= MOMENTUM_FULL_THRESHOLD:
            change_score = 1.0
        elif change_24h_pct >= MOMENTUM_HALF_THRESHOLD:
            change_score = 0.5

    # ── Pillar 3: Supply Health (CORRECTED: high circ = good) ─────────────────
    supply_score = 0.0
    if supply_ratio is not None:
        if supply_ratio > SUPPLY_FULL_THRESHOLD:
            supply_score = 1.0
        elif supply_ratio > SUPPLY_HALF_THRESHOLD:
            supply_score = 0.5

    # ── Pillar 4: Market Cap ──────────────────────────────────────────────────
    market_cap_score = 0.0
    if market_cap is not None:
        if MCAP_FULL_LOW <= market_cap <= MCAP_FULL_HIGH:
            market_cap_score = 1.0
        elif MCAP_MIN <= market_cap <= MCAP_HALF_MAX:
            market_cap_score = 0.5

    # ── Pillar 5: Spread ──────────────────────────────────────────────────────
    spread_score = 0.0
    if spread_bps is not None:
        if spread_bps < SPREAD_FULL_THRESHOLD:
            spread_score = 1.0
        elif spread_bps < SPREAD_HALF_THRESHOLD:
            spread_score = 0.5

    total_score = round(
        (rvol_score       * RVOL_WEIGHT)
        + (change_score   * MOMENTUM_WEIGHT)
        + (supply_score   * SUPPLY_WEIGHT)
        + (market_cap_score * MCAP_WEIGHT)
        + (spread_score   * SPREAD_WEIGHT),
        4,
    )

    grade = score_to_grade(total_score)

    pillars = {
        "rvol":       {"score": rvol_score,        "pass": rvol_score >= PILLAR_PASS_THRESHOLD,        "value": rvol},
        "momentum":   {"score": change_score,      "pass": change_score >= PILLAR_PASS_THRESHOLD,      "value": change_24h_pct},
        "supply":     {"score": supply_score,      "pass": supply_score >= PILLAR_PASS_THRESHOLD,      "value": supply_ratio},
        "market_cap": {"score": market_cap_score,  "pass": market_cap_score >= PILLAR_PASS_THRESHOLD,  "value": market_cap},
        "spread":     {"score": spread_score,      "pass": spread_score >= PILLAR_PASS_THRESHOLD,      "value": spread_bps},
    }

    passing = [k for k, v in pillars.items() if v["pass"]]
    failing = [k for k, v in pillars.items() if not v["pass"]]
    logger.debug(
        "Pillar breakdown: score=%.4f grade=%s pass=[%s] fail=[%s]",
        total_score, grade, ", ".join(passing), ", ".join(failing),
    )

    return {"score": total_score, "grade": grade, "pillars": pillars}


def calculate_aplus_score(
    rvol: Optional[float],
    supply_ratio: Optional[float],
    market_cap: Optional[float],
    spread_bps: Optional[float],
    change_24h_pct: Optional[float] = None,
) -> float:
    """
    Calculate A+ composite score (0.0–1.0).

    Backward-compatible wrapper around score_pillars(). Returns only the
    composite float; for per-pillar breakdown use score_pillars() directly.

    Args:
        rvol:           Daily volume / 50-day avg daily volume (e.g. 5.0 = 500%)
        supply_ratio:   Circulating supply / Total supply (0.0 to 1.0)
        market_cap:     Market capitalization in USD
        spread_bps:     Bid-ask spread in basis points
        change_24h_pct: 24-hour price change percentage (e.g. 10.5 = +10.5%)

    Returns:
        Composite A+ score from 0.0 to 1.0
    """
    return score_pillars(rvol, supply_ratio, market_cap, spread_bps, change_24h_pct)["score"]


def calculate_granular_rvol(
    current_hour_volume: float,
    current_hour_progress: float,
    hourly_sma_50d: Optional[float],
) -> Optional[float]:
    """
    Calculate granular RVOL using pro-rata multiplier for current hour.

    This implements Ross Cameron's strategy for detecting institutional
    and retail "crowding" in real-time using 1-hour bars.

    Args:
        current_hour_volume:  Volume of current incomplete 1-hour bar
        current_hour_progress: Progress through current hour (0.0 to 1.0)
            e.g., 0.25 = 15 minutes into the hour
        hourly_sma_50d: 50-day SMA of hourly volumes (baseline)

    Returns:
        RVOL value (projected current hour volume / 50-day hourly average)
        Returns None if insufficient data
    """
    if hourly_sma_50d is None or hourly_sma_50d <= 0:
        return None

    if current_hour_progress <= 0:
        return None

    projected_hourly_volume = current_hour_volume / current_hour_progress
    return projected_hourly_volume / hourly_sma_50d


def get_rvol_score(rvol: Optional[float]) -> float:
    """
    Get RVOL component score for A+ scoring.

    Args:
        rvol: RVOL value (daily volume / 50-day daily average)

    Returns:
        1.0 if RVOL > RVOL_FULL_THRESHOLD, 0.5 if > RVOL_HALF_THRESHOLD, else 0.0
    """
    if rvol is None:
        return 0.0
    if rvol > RVOL_FULL_THRESHOLD:
        return 1.0
    elif rvol > RVOL_HALF_THRESHOLD:
        return 0.5
    return 0.0


def score_to_grade(score: Optional[float]) -> str:
    """
    Convert numeric A+ score to letter grade.

    Grading scale:
    - A+: score >= 0.85
    - A:  0.70 <= score < 0.85
    - B:  0.55 <= score < 0.70
    - C:  0.40 <= score < 0.55
    - D:  0.25 <= score < 0.40
    - F:  score < 0.25 or None
    """
    if score is None:
        return "F"
    if score >= GRADE_APLUS_MIN:
        return "A+"
    elif score >= GRADE_A_MIN:
        return "A"
    elif score >= GRADE_B_MIN:
        return "B"
    elif score >= GRADE_C_MIN:
        return "C"
    elif score >= GRADE_D_MIN:
        return "D"
    return "F"


# Minimum score required for each grade (used by min_allowed_grade filter)
_GRADE_MIN_SCORE: Dict[str, float] = {
    "A+": GRADE_APLUS_MIN,
    "A":  GRADE_A_MIN,
    "B":  GRADE_B_MIN,
    "C":  GRADE_C_MIN,
    "D":  GRADE_D_MIN,
    "F":  0.0,
}


def grade_to_min_score(grade: str) -> float:
    """
    Return the minimum A+ score required for a given grade.

    Used by min_allowed_grade filter: symbol's score must be >= this value
    for the pair to qualify. Grade ordering: A+ > A > B > C > D > F.

    Args:
        grade: Letter grade "A+", "A", "B", "C", "D", or "F"

    Returns:
        Minimum score (0.0–1.0) for that grade. Returns 0.0 for unknown grades.
    """
    return _GRADE_MIN_SCORE.get((grade or "").strip().upper(), 0.0)
