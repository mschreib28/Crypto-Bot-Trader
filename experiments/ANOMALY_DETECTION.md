# Anomaly Detection — When to Suspect a Bug

This file defines patterns in experiment results that indicate a BUG rather
than a true negative result. The agent applies these rules during ITERATION_TASK
step "Step 2.5: Anomaly Detection" before proposing new experiments.

## Core Principle

A "true negative" result means the parameter was honored but didn't help.
A "bug result" means the parameter wasn't honored at all.

The most reliable bug signal: **two different parameter values produce
mathematically identical results**.

## Anomaly Rules

### Rule 1: Identical-to-Baseline Detection

**Trigger:** An experiment's results match baseline EXACTLY on multiple metrics
(trades, win_rate, rr_ratio, total_pnl, max_dd).

**Threshold:** All five metrics match within 0.001 absolute difference.

**Diagnosis:** The config_override was likely silently ignored. The CLI flag
may be accepted by argparse but not propagated to the strategy config object.

**Example detected on 2026-06-01:**
- baseline: trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- exp_006 (breakeven_requires_tp1=true): trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- Identical on all metrics → parameter not flowing through

**Action:** Write proposal to PROPOSALS/ with diagnosis. Mark parameter as
"unverified" in HUMAN_NOTES.md. Do NOT propose more experiments using that
parameter until human confirms it's fixed.

### Rule 2: Identical-Group Detection

**Trigger:** Two experiments that change DIFFERENT parameters produce identical
results to each other.

**Example:**
- exp_007 (volume_1.5 + htf_rsi_40): trades=107, WR=47.7%, P&L=$6.84
- exp_008 (volume_1.5 + breakeven_tp1): trades=107, WR=47.7%, P&L=$6.84
- Identical → the second parameter (breakeven_tp1) had no effect; only the
  shared parameter (volume_1.5) is doing anything

**Action:** Same as Rule 1 for the parameter that appears to be ignored.

### Rule 3: Counter-Intuitive Direction

**Trigger:** A filter that should REDUCE trades INCREASES them, OR vice versa.

**Example detected on 2026-06-01:**
- baseline (no volume filter): 86 trades
- exp_001 (volume >= 1.2x SMA): 109 trades
- exp_002 (volume >= 1.5x SMA): 107 trades
- exp_003 (volume >= 2.0x SMA): 103 trades

A stricter volume filter should produce FEWER trades, but increasing the
threshold from "any volume" to "1.2x SMA" added 23 trades. Filter logic may
be inverted, or enabling the filter removes a different protective check.

**Action:** Write proposal to PROPOSALS/ explaining the inverted behavior.
Do not propose more experiments in this direction until investigated.

### Rule 4: All-or-Nothing Filter Effect

**Trigger:** Multiple values of the same parameter produce nearly identical
results to each other (within 5% of each metric), but all DIFFER from baseline.

**Diagnosis:** The filter is binary (on/off) regardless of value. The threshold
parameter exists in CLI but the strategy code treats any non-default value
identically.

**Example:**
- volume_1.2: P&L=$6.93, trades=109
- volume_1.5: P&L=$6.84, trades=107
- volume_2.0: P&L=$6.66, trades=103

These are suspiciously close. The parameter's *value* may not be doing what
its name suggests.

**Action:** Write proposal asking human to verify what the parameter actually
controls in the strategy code.

### Rule 5: Zero-Trades Result

**Trigger:** An experiment returns 0 trades when baseline returns ≥30.

**Diagnosis:** Likely one of:
- Parameter value is outside the strategy's signal range (correct rejection)
- Parameter value caused a runtime error that swallowed all signals
- The strategy crashed and returned no results

**Action:** Distinguish between "filter too strict" vs "crash":
- Read the exp's stdout_tail. Look for tracebacks or error messages.
- If errors found: write a bug proposal.
- If no errors and value is within STRATEGY_KNOWLEDGE.md "Safe Range": still
  a bug proposal (unexpected behavior).
