#!/usr/bin/env python3
"""
Trade Journal Analysis — Phase 1 of "Way B" scanner optimization.

Reads backtest_trades.csv and produces a multi-dimensional breakdown of
winners vs losers, sliced by every captured entry-time factor.

Output: A markdown report at experiments/trade_journal.md showing:
- Overall stats
- Win rate / R:R / expectancy by each dimension
- Cross-tabulations of the strongest signal dimensions

Usage:
    python experiments/analyze_trades.py
    python experiments/analyze_trades.py --csv path/to/other.csv --out custom.md
    python experiments/analyze_trades.py --min-bucket-size 5
"""
import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    import pandas as pd
except ImportError:
    print("ERROR: pandas not installed. Run: pip install pandas")
    sys.exit(1)


# ─── Configuration ────────────────────────────────────────────────────────────

DEFAULT_CSV = "backtest_trades.csv"
DEFAULT_OUT = "experiments/trade_journal.md"
DEFAULT_MIN_BUCKET = 3  # ignore buckets with < N trades (noise)


# ─── Bucketing helpers ────────────────────────────────────────────────────────

def bucket_rvol(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < 3: return "0_under_3x"
    if v < 5: return "1_3_to_5x"
    if v < 8: return "2_5_to_8x"
    if v < 15: return "3_8_to_15x"
    return "4_over_15x"


def bucket_change_24h(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < -10: return "0_down_10pct+"
    if v < -5: return "1_down_5_10"
    if v < 0: return "2_down_0_5"
    if v < 5: return "3_up_0_5"
    if v < 10: return "4_up_5_10"
    if v < 20: return "5_up_10_20"
    return "6_up_20pct+"


def bucket_change_4h(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < -5: return "0_down_5pct+"
    if v < -2: return "1_down_2_5"
    if v < 0: return "2_down_0_2"
    if v < 2: return "3_up_0_2"
    if v < 5: return "4_up_2_5"
    return "5_up_5pct+"


def bucket_volume_24h(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < 500_000: return "0_under_500k"
    if v < 2_000_000: return "1_500k_2M"
    if v < 5_000_000: return "2_2M_5M"
    if v < 20_000_000: return "3_5M_20M"
    return "4_over_20M"


def bucket_btc_4h(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < -3: return "0_btc_down_3pct+"
    if v < -1: return "1_btc_down_1_3"
    if v < 1: return "2_btc_flat"
    if v < 3: return "3_btc_up_1_3"
    return "4_btc_up_3pct+"


def bucket_vwap_distance(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < -10: return "0_below_10pct+"
    if v < -5: return "1_below_5_10"
    if v < -2: return "2_below_2_5"
    if v < 0: return "3_below_0_2"
    if v < 2: return "4_above_0_2"
    if v < 5: return "5_above_2_5"
    if v < 10: return "6_above_5_10"
    return "7_above_10pct+"


def bucket_atr_pct(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < 2: return "0_under_2pct"
    if v < 4: return "1_2_to_4"
    if v < 7: return "2_4_to_7"
    if v < 12: return "3_7_to_12"
    return "4_over_12pct"


def bucket_htf_rsi(v: float) -> str:
    if pd.isna(v): return "missing"
    if v < 30: return "0_under_30"
    if v < 40: return "1_30_40"
    if v < 50: return "2_40_50"
    if v < 60: return "3_50_60"
    if v < 70: return "4_60_70"
    return "5_over_70"


def bucket_hour(v) -> str:
    if pd.isna(v): return "missing"
    h = int(v)
    if h < 6: return "0_00_06_utc"
    if h < 12: return "1_06_12_utc"
    if h < 18: return "2_12_18_utc"
    return "3_18_24_utc"


def bucket_dow(v) -> str:
    if pd.isna(v): return "missing"
    names = ["0_mon", "1_tue", "2_wed", "3_thu", "4_fri", "5_sat", "6_sun"]
    try:
        return names[int(v)]
    except (ValueError, IndexError):
        return "missing"


# Map of dimension name → (column name in CSV, bucketing function)
DIMENSIONS: dict[str, tuple[str, Callable]] = {
    "grade": ("grade", lambda v: str(v) if not pd.isna(v) else "missing"),
    "side": ("side", lambda v: str(v) if not pd.isna(v) else "missing"),
    "exit_reason": ("exit_reason", lambda v: str(v) if not pd.isna(v) else "missing"),
    "rvol": ("entry_rvol", bucket_rvol),
    "change_24h": ("entry_change_24h_pct", bucket_change_24h),
    "change_4h": ("entry_change_4h_pct", bucket_change_4h),
    "volume_24h": ("entry_volume_24h_usd", bucket_volume_24h),
    "btc_regime": ("entry_btc_4h_change_pct", bucket_btc_4h),
    "vwap_distance": ("entry_vwap_distance_pct", bucket_vwap_distance),
    "htf_rsi": ("entry_htf_rsi", bucket_htf_rsi),
    "atr_pct": ("entry_atr_pct", bucket_atr_pct),
    "hour": ("entry_utc_hour", bucket_hour),
    "day_of_week": ("entry_day_of_week", bucket_dow),
    "year_month": ("entry_year_month",
                   lambda v: str(v) if not pd.isna(v) else "missing"),
}


# ─── Stat helpers ─────────────────────────────────────────────────────────────

@dataclass
class BucketStats:
    n: int
    wins: int
    losses: int
    wr: float
    avg_win_pnl: float
    avg_loss_pnl: float
    rr: float           # avg_win / abs(avg_loss); inf if no losses
    total_pnl: float
    expectancy: float   # per-trade EV in USD
    tp1_hit_rate: float


def compute_stats(group: pd.DataFrame) -> BucketStats:
    n = len(group)
    wins_df = group[group["pnl_usd"] > 0]
    losses_df = group[group["pnl_usd"] <= 0]
    w = len(wins_df)
    l = len(losses_df)
    wr = (w / n * 100) if n else 0.0
    avg_win = wins_df["pnl_usd"].mean() if w else 0.0
    avg_loss = losses_df["pnl_usd"].mean() if l else 0.0
    rr = (avg_win / abs(avg_loss)) if l and avg_loss != 0 else float("inf")
    total = group["pnl_usd"].sum()
    expectancy = total / n if n else 0.0
    tp1_rate = (group["tp1_hit"].astype(bool).sum() / n * 100) if n else 0.0
    return BucketStats(
        n=n, wins=w, losses=l, wr=wr,
        avg_win_pnl=avg_win, avg_loss_pnl=avg_loss,
        rr=rr if rr != float("inf") else 999.0,
        total_pnl=total, expectancy=expectancy, tp1_hit_rate=tp1_rate,
    )


def stars(baseline_wr: float, bucket_wr: float, n: int, min_n: int) -> str:
    """Visual signal strength based on WR deviation from baseline and sample size."""
    if n < min_n:
        return "⚪ (too few)"
    delta = bucket_wr - baseline_wr
    if abs(delta) < 3:
        return ""
    if delta > 0:
        if delta > 15 and n >= 10: return "🟢🟢🟢 strong+"
        if delta > 8 and n >= 5:  return "🟢🟢 mod+"
        return "🟢 mild+"
    else:
        if delta < -15 and n >= 10: return "🔴🔴🔴 strong-"
        if delta < -8 and n >= 5:  return "🔴🔴 mod-"
        return "🔴 mild-"


# ─── Report rendering ─────────────────────────────────────────────────────────

def fmt_money(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):.2f}"


def fmt_rr(v: float) -> str:
    if v >= 999: return "∞"
    return f"{v:.2f}"


def render_overall(df: pd.DataFrame) -> str:
    overall = compute_stats(df)
    lines = [
        "## Overall Stats",
        "",
        f"- **Trades**: {overall.n}",
        f"- **Wins**: {overall.wins} ({overall.wr:.1f}%)",
        f"- **Losses**: {overall.losses}",
        f"- **TP1 hit rate**: {overall.tp1_hit_rate:.1f}%",
        f"- **Avg win**: {fmt_money(overall.avg_win_pnl)}",
        f"- **Avg loss**: {fmt_money(overall.avg_loss_pnl)}",
        f"- **R:R**: {fmt_rr(overall.rr)}",
        f"- **Total P&L**: {fmt_money(overall.total_pnl)}",
        f"- **Expectancy per trade**: {fmt_money(overall.expectancy)}",
        "",
    ]
    return "\n".join(lines)


def render_dimension(df: pd.DataFrame, dim_name: str, col: str,
                     bucket_fn: Callable, baseline_wr: float,
                     min_n: int) -> str:
    if col not in df.columns:
        return f"## By {dim_name}\n\n_Column `{col}` not in CSV. Skipping._\n\n"

    # Apply bucketing
    df = df.copy()
    df["_bucket"] = df[col].apply(bucket_fn)

    # If all values bucketed as "missing", skip
    if df["_bucket"].nunique() == 1 and df["_bucket"].iloc[0] == "missing":
        return f"## By {dim_name}\n\n_All values missing in `{col}`. Skipping._\n\n"

    grouped = df.groupby("_bucket")
    rows = []
    for bucket_label, group in sorted(grouped):
        s = compute_stats(group)
        signal = stars(baseline_wr, s.wr, s.n, min_n)
        rows.append((
            bucket_label, s.n, s.wins, s.wr, fmt_rr(s.rr),
            fmt_money(s.avg_win_pnl), fmt_money(s.avg_loss_pnl),
            fmt_money(s.expectancy), fmt_money(s.total_pnl), signal,
        ))

    lines = [
        f"## By {dim_name}",
        "",
        "| Bucket | N | Wins | WR | R:R | Avg Win | Avg Loss | Expectancy | Total P&L | Signal |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| `{r[0]}` | {r[1]} | {r[2]} | {r[3]:.1f}% | {r[4]} | "
            f"{r[5]} | {r[6]} | {r[7]} | {r[8]} | {r[9]} |"
        )
    lines.append("")
    return "\n".join(lines)


def find_strong_signals(df: pd.DataFrame, baseline_wr: float,
                        min_n: int) -> list[dict]:
    """Identify the strongest predictive buckets across all dimensions."""
    findings = []
    for dim_name, (col, bucket_fn) in DIMENSIONS.items():
        if col not in df.columns:
            continue
        d = df.copy()
        d["_bucket"] = d[col].apply(bucket_fn)
        if d["_bucket"].nunique() == 1 and d["_bucket"].iloc[0] == "missing":
            continue
        for bucket_label, group in d.groupby("_bucket"):
            if bucket_label == "missing":
                continue
            s = compute_stats(group)
            if s.n < min_n:
                continue
            delta = s.wr - baseline_wr
            if abs(delta) >= 8:  # at least 8 percentage points off baseline
                findings.append({
                    "dimension": dim_name,
                    "bucket": bucket_label,
                    "n": s.n,
                    "wr": s.wr,
                    "delta_wr": delta,
                    "expectancy": s.expectancy,
                    "rr": s.rr,
                })
    findings.sort(key=lambda x: abs(x["delta_wr"]), reverse=True)
    return findings


def render_top_signals(findings: list[dict]) -> str:
    if not findings:
        return ("## Strong Signals\n\n_No buckets deviate ≥8% from baseline WR "
                "with sufficient sample size. Either the dataset is too small "
                "or the strategy is genuinely uniform across conditions._\n\n")
    lines = [
        "## Strong Signals (top 15, sorted by |Δ WR|)",
        "",
        "These are the buckets where WR deviates most from baseline. ",
        "Use these for targeted scanner experiments.",
        "",
        "| Dimension | Bucket | N | WR | Δ vs Baseline | Expectancy | R:R |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for f in findings[:15]:
        sign = "+" if f["delta_wr"] >= 0 else ""
        lines.append(
            f"| {f['dimension']} | `{f['bucket']}` | {f['n']} | "
            f"{f['wr']:.1f}% | {sign}{f['delta_wr']:.1f}% | "
            f"{fmt_money(f['expectancy'])} | {fmt_rr(f['rr'])} |"
        )
    lines.append("")
    return "\n".join(lines)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help=f"Input CSV (default {DEFAULT_CSV})")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"Output markdown (default {DEFAULT_OUT})")
    parser.add_argument("--min-bucket-size", type=int, default=DEFAULT_MIN_BUCKET,
                        help=f"Ignore buckets smaller than this (default {DEFAULT_MIN_BUCKET})")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    out_path = Path(args.out)

    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} trades from {csv_path}")

    if len(df) == 0:
        print("ERROR: CSV has 0 rows. Nothing to analyze.")
        sys.exit(1)

    # Compute overall baseline
    overall_stats = compute_stats(df)
    baseline_wr = overall_stats.wr

    # Render report
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")

    sections = [
        "# Trade Journal — Multi-Dimensional Analysis",
        "",
        f"_Generated: {timestamp}_",
        f"_Source: `{csv_path}` ({len(df)} trades)_",
        f"_Minimum bucket size for signals: {args.min_bucket_size}_",
        "",
        render_overall(df),
    ]

    # Strong signals first (executive summary)
    findings = find_strong_signals(df, baseline_wr, args.min_bucket_size)
    sections.append(render_top_signals(findings))

    sections.append("---\n\n## Full Breakdown by Dimension\n")

    for dim_name, (col, bucket_fn) in DIMENSIONS.items():
        sections.append(render_dimension(df, dim_name, col, bucket_fn,
                                         baseline_wr, args.min_bucket_size))

    sections.append("---\n")
    sections.append("## Legend\n")
    sections.append("- 🟢 = bucket WR above baseline by 3+%")
    sections.append("- 🟢🟢 = above by 8+% with N≥5")
    sections.append("- 🟢🟢🟢 = above by 15+% with N≥10")
    sections.append("- 🔴 / 🔴🔴 / 🔴🔴🔴 = same scale, below baseline")
    sections.append("- ⚪ (too few) = bucket size below minimum, ignore signal")
    sections.append("")

    out_path.write_text("\n".join(sections))
    print(f"Report written to {out_path}")
    print(f"Found {len(findings)} buckets with significant deviation from baseline.")
    if findings:
        print("\nTop 3 strongest signals:")
        for f in findings[:3]:
            sign = "+" if f["delta_wr"] >= 0 else ""
            print(f"  - {f['dimension']:<15} {f['bucket']:<20} "
                  f"N={f['n']:<4} WR={f['wr']:.1f}% ({sign}{f['delta_wr']:.1f}%)")


if __name__ == "__main__":
    main()