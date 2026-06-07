# meanrev Diagnosis

## Verified Data

Source: [`backtest_trades.csv`](backtest_trades.csv) (584 rows, all meanrev pipeline trades on 4h).

| Metric | Value |
|--------|-------|
| Trade count | **584** |
| Win rate | **42.1%** (246 wins / 338 losses) |
| Total P&L | **-$167.46** |
| Avg win | **$1.9122** |
| Avg loss | **-$1.8871** |
| Win:loss R:R | **1.013** (matches user's 1.01) |
| TP1 hit rate | **17.0%** (99/584) |
| TP2 exit rate | **8.0%** (47/584) |

**Exit reason distribution**

| exit_reason | count | % |
|-------------|------:|--:|
| invalidation_rsi | 412 | 70.5% |
| stop_loss | 105 | 18.0% |
| tp2 | 47 | 8.0% |
| max_hold | 20 | 3.4% |
| tp1 | 0 | 0.0% |

Note: `tp1` never appears as a final exit reason because TP1 is a **partial** close only ([`backtest.py:1691-1705`](backtest.py)); the remainder exits via another reason.

**P&L by exit reason**

| exit_reason | n | total P&L | avg P&L | win rate |
|-------------|--:|----------:|--------:|---------:|
| stop_loss | 105 | -$335.30 | -$3.19 | 5.7% |
| invalidation_rsi | 412 | -$67.75 | -$0.16 | 42.0% |
| tp2 | 47 | +$183.06 | +$3.90 | 100% |
| max_hold | 20 | +$52.54 | +$2.63 | 100% |

**Trades that reach TP1 vs not**

- `tp1_hit=False` (485 trades): avg **-$1.02**, total **-$496.62**
- `tp1_hit=True` (99 trades): avg **+$3.33**, total **+$329.17**

**Sample trades (entry / exit / stop / tp / pnl)**

| # | symbol | exit_reason | entry | exit | stop | tp1 (+R) | tp2 (+R) | pnl | bars | tp1_hit |
|---|--------|-------------|-------|------|------|----------|----------|-----|------|---------|
| 1 | VTHO/USD | stop_loss | 0.005680 | 0.005447 | 0.005447 | 0.005913 (1.00R) | 0.006146 (2.00R) | -$5.50 | 4 | False |
| 2 | BAND/USD | invalidation_rsi | 5.849700 | 5.977300 | 5.415312 | 6.284088 (1.00R) | 6.718475 (2.00R) | +$1.17 | 4 | False |
| 144 | HBAR/USD | invalidation_rsi | 0.100700 | 0.102500 | 0.097619 | 0.103781 (1.00R) | 0.106861 (2.00R) | +$0.96 | 4 | False |
| 7 | ATOM/USD | tp2 | 9.608000 | 11.018176 | 8.902912 | 10.313088 (1.00R) | 11.018176 (2.00R) | +$3.39 | 11 | True |
| 11 | OGN/USD | max_hold | 0.573900 | 0.648200 | 0.526800 | 0.621000 (1.00R) | 0.668100 (2.00R) | +$5.93 | 12 | True |
| 8 | REQ/USD | stop_loss | 0.126400 | 0.108126 | 0.108126 | 0.144674 (1.00R) | 0.162949 (2.00R) | -$5.23 | — | False |

TP distances consistently match `tp1_R=1.0` and `tp2_R=2.0` from [`MEANREV_DEFAULT_CONFIG`](backtest.py) lines 168-169.

---

## Hypothesis Evaluation

### H1: Strategy never hits TPs because exits trigger first — **CONFIRMED**

**Evidence**

- 70.5% of trades exit via `invalidation_rsi`; **350/412 (85%)** of those exit at exactly **4 bars** (minimum hold before invalidation can fire).
- Median `bars_held` for invalidation = **4.0**; median price R at exit ≈ **0.015R** (essentially flat).
- Only **17%** hit TP1; only **8%** reach TP2.
- Trades that hit TP1 are strongly profitable (+$329 total); trades that do not are strongly negative (-$497 total).

**Root mechanism (code bug, not tuning)**

Entry ([`backtest.py:1293-1294`](backtest.py)) requires `RSI < rsi_oversold_threshold` (default **40**).

Exit ([`backtest.py:1743-1751`](backtest.py)) fires after `invalidation_rsi_candles` (default **4**) when `RSI < invalidation_rsi_long_floor` (default **40** via `.get(..., 40)`).

`MEANREV_DEFAULT_CONFIG` ([`backtest.py:176-177`](backtest.py)) sets `invalidation_rsi_candles: 4` but did **not** override `invalidation_rsi_long_floor` or require RSI recovery before invalidation.

For mean reversion longs, **still being oversold after 4 bars is expected**, not invalidation. The shared VWAP-style invalidation rule is inverted for this entry thesis. Compare [`HTF_TREND_DEFAULT_CONFIG`](backtest.py) which explicitly sets `invalidation_rsi_long_floor: 35` and `invalidation_rsi_candles: 6` — meanrev inherited generic defaults instead.

Live monitor repeats the same pattern ([`backend/positions/monitor.py:1184`](backend/positions/monitor.py)): exit long when `rsi < 40` after N candles, with comment claiming RSI should have "reverted to neutral (> 40)" but code never checks that recovery occurred.

### H2: TP/stop math wrong for meanrev — **REJECTED**

**Evidence**

- [`check_meanrev_entry_signal`](backtest.py) (lines 1323-1333) computes stop and TP independently: `stop = min(lower_band - buffer, entry - atr*mult)`, `tp1 = entry + risk*tp1_R`, `tp2 = entry + risk*tp2_R`.
- CSV samples show exact 1.00R / 2.00R distances.
- Stop-loss fills match stop price (exit_price == stop_loss on sampled stop_loss rows).
- Does **not** use `_compute_stop_and_targets()` (that path is VWAP-specific); meanrev has its own function.

### H3: ADX filter not working — **WEAK / secondary**

**Evidence**

- Backtest entry **does** enforce ADX: [`backtest.py:1287-1295`](backtest.py) `adx_pass = adx is None or adx < adx_max_threshold` (threshold 30).
- Pipeline D2 substitute also requires `adx < 30` ([`backtest.py:1968`](backtest.py)).
- Month-level performance is mixed: 2024-03 (+$42, 66.7% WR) vs 2024-12 (-$18, 36.8% WR) — not a clean "trending months lose" pattern.
- **Separate live-path gap**: [`generate_signals()`](research/strategies/meanrev/strategy.py) (lines 237-258) checks BB+RSI only — **no ADX/ATR gate** in the live entry path (ADX only in `evaluate()` for screener confidence). Backtest is stricter than live entry, but this does not explain the 584-trade CSV results.

Minor fail-open: `adx_pass` is True when `adx is None` ([`backtest.py:1295`](backtest.py)).

### H4: Strategy concept doesn't work in crypto — **REJECTED (premature)**

**Evidence**

- Trades reaching TP2: 47/47 winners, +$183.
- Trades with `tp1_hit=True`: +$329 total, +$3.33 avg.
- Negative expectancy is driven by premature invalidation (-$68) and stops (-$335), not by TP logic failing when reached.
- Cannot conclude concept failure until exit logic is fixed.

### H5: Sign error / long-short inversion — **REJECTED**

**Evidence**

- All 584 trades are `side=long` ([`MEANREV_DEFAULT_CONFIG long_only: True`](backtest.py), [`MeanReversionConfig.long_only`](research/strategies/meanrev/config.py)).
- P&L is not symmetric around zero due to exit-type skew; tp2/max_hold are 100% winners, stop_loss 94% losers.
- No inverted symbol pattern observed.

---

## Most Likely Root Cause

**H1 — contradictory RSI invalidation exit shared from VWAP strategies**

Confidence: **95%**

Entry requires RSI < 40 (oversold). After the minimum 4-bar hold, exit fires when RSI is **still** < 40. On 4h bars that is ~16 hours — often insufficient for a 1R–2R mean-reversion move. Result: mass early flat exits, 1.01 win:loss size ratio, 42% WR with negative expectancy, and max drawdown exceeding starting equity from accumulated small losses + stops.

The suspicious 1.01 R:R is a **symptom** of flat invalidation exits averaging ~0R, not proof of correct symmetric TP/stop behavior.

---

## Quick Test

Re-run the existing 5-year meanrev backtest with failed-recovery RSI invalidation enabled (`invalidation_rsi_requires_recovery: True`, `invalidation_rsi_recovery_level: 45`).

**Confirm if diagnosis is correct:**

| Metric | Before fix | Expected if H1 correct |
|--------|---------|------------------------|
| invalidation_rsi exits | 70.5% | drops sharply |
| tp1_hit rate | 17.0% | rises materially (target >35%) |
| tp2 exit rate | 8.0% | rises |
| total P&L | -$167 | improves significantly |

**CSV-only sanity check (no re-run):** Filter `exit_reason == 'invalidation_rsi' AND bars_held == 4` → 350 trades, mean exit R ≈ -0.02, total P&L ≈ -$103. These are trades killed before TP1 with ~0R outcomes.

Alternative CSV column check: compare `tp1_hit=True` vs `False` P&L (already shows +$3.33 vs -$1.02 avg) — strong proxy for "allowed to reach target vs cut early."

---

## Recommended Next Steps

**C) Code fix** (implemented)

