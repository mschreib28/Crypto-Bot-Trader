"""Sanity-check Kraken wsname → Binance symbol helpers in repo-root backtest.py."""

import importlib.util
from pathlib import Path


def _load_repo_backtest():
    root = Path(__file__).resolve().parents[2]
    path = root / "backtest.py"
    spec = importlib.util.spec_from_file_location("_crypto_backtest", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_kraken_usd_to_binance_symbol_aliases():
    bt = _load_repo_backtest()
    assert bt.kraken_usd_to_binance_symbol("XBT/USD") == "BTCUSDT"
    assert bt.kraken_usd_to_binance_symbol("ETH/USD") == "ETHUSDT"
    assert bt.kraken_usd_to_binance_symbol("XDg/Usd") == "DOGEUSDT"


def test_kraken_pair_slug_cache_filename():
    bt = _load_repo_backtest()
    assert bt._symbol_to_kraken_pair("SNX/USD") == "SNXUSD"