- If value is outside safe range: not a bug, just an over-strict filter.

### Rule 6: P&L Without Trades

**Trigger:** total_pnl != 0 but trades == 0.

**Diagnosis:** Definite bug. Should be impossible.

**Action:** HALT the loop. Write LOOP_HALTED to STATUS.md.

### Rule 7: Win Rate / Trade Count Mismatch

**Trigger:** Win rate × trade count doesn't round to an integer.

**Diagnosis:** Possible metric corruption. WR is computed from win count / total,
so for any integer wins and total, WR × total should round to an integer wins.

**Action:** Write a proposal noting the metric inconsistency.

## Proposal Format

When the agent detects an anomaly, write to `experiments/PROPOSALS/PROP_NNN_short_description.md`
with this structure:

```markdown
# Proposal NNN: <Short Title>

**Status:** PENDING_REVIEW
**Detected:** <ISO timestamp>
**Rule Triggered:** <Rule N name>
**Severity:** <BLOCKER | HIGH | MEDIUM | LOW>

## Evidence
# Anomaly Detection — When to Suspect a Bug

This file defines patterns in experiment results that indicate a BUG rather
than a true negative result. The agent applies these rules during ITERATION_TASK
step "Step 2.5: Anomaly Detection" before proposing new experiments.

## Core Principle

A "true negative" result means the parameter was honored but didn't help.
A "bug result" means the parameter wasn't honored at all.

The most reliable bug signal: **two different parameter values produce
mathematically identical results**.

## Anomaly Rules

### Rule 1: Identical-to-Baseline Detection

**Trigger:** An experiment's results match baseline EXACTLY on multiple metrics
(trades, win_rate, rr_ratio, total_pnl, max_dd).

**Threshold:** All five metrics match within 0.001 absolute difference.

**Diagnosis:** The config_override was likely silently ignored. The CLI flag
may be accepted by argparse but not propagated to the strategy config object.

**Example detected on 2026-06-01:**
- baseline: trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- exp_006 (breakeven_requires_tp1=true): trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- Identical on all metrics → parameter not flowing through

**Action:** Write proposal to PROPOSALS/ with diagnosis. Mark parameter as
"unverified" in HUMAN_NOTES.md. Do NOT propose more experiments using that
parameter until human confirms it's fixed.

### Rule 2: Identical-Group Detection

**Trigger:** Two experiments that change DIFFERENT parameters produce identical
results to each other.

**Example:**
- exp_007 (volume_1.5 + htf_rsi_40): trades=107, WR=47.7%, P&L=$6.84
- exp_008 (volume_1.5 + breakeven_tp1): trades=107, WR=47.7%, P&L=$6.84
- Identical → the second parameter (breakeven_tp1) had no effect; only the
  shared parameter (volume_1.5) is doing anything

**Action:** Same as Rule 1 for the parameter that appears to be ignored.

### Rule 3: Counter-Intuitive Direction

**Trigger:** A filter that should REDUCE trades INCREASES them, OR vice versa.

**Example detected on 2026-06-01:**
- baseline (no volume filter): 86 trades
- exp_001 (volume >= 1.2x SMA): 109 trades
- exp_002 (volume >= 1.5x SMA): 107 trades
- exp_003 (volume >= 2.0x SMA): 103 trades

A stricter volume filter should produce FEWER trades, but increasing the
threshold from "any volume" to "1.2x SMA" added 23 trades. Filter logic may
be inverted, or enabling the filter removes a different protective check.

**Action:** Write proposal to PROPOSALS/ explaining the inverted behavior.
Do not propose more experiments in this direction until investigated.

### Rule 4: All-or-Nothing Filter Effect

**Trigger:** Multiple values of the same parameter produce nearly identical
results to each other (within 5% of each metric), but all DIFFER from baseline.

**Diagnosis:** The filter is binary (on/off) regardless of value. The threshold
parameter exists in CLI but the strategy code treats any non-default value
identically.

**Example:**
- volume_1.2: P&L=$6.93, trades=109
- volume_1.5: P&L=$6.84, trades=107
- volume_2.0: P&L=$6.66, trades=103

These are suspiciously close. The parameter's *value* may not be doing what
its name suggests.

**Action:** Write proposal asking human to verify what the parameter actually
controls in the strategy code.

### Rule 5: Zero-Trades Result

**Trigger:** An experiment returns 0 trades when baseline returns ≥30.

**Diagnosis:** Likely one of:
- Parameter value is outside the strategy's signal range (correct rejection)
- Parameter value caused a runtime error that swallowed all signals
- The strategy crashed and returned no results

**Action:** Distinguish between "filter too strict" vs "crash":
- Read the exp's stdout_tail. Look for tracebacks or error messages.
- If errors found: write a bug proposal.
- If no errors and value is within STRATEGY_KNOWLEDGE.md "Safe Range": still
  a bug proposal (unexpected behavior).
- If value is outside safe range: not a bug, just an over-strict filter.

### Rule 6: P&L Without Trades

**Trigger:** total_pnl != 0 but trades == 0.

**Diagnosis:** Definite bug. Should be impossible.

**Action:** HALT the loop. Write LOOP_HALTED to STATUS.md.

### Rule 7: Win Rate / Trade Count Mismatch

**Trigger:** Win rate × trade count doesn't round to an integer.

**Diagnosis:** Possible metric corruption. WR is computed from win count / total,
so for any integer wins and total, WR × total should round to an integer wins.

**Action:** Write a proposal noting the metric inconsistency.

## Proposal Format

When the agent detects an anomaly, write to `experiments/PROPOSALS/PROP_NNN_short_description.md`
with this structure:

```markdown# Anomaly Detection — When to Suspect a Bug

