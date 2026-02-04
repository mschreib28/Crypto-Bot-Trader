"""Add is_live and execution_mode fields to orders table

Revision ID: 003_add_execution_mode
Revises: 002_add_positions_table
Create Date: 2026-01-30 00:00:00.000000

TICKET-603: Add is_live (Boolean) and execution_mode (String) fields to orders table
to distinguish live trades from shadow trades.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '003_add_execution_mode'
down_revision = '002_add_positions_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TICKET-603: Add is_live and execution_mode columns to orders table
    # Default values ensure backward compatibility with existing orders
    
    # Add is_live column (default: TRUE for existing orders)
    op.add_column('orders', 
        sa.Column('is_live', sa.Boolean(), nullable=False, server_default='true')
    )
    
    # Add execution_mode column (default: 'live' for existing orders)
    op.add_column('orders',
        sa.Column('execution_mode', sa.String(20), nullable=False, server_default='live')
    )
    
    # Add constraint: execution_mode must be 'shadow' or 'live'
    op.create_check_constraint(
        'orders_execution_mode_check',
        'orders',
        "execution_mode IN ('shadow', 'live')"
    )


def downgrade() -> None:
    # Remove constraint
    op.drop_constraint('orders_execution_mode_check', 'orders', type_='check')
    
    # Remove columns
    op.drop_column('orders', 'execution_mode')
    op.drop_column('orders', 'is_live')
