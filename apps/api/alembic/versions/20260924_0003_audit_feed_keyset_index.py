"""audit feed keyset index

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24 18:38:10.840340

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_audit_events_user_id_id', 'audit_events', ['user_id', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_audit_events_user_id_id', table_name='audit_events')
