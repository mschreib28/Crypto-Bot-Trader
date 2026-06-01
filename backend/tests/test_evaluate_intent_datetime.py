"""Regression test: evaluate_intent() must not raise UnboundLocalError for datetime.

Reproduces the bug where `from datetime import datetime, timezone` inside
evaluate_intent() shadowed the module-level import, causing:
  UnboundLocalError: cannot access local variable 'datetime' where it is not
  associated with a value

The fix: remove the redundant local import on line ~219 of risk/evaluator.py.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Minimal stubs so we can import evaluator without real Redis / DB
# ---------------------------------------------------------------------------

def _make_redis_stub():
    client = MagicMock()
    client.ping.return_value = True
    client.get.return_value = None
    client.set.return_value = True
    client.exists.return_value = False
    client.hgetall.return_value = {}
    return client


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    # Wire the submodule as an attribute on the parent so patch() can traverse it.
    if "." in name:
        parent_name, child_name = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, mod)
    return mod


def _setup_stubs():
    redis_client = _make_redis_stub()

    _stub_module("backend", )
    _stub_module("backend.redis", get_redis_client=lambda: redis_client)
    _stub_module("backend.redis.keys",
                 SHADOW_LIVE_MODE_KEY="shadow_live_mode",
                 TRADING_ENABLED_KEY="trading_enabled",
                 HALT_KEY="halt",
                 POSITION_KEY="pos:{symbol}",
                 MARKET_DATA_KEY="market:{symbol}",
                 PENDING_INTENTS_KEY="pending_intents",
                 PORTFOLIO_EXPOSURE_KEY="portfolio_exposure")

    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    _stub_module("backend.db", get_session=lambda: db_session)
    _stub_module("backend.db.models", EquityCurve=MagicMock(), Order=MagicMock(), Signal=MagicMock())

    _stub_module("backend.config",
                 MAX_DRAWDOWN_PCT=3.0,
                 CONFIDENCE_THRESHOLD_PCT=70.0,
                 RISK_PCT_PER_TRADE=2.0)

    _stub_module("backend.risk")
    _stub_module("backend.risk.halt", is_halted=lambda: False, set_halt_mode=lambda x: None)
    _stub_module("backend.risk.portfolio",
                 get_portfolio_exposure=lambda: 0.0,
                 get_pending_intents_exposure=lambda total_equity=None, session=None: 0.0,
                 get_strategy_current_exposure=lambda sid: 0.0,
                 get_daily_pnl=lambda session=None: 0.0,
                 get_current_equity=lambda session=None: 100.0,
                 get_open_positions_value=lambda session=None: 0.0)
    _stub_module("backend.risk.rules",
                 MAX_PORTFOLIO_RISK_PCT=10.0,
                 MAX_STRATEGY_RISK_PCT=5.0,
                 get_portfolio_exposure=lambda: 0.0,
                 get_pending_intents_exposure=lambda: 0.0,
                 get_strategy_current_exposure=lambda sid: 0.0,
                 check_portfolio_limit=lambda cur, pend, intent: (True, ""),
                 check_strategy_limit=lambda sid, exp, intent: (True, ""),
                 check_market_data_freshness=lambda symbol: (True, ""))
    _stub_module("backend.risk.limits",
                 check_budget_limit=lambda intent, exp_dollars, equity: (True, ""),
                 check_daily_loss_limit=lambda pnl: False,
                 get_current_exposure_dollars=lambda equity: 0.0)
    _stub_module("backend.risk.micro_mode",
                 is_micro_mode=lambda equity: False,
                 check_max_positions=lambda count: (True, ""),
                 check_entry_position_limits=lambda symbol, canon, tracker: (True, None),
                 get_micro_mode_status=lambda equity: {})
    _stub_module("backend.risk.models",
                 TradeIntent=MagicMock,
                 RiskDecision=MagicMock)

    _stub_module("backend.execution")
    _stub_module("backend.execution.panic", execute_panic_sequence=lambda: {"orders_cancelled": 0, "trading_disabled": False})

    _stub_module("backend.api")
    _stub_module("backend.api.routes")
    _stub_module("backend.api.routes.events", log_activity=lambda **kwargs: None)

    _stub_module("backend.screener")
    # Stub market data freshness check
    _stub_module("backend.screener.engine", )

    _stub_module("backend.positions")
    _stub_module("backend.positions.tracker", get_position_tracker=lambda: MagicMock())

    return redis_client


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestEvaluateIntentDatetime:
    """evaluate_intent() must not raise UnboundLocalError for 'datetime'."""

    def test_evaluate_intent_does_not_raise_unbound_datetime(self):
        """Calling evaluate_intent() must not raise UnboundLocalError."""
        _setup_stubs()

        # Load the real evaluator module directly (bypassing package hierarchy)
        # and register it in sys.modules so patch() can target it.
        import importlib.util, os
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _evaluator_path = os.path.join(_project_root, "backend", "risk", "evaluator.py")
        spec = importlib.util.spec_from_file_location("backend.risk.evaluator", _evaluator_path)
        evaluator_mod = importlib.util.module_from_spec(spec)
        sys.modules["backend.risk.evaluator"] = evaluator_mod
        spec.loader.exec_module(evaluator_mod)

        # We need to patch the internal helpers so evaluate_intent runs without
        # real infrastructure, but crucially reaches the block that previously
        # triggered the UnboundLocalError (the max-drawdown kill-switch check).
        # get_session is imported lazily inside evaluate_intent(), not at module level.
        # The backend.db stub already provides a mock session; just patch via db module.
        with (
            patch("backend.risk.evaluator.is_halted", return_value=False),
            patch("backend.risk.evaluator.check_market_data_freshness", return_value=(True, "")),
            patch("backend.risk.evaluator.get_portfolio_exposure", return_value=0.0),
            patch("backend.risk.evaluator.get_pending_intents_exposure", return_value=0.0),
            patch("backend.risk.evaluator.get_strategy_current_exposure", return_value=0.0),
        ):
            evaluate_intent = evaluator_mod.evaluate_intent
            from backend.risk.models import TradeIntent

            intent = TradeIntent(
                strategy_id="test_strategy",
                symbol="BABY/USD",
                side="buy",
                intent_type="enter",
                notional_risk_pct=2.0,
                metadata={},
            )

            # This must NOT raise UnboundLocalError
            try:
                decision = evaluate_intent(intent)
                # If we get here, the bug is fixed
                assert decision is not None
            except UnboundLocalError as e:
                pytest.fail(
                    f"evaluate_intent() raised UnboundLocalError: {e}\n"
                    "Root cause: 'from datetime import datetime, timezone' inside "
                    "evaluate_intent() shadowed the module-level import. "
                    "Fix: remove the redundant local import."
                )
