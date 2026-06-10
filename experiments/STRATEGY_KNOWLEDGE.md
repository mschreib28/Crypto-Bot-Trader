# Strategy Knowledge — Parameter Reference

Domain knowledge cheat sheet for the agent. Lists every config parameter the
agent may safely propose to modify, with valid ranges and notes on coupling.

If a parameter is NOT in this file, DO NOT propose changing it. If you want to
explore a parameter not listed here, write a request to HUMAN_NOTES.md under
`## Parameter Requests`.

## Universal Rules

- All parameters must be valid Python literals (numbers, booleans, strings)
- Never propose `None` unless explicitly noted as allowed
- Never propose negative numbers unless explicitly noted as allowed
- Boolean flags default to false unless noted

## Engine-Level Parameters (ALL strategies, added 2026-06-09)

These affect the backtest engine's exit simulation and data window. They apply
to every strategy via shared `check_exits()`.

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `intrabar_exits` | bool | true | true/false | Stops/TPs trigger on bar high/low (realistic, matches live monitor). false = legacy close-only (inflates WR). Only set false for A/B comparison vs old results. |
| `slippage_pct` | float | 0.0 | 0.0 — 0.5 | Adverse slippage % per fill. 0.1–0.3 realistic for mid-cap crypto spot. |
| `end_days_ago` | float | 0 | 0 — 1095 | Ends the data window N days before now. Use for out-of-sample splits: train `days: 1460, end_days_ago: 365`, validate `days: 365, end_days_ago: 0`. A config is only a winner if it improves BOTH. |
| `swing_stop_recent` | bool | false | true/false | true = stop anchored to MOST RECENT swing low instead of lowest swing in window. High-value experiment: changes stop distance and entire R framework. |
| `breakeven_trigger_r` | float | 0.5 | 0.0 — 1.5 | Only active when `breakeven_requires_tp1: false` — R-multiple at which breakeven arms early (mirrors monitor.py). |

IMPORTANT: results produced before 2026-06-09 used close-only exit triggers.
They are NOT comparable to new runs. Re-establish the baseline first.

---

## VWAP Mean Reversion (vwap_meanrev)

File: `research/strategies/vwap_meanrev/config.py`

### Entry Filters

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `dev_threshold_pct` | float | 2.0 | 1.0 — 5.0 | Min % below VWAP to enter long |
| `rsi_oversold` | float | 30.0 | 15.0 — 40.0 | Lower = stricter. Below 20 produces almost no signals. |
| `rsi_overbought` | float | 70.0 | 60.0 — 85.0 | For short signals (currently long_only) |
| `long_min_volume_ratio` | float\|None | None | 1.0 — 2.5 | Reversal candle volume vs SMA. None = disabled. |
| `htf_rsi_long_max` | float\|None | None | 30.0 — 50.0 | HTF RSI ceiling. None = disabled. |

### Stops and Targets

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `atr_stop_mult` | float | 1.5 | 0.8 — 3.0 | Stop = ATR × this. Tight stops = more losses but smaller |
| `stop_buffer_ATR` | float | 0.15 | 0.0 — 0.5 | Buffer outside swing low |
| `tp1_R` | float | 1.0 | 0.5 — 2.0 | First target in R-multiples |
| `tp2_R` | float | 2.0 | 1.5 — 4.0 | Second target in R-multiples. MUST be > tp1_R |
| `tp1_partial_pct` | float | 0.6 | 0.3 — 0.8 | Fraction of position closed at TP1 |
| `max_bars_in_trade` | int | 6 | 3 — 24 | Max hold time |

### Reversal Confirmation

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `reversal_body_pct` | float | 0.6 | 0.4 — 0.9 | Body must be X% of candle range |
| `reversal_close_position` | float | 0.25 | 0.1 — 0.4 | Close must be in top X% of range |

### Breakeven Guard (NEW)

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `breakeven_requires_tp1` | bool | false | true/false | If true, breakeven only activates after TP1 hit |
| `breakeven_trigger_r` | float | 0.5 | 0.0 — 1.5 | When breakeven_requires_tp1=false, activate at this R |

### Coupling Warnings

- `rsi_oversold` and `dev_threshold_pct` together control entry frequency. Making
  both stricter at once can kill all signals. Vary one at a time.
- `tp1_R` and `tp2_R` are coupled. tp2 must be > tp1 by at least 0.5R.
- `atr_stop_mult` and `tp1_R` define the R framework. Don't change both at once.

---

## Volatility Breakout (volatility_breakout)

File: `research/strategies/volatility_breakout/config.py`

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `require_btc_bull_market` | bool | true | true/false | BTC must be above 200d EMA |
| `btc_ema_period` | int | 200 | 50 — 300 | Period for BTC EMA filter |
| `bb_period` | int | 20 | 10 — 50 | Bollinger Bands lookback |
| `bb_std_mult` | float | 2.0 | 1.5 — 3.0 | BB standard deviations |
| `volume_breakout_mult` | float | 2.0 | 1.5 — 4.0 | Volume must exceed SMA × this on breakout |
| `atr_stop_mult` | float | 1.5 | 0.8 — 3.0 | |
| `tp1_R` | float | 1.0 | 0.5 — 2.0 | |
| `tp2_R` | float | 2.0 | 1.5 — 4.0 | |

### Coupling Warnings

- The BTC filter is the highest-impact knob. Disabling it returned strategy to
  unprofitable. Default ON.

---

## HTF Trend Pullback (htf_trend)

