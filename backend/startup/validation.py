"""Startup position and grade validation (runner + API boot)."""

import json
import logging
from typing import Any, Dict, Optional

from backend.positions.quantity import is_valid_position_quantity
from backend.positions.tracker import get_position_tracker
from backend.redis import get_redis_client
from backend.redis.keys import APLUS_SCORES_KEY

logger = logging.getLogger(__name__)

_PASSING_GRADES = frozenset({"A+", "A", "B", "C"})


def _grade_for_symbol(symbol: str) -> Optional[str]:
    try:
        raw = get_redis_client().hget(APLUS_SCORES_KEY, symbol)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            g = data.get("grade")
            return str(g).strip() if g is not None else None
    except Exception:
        return None
    return None


def run_startup_validation() -> Dict[str, Any]:
    """
    Purge positions with invalid quantity; log grade warnings for open positions.

    Returns summary dict: checked, purged, grade_warnings.
    """
    tracker = get_position_tracker()
    symbols = tracker.list_all_position_symbols()
    purged = 0
    grade_warnings = 0

    for symbol in symbols:
        position = tracker.get_position(symbol)
        if position is None:
            continue
        if not is_valid_position_quantity(position.quantity):
            if tracker.purge_corrupted_position(
                symbol, reason="startup_invalid_quantity"
            ):
                purged += 1
            continue

        grade = _grade_for_symbol(symbol)
        norm = (grade or "").strip().upper()
        if not norm or norm in ("D", "F") or norm not in _PASSING_GRADES:
            grade_warnings += 1
            logger.info(
                "startup_validation grade_warning symbol=%s grade=%r "
                "(exits allowed, no new entries until grade improves)",
                symbol,
                grade,
            )

    summary = {
        "checked": len(symbols),
        "purged": purged,
        "grade_warnings": grade_warnings,
    }
    logger.info(
        "Startup validation: %d positions checked, %d purged, %d grade warnings",
        summary["checked"],
        summary["purged"],
        summary["grade_warnings"],
    )
    return summary
