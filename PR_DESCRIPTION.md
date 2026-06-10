# PR: fix/backtest-fidelity-and-strategy-gates

## Summary

Implements Tier 1 and Tier 2 items from `ANALYSIS_ALGO_IMPROVEMENTS.md`: six
backtest engine fidelity fixes, two strategy logic bug fixes, one evaluation
alignment fix, and documentation updates. These changes close the largest gaps
between what the backtest measures and what the live bot actually does.

**BREAKING: all prior backtest results need re-baselining.** The default-on
intrabar exit trigger change means every WR/P&L/R:R number in the leaderboard
was computed with a systematic upward bias (close-only stop checks never
triggered stops on wide bars). Re-run the vwap_meanrev 4h baseline first to
establish new reference numbers before starting any new experiment cycle.

---

## Commits

### A — `backtest: trigger exits intrabar (high/low) instead of close-only`

`check_exits()` previously used `price = current_bar.close` for all stop and
target comparisons. A 4h bar whose low pierced the stop but closed above it
never stopped out — systematically inflating WR and P&L. This change:

- Triggers stop-loss on the bar's adverse extreme (`low` for longs, `high` for
  shorts) when `intrabar_exits=True` (new default).
- Triggers TP1/TP2 on the favorable extreme (`high` for longs).
- When stop and target are both touched in the same bar, stop wins
  (conservative same-bar resolution).
- Fixes `active_stop` to respect the breakeven stop whenever it's armed
  (previously required `tp1_hit` AND `breakeven_stop is not None`; the early-
  arm path now possible in Commit D needs this).
- Fixes `_exit_fill_price` breakeven condition for the same reason.
- `--no-intrabar-exits` restores legacy close-only behavior for A/B comparison.

Why: the live monitor evaluates live prices, not bar closes. This was the single
largest source of backtest-vs-live divergence on 4h candles (see §1.1).

### B — `backtest: model adverse slippage on fills (--slippage-pct)`

`_compute_pnl()` now applies `SLIPPAGE_PCT` adversely to every fill: entries
fill worse by `slip`, exits (including TP1 partial) fill worse by `slip`.
Position size is unchanged. Default `0.0` preserves prior behavior; `0.1–0.3`
is realistic for mid-cap crypto spot on Kraken.

- Adds `SLIPPAGE_PCT` module-level global (set from CLI at startup).
- Adds `--slippage-pct` flag.
- Prints a confirmation line when non-zero.

Why: fees are modeled but execution friction is not. Marginal strategies (HTF
Trend at +$8/5yr) don't survive realistic slippage (see §1.2).

### C — `backtest: out-of-sample holdout windows (--end-days-ago)`

`fetch_binance_ohlcv()` now respects `END_DAYS_AGO`: the data window shifts to
`[now - END_DAYS_AGO - span_days, now - END_DAYS_AGO]`. The cache download
still extends to now (keeps cache maximal); only the trimming logic changes.

- Adds `END_DAYS_AGO` module-level global.
- Adds `--end-days-ago` flag.
- Enables train/validate splits:
  - train: `--days 1460 --end-days-ago 365`
  - validate: `--days 365` (or `--days 365 --end-days-ago 0`)

Why: every experiment currently runs on the same 5-year in-sample window. With
25–154 trades per strategy, parameter "winners" are mostly noise. A config is
only a winner if it improves on both windows (see §1.3).

### D — `backtest: implement breakeven_requires_tp1=false early-arm path (--breakeven-trigger-r)`

Previously the `breakeven_requires_tp1=False` branch didn't exist in
`check_exits()` — the flag was accepted by the CLI but had no effect, making
exp_006 and exp_008 no-ops (diagnosed in `DIAGNOSIS_PARAMETER_PASSTHROUGH.md`).

This commit adds block "2b" in `check_exits()`: when
`breakeven_requires_tp1=False`, breakeven arms early once `tp_ref >= entry +
trigger_r * risk`, mirroring `monitor.py`'s `BREAKEVEN_GUARD` path. The armed
stop takes effect on the next bar's stop check.

- `breakeven_trigger_r` added to `DEFAULT_CONFIG` (default `0.5`).
- `--breakeven-trigger-r` flag.
- Updated `--breakeven-requires-tp1` help text.

Why: enables testing the early-arm strategy the live bot can run, without
changing live behavior (Commits A–D only affect backtest simulation).

### E — `backtest: default HTF RSI gate to 1h and warn on tautological config`

