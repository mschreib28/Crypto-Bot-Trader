"""Trade analytics API routes."""

from typing import Any, Dict, List

from fastapi import APIRouter

from backend.analytics.store import (
    aggregate_by_grade,
    factor_correlations,
    list_trade_records,
)

router = APIRouter()


@router.get("/analytics/trades")
async def get_analytics_trades() -> Dict[str, Any]:
    """Return all closed-trade analytics records."""
    records = list_trade_records()
    return {"data": records, "count": len(records)}


@router.get("/analytics/by-grade")
async def get_analytics_by_grade() -> Dict[str, Any]:
    """Win rate and avg R grouped by screener grade."""
    rows = aggregate_by_grade()
    return {"data": rows}


@router.get("/analytics/factor-correlation")
async def get_factor_correlation() -> Dict[str, Any]:
    """Pearson correlation between entry factors and outcomes."""
    return factor_correlations()
