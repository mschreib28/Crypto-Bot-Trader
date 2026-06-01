"""
Live trade performance evaluator (Task 4).

Aggregates closed-trade R-multiples from Redis (strategy:r_multiples:{strategy_id})
within a rolling window, maps DB strategy rows to supervisor canonical names, classifies
with stricter live thresholds, and writes supervisor:live:{strategy} verdicts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.db.models import Strategy
from backend.redis import get_redis_client
from backend.redis.keys import (
    STRATEGY_R_MULTIPLES_KEY,
    STRATEGY_R_MULTIPLES_MAX,
    SUPERVISOR_LIVE_PROMOTE_STREAK_KEY,
)
from backend.supervisor.classifier import StrategyVerdict
from backend.supervisor.config import (
    LIVE_ACTIVE_RR,
    LIVE_ACTIVE_WR,
    LIVE_EVAL_MIN_TRADES,
    LIVE_EVAL_WINDOW_HOURS,
    LIVE_REDUCED_RR,
    LIVE_REDUCED_WR,
)
from backend.supervisor.store import (
    canonical_name,
    delete_live_verdict,
    read_live_verdict,
    write_live_verdict,
)

logger = logging.getLogger(__name__)


def classify_live(win_rate: float, rr_ratio: float, trades: int) -> StrategyVerdict:
    """Classify live rolling stats (stricter thresholds than backtest)."""
    effective_rr = min(rr_ratio, 99.0) if rr_ratio != float("inf") else 99.0

    if trades == 0:
        return StrategyVerdict(
            status="SUSPENDED",
            size_factor=0.0,
            reason="no_trades_in_period",
        )

    if win_rate >= LIVE_ACTIVE_WR and effective_rr >= LIVE_ACTIVE_RR:
        if trades < LIVE_EVAL_MIN_TRADES:
            return StrategyVerdict(
                status="REDUCED",
                size_factor=0.5,
                reason=f"wr_rr_pass_but_sample_small:{trades}<{LIVE_EVAL_MIN_TRADES}",
            )
        return StrategyVerdict(
            status="ACTIVE",
            size_factor=1.0,
            reason=f"wr>={LIVE_ACTIVE_WR}_and_rr>={LIVE_ACTIVE_RR}",
        )

    if win_rate >= LIVE_REDUCED_WR and effective_rr >= LIVE_REDUCED_RR:
        return StrategyVerdict(
            status="REDUCED",
            size_factor=0.5,
            reason=f"wr>={LIVE_REDUCED_WR}_and_rr>={LIVE_REDUCED_RR}",
        )

    return StrategyVerdict(
        status="SUSPENDED",
        size_factor=0.0,
        reason=f"wr={win_rate:.1f}_rr={effective_rr:.2f}_below_threshold",
    )


def _aggregate_window(r_values: list[float]) -> tuple[int, int, int, float, float]:
    """
    Returns (trades, wins, losses, win_rate_pct, rr_ratio).

    R:R = (average winning R) / (average losing R magnitude), matching sim-stats /
    backtest-style display. All-breakeven losses use |R| in the loss average.
    """
    trades = len(r_values)
    if trades == 0:
        return (0, 0, 0, 0.0, 0.0)

    wins_r = [r for r in r_values if r > 0]
    losses_r = [r for r in r_values if r <= 0]

    wins = len(wins_r)
    losses = len(losses_r)
    win_rate = (100.0 * wins / trades) if trades else 0.0

    avg_win = sum(wins_r) / wins if wins else 0.0
    loss_mags = [abs(r) for r in losses_r]
    avg_loss_mag = sum(loss_mags) / losses if losses else 0.0

    if avg_loss_mag > 0:
        rr_ratio = avg_win / avg_loss_mag
    elif avg_win > 0:
        rr_ratio = float("inf")
    else:
        rr_ratio = 0.0

    return (trades, wins, losses, win_rate, rr_ratio)


def _parse_exit_time(raw: str) -> Optional[datetime]:
    try:
        s = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _load_r_multiples_in_window(strategy_id: str, since_utc: datetime) -> list[float]:
    """Load R-multiple values from Redis for exits at or after since_utc."""
    redis = get_redis_client()
    key = STRATEGY_R_MULTIPLES_KEY.format(strategy_id=strategy_id)
    try:
        raw_list = redis.lrange(key, 0, STRATEGY_R_MULTIPLES_MAX - 1)
    except Exception as exc:
        logger.warning("[live_eval] lrange failed for %s: %s", strategy_id, exc)
        return []

    out: list[float] = []
    for raw in raw_list or []:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        exit_time = rec.get("exit_time")
        if not exit_time:
            continue
        ts = _parse_exit_time(str(exit_time))
        if ts is None or ts < since_utc:
            continue
        try:
            out.append(float(rec["r_multiple"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def strategy_uuids_for_canonical(session: Session, canonical: str) -> list[str]:
    """Return string UUIDs for active DB strategies whose name maps to canonical."""
    rows = session.query(Strategy.id, Strategy.name).filter(Strategy.status == "active").all()
    uuids: list[str] = []
    for sid, name in rows:
        if canonical_name(name or "") == canonical:
            uuids.append(str(sid))
    return uuids


def evaluate_live_stats(canonical: str, session: Session) -> Optional[dict[str, Any]]:
    """
    Compute live rolling verdict dict for one canonical strategy, or None if
    fewer than LIVE_EVAL_MIN_TRADES exits in the window (caller should delete live key).
    """
    since = datetime.now(timezone.utc) - timedelta(hours=LIVE_EVAL_WINDOW_HOURS)
    uuids = strategy_uuids_for_canonical(session, canonical)
    r_all: list[float] = []
    for uid in uuids:
        r_all.extend(_load_r_multiples_in_window(uid, since))

    if len(r_all) < LIVE_EVAL_MIN_TRADES:
        return None

    trades, wins, losses, win_rate, rr_ratio = _aggregate_window(r_all)
    verdict_obj = classify_live(win_rate, rr_ratio, trades)
    rr_stored = 99.0 if rr_ratio == float("inf") else rr_ratio

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "strategy": canonical,
        "verdict_obj": verdict_obj,
        "win_rate": round(win_rate, 2),
        "rr_ratio": round(rr_stored, 4) if rr_stored != float("inf") else 99.0,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "last_evaluated": now_iso,
        "source": "live",
        "lookback_hours": LIVE_EVAL_WINDOW_HOURS,
    }


def _apply_promotion_gate(canonical: str, draft: dict, raw: StrategyVerdict) -> dict:
    """
    Demotion to SUSPENDED is immediate. Promotion from a published live SUSPENDED to
    REDUCED/ACTIVE requires two consecutive cycles at REDUCED-or-better.
    """
    redis = get_redis_client()
    streak_key = SUPERVISOR_LIVE_PROMOTE_STREAK_KEY.format(strategy=canonical)

    if raw.status == "SUSPENDED":
        try:
            redis.delete(streak_key)
        except Exception:
            pass
        return draft

    prev = read_live_verdict(canonical)
    prev_status = str((prev or {}).get("status", "")).upper()

    if prev_status != "SUSPENDED":
        try:
            redis.delete(streak_key)
        except Exception:
            pass
        return draft

    try:
        cur = redis.get(streak_key)
        streak = int(cur.decode() if isinstance(cur, bytes) else cur) if cur else 0
        streak += 1
        redis.set(streak_key, str(streak))
    except Exception as exc:
        logger.warning("[live_eval] promote streak failed for %s: %s", canonical, exc)
        streak = 2

    if streak >= 2:
        try:
            redis.delete(streak_key)
        except Exception:
            pass
        return draft

    pending = dict(draft)
    pending["status"] = "SUSPENDED"
    pending["size_factor"] = 0.0
    pending["reason"] = f"promotion_pending:{streak}/2"
    return pending


def publish_live_verdict(canonical: str, session: Session) -> None:
    """Evaluate one strategy and write or delete Redis live verdict."""
    stats = evaluate_live_stats(canonical, session)
    if stats is None:
        delete_live_verdict(canonical)
        return

    v = stats.pop("verdict_obj")
    draft = {
        "strategy": canonical,
        "status": v.status,
        "win_rate": stats["win_rate"],
        "rr_ratio": stats["rr_ratio"],
        "trades": stats["trades"],
        "wins": stats["wins"],
        "losses": stats["losses"],
        "size_factor": v.size_factor,
        "reason": v.reason,
        "last_evaluated": stats["last_evaluated"],
        "source": stats["source"],
        "lookback_hours": stats["lookback_hours"],
    }
    final_dict = _apply_promotion_gate(canonical, draft, v)
    write_live_verdict(canonical, final_dict)


def run_live_eval_for_all_strategies(session: Session, strategies: list[str]) -> None:
    """Run publish_live_verdict for each canonical strategy name."""
    for name in strategies:
        try:
            publish_live_verdict(name, session)
        except Exception as exc:
            logger.error("[live_eval] failed for %s: %s", name, exc, exc_info=True)
