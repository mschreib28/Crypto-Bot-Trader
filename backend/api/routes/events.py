"""Events log endpoint and helper functions."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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
