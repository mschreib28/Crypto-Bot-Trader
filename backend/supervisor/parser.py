"""Parse backtest.py stdout into structured metrics."""

import re
from dataclasses import dataclass


@dataclass
class BacktestMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    rr_ratio: float  # float('inf') when no losses


_RE_TRADES = re.compile(r"Trades\s*:\s*(\d+)\s*\((\d+) wins / (\d+) losses\)")
_RE_WIN_RATE = re.compile(r"Win rate\s*:\s*([\d.]+)%")
_RE_RR = re.compile(r"R:R\s*:\s*([\d.]+|inf):1")


def parse_backtest_stdout(text: str) -> BacktestMetrics:
    """Parse stdout from backtest.py pipeline run.

    Returns BacktestMetrics with trades=0 when no trades were generated.
    Raises ValueError if expected fields are present but unparseable.
    """
    if "No trades generated." in text:
        return BacktestMetrics(trades=0, wins=0, losses=0, win_rate=0.0, rr_ratio=0.0)

    trades_match = _RE_TRADES.search(text)
    wr_match = _RE_WIN_RATE.search(text)
    rr_match = _RE_RR.search(text)

    missing = []
    if not trades_match:
        missing.append("Trades")
    if not wr_match:
        missing.append("Win rate")
    if not rr_match:
        missing.append("R:R")

    if missing:
        raise ValueError(f"Backtest output missing fields: {', '.join(missing)}")

    total = int(trades_match.group(1))
    wins = int(trades_match.group(2))
    losses = int(trades_match.group(3))
    win_rate = float(wr_match.group(1))
    rr_raw = rr_match.group(1)
    rr_ratio = float("inf") if rr_raw == "inf" else float(rr_raw)

    return BacktestMetrics(
        trades=total,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        rr_ratio=rr_ratio,
    )
