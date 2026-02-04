"""Add positions table with strategy ownership

Revision ID: 002_add_positions_table
Revises: 001_initial_schema
Create Date: 2026-01-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002_add_positions_table'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Positions table - tracks open positions with strategy ownership
    op.create_table(
        'positions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('symbol', sa.String(50), nullable=False),
        sa.Column('side', sa.String(10), nullable=False),
        sa.Column('quantity', sa.Numeric(20, 8), nullable=False),
        sa.Column('entry_price', sa.Numeric(20, 8), nullable=False),
        sa.Column('entry_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('unrealized_pnl', sa.Numeric(20, 8), nullable=False, server_default='0'),
        sa.Column('opened_by_strategy_id', sa.String(50), nullable=True),  # NULL for legacy/manual trades
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("side IN ('long', 'short')", name='positions_side_check'),
    )
    
    # Unique constraint on symbol (one position per symbol)
    op.create_unique_constraint('positions_symbol_key', 'positions', ['symbol'])
    
    # Index for strategy ownership queries
    op.create_index('idx_positions_opened_by_strategy_id', 'positions', ['opened_by_strategy_id'])


def downgrade() -> None:
    op.drop_index('idx_positions_opened_by_strategy_id', table_name='positions')
    op.drop_table('positions')
