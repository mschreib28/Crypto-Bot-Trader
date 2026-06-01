"""Redis persistence for supervisor verdicts."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from backend.redis import get_redis_client
from backend.redis.keys import (
    SUPERVISOR_INDEX_KEY,
    SUPERVISOR_LAST_RUN_KEY,
    SUPERVISOR_LIVE_LAST_RUN_KEY,
    SUPERVISOR_LIVE_PROMOTE_STREAK_KEY,
    SUPERVISOR_LIVE_STATUS_KEY,
    SUPERVISOR_LOCK_KEY,
    SUPERVISOR_LOCK_TTL,
    SUPERVISOR_STATUS_KEY,
    STRATEGY_MANUAL_MODE_KEY,
    STRATEGY_MANUAL_MODE_UPDATED_KEY,
    STRATEGY_SIM_BALANCE_KEY,
    STRATEGY_SIM_STATS_KEY,
    STRATEGY_DRAWDOWN_SUSPENDED_KEY,
    STRATEGY_CUMULATIVE_R_LOSS_KEY,
)

logger = logging.getLogger(__name__)

# Maps DB/registry strategy names → backtest CLI canonical names
_CANONICAL: dict[str, str] = {
    "vwap_meanrev": "vwap_meanrev",
    "vwap_meanrev_1h": "vwap_meanrev_1h",
    "vwap_meanreversion": "vwap_meanrev",
    "htf_trend": "htf_trend",
    "htf_trend_pullback": "htf_trend",
    "volatility_breakout": "volatility_breakout",
    "meanrev": "meanrev",
    "mean_reversion": "meanrev",
    "mean_rev": "meanrev",
    "pullback_vwap": "pullback_vwap",
    "pullback_to_vwap": "pullback_vwap",
}


def canonical_name(strategy_name: str) -> str:
    """Map any strategy name variant to its backtest CLI canonical name.

    Falls back to a substring-based heuristic if not in the explicit map.
    Returns the original name lowercased if nothing matches.
    """
    lower = strategy_name.lower()
    if lower in _CANONICAL:
        return _CANONICAL[lower]
    # Substring heuristics (same order as registry.py)
    if "vwap_meanrev" in lower or "vwap_meanreversion" in lower:
        return "vwap_meanrev"
    if "pullback_vwap" in lower or "pullback_to_vwap" in lower:
        return "pullback_vwap"
    if "htf_trend" in lower:
        return "htf_trend"
    if "volatility_breakout" in lower:
        return "volatility_breakout"
    if "meanrev" in lower or "mean_rev" in lower or "mean_reversion" in lower:
        return "meanrev"
    return lower


def write_verdict(strategy: str, verdict: dict) -> None:
    """Persist a strategy verdict to Redis."""
    try:
        redis = get_redis_client()
        key = SUPERVISOR_STATUS_KEY.format(strategy=strategy)
        redis.set(key, json.dumps(verdict))
        redis.sadd(SUPERVISOR_INDEX_KEY, strategy)
        logger.debug(f"[store] Wrote verdict for {strategy}: {verdict['status']}")
    except Exception as exc:
        logger.error(f"[store] Failed to write verdict for {strategy}: {exc}")


def read_verdict(strategy: str) -> Optional[dict]:
    """Read a strategy verdict from Redis. Returns None if not found."""
    try:
        redis = get_redis_client()
        key = SUPERVISOR_STATUS_KEY.format(strategy=strategy)
        raw = redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.error(f"[store] Failed to read verdict for {strategy}: {exc}")
        return None


def read_all_verdicts() -> list[dict]:
    """Read all strategy verdicts from Redis."""
    try:
        redis = get_redis_client()
        strategies = redis.smembers(SUPERVISOR_INDEX_KEY)
        if not strategies:
            return []
        results = []
        for s in strategies:
            name = s.decode() if isinstance(s, bytes) else s
            verdict = read_verdict(name)
            if verdict:
                results.append(verdict)
        return results
    except Exception as exc:
        logger.error(f"[store] Failed to read all verdicts: {exc}")
        return []


def write_last_run(timestamp_iso: str) -> None:
    """Record the ISO timestamp of the latest completed cycle."""
    try:
        redis = get_redis_client()
        redis.set(SUPERVISOR_LAST_RUN_KEY, timestamp_iso)
    except Exception as exc:
        logger.error(f"[store] Failed to write last_run: {exc}")


def read_last_run() -> Optional[str]:
    """Read the ISO timestamp of the last completed cycle."""
    try:
        redis = get_redis_client()
        raw = redis.get(SUPERVISOR_LAST_RUN_KEY)
        return raw.decode() if isinstance(raw, bytes) else raw
    except Exception as exc:
        logger.error(f"[store] Failed to read last_run: {exc}")
        return None


def write_live_verdict(strategy: str, verdict: dict) -> None:
    """Persist rolling live verdict JSON (supervisor:live:{strategy})."""
    try:
        redis = get_redis_client()
        key = SUPERVISOR_LIVE_STATUS_KEY.format(strategy=strategy)
        redis.set(key, json.dumps(verdict))
        logger.debug(f"[store] Wrote live verdict for {strategy}: {verdict.get('status')}")
    except Exception as exc:
        logger.error(f"[store] Failed to write live verdict for {strategy}: {exc}")


def read_live_verdict(strategy: str) -> Optional[dict]:
    """Read rolling live verdict or None if missing."""
    try:
        redis = get_redis_client()
        key = SUPERVISOR_LIVE_STATUS_KEY.format(strategy=strategy)
        raw = redis.get(key)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception as exc:
        logger.error(f"[store] Failed to read live verdict for {strategy}: {exc}")
        return None


def delete_live_verdict(strategy: str) -> None:
    """Remove live verdict and promotion streak (insufficient sample or reset)."""
    try:
        redis = get_redis_client()
        redis.delete(SUPERVISOR_LIVE_STATUS_KEY.format(strategy=strategy))
        redis.delete(SUPERVISOR_LIVE_PROMOTE_STREAK_KEY.format(strategy=strategy))
    except Exception as exc:
        logger.error(f"[store] Failed to delete live verdict for {strategy}: {exc}")


def write_live_last_run(timestamp_iso: str) -> None:
    try:
        redis = get_redis_client()
        redis.set(SUPERVISOR_LIVE_LAST_RUN_KEY, timestamp_iso)
    except Exception as exc:
        logger.error(f"[store] Failed to write live last_run: {exc}")


def read_live_last_run() -> Optional[str]:
    try:
        redis = get_redis_client()
        raw = redis.get(SUPERVISOR_LIVE_LAST_RUN_KEY)
        return raw.decode() if isinstance(raw, bytes) else raw
    except Exception as exc:
        logger.error(f"[store] Failed to read live last_run: {exc}")
        return None


def read_all_live_verdicts(strategies: list[str]) -> list[dict]:
    """Return live verdict or placeholder per strategy (stable order)."""
    out: list[dict] = []
    for name in strategies:
        v = read_live_verdict(name)
        if v:
            out.append(v)
        else:
            out.append(
                {
                    "strategy": name,
                    "status": None,
                    "win_rate": None,
                    "rr_ratio": None,
                    "trades": None,
                    "wins": None,
                    "losses": None,
                    "size_factor": None,
                    "reason": None,
                    "last_evaluated": None,
                    "source": "live",
                }
            )
    return out


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_strategy_manual_mode(strategy: str) -> str:
    """Returns 'LIVE' or 'SIM'. Defaults to 'LIVE' if not set."""
    try:
        redis = get_redis_client()
        key = STRATEGY_MANUAL_MODE_KEY.format(strategy=strategy)
        raw = redis.get(key)
        if raw is None:
            return "LIVE"
        v = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        v = v.strip().upper()
        return "SIM" if v == "SIM" else "LIVE"
    except Exception as exc:
        logger.warning(f"[store] get_strategy_manual_mode({strategy}): {exc}, default LIVE")
        return "LIVE"


def set_strategy_manual_mode(strategy: str, mode: str) -> str:
    """Set 'LIVE' or 'SIM'. Returns ISO timestamp."""
    normalized = str(mode).strip().upper()
    if normalized not in ("LIVE", "SIM"):
        raise ValueError(f"Invalid manual mode: {mode!r} (expected LIVE or SIM)")
    redis = get_redis_client()
    ts = _iso_timestamp()
    key = STRATEGY_MANUAL_MODE_KEY.format(strategy=strategy)
    redis.set(key, normalized)
    redis.set(STRATEGY_MANUAL_MODE_UPDATED_KEY.format(strategy=strategy), ts)
    logger.info(f"[store] strategy {strategy} manual_mode={normalized} at {ts}")
    return ts


def read_cumulative_r_loss(strategy_canonical: str) -> Optional[float]:
    """Read rolling cumulative R loss (sum of negative R in window), or None if unset."""
    try:
        redis = get_redis_client()
        key = STRATEGY_CUMULATIVE_R_LOSS_KEY.format(strategy=strategy_canonical)
        raw = redis.get(key)
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        return float(text)
    except Exception as exc:
        logger.error(
            f"[store] Failed to read cumulative_r_loss for {strategy_canonical}: {exc}"
        )
        return None


def write_cumulative_r_loss(strategy_canonical: str, value: float) -> None:
    """Persist cumulative R loss for supervisor backtest cycle reads."""
    try:
        redis = get_redis_client()
        key = STRATEGY_CUMULATIVE_R_LOSS_KEY.format(strategy=strategy_canonical)
        redis.set(key, str(value))
        logger.debug(
            "[store] cumulative_r_loss strategy=%s value=%.2f",
            strategy_canonical,
            value,
        )
    except Exception as exc:
        logger.error(
            f"[store] Failed to write cumulative_r_loss for {strategy_canonical}: {exc}"
        )


def set_drawdown_suspended(strategy_canonical: str, reason: str) -> None:
    """Mark strategy suspended due to R-drawdown breach (no TTL)."""
    try:
        redis = get_redis_client()
        key = STRATEGY_DRAWDOWN_SUSPENDED_KEY.format(strategy=strategy_canonical)
        redis.set(key, json.dumps({"reason": reason}))
        logger.warning(
            "[store] drawdown_suspended strategy=%s reason=%s",
            strategy_canonical,
            reason,
        )
    except Exception as exc:
        logger.error(f"[store] Failed to set drawdown suspended for {strategy_canonical}: {exc}")


def clear_drawdown_suspended(strategy_canonical: str) -> bool:
    """Clear drawdown suspend flag. Returns True if a key was removed."""
    try:
        redis = get_redis_client()
        key = STRATEGY_DRAWDOWN_SUSPENDED_KEY.format(strategy=strategy_canonical)
        removed = int(redis.delete(key))
        if removed:
            logger.info("[store] drawdown_suspended cleared for %s", strategy_canonical)
        return removed > 0
    except Exception as exc:
        logger.error(f"[store] Failed to clear drawdown suspended for {strategy_canonical}: {exc}")
        return False


def is_drawdown_suspended(strategy_canonical: str) -> bool:
    """True if strategy is sticky-suspended for R-drawdown breach."""
    try:
        redis = get_redis_client()
        key = STRATEGY_DRAWDOWN_SUSPENDED_KEY.format(strategy=strategy_canonical)
        return bool(redis.exists(key))
    except Exception as exc:
        logger.error(
            f"[store] Failed to read drawdown suspended for {strategy_canonical}: {exc}"
        )
        return False


def get_effective_mode(strategy_canonical: str) -> Tuple[str, float]:
    """Returns (mode, size_factor) where mode is 'LIVE' or 'SIM'.

    size_factor applies when mode is LIVE (supervisor sizing); on SIM path use paper at factor 1.0.
    SUSPENDED maps to SIM with factor 1.0 so per-strategy sim stats stay meaningful.
    """
    from backend.api.routes.trading import get_bot_mode
    from backend.supervisor.config import LIVE_EVAL_MIN_TRADES

    if get_bot_mode() == "SHADOW":
        return ("SIM", 1.0)

    manual = get_strategy_manual_mode(strategy_canonical)
    if manual == "SIM":
        return ("SIM", 1.0)

    live = read_live_verdict(strategy_canonical)
    if live and int(live.get("trades") or 0) >= LIVE_EVAL_MIN_TRADES:
        lst = str(live.get("status", "SUSPENDED")).upper()
        if lst == "SUSPENDED":
            return ("SIM", 1.0)
        if lst == "REDUCED":
            return ("LIVE", 0.5)
        return ("LIVE", 1.0)

    verdict = read_verdict(strategy_canonical)
    if verdict is None:
        return ("SIM", 0.5)

    status = str(verdict.get("status", "SUSPENDED")).upper()
    if status == "SUSPENDED":
        return ("SIM", 1.0)
    if status == "REDUCED":
        return ("LIVE", 0.5)
    return ("LIVE", 1.0)


def _default_sim_balance() -> dict[str, float]:
    return {"total_usd": 500.0, "available_usd": 500.0, "pnl": 0.0}


def get_strategy_sim_balance(strategy: str) -> dict[str, float]:
    """Read per-strategy SIM balance JSON from Redis."""
    try:
        redis = get_redis_client()
        raw = redis.get(STRATEGY_SIM_BALANCE_KEY.format(strategy=strategy))
        if not raw:
            return _default_sim_balance()
        data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        return {
            "total_usd": float(data.get("total_usd", 500.0)),
            "available_usd": float(data.get("available_usd", 500.0)),
            "pnl": float(data.get("pnl", 0.0)),
        }
    except Exception as exc:
        logger.warning(f"[store] get_strategy_sim_balance({strategy}): {exc}")
        return _default_sim_balance()


def _write_strategy_sim_balance(strategy: str, bal: dict[str, float]) -> None:
    redis = get_redis_client()
    redis.set(STRATEGY_SIM_BALANCE_KEY.format(strategy=strategy), json.dumps(bal))


def ensure_strategy_sim_balance(strategy: str) -> dict[str, float]:
    """Ensure Redis has a sim balance record; return current balance."""
    bal = get_strategy_sim_balance(strategy)
    _write_strategy_sim_balance(strategy, bal)
    return bal


def apply_strategy_sim_buy(strategy: str, cost_usd: float) -> Optional[dict[str, float]]:
    """Deduct cost from per-strategy sim available (opening position)."""
    try:
        bal = get_strategy_sim_balance(strategy)
        avail = max(0.0, bal["available_usd"] - float(cost_usd))
        bal["available_usd"] = avail
        _write_strategy_sim_balance(strategy, bal)
        return bal
    except Exception as exc:
        logger.error(f"[store] apply_strategy_sim_buy({strategy}): {exc}")
        return None


def apply_strategy_sim_sell(
    strategy: str,
    proceeds_usd: float,
    realized_pnl_usd: float,
    r_multiple: Optional[float] = None,
) -> Optional[dict[str, float]]:
    """Credit proceeds, bump pnl, update rolling sim stats on closed SIM trade."""
    try:
        redis = get_redis_client()
        bal = get_strategy_sim_balance(strategy)
        bal["available_usd"] = max(0.0, bal["available_usd"] + float(proceeds_usd))
        bal["total_usd"] = max(0.0, bal["total_usd"] + float(realized_pnl_usd))
        bal["pnl"] = float(bal.get("pnl", 0.0)) + float(realized_pnl_usd)
        _write_strategy_sim_balance(strategy, bal)

        stats_key = STRATEGY_SIM_STATS_KEY.format(strategy=strategy)
        raw = redis.get(stats_key)
        stats: dict[str, Any] = {"trades": 0, "wins": 0, "losses": 0, "sum_win_r": 0.0, "sum_loss_r": 0.0}
        if raw:
            try:
                stats = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            except Exception:
                pass
        stats["trades"] = int(stats.get("trades", 0)) + 1
        pnl = float(realized_pnl_usd)
        r_val = float(r_multiple) if r_multiple is not None else (1.0 if pnl > 0 else (-1.0 if pnl < 0 else 0.0))
        if pnl > 0:
            stats["wins"] = int(stats.get("wins", 0)) + 1
            stats["sum_win_r"] = float(stats.get("sum_win_r", 0.0)) + max(0.0, r_val)
        elif pnl < 0:
            stats["losses"] = int(stats.get("losses", 0)) + 1
            stats["sum_loss_r"] = float(stats.get("sum_loss_r", 0.0)) + abs(min(0.0, r_val))
        redis.set(stats_key, json.dumps(stats))
        return bal
    except Exception as exc:
        logger.error(f"[store] apply_strategy_sim_sell({strategy}): {exc}")
        return None


def read_strategy_sim_stats(strategy: str) -> dict[str, Any]:
    """Aggregate sim stats + balance for API (rolling, Redis-backed)."""
    bal = get_strategy_sim_balance(strategy)
    try:
        redis = get_redis_client()
        raw = redis.get(STRATEGY_SIM_STATS_KEY.format(strategy=strategy))
        stats = {"trades": 0, "wins": 0, "losses": 0, "sum_win_r": 0.0, "sum_loss_r": 0.0}
        if raw:
            stats = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        trades = int(stats.get("trades", 0))
        wins = int(stats.get("wins", 0))
        losses = int(stats.get("losses", 0))
        sw = float(stats.get("sum_win_r", 0.0))
        sl = float(stats.get("sum_loss_r", 0.0))
        win_rate = (100.0 * wins / trades) if trades else None
        avg_w = sw / wins if wins else 0.0
        avg_l = sl / losses if losses else 0.0
        rr_ratio = (avg_w / avg_l) if avg_l > 0 else None
        return {
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "rr_ratio": rr_ratio,
            "pnl": bal.get("pnl", 0.0),
            "balance": bal,
        }
    except Exception as exc:
        logger.warning(f"[store] read_strategy_sim_stats({strategy}): {exc}")
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "rr_ratio": None,
            "pnl": bal.get("pnl", 0.0),
            "balance": bal,
        }


def acquire_lock() -> bool:
    """Acquire the supervisor cycle lock. Returns True if acquired."""
    try:
        redis = get_redis_client()
        result = redis.set(SUPERVISOR_LOCK_KEY, "1", nx=True, ex=SUPERVISOR_LOCK_TTL)
        return result is not None
    except Exception as exc:
        logger.error(f"[store] Failed to acquire lock: {exc}")
        return False


def release_lock() -> None:
    """Release the supervisor cycle lock."""
    try:
        redis = get_redis_client()
        redis.delete(SUPERVISOR_LOCK_KEY)
    except Exception as exc:
        logger.error(f"[store] Failed to release lock: {exc}")
