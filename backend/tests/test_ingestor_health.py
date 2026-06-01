"""Unit tests for ingestor health checks."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from backend.ingestor.health import (
    check_ingestor_health,
    check_websocket_health,
    get_ingestor_symbols_count,
    is_ingestor_healthy,
)
from backend.redis.keys import (
    INGESTOR_ACTIVE_SYMBOLS_KEY,
    INGESTOR_HEARTBEAT_KEY,
    INGESTOR_SYMBOLS_COUNT_KEY,
)


def _fresh_heartbeat() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_heartbeat() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()


class TestGetIngestorSymbolsCount:
    def test_reads_symbols_count_key_first(self):
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: b"23",
        }.get(key)

        assert get_ingestor_symbols_count(client) == 23

    def test_falls_back_to_active_symbols(self):
        symbols = ["BTC/USD", "ETH/USD", "SOL/USD"]
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: None,
            INGESTOR_ACTIVE_SYMBOLS_KEY: json.dumps(symbols),
        }.get(key)

        assert get_ingestor_symbols_count(client) == 3

    def test_falls_back_to_ohlcv_keys(self):
        client = MagicMock()
        client.get.return_value = None
        client.keys.return_value = [
            b"market:ohlcv:BTC/USD:1m",
            b"market:ohlcv:ETH/USD:1m",
        ]

        assert get_ingestor_symbols_count(client) == 2


class TestCheckIngestorHealth:
    def test_fresh_heartbeat_is_running(self):
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: b"23",
            INGESTOR_HEARTBEAT_KEY: _fresh_heartbeat(),
        }.get(key)

        status, count = check_ingestor_health(client)
        assert status == "running"
        assert count == 23

    def test_missing_heartbeat_with_active_symbols_is_running(self):
        symbols = ["BTC/USD"] * 23
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: None,
            INGESTOR_ACTIVE_SYMBOLS_KEY: json.dumps(symbols),
            INGESTOR_HEARTBEAT_KEY: None,
        }.get(key)

        status, count = check_ingestor_health(client)
        assert status == "running"
        assert count == 23

    def test_stale_heartbeat_is_stale(self):
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: b"10",
            INGESTOR_HEARTBEAT_KEY: _stale_heartbeat(),
        }.get(key)

        status, count = check_ingestor_health(client)
        assert status == "stale"
        assert count == 10

    def test_nothing_in_redis_is_unknown(self):
        client = MagicMock()
        client.get.return_value = None
        client.keys.return_value = []

        status, count = check_ingestor_health(client)
        assert status == "unknown"
        assert count == 0


class TestCheckWebSocketHealth:
    def test_fresh_heartbeat_is_connected(self):
        ts = _fresh_heartbeat()
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: b"5",
            INGESTOR_HEARTBEAT_KEY: ts,
        }.get(key)

        status, last = check_websocket_health(client)
        assert status == "connected"
        assert last == ts

    def test_missing_heartbeat_with_symbols_is_connected(self):
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: None,
            INGESTOR_ACTIVE_SYMBOLS_KEY: json.dumps(["BTC/USD"]),
            INGESTOR_HEARTBEAT_KEY: None,
        }.get(key)

        status, last = check_websocket_health(client)
        assert status == "connected"
        assert last == "N/A"


class TestIsIngestorHealthy:
    def test_running_is_healthy(self):
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: b"1",
            INGESTOR_HEARTBEAT_KEY: _fresh_heartbeat(),
        }.get(key)

        assert is_ingestor_healthy(client) is True

    def test_stale_is_unhealthy(self):
        client = MagicMock()
        client.get.side_effect = lambda key: {
            INGESTOR_SYMBOLS_COUNT_KEY: b"1",
            INGESTOR_HEARTBEAT_KEY: _stale_heartbeat(),
        }.get(key)

        assert is_ingestor_healthy(client) is False
