# Long-Only Violation Diagnosis

## Confirmed Behavior

- Config field `long_only` exists at: `research/strategies/vwap_meanrev/config.py:86`
- Default value: `True`
- Backtest produces: **16 long, 61 short** trades over 5 years (77 total; verified from `backtest_trades.csv` and `experiments/results/baseline.json`)

Sample short trade from CSV:

| Field | Value |
|-------|-------|
| symbol | OP/USD |
| side | short |
| entry_price | 1.983 |
| stop_loss | 2.225 |
| tp1_price | 1.741 |
| exit_reason | invalidation_rsi |

Stop above entry and TPs below entry confirm these are genuine short structures, not mislabeled longs.

---

## How sell signals are generated

The live strategy and the backtester use **separate code paths**. Short trades in `backtest_trades.csv` come from the backtester path, not from `VWAPMeanReversionStrategy.generate_signals()`.

### Path A — Live strategy (`strategy.py`) — correctly gated

1. `backend/runner/service.py` calls `self._strategy.generate_signals(bar)` on each bar.
2. `VWAPMeanReversionStrategy.generate_signals()` evaluates long conditions first (lines 584–692).
3. After the long block, the short path is gated:

```694:695:research/strategies/vwap_meanrev/strategy.py
        if self.config.long_only:
            return None
```

4. If `long_only` is False, short logic runs (lines 697–784), returning `side="sell"`:

```752:756:research/strategies/vwap_meanrev/strategy.py
            return TradeIntent(
                strategy_id=self.config.strategy_id,
                symbol=self.config.symbol,
                side="sell",
                intent_type="enter",
```

5. The screener's `evaluate()` path also gates shorts:

```981:982:research/strategies/vwap_meanrev/strategy.py
            # SHORT setup scoring (disabled when long_only)
            elif not self.config.long_only and deviation_atr >= self.config.dev_threshold_ATR and rsi >= self.config.rsi_overbought:
```

With default config (`long_only=True`), live `generate_signals()` never reaches the short block.

### Path B — Backtester (`backtest.py`) — **not connected to strategy config**

The backtester does **not** import or call `VWAPMeanReversionStrategy`. It duplicates entry logic in `check_entry_signal()`:

```870:878:backtest.py
def check_entry_signal(
    bars: list[Bar], cfg: dict, equity: float, risk_pct: float, long_only: bool,
    debug: bool = False,
    htf_bars: Optional[list[Bar]] = None,
) -> Optional[Trade]:
    """
    Evaluate the most-recent bar for a VWAP mean-reversion entry signal.

    Faithfully ports generate_signals() core conditions.
```

Long entries are built at lines 949–991 (`side="long"`). Short entries follow a separate `long_only` parameter guard:

```993:1006:backtest.py
    if long_only:
        return None

    # ── SHORT ──
    dev_short = (price - vwap) / vwap * 100.0 if vwap > 0 else 0.0
    if (
        dev_short >= cfg["dev_threshold_pct"] and
        rsi >= cfg["rsi_overbought"] and
        _reversal_confirmed(bar, vwap, "sell", cfg) and
        not _momentum_excluded(bars, "sell", cfg)
    ):
        entry  = max(price, vwap - atr * 0.05)
        levels = _compute_stop_and_targets(entry, "sell", bars, atr, cfg)
        return _build_trade("short", entry, levels)
```

Pipeline mode (the default when `--symbol` is omitted) calls this with `args.long_only`:

```3087:3095:backtest.py
        trades, equity_curve = run_pipeline_backtest(
            universe_bars,
            btc_bars,
            universe,
            supplies,
            cfg,
            args.starting_equity,
            args.risk_pct,
            args.long_only,
```

The CLI flag defaults to **False**:

```2829:2830:backtest.py
    parser.add_argument("--long-only",        action="store_true",
                        help="Only trade long setups (no shorts)")
```

`action="store_true"` means `args.long_only` is `False` unless the user explicitly passes `--long-only`.

Single-symbol mode hardcodes the opposite:

```3251:3257:backtest.py
        trades, equity_curve = run_backtest(
            bars,
            cfg,
            args.starting_equity,
            args.risk_pct,
            long_only=True,
            strategy=args.strategy,
```

### Config objects involved

| Source | `long_only` present? | Default | Used by backtest? |
|--------|---------------------|---------|-------------------|
| `VWAPMeanReversionConfig` | Yes (`config.py:86`) | `True` | **No** — strategy class not invoked |
| `DEFAULT_CONFIG` dict (`backtest.py:97–125`) | **No** | n/a | Yes — but field absent |
| CLI `--long-only` | n/a | `False` | Yes — pipeline mode only |

`research/strategies/vwap_meanrev/__init__.py` exports the strategy class only; no `__main__.py` exists.

Experiments (`experiments/run_experiments.py`) invoke `backtest.py` without `--long-only`, so all pipeline runs inherit the False default. Baseline was run as pipeline mode (`--days 1826 --interval 4h`, no `--symbol`), producing 77 trades including 61 shorts.

---

## Why the long_only flag is bypassed

### Possibility A: Flag exists but is never read by short-generation code

**Partially true for backtest; false for live strategy.**

