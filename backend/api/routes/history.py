"""Persistent activity log history API — query and export."""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_EXPORT_ROWS = 10_000
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200


class HistoryItem(BaseModel):
    id: str
    timestamp: str
    type: str
    message: str
    details: Optional[Dict[str, Any]] = None
    symbol: Optional[str] = None
    strategy: Optional[str] = None


class HistoryResponse(BaseModel):
    items: List[HistoryItem]
    total: int
    page: int
    per_page: int
    pages: int


def _build_query(session, from_date, to_date, type_filter, symbol, strategy, search):
    """Build a filtered SQLAlchemy query against activity_log."""
    from backend.db.models import ActivityLog
    from sqlalchemy import cast, String

    q = session.query(ActivityLog)

    if from_date:
        q = q.filter(ActivityLog.timestamp >= from_date)
    if to_date:
        q = q.filter(ActivityLog.timestamp <= to_date)
    if type_filter:
        q = q.filter(ActivityLog.type == type_filter)
    if symbol:
        q = q.filter(ActivityLog.symbol.ilike(f"%{symbol}%"))
    if strategy:
        q = q.filter(ActivityLog.strategy.ilike(f"%{strategy}%"))
    if search:
        q = q.filter(ActivityLog.message.ilike(f"%{search}%"))

    return q


def _row_to_item(row) -> HistoryItem:
    return HistoryItem(
        id=str(row.id),
        timestamp=row.timestamp.isoformat().replace("+00:00", "Z") if row.timestamp else "",
        type=row.type,
        message=row.message,
        details=row.details,
        symbol=row.symbol,
        strategy=row.strategy,
    )


@router.get("/history", response_model=HistoryResponse, summary="Query activity history")
def query_history(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
    type: Optional[str] = Query(default=None, description="Filter by activity type"),
    symbol: Optional[str] = Query(default=None, description="Filter by symbol (partial match)"),
    strategy: Optional[str] = Query(default=None, description="Filter by strategy (partial match)"),
    from_date: Optional[str] = Query(default=None, description="ISO 8601 start date (inclusive)"),
    to_date: Optional[str] = Query(default=None, description="ISO 8601 end date (inclusive)"),
    search: Optional[str] = Query(default=None, description="Full-text search on message"),
) -> HistoryResponse:
    """Paginated query of the persistent activity log."""
    try:
        from backend.db import get_session

        from_dt = datetime.fromisoformat(from_date.replace("Z", "+00:00")) if from_date else None
        to_dt = datetime.fromisoformat(to_date.replace("Z", "+00:00")) if to_date else None

        session = get_session()
        try:
            q = _build_query(session, from_dt, to_dt, type, symbol, strategy, search)
            total = q.count()
            rows = (
                q.order_by(
                    __import__("backend.db.models", fromlist=["ActivityLog"]).ActivityLog.timestamp.desc()
                )
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            )
            items = [_row_to_item(r) for r in rows]
        finally:
            session.close()

        pages = max(1, (total + per_page - 1) // per_page)
        return HistoryResponse(items=items, total=total, page=page, per_page=per_page, pages=pages)

    except Exception as exc:
        logger.error("Error querying activity history: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to query activity history")


@router.delete("/history", summary="Clear all activity history")
def clear_history() -> dict:
    """Delete all rows from the persistent activity log."""
    try:
        from backend.db import get_session
        from backend.db.models import ActivityLog

        session = get_session()
        try:
            count = session.query(ActivityLog).count()
            session.query(ActivityLog).delete()
            session.commit()
        finally:
            session.close()

        return {"cleared": count, "message": f"Cleared {count} history entries"}

    except Exception as exc:
        logger.error("Error clearing activity history: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to clear activity history")


@router.get("/history/types", summary="List distinct activity types")
def list_types() -> Dict[str, List[str]]:
    """Return all distinct activity types present in the log."""
    try:
        from backend.db import get_session
        from backend.db.models import ActivityLog

        session = get_session()
        try:
            rows = session.query(ActivityLog.type).distinct().order_by(ActivityLog.type).all()
            types = [r[0] for r in rows]
        finally:
            session.close()

        return {"types": types}

    except Exception as exc:
        logger.error("Error fetching activity types: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch activity types")


@router.get("/history/export", summary="Export activity history as CSV or JSON")
def export_history(
    format: str = Query(default="json", pattern="^(csv|json)$"),
    limit: Optional[int] = Query(default=None, ge=1, le=None),
    type: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    strategy: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
):
    """Download activity log as CSV or JSON. Omit limit to export all records."""
    try:
        from backend.db import get_session
        from backend.db.models import ActivityLog

        from_dt = datetime.fromisoformat(from_date.replace("Z", "+00:00")) if from_date else None
        to_dt = datetime.fromisoformat(to_date.replace("Z", "+00:00")) if to_date else None

        session = get_session()
        try:
            q = _build_query(session, from_dt, to_dt, type, symbol, strategy, search)
            q = q.order_by(ActivityLog.timestamp.desc())
            if limit is not None:
                q = q.limit(limit)
            rows = q.all()
            items = [_row_to_item(r) for r in rows]
        finally:
            session.close()

        if format == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["timestamp", "type", "symbol", "strategy", "message", "details"])
            for it in items:
                import json
                writer.writerow([
                    it.timestamp,
                    it.type,
                    it.symbol or "",
                    it.strategy or "",
                    it.message,
                    json.dumps(it.details) if it.details else "",
                ])
            buf.seek(0)
            return StreamingResponse(
                iter([buf.getvalue()]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=activity_log.csv"},
            )
        else:
            import json
            content = json.dumps([it.model_dump() for it in items], indent=2)
            return StreamingResponse(
                iter([content]),
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=activity_log.json"},
            )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error exporting activity history: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export activity history")
