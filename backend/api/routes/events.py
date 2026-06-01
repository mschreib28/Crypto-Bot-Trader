"""Events log endpoint and helper functions."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from backend.db.audit import get_audit_writer
from backend.redis import get_redis_client
from backend.redis.keys import EVENTS_LOG_KEY

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum number of event entries to store
EVENTS_LOG_MAX = 100


class ActivityItem(BaseModel):
    """Single activity entry."""
    timestamp: str
    type: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ActivityResponse(BaseModel):
    """Response model for activity list."""
    activities: List[ActivityItem]


def log_activity(
    activity_type: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log an activity event to Redis.
    
    Events are stored in a Redis list with LPUSH (newest first),
    and trimmed to keep only the most recent EVENTS_LOG_MAX entries.
    
    Args:
        activity_type: Type of activity (signal, order, error, system)
        message: Human-readable description
        details: Optional additional details
    """
    try:
        client = get_redis_client()
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        entry = {
            "timestamp": timestamp,
            "type": activity_type,
            "message": message,
        }
        if details:
            entry["details"] = details
        
        # LPUSH adds to front of list (newest first)
        client.lpush(EVENTS_LOG_KEY, json.dumps(entry))

        # LTRIM keeps only the first N entries (0 to N-1)
        client.ltrim(EVENTS_LOG_KEY, 0, EVENTS_LOG_MAX - 1)

        logger.debug(f"Activity logged: [{activity_type}] {message}")

        # Persist to PostgreSQL (non-blocking, fire-and-forget)
        try:
            get_audit_writer().enqueue(activity_type, message, details, timestamp)
        except Exception as pg_err:
            logger.warning(f"AuditWriter enqueue failed: {pg_err}")

    except Exception as e:
        logger.warning(f"Failed to log activity: {e}")


@router.get("/events", summary="List recent system events", response_model=ActivityResponse)
async def list_activity(
    limit: int = Query(default=20, ge=1, le=100, description="Maximum number of events to return")
) -> ActivityResponse:
    """
    List recent system events.
    
    Returns a chronological list of recent system events including
    signals, orders, errors, and system events.
    
    Sorted by timestamp descending (newest first).
    """
    try:
        client = get_redis_client()
        
        # LRANGE 0 to limit-1 gets the first 'limit' entries (newest first)
        raw_entries = client.lrange(EVENTS_LOG_KEY, 0, limit - 1)
        
        activities = []
        for raw in raw_entries:
            try:
                data = json.loads(raw)
                activities.append(ActivityItem(
                    timestamp=data.get("timestamp", ""),
                    type=data.get("type", ""),
                    message=data.get("message", ""),
                    details=data.get("details"),
                ))
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse activity entry: {e}")
                continue
        
        return ActivityResponse(activities=activities)
        
    except Exception as e:
        logger.error(f"Error fetching activities: {e}", exc_info=True)
        # Return empty list on error rather than failing
        return ActivityResponse(activities=[])


@router.get("/events/export", summary="Download full events log as JSON (attachment)")
def export_events(
    limit: int = Query(0, ge=0, description="Must be 0 — exports entire Redis list (no export-time cap)"),
) -> Response:
    """
    Returns a downloadable JSON file (not inline JSON). Only limit=0 is supported.
    """
    if limit != 0:
        raise HTTPException(
            status_code=422,
            detail="Only limit=0 is supported (exports the entire Redis events list).",
        )
    try:
        client = get_redis_client()
        raw_entries = client.lrange(EVENTS_LOG_KEY, 0, -1)
    except Exception as e:
        logger.error(f"Error exporting events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export events") from e

    rows: List[Dict[str, Any]] = []
    for raw in raw_entries or []:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            rows.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse activity entry during export: {e}")
            continue

    body = json.dumps(rows).encode("utf-8")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"events_export_{ts}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/events", summary="Clear event log")
async def clear_activity_log() -> dict:
    """
    Clear all entries from the event log.
    
    Returns count of entries cleared.
    """
    try:
        client = get_redis_client()
        # Get count before clearing
        count = client.llen(EVENTS_LOG_KEY)
        # Delete the key
        client.delete(EVENTS_LOG_KEY)
        return {"cleared": count, "message": f"Cleared {count} event entries"}
    except Exception as e:
        logger.error(f"Failed to clear event log: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear event log")