This file defines patterns in experiment results that indicate a BUG rather
than a true negative result. The agent applies these rules during ITERATION_TASK
step "Step 2.5: Anomaly Detection" before proposing new experiments.

## Core Principle

A "true negative" result means the parameter was honored but didn't help.
A "bug result" means the parameter wasn't honored at all.

The most reliable bug signal: **two different parameter values produce
mathematically identical results**.

## Anomaly Rules

### Rule 1: Identical-to-Baseline Detection

**Trigger:** An experiment's results match baseline EXACTLY on multiple metrics
(trades, win_rate, rr_ratio, total_pnl, max_dd).

**Threshold:** All five metrics match within 0.001 absolute difference.

**Diagnosis:** The config_override was likely silently ignored. The CLI flag
may be accepted by argparse but not propagated to the strategy config object.

**Example detected on 2026-06-01:**
- baseline: trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- exp_006 (breakeven_requires_tp1=true): trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- Identical on all metrics → parameter not flowing through

**Action:** Write proposal to PROPOSALS/ with diagnosis. Mark parameter as
"unverified" in HUMAN_NOTES.md. Do NOT propose more experiments using that
parameter until human confirms it's fixed.

### Rule 2: Identical-Group Detection

**Trigger:** Two experiments that change DIFFERENT parameters produce identical
results to each other.

**Example:**
- exp_007 (volume_1.5 + htf_rsi_40): trades=107, WR=47.7%, P&L=$6.84
- exp_008 (volume_1.5 + breakeven_tp1): trades=107, WR=47.7%, P&L=$6.84
- Identical → the second parameter (breakeven_tp1) had no effect; only the
  shared parameter (volume_1.5) is doing anything

**Action:** Same as Rule 1 for the parameter that appears to be ignored.

### Rule 3: Counter-Intuitive Direction

**Trigger:** A filter that should REDUCE trades INCREASES them, OR vice versa.

**Example detected on 2026-06-01:**
- baseline (no volume filter): 86 trades
- exp_001 (volume >= 1.2x SMA): 109 trades
- exp_002 (volume >= 1.5x SMA): 107 trades
- exp_003 (volume >= 2.0x SMA): 103 trades

