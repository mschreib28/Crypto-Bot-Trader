# Algorithm & Strategy Improvement Analysis

Date: 2026-06-09. Scope: full repo review — strategy code (`research/strategies/`), backtest engine (`backtest.py`), experiment framework (`experiments/`), and existing diagnosis docs. Backtest result JSONs live only on corpus (`experiments/results/` is rsync-excluded); see "Pulling the data" at the end.

**Bottom line:** The biggest wins are not new strategy ideas — they're fixing backtest fidelity so the experiment loop optimizes against reality. Three of the findings below mean current backtest numbers (49.3% WR / 3.13 R:R on vwap_meanrev) are likely optimistic, and several "filters" the strategies claim to have are not actually active in the trading path.

---

## Tier 1 — Backtest fidelity (fix before tuning anything)

### 1.1 Exits trigger on bar CLOSE, not intrabar high/low — WR is inflated

`backtest.py` `check_exits()` (~L1687): `price = current_bar.close`, then stop fires only if `close <= stop`. A 4h bar whose **low** pierces the stop but closes back above it never stops out in the backtest. The live monitor evaluates live prices, so it **would** stop out. Same asymmetry for TP1/TP2 (close must exceed target, but fill is booked at the exact target price).

On 4h candles, intrabar range is routinely several percent — this is the single largest source of backtest-vs-live divergence. Every config the experiment loop has ranked was ranked under this bias.

**Fix:** trigger stops on `bar.low <= stop` (longs) and TPs on `bar.high >= tp`; when both hit in the same bar, assume stop first (conservative). Fill stops at the stop price. Re-run the baseline — expect WR to drop. That lower number is the real benchmark.

### 1.2 No slippage or spread modeled

Fees are modeled (0.16% maker / 0.26% taker), fills are at exact prices. The pairs this bot targets ($500K–$50M daily volume) have meaningful spreads. **Fix:** add a `--slippage-pct` flag (default 0.1–0.2%) applied against you on every fill. Cheap to implement, immediately makes marginal strategies (HTF Trend at +$8/5yr) show their true colors.

### 1.3 Everything is in-sample — the experiment loop is an overfitting machine

Every experiment runs on the same 5-year window, 10–20 configs/day, ranked by P&L. With trade counts of 25–154 per strategy over 5 years, parameter "winners" are mostly noise. **Fix:** add a train/validation split to `run_experiments.py` (e.g., train 2019–2024, validate 2025–2026, or rolling walk-forward). Only promote a config if it improves on **both** windows. Also report a bootstrap confidence interval on expectancy per experiment, not just raw P&L — with n=25 trades, P&L differences of ±$10 are meaningless.

### 1.4 Known-broken parameter passthroughs (already diagnosed, not yet fixed)

Per `DIAGNOSIS_PARAMETER_PASSTHROUGH.md` and `experiments/HUMAN_NOTES.md`:

