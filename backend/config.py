"""Configuration management for the backend application."""

import os
from typing import Optional

# API Configuration
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))

# CORS Configuration
CORS_ORIGINS: list[str] = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8080"
).split(",")

# Logging Configuration
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# Redis Configuration
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_MAX_CONNECTIONS: int = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))

# Kraken API Configuration
KRAKEN_API_KEY: Optional[str] = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET: Optional[str] = os.getenv("KRAKEN_API_SECRET")

# Account & Risk Configuration (2% Rule)
ACCOUNT_EQUITY: float = float(os.getenv("ACCOUNT_EQUITY", "41.67"))
RISK_PCT_PER_TRADE: float = float(os.getenv("RISK_PCT_PER_TRADE", "2.0"))
STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "1.5"))  # Ross Cameron spec: 1.5% stop loss
DAILY_LOSS_LIMIT: float = float(os.getenv("DAILY_LOSS_LIMIT", "10.0"))
MAX_DRAWDOWN_PCT: float = float(os.getenv("MAX_DRAWDOWN_PCT", "3.0"))  # Ross Cameron spec: 3.0% daily equity drop kill switch

# Execution Configuration
CONFIDENCE_THRESHOLD_PCT: float = float(os.getenv("CONFIDENCE_THRESHOLD_PCT", "80.0"))  # Ross Cameron spec: 80% confidence threshold
MIN_EXECUTION_CONFIDENCE: float = float(os.getenv("MIN_EXECUTION_CONFIDENCE", "80.0"))  # Minimum confidence required for trade execution (Strategy Arbiter)
LIMIT_ORDER_OFFSET_PCT: float = float(os.getenv("LIMIT_ORDER_OFFSET_PCT", "0.02"))  # Ross Cameron spec: Ask + 0.02% for limit orders

# Opportunity Filter Configuration
OPPORTUNITY_FILTER_HOURS: float = float(os.getenv("OPPORTUNITY_FILTER_HOURS", "48.0"))

# Breakeven Guard Configuration
BREAKEVEN_GUARD_TRIGGER_PCT: float = float(os.getenv("BREAKEVEN_GUARD_TRIGGER_PCT", "1.0"))  # Legacy: +1.0% trigger when BREAKEVEN_REQUIRES_TP1=false
BREAKEVEN_REQUIRES_TP1: bool = os.getenv("BREAKEVEN_REQUIRES_TP1", "true").lower() == "true"
KRAKEN_FEE_PCT: float = float(os.getenv("KRAKEN_FEE_PCT", "0.26"))

# Minimum Hold Duration Configuration (TICKET-614)
MIN_HOLD_DURATION_SECONDS: int = int(os.getenv("MIN_HOLD_DURATION_SECONDS", "900"))  # Default: 15 minutes
