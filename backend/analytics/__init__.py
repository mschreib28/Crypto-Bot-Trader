"""Trade analytics for factor attribution."""

from backend.analytics.store import (
    capture_entry_snapshot,
    finalize_trade,
    aggregate_by_grade,
    factor_correlations,
    list_trade_records,
)

__all__ = [
    "capture_entry_snapshot",
    "finalize_trade",
    "aggregate_by_grade",
    "factor_correlations",
    "list_trade_records",
]