- `htf_rsi_long_max` is tautological at 4h/4h (HTF RSI == entry RSI; the oversold check already implies it passes). Fix: compute HTF RSI on `htf_interval` (1h) as live does, or sweep at 15m/1h entry.
- `breakeven_requires_tp1: false` is untestable (runner omits false booleans; the false branch doesn't even exist in `check_exits()`). Fix: emit `--no-breakeven-requires-tp1`, and implement the early-breakeven path to mirror `monitor.py`'s +1% trigger.
- Experiments exp_004–exp_008 are invalid for these knobs; re-run after fixes.

### 1.5 Live exits diverge from backtest exits

`monitor.py` has a trailing stop and a 48h opportunity filter; `backtest.py` simulates neither. Breakeven fee buffers differ. Even a perfect backtest currently predicts a different bot than the one running. Either simulate both behaviors in `check_exits()` or disable them live until they're backtestable — otherwise the "backtest before deploy" rule (invariant #10) isn't validating what ships.

---

## Tier 2 — Strategy logic bugs (real code, verified line numbers)

### 2.1 Swing stop uses the LOWEST swing low in the entire window, not the most recent

`backtest.py:847`, `vwap_meanrev/strategy.py:458`, `htf_trend/strategy.py:379`:

```python
swing_stop = min(swing_lows) if swing_lows else entry * 0.95
stop       = min(swing_stop, atr_stop) - buffer
```

`min(swing_lows)` over a ~200-bar window puts the stop below the lowest low of the whole window — sometimes the multi-week low — instead of the structure being traded. Because TP1/TP2 are R-multiples of that inflated risk, targets also drift far away, and 2%-risk sizing shrinks positions. Almost certainly intended: `swing_lows[-1]` (most recent swing low). **This is a high-value experiment:** test `recent swing low` vs current behavior on vwap_meanrev and htf_trend. It changes stop distance, R framework, and P&L simultaneously.

### 2.2 vwap_meanrev's HTF regime filter is a no-op

`vwap_meanrev/strategy.py:256–330` (`_check_regime_filter`): every code path returns `(True, ...)` except the HTF volatility cap. Lines 307–320 compute `is_bullish`/`is_bearish` and then unconditionally `return (True, ...)`. The docstring promises a "1h trend/range regime filter"; nothing is filtered. Either wire the EMA200/slope result into an actual block (e.g., block longs when price < EMA200 AND slope strongly negative) or delete the dead code. Given the BTC bull-market filter was the highest-impact knob on volatility_breakout, an actually-functioning regime gate here is a promising experiment.

### 2.3 meanrev: the ADX gate — its core premise — is missing from the live trading path

`meanrev/strategy.py`: `evaluate()` (screener confidence scoring) uses ADX at L497+, but `generate_signals()` (L183, the path that fires trades) never checks ADX. The file header calls ADX "CRITICAL for mean reversion." The runner will happily mean-revert into a strong trend. Additionally `generate_signals` computes RSI from a single price pair (`self._calculate_rsi(bar.close, previous_price)`, L~215) rather than a proper 14-period series. Both should match `evaluate()`.

### 2.4 Screener and runner disagree on entry criteria (vwap_meanrev)

`generate_signals()` uses percentage deviation when `use_percentage_deviation` is set (the Ross Cameron spec); `evaluate()` scores using ATR-based deviation (`deviation_atr <= -dev_threshold_ATR`, L964). So the confidence score the supervisor/screener sees is computed from different entry logic than what triggers trades. Align `evaluate()` to the same deviation mode.

### 2.5 "Session VWAP" is actually a rolling-window VWAP

`calculate_vwap()` with no anchor cumulates from the start of whatever slice it's given — in the live strategy that's a `deque(maxlen=200)`, so the "session VWAP" is a 200-bar rolling VWAP whose value silently depends on buffer length; in backtest it depends on the trimmed window. Crypto has no session, so anchored VWAP (swing-low anchor — already implemented) is the right concept; consider dropping the fallback or renaming it so experiments aren't interpreting it as session VWAP. Also note `anchored_vwap if anchored_vwap else session_vwap` is a falsy check, and anchor selection prefers swing **lows** even for short setups.

### 2.6 RSI is Cutler's (SMA-based), not Wilder's

`indicators.py:93` averages the last 14 changes rather than Wilder-smoothing. Not a bug — but values diverge from what Kraken/TradingView charts show, so threshold intuition ("RSI 30") doesn't transfer, and Cutler RSI is choppier. Fine to keep since thresholds were tuned on it; just keep it consistent everywhere (backtest.py imports the same function — good) and document it in STRATEGY_KNOWLEDGE.md.

---

## Tier 3 — Portfolio / process

- **Bull Flag is the worst performer (-$34/5yr, 0.80 R:R) yet four variants are active in the supervisor** (`bull_flag_1m/5m/1h/swing_bull_flag`). The supervisor will suspend them eventually, but they're burning the live shadow sample budget (2–4 trades/week total) that's gated on reaching 50 clean vwap_meanrev trades. Consider suspending bull flag variants until the 4h proxy backtests positive.
- **Volatility Breakout's +$24 rests on 25 trades** — far too few to trust the 60% WR. Treat as unproven rather than "STRONG"; prioritize getting its trade count up in backtests (more pairs / longer window) before sizing it like an anchor.
- **Rank experiments by expectancy per trade with confidence intervals**, and prefer configs that win across both 4h and 1h variants — single-window P&L ranking plus 10+ experiments/day is how you converge on noise.
- **Concentrate iteration on vwap_meanrev 4h** (the anchor) until Tier 1 fixes land; results for everything else will be re-shuffled by the intrabar-exit fix anyway.

---

## Suggested order of operations

1. Fix intrabar exit triggers + add slippage flag in `backtest.py`; re-run baseline → new reference numbers.
2. Fix the two broken passthroughs (HTF RSI interval, breakeven false branch); re-run exp_004–exp_008.
3. Add OOS split to `run_experiments.py`.
4. Experiment: `swing_lows[-1]` vs `min(swing_lows)` stop on vwap_meanrev + htf_trend.
5. Fix meanrev `generate_signals` (ADX gate + proper RSI) and vwap_meanrev regime filter; backtest before/after.
6. Restart the experiment loop only after 1–3 land — current leaderboard rankings are not trustworthy.

---

## Pulling the backtest data from corpus

Results were never synced locally (`experiments/results` is in the rsync exclude list and the loop shows NOT_STARTED locally). To pull everything down for analysis:

```bash
rsync -av ark@corpus:~/crypto-bot-trading/experiments/results/ \
  "/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/experiments/results/"
rsync -av ark@corpus:~/crypto-bot-trading/experiments/leaderboard.md \
  ark@corpus:~/crypto-bot-trading/experiments/trade_journal.md \
  ark@corpus:~/crypto-bot-trading/backtest_trades.csv \
  "/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/"
```

(Adjust the destination if running from this machine instead of kevin's.) Once `backtest_trades.csv` and the per-experiment JSONs are in this folder, the per-trade columns (entry_rvol, vwap_distance, utc_hour, exit_reason, etc.) support a proper win/loss attribution pass — `experiments/analyze_trades.py` already does 14-dimension bucketing, and I can dig further from there.
