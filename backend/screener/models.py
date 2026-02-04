"""Data models for the screener module."""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


@dataclass
class SignalResult:
    """
    Result of strategy evaluation for a symbol.
    
    This model represents the output from strategy.evaluate() (T62).
    
    Attributes:
        symbol: Trading pair (e.g., "ETH/USD")
        signal_type: Signal type ("BUY", "SELL", or "NONE")
        confidence: Confidence level from 0-100
        strategy_id: ID of the strategy that generated this signal
        indicators: Additional signal data (indicator values, etc.)
        timestamp: ISO8601 timestamp of evaluation
    """
    symbol: str
    signal_type: Literal["BUY", "SELL", "NONE"]
    confidence: float  # 0-100
    strategy_id: str
    indicators: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    
    def __post_init__(self):
        """Validate SignalResult fields."""
        if self.signal_type not in ("BUY", "SELL", "NONE"):
            raise ValueError(
                f"signal_type must be 'BUY', 'SELL', or 'NONE', got: {self.signal_type}"
            )
        if not 0 <= self.confidence <= 100:
            raise ValueError(
                f"confidence must be 0-100, got: {self.confidence}"
            )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "confidence": self.confidence,
            "strategy_id": self.strategy_id,
            "indicators": self.indicators,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignalResult":
        """Create SignalResult from dictionary."""
        return cls(
            symbol=data["symbol"],
            signal_type=data["signal_type"],
            confidence=data["confidence"],
            strategy_id=data["strategy_id"],
            indicators=data.get("indicators", {}),
            timestamp=data.get("timestamp", ""),
        )


@dataclass
class ScreenerResult:
    """
    Result of scanning a symbol for trading signals.
    
    Attributes:
        symbol: Trading pair (e.g., "ETH/USD")
        signal_type: Type of signal detected
        signal_strength: Signal strength from 0-100
        indicators: Dictionary of calculated indicator values
        timestamp: ISO8601 timestamp of the scan
    """
    symbol: str
    signal_type: Literal["BUY", "SELL", "NONE"]
    signal_strength: float  # 0-100
    indicators: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    
    def __post_init__(self):
        """Validate ScreenerResult fields."""
        if self.signal_type not in ("BUY", "SELL", "NONE"):
            raise ValueError(
                f"signal_type must be 'BUY', 'SELL', or 'NONE', got: {self.signal_type}"
            )
        if not 0 <= self.signal_strength <= 100:
            raise ValueError(
                f"signal_strength must be 0-100, got: {self.signal_strength}"
            )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "signal_strength": self.signal_strength,
            "indicators": self.indicators,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScreenerResult":
        """Create ScreenerResult from dictionary."""
        return cls(
            symbol=data["symbol"],
            signal_type=data["signal_type"],
            signal_strength=data["signal_strength"],
            indicators=data.get("indicators", {}),
            timestamp=data.get("timestamp", ""),
        )
