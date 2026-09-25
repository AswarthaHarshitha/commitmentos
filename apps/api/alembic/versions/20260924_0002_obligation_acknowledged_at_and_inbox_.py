"""obligation acknowledged_at and inbox index

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24 18:13:47.341810

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('obligations', sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_obligations_inbox', 'obligations', ['user_id', 'created_at'], unique=False, postgresql_where=sa.text('acknowledged_at IS NULL'))

    # hand-written: RunStatus gained WAITING. Enum CHECK constraints are not diffed by autogenerate.
    op.drop_constraint(op.f('ck_automation_runs_run_status'), 'automation_runs', type_='check')
    op.create_check_constraint(
        op.f('ck_automation_runs_run_status'),
        'automation_runs',
        "status IN ('RUNNING','WAITING','SUCCESS','FAILED','SKIPPED','PARTIAL')",
    )


def downgrade() -> None:
    op.execute("UPDATE automation_runs SET status = 'RUNNING' WHERE status = 'WAITING'")
    op.drop_constraint(op.f('ck_automation_runs_run_status'), 'automation_runs', type_='check')
    op.create_check_constraint(
        op.f('ck_automation_runs_run_status'),
        'automation_runs',
        "status IN ('RUNNING','SUCCESS','FAILED','SKIPPED','PARTIAL')",
    )
    op.drop_index('ix_obligations_inbox', table_name='obligations', postgresql_where=sa.text('acknowledged_at IS NULL'))
    op.drop_column('obligations', 'acknowledged_at')
