#!/usr/bin/env python3
"""
Automated backtest experiment runner.

Reads experiments/experiments.yaml, runs each experiment that hasn't been
completed yet, parses results, writes one JSON per experiment, regenerates
leaderboard.md.

Usage:
    python experiments/run_experiments.py
    python experiments/run_experiments.py --smoke   # short --days for testing

Designed to be re-runnable. Skips experiments already in results/.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Resolve paths relative to this script's location
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
RESULTS_DIR = SCRIPT_DIR / "results"
EXPERIMENTS_YAML = SCRIPT_DIR / "experiments.yaml"
LEADERBOARD_MD = SCRIPT_DIR / "leaderboard.md"
RUN_LOG = SCRIPT_DIR / "run.log"
BACKTEST_PY = PROJECT_ROOT / "backtest.py"

# Per-experiment timeout (1 hour default; smoke mode uses 5 min)
DEFAULT_TIMEOUT_SECONDS = 3600
SMOKE_TIMEOUT_SECONDS = 300


def log(msg: str) -> None:
    """Print to stdout AND append to run.log."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with RUN_LOG.open("a") as f:
        f.write(line + "\n")


def parse_metrics(stdout: str) -> dict:
    """
    Extract metrics from backtest.py output.
    Backtest format (as of May 2026):
        Trades   : 75  (37 wins / 38 losses)
        Win rate : 49.3%
        R:R      : 3.13:1
        Total P&L: $+12.59
        Max DD   : -$1.93
    """
    def extract(pattern, cast=float, default=None):
        m = re.search(pattern, stdout)
        if not m:
            return default
        try:
            return cast(m.group(1))
        except (ValueError, IndexError):
            return default

    return {
        "trades":    extract(r"Trades\s*:\s*(\d+)", int, 0),
        "win_rate":  extract(r"Win rate\s*:\s*([\d.]+)%", float, 0.0),
        "rr_ratio":  extract(r"R:R\s*:\s*([\d.]+):1", float, 0.0),
        "total_pnl": extract(r"Total P&L:\s*\$([+-]?[\d.]+)", float, 0.0),
        "max_dd":    extract(r"Max DD\s*:\s*-?\$([\d.]+)", float, 0.0),
    }


def run_backtest(exp_id: str, strategy: str, days: int, interval: str,
                 overrides: dict, timeout: int) -> tuple[dict, str, int]:
    """
    Run backtest.py for one experiment.
    Returns: (metrics_dict, stdout_str, exit_code)
    """
    cmd = [
        sys.executable, str(BACKTEST_PY),
        "--strategy", strategy,
        "--days", str(days),
        "--interval", interval,
    ]
    for key, val in (overrides or {}).items():
        flag_base = key.replace("_", "-")
        if isinstance(val, bool):
            if val:
                cmd.append(f"--{flag_base}")
            else:
                cmd.append(f"--no-{flag_base}")
        else:
            cmd.extend([f"--{flag_base}", str(val)])

    log(f"  RUN {exp_id}: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT_ROOT)
        )
        metrics = parse_metrics(result.stdout)
        return metrics, result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT {exp_id} after {timeout}s")
        return {"trades": 0, "win_rate": 0.0, "rr_ratio": 0.0,
                "total_pnl": 0.0, "max_dd": 0.0, "timeout": True}, "", -1


def save_result(exp_id: str, exp_def: dict, metrics: dict, stdout_tail: str,
                exit_code: int) -> Path:
    """Write results/{id}.json with full record."""
    record = {
        "id": exp_id,
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exit_code": exit_code,
        **{k: v for k, v in exp_def.items() if k != "id"},
        **metrics,
        "stdout_tail": stdout_tail[-2000:] if stdout_tail else "",
    }
    path = RESULTS_DIR / f"{exp_id}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def load_existing_results() -> dict[str, dict]:
    """Load all completed experiment results."""
    results = {}
    for path in RESULTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            results[data["id"]] = data
        except Exception as e:
            log(f"  WARN: failed to load {path}: {e}")
    return results


