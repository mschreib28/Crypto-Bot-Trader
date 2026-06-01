# Loop Status

## Current State

STATUS: NOT_STARTED
LAST_UPDATED: (auto-populated on first run)
GENERATION: 0

## Current Best Config

(Will be populated after baseline runs)

- Strategy: -
- Win Rate: -
- R:R: -
- P&L: -
- Max DD: -
- Experiment ID: -

## Last 5 Generations

(Will be populated as the loop runs)

| Gen | Timestamp | Experiments | Winners | Best P&L | Δ vs Prev Best |
|---|---|---|---|---|---|

## Convergence Tracking

Consecutive generations without ≥5% improvement: 0
Convergence threshold: 3 generations

## Halt Conditions

If any of these become true, write LOOP_HALTED: <reason> and stop:

- Nondeterminism detected (baseline re-run differs from original)
- Runner crashes 3 times in a row
- A real bug found in strategy code (write to HUMAN_NOTES.md)
- Human writes "PAUSE" to this file

## Notes

This file is auto-updated by the agent each iteration. Humans can read it but
should generally not edit it directly. To pause the loop, replace this file's
top line with `STATUS: PAUSED_BY_HUMAN`. To send instructions to the agent,
write to HUMAN_NOTES.md instead.
