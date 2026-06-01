"""Tests for the 5-Pillar A+ scoring system.

Validates:
  - score_pillars() structure and per-pillar breakdown
  - Each pillar's full/half/zero scoring logic
  - Supply logic correction (high circ = good, low circ = bad)
  - Grade boundary accuracy
  - calculate_aplus_score() backward compatibility (still returns float)
  - Graceful handling of None/missing data
"""

import pytest
from backend.screener.scoring import (
    calculate_aplus_score,
    score_pillars,
    score_to_grade,
    RVOL_FULL_THRESHOLD,
    RVOL_HALF_THRESHOLD,
    MOMENTUM_FULL_THRESHOLD,
    MOMENTUM_HALF_THRESHOLD,
    SUPPLY_FULL_THRESHOLD,
    SUPPLY_HALF_THRESHOLD,
    MCAP_FULL_LOW,
    MCAP_FULL_HIGH,
    MCAP_HALF_MAX,
    MCAP_MIN,
    SPREAD_FULL_THRESHOLD,
    SPREAD_HALF_THRESHOLD,
    GRADE_APLUS_MIN,
    GRADE_A_MIN,
    GRADE_B_MIN,
    GRADE_C_MIN,
    GRADE_D_MIN,
    PILLAR_PASS_THRESHOLD,
)


# ── Structure Tests ────────────────────────────────────────────────────────────

class TestScorePillarsStructure:
    """Validate the structure returned by score_pillars()."""

    def test_returns_required_keys(self):
        result = score_pillars(
            rvol=6.0, supply_ratio=0.9, market_cap=50_000_000,
            spread_bps=5.0, change_24h_pct=12.0,
        )
        assert "score" in result
        assert "grade" in result
        assert "pillars" in result

    def test_pillars_has_all_five(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None)
        assert set(result["pillars"].keys()) == {"rvol", "momentum", "supply", "market_cap", "spread"}

    def test_each_pillar_has_score_pass_value(self):
        result = score_pillars(
            rvol=6.0, supply_ratio=0.9, market_cap=50_000_000,
            spread_bps=5.0, change_24h_pct=12.0,
        )
        for name, pillar in result["pillars"].items():
            assert "score" in pillar, f"{name} missing 'score'"
            assert "pass" in pillar, f"{name} missing 'pass'"
            assert "value" in pillar, f"{name} missing 'value'"

    def test_score_is_float(self):
        result = score_pillars(rvol=3.0, supply_ratio=0.85, market_cap=100_000_000, spread_bps=8.0)
        assert isinstance(result["score"], float)

    def test_grade_is_string(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None)
        assert isinstance(result["grade"], str)

    def test_pillar_pass_matches_score_threshold(self):
        result = score_pillars(rvol=6.0, supply_ratio=None, market_cap=None, spread_bps=None)
        # rvol=6.0 → score=1.0 → pass=True
        assert result["pillars"]["rvol"]["pass"] is True
        # supply=None → score=0.0 → pass=False
        assert result["pillars"]["supply"]["pass"] is False

    def test_value_preserved_in_pillar(self):
        result = score_pillars(rvol=7.5, supply_ratio=0.85, market_cap=200_000_000, spread_bps=6.0, change_24h_pct=15.0)
        assert result["pillars"]["rvol"]["value"] == 7.5
        assert result["pillars"]["momentum"]["value"] == 15.0
        assert result["pillars"]["supply"]["value"] == 0.85
        assert result["pillars"]["market_cap"]["value"] == 200_000_000
        assert result["pillars"]["spread"]["value"] == 6.0


# ── Pillar 1: RVOL ────────────────────────────────────────────────────────────

