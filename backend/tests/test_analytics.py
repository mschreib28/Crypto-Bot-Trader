"""Tests for trade analytics store."""

import json
from unittest.mock import MagicMock, patch

from backend.analytics.store import (
    aggregate_by_grade,
    capture_entry_snapshot,
    factor_correlations,
    finalize_trade,
)


class TestAnalyticsStore:
    def test_capture_and_finalize(self):
        redis = MagicMock()
        redis.hget.return_value = json.dumps(
            {
                "grade": "A",
                "rvol": 4.2,
                "market_cap": 1e8,
                "supply_ratio": 0.3,
                "price": 1.5,
                "spread_bps": 12,
                "change_24h_pct": 5.0,
            }
        )
        stored = {}

        def _set(key, val):
            stored[key] = val

        def _get(key):
            return stored.get(key)

        redis.set.side_effect = _set
        redis.get.side_effect = _get
        redis.lpush.return_value = 1
        redis.ltrim.return_value = True
        redis.delete.return_value = 1

        with patch("backend.analytics.store.get_redis_client", return_value=redis):
            capture_entry_snapshot(
                "ETH/USD",
                "vwap_meanrev",
                2000.0,
                0.1,
                metadata={"confidence": 85},
            )
            finalize_trade(
                "ETH/USD",
                "vwap_meanrev",
                exit_price=2100.0,
                pnl_usd=10.0,
                r_multiple=2.0,
                is_win=True,
                exit_reason="tp1",
            )

        assert redis.lpush.called
        payload = json.loads(redis.lpush.call_args[0][1])
        assert payload["screener_grade"] == "A"
        assert payload["is_win"] is True

    def test_aggregate_by_grade(self):
        records = [
            {"screener_grade": "A+", "is_win": True, "r_multiple": 2.0},
            {"screener_grade": "A+", "is_win": False, "r_multiple": -1.0},
            {"screener_grade": "B", "is_win": True, "r_multiple": 1.0},
        ]
        with patch(
            "backend.analytics.store.list_trade_records", return_value=records
        ):
            rows = aggregate_by_grade()

        a_plus = next(r for r in rows if r["grade"] == "A+")
        assert a_plus["trades"] == 2
        assert a_plus["win_rate"] == 50.0

    def test_factor_correlations_insufficient_data(self):
        with patch("backend.analytics.store.list_trade_records", return_value=[]):
            result = factor_correlations()
        assert result["sample_size"] == 0

    def _redis_capture_finalize(self, redis, metadata=None):
        stored = {}

        def _set(key, val):
            stored[key] = val

        def _get(key):
            return stored.get(key)

        redis.set.side_effect = _set
        redis.get.side_effect = _get
        redis.lpush.return_value = 1
        redis.ltrim.return_value = True
        redis.delete.return_value = 1
        redis.hget.return_value = None

        with patch("backend.analytics.store.get_redis_client", return_value=redis):
            capture_entry_snapshot(
                "ETH/USD",
                "vwap_meanrev",
                2000.0,
                0.1,
                metadata=metadata or {},
            )
            finalize_trade(
                "ETH/USD",
                "vwap_meanrev",
                exit_price=2100.0,
                pnl_usd=10.0,
                r_multiple=2.0,
                is_win=True,
                exit_reason="tp1",
            )
        return json.loads(redis.lpush.call_args[0][1])

    def test_capture_uses_redis_strategy_column_cache(self):
        redis = MagicMock()
        with patch(
            "backend.screener.strategy_columns.read_cached_vwap_distance",
            return_value=-2.5,
        ):
            with patch(
                "backend.screener.strategy_columns.read_cached_htf_trend",
                return_value="UP",
            ):
                payload = self._redis_capture_finalize(redis)

        assert payload["vwap_distance_pct"] == -2.5
        assert payload["htf_trend_direction"] == "UP"

    def test_capture_deviation_pct_fallback_sign(self):
        redis = MagicMock()
        with patch(
            "backend.screener.strategy_columns.read_cached_vwap_distance",
            return_value=None,
        ):
            with patch(
                "backend.screener.strategy_columns.read_cached_htf_trend",
                return_value=None,
            ):
                payload = self._redis_capture_finalize(
                    redis,
                    metadata={
                        "strategy_specific": {"deviation_pct": 3.2},
                    },
                )

        assert payload["vwap_distance_pct"] == -3.2
        assert payload["htf_trend_direction"] is None

    def test_capture_trend_direction_fallback(self):
        redis = MagicMock()
        with patch(
            "backend.screener.strategy_columns.read_cached_vwap_distance",
            return_value=None,
        ):
            with patch(
                "backend.screener.strategy_columns.read_cached_htf_trend",
                return_value=None,
            ):
                payload = self._redis_capture_finalize(
                    redis,
                    metadata={
                        "strategy_specific": {"trend_direction": "bullish"},
                    },
                )

        assert payload["htf_trend_direction"] == "UP"
        assert payload["vwap_distance_pct"] is None

    def test_capture_null_when_no_sources(self):
        redis = MagicMock()
        with patch(
            "backend.screener.strategy_columns.read_cached_vwap_distance",
            return_value=None,
        ):
            with patch(
                "backend.screener.strategy_columns.read_cached_htf_trend",
                return_value=None,
            ):
                payload = self._redis_capture_finalize(redis, metadata={})

        assert payload["vwap_distance_pct"] is None
        assert payload["htf_trend_direction"] is None