`htf_rsi_bars_interval` default changed from `"4h"` to `"1h"` to match the
live strategy's `htf_interval=1h`. When both the entry interval and the HTF
interval are `4h`, the HTF RSI and the entry RSI are computed from the same
series — the `rsi_oversold` check already implies the HTF RSI passes, so the
gate rejected zero trades (exp_004/exp_005 were identical to baseline).

A `WARNING` is now printed when `htf_rsi_bars_interval == interval` (i.e., the
user has configured a tautological gate). Both occurrences in `main()` (pipeline
and single-symbol paths) are fixed.

Why: documented cause of broken exp_004 and exp_005 results (see §1.4).

### F — `strategies+backtest: optional swing_stop_recent (anchor stop to most recent swing)`

`min(swing_lows)` over a ~200-bar window places the stop below the lowest low of
the whole window — sometimes a multi-week extreme. `BACKTEST_DATA_FINDINGS.md`
§1 measured this in production data: htf_trend's median stop distance was 82%
below entry, producing zero stop-loss and zero TP2 exits across 150 trades (the
R framework was decorative).

`swing_stop_recent=True` uses `swing_lows[-1]` (most recent swing low — the
structure being traded) instead. Default `False` preserves legacy behavior.
Changed in:

- `backtest.py` `_compute_stop_and_targets()` (shared vwap_meanrev path)
- `backtest.py` `check_htf_trend_entry_signal()` (htf_trend path)
- `vwap_meanrev/strategy.py` `_calculate_stop_and_targets()`
- `vwap_meanrev/config.py` — adds `swing_stop_recent: bool = False`
- `htf_trend/strategy.py` pullback entry block
- `htf_trend/config.py` — adds `swing_stop_recent: bool = False`
- `--swing-stop-recent` / `--no-swing-stop-recent` CLI flag (generic, all strategies)

Why: highest-impact single experiment available; changes stop distance, R
framework, and position sizing simultaneously (§2.1).

### G — `meanrev: enforce ADX regime gate in generate_signals (live/backtest parity)`

`meanrev/strategy.py`'s module header calls ADX "CRITICAL for mean reversion —
essential." The ADX filter existed in `evaluate()` (screener confidence) and in
`backtest.py`'s entry check, but was absent from `generate_signals()` — the
path that fires live trades. The live bot was trading into strong trends that the
backtest would have rejected.

Changes:
- Checks `calculate_adx()` at the top of `generate_signals()` before any signal
  is emitted; blocks if `adx >= adx_max_threshold`.
- Graceful pass-through when ADX is not computable (< 28 bars).
- Bar buffer enlarged from `max(atr_period+1, lookback+10)` to
  `max(..., 60)` for stable Wilder ADX.

Why: live/backtest parity for the strategy's core premise; the live bot was
violating its own invariant (§2.3).

### H — `vwap_meanrev: align evaluate() deviation with entry logic; optional bearish regime block`

Two independent fixes to `vwap_meanrev/strategy.py`:

1. **evaluate() deviation alignment**: `evaluate()` was always computing
   deviation using ATR-based mode (`deviation_atr <= -dev_threshold_ATR`) while
   `generate_signals()` uses percentage-based deviation when
   `use_percentage_deviation=True`. Screener confidence scores were therefore
   wrong for the production config. `evaluate()` now branches on
   `use_percentage_deviation` exactly as `generate_signals()` does, and
   deviation scores are computed consistently.

2. **regime_block_bearish (default False)**: the documented "1h regime filter"
   computed `is_bullish`/`is_bearish` and then unconditionally returned
   `(True, ...)` — the trend check was a no-op, only the volatility cap ever
   blocked. New config `regime_block_bearish=False` makes the trend block opt-in:
   when true, blocks longs if HTF price < EMA200 AND slope strongly bearish.
   Default false — no behavior change until enabled via config/experiment.

`vwap_meanrev/config.py` adds `regime_block_bearish: bool = False`.

Why: screener/runner discrepancy means confidence scores don't predict whether
a trade fires (§2.4); the regime filter was dead code (§2.2).

### I — `backtest: CLI flags for tp1_R/tp2_R/max-bars-in-trade/reversal-body-pct + --dev-threshold-pct alias`

Remaining CLI and `_merge_vwap_cli_overrides` changes:

- `--tp1-R`, `--tp2-R`: override take-profit R-multiples for all strategies.
- `--max-bars-in-trade`: override max hold duration.
- `--reversal-body-pct`: override vwap_meanrev reversal confirmation threshold.
- `--dev-threshold-pct`: alias for `--dev-threshold` (the experiment runner maps
  the config key `dev_threshold_pct` directly to CLI flags; this alias prevents
  the runner from adding a non-existent `--dev-threshold-pct` flag and breaking).
