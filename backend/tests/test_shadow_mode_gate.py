"""Tests for shadow mode gating (bot_mode vs legacy shadow_live_mode key)."""

from unittest.mock import MagicMock, patch

from backend.api.routes.trading import get_shadow_live_mode
from backend.redis.keys import BOT_MODE_KEY, SHADOW_LIVE_MODE_KEY


def _redis_with(**keys):
    client = MagicMock()

    def _get(key):
        val = keys.get(key)
        if val is None:
            return None
        return val.encode("utf-8") if isinstance(val, str) else val

    client.get.side_effect = _get
    return client


def test_shadow_live_mode_true_when_bot_mode_shadow_legacy_unset():
    client = _redis_with(**{BOT_MODE_KEY: "SHADOW"})
    with patch("backend.api.routes.trading.get_redis_client", return_value=client):
        assert get_shadow_live_mode() is True


def test_shadow_live_mode_false_when_bot_mode_live():
    client = _redis_with(**{BOT_MODE_KEY: "LIVE", SHADOW_LIVE_MODE_KEY: "true"})
    with patch("backend.api.routes.trading.get_redis_client", return_value=client):
        assert get_shadow_live_mode() is False


def test_shadow_live_mode_falls_back_to_legacy_key():
    client = _redis_with(**{SHADOW_LIVE_MODE_KEY: "true"})
    with patch("backend.api.routes.trading.get_redis_client", return_value=client):
        assert get_shadow_live_mode() is True
