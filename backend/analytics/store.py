"""Redis-backed trade analytics records and aggregations."""

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.redis import get_redis_client
from backend.redis.keys import (
    APLUS_SCORES_KEY,
    TRADE_ANALYTICS_MAX_RECORDS,
    TRADE_ANALYTICS_PENDING_KEY,
    TRADE_ANALYTICS_RECORDS_KEY,
)

logger = logging.getLogger(__name__)

_NUMERIC_FACTORS = (
    "rvol",
    "market_cap",
    "supply_pct",
    "price",
    "spread_bps",
    "change_24h_pct",
    "vwap_distance_pct",
    "confidence",
)


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)


def _aplus_snapshot(symbol: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        raw = get_redis_client().hget(APLUS_SCORES_KEY, symbol)
        if not raw:
            return out
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return out
        out["screener_grade"] = data.get("grade")
        out["rvol"] = data.get("rvol")
        out["market_cap"] = data.get("market_cap")
        out["supply_pct"] = data.get("supply_ratio")
        out["price"] = data.get("price")
        out["spread_bps"] = data.get("spread_bps")
        out["change_24h_pct"] = data.get("change_24h_pct")
    except Exception as exc:
        logger.debug("analytics aplus read failed for %s: %s", symbol, exc)
    return out


def _resolve_vwap_distance_pct(
    meta: Dict[str, Any],
    strategy_specific: Dict[str, Any],
    symbol: str,
) -> Optional[float]:
    """Resolve signed VWAP distance % (screener convention: negative = below VWAP)."""
    for key in ("vwap_distance_pct",):
        val = meta.get(key) or strategy_specific.get(key)
        if val is not None:
            try:
                f = float(val)
                if math.isfinite(f):
                    return f
            except (TypeError, ValueError):
                pass

    try:
        from backend.screener.strategy_columns import read_cached_vwap_distance

        cached = read_cached_vwap_distance(symbol)
        if cached is not None:
            return cached
    except Exception as exc:
        logger.debug("vwap_distance cache read failed for %s: %s", symbol, exc)

    dev = strategy_specific.get("deviation_pct")
    if dev is not None:
        try:
            f = float(dev)
            if math.isfinite(f):
                return -f
        except (TypeError, ValueError):
            pass
    return None


def _resolve_htf_trend_direction(
    meta: Dict[str, Any],
    strategy_specific: Dict[str, Any],
    symbol: str,
) -> Optional[str]:
    """Resolve HTF trend as UP or DOWN."""
    for key in ("htf_trend_direction",):
        val = meta.get(key) or strategy_specific.get(key)
        if val is not None:
            norm = str(val).strip().upper()
            if norm in ("UP", "DOWN"):
                return norm

    try:
        from backend.screener.strategy_columns import read_cached_htf_trend

        cached = read_cached_htf_trend(symbol)
        if cached:
            return cached
    except Exception as exc:
        logger.debug("htf_trend cache read failed for %s: %s", symbol, exc)

    trend = strategy_specific.get("trend_direction")
    if trend is not None:
        mapping = {"BULLISH": "UP", "BEARISH": "DOWN"}
        return mapping.get(str(trend).strip().upper())
    return None


def capture_entry_snapshot(
    symbol: str,
    strategy: str,
    entry_price: float,
    quantity: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Store entry-time factors keyed by symbol until close."""
    meta = metadata or {}
    strategy_specific = meta.get("strategy_specific") or {}
    if not isinstance(strategy_specific, dict):
        strategy_specific = {}

    snap = {
        "symbol": symbol,
        "strategy": strategy,
        "entry_price": entry_price,
        "quantity": quantity,
        "entry_at": datetime.now(timezone.utc).isoformat(),
        "confidence": meta.get("confidence"),
        "vwap_distance_pct": _resolve_vwap_distance_pct(meta, strategy_specific, symbol),
        "htf_trend_direction": _resolve_htf_trend_direction(
            meta, strategy_specific, symbol
        ),
    }
    snap.update(_aplus_snapshot(symbol))

    try:
        key = TRADE_ANALYTICS_PENDING_KEY.format(symbol=symbol)
        get_redis_client().set(key, json.dumps(snap))
    except Exception as exc:
        logger.warning("Failed to capture entry snapshot for %s: %s", symbol, exc)


def finalize_trade(
    symbol: str,
    strategy: str,
    exit_price: float,
    pnl_usd: float,
    r_multiple: float,
    is_win: bool,
    exit_reason: str,
    exit_at: Optional[str] = None,
) -> None:
    """Append closed trade record and remove pending snapshot."""
    rc = get_redis_client()
    pending_key = TRADE_ANALYTICS_PENDING_KEY.format(symbol=symbol)
    entry: Dict[str, Any] = {}
    try:
        raw = rc.get(pending_key)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            entry = json.loads(raw)
    except Exception:
        entry = {}

    record = {
        "symbol": symbol,
        "strategy": strategy,
        "entry_at": entry.get("entry_at"),
        "exit_at": exit_at or datetime.now(timezone.utc).isoformat(),
        "entry_price": entry.get("entry_price"),
        "exit_price": exit_price,
        "pnl_usd": round(pnl_usd, 4),
        "r_multiple": r_multiple,
        "is_win": bool(is_win),
        "exit_reason": exit_reason,
        "screener_grade": entry.get("screener_grade"),
        "rvol": entry.get("rvol"),
        "market_cap": entry.get("market_cap"),
        "supply_pct": entry.get("supply_pct"),
        "price": entry.get("price"),
        "spread_bps": entry.get("spread_bps"),
        "change_24h_pct": entry.get("change_24h_pct"),
        "vwap_distance_pct": entry.get("vwap_distance_pct"),
        "htf_trend_direction": entry.get("htf_trend_direction"),
        "confidence": entry.get("confidence"),
    }

    try:
        rc.lpush(TRADE_ANALYTICS_RECORDS_KEY, json.dumps(record))
        rc.ltrim(TRADE_ANALYTICS_RECORDS_KEY, 0, TRADE_ANALYTICS_MAX_RECORDS - 1)
        rc.delete(pending_key)
    except Exception as exc:
        logger.warning("Failed to finalize trade analytics for %s: %s", symbol, exc)


def list_trade_records() -> List[Dict[str, Any]]:
    try:
        raw_list = get_redis_client().lrange(TRADE_ANALYTICS_RECORDS_KEY, 0, -1)
    except Exception as exc:
        logger.error("Failed to list trade analytics: %s", exc)
        return []
    records: List[Dict[str, Any]] = []
    for raw in raw_list or []:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            records.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return records


def aggregate_by_grade() -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for rec in list_trade_records():
        grade = str(rec.get("screener_grade") or "unknown")
        b = buckets.setdefault(
            grade,
            {"grade": grade, "trades": 0, "wins": 0, "sum_r": 0.0},
        )
        b["trades"] += 1
        if rec.get("is_win"):
            b["wins"] += 1
        b["sum_r"] += float(rec.get("r_multiple") or 0)

    out: List[Dict[str, Any]] = []
    for grade, b in sorted(buckets.items()):
        trades = b["trades"]
        wins = b["wins"]
        out.append(
            {
                "grade": grade,
                "trades": trades,
                "win_rate": round((wins / trades) * 100, 2) if trades else 0.0,
                "avg_r": round(b["sum_r"] / trades, 2) if trades else 0.0,
            }
        )
    return out


def factor_correlations() -> Dict[str, Any]:
    records = list_trade_records()
    if len(records) < 3:
        return {"factors": [], "sample_size": len(records)}

    results: List[Dict[str, Any]] = []
    for factor in _NUMERIC_FACTORS:
        pairs_win: List[tuple] = []
        pairs_r: List[tuple] = []
        for rec in records:
            val = rec.get(factor)
            if val is None:
                continue
            try:
                x = float(val)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(x):
                continue
            pairs_win.append((x, 1.0 if rec.get("is_win") else 0.0))
            pairs_r.append((x, float(rec.get("r_multiple") or 0)))

        corr_win = None
        corr_r = None
        if len(pairs_win) >= 10:
            xs, ys = zip(*pairs_win)
            corr_win = _pearson(list(xs), list(ys))
        if len(pairs_r) >= 10:
            xs, ys = zip(*pairs_r)
            corr_r = _pearson(list(xs), list(ys))

        results.append(
            {
                "factor": factor,
                "sample_size": len(pairs_win),
                "correlation_win": corr_win,
                "correlation_r": corr_r,
            }
        )

    results.sort(
        key=lambda r: abs(r["correlation_win"] or 0) + abs(r["correlation_r"] or 0),
        reverse=True,
    )
    return {"factors": results, "sample_size": len(records)}
