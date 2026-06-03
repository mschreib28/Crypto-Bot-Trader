# Pre-flight: Long-only enforcement & sweep readiness

Generated: 2026-06-03 (post `long_only` fix in `backtest.py` for vwap_meanrev)

## Summary

| Strategy | Config `long_only` | Backtest entry path | Long-only in backtest? | Live strategy shorts? |
|---|---|---|---|---|
| vwap_meanrev | Yes (`True` default) | `check_entry_signal` | Yes (config + gate) | Gated when `long_only=True` |
| volatility_breakout | **No field** | `check_volatility_breakout_entry_signal` | **Implicit** (long-only impl) | **Can emit shorts** |
| htf_trend | **No field** | `check_htf_trend_entry_signal` | **Implicit** (long-only impl) | **Can emit shorts** |
| meanrev | **No field** | `check_meanrev_entry_signal` | **Implicit** (long-only impl) | **Can emit shorts** |

**Sweep interpretation:** Pipeline backtests for VB, HTF, and meanrev do not take a `long_only` parameter; entry functions only open `side="long"` trades. Prior profitable VB/HTF numbers were not inflated by backtest shorts. Live trading parity is a separate concern (strategy modules still document Sell paths).

---

## vwap_meanrev

### Strategy config (`research/strategies/vwap_meanrev/config.py`)

- `long_only: bool = True` — explicit, documented as permanent long-only design.

### Backtest config (`backtest.py` `DEFAULT_CONFIG`)

- `"long_only": True` — mirrors `VWAPMeanReversionConfig`.

### Backtest path

- **Single-symbol:** `run_backtest` → `check_entry_signal(..., long_only, ...)`; shorts blocked at lines 994–996 via `cfg.get("long_only", True)`.
- **Pipeline:** `run_pipeline_backtest` → same `check_entry_signal` with `cfg.get("long_only", True)` (line ~3103). CLI `--long-only` / `--no-long-only` can override via `_merge_vwap_cli_overrides`.
- `run_experiments.py` invokes `backtest.py` with default pipeline (`--all-pairs`); no overrides in sweep YAML → `long_only` stays **True**.

**Status:** Fully enforced for sweep baselines.

---

## volatility_breakout

### Strategy config (`research/strategies/volatility_breakout/config.py`)

- **No `long_only` field.** Docstring/strategy class describes both Buy and Sell (breakout below lower BB).

### Backtest config (`VOLATILITY_BREAKOUT_DEFAULT_CONFIG`)

- **No `long_only` key.**

### Backtest path

- `check_volatility_breakout_entry_signal` — docstring: "long only"; only bullish breakout logic; always returns `Trade(side="long")`.
- `run_pipeline_backtest` does **not** pass `long_only` to this function (parameter unused for this strategy).

**Gap (document only):** No configurable `long_only` in config or backtest dict; enforcement is **hard-coded long-only** in the backtest entry function, not a shared flag. Live `VolatilityBreakoutStrategy` may still generate short signals — backtest sweep results are long-only by implementation, not by an explicit config contract.

---

## htf_trend

### Strategy config (`research/strategies/htf_trend/config.py`)

- **No `long_only` field.** Strategy docstring lists Sell (bearish HTF + pullback).

### Backtest config (`HTF_TREND_DEFAULT_CONFIG`)

- **No `long_only` key.**

### Backtest path

- `check_htf_trend_entry_signal` — docstring: "long only"; requires price above EMA200, bullish reversal; always `side="long"`.
- Pipeline path same as VB — no `long_only` argument on entry check.

**Gap (document only):** Same pattern as VB — implicit long-only in backtest only; live strategy can still emit shorts.

---

## meanrev

### Strategy config (`research/strategies/meanrev/config.py`)

- **No `long_only` field.** Docstring lists Sell (upper BB + RSI overbought).

### Backtest config (`MEANREV_DEFAULT_CONFIG`)

- **No `long_only` key.**

### Backtest path

- `check_meanrev_entry_signal` — docstring: "Entry criteria (long only)"; oversold/lower-band logic only; always `side="long"`.

**Gap (document only):** Implicit long-only in backtest; live `MeanReversionStrategy` may still emit sell signals.

---

## Strategy/Timeframe Errors

_(Populated after 60-day smoke tests — see below.)_

---

## Estimated sweep runtime

Assumptions: full Kraken USD universe pipeline (`backtest.py` default `--all-pairs`), 1826 days (~5 years), local OHLC cache warm after first run.

| Run | Interval | Bars/day (approx) | Relative bar load vs 4h | Est. runtime |
|---|---|---|---|---|
| Baseline | 4h | 6 | 1× | **~15 min** (observed) |
| vwap_meanrev | 5m | 288 | **~48×** | **~6–12 h** (fetch + replay) |
| vwap_meanrev | 15m | 96 | ~16× | **~2–4 h** |
| vwap_meanrev | 1h | 24 | ~4× | **~45–90 min** |
| volatility_breakout | 1h | 24 | ~4× | **~45–90 min** |
| volatility_breakout | 4h | 6 | 1× | **~15–25 min** |
| htf_trend | 1h | 24 | ~4× | **~45–90 min** (+ daily BTC fetch) |
| htf_trend | 4h | 6 | 1× | **~15–25 min** |
| meanrev | 1h | 24 | ~4× | **~45–90 min** |
| meanrev | 4h | 6 | 1× | **~15–25 min** |

**Total (1 baseline + 9 experiments):** roughly **12–20 hours** if run sequentially with warm cache; first cold-cache run may add 1–3 h of Binance fetches. **5m vwap_meanrev dominates** wall time.

**Suggested staging:**

1. All **4h** + **1h** jobs first (~3–4 h): baseline, sweep_003–005, sweep_007–009.
2. **15m** vwap (~2–4 h): sweep_002.
3. **5m** vwap last (~6–12 h): sweep_001.

Smoke tests (60 days) scale roughly linearly: ~**1/30** of full runtime per job (~0.5–25 min each depending on interval).
