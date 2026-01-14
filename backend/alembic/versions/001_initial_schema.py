"""Initial schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Strategies table
    op.create_table(
        'strategies',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(50), nullable=False, server_default='inactive'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("status IN ('active', 'inactive', 'paused')", name='strategies_status_check'),
    )
    op.create_unique_constraint('strategies_name_key', 'strategies', ['name'])

    # Signals table
    op.create_table(
        'signals',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('strategy_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('symbol', sa.String(50), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('intent_type', sa.String(20), nullable=False),
        sa.Column('notional_risk_pct', sa.Numeric(10, 4), nullable=False),
        sa.Column('metadata', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("side IN ('buy', 'sell')", name='signals_side_check'),
        sa.CheckConstraint("intent_type IN ('enter', 'exit', 'reduce')", name='signals_intent_type_check'),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'executed')", name='signals_status_check'),
    )
    op.create_foreign_key('signals_strategy_id_fkey', 'signals', 'strategies', ['strategy_id'], ['id'], ondelete='CASCADE')
    op.create_index('idx_signals_strategy_id', 'signals', ['strategy_id'])
    op.create_index('idx_signals_created_at', 'signals', ['created_at'])

    # Orders table
    op.create_table(
        'orders',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('signal_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('symbol', sa.String(50), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('executed_price', sa.Numeric(20, 8), nullable=False),
        sa.Column('quantity', sa.Numeric(20, 8), nullable=False),
        sa.Column('fees', sa.Numeric(20, 8), nullable=False, server_default='0'),
        sa.Column('slippage', sa.Numeric(20, 8), nullable=False, server_default='0'),
        sa.Column('exchange_order_id', sa.String(255), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("side IN ('buy', 'sell')", name='orders_side_check'),
        sa.CheckConstraint("status IN ('pending', 'executed', 'cancelled', 'failed')", name='orders_status_check'),
    )
    op.create_foreign_key('orders_signal_id_fkey', 'orders', 'signals', ['signal_id'], ['id'], ondelete='SET NULL')
    op.create_unique_constraint('orders_exchange_order_id_key', 'orders', ['exchange_order_id'])
    op.create_index('idx_orders_signal_id', 'orders', ['signal_id'])
    op.create_index('idx_orders_executed_at', 'orders', ['executed_at'])

    # Equity curve table
    op.create_table(
        'equity_curve',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('total_equity', sa.Numeric(20, 8), nullable=False),
        sa.Column('realized_pnl', sa.Numeric(20, 8), nullable=False, server_default='0'),
        sa.Column('unrealized_pnl', sa.Numeric(20, 8), nullable=False, server_default='0'),
        sa.Column('exposure_pct', sa.Numeric(10, 4), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index('idx_equity_curve_timestamp', 'equity_curve', ['timestamp'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_equity_curve_timestamp', table_name='equity_curve')
    op.drop_index('idx_orders_executed_at', table_name='orders')
    op.drop_index('idx_orders_signal_id', table_name='orders')
    op.drop_index('idx_signals_created_at', table_name='signals')
    op.drop_index('idx_signals_strategy_id', table_name='signals')
    
    # Drop tables (foreign keys will be dropped automatically)
    op.drop_table('equity_curve')
    op.drop_table('orders')
    op.drop_table('signals')
    op.drop_table('strategies')
