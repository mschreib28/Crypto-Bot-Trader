"""API response models."""

from datetime import datetime
from typing import List
from pydantic import BaseModel, Field


class StrategyItem(BaseModel):
    """Strategy item in the strategies list response."""
    
    id: str = Field(..., description="Unique strategy identifier")
    name: str = Field(..., description="Human-readable strategy name")
    status: str = Field(..., description="Current lifecycle status of the strategy")
    created_at: datetime = Field(..., description="UTC timestamp when the strategy was registered")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "strategy_001",
                "name": "Momentum BTC v1",
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z"
            }
        }


class StrategyList(BaseModel):
    """Response model for the strategies list endpoint."""
    
    strategies: List[StrategyItem] = Field(..., description="List of registered strategies")
    
    class Config:
        json_schema_extra = {
            "example": {
                "strategies": [
                    {
                        "id": "strategy_001",
                        "name": "Momentum BTC v1",
                        "status": "active",
                        "created_at": "2024-01-01T00:00:00Z"
                    }
                ]
            }
        }
