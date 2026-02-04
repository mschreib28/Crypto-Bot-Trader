"""Type definitions matching contracts/types.md and contracts/events.md.

These types are authoritative and must match the contract schemas exactly.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal


@dataclass
class TradeIntent:
    """
    TradeIntent type matching contracts/types.md.
    
    Represents a strategy's request to enter or exit a position.
    TradeIntents express desire, not permission.
    """
    strategy_id: str
    symbol: str
    side: Literal["buy", "sell"]
    intent_type: Literal["enter", "exit", "reduce"]
    notional_risk_pct: float
    metadata: Dict[str, Any]
    
    def __post_init__(self):
        """Validate TradeIntent fields."""
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got: {self.side}")
        if self.intent_type not in ("enter", "exit", "reduce"):
            raise ValueError(
                f"intent_type must be 'enter', 'exit', or 'reduce', got: {self.intent_type}"
            )
        if self.notional_risk_pct <= 0:
            raise ValueError(
                f"notional_risk_pct must be positive, got: {self.notional_risk_pct}"
            )


@dataclass
class MarketDataEvent:
    """
    MarketDataEvent type matching contracts/events.md.
    
    Normalized market data update (tick or bar) consumed by strategy modules.
    """
    symbol: str
    interval: str  # e.g., "4h", "1d", "tick"
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: str  # ISO8601 format
    
    def __post_init__(self):
        """Validate MarketDataEvent fields."""
        if self.high < max(self.open, self.close):
            raise ValueError(
                f"high ({self.high}) must be >= max(open, close) "
                f"({max(self.open, self.close)})"
            )
        if self.low > min(self.open, self.close):
            raise ValueError(
                f"low ({self.low}) must be <= min(open, close) "
                f"({min(self.open, self.close)})"
            )
        if self.volume < 0:
            raise ValueError(f"volume must be non-negative, got: {self.volume}")


@dataclass
class SignalResult:
    """
    Result of evaluating a strategy against a symbol's bars.
    
    Used by screener to rank opportunities across all symbols.
    """
    symbol: str
    signal_type: str  # "BUY", "SELL", or "NONE"
    confidence: float  # 0.0 to 100.0
    strategy_id: str
    indicators: Dict[str, Any]
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "confidence": self.confidence,
            "strategy_id": self.strategy_id,
            "indicators": self.indicators,
            "timestamp": self.timestamp,
        }
