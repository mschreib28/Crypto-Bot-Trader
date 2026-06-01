"""Tests for GET /api/v1/events/export (Task 5)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.api.routes.events import export_events


def test_export_limit_zero_attachment_json():
    sample = {"timestamp": "2026-01-01T00:00:00Z", "type": "system", "message": "ping"}
    redis_mock = MagicMock()
    redis_mock.lrange.return_value = [json.dumps(sample).encode("utf-8")]

    with patch("backend.api.routes.events.get_redis_client", return_value=redis_mock):
        resp = export_events(limit=0)

    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd.lower()
    assert "events_export_" in cd
    assert resp.media_type == "application/json"
    body = json.loads(resp.body.decode("utf-8"))
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["type"] == "system"
    redis_mock.lrange.assert_called_once()


def test_export_limit_non_zero_422():
    redis_mock = MagicMock()
    with patch("backend.api.routes.events.get_redis_client", return_value=redis_mock):
        with pytest.raises(HTTPException) as exc_info:
            export_events(limit=10)
    assert exc_info.value.status_code == 422
    redis_mock.lrange.assert_not_called()