A stricter volume filter should produce FEWER trades, but increasing the
threshold from "any volume" to "1.2x SMA" added 23 trades. Filter logic may
be inverted, or enabling the filter removes a different protective check.

**Action:** Write proposal to PROPOSALS/ explaining the inverted behavior.
Do not propose more experiments in this direction until investigated.

### Rule 4: All-or-Nothing Filter Effect

**Trigger:** Multiple values of the same parameter produce nearly identical
results to each other (within 5% of each metric), but all DIFFER from baseline.

**Diagnosis:** The filter is binary (on/off) regardless of value. The threshold
parameter exists in CLI but the strategy code treats any non-default value
identically.

**Example:**
- volume_1.2: P&L=$6.93, trades=109
- volume_1.5: P&L=$6.84, trades=107
- volume_2.0: P&L=$6.66, trades=103

These are suspiciously close. The parameter's *value* may not be doing what
its name suggests.

**Action:** Write proposal asking human to verify what the parameter actually
controls in the strategy code.

### Rule 5: Zero-Trades Result

**Trigger:** An experiment returns 0 trades when baseline returns ≥30.

**Diagnosis:** Likely one of:
- Parameter value is outside the strategy's signal range (correct rejection)
- Parameter value caused a runtime error that swallowed all signals
- The strategy crashed and returned no results

**Action:** Distinguish between "filter too strict" vs "crash":
- Read the exp's stdout_tail. Look for tracebacks or error messages.
- If errors found: write a bug proposal.
- If no errors and value is within STRATEGY_KNOWLEDGE.md "Safe Range": still
  a bug proposal (unexpected behavior).
- If value is outside safe range: not a bug, just an over-strict filter.

### Rule 6: P&L Without Trades

**Trigger:** total_pnl != 0 but trades == 0.

**Diagnosis:** Definite bug. Should be impossible.

**Action:** HALT the loop. Write LOOP_HALTED to STATUS.md.

### Rule 7: Win Rate / Trade Count Mismatch

**Trigger:** Win rate × trade count doesn't round to an integer.

**Diagnosis:** Possible metric corruption. WR is computed from win count / total,
so for any integer wins and total, WR × total should round to an integer wins.

**Action:** Write a proposal noting the metric inconsistency.

## Proposal Format

When the agent detects an anomaly, write to `experiments/PROPOSALS/PROP_NNN_short_description.md`
with this structure:

