"""WebSocket endpoint for real-time event streaming."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# --- WebSocket Message Models ---


class WebSocketMessage(BaseModel):
    """Base WebSocket message format."""

    type: str = Field(..., description="Event type identifier")
    data: Dict[str, Any] = Field(..., description="Event payload")
    timestamp: str = Field(..., description="ISO8601 UTC timestamp")


class SignalCreatedMessage(WebSocketMessage):
    """Message for signal_created events."""

    type: Literal["signal_created"] = "signal_created"


class OrderExecutedMessage(WebSocketMessage):
    """Message for order_executed events."""

    type: Literal["order_executed"] = "order_executed"


class SystemStatusMessage(WebSocketMessage):
    """Message for system_status events."""

    type: Literal["system_status"] = "system_status"


# --- Connection Manager ---


class ConnectionManager:
    """Manages WebSocket connections for broadcasting events."""

    def __init__(self) -> None:
        """Initialize the connection manager."""
        self._active_connections: List[WebSocket] = []

    @property
    def active_connections(self) -> List[WebSocket]:
        """Return list of active connections (read-only)."""
        return list(self._active_connections)

    @property
    def connection_count(self) -> int:
        """Return number of active connections."""
        return len(self._active_connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._active_connections.append(websocket)
        logger.info(
            "WebSocket client connected. Total connections: %d",
            self.connection_count,
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from active connections."""
        if websocket in self._active_connections:
            self._active_connections.remove(websocket)
            logger.info(
                "WebSocket client disconnected. Total connections: %d",
                self.connection_count,
            )

    async def send_personal_message(
        self, message: Dict[str, Any], websocket: WebSocket
    ) -> None:
        """Send a message to a specific client."""
        await websocket.send_json(message)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """
        Broadcast a message to all connected clients.

        Failed sends are logged and the connection is removed.
        """
        disconnected: List[WebSocket] = []

        for connection in self._active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning("Failed to send to client: %s", e)
                disconnected.append(connection)

        # Clean up failed connections
        for connection in disconnected:
            self.disconnect(connection)


# Global connection manager instance
manager = ConnectionManager()


# --- Helper Functions ---


def create_message(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a properly formatted WebSocket message.

    Args:
        event_type: The event type (e.g., "signal_created", "order_executed")
        data: The event payload

    Returns:
        A dictionary with type, data, and timestamp fields
    """
    return {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


async def broadcast_signal_created(signal_data: Dict[str, Any]) -> None:
    """Broadcast a signal_created event to all connected clients."""
    message = create_message("signal_created", signal_data)
    await manager.broadcast(message)


async def broadcast_order_executed(order_data: Dict[str, Any]) -> None:
    """Broadcast an order_executed event to all connected clients."""
    message = create_message("order_executed", order_data)
    await manager.broadcast(message)


async def broadcast_system_status(halted: bool) -> None:
    """Broadcast a system_status event to all connected clients."""
    message = create_message("system_status", {"halted": halted})
    await manager.broadcast(message)


# --- WebSocket Endpoint ---


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time event streaming.

    Clients connect to receive broadcast events for:
    - signal_created: New trading signals
    - order_executed: Order execution confirmations
    - system_status: System state changes (halt/resume)

    The connection stays open until the client disconnects.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive by waiting for any client message
            # Client can send ping messages to keep connection alive
            data = await websocket.receive_text()
            logger.debug("Received from client: %s", data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected gracefully")
    except Exception as e:
        manager.disconnect(websocket)
        logger.warning("Client connection error: %s", e)
