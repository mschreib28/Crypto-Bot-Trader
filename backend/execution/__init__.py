"""Execution Engine module for order management and execution."""

from backend.execution.executor import execute_approved_intent, set_kraken_client, get_kraken_client
from backend.execution.nonce import get_next_nonce, reset_nonce, get_current_nonce
from backend.execution.models import Fill
from backend.execution.order_manager import convert_intent_to_order_params, execute_order
from backend.execution.kraken_interface import KrakenClientInterface, KrakenClientStub, KrakenOrderResponse

__all__ = [
    "execute_approved_intent",
    "set_kraken_client",
    "get_kraken_client",
    "get_next_nonce",
    "reset_nonce",
    "get_current_nonce",
    "Fill",
    "convert_intent_to_order_params",
    "execute_order",
    "KrakenClientInterface",
    "KrakenClientStub",
    "KrakenOrderResponse",
]
