# Parameter Passthrough Diagnosis

Investigation date: 2026-06-02. Scope: `vwap_meanrev` pipeline backtests (`--interval 4h`, 5-year window) and CLI flags reported in experiment evidence.

---

## Bug Location

Two separate root causes produce identical metrics; neither is “CLI never reaches `cfg`.”

### `htf_rsi_long_max`

| Location | Role |
|----------|------|
| `research/strategies/vwap_meanrev/config.py` L44–45 | Field **exists** (`Optional[float]`, default `None`). |
| `research/strategies/vwap_meanrev/strategy.py` L580–636, L943–958 | Field **is read** for live/screener entry (`_latest_htf_rsi` uses `config.htf_interval`, default **1h**). |
| `backtest.py` L906–913, L841–852 | Field **is read** in `check_entry_signal()` via `_vwap_htf_rsi_at(htf_bars, …)`. |
| `experiments/experiments.yaml` L10–11, L40–54 | Experiments force **`interval: 4h`**; HTF RSI klines default to **`htf_rsi_bars_interval: "4h"`** (`backtest.py` L122–123, L2879). |

**Why it is silently ignored:** On every signal bar, entry RSI already requires `rsi <= rsi_oversold` (30) on the **same** 4h series used for the “HTF” RSI gate. The HTF RSI value equals the entry-bar RSI, so thresholds 35 and 40 never reject a trade that passed the oversold check. The parameter is wired but **logically tautological** under the experiment timeframe setup.

Live bot behavior differs: `strategy.py` computes HTF RSI on `htf_interval` (1h), not on the 4h entry chart.

### `breakeven_requires_tp1`

| Location | Role |
|----------|------|
| `research/strategies/vwap_meanrev/config.py` | Field **does not exist** (exit/monitor concern only). |
| `research/strategies/vwap_meanrev/strategy.py` | **Never read** (expected — exits are not simulated in strategy). |
| `backend/positions/monitor.py` L1276–1278, L1394–1418 | Live logic uses **`backend.config.BREAKEVEN_REQUIRES_TP1`** (env), not strategy DB params. |
| `backtest.py` L124, L1681–1686, L2599–2600, L2872, L3016 | Field **is read** in `check_exits()` when TP1 is hit. |
| `experiments/run_experiments.py` L90–93 | Booleans: append flag **only when `true`**; **`false` is omitted** (no `--no-breakeven-requires-tp1`). |

**Why experiments match baseline:** `DEFAULT_CONFIG["breakeven_requires_tp1"]` is already **`True`** (`backtest.py` L124). `exp_006` / `exp_008` only set `breakeven_requires_tp1: true`, which reproduces the default. Passing `--breakeven-requires-tp1` on the CLI also leaves `True` — same outcome.

**Secondary gap:** When `breakeven_requires_tp1` is `false`, `monitor.py` can still arm breakeven at `BREAKEVEN_GUARD_TRIGGER_PCT` (+1% profit, L1409–1418). `backtest.py` only sets `breakeven_stop` inside `if cfg.get("breakeven_requires_tp1", True)` on TP1 (L1681); there is **no** legacy early-breakeven path. So even a correct `--no-breakeven-requires-tp1` run would **not** mirror live “false” behavior until that path is implemented.

---

## Working Path (for reference)

`--long-min-volume-ratio` → strategy entry filter that changes trade count when tightened.

1. **CLI:** `parser.add_argument("--long-min-volume-ratio", …, dest="long_min_volume_ratio")` (`backtest.py` L2668–2673).
2. **Merge:** `_merge_vwap_cli_overrides()` sets `cfg["long_min_volume_ratio"]` when arg is not `None` (L2591–2592).
3. **Simulation:** `run_pipeline_backtest()` → `check_entry_signal(..., htf_bars=htf_seg)` (L2280–2287).
4. **Gate:** `long_vol_ok = long_min is None or vol_ratio >= long_min` (L910–911); failure rejects entry (L966+).
5. **Live parity:** Same field on `VWAPMeanReversionConfig` and same checks in `strategy.py` L626–628.

`--atr-stop-mult` follows the same merge path (L2597–2598) into `_compute_stop_and_targets()` (L823–824), which changes stops, R-multiples, and P&L.

