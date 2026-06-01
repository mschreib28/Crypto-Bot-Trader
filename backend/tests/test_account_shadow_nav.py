"""Shadow account NAV, P&L reconciliation, and per-asset paper holdings (account routes)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.execution.kraken_cli import (
    PaperAssetBalance,
    PaperBalance,
    PaperStatus,
    TickerResult,
)
from backend.risk.account import AccountState


def _mock_pos(side="long", qty=1.0, entry=100.0, current=110.0, symbol="ETH/USD"):
    p = MagicMock()
    p.symbol = symbol
    p.quantity = qty
    p.entry_price = entry
    p.current_price = current
    p.side = side
    return p


def test_incremental_unrealized_long():
    from backend.api.routes.account import _incremental_unrealized_usd

    assert _incremental_unrealized_usd([_mock_pos()]) == pytest.approx(10.0)


def test_incremental_unrealized_short():
    from backend.api.routes.account import _incremental_unrealized_usd

    p = _mock_pos(side="short", entry=100.0, current=90.0)
    assert _incremental_unrealized_usd([p]) == pytest.approx(10.0)


def test_open_positions_mark_value_usd():
    from backend.api.routes.account import _open_positions_mark_value_usd

    assert _open_positions_mark_value_usd([_mock_pos()]) == pytest.approx(110.0)


def test_shadow_nav_ledger_plus_marks():
    from backend.api.routes.account import _shadow_nav_ledger_plus_marks

    assert _shadow_nav_ledger_plus_marks(400.0, [_mock_pos()]) == pytest.approx(510.0)


def test_merge_shadow_holdings_by_symbol():
    from backend.api.routes.account import _merge_shadow_holdings_by_symbol

    raw = [
        {"symbol": "BTC", "quantity": 0.01, "value_usd": 500.0},
        {"symbol": "BTC", "quantity": 0.01, "value_usd": 500.0},
        {"symbol": "ETH", "quantity": 1.0, "value_usd": 3000.0},
    ]
    merged = _merge_shadow_holdings_by_symbol(raw)
    by = {h["symbol"]: h for h in merged}
    assert by["BTC"]["quantity"] == pytest.approx(0.02)
    assert by["BTC"]["value_usd"] == pytest.approx(1000.0)
    assert by["ETH"]["value_usd"] == pytest.approx(3000.0)


def test_shadow_nav_from_paper():
    from backend.api.routes.account import _shadow_nav_from_paper

    holdings = [
        {"symbol": "USD", "quantity": 999.0, "value_usd": 999.0},
        {"symbol": "BTC", "quantity": 0.02, "value_usd": 1000.0},
    ]
    assert _shadow_nav_from_paper(400.0, holdings) == pytest.approx(1400.0)


@pytest.mark.asyncio
async def test_shadow_paper_balance_dict_uses_computed_nav_not_cli_current_value():
    from backend.api.routes.account import _shadow_paper_balance_dict

    pb = PaperBalance(
        balances={
            "USD": PaperAssetBalance(available=400.0, reserved=0.0, total=400.0),
            "XETH": PaperAssetBalance(available=1.0, reserved=0.0, total=1.0),
        },
        mode="paper",
    )
    ps = PaperStatus(
        starting_balance=500.0,
        current_value=999999.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        open_orders=0,
        total_trades=0,
        fee_rate=0.0026,
        mode="paper",
    )

    async def ticker_side_effect(pair: str):
        assert pair == "ETHUSD"
        return TickerResult(
            pair=pair,
            ask=3000.0,
            bid=2999.0,
            last=3000.0,
            volume_24h=1.0,
            high_24h=1.0,
            low_24h=1.0,
        )

    with patch("backend.execution.kraken_cli.get_ticker", side_effect=ticker_side_effect):
        body = await _shadow_paper_balance_dict(pb, ps)

    assert body["total_usd"] == pytest.approx(3400.0)
    assert body["available_usd"] == pytest.approx(400.0)


@pytest.mark.asyncio
async def test_build_shadow_paper_holdings_distinct_per_asset():
    from backend.api.routes.account import _build_shadow_paper_holdings

    pb = PaperBalance(
        balances={
            "USD": PaperAssetBalance(available=100.0, reserved=0, total=100.0),
            "XXBT": PaperAssetBalance(available=0.02, reserved=0, total=0.02),
            "XETH": PaperAssetBalance(available=1.0, reserved=0, total=1.0),
        },
        mode="paper",
    )

    async def ticker_side_effect(pair: str):
        if pair == "BTCUSD":
            return TickerResult(
                pair=pair,
                ask=50000.0,
                bid=49999.0,
                last=50000.0,
                volume_24h=1.0,
                high_24h=1.0,
                low_24h=1.0,
            )
        if pair == "ETHUSD":
            return TickerResult(
                pair=pair,
                ask=3000.0,
                bid=2999.0,
                last=3000.0,
                volume_24h=1.0,
                high_24h=1.0,
                low_24h=1.0,
            )
        raise AssertionError(f"unexpected pair {pair}")

    with patch("backend.execution.kraken_cli.get_ticker", side_effect=ticker_side_effect):
        holdings = await _build_shadow_paper_holdings(pb)

    by = {h["symbol"]: h["value_usd"] for h in holdings}
    assert by["USD"] == 100.0
    assert abs(by["BTC"] - 0.02 * 50000.0) < 0.01
    assert abs(by["ETH"] - 3000.0) < 0.01
    assert by["BTC"] != by["ETH"]


@pytest.mark.asyncio
async def test_get_account_shadow_nav_and_reconciled_pnl():
    from backend.api.routes import account as account_mod
    from backend.api.routes.account import get_account

    state = AccountState(
        initial_equity=500.0,
        realized_pnl=0.0,
        current_equity=400.0,
        daily_pnl=0.0,
        max_risk_per_trade=8.0,
    )
    tracker = MagicMock()
    tracker.get_state = MagicMock(return_value=state)

    pos = _mock_pos(qty=1.0, entry=100.0, current=110.0)
    pt = MagicMock()
    pt.get_all_positions = MagicMock(return_value=[pos])

    pb = MagicMock()
    pb.usd_available = 400.0

    with patch.object(account_mod, "get_account_tracker", return_value=tracker):
        with patch.object(account_mod, "get_shadow_live_mode", return_value=True):
            with patch("backend.api.routes.trading.get_bot_mode", return_value="SHADOW"):
                with patch(
                    "backend.execution.kraken_cli.paper_ensure_init",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "backend.execution.kraken_cli.paper_balance",
                        new_callable=AsyncMock,
                        return_value=pb,
                    ):
                        with patch.object(
                            account_mod,
                            "_build_shadow_paper_holdings",
                            new_callable=AsyncMock,
                            return_value=[
                                {"symbol": "BTC", "quantity": 1.0, "value_usd": 115.0},
                            ],
                        ):
                            with patch(
                                "backend.positions.tracker.get_position_tracker",
                                return_value=pt,
                            ):
                                out = await get_account()

    assert out["current_equity"] == 515.0
    assert out["total_pnl"] == pytest.approx(15.0)
    assert out["unrealized_pnl"] == pytest.approx(10.0)
    assert out["realized_pnl"] == pytest.approx(5.0)
    assert out["max_risk_per_trade"] == pytest.approx(10.3)
    assert out["available_usd"] == 400.0
    assert len(out["holdings"]) == 1
    assert out["holdings"][0]["symbol"] == "ETH/USD"
    assert out["holdings"][0]["value_usd"] == pytest.approx(110.0)
    assert out["holdings"][0]["entry_price"] == pytest.approx(100.0)
    assert out["micro_mode"]["max_positions"] == out["live_slots_max"] == 999
    assert out["live_slots_active"] == 1


@pytest.mark.asyncio
async def test_get_account_live_unchanged_formula():
    from backend.api.routes import account as account_mod
    from backend.api.routes.account import get_account

    state = AccountState(
        initial_equity=500.0,
        realized_pnl=0.0,
        current_equity=400.0,
        daily_pnl=0.0,
        max_risk_per_trade=8.0,
    )
    tracker = MagicMock()
    tracker.get_state = MagicMock(return_value=state)

    pos = _mock_pos(qty=1.0, entry=100.0, current=110.0)
    pt = MagicMock()
    pt.get_all_positions = MagicMock(return_value=[pos])

    with patch.object(account_mod, "get_account_tracker", return_value=tracker):
        with patch.object(account_mod, "get_shadow_live_mode", return_value=False):
            with patch("backend.api.routes.trading.get_bot_mode", return_value="LIVE"):
                with patch("backend.positions.tracker.get_position_tracker", return_value=pt):
                    out = await get_account()

    # Ledger equity + full position notional offset (legacy live path)
    assert out["current_equity"] == 400.0
    assert out["realized_pnl"] == pytest.approx(-100.0)
    assert out["unrealized_pnl"] == pytest.approx(110.0)
    assert out["total_pnl"] == pytest.approx(10.0)
    assert out["available_usd"] == pytest.approx(300.0)
    assert len(out["holdings"]) == 1
    assert out["holdings"][0]["symbol"] == "ETH/USD"
    assert out["micro_mode"]["max_positions"] == out["live_slots_max"] == 2
    assert out["live_slots_active"] == 1
