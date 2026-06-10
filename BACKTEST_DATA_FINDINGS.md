# Backtest Data Findings — 2026-06-09

Source: 5 trade-level CSVs in `data/`. Note `backtest_trades.csv` and `backtest_trades_baseline.csv` are byte-identical (584 trades, 2021-07 → 2026-05). The 584-trade run has no VWAP entry logging, so it is not vwap_meanrev/pullback — most likely meanrev 4h (please confirm which experiment produced it). All of these runs predate today's intrabar-exit fix, so absolute numbers are optimistic; the *relative* patterns below are still informative.

## Headline numbers

| File | Trades | WR | P&L | R:R | Max DD | Exit mix |
|---|---|---|---|---|---|---|
| main/baseline | 584 | 42.1% | -$167 | 1.01 | -$189 | 71% invalidation_rsi, 18% stop, 8% tp2 |
| "fixed" variant | 443 | 45.4% | -$150 | 0.93 | -$171 | 40% max_hold, 40% stop, 11% tp2 |
| htf_trend_4h | 150 | 40.0% | +$15 | 2.12 | -$5.9 | 59% invalidation_rsi, 41% max_hold |
| htf_trend rsi_prefix | 160 | 38.1% | +$11 | 2.16 | -$4.8 | 71% invalidation_rsi, 29% max_hold |

## Finding 1 — htf_trend's stops are 80% away from entry (swing-stop bug confirmed in data)

Median stop distance in the htf_trend files is **82% below entry** (mean 78%, max 120% — below zero). This is the `min(swing_lows)` bug from ANALYSIS_ALGO_IMPROVEMENTS.md §2.1 measured in the wild: stops anchor to the lowest swing low of the entire multi-year window. Consequences visible in the data: **zero** stop_loss exits and **zero** tp2 exits in 150 trades (targets are 2R away = +160%); every trade exits via RSI invalidation or max_hold; and 2%-risk sizing over an 80% stop distance produces tiny positions (hence the small ±$ numbers). The R framework for htf_trend is currently decorative. The new `--swing-stop-recent` flag is the direct A/B for this and is likely the single highest-impact experiment available.

(The 584-trade run's stop distances are sane — median 5.1% — because its stop is computed differently.)

## Finding 2 — Stops lose ~1.7x what winners make

In the main run: average tp2 win +$3.89, average stop -$3.19, average overall win only +$1.91. Despite tp2_R=2.0, the realized R:R is 1.01. 93% of stop-outs happen within 4 bars of entry — entries are immediately wrong, not slowly decaying. And these were *close-only* stop checks; with intrabar triggers (now fixed) the stop count will rise further. This is an entry-quality problem more than an exit problem.

## Finding 3 — A volatility + RVOL band separates winners from losers

The strongest cross-sectional pattern, and it **replicates across both the main and "fixed" runs**:

- entry_atr_pct ≥ 4: +$28 over 214 trades (49–53% WR) vs **-$197 over 366 trades** below 4 (37-39% WR)
- entry_rvol 3–5: +$20 (50% WR); RVOL > 15: -$42 (28% WR — exhaustion blow-off entries); RVOL < 3: -$140
- Combined filter (RVOL 3–8 AND ATR% ≥ 4): main run **67 trades, 59.7% WR, +$45**; fixed run **47 trades, 59.6% WR, +$40** — flipping a -$167 strategy positive in both runs.

Caveats: this is in-sample bucket mining on ~67 trades; validate with `--end-days-ago` OOS splits before trusting it. But both knobs already exist (`atr_min_ratio` for meanrev, `d1_rvol_min` pipeline gate; an RVOL *ceiling* would need a small addition) and the consistency across two independent runs is encouraging.

## Finding 4 — Grade gate matters; this run traded down to B

A+ entries: 54.3% WR, +$16 (n=35). A: -$128 (n=264). B: -$56 (n=285). The backtest default is `--min-grade B`; live invariants say A+. 94% of these backtest trades would not have been taken live, so this run materially overstates trade frequency and understates quality vs the live config. Experiments should pass `--min-grade A+` to mirror production (or sweep the grade gate deliberately).

## Finding 5 — 2022 is the loss engine; no BTC regime filter on this strategy

By entry year (main): 2022 -$90, 2023 -$45, 2024 +$12, 2025 -$33. The fixed variant loses $113 in 2022 alone. The strategies with a BTC bull-market filter (volatility_breakout, htf_trend) are the only ones in PROJECT_CONTEXT with positive 5-year P&L. Adding `require_btc_bull_market` (BTC > 200d EMA) to meanrev-style entries is a directly testable hypothesis with existing machinery.

## Finding 6 — Concentration risk: Dogecoin

XDG/USD accounts for 128/584 trades (22%) and -$49 of P&L. The next worst symbols (DOT, VET, CRV, BAT, HBAR) are also low-ATR majors/memes — consistent with Finding 3 (low ATR% = losses). A per-symbol trade cap or simply the ATR% floor handles this.

## Recommended experiment queue (in order)

1. Re-baseline everything with today's engine fixes (`intrabar_exits` on, `--slippage-pct 0.2`, `--min-grade A+`).
2. htf_trend with `--swing-stop-recent` vs without — expect a structurally different (real) R framework.
3. meanrev/main strategy with ATR% floor (`atr_min_ratio` ≥ 1.0) — proxy for the ATR ≥ 4% bucket.
4. Same + BTC bull filter; compare 2022 segment specifically.
5. Validate winners OOS: train `--days 1460 --end-days-ago 365`, validate `--days 365`.
6. Consider an RVOL ceiling (~10–15) — needs a small new config knob; the data says extreme-RVOL entries are exhaustion tops.