File: `research/strategies/htf_trend/config.py`

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `require_btc_bull_market` | bool | true | true/false | |
| `btc_ema_period` | int | 200 | 50 — 300 | |
| `htf_ema_fast` | int | 50 | 20 — 100 | |
| `htf_ema_slow` | int | 200 | 100 — 300 | MUST be > htf_ema_fast |
| `pullback_rsi_max` | float | 50.0 | 35.0 — 60.0 | Max RSI on pullback entry |
| `invalidation_rsi_long_floor` | float | 35.0 | 25.0 — 45.0 | Exit if RSI drops below this |
| `min_hold_bars_before_rsi_exit` | int | 6 | 3 — 12 | |
| `atr_stop_mult` | float | 1.5 | 0.8 — 3.0 | |

---

## Range Mean Reversion (meanrev) — BB+RSI+ADX

File: `research/strategies/meanrev/config.py`

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `rsi_period` | int | 14 | 7 — 28 | |
| `rsi_oversold_threshold` | float | 40.0 | 25.0 — 50.0 | |
| `rsi_overbought_threshold` | float | 75.0 | 60.0 — 85.0 | |
| `adx_max_threshold` | float | 30.0 | 20.0 — 40.0 | ADX must be BELOW this (ranging market) |
| `atr_min_ratio` | float | 0.8 | 0.5 — 1.5 | Min current ATR vs avg |
| `atr_stop_mult` | float | 1.5 | 0.8 — 3.0 | |
| `stop_buffer_ATR` | float | 0.15 | 0.0 — 0.5 | |

### Coupling Warnings

- This strategy bypasses D2 momentum requirement intentionally. Do NOT propose
  re-enabling D2 for meanrev.

---

## Bull Flag (bull_flag) — All Timeframes

Files: `research/strategies/bull_flag/config.py` and `config_swing.py`

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `min_pole_pct` | float | 5.0 | 3.0 — 10.0 | Minimum pole height % |
| `max_pole_bars` | int | 10 | 5 — 20 | Pole must complete within N bars |
| `flag_max_retrace_pct` | float | 50.0 | 30.0 — 70.0 | Max retracement of pole |
| `flag_min_bars` | int | 3 | 2 — 8 | Min flag consolidation bars |
| `flag_max_bars` | int | 15 | 8 — 30 | Max flag duration |
| `volume_breakout_mult` | float | 1.5 | 1.0 — 3.0 | |

---

## Screener / Pipeline Parameters

File: `backend/screener/pipeline.py`

These are GLOBAL parameters that affect ALL strategies. Be cautious — changes
here cascade.

| Parameter | Type | Default | Safe Range | Notes |
|---|---|---|---|---|
| `min_allowed_grade` | str | "A+" | A+, A, B, C | The grade gate |
| `d1_rvol_min` | float | 3.0 | 2.0 — 5.0 | RVOL minimum for D1 pass |
| `d2_momentum_24h_pct` | float | 8.0 | 5.0 — 15.0 | D2 momentum threshold |
| `d2_momentum_4h_pct` | float | 5.0 | 3.0 — 10.0 | D2 alt threshold |
| `d3_volume_min_usd` | int | 500_000 | 100_000 — 1_000_000 | D3 lower bound |
| `d3_volume_max_usd` | int | 50_000_000 | 10_000_000 — 100_000_000 | D3 upper bound |
| `d4_btc_drop_threshold_pct` | float | -4.0 | -8.0 — -2.0 | BTC must not drop more than this in 4h |
| `e1_float_turnover_min` | float | 0.05 | 0.02 — 0.20 | E1 soft gate (volume/market_cap) |

### Coupling Warnings

- Loosening `min_allowed_grade` from A+ to B will dramatically increase trade
  count. Use sparingly and watch WR closely.
- Lowering `d1_rvol_min` from 3.0 reduces momentum quality. Counter-intuitive
  for meanrev (which doesn't want momentum) but the gate still affects everything.
- Changing `d2_momentum_24h_pct` affects ONLY momentum strategies (vwap_meanrev
  and meanrev are exempt).

---

## CLI Flag Mapping

When proposing a config_override in experiments.yaml, the runner translates the
key to a CLI flag by replacing underscores with dashes. Example:

  config_overrides:
    long_min_volume_ratio: 1.5
  
Becomes: `--long-min-volume-ratio 1.5`

Verify the flag exists in `backtest.py` by grepping for it. If the flag doesn't
exist, the runner adds CLI passthrough automatically — but verify first.

Known existing flags (as of 2026-06-09):
- --rsi-oversold
- --long-min-volume-ratio
- --htf-rsi-long-max
- --htf-rsi-bars-interval  (NOTE: now defaults to 1h; using the entry interval makes the gate tautological)
- --strategy
- --days
- --interval
- --dev-threshold / --dev-threshold-pct (alias added; experiments may use dev_threshold_pct directly)
- --tp1-R / --tp2-R / --max-bars-in-trade / --reversal-body-pct (added 2026-06-09)
- --breakeven-requires-tp1 / --no-breakeven-requires-tp1 (false branch now actually simulated)
- --breakeven-trigger-r
- --intrabar-exits / --no-intrabar-exits
- --swing-stop-recent / --no-swing-stop-recent
- --slippage-pct
- --end-days-ago

If you propose a parameter and the CLI flag doesn't exist, document this in
HUMAN_NOTES.md under `## CLI Flags Needed`.

---

## Forbidden Modifications

NEVER propose changes to:

- Strategy direction (long_only flag)
- vwap_meanrev_1h status (locked DISABLED until late 2026)
- pullback_vwap, macd (RETIRED)
- Bot mode flags (SHADOW vs LIVE)
- Position sizing rules (2% risk, $5 min notional)
- Cooldown periods
- Hybrid exit logic
- Anything in backend/, frontend/, or strategy.py files

If a hypothesis genuinely requires modifying one of the above, write to
HUMAN_NOTES.md under `## Code Changes Needed` and let a human handle it.