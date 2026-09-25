"""link approval requests to the automation run that proposed them

A workflow that proposes an action for a human to approve ends in RUNNING/WAITING; when the human decides,
the run must be resolved. That needs to know which run proposed which approval.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24 19:55:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: str | Sequence[str] | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('approval_requests', sa.Column('automation_run_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_approval_requests_automation_run_id', 'approval_requests', 'automation_runs', ['automation_run_id'], ['id'], ondelete='SET NULL'
    )
    op.create_index('ix_approval_requests_automation_run_id', 'approval_requests', ['automation_run_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_approval_requests_automation_run_id', table_name='approval_requests')
    op.drop_constraint('fk_approval_requests_automation_run_id', 'approval_requests', type_='foreignkey')
    op.drop_column('approval_requests', 'automation_run_id')