```markdown
# Proposal NNN: <Short Title>

**Status:** PENDING_REVIEW
**Detected:** <ISO timestamp>
**Rule Triggered:** <Rule N name>
**Severity:** <BLOCKER | HIGH | MEDIUM | LOW>

## Evidence

<List of experiment IDs and their identical/anomalous metrics>

## Diagnosis

<2-3 sentence hypothesis of what's wrong. Be specific about which file 
or code path likely contains the bug. Reference STRATEGY_KNOWLEDGE.md
when relevant.>

## Suggested Investigation
# Anomaly Detection — When to Suspect a Bug

This file defines patterns in experiment results that indicate a BUG rather
than a true negative result. The agent applies these rules during ITERATION_TASK
step "Step 2.5: Anomaly Detection" before proposing new experiments.

## Core Principle

A "true negative" result means the parameter was honored but didn't help.
A "bug result" means the parameter wasn't honored at all.

The most reliable bug signal: **two different parameter values produce
mathematically identical results**.

## Anomaly Rules

### Rule 1: Identical-to-Baseline Detection

**Trigger:** An experiment's results match baseline EXACTLY on multiple metrics
(trades, win_rate, rr_ratio, total_pnl, max_dd).

**Threshold:** All five metrics match within 0.001 absolute difference.

**Diagnosis:** The config_override was likely silently ignored. The CLI flag
may be accepted by argparse but not propagated to the strategy config object.

**Example detected on 2026-06-01:**
- baseline: trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- exp_006 (breakeven_requires_tp1=true): trades=86, WR=47.7%, R:R=3.27, P&L=$17.12, DD=-$1.65
- Identical on all metrics → parameter not flowing through

**Action:** Write proposal to PROPOSALS/ with diagnosis. Mark parameter as
"unverified" in HUMAN_NOTES.md. Do NOT propose more experiments using that
parameter until human confirms it's fixed.

### Rule 2: Identical-Group Detection

**Trigger:** Two experiments that change DIFFERENT parameters produce identical
results to each other.

**Example:**
- exp_007 (volume_1.5 + htf_rsi_40): trades=107, WR=47.7%, P&L=$6.84
- exp_008 (volume_1.5 + breakeven_tp1): trades=107, WR=47.7%, P&L=$6.84
- Identical → the second parameter (breakeven_tp1) had no effect; only the
  shared parameter (volume_1.5) is doing anything

**Action:** Same as Rule 1 for the parameter that appears to be ignored.

### Rule 3: Counter-Intuitive Direction

**Trigger:** A filter that should REDUCE trades INCREASES them, OR vice versa.

**Example detected on 2026-06-01:**
- baseline (no volume filter): 86 trades
- exp_001 (volume >= 1.2x SMA): 109 trades
- exp_002 (volume >= 1.5x SMA): 107 trades
- exp_003 (volume >= 2.0x SMA): 103 trades

A stricter volume filter should produce FEWER trades, but increasing the
threshold from "any volume" to "1.2x SMA" added 23 trades. Filter logic may
be inverted, or enabling the filter removes a different protective check.

**Action:** Write proposal to PROPOSALS/ explaining the inverted behavior.
Do not propose more experiments in this direction until investigated.

### Rule 4: All-or-Nothing Filter Effect

**Trigger:** Multiple values of the same parameter produce nearly identical
results to each other (within 5% of each metric), but all DIFFER from baseline.

**Diagnosis:** The filter is binary (on/off) regardless of value. The threshold
parameter exists in CLI but the strategy code treats any non-default value
identically.

**Example:**
- volume_1.2: P&L=$6.93, trades=109
- volume_1.5: P&L=$6.84, trades=107
- volume_2.0: P&L=$6.66, trades=103

These are suspiciously close. The parameter's *value* may not be doing what
its name suggests.

**Action:** Write proposal asking human to verify what the parameter actually
controls in the strategy code.

### Rule 5: Zero-Trades Result

**Trigger:** An experiment returns 0 trades when baseline returns ≥30.

**Diagnosis:** Likely one of:
- Parameter value is outside the strategy's signal range (correct rejection)
- Parameter value caused a runtime error that swallowed all signals
- The strategy crashed and returned no results

**Action:** Distinguish between "filter too strict" vs "crash":
- Read the exp's stdout_tail. Look for tracebacks or error messages.
- If errors found: write a bug proposal.
- If no errors and value is within STRATEGY_KNOWLEDGE.md "Safe Range": still
  a bug proposal (unexpected behavior).
- If value is outside safe range: not a bug, just an over-strict filter.

### Rule 6: P&L Without Trades

**Trigger:** total_pnl != 0 but trades == 0.

**Diagnosis:** Definite bug. Should be impossible.

**Action:** HALT the loop. Write LOOP_HALTED to STATUS.md.

### Rule 7: Win Rate / Trade Count Mismatch

**Trigger:** Win rate × trade count doesn't round to an integer.

**Diagnosis:** Possible metric corruption. WR is computed from win count / total,
so for any integer wins and total, WR × total should round to an integer wins.

**Action:** Write a proposal noting the metric inconsistency.

## Proposal Format

When the agent detects an anomaly, write to `experiments/PROPOSALS/PROP_NNN_short_description.md`
with this structure:

```markdown
# Proposal NNN: <Short Title>

**Status:** PENDING_REVIEW
**Detected:** <ISO timestamp>
**Rule Triggered:** <Rule N name>
**Severity:** <BLOCKER | HIGH | MEDIUM | LOW>

