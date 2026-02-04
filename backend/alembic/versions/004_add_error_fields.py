"""Add error_type and error_message fields to orders table

Revision ID: 004_add_error_fields
Revises: 003_add_execution_mode
Create Date: 2026-01-30 00:00:00.000000

TICKET-605: Add error_type and error_message fields to orders table
to track order failures with classified error types.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '004_add_error_fields'
down_revision = '003_add_execution_mode'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TICKET-605: Add error tracking fields to orders table
    # These fields are nullable since most orders succeed
    
    # Add error_type column (nullable)
    op.add_column('orders', 
        sa.Column('error_type', sa.String(50), nullable=True)
    )
    
    # Add error_message column (nullable)
    op.add_column('orders',
        sa.Column('error_message', sa.String(500), nullable=True)
    )


def downgrade() -> None:
    # Remove columns
    op.drop_column('orders', 'error_message')
    op.drop_column('orders', 'error_type')
