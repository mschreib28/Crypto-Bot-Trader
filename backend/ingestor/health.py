"""Shared ingestor health checks for API routes."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Tuple

from backend.redis.keys import (
    INGESTOR_ACTIVE_SYMBOLS_KEY,
    INGESTOR_HEARTBEAT_KEY,
    INGESTOR_HEARTBEAT_MAX_AGE_SECONDS,
    INGESTOR_SYMBOLS_COUNT_KEY,
)

logger = logging.getLogger(__name__)


def _decode_redis_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def get_ingestor_symbols_count(redis_client) -> int:
    """
    Get the number of symbols being ingested.

    Priority: symbols_count key → active_symbols JSON → market:ohlcv:*:1m keys.
    """
    try:
        count = redis_client.get(INGESTOR_SYMBOLS_COUNT_KEY)
        if count is not None:
            return int(_decode_redis_value(count))

        active = redis_client.get(INGESTOR_ACTIVE_SYMBOLS_KEY)
        if active is not None:
            symbols = json.loads(_decode_redis_value(active))
            if isinstance(symbols, list):
                return len(symbols)

        keys = redis_client.keys("market:ohlcv:*:1m")
        if not keys:
            return 0

        symbols = set()
        for key in keys:
            key_str = _decode_redis_value(key)
            parts = key_str.split(":")
            if len(parts) >= 4:
                symbols.add(parts[2])
        return len(symbols)
    except Exception as e:
        logger.warning(f"Failed to get ingestor symbols count: {e}")
        return 0


def _heartbeat_age_seconds(redis_client) -> Tuple[bool, float, str]:
    """Return (exists, age_seconds, raw_timestamp)."""
    heartbeat = redis_client.get(INGESTOR_HEARTBEAT_KEY)
    if heartbeat is None:
        return False, 0.0, "N/A"

    raw = _decode_redis_value(heartbeat)
    heartbeat_time = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    age_seconds = (datetime.now(timezone.utc) - heartbeat_time).total_seconds()
    return True, age_seconds, raw


def check_ingestor_health(redis_client) -> Tuple[str, int]:
    """
    Check ingestor status via Redis heartbeat and symbol metadata.

    Returns:
        Tuple of (status, symbols_count)
    """
    try:
        symbols_count = get_ingestor_symbols_count(redis_client)
        exists, age_seconds, _ = _heartbeat_age_seconds(redis_client)

        if not exists:
            if symbols_count > 0:
                return ("running", symbols_count)
            return ("unknown", symbols_count)

        if age_seconds > INGESTOR_HEARTBEAT_MAX_AGE_SECONDS:
            return ("stale", symbols_count)
        return ("running", symbols_count)
    except Exception as e:
        logger.warning(f"Ingestor health check failed: {e}")
        return ("error", 0)


def check_websocket_health(redis_client) -> Tuple[str, str]:
    """
    Check Kraken data feed status via ingestor heartbeat.

    Returns:
        Tuple of (status, last_heartbeat_timestamp)
    """
    try:
        symbols_count = get_ingestor_symbols_count(redis_client)
        exists, age_seconds, raw = _heartbeat_age_seconds(redis_client)

        if not exists:
            if symbols_count > 0:
                return ("connected", "N/A")
            return ("disconnected", "N/A")

        if age_seconds > INGESTOR_HEARTBEAT_MAX_AGE_SECONDS:
            return ("stale", raw)
        return ("connected", raw)
    except Exception as e:
        logger.warning(f"WebSocket health check failed: {e}")
        return ("error", "N/A")


def is_ingestor_healthy(redis_client) -> bool:
    """True when ingestor is running (fresh heartbeat or active symbol list)."""
    status, _ = check_ingestor_health(redis_client)
    return status == "running"
