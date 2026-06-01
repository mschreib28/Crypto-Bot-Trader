"""Subprocess wrapper: runs backtest.py and returns raw stdout/stderr."""

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Project root is two levels up from this file (backend/supervisor/runner.py)
_APP_ROOT = Path(__file__).parent.parent.parent


@dataclass
class BacktestRun:
    strategy: str
    stdout: str
    stderr: str
    exit_code: int
    duration_sec: float


def run_backtest(
    strategy: str,
    days: int,
    interval: str,
    timeout_sec: int,
) -> BacktestRun:
    """Run backtest.py for one strategy and return raw results.

    Uses all-pairs pipeline mode (no --symbol flag).
    Output CSV is written to /tmp to avoid clobbering shared state.
    """
    output_path = f"/tmp/supervisor_{strategy}.csv"
    cmd = [
        sys.executable,
        "backtest.py",
        "--strategy", strategy,
        "--days", str(days),
        "--interval", interval,
        "--output", output_path,
    ]

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(_APP_ROOT))

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_APP_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        duration = time.monotonic() - start
        return BacktestRun(
            strategy=strategy,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            duration_sec=round(duration, 1),
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return BacktestRun(
            strategy=strategy,
            stdout="",
            stderr=f"Backtest timed out after {timeout_sec}s",
            exit_code=124,
            duration_sec=round(duration, 1),
        )
    except Exception as exc:
        duration = time.monotonic() - start
        return BacktestRun(
            strategy=strategy,
            stdout="",
            stderr=str(exc),
            exit_code=1,
            duration_sec=round(duration, 1),
        )
