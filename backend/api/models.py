"""API response models."""

from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SystemStatus(BaseModel):
    """System status response model."""
    
    halted: bool = Field(..., description="Whether the system is in halt mode")
    portfolio_exposure: float = Field(..., description="Current total portfolio exposure as percentage of equity")
    active_strategies: int = Field(..., description="Count of active strategies")
    redis_connected: bool = Field(..., description="Whether Redis is reachable")
    db_connected: bool = Field(..., description="Whether database connection is healthy")
    ingestor_healthy: bool = Field(..., description="Whether data ingestor is healthy")
    last_updated: datetime = Field(..., description="UTC timestamp when this status was generated")
    
    class Config:
        json_schema_extra = {
            "example": {
                "halted": False,
                "portfolio_exposure": 12.5,
                "active_strategies": 2,
                "redis_connected": True,
                "db_connected": True,
                "ingestor_healthy": True,
                "last_updated": "2024-01-01T00:00:00Z"
            }
        }


class StrategyItem(BaseModel):
    """Strategy item in the strategies list response."""
    
    id: str = Field(..., description="Unique strategy identifier")
    name: str = Field(..., description="Human-readable strategy name")
    status: str = Field(..., description="Current lifecycle status of the strategy")
    created_at: datetime = Field(..., description="UTC timestamp when the strategy was registered")
    interval: str = Field(..., description="Trading interval/timeframe for the strategy")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "strategy_001",
                "name": "Momentum BTC v1",
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "interval": "5m"
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


class SignalItem(BaseModel):
    """Signal item in the signals list response."""
    
    id: str = Field(..., description="Unique signal identifier")
    strategy_id: str = Field(..., description="ID of the strategy that generated this signal")
    symbol: str = Field(..., description="Trading pair symbol")
    side: str = Field(..., description="Direction of the signal (buy/sell)")
    intent_type: str = Field(..., description="Type of trade intent (enter/exit/reduce)")
    status: str = Field(..., description="Current status of the signal")
    created_at: datetime = Field(..., description="UTC timestamp when the signal was created")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "strategy_id": "987fcdeb-51a2-3b4c-d567-890123456789",
                "symbol": "BTC/USD",
                "side": "buy",
                "intent_type": "enter",
                "status": "approved",
                "created_at": "2024-01-01T12:00:00Z"
            }
        }


class OrderItem(BaseModel):
    """Order item in the orders list response."""
    
    id: str = Field(..., description="Unique order identifier")
    symbol: str = Field(..., description="Trading pair symbol")
    side: str = Field(..., description="Execution side (buy/sell)")
    quantity: float = Field(..., description="Quantity of the base asset executed")
    price: float = Field(..., description="Price at which the order was executed")
    status: str = Field(..., description="Order status (pending/executed/cancelled/failed)")
    strategy_id: Optional[str] = Field(None, description="ID of the strategy that placed this order, if any")
    executed_at: Optional[datetime] = Field(None, description="UTC timestamp when the order was executed")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "456e7890-a12b-34c5-d678-901234567890",
                "symbol": "BTC/USD",
                "side": "buy",
                "quantity": 0.001,
                "price": 98000.00,
                "status": "executed",
                "strategy_id": "trend_following",
                "executed_at": "2024-01-01T12:00:00Z"
            }
        }


class OrderList(BaseModel):
    """Response model for the orders list endpoint."""
    
    orders: List[OrderItem] = Field(..., description="List of executed orders")
    
    class Config:
        json_schema_extra = {
            "example": {
                "orders": [
                    {
                        "id": "456e7890-a12b-34c5-d678-901234567890",
                        "symbol": "BTC/USD",
                        "side": "buy",
                        "quantity": 0.001,
                        "price": 98000.00,
                        "status": "executed",
                        "strategy_id": "trend_following",
                        "executed_at": "2024-01-01T12:00:00Z"
                    }
                ]
            }
        }


