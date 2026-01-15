"""Risk Manager data models matching contract schemas."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class RiskDecision:
    """
    Risk Manager's evaluation of a TradeIntent.
    
    Matches the RiskDecision contract schema from contracts/types.md.
    """
    intent_id: str
    approved: bool
    rejection_reason: Optional[str]
    evaluated_portfolio_risk: float
    timestamp: str  # ISO8601 format
    
    def __post_init__(self):
        """Validate RiskDecision fields."""
        if self.approved and self.rejection_reason is not None:
            raise ValueError("rejection_reason must be None when approved is True")
        if not self.approved and self.rejection_reason is None:
            raise ValueError("rejection_reason must be provided when approved is False")
        
        # Validate timestamp format (basic check)
        try:
            datetime.fromisoformat(self.timestamp.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"timestamp must be ISO8601 format, got: {self.timestamp}")