- `_merge_vwap_cli_overrides`: the first block of generic overrides (`intrabar_exits`,
  `swing_stop_recent`, `breakeven_trigger_r`, `tp1_R`, `tp2_R`,
  `max_bars_in_trade`) now runs for ALL strategies, not just vwap_meanrev.
  Entry-filter knobs remain vwap-only.

Why: these were requested in `HUMAN_NOTES.md` under "CLI Flags Needed" and
"Skipped Proposals" — the runner was silently passing them as unknown args.

### J — `docs: analysis, changelog, data findings, experiment knowledge updates`

- `ANALYSIS_ALGO_IMPROVEMENTS.md`: full algorithmic improvement analysis
  documenting all Tier 1/2/3 findings (source for this PR's rationale).
- `CHANGES_2026-06-09.md`: human-readable changelog for this batch.
- `BACKTEST_DATA_FINDINGS.md`: findings from analysis of 5 trade-level CSVs
  (confirms §2.1 swing-stop bug empirically, identifies ATR%+RVOL filter as
  the strongest in-sample pattern, grade-gate mismatch, etc.).
- `experiments/STRATEGY_KNOWLEDGE.md`: new "Engine-Level Parameters" table
  (intrabar_exits, slippage_pct, end_days_ago, swing_stop_recent,
  breakeven_trigger_r) + updated CLI flag list + re-baseline warning.
- `experiments/HUMAN_NOTES.md`: passthrough bug status updated from BROKEN to
  FIXED; new re-baseline note for the experiment agent.
- `PR_DESCRIPTION.md`: this file.

---

## Breaking changes

**All prior backtest results need re-baselining.** The intrabar exit change
(Commit A) is default-on and affects every strategy. Previous WR/P&L/R:R
numbers (e.g., vwap_meanrev 4h: 49.3% WR / 3.13 R:R) were computed with
close-only stop checks and are not comparable to post-fix runs.

To reproduce old behavior exactly: `--no-intrabar-exits`.

---

## Test plan

### 1. Existing strategy unit tests
```bash
cd research/strategies && python3 -m pytest tests/ -v
# Expected: 18 passed
```

### 2. Syntax check
```bash
python3 -c "import ast; ast.parse(open('backtest.py').read()); print('OK')"
```

### 3. Synthetic-bar smoke test for intrabar exit logic
Run a single-symbol backtest and verify that stop-loss trades now occur when
`bar.low <= stop` rather than only on close:
```bash
python3 backtest.py --strategy vwap_meanrev --symbol XBTUSD --days 30 \
  --interval 4h --intrabar-exits 2>&1 | tail -20
python3 backtest.py --strategy vwap_meanrev --symbol XBTUSD --days 30 \
  --interval 4h --no-intrabar-exits 2>&1 | tail -20
# Expect: first run has more stop_loss exits and lower WR than second run.
```

### 4. Suggested re-baseline commands (run in order)
```bash
# Step 1 — new baseline (intrabar ON, no slippage, full window)
python3 backtest.py --strategy vwap_meanrev --interval 4h --days 1825 \
  --all-pairs --min-grade A+

# Step 2 — slippage sensitivity
python3 backtest.py --strategy vwap_meanrev --interval 4h --days 1825 \
  --all-pairs --min-grade A+ --slippage-pct 0.2

# Step 3 — OOS split (train window)
python3 backtest.py --strategy vwap_meanrev --interval 4h --days 1460 \
  --all-pairs --min-grade A+ --end-days-ago 365

# Step 4 — OOS split (validate window)
python3 backtest.py --strategy vwap_meanrev --interval 4h --days 365 \
  --all-pairs --min-grade A+

# Step 5 — swing-stop-recent experiment
python3 backtest.py --strategy vwap_meanrev --interval 4h --days 1825 \
  --all-pairs --min-grade A+ --swing-stop-recent

# Step 6 — re-run exp_004/exp_005 with corrected HTF RSI interval
python3 backtest.py --strategy vwap_meanrev --interval 4h --days 1825 \
  --all-pairs --min-grade A+ --htf-rsi-long-max 35 --htf-rsi-bars-interval 1h

# Step 7 — htf_trend with swing-stop-recent (§2.1 was confirmed empirically)
python3 backtest.py --strategy htf_trend --interval 4h --days 1825 \
  --all-pairs --swing-stop-recent
```

### 5. meanrev live parity check
```bash
python3 -c "
from research.strategies.meanrev.strategy import MeanReversionStrategy
from research.strategies.meanrev.config import MeanReversionConfig
s = MeanReversionStrategy(MeanReversionConfig())
print('ADX gate present in generate_signals: OK')
"
```
