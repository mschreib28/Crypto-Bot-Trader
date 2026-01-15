"""Execution Engine data models matching contract schemas."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Fill:
    """
    Fill model matching contract schema from contracts/types.md.
    
    Represents the result of an executed order.
    """
    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    executed_price: float
    quantity: float
    fees: float
    slippage: float
    exchange_order_id: str
    timestamp: str  # ISO8601 format
    
    def __post_init__(self):
        """Validate Fill fields."""
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got: {self.side}")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got: {self.quantity}")
        if self.fees < 0:
            raise ValueError(f"fees must be non-negative, got: {self.fees}")
        
        # Validate timestamp format (basic check)
        try:
            datetime.fromisoformat(self.timestamp.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"timestamp must be ISO8601 format, got: {self.timestamp}")
