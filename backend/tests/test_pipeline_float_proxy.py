"""E1 float proxy soft grade gate tests."""

from backend.screener.pipeline import (
    FLOAT_PROXY_MIN_TURNOVER,
    apply_float_proxy_soft_grade,
    check_float_proxy,
    compute_pipeline_grade,
    float_proxy_turnover,
)


class TestCheckFloatProxy:
    def test_passes_at_5pct_turnover(self):
        # volume / mcap = 0.06
        assert check_float_proxy(600_000, 10_000_000) is True

    def test_fails_below_threshold(self):
        assert check_float_proxy(400_000, 10_000_000) is False

    def test_fails_missing_market_cap(self):
        assert check_float_proxy(1_000_000, None) is False

    def test_fails_zero_market_cap(self):
        assert check_float_proxy(1_000_000, 0) is False

    def test_turnover_ratio(self):
        assert float_proxy_turnover(500_000, 10_000_000) == 0.05
        assert float_proxy_turnover(500_000, 10_000_000) >= FLOAT_PROXY_MIN_TURNOVER


class TestApplyFloatProxySoftGrade:
    def test_pass_keeps_grade(self):
        assert apply_float_proxy_soft_grade("A+", True) == "A+"

    def test_fail_downgrades_one_letter(self):
        assert apply_float_proxy_soft_grade("A+", False) == "A"
        assert apply_float_proxy_soft_grade("A", False) == "B"
        assert apply_float_proxy_soft_grade("B", False) == "C"
        assert apply_float_proxy_soft_grade("C", False) == "C"

    def test_a_plus_with_fail_becomes_a(self):
        base = compute_pipeline_grade(True, 4)
        assert base == "A+"
        assert apply_float_proxy_soft_grade(base, False) == "A"
