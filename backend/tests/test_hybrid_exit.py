"""Tests for hybrid exit bearish confidence parsing and opener absent-symbol silence bump."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.positions.models import Position
from backend.positions.monitor import (
    PositionMonitor,
    bump_hybrid_silence_if_opener_missing_symbol,
    hybrid_bearish_exit_confidence,
)


def test_hybrid_bearish_exit_confidence_explicit_sell():
    row = {"signal_type": "SELL", "confidence": 80.0, "indicators": {}}
    assert hybrid_bearish_exit_confidence(row) == 80.0


def test_hybrid_bearish_exit_confidence_none_with_original_sell():
    row = {
        "signal_type": "NONE",
        "confidence": 65.0,
        "indicators": {"original_signal": "SELL"},
    }
    assert hybrid_bearish_exit_confidence(row) == 65.0


def test_hybrid_bearish_exit_confidence_pure_none():
    row = {"signal_type": "NONE", "confidence": 0.0, "indicators": {}}
    assert hybrid_bearish_exit_confidence(row) is None


def test_hybrid_bearish_exit_confidence_buy_ignored():
    row = {"signal_type": "BUY", "confidence": 95.0, "indicators": {}}
    assert hybrid_bearish_exit_confidence(row) is None


def test_bump_silence_when_symbol_missing_and_last_scan_advances():
    redis = MagicMock()
    redis.get.return_value = b"2025-01-01T00:00:00Z"

    opener_id = "ae330374-5716-4a3d-bd8a-3d1ece166bfb"
    symbol = "SAHARA/USD"
    blob = {
        "last_scan": "2025-01-02T00:00:00Z",
        "results": [{"symbol": "ETH/USD", "signal_type": "NONE", "confidence": 0.0}],
    }

    bump_hybrid_silence_if_opener_missing_symbol(redis, opener_id, symbol, blob)

    redis.incr.assert_called_once()
    redis.expire.assert_called_once()
    redis.setex.assert_called_once()


def test_bump_silence_skips_when_symbol_present():
    redis = MagicMock()
    symbol = "SAHARA/USD"
    blob = {
        "last_scan": "2025-01-02T00:00:00Z",
        "results": [{"symbol": symbol, "signal_type": "NONE", "confidence": 0.0}],
    }
    bump_hybrid_silence_if_opener_missing_symbol(redis, "sid", symbol, blob)
    redis.incr.assert_not_called()


def test_bump_silence_skips_when_last_scan_unchanged():
    redis = MagicMock()
    redis.get.return_value = b"same-scan"
    symbol = "SAHARA/USD"
    blob = {"last_scan": "same-scan", "results": []}
    bump_hybrid_silence_if_opener_missing_symbol(redis, "sid", symbol, blob)
    redis.incr.assert_not_called()


def _position_opened_minutes_ago(minutes: float) -> Position:
    entry = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    entry_iso = entry.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return Position(
        symbol="ETH/USD",
        side="long",
        quantity=1.0,
        entry_price=100.0,
        entry_time=entry_iso,
        opened_by_strategy_id="meanrev",
    )


@pytest.mark.asyncio
async def test_hybrid_exit_skipped_within_min_hold_bars():
    """Valve 1/2 must not run until MIN_HYBRID_EXIT_HOLD_BARS elapsed (opener bar size)."""
    m = PositionMonitor()
    pos = _position_opened_minutes_ago(2.0)
    force = AsyncMock()
    rc = MagicMock()
    rc.get.return_value = None

    with patch.object(m, "_opener_strategy_interval_minutes", return_value=5.0), patch.object(
        m, "_force_exit_position", force
    ), patch("backend.redis.get_redis_client", return_value=rc):
        await m._check_hybrid_exit(pos, 100.0)

    force.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_exit_after_min_hold_no_valve_no_force_exit():
    """Past min hold, empty active strategies → no hybrid close (sanity)."""
    m = PositionMonitor()
    pos = _position_opened_minutes_ago(20.0)
    force = AsyncMock()
    rc = MagicMock()
    rc.get.return_value = None

    sess = MagicMock()
    q = MagicMock()
    sess.query.return_value = q
    q.filter.return_value = q
    q.all.return_value = []

    with patch.object(m, "_opener_strategy_interval_minutes", return_value=5.0), patch.object(
        m, "_force_exit_position", force
    ), patch("backend.redis.get_redis_client", return_value=rc), patch(
        "backend.db.get_session", return_value=sess
    ):
        await m._check_hybrid_exit(pos, 100.0)

    force.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_exit_after_min_hold_high_confidence_calls_force_exit():
    """Past min hold, valve 2 (bearish >= override %) triggers _force_exit_position."""
    m = PositionMonitor()
    pos = _position_opened_minutes_ago(20.0)
    force = AsyncMock()
    opener_id = "11111111-1111-1111-1111-111111111101"
    closer_id = "22222222-2222-2222-2222-222222222202"
    pos.opened_by_strategy_id = opener_id

    row = {
        "symbol": "ETH/USD",
        "signal_type": "SELL",
        "confidence": 90.0,
        "indicators": {},
    }
    closer_blob = json.dumps({"results": [row]}).encode()

    def redis_get(key: str):
        key_s = key.decode() if isinstance(key, bytes) else key
        if f"strategy:{opener_id}" in key_s or key_s.endswith(opener_id):
            return None
        return closer_blob

    rc = MagicMock()
    rc.get.side_effect = redis_get

    st = MagicMock()
    st.id = closer_id
    st.name = "macd"
    st.status = "active"
    so = MagicMock()
    so.id = opener_id
    so.name = "meanrev"
    so.status = "active"

    sess = MagicMock()
    q = MagicMock()
    sess.query.return_value = q
    q.filter.return_value = q
    q.all.return_value = [so, st]

    with patch.object(m, "_opener_strategy_interval_minutes", return_value=5.0), patch.object(
        m, "_force_exit_position", force
    ), patch.object(m, "_strategy_display_name", return_value="meanrev"), patch(
        "backend.redis.get_redis_client", return_value=rc
    ), patch("backend.db.get_session", return_value=sess):
        await m._check_hybrid_exit(pos, 100.0)

    force.assert_called_once()
    call_kw = force.call_args[1]
    assert "hybrid_exit_high_confidence" in call_kw["reason"]