def generate_leaderboard(results: dict[str, dict]) -> None:
    """Write leaderboard.md sorted by P&L with delta vs baseline."""
    baseline = results.get("baseline")
    baseline_pnl = baseline.get("total_pnl") if baseline else None

    rows = []
    for r in results.values():
        if r.get("trades", 0) == 0 and r["id"] != "baseline":
            continue
        rows.append(r)
    rows.sort(key=lambda x: x.get("total_pnl") or -999, reverse=True)

    lines = [
        "# Experiment Leaderboard",
        "",
        f"_Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
        "",
    ]
    if baseline:
        lines.append(
            f"**Baseline** (`{baseline['id']}`): "
            f"Trades={baseline.get('trades', '?')} · "
            f"WR={baseline.get('win_rate', '?')}% · "
            f"R:R={baseline.get('rr_ratio', '?')} · "
            f"P&L=${baseline.get('total_pnl', '?')} · "
            f"DD=-${baseline.get('max_dd', '?')}"
        )
        lines.append("")

    lines.extend([
        "| Rank | ID | Trades | WR % | R:R | P&L | Δ Baseline | Max DD | Description |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for i, r in enumerate(rows, 1):
        pnl = r.get("total_pnl") or 0.0
        delta_str = "—"
        if baseline_pnl is not None and r["id"] != "baseline":
            delta = pnl - baseline_pnl
            delta_str = f"+${delta:.2f}" if delta >= 0 else f"-${abs(delta):.2f}"
        lines.append(
            f"| {i} | `{r['id']}` | {r.get('trades', '-')} | "
            f"{r.get('win_rate', '-')} | {r.get('rr_ratio', '-')} | "
            f"${pnl:.2f} | {delta_str} | "
            f"-${r.get('max_dd', '-')} | {r.get('description', '')} |"
        )

    LEADERBOARD_MD.write_text("\n".join(lines) + "\n")
    log(f"Leaderboard written: {LEADERBOARD_MD}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true",
                        help="Override to --days 60 with short timeout for testing")
    parser.add_argument("--only", type=str, default=None,
                        help="Run only this experiment id (skip others)")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True, parents=True)

    if not EXPERIMENTS_YAML.exists():
        log(f"ERROR: {EXPERIMENTS_YAML} not found")
        sys.exit(1)

    with EXPERIMENTS_YAML.open() as f:
        cfg = yaml.safe_load(f)

    timeout = SMOKE_TIMEOUT_SECONDS if args.smoke else DEFAULT_TIMEOUT_SECONDS
    existing = load_existing_results()
    log(f"Loaded {len(existing)} existing results")

    # Run baseline first if not already done
    if "baseline" not in existing:
        b = cfg.get("baseline", {})
        days = 60 if args.smoke else b.get("days", 1826)
        log(f"RUN baseline (days={days})")
        metrics, stdout, exit_code = run_backtest(
            "baseline", b["strategy"], days,
            b.get("interval", "4h"), b.get("config_overrides", {}),
            timeout,
        )
        save_result("baseline", b, metrics, stdout, exit_code)
        existing = load_existing_results()

    # Run experiments
    for exp in cfg.get("experiments", []):
        if args.only and exp["id"] != args.only:
            continue
        if exp["id"] in existing:
            log(f"SKIP {exp['id']} (already in results)")
            continue
        days = 60 if args.smoke else exp.get("days", 1826)
        log(f"RUN {exp['id']}: {exp.get('description', '')}")
        metrics, stdout, exit_code = run_backtest(
            exp["id"], exp["strategy"], days, exp.get("interval", "4h"),
            exp.get("config_overrides", {}), timeout,
        )
        save_result(exp["id"], exp, metrics, stdout, exit_code)
        existing = load_existing_results()

    generate_leaderboard(existing)
    log("Done.")


if __name__ == "__main__":
    main()