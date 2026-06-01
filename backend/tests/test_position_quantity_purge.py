"""Tests for dust/invalid position quantity purge."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.positions.quantity import floor_qty_8dp, is_valid_position_quantity
from backend.positions.tracker import PositionTracker


class TestQuantityHelpers:
    def test_floor_qty_8dp(self):
        assert floor_qty_8dp(1.123456789) == 1.12345678

    def test_invalid_dust(self):
        assert is_valid_position_quantity(1e-12) is False
        assert is_valid_position_quantity(0) is False
        assert is_valid_position_quantity(float("nan")) is False

    def test_valid_qty(self):
        assert is_valid_position_quantity(0.001) is True


class TestPurgeCorruptedPosition:
    def test_purge_deletes_position_keys(self):
        redis = MagicMock()
        redis.delete.return_value = 3
        with patch("backend.positions.tracker.get_redis_client", return_value=redis):
            tracker = PositionTracker()

        assert tracker.purge_corrupted_position("ETH/USD", "test") is True
        redis.delete.assert_called_once()
        keys = redis.delete.call_args[0]
        assert any("ETH/USD" in k for k in keys)


class TestStartupValidation:
    def test_purges_invalid_positions(self):
        mock_tracker = MagicMock()
        mock_tracker.list_all_position_symbols.return_value = ["DUST/USD"]
        pos = MagicMock()
        pos.quantity = 1e-15
        mock_tracker.get_position.return_value = pos
        mock_tracker.purge_corrupted_position.return_value = True

        with patch(
            "backend.startup.validation.get_position_tracker",
            return_value=mock_tracker,
        ):
            from backend.startup.validation import run_startup_validation

            result = run_startup_validation()

        assert result["purged"] == 1
        mock_tracker.purge_corrupted_position.assert_called_once()
