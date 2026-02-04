"""Position data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Position:
    """
    Position model for tracking owned assets.
    
    Stores the current state of a trading position including
    entry details and unrealized P&L.
    """
    symbol: str           # "ETH/USD"
    side: str             # "long" or "short"
    quantity: float       # Amount owned
    entry_price: float    # Average entry price
    entry_time: str       # ISO timestamp
    unrealized_pnl: float = 0.0
    current_price: Optional[float] = None  # Current market price (updated periodically)
    opened_by_strategy_id: Optional[str] = None  # Strategy that opened this position
    stop_loss_order_id: Optional[str] = None  # Kraken stop-loss order txid
    stop_loss_price: Optional[float] = None  # Stop-loss trigger price
    # Scout & Soldier two-stage entry fields
    scout_entry_price: Optional[float] = None  # Scout entry price (first stage)
    soldier_entry_price: Optional[float] = None  # Soldier scale-in entry price (second stage)
    scale_in_triggered: bool = False  # Whether Soldier scale-in has been triggered
    breakeven_guard_active: bool = False  # Whether breakeven stop is active
    breakeven_stop_price: Optional[float] = None  # Breakeven stop price (entry + fees)
    trailing_stop_active: bool = False  # Whether ATR trailing stop is active
    trailing_stop_price: Optional[float] = None  # ATR trailing stop price
    
    def __post_init__(self):
        """Validate Position fields."""
        if self.side not in ("long", "short"):
            raise ValueError(f"side must be 'long' or 'short', got: {self.side}")
        if self.quantity < 0:
            raise ValueError(f"quantity must be non-negative, got: {self.quantity}")
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be positive, got: {self.entry_price}")
        
        # Validate timestamp format
        try:
            datetime.fromisoformat(self.entry_time.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"entry_time must be ISO8601 format, got: {self.entry_time}")
    
    def to_dict(self) -> dict:
        """Convert position to dictionary for Redis storage."""
        data = {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "entry_price": str(self.entry_price),
            "entry_time": self.entry_time,
            "unrealized_pnl": str(self.unrealized_pnl),
        }
        if self.current_price is not None:
            data["current_price"] = str(self.current_price)
        if self.opened_by_strategy_id is not None:
            data["opened_by_strategy_id"] = self.opened_by_strategy_id
        if self.stop_loss_order_id is not None:
            data["stop_loss_order_id"] = self.stop_loss_order_id
        if self.stop_loss_price is not None:
            data["stop_loss_price"] = str(self.stop_loss_price)
        # Scout & Soldier fields
        if self.scout_entry_price is not None:
            data["scout_entry_price"] = str(self.scout_entry_price)
        if self.soldier_entry_price is not None:
            data["soldier_entry_price"] = str(self.soldier_entry_price)
        if self.scale_in_triggered:
            data["scale_in_triggered"] = "true"
        if self.breakeven_guard_active:
            data["breakeven_guard_active"] = "true"
        if self.breakeven_stop_price is not None:
            data["breakeven_stop_price"] = str(self.breakeven_stop_price)
        if self.trailing_stop_active:
            data["trailing_stop_active"] = "true"
        if self.trailing_stop_price is not None:
            data["trailing_stop_price"] = str(self.trailing_stop_price)
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        """Create position from dictionary (Redis retrieval)."""
        stop_loss_price = data.get("stop_loss_price")
        current_price = data.get("current_price")
        scout_entry_price = data.get("scout_entry_price")
        soldier_entry_price = data.get("soldier_entry_price")
        scale_in_triggered = data.get("scale_in_triggered", "false").lower() == "true"
        breakeven_guard_active = data.get("breakeven_guard_active", "false").lower() == "true"
        breakeven_stop_price = data.get("breakeven_stop_price")
        trailing_stop_active = data.get("trailing_stop_active", "false").lower() == "true"
        trailing_stop_price = data.get("trailing_stop_price")
        return cls(
            symbol=data["symbol"],
            side=data["side"],
            quantity=float(data["quantity"]),
            entry_price=float(data["entry_price"]),
            entry_time=data["entry_time"],
            unrealized_pnl=float(data.get("unrealized_pnl", 0.0)),
            current_price=float(current_price) if current_price else None,
            opened_by_strategy_id=data.get("opened_by_strategy_id"),
            stop_loss_order_id=data.get("stop_loss_order_id"),
            stop_loss_price=float(stop_loss_price) if stop_loss_price else None,
            scout_entry_price=float(scout_entry_price) if scout_entry_price else None,
            soldier_entry_price=float(soldier_entry_price) if soldier_entry_price else None,
            scale_in_triggered=scale_in_triggered,
            breakeven_guard_active=breakeven_guard_active,
            breakeven_stop_price=float(breakeven_stop_price) if breakeven_stop_price else None,
            trailing_stop_active=trailing_stop_active,
            trailing_stop_price=float(trailing_stop_price) if trailing_stop_price else None,
        )