class TestPillar1RVOL:

    def test_above_full_threshold_scores_1(self):
        result = score_pillars(rvol=RVOL_FULL_THRESHOLD + 0.1, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["rvol"]["score"] == 1.0

    def test_exactly_at_full_threshold_scores_half(self):
        # Threshold is strict greater-than, so exactly 5.0 → half
        result = score_pillars(rvol=RVOL_FULL_THRESHOLD, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["rvol"]["score"] == 0.5

    def test_between_thresholds_scores_half(self):
        result = score_pillars(rvol=3.0, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["rvol"]["score"] == 0.5
        assert result["pillars"]["rvol"]["pass"] is True

    def test_below_half_threshold_scores_zero(self):
        result = score_pillars(rvol=1.5, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["rvol"]["score"] == 0.0
        assert result["pillars"]["rvol"]["pass"] is False

    def test_none_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["rvol"]["score"] == 0.0
        assert result["pillars"]["rvol"]["value"] is None

    def test_rvol_contributes_30pct_to_composite(self):
        # Only RVOL at full → composite = 1.0 * 0.30 = 0.30
        result = score_pillars(rvol=6.0, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["score"] == pytest.approx(0.30, abs=0.001)


# ── Pillar 2: Momentum ────────────────────────────────────────────────────────

class TestPillar2Momentum:

    def test_at_or_above_full_threshold_scores_1(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=MOMENTUM_FULL_THRESHOLD)
        assert result["pillars"]["momentum"]["score"] == 1.0

    def test_above_half_threshold_scores_half(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=7.0)
        assert result["pillars"]["momentum"]["score"] == 0.5

    def test_below_half_threshold_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=3.0)
        assert result["pillars"]["momentum"]["score"] == 0.0

    def test_negative_change_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=-5.0)
        assert result["pillars"]["momentum"]["score"] == 0.0

    def test_none_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=None)
        assert result["pillars"]["momentum"]["score"] == 0.0

    def test_momentum_contributes_25pct_to_composite(self):
        # Only momentum at full → composite = 1.0 * 0.25 = 0.25
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=20.0)
        assert result["score"] == pytest.approx(0.25, abs=0.001)


# ── Pillar 3: Supply Health (CRITICAL FIX) ────────────────────────────────────

class TestPillar3SupplyHealth:
    """
    CRITICAL: Supply logic must be CORRECT (high circ = good, low circ = bad).

    High circulating ratio (>80%) means most supply is already on the market,
    so there is less risk of sudden unlock events dumping the price.

    Low circulating ratio (<60%) means large locked supply → dump risk.
    """

    def test_high_circ_above_full_threshold_scores_1(self):
        """90% circulating = low dump risk → full score."""
        result = score_pillars(rvol=None, supply_ratio=0.90, market_cap=None, spread_bps=None)
        assert result["pillars"]["supply"]["score"] == 1.0
        assert result["pillars"]["supply"]["pass"] is True

    def test_moderate_circ_above_half_threshold_scores_half(self):
        """70% circulating = moderate risk → half score."""
        result = score_pillars(rvol=None, supply_ratio=0.70, market_cap=None, spread_bps=None)
        assert result["pillars"]["supply"]["score"] == 0.5

    def test_low_circ_scores_zero(self):
        """30% circulating = high dump risk → zero score."""
        result = score_pillars(rvol=None, supply_ratio=0.30, market_cap=None, spread_bps=None)
        assert result["pillars"]["supply"]["score"] == 0.0
        assert result["pillars"]["supply"]["pass"] is False

    def test_very_low_circ_also_scores_zero(self):
        """15% circulating (mostly locked) = very high dump risk → zero."""
        result = score_pillars(rvol=None, supply_ratio=0.15, market_cap=None, spread_bps=None)
        assert result["pillars"]["supply"]["score"] == 0.0

    def test_full_threshold_boundary_is_exclusive(self):
        """supply_ratio == SUPPLY_FULL_THRESHOLD is NOT a full score (> required)."""
        result = score_pillars(rvol=None, supply_ratio=SUPPLY_FULL_THRESHOLD, market_cap=None, spread_bps=None)
        # 0.80 is not > 0.80, so it falls into half-score range
        assert result["pillars"]["supply"]["score"] == 0.5

    def test_half_threshold_boundary_is_exclusive(self):
        """supply_ratio == SUPPLY_HALF_THRESHOLD is NOT a half score (> required)."""
        result = score_pillars(rvol=None, supply_ratio=SUPPLY_HALF_THRESHOLD, market_cap=None, spread_bps=None)
        # 0.60 is not > 0.60, so it falls to zero
        assert result["pillars"]["supply"]["score"] == 0.0

    def test_none_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["supply"]["score"] == 0.0

    def test_supply_contributes_20pct_to_composite(self):
        # Only supply at full → composite = 1.0 * 0.20 = 0.20
        result = score_pillars(rvol=None, supply_ratio=0.95, market_cap=None, spread_bps=None)
        assert result["score"] == pytest.approx(0.20, abs=0.001)

    def test_old_inverted_logic_would_fail(self):
        """
        Guard against regression to inverted logic.
        Old (wrong) code: supply_ratio < 0.20 → score 1.0
        That should now produce 0.0 (low circ = dump risk).
        """
        result = score_pillars(rvol=None, supply_ratio=0.10, market_cap=None, spread_bps=None)
        assert result["pillars"]["supply"]["score"] == 0.0, (
            "Regression: supply_ratio=0.10 (low circ) must NOT score high. "
            "Low circulating supply = high dump risk."
        )


# ── Pillar 4: Market Cap ──────────────────────────────────────────────────────

class TestPillar4MarketCap:

    def test_sweet_spot_scores_full(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=50_000_000, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 1.0

    def test_at_lower_boundary_scores_full(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=MCAP_FULL_LOW, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 1.0

    def test_at_upper_boundary_scores_full(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=MCAP_FULL_HIGH, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 1.0

    def test_mid_large_scores_half(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=500_000_000, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 0.5

    def test_too_large_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=5_000_000_000, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 0.0

    def test_too_small_scores_zero(self):
        """Below MCAP_MIN ($5M) is too illiquid."""
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=1_000_000, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 0.0

    def test_none_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 0.0

    def test_mcap_contributes_15pct_to_composite(self):
        # Only market_cap at full → composite = 1.0 * 0.15 = 0.15
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=50_000_000, spread_bps=None)
        assert result["score"] == pytest.approx(0.15, abs=0.001)


# ── Pillar 5: Spread ──────────────────────────────────────────────────────────

class TestPillar5Spread:

    def test_tight_spread_scores_full(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=5.0)
        assert result["pillars"]["spread"]["score"] == 1.0

    def test_at_full_threshold_still_scores_full(self):
        """spread_bps < SPREAD_FULL_THRESHOLD → boundary is exclusive on the tight side."""
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=SPREAD_FULL_THRESHOLD - 0.1)
        assert result["pillars"]["spread"]["score"] == 1.0

    def test_acceptable_spread_scores_half(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=15.0)
        assert result["pillars"]["spread"]["score"] == 0.5

    def test_wide_spread_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=25.0)
        assert result["pillars"]["spread"]["score"] == 0.0
        assert result["pillars"]["spread"]["pass"] is False

    def test_none_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["spread"]["score"] == 0.0

    def test_spread_contributes_10pct_to_composite(self):
        # Only spread at full → composite = 1.0 * 0.10 = 0.10
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=5.0)
        assert result["score"] == pytest.approx(0.10, abs=0.001)


# ── Composite Score and Grades ────────────────────────────────────────────────

class TestCompositeScoreAndGrade:

    def test_all_pillars_full_is_aplus(self):
        result = score_pillars(
            rvol=6.0, supply_ratio=0.90, market_cap=50_000_000,
            spread_bps=5.0, change_24h_pct=12.0,
        )
        assert result["score"] == pytest.approx(1.0, abs=0.001)
        assert result["grade"] == "A+"

    def test_all_pillars_none_is_f(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["score"] == 0.0
        assert result["grade"] == "F"

    def test_weights_sum_correctly(self):
        """All pillars at full → 0.30 + 0.25 + 0.20 + 0.15 + 0.10 = 1.00."""
        result = score_pillars(
            rvol=6.0, supply_ratio=0.90, market_cap=50_000_000,
            spread_bps=5.0, change_24h_pct=12.0,
        )
        assert result["score"] == pytest.approx(1.0, abs=0.001)

    def test_grade_aplus_boundary(self):
        assert score_to_grade(GRADE_APLUS_MIN) == "A+"
        assert score_to_grade(GRADE_APLUS_MIN - 0.001) == "A"

    def test_grade_a_boundary(self):
        assert score_to_grade(GRADE_A_MIN) == "A"
        assert score_to_grade(GRADE_A_MIN - 0.001) == "B"

    def test_grade_b_boundary(self):
        assert score_to_grade(GRADE_B_MIN) == "B"
        assert score_to_grade(GRADE_B_MIN - 0.001) == "C"

    def test_grade_c_boundary(self):
        assert score_to_grade(GRADE_C_MIN) == "C"
        assert score_to_grade(GRADE_C_MIN - 0.001) == "D"

    def test_grade_d_boundary(self):
        assert score_to_grade(GRADE_D_MIN) == "D"
        assert score_to_grade(GRADE_D_MIN - 0.001) == "F"

    def test_grade_none_is_f(self):
        assert score_to_grade(None) == "F"

    def test_grade_zero_is_f(self):
        assert score_to_grade(0.0) == "F"

    def test_only_rvol_gives_30pct_which_is_d(self):
        """RVOL only at full → score=0.30 → grade=D."""
        result = score_pillars(rvol=6.0, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["score"] == pytest.approx(0.30, abs=0.001)
        assert result["grade"] == "D"

    def test_rvol_plus_momentum_at_full_gives_55pct_which_is_b(self):
        """RVOL (0.30) + Momentum (0.25) = 0.55 → grade=B."""
        result = score_pillars(
            rvol=6.0, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=15.0,
        )
        assert result["score"] == pytest.approx(0.55, abs=0.001)
        assert result["grade"] == "B"


# ── Backward Compatibility ────────────────────────────────────────────────────

class TestBackwardCompatibility:
    """Ensure calculate_aplus_score() still returns a plain float."""

    def test_returns_float_not_dict(self):
        result = calculate_aplus_score(
            rvol=6.0, supply_ratio=0.90, market_cap=50_000_000,
            spread_bps=5.0, change_24h_pct=12.0,
        )
        assert isinstance(result, float)
        assert not isinstance(result, dict)

    def test_positional_args_work(self):
        """Old call sites pass positional args."""
        result = calculate_aplus_score(3.0, 0.85, 100_000_000, 12.0)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_all_none_returns_zero(self):
        result = calculate_aplus_score(None, None, None, None)
        assert result == 0.0

    def test_matches_score_pillars_composite(self):
        """calculate_aplus_score and score_pillars must agree on the composite."""
        kwargs = dict(rvol=4.0, supply_ratio=0.75, market_cap=80_000_000, spread_bps=8.0, change_24h_pct=6.0)
        assert calculate_aplus_score(**kwargs) == score_pillars(**kwargs)["score"]


# ── Missing Data Handling ─────────────────────────────────────────────────────

class TestMissingDataGraceful:
    """None values must not crash and must score 0.0 for that pillar."""

    def test_partial_data_no_crash(self):
        result = score_pillars(rvol=6.0, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["rvol"]["score"] == 1.0
        assert result["pillars"]["supply"]["score"] == 0.0
        assert result["pillars"]["supply"]["value"] is None

    def test_all_none_no_crash(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=None, change_24h_pct=None)
        assert result["score"] == 0.0

    def test_zero_rvol_does_not_crash(self):
        result = score_pillars(rvol=0.0, supply_ratio=None, market_cap=None, spread_bps=None)
        assert result["pillars"]["rvol"]["score"] == 0.0

    def test_zero_market_cap_scores_zero(self):
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=0.0, spread_bps=None)
        assert result["pillars"]["market_cap"]["score"] == 0.0

    def test_negative_spread_scores_full(self):
        """Negative spread (crossed book) is very tight — should score full."""
        result = score_pillars(rvol=None, supply_ratio=None, market_cap=None, spread_bps=-1.0)
        assert result["pillars"]["spread"]["score"] == 1.0
