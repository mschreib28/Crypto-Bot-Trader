# Human Notes — Override Channel

This is YOUR communication channel to the autonomous agent. The agent reads this
file every iteration and weighs it heavily. Use it to:

- Set direction (focus on X, ignore Y)
- Note hypotheses you want tested
- Reject paths the agent is going down
- Document things the agent found that need human action

## Current Direction

(Edit this section to steer the agent's next iteration. The agent treats
this as priority #1, overriding its normal proposal strategy.)

DIRECTION: (none — agent uses default proposal strategy)

Example direction lines you might write:
- "Focus on breakeven_requires_tp1 variations — that's the biggest issue right now"
- "Stop exploring grade gates. We've tested enough. Move on to stop multipliers."
- "Try wildcards more aggressively. We're stuck in a local optimum."

---

## Hypotheses to Test

(Add specific ideas you want the agent to explore. Mark them DONE when tested.)

- [ ] Does breakeven_requires_tp1=true improve overall P&L on vwap_meanrev?
- [ ] Is there a sweet spot for rsi_oversold between 25 and 35?
- [ ] Does combining HTF RSI filter with volume filter beat either alone?

---

## Dead Ends (don't propose these again)

(Mark experiments or parameter ranges that have been tried and don't work.
The agent reads this to avoid repeating failed paths.)

- vwap_meanrev_1h with any tested filter — 22.9% WR ceiling, structural problem
- atr_stop_mult < 0.8 — too tight, every trade stops out
- rsi_oversold > 40 — too loose, signal frequency too low to validate

---

## Parameter Requests

(If the agent wants to test a parameter not in STRATEGY_KNOWLEDGE.md, it
writes here. You either approve or reject.)

(No pending requests)

---

## CLI Flags Needed

(If the agent proposes a config that needs a new backtest.py CLI flag,
it writes here so you can add the flag in a separate Cursor session.)

- `dev_threshold_pct` → runner should map to existing `--dev-threshold` (or add `--dev-threshold-pct` alias)

---

## Code Changes Needed

(If a hypothesis genuinely requires modifying strategy.py or backend/,
the agent writes here. You decide whether to take it on manually.)

(No pending requests)

---

## Bugs Found

(If the agent detects a real bug in the runner or backtester,
it writes here AND halts the loop.)

(No bugs reported)

---

## Skipped Proposals

(Agent writes here when it skips a proposal due to validation failure.
Helps you understand what guard rails are firing.)

- **2026-06-01 Gen 1**: `breakeven_requires_tp1: false` — runner omits false booleans (only appends flag when true). Needs `--no-breakeven-requires-tp1` passthrough in run_experiments.py to test Ross Cameron false branch.
- **2026-06-01 Gen 1**: `dev_threshold_pct` — STRATEGY_KNOWLEDGE name maps to `--dev-threshold-pct` via runner, but backtest.py only exposes `--dev-threshold`. Add alias mapping in run_experiments.py or CLI flag.
- **2026-06-01 Gen 1**: `tp1_R`, `tp2_R`, `reversal_body_pct`, `max_bars_in_trade` — no matching CLI flags in backtest.py (config-only in strategy dataclass).

---

## Convergence History

(When the loop converges, the agent writes the final state here for review.
You can then decide to accept the config and deploy, or set new direction
to break out of the local optimum.)

(Not yet converged)
## Parameter Passthrough Status (verified 2026-06-01)

### Working (use freely)
- `long_min_volume_ratio` — entry filter (in strategy.py)
- `atr_stop_mult` — stop calculation (in strategy.py)

### FIXED 2026-06-09 (was: BROKEN — silently ignored)
- `htf_rsi_long_max` — root cause was a tautological gate: HTF RSI was computed
  on the same 4h series as entry RSI (rsi<=30 always implies htf_rsi<=35/40).
  `htf_rsi_bars_interval` now defaults to **1h** (live parity) and backtest.py
  prints a WARNING when it equals the entry interval.
- `breakeven_requires_tp1` — `false` branch now implemented in
  `backtest.check_exits()`: breakeven arms early at `breakeven_trigger_r`
  (default 0.5R, mirrors monitor.py). Test with
  `breakeven_requires_tp1: false` (runner already emits `--no-` for false bools).

### MAJOR ENGINE CHANGE 2026-06-09 — re-baseline required
`check_exits()` now triggers stops/TPs on bar high/low (intrabar), not close.
ALL prior results are optimistic and not comparable. Re-run baseline before any
new experiments. Legacy behavior available via `intrabar_exits: false`.
New knobs: `slippage_pct`, `end_days_ago` (OOS splits), `swing_stop_recent`,
`breakeven_trigger_r`, plus CLI flags for `tp1_R`, `tp2_R`, `max_bars_in_trade`,
`reversal_body_pct`, `dev_threshold_pct`. See STRATEGY_KNOWLEDGE.md.

### Affected experiments to re-run after fix
ALL (engine change). Previously flagged: exp_004, exp_005, exp_006, exp_008
