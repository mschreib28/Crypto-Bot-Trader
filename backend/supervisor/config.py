"""Supervisor service configuration — all values env-driven."""

import os

# How often the supervisor runs a full evaluation cycle (seconds)
SUPERVISOR_INTERVAL_SEC = int(os.getenv("SUPERVISOR_INTERVAL_SEC", "28800"))  # 8 hours

# Default backtest parameters
SUPERVISOR_BACKTEST_DAYS = int(os.getenv("SUPERVISOR_BACKTEST_DAYS", "30"))
SUPERVISOR_BACKTEST_INTERVAL = os.getenv("SUPERVISOR_BACKTEST_INTERVAL", "1h")

# Per-strategy subprocess timeout (seconds)
SUPERVISOR_TIMEOUT_SEC = int(os.getenv("SUPERVISOR_TIMEOUT_SEC", "1200"))  # 20 min max per strategy

# Classification thresholds
ACTIVE_WR_THRESHOLD = float(os.getenv("SUPERVISOR_ACTIVE_WR", "40.0"))
ACTIVE_RR_THRESHOLD = float(os.getenv("SUPERVISOR_ACTIVE_RR", "1.2"))
REDUCED_WR_THRESHOLD = float(os.getenv("SUPERVISOR_REDUCED_WR", "30.0"))
REDUCED_RR_THRESHOLD = float(os.getenv("SUPERVISOR_REDUCED_RR", "0.8"))

# Minimum trades required for ACTIVE classification (avoids noise from tiny samples)
MIN_TRADES_FOR_ACTIVE = int(os.getenv("SUPERVISOR_MIN_TRADES_ACTIVE", "5"))

# Gate enable/disable (set to "0" to bypass supervisor checks in the runner)
SUPERVISOR_GATE_ENABLED = os.getenv("SUPERVISOR_GATE_ENABLED", "1") == "1"

# Hybrid exit (position monitor): minimum chart bars after entry before valve 1/2 can fire
MIN_HYBRID_EXIT_HOLD_BARS = int(os.getenv("HYBRID_EXIT_MIN_HOLD_BARS", "3"))

# Canonical strategy names (must match backtest.py --strategy choices)
SUPERVISOR_STRATEGIES = [
    "vwap_meanrev",
    "vwap_meanrev_1h",
    "htf_trend",
    "volatility_breakout",
    "meanrev",
    "bull_flag_1m",
    "bull_flag_5m",
    "bull_flag_1h",
    "swing_bull_flag",
]

# Live rolling evaluator (Task 4) — Redis R-multiple exits + DB strategy names
LIVE_EVAL_WINDOW_HOURS = int(os.getenv("LIVE_EVAL_WINDOW_HOURS", "24"))
LIVE_EVAL_MIN_TRADES = int(os.getenv("LIVE_EVAL_MIN_TRADES", "5"))
LIVE_EVAL_INTERVAL_SEC = int(os.getenv("LIVE_EVAL_INTERVAL_SEC", "1800"))  # 30 min
LIVE_ACTIVE_WR = float(os.getenv("LIVE_ACTIVE_WR", "50.0"))
LIVE_ACTIVE_RR = float(os.getenv("LIVE_ACTIVE_RR", "1.5"))
LIVE_REDUCED_WR = float(os.getenv("LIVE_REDUCED_WR", "35.0"))
LIVE_REDUCED_RR = float(os.getenv("LIVE_REDUCED_RR", "0.9"))

# Per-strategy overrides: interval/days can differ from default
SUPERVISOR_STRATEGY_OVERRIDES: dict[str, dict] = {
    "vwap_meanrev_1h": {
        "interval": os.getenv("SUPERVISOR_VWAP_MEANREV_1H_INTERVAL", "1h"),
        "days": int(os.getenv("SUPERVISOR_VWAP_MEANREV_1H_DAYS", "28")),
    },
    "bull_flag_1m": {
        "interval": os.getenv("SUPERVISOR_BULL_FLAG_1M_INTERVAL", "1m"),
        "days": int(os.getenv("SUPERVISOR_BULL_FLAG_1M_DAYS", "3")),
    },
    "bull_flag_5m": {
        "interval": os.getenv("SUPERVISOR_BULL_FLAG_5M_INTERVAL", "5m"),
        "days": int(os.getenv("SUPERVISOR_BULL_FLAG_5M_DAYS", "14")),
    },
    "bull_flag_1h": {
        "interval": os.getenv("SUPERVISOR_BULL_FLAG_1H_INTERVAL", "1h"),
        "days": int(os.getenv("SUPERVISOR_BULL_FLAG_1H_DAYS", "30")),
    },
    "swing_bull_flag": {
        "interval": os.getenv("SUPERVISOR_SWING_BULL_FLAG_INTERVAL", "4h"),
        "days": int(os.getenv("SUPERVISOR_SWING_BULL_FLAG_DAYS", "180")),
    },
}