## Evidence

<List of experiment IDs and their identical/anomalous metrics>

## Diagnosis

<2-3 sentence hypothesis of what's wrong. Be specific about which file 
or code path likely contains the bug. Reference STRATEGY_KNOWLEDGE.md
when relevant.>

## Suggested Investigation

<Concrete checks the human or dev agent can do:
- "grep for X in file Y"  
- "verify CLI args reach config object via Z"
- "check if parameter is read in strategy.py or only in monitor.py">

## Suggested Fix Outline

<High-level sketch of what the fix probably looks like. Do not write code.
Just describe: "If the issue is X, the fix is probably to add Y in file Z.">

## Affected Experiments

<List of experiment IDs whose results may be unreliable because of this bug.
After fix, these should be re-run.>
```

## Severity Definitions

- **BLOCKER**: Loop must halt. Continuing wastes time on broken results.
- **HIGH**: Specific parameter unreliable. Stop proposing experiments using it. Other params still safe.
- **MEDIUM**: Result confidence reduced but parameter may still be partially working.
- **LOW**: Curiosity flag. Mention but continue normally.

## When NOT to File a Proposal

- Result is just worse than baseline (that's a true negative, not a bug)
- Trade count drops with stricter filter (that's expected behavior)
- A single experiment failed (could be transient — re-run before proposing)
- The agent doesn't understand WHY a result is bad (not enough signal to propose)

False positives are expensive. They waste human review time and erode trust
in the agent. Only file a proposal when the evidence is structural (Rules 1-7),
not when it's just "this result surprises me."

## Proposal Lifecycle

1. Agent writes `PROPOSALS/PROP_NNN_*.md` with status PENDING_REVIEW
2. Agent appends one line to HUMAN_NOTES.md `## Pending Proposals` section
3. Human reviews proposal
4. Human edits proposal status to APPROVED, REJECTED, or NEEDS_INFO
5. If APPROVED: human (or a separate dev session) implements the fix
6. After fix: human deletes the proposal file or moves to PROPOSALS/RESOLVED/
7. Affected experiments are re-run
## Suggested Fix Outline

<High-level sketch of what the fix probably looks like. Do not write code.
Just describe: "If the issue is X, the fix is probably to add Y in file Z.">

## Affected Experiments

<List of experiment IDs whose results may be unreliable because of this bug.
After fix, these should be re-run.>
```

## Severity Definitions

- **BLOCKER**: Loop must halt. Continuing wastes time on broken results.
- **HIGH**: Specific parameter unreliable. Stop proposing experiments using it. Other params still safe.
- **MEDIUM**: Result confidence reduced but parameter may still be partially working.
- **LOW**: Curiosity flag. Mention but continue normally.

## When NOT to File a Proposal

- Result is just worse than baseline (that's a true negative, not a bug)
- Trade count drops with stricter filter (that's expected behavior)
- A single experiment failed (could be transient — re-run before proposing)
- The agent doesn't understand WHY a result is bad (not enough signal to propose)

False positives are expensive. They waste human review time and erode trust
in the agent. Only file a proposal when the evidence is structural (Rules 1-7),
not when it's just "this result surprises me."

## Proposal Lifecycle

1. Agent writes `PROPOSALS/PROP_NNN_*.md` with status PENDING_REVIEW
2. Agent appends one line to HUMAN_NOTES.md `## Pending Proposals` section
3. Human reviews proposal
4. Human edits proposal status to APPROVED, REJECTED, or NEEDS_INFO
5. If APPROVED: human (or a separate dev session) implements the fix
6. After fix: human deletes the proposal file or moves to PROPOSALS/RESOLVED/
7. Affected experiments are re-run
# Proposal NNN: <Short Title>

**Status:** PENDING_REVIEW
**Detected:** <ISO timestamp>
**Rule Triggered:** <Rule N name>
**Severity:** <BLOCKER | HIGH | MEDIUM | LOW>

