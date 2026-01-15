"""SQLAlchemy ORM models for Omni-Bot Trading Platform."""

from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import Column, String, Numeric, DateTime, ForeignKey, CheckConstraint, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, declarative_base
import uuid

Base = declarative_base()


class Strategy(Base):
    """Strategy configuration and lifecycle state."""
    
    __tablename__ = "strategies"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False)
    config = Column(JSONB, nullable=False, default={})
    status = Column(String(50), nullable=False, default="inactive")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    signals = relationship("Signal", back_populates="strategy", cascade="all, delete-orphan")
    
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive', 'paused')", name="strategies_status_check"),
    )


class Signal(Base):
    """All generated signals (approved and rejected). Corresponds to TradeIntent."""
    
    __tablename__ = "signals"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id = Column(UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(50), nullable=False)
    side = Column(String(10), nullable=False)
    intent_type = Column(String(20), nullable=False)
    notional_risk_pct = Column(Numeric(10, 4), nullable=False)
    signal_metadata = Column(JSONB, name='metadata', nullable=False, default={})
    status = Column(String(50), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    
    # Relationships
    strategy = relationship("Strategy", back_populates="signals")
    orders = relationship("Order", back_populates="signal")
    
    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="signals_side_check"),
        CheckConstraint("intent_type IN ('enter', 'exit', 'reduce')", name="signals_intent_type_check"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'executed')", name="signals_status_check"),
        Index("idx_signals_strategy_id", "strategy_id"),
        Index("idx_signals_created_at", "created_at"),
    )


class Order(Base):
    """Executed orders with fees, slippage, exchange IDs. Corresponds to Fill."""
    
    __tablename__ = "orders"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    signal_id = Column(UUID(as_uuid=True), ForeignKey("signals.id", ondelete="SET NULL"), nullable=True)
    symbol = Column(String(50), nullable=False)
    side = Column(String(10), nullable=False)
    executed_price = Column(Numeric(20, 8), nullable=False)
    quantity = Column(Numeric(20, 8), nullable=False)
    fees = Column(Numeric(20, 8), nullable=False, default=0)
    slippage = Column(Numeric(20, 8), nullable=False, default=0)
    exchange_order_id = Column(String(255), unique=True, nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    signal = relationship("Signal", back_populates="orders")
    
    __table_args__ = (
        CheckConstraint("side IN ('buy', 'sell')", name="orders_side_check"),
        CheckConstraint("status IN ('pending', 'executed', 'cancelled', 'failed')", name="orders_status_check"),
        Index("idx_orders_signal_id", "signal_id"),
        Index("idx_orders_executed_at", "executed_at"),
    )


class EquityCurve(Base):
    """Portfolio snapshots every 15 minutes."""
    
    __tablename__ = "equity_curve"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    total_equity = Column(Numeric(20, 8), nullable=False)
    realized_pnl = Column(Numeric(20, 8), nullable=False, default=0)
    unrealized_pnl = Column(Numeric(20, 8), nullable=False, default=0)
    exposure_pct = Column(Numeric(10, 4), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_equity_curve_timestamp", "timestamp"),
    )