- `strategy.py` **does** read `self.config.long_only` before the short block (line 694) and in `evaluate()` (line 982). Live bot behavior is correct.
- `backtest.py` short generation reads a **separate** `long_only: bool` function argument, not `VWAPMeanReversionConfig.long_only` and not any key in `DEFAULT_CONFIG`.

### Possibility B: Flag is read but in the wrong place

**Not the primary cause.** Both code paths that matter gate shorts in the right place structurally (after long evaluation, before short logic). The problem is not placement within a file — it is that two independent enforcement mechanisms exist with conflicting defaults.

### Possibility C: Backtest uses a different config object than live — **CONFIRMED ROOT CAUSE**

This is the actual cause:

1. Live bot loads `VWAPMeanReversionConfig(long_only=True)` via `backend/strategies/registry.py`.
2. Backtest pipeline loads `DEFAULT_CONFIG.copy()` — a plain dict with **no `long_only` key** — and passes `args.long_only=False` to `check_entry_signal()`.
3. Default pipeline mode (`--symbol` omitted → `--all-pairs` auto-set at line 2912–2913) is what produced `backtest_trades.csv` and all `experiments/results/*.json` files.
4. Single-symbol backtest was partially fixed (`long_only=True` hardcoded at line 3256) but pipeline mode was not updated when `long_only` was added to the strategy config (commit `25aeb20`).

There is an internal inconsistency within `backtest.py` itself: pipeline defaults to shorts enabled; single-symbol defaults to long-only.

---

## Suggested Fix

Three options from the brief, assessed:

| Option | Assessment |
|--------|------------|
| **1. Guard in `strategy.py`** | Already implemented (line 694). Fixes live only; does not fix backtest. |
| **2. Delete short code entirely** | Over-aggressive; backtest still has its own short block in `check_entry_signal()`. Would require editing two files and removes future short capability. |
| **3. Backtest guard** | **Recommended.** Minimal, targeted fix in `backtest.py`. |

### Recommended fix (Option 3, config-driven)

1. Add `"long_only": True` to `DEFAULT_CONFIG` in `backtest.py` (mirroring `VWAPMeanReversionConfig`).
2. In pipeline mode, replace `args.long_only` with:

   ```python
   long_only = args.long_only or cfg.get("long_only", False)
   ```

   This makes pipeline behavior match single-symbol mode and the strategy dataclass default. Users who want to test shorts can still pass `--long-only` as an explicit opt-in to long-only (or a future `--allow-shorts` flag could be added).
3. Optionally align single-symbol mode to use the same expression instead of the hardcoded `long_only=True` at line 3256, so both modes share one rule.

**Justification:** One file change, no live-bot risk, restores parity between backtest and production config. Option 1 is already done and insufficient. Option 2 is unnecessary given the strategy may re-enable shorts later. Option 3 fixes the actual divergence without duplicating logic in a post-filter.

---

## Risk Assessment

### Live bot behavior after fix

**No change expected.** The live runner already uses `generate_signals()` with `long_only=True`. Bot is in SHADOW mode (paper trades only). The bug affects research/backtest artifacts, not production signal generation.

### Backtest results after fix

- Trade count drops from 77 → ~16 (long-only entries only).
- Contaminated baseline metrics (46.8% win rate, $+10.61 PnL, 2.62 R:R) are not representative of the intended long-only strategy.
- From current CSV: longs alone sum to **$+4.46** (8/16 wins); shorts sum to **$+6.15** (28/61 wins). Total PnL was inflated by short trades that the live bot would never take. After fix, sample size is small (~16 trades over 5 years) — statistical confidence drops sharply; long-only profitability must be re-evaluated on its own merits.
- All files under `experiments/results/` (baseline + exp_001 through exp_020) must be re-run.

### Test coverage

| Location | Long-only coverage |
|----------|-------------------|
| `research/strategies/vwap_meanrev/tests/test_strategy.py` | **None.** Tests initialization, insufficient data, symbol mismatch, Task L gates. No assertion that `generate_signals()` returns no sell intent when `long_only=True`. |
| `backend/tests/test_strategy_registry.py` | `long_only: True` appears only in `PULLBACK_VWAP_CONFIG`, not `VWAP_CONFIG`. |
| `backend/tests/` (runner, screener) | No long-only assertions for vwap_meanrev. |

No existing test would have caught the pipeline backtest divergence.

---

## Affected Data

- `backtest_trades.csv` — 61 of 77 rows are invalid short trades for a long-only strategy.
- `experiments/results/baseline.json` — reports 77 trades; contaminated.
- All `experiments/results/exp_*.json` files — same pipeline invocation path; all contaminated.
- `experiments/leaderboard.md` — rankings based on contaminated results.

---

## Recommendation

Apply **Option 3** (backtest config alignment): add `"long_only": True` to `DEFAULT_CONFIG` and wire pipeline mode to honor it, matching the existing single-symbol hardcode and `VWAPMeanReversionConfig`. Do not defer — the current 5-year baseline and experiment suite systematically overstate trade frequency and mix in 61 short trades the live bot will never execute, making all parameter-sweep conclusions unreliable for the stated long-only design. Re-run baseline and all experiments after the fix; add a unit test asserting `check_entry_signal(..., long_only=True)` never returns `side="short"` and that `generate_signals()` with default config never returns `side="sell"`.
