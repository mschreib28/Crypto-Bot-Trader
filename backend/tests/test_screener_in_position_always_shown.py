"""
Regression test: a symbol with an open position must always appear in the
unified screener API response, even when it has no RVOL / market-cap data
(i.e. it was excluded from APLUS_SCORES_KEY by the screener service).

Two failure modes are guarded against:
  1. Service-level exclusion: the screener service's `_calculate_aplus_scores`
     skips symbols where `has_data = False`.  Fixed by checking
     `_active_position_symbols` before the `has_data` guard.

  2. API-level exclusion: `get_unified_screener` only iterates keys already in
     the APLUS_SCORES_KEY Redis hash.  Fixed by injecting a stub entry for
     in-position symbols before the iteration loop.

This test exercises the API-level path (the stub-injection path in
`backend/api/routes/screener.py`) because it is the most reachable path
without standing up the full screener service.
"""

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# Minimal module stubs
# ---------------------------------------------------------------------------

def _stub_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    if "." in name:
        parent_name, child_name = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, mod)
    return mod


def _make_position(symbol: str, quantity: float = 100.0):
    pos = MagicMock()
    pos.symbol = symbol
    pos.quantity = quantity
    pos.side = "long"
    pos.entry_price = 0.015
    pos.stop_loss_price = 0.014
    pos.opened_by_strategy_id = "strategy-abc"
    pos.unrealized_pnl = 0.0
    return pos


def _setup_stubs(
    aplus_redis_data: dict,
    active_positions: list,
    position_status_map: dict | None = None,
):
    """
    Wire up all module stubs and return (redis_client, tracker_mock).

    aplus_redis_data   – what client.hgetall(APLUS_SCORES_KEY) returns
    active_positions   – list of position objects tracker.get_all_positions() returns
    position_status_map – symbol → status string for the positions/realtime endpoint
    """
    redis_client = MagicMock()
    redis_client.get.return_value = None
    redis_client.set.return_value = True
    redis_client.exists.return_value = False
    redis_client.hgetall.return_value = aplus_redis_data

    tracker_mock = MagicMock()
    tracker_mock.get_all_positions.return_value = active_positions

    # Map symbol → position for get_position() calls made during enrichment
    positions_by_symbol = {p.symbol: p for p in active_positions}
    tracker_mock.get_position.side_effect = lambda sym: positions_by_symbol.get(sym)

    if position_status_map:
        tracker_mock.get_position_status.side_effect = lambda sym: position_status_map.get(sym)
    else:
        tracker_mock.get_position_status.return_value = None

    _stub_module("backend")
    _stub_module("backend.redis", get_redis_client=lambda: redis_client)
    _stub_module(
        "backend.redis.keys",
        APLUS_SCORES_KEY="screener:aplus_scores",
        SCREENER_SIGNALS_HISTORY_KEY="screener:signals:history",
        TOP_10_OBVIOUS_KEY="screener:top_10_obvious",
        SHADOW_LIVE_MODE_KEY="shadow:live",
        TRADING_ENABLED_KEY="trading:enabled",
        POSITION_KEY="pos:{symbol}",
        POSITION_STATUS_KEY="pos:status:{symbol}",
        SCREENER_STRATEGY_RESULTS_KEY="screener:results:{strategy_id}",
        HALT_KEY="halt",
    )
    _stub_module("backend.db", get_session=MagicMock(return_value=MagicMock()))
    _stub_module("backend.db.models",
                 Strategy=MagicMock(),
                 Order=MagicMock(),
                 get_strategy_display_name=lambda s: "Test Strategy")
    _stub_module("backend.risk")
    _stub_module("backend.risk.halt", is_halted=lambda: False)
    _stub_module("backend.execution")
    _stub_module("backend.execution.panic", execute_panic_sequence=lambda: {})
    _stub_module("backend.screener")
    _stub_module("backend.screener.models", ScreenerResult=MagicMock())
    screener_service_instance = MagicMock()
    screener_service_instance._get_recent_bars = AsyncMock(return_value=[])
    screener_service_instance._get_signal_lead = MagicMock(return_value=None)
    screener_service_instance.get_last_scan_time = MagicMock(return_value=None)
    screener_service_class = MagicMock(return_value=screener_service_instance)
    _stub_module("backend.screener.service",
                 ScreenerService=screener_service_class,
                 get_trading_enabled=lambda: False,
                 _get_enabled_strategy_display_names=lambda: [])
    _stub_module("backend.screener.strategy_columns",
                 calculate_vwap_distance=lambda *a, **kw: None,
                 calculate_hod_distance=lambda *a, **kw: None,
                 calculate_htf_trend=lambda *a, **kw: None)
    _stub_module("backend.positions")
    _stub_module("backend.positions.tracker", get_position_tracker=lambda: tracker_mock)
    _stub_module("backend.positions.models", Position=MagicMock())
    _stub_module("backend.intervals")
    _stub_module("backend.intervals.config", POSITION_MONITOR_INTERVAL_SECONDS=10)
    _stub_module("backend.api")
    _stub_module("backend.api.routes")
    _stub_module("backend.api.routes.events", log_activity=lambda **kwargs: None)

    return redis_client, tracker_mock


