"""Performance metrics data models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import json


@dataclass
class StrategyPerformance:
    """Performance metrics for a strategy."""
    
    strategy_id: str
    win_rate: float  # Percentage (0-100)
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl: float  # Total profit/loss in USD
    recent_pnl_24h: float  # P&L from trades in last 24 hours
    average_win: float  # Average profit per winning trade
    average_loss: float  # Average loss per losing trade
    last_updated: str  # ISO timestamp
    
    def to_dict(self) -> dict:
        """Convert to dictionary for Redis storage."""
        return {
            "strategy_id": self.strategy_id,
            "win_rate": str(self.win_rate),
            "total_trades": str(self.total_trades),
            "winning_trades": str(self.winning_trades),
            "losing_trades": str(self.losing_trades),
            "total_pnl": str(self.total_pnl),
            "recent_pnl_24h": str(self.recent_pnl_24h),
            "average_win": str(self.average_win),
            "average_loss": str(self.average_loss),
            "last_updated": self.last_updated,
        }
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: dict) -> "StrategyPerformance":
        """Create from dictionary (Redis retrieval)."""
        return cls(
            strategy_id=data.get("strategy_id", "unknown"),
            win_rate=float(data.get("win_rate", 0.0)),
            total_trades=int(data.get("total_trades", 0)),
            winning_trades=int(data.get("winning_trades", 0)),
            losing_trades=int(data.get("losing_trades", 0)),
            total_pnl=float(data.get("total_pnl", 0.0)),
            recent_pnl_24h=float(data.get("recent_pnl_24h", 0.0)),
            average_win=float(data.get("average_win", 0.0)),
            average_loss=float(data.get("average_loss", 0.0)),
            last_updated=data.get("last_updated", datetime.utcnow().isoformat()),
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "StrategyPerformance":
        """Create from JSON string."""
        return cls.from_dict(json.loads(json_str))