---

## Broken Path

### `breakeven_requires_tp1`

```
experiments.yaml: breakeven_requires_tp1: true
    → run_experiments.py: cmd += ["--breakeven-requires-tp1"]   # only if true
    → backtest.py main: cfg["breakeven_requires_tp1"] = True    # same as DEFAULT_CONFIG default
    → check_exits() L1681: if True → set breakeven_stop on TP1
```

**Drop point for experiments:** No effective override — baseline and `exp_006`/`exp_008` are the same boolean. The runner never tests `false` (HUMAN_NOTES L87).

**Drop point for meaningful CLI test:** User compared `--breakeven-requires-tp1` (true) vs no flag (default true) → identical by design, not a passthrough failure.

**Live vs backtest:** Live reads `BREAKEVEN_REQUIRES_TP1` from env (`backend/config.py` L43), not `cfg` from backtest or `VWAPMeanReversionConfig`.

### `htf_rsi_long_max`

```
experiments.yaml: htf_rsi_long_max: 40
    → run_experiments.py: --htf-rsi-long-max 40
    → _merge_vwap_cli_overrides(): cfg["htf_rsi_long_max"] = 40
    → main: fetch 4h HTF bars per symbol (L2874–2893)
    → check_entry_signal(): htf_rsi_val = _vwap_htf_rsi_at(4h bars, …)
    → htf_long_ok = htf_rsi_val <= 40
```

**Effective drop point:** Logical, not wiring — with `interval: 4h` and `htf_rsi_bars_interval: 4h`, `htf_rsi_val` equals entry RSI, and `rsi <= 30` implies `htf_rsi_val <= 35` and `<= 40` always. Changing max between 35 and 40 changes nothing.

---

## Same Pattern for HTF RSI

**Not the same root cause as breakeven.**

| Aspect | `htf_rsi_long_max` | `breakeven_requires_tp1` |
|--------|-------------------|---------------------------|
| In `config.py`? | Yes | No |
| In `strategy.py`? | Yes (1h HTF) | No |
| In `backtest.py`? | Yes (entry) | Yes (exit) |
| CLI → `cfg`? | Works | Works |
| Why identical results? | Tautological gate at 4h/4h | Default/alternate value never tested |
| Live implementation | `strategy.py` + `htf_interval` | `monitor.py` + env `BREAKEVEN_REQUIRES_TP1` |

`exp_007` can show volume effects from `long_min_volume_ratio` while the HTF portion still contributes nothing for the same 4h/4h reason.

---

## Suggested Fix

Minimal, targeted changes (no code written in this pass):

### HTF RSI (pick one or combine)

1. **Backtest alignment:** When `htf_rsi_long_max` is set, compute RSI on bars at `htf_interval` from strategy config (**1h**), not on `htf_rsi_bars_interval` when it equals the pipeline entry interval. Reuse the same interval live uses (`strategy.py` L336: `self.config.htf_interval`).
2. **Experiment hygiene:** Run HTF RSI sweeps at `interval: 15m` or `1h` with explicit `--htf-rsi-bars-interval 4h` (or 1h) so entry RSI and HTF RSI are on different series.
3. **Optional:** Add `htf_rsi_long_max` to DB seeds if live configs should vary it per strategy instance.

### Breakeven (pick one or combine)

1. **`run_experiments.py`:** For `bool` overrides, emit `--no-breakeven-requires-tp1` when value is `false` (BooleanOptionalAction already exists on `backtest.py` L2703–2707).
2. **`check_exits()`:** When `breakeven_requires_tp1` is `false`, implement legacy breakeven arming (e.g. at `BREAKEVEN_GUARD_TRIGGER_PCT` or `breakeven_trigger_r` from STRATEGY_KNOWLEDGE) to match `monitor.py` L1409–1418.
3. **Experiments:** Re-run with `breakeven_requires_tp1: false` vs baseline `true`, not redundant `true` rows.
4. **Live parity (optional):** Map strategy DB parameter → env or monitor config if per-strategy breakeven is desired; today it is global env only.