1. **Backtest config** — Added meanrev-specific failed-recovery RSI invalidation in [`MEANREV_DEFAULT_CONFIG`](backtest.py): only invalidate after RSI has crossed above recovery level (45), then dropped back below floor (40).
2. **Live monitor** — Aligned [`backend/positions/monitor.py`](backend/positions/monitor.py) mean-reversion block with the same failed-recovery semantics.
3. Re-run 5-year baseline and compare exit_reason distribution before/after.

Do **not** deprecate yet. Edge exists in trades that reach TP1/TP2.

---

## Risk Assessment (Code fix)

| Risk | Assessment |
|------|------------|
| Fix works | **High (80%+)** — logic contradiction is clear; tp1_hit trades already profitable |
| Introduces new bugs | **Low–medium** — scoped to exit config / monitor branch; main risk is over-loosening exits → larger stop losses or longer holds |
| False positive | **Low** — 350 bar-4 invalidations with ~0R is too structural to be random |
| Niche viability | Mean reversion on alts in ranging regimes may still work once exits stop sabotaging entries |

---

## Post-Fix Verification (5-year pipeline re-run)

Re-ran: `python3 backtest.py --years 5 --strategy meanrev --interval 4h --output backtest_trades_fixed.csv`

| Metric | Baseline | After fix | Delta |
|--------|----------|-----------|-------|
| Trades | 584 | 443 | -141 |
| Win rate | 42.1% | 45.4% | +3.3pp |
| Total P&L | -$167.46 | -$149.83 | +$17.62 |
| TP1 hit rate | 17.0% | 30.0% | +13.0pp |
| invalidation_rsi exits | 412 (70.5%) | 37 (8.4%) | **-375** |
| tp2 exits | 47 (8.0%) | 49 (11.1%) | +2 |
| stop_loss | 105 (18.0%) | 178 (40.2%) | +73 |
| max_hold | 20 (3.4%) | 179 (40.4%) | +159 |

**H1 confirmed:** failed-recovery invalidation eliminates the bar-4 mass exit bug. Trades now reach TP1/TP2 or exit via stop/max_hold instead of premature flat invalidation.

**Remaining issue:** strategy is still slightly negative overall (-$150 on $500 over 5y). Exit fix removes the catastrophic bug but does not prove full profitability — further entry tuning or timeframe work may still be needed.

---

**Diagnosis: meanrev needs code fix**