# ---------------------------------------------------------------------------
# Helper: load the real screener route and call get_unified_screener()
# ---------------------------------------------------------------------------

def _load_screener_router():
    import importlib.util
    import os

    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    path = os.path.join(project_root, "backend", "api", "routes", "screener.py")
    spec = importlib.util.spec_from_file_location("backend.api.routes.screener", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backend.api.routes.screener"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInPositionSymbolAlwaysShown:
    """A symbol with an open long position must appear in screener results."""

    def test_in_position_symbol_present_when_missing_from_aplus_scores(self):
        """
        BABY/USD has no entry in APLUS_SCORES_KEY (e.g. RVOL data gap) but has
        an open position.  The API must inject it so it appears in the response.
        """
        # Redis returns NO entry for BABY/USD
        aplus_data = {
            b"ETH/USD": json.dumps({"score": 75.0, "grade": "A", "rvol": 3.5,
                                    "market_cap": 400e9, "supply_ratio": 0.12,
                                    "spread_bps": 5.0, "change_24h_pct": 1.2}).encode(),
        }
        position = _make_position("BABY/USD")
        _, tracker_mock = _setup_stubs(
            aplus_redis_data=aplus_data,
            active_positions=[position],
        )

        screener_mod = _load_screener_router()

        import asyncio

        with (
            patch("backend.api.routes.screener.get_position_tracker",
                  return_value=tracker_mock),
        ):
            # get_unified_screener is an async FastAPI endpoint; call it directly
            result = asyncio.run(screener_mod.get_unified_screener())

        symbols_in_response = {r["symbol"] for r in result.get("results", [])}
        assert "BABY/USD" in symbols_in_response, (
            f"BABY/USD must appear in screener response when position is open. "
            f"Got symbols: {symbols_in_response}"
        )

    def test_in_position_symbol_status_is_live(self):
        """
        An injected in-position symbol must show trade_status = 'LIVE' (or at
        minimum not 'SCANNING'), confirming the status is derived from position state.
        """
        aplus_data = {}  # Completely empty — BABY/USD not scored at all
        position = _make_position("BABY/USD")
        _, tracker_mock = _setup_stubs(
            aplus_redis_data=aplus_data,
            active_positions=[position],
            position_status_map={"BABY/USD": "LIVE"},
        )

        screener_mod = _load_screener_router()

        import asyncio

        with patch("backend.api.routes.screener.get_position_tracker",
                   return_value=tracker_mock):
            result = asyncio.run(screener_mod.get_unified_screener())

        results_by_symbol = {r["symbol"]: r for r in result.get("results", [])}
        assert "BABY/USD" in results_by_symbol, "BABY/USD must appear in screener"

        # The backend stores position status in indicators.status (the frontend
        # then renders it as trade_status).  It should reflect the real position state.
        indicator_status = results_by_symbol["BABY/USD"].get("indicators", {}).get("status")
        assert indicator_status == "LIVE", (
            f"Expected indicators.status == 'LIVE' for an open position but got '{indicator_status}'"
        )

    def test_normal_symbol_without_position_still_shown(self):
        """
        Symbols that DO have A+ score data must still appear (regression guard).
        """
        aplus_data = {
            b"ETH/USD": json.dumps({"score": 60.0, "grade": "B", "rvol": 2.0,
                                    "market_cap": 400e9, "supply_ratio": 0.1,
                                    "spread_bps": 4.0, "change_24h_pct": 0.5}).encode(),
            b"BTC/USD": json.dumps({"score": 80.0, "grade": "A+", "rvol": 5.0,
                                    "market_cap": 1e12, "supply_ratio": 0.05,
                                    "spread_bps": 2.0, "change_24h_pct": 2.1}).encode(),
        }
        _, tracker_mock = _setup_stubs(
            aplus_redis_data=aplus_data,
            active_positions=[],  # No open positions
        )

        screener_mod = _load_screener_router()

        import asyncio

        with patch("backend.api.routes.screener.get_position_tracker",
                   return_value=tracker_mock):
            result = asyncio.run(screener_mod.get_unified_screener())

        symbols_in_response = {r["symbol"] for r in result.get("results", [])}
        assert "ETH/USD" in symbols_in_response
        assert "BTC/USD" in symbols_in_response