**Not recommended:** Replacing all of `check_exits()` with `monitor.py` imports — large scope; monitor is async, Redis/ Kraken-coupled, and includes 48h opportunity filter and trailing stops not present in backtest.

---

## Risk Assessment

| Fix area | Live bot risk | Tests |
|----------|---------------|--------|
| HTF RSI interval fix in backtest | **Low** for production if live already uses 1h HTF in `strategy.py`. Could change which pairs pass screener vs backtest until aligned. | `research/strategies/vwap_meanrev/tests/test_strategy.py` (HTF fetch mock); `test_strategy.py` backtest helpers for `_vwap_htf_rsi_at`. No test asserts 4h pipeline HTF gate is non-tautological. |
| Breakeven false path in backtest | **None** on live until env/DB wiring changes. | `backend/tests/test_breakeven_guard.py` covers **monitor** only, not `backtest.check_exits`. |
| `run_experiments` false bool | **None** on live; enables valid A/B backtests. | None. |
| Changing `BREAKEVEN_REQUIRES_TP1` env on corpus | **High** — alters when stops move to breakeven for all open positions. | Integration tests in `test_msdd_v3_lifecycle.py`, `test_breakeven_guard.py`. |

After fixes, any leaderboard row that only varied the broken knobs should be treated as invalid.

---

## Affected Experiments

All listed IDs used `interval: 4h` and are **unreliable** for the broken parameters (metrics may be valid only for co-varied knobs like volume or ATR).

| Experiment ID | Broken override | Notes |
|---------------|-----------------|-------|
| `baseline` | — | Reference; no HTF/breakeven override. |
| `exp_004_htf_rsi_40` | `htf_rsi_long_max: 40` | Equivalent to baseline on 4h/4h. |
| `exp_005_htf_rsi_35` | `htf_rsi_long_max: 35` | Same as baseline and exp_004. |
| `exp_006_breakeven_requires_tp1` | `breakeven_requires_tp1: true` | Same as default; not a test. |
| `exp_007_volume_15_htf_40` | HTF portion | Volume half may be valid; HTF half is not. |
| `exp_008_volume_15_breakeven_tp1` | `breakeven_requires_tp1: true` | Volume half may be valid; breakeven half is not. |

**Reliable in same queue (for reference):** `exp_001`–`exp_003` (volume), `exp_009`–`exp_010` (ATR stop).

**Re-run after fix:** `exp_004`, `exp_005`, `exp_006`, `exp_007` (HTF leg), `exp_008` (breakeven leg), plus new experiments with `breakeven_requires_tp1: false` and HTF RSI tested at entry TF ≠ HTF TF (e.g. 15m + 4h or 4h + 1h).

`experiments/results/*.json` was not present in the workspace at diagnosis time; conclusions are from code trace and `experiments/HUMAN_NOTES.md` / `experiments/ANOMOLY_DETECTION.md` reported metrics (86 trades, 47.7% WR, $17.12 P&L).

---

## Exit logic: `backtest.py` vs `monitor.py` (divergences)

Shared intent (TP1 partial, breakeven after TP1, stop/TP2, VWAP/RSI invalidation) but different implementations:

| Behavior | `backtest.check_exits()` | `monitor.py` |
|----------|-------------------------|--------------|
| Breakeven config source | `cfg["breakeven_requires_tp1"]` | `BREAKEVEN_REQUIRES_TP1` env |
| Breakeven when flag false | No early breakeven | `BREAKEVEN_GUARD_TRIGGER_PCT` (+1%) path |
| TP1 detection | Intrabar close vs TP1 price | Redis TP1 keys |
| Trailing stop | Not simulated | Yes |
| 48h opportunity filter | Not simulated | `_check_48h_opportunity_filter` |
| Max hold | `max_bars_in_trade` bars | Configurable candles + strategy-specific |
| Invalidation | In-process VWAP/RSI on bar history | Screener indicator snapshot |
| Fees on breakeven | `MAKER_FEE + TAKER_FEE` multiplier | `KRAKEN_FEE_PCT` per unit |

`htf_rsi_long_max` affects **entry** in backtest (`check_entry_signal`), not monitor. Monitor does not implement HTF RSI gating.