## Evidence

<List of experiment IDs and their identical/anomalous metrics>

## Diagnosis

<2-3 sentence hypothesis of what's wrong. Be specific about which file 
or code path likely contains the bug. Reference STRATEGY_KNOWLEDGE.md
when relevant.>

## Suggested Investigation

<Concrete checks the human or dev agent can do:
- "grep for X in file Y"  
- "verify CLI args reach config object via Z"
- "check if parameter is read in strategy.py or only in monitor.py">

## Suggested Fix Outline

<High-level sketch of what the fix probably looks like. Do not write code.
Just describe: "If the issue is X, the fix is probably to add Y in file Z.">

## Affected Experiments

<List of experiment IDs whose results may be unreliable because of this bug.
After fix, these should be re-run.>
```

## Severity Definitions

- **BLOCKER**: Loop must halt. Continuing wastes time on broken results.
- **HIGH**: Specific parameter unreliable. Stop proposing experiments using it. Other params still safe.
- **MEDIUM**: Result confidence reduced but parameter may still be partially working.
- **LOW**: Curiosity flag. Mention but continue normally.

## When NOT to File a Proposal

- Result is just worse than baseline (that's a true negative, not a bug)
- Trade count drops with stricter filter (that's expected behavior)
- A single experiment failed (could be transient — re-run before proposing)
- The agent doesn't understand WHY a result is bad (not enough signal to propose)

False positives are expensive. They waste human review time and erode trust
in the agent. Only file a proposal when the evidence is structural (Rules 1-7),
not when it's just "this result surprises me."

## Proposal Lifecycle

1. Agent writes `PROPOSALS/PROP_NNN_*.md` with status PENDING_REVIEW
2. Agent appends one line to HUMAN_NOTES.md `## Pending Proposals` section
3. Human reviews proposal
4. Human edits proposal status to APPROVED, REJECTED, or NEEDS_INFO
5. If APPROVED: human (or a separate dev session) implements the fix
6. After fix: human deletes the proposal file or moves to PROPOSALS/RESOLVED/
7. Affected experiments are re-run
## Suggested Investigation

<Concrete checks the human or dev agent can do:
- "grep for X in file Y"  
- "verify CLI args reach config object via Z"
- "check if parameter is read in strategy.py or only in monitor.py">

## Suggested Fix Outline

<High-level sketch of what the fix probably looks like. Do not write code.
Just describe: "If the issue is X, the fix is probably to add Y in file Z.">

## Affected Experiments

<List of experiment IDs whose results may be unreliable because of this bug.
After fix, these should be re-run.>
```

## Severity Definitions

- **BLOCKER**: Loop must halt. Continuing wastes time on broken results.
- **HIGH**: Specific parameter unreliable. Stop proposing experiments using it. Other params still safe.
- **MEDIUM**: Result confidence reduced but parameter may still be partially working.
- **LOW**: Curiosity flag. Mention but continue normally.

## When NOT to File a Proposal

- Result is just worse than baseline (that's a true negative, not a bug)
- Trade count drops with stricter filter (that's expected behavior)
- A single experiment failed (could be transient — re-run before proposing)
- The agent doesn't understand WHY a result is bad (not enough signal to propose)

False positives are expensive. They waste human review time and erode trust
in the agent. Only file a proposal when the evidence is structural (Rules 1-7),
not when it's just "this result surprises me."

## Proposal Lifecycle

1. Agent writes `PROPOSALS/PROP_NNN_*.md` with status PENDING_REVIEW
2. Agent appends one line to HUMAN_NOTES.md `## Pending Proposals` section
3. Human reviews proposal
4. Human edits proposal status to APPROVED, REJECTED, or NEEDS_INFO
5. If APPROVED: human (or a separate dev session) implements the fix
6. After fix: human deletes the proposal file or moves to PROPOSALS/RESOLVED/
7. Affected experiments are re-run