class PositionItem(BaseModel):
    """Position item in the positions list response."""
    
    symbol: str = Field(..., description="Trading pair symbol (e.g., ETH/USD)")
    side: str = Field(..., description="Position side: 'long' or 'short'")
    quantity: float = Field(..., description="Amount of base asset owned")
    entry_price: float = Field(..., description="Average entry price")
    entry_time: datetime = Field(..., description="UTC timestamp when position was opened")
    unrealized_pnl: float = Field(..., description="Unrealized profit/loss")
    current_price: Optional[float] = Field(None, description="Current market price (updated periodically)")
    strategy_id: Optional[str] = Field(None, description="ID of the strategy that opened this position")
    strategy_name: Optional[str] = Field(None, description="Name of the strategy that opened this position")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "symbol": "ETH/USD",
                "side": "long",
                "quantity": 0.01,
                "entry_price": 3200.00,
                "entry_time": "2024-01-01T12:00:00Z",
                "unrealized_pnl": 0.46,
                "strategy_id": "strategy-uuid",
                "strategy_name": "trend_following"
            }
        }
    }
    
    def model_dump(self, **kwargs):
        """Override model_dump() to include None values by default."""
        kwargs.setdefault('exclude_none', False)
        return super().model_dump(**kwargs)


class PositionList(BaseModel):
    """Response model for the positions list endpoint."""
    
    positions: List[PositionItem] = Field(..., description="List of open positions")
    
    class Config:
        json_schema_extra = {
            "example": {
                "positions": [
                    {
                        "symbol": "ETH/USD",
                        "side": "long",
                        "quantity": 0.01,
                        "entry_price": 3200.00,
                        "entry_time": "2024-01-01T12:00:00Z",
                        "unrealized_pnl": 0.46
                    }
                ]
            }
        }


class StrategyMetricsItem(BaseModel):
    """Performance metrics for a single strategy."""
    
    accuracy_pct: float = Field(..., description="Win rate as a percentage")
    total_pnl: float = Field(..., description="Total profit/loss for this strategy")
    win_count: int = Field(..., description="Number of winning trades")
    loss_count: int = Field(..., description="Number of losing trades")
    open_count: int = Field(..., description="Number of currently open positions")
    
    class Config:
        json_schema_extra = {
            "example": {
                "accuracy_pct": 65.5,
                "total_pnl": 1250.75,
                "win_count": 15,
                "loss_count": 8,
                "open_count": 2
            }
        }


class MetricsResponse(BaseModel):
    """Response model for the metrics endpoint."""
    
    strategies: Dict[str, StrategyMetricsItem] = Field(
        ..., description="Metrics keyed by strategy_id"
    )
    total_pnl: float = Field(..., description="Total P&L across all strategies")
    overall_accuracy_pct: float = Field(
        ..., description="Overall accuracy percentage across all strategies"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "strategies": {
                    "trend_following": {
                        "accuracy_pct": 85.5,
                        "total_pnl": 12.50,
                        "win_count": 17,
                        "loss_count": 3,
                        "open_count": 1
                    },
                    "mean_reversion": {
                        "accuracy_pct": 90.0,
                        "total_pnl": 8.25,
                        "win_count": 9,
                        "loss_count": 1,
                        "open_count": 0
                    }
                },
                "total_pnl": 20.75,
                "overall_accuracy_pct": 87.5
            }
        }


class StrategyConfigResponse(BaseModel):
    """Response model for strategy configuration."""
    
    strategy_id: str = Field(..., description="Unique strategy identifier")
    strategy_type: str = Field(..., description="Strategy type (e.g., mean_reversion, momentum)")
    parameters: Dict = Field(..., description="Strategy-specific parameters")
    filters: Dict = Field(..., description="Screening/filtering criteria")
    description: str = Field(..., description="Human-readable description of the strategy")
    volume_threshold: Optional[float] = Field(None, description="Volume threshold multiplier for screening")
    
    class Config:
        json_schema_extra = {
            "example": {
                "strategy_id": "mean_reversion",
                "strategy_type": "mean_reversion",
                "parameters": {
                    "rsi_period": 14,
                    "rsi_overbought": 70,
                    "rsi_oversold": 30,
                    "lookback_period": 20,
                    "bollinger_std": 2.0
                },
                "filters": {
                    "min_price": 0.01,
                    "max_price": 100000,
                    "min_volume_24h": 1000000
                },
                "description": "Buys when RSI < 30 and price below lower Bollinger Band"
            }
        }


class StrategyConfigUpdate(BaseModel):
    """Request model for updating strategy configuration."""
    
    parameters: Optional[Dict] = Field(None, description="Strategy parameters to update")
    filters: Optional[Dict] = Field(None, description="Screening filters to update")
    volume_threshold: Optional[float] = Field(None, description="Volume threshold multiplier for screening")
    
    class Config:
        json_schema_extra = {
            "example": {
                "parameters": {
                    "rsi_period": 14,
                    "rsi_oversold": 25
                },
                "filters": {
                    "min_volume_24h": 2000000
                }
            }
        }
