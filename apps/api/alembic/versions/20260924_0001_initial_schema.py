"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-24 18:00:20.393281

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('system_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_system_settings'))
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=120), server_default='', nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default='UTC', nullable=False),
    sa.Column('preferences', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('token_version', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('obligations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('action', sa.String(length=500), nullable=True),
    sa.Column('source', sa.Enum('GMAIL', 'GOOGLE_CALENDAR', 'WEBHOOK', 'MANUAL', 'DEMO', name='source_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('source_reference', sa.String(length=512), nullable=True),
    sa.Column('obligation_type', sa.Enum('DEADLINE', 'PAYMENT', 'APPOINTMENT', 'INTERVIEW', 'DOCUMENT_REQUEST', 'FOLLOW_UP', 'RENEWAL', 'RETURN', 'PERSONAL_COMMITMENT', 'TASK', 'OTHER', name='obligation_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('status', sa.Enum('DETECTED', 'NEEDS_REVIEW', 'OPEN', 'ACTION_REQUIRED', 'SCHEDULED', 'COMPLETED', 'DISMISSED', 'OVERDUE', 'ESCALATED', name='obligation_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('priority', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'URGENT', name='priority', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('due_precision', sa.Enum('DATE', 'DATETIME', name='due_precision', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('due_text', sa.String(length=300), nullable=True),
    sa.Column('due_timezone', sa.String(length=64), nullable=True),
    sa.Column('due_resolution', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ambiguity', sa.Text(), nullable=True),
    sa.Column('owner', sa.String(length=200), server_default='me', nullable=False),
    sa.Column('counterparty_name', sa.String(length=200), nullable=True),
    sa.Column('counterparty_email', sa.String(length=320), nullable=True),
    sa.Column('requires_confirmation', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('recurrence', sa.Enum('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY', name='recurrence', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('entities', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_action_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_via', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status <> 'COMPLETED' OR completed_at IS NOT NULL", name=op.f('ck_obligations_completed_has_timestamp')),
    sa.CheckConstraint('(due_at IS NULL) = (due_precision IS NULL)', name=op.f('ck_obligations_due_precision_consistent')),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_obligations_confidence_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_obligations_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_obligations'))
    )
    op.create_index('ix_obligations_created_at', 'obligations', ['created_at'], unique=False)
    op.create_index('ix_obligations_due_at', 'obligations', ['due_at'], unique=False)
    op.create_index('ix_obligations_monitor', 'obligations', ['next_action_at'], unique=False, postgresql_where=sa.text("next_action_at IS NOT NULL AND status IN ('OPEN','ACTION_REQUIRED','SCHEDULED','OVERDUE','ESCALATED')"))
    op.create_index('ix_obligations_source', 'obligations', ['source'], unique=False)
    op.create_index('ix_obligations_status', 'obligations', ['status'], unique=False)
    op.create_index('ix_obligations_user_id', 'obligations', ['user_id'], unique=False)
    op.create_index('ix_obligations_user_id_created_at', 'obligations', ['user_id', 'created_at'], unique=False)
    op.create_index('ix_obligations_user_id_due_at', 'obligations', ['user_id', 'due_at'], unique=False)
    op.create_index('ix_obligations_user_id_fingerprint', 'obligations', ['user_id', 'fingerprint'], unique=False)
    op.create_index('ix_obligations_user_id_source', 'obligations', ['user_id', 'source'], unique=False)
    op.create_index('ix_obligations_user_id_status', 'obligations', ['user_id', 'status'], unique=False)
    op.create_table('approval_requests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('obligation_id', sa.Uuid(), nullable=False),
    sa.Column('action_type', sa.Enum('SEND_FOLLOW_UP', 'CREATE_CALENDAR_EVENT', 'DISMISS_OBLIGATION', 'COMPLETE_OBLIGATION', name='approval_action', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'EXECUTING', 'EXECUTED', 'FAILED', 'REJECTED', 'EXPIRED', 'CANCELLED', name='approval_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('proposed_by', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['obligation_id'], ['obligations.id'], name=op.f('fk_approval_requests_obligation_id_obligations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_approval_requests_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_approval_requests'))
    )
    op.create_index('ix_approval_requests_created_at', 'approval_requests', ['created_at'], unique=False)
    op.create_index('ix_approval_requests_obligation_id', 'approval_requests', ['obligation_id'], unique=False)
    op.create_index('ix_approval_requests_status_expires_at', 'approval_requests', ['status', 'expires_at'], unique=False)
    op.create_index('ix_approval_requests_user_id_status', 'approval_requests', ['user_id', 'status'], unique=False)
    op.create_table('automation_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('obligation_id', sa.Uuid(), nullable=True),
    sa.Column('workflow_key', sa.String(length=64), nullable=False),
    sa.Column('workflow_name', sa.String(length=200), nullable=False),
    sa.Column('n8n_workflow_id', sa.String(length=64), nullable=True),
    sa.Column('n8n_execution_id', sa.String(length=64), nullable=True),
    sa.Column('trigger', sa.String(length=32), nullable=False),
    sa.Column('status', sa.Enum('RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED', 'PARTIAL', name='run_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('attempt', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('error_node', sa.String(length=200), nullable=True),
    sa.Column('correlation_id', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['obligation_id'], ['obligations.id'], name=op.f('fk_automation_runs_obligation_id_obligations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_automation_runs_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_automation_runs'))
    )
    op.create_index('ix_automation_runs_correlation_id', 'automation_runs', ['correlation_id'], unique=False)
    op.create_index('ix_automation_runs_obligation_id', 'automation_runs', ['obligation_id'], unique=False)
    op.create_index('ix_automation_runs_status', 'automation_runs', ['status'], unique=False)
    op.create_index('ix_automation_runs_user_id_started_at', 'automation_runs', ['user_id', 'started_at'], unique=False)
    op.create_index('ix_automation_runs_workflow_key_started_at', 'automation_runs', ['workflow_key', 'started_at'], unique=False)
    op.create_index('uq_automation_runs_n8n_execution_id', 'automation_runs', ['n8n_execution_id'], unique=True, postgresql_where=sa.text('n8n_execution_id IS NOT NULL'))
    op.create_table('calendar_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('obligation_id', sa.Uuid(), nullable=True),
    sa.Column('provider', sa.Enum('GOOGLE', 'LOCAL', name='calendar_provider', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('external_id', sa.String(length=512), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default='UTC', nullable=False),
    sa.Column('location', sa.String(length=300), nullable=True),
    sa.Column('url', sa.String(length=1024), nullable=True),
    sa.Column('status', sa.Enum('CONFIRMED', 'CANCELLED', name='calendar_event_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['obligation_id'], ['obligations.id'], name=op.f('fk_calendar_events_obligation_id_obligations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_calendar_events_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_events')),
    sa.UniqueConstraint('user_id', 'provider', 'external_id', name='uq_calendar_events_user_provider_external')
    )
    op.create_index('ix_calendar_events_obligation_id', 'calendar_events', ['obligation_id'], unique=False)
    op.create_index('ix_calendar_events_user_id_start_at', 'calendar_events', ['user_id', 'start_at'], unique=False)
    op.create_table('sources',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('obligation_id', sa.Uuid(), nullable=True),
    sa.Column('source_type', sa.Enum('GMAIL', 'GOOGLE_CALENDAR', 'WEBHOOK', 'MANUAL', 'DEMO', name='source_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('external_id', sa.String(length=512), nullable=False),
    sa.Column('thread_id', sa.String(length=512), nullable=True),
    sa.Column('rfc_message_id', sa.String(length=512), nullable=True),
    sa.Column('sender_email', sa.String(length=320), nullable=True),
    sa.Column('sender_name', sa.String(length=200), nullable=True),
    sa.Column('subject', sa.String(length=500), nullable=True),
    sa.Column('excerpt', sa.Text(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('disposition', sa.Enum('OBLIGATION_CREATED', 'NEEDS_REVIEW', 'CANDIDATE', 'DUPLICATE', 'NOT_OBLIGATION', 'EXTRACTION_FAILED', 'MANUAL', name='source_disposition', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('role', sa.String(length=16), server_default='PRIMARY', nullable=False),
    sa.Column('extraction', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_synthetic', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['obligation_id'], ['obligations.id'], name=op.f('fk_sources_obligation_id_obligations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sources_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sources')),
    sa.UniqueConstraint('user_id', 'source_type', 'external_id', name='uq_sources_user_type_external')
    )
    op.create_index('ix_sources_content_hash', 'sources', ['content_hash'], unique=False)
    op.create_index('ix_sources_created_at', 'sources', ['created_at'], unique=False)
    op.create_index('ix_sources_obligation_id', 'sources', ['obligation_id'], unique=False)
    op.create_index('ix_sources_source_type', 'sources', ['source_type'], unique=False)
    op.create_index('ix_sources_user_id', 'sources', ['user_id'], unique=False)
    op.create_index('ix_sources_user_id_thread_id', 'sources', ['user_id', 'thread_id'], unique=False)
    op.create_table('audit_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('obligation_id', sa.Uuid(), nullable=True),
    sa.Column('source_id', sa.Uuid(), nullable=True),
    sa.Column('automation_run_id', sa.Uuid(), nullable=True),
    sa.Column('event_type', sa.Enum('MESSAGE_RECEIVED', 'COMMITMENT_DETECTED', 'AI_CLASSIFIED', 'EXTRACTION_FAILED', 'OBLIGATION_CREATED', 'CANDIDATE_STORED', 'CANDIDATE_PROMOTED', 'CANDIDATE_DISCARDED', 'DUPLICATE_MERGED', 'NOT_AN_OBLIGATION', 'OBLIGATION_UPDATED', 'STATUS_CHANGED', 'ACCEPTED', 'SNOOZED', 'COMPLETED', 'DISMISSED', 'REOPENED', 'OVERDUE_MARKED', 'ESCALATED', 'RECURRENCE_SPAWNED', 'NOTIFICATION_QUEUED', 'NOTIFICATION_SENT', 'NOTIFICATION_FAILED', 'NOTIFICATION_SUPPRESSED', 'REMINDERS_STOPPED', 'APPROVAL_REQUESTED', 'APPROVAL_APPROVED', 'APPROVAL_REJECTED', 'APPROVAL_EXPIRED', 'ACTION_EXECUTED', 'ACTION_FAILED', 'CALENDAR_CHECKED', 'CALENDAR_EVENT_CREATED', 'FOLLOW_UP_DRAFTED', 'AUTOMATION_RUN', 'SECURITY', 'DEMO', name='audit_event_type', native_enum=False, create_constraint=True, length=48), nullable=False),
    sa.Column('actor_type', sa.Enum('USER', 'SYSTEM', 'N8N', 'AI', name='actor_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('actor_id', sa.String(length=64), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['automation_run_id'], ['automation_runs.id'], name=op.f('fk_audit_events_automation_run_id_automation_runs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['obligation_id'], ['obligations.id'], name=op.f('fk_audit_events_obligation_id_obligations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_audit_events_source_id_sources'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_events_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    op.create_index('ix_audit_events_created_at', 'audit_events', ['created_at'], unique=False)
    op.create_index('ix_audit_events_event_type', 'audit_events', ['event_type'], unique=False)
    op.create_index('ix_audit_events_obligation_id_created_at', 'audit_events', ['obligation_id', 'created_at'], unique=False)
    op.create_index('ix_audit_events_user_id_created_at', 'audit_events', ['user_id', 'created_at'], unique=False)
    op.create_table('detection_candidates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('action', sa.String(length=500), nullable=True),
    sa.Column('obligation_type', sa.Enum('DEADLINE', 'PAYMENT', 'APPOINTMENT', 'INTERVIEW', 'DOCUMENT_REQUEST', 'FOLLOW_UP', 'RENEWAL', 'RETURN', 'PERSONAL_COMMITMENT', 'TASK', 'OTHER', name='obligation_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=False),
    sa.Column('extraction', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'PROMOTED', 'DISCARDED', name='candidate_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('promoted_obligation_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['promoted_obligation_id'], ['obligations.id'], name=op.f('fk_detection_candidates_promoted_obligation_id_obligations'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_detection_candidates_source_id_sources'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_detection_candidates_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_detection_candidates'))
    )
    op.create_index('ix_detection_candidates_created_at', 'detection_candidates', ['created_at'], unique=False)
    op.create_index('ix_detection_candidates_source_id', 'detection_candidates', ['source_id'], unique=False)
    op.create_index('ix_detection_candidates_user_id_status', 'detection_candidates', ['user_id', 'status'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('obligation_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.Enum('DETECTED', 'NEEDS_REVIEW', 'REMINDER', 'HIGH_PRIORITY_REMINDER', 'OVERDUE', 'ESCALATION', 'APPROVAL_REQUESTED', 'ACTION_RESULT', 'CALENDAR_SUGGESTION', 'FOLLOW_UP_SUGGESTION', name='notification_kind', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('channel', sa.Enum('IN_APP', 'EMAIL', 'TELEGRAM', name='notification_channel', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'SENDING', 'SENT', 'FAILED', 'CANCELLED', 'SKIPPED', name='notification_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('dedupe_key', sa.String(length=255), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('automation_run_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['automation_run_id'], ['automation_runs.id'], name=op.f('fk_notifications_automation_run_id_automation_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['obligation_id'], ['obligations.id'], name=op.f('fk_notifications_obligation_id_obligations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('user_id', 'dedupe_key', name='uq_notifications_user_dedupe')
    )
    op.create_index('ix_notifications_obligation_id', 'notifications', ['obligation_id'], unique=False)
    op.create_index('ix_notifications_status_next_attempt', 'notifications', ['status', 'next_attempt_at'], unique=False)
    op.create_index('ix_notifications_user_id_created_at', 'notifications', ['user_id', 'created_at'], unique=False)
    op.create_index('ix_notifications_user_id_status', 'notifications', ['user_id', 'status'], unique=False)

    # --- hand-written: the audit log is append-only, enforced by the database itself ---
    op.execute(
        """
        CREATE FUNCTION audit_events_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_reject_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_reject_mutation()")
    op.drop_index('ix_notifications_user_id_status', table_name='notifications')
    op.drop_index('ix_notifications_user_id_created_at', table_name='notifications')
    op.drop_index('ix_notifications_status_next_attempt', table_name='notifications')
    op.drop_index('ix_notifications_obligation_id', table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('ix_detection_candidates_user_id_status', table_name='detection_candidates')
    op.drop_index('ix_detection_candidates_source_id', table_name='detection_candidates')
    op.drop_index('ix_detection_candidates_created_at', table_name='detection_candidates')
    op.drop_table('detection_candidates')
    op.drop_index('ix_audit_events_user_id_created_at', table_name='audit_events')
    op.drop_index('ix_audit_events_obligation_id_created_at', table_name='audit_events')
    op.drop_index('ix_audit_events_event_type', table_name='audit_events')
    op.drop_index('ix_audit_events_created_at', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index('ix_sources_user_id_thread_id', table_name='sources')
    op.drop_index('ix_sources_user_id', table_name='sources')
    op.drop_index('ix_sources_source_type', table_name='sources')
    op.drop_index('ix_sources_obligation_id', table_name='sources')
    op.drop_index('ix_sources_created_at', table_name='sources')
    op.drop_index('ix_sources_content_hash', table_name='sources')
    op.drop_table('sources')
    op.drop_index('ix_calendar_events_user_id_start_at', table_name='calendar_events')
    op.drop_index('ix_calendar_events_obligation_id', table_name='calendar_events')
    op.drop_table('calendar_events')
    op.drop_index('uq_automation_runs_n8n_execution_id', table_name='automation_runs', postgresql_where=sa.text('n8n_execution_id IS NOT NULL'))
    op.drop_index('ix_automation_runs_workflow_key_started_at', table_name='automation_runs')
    op.drop_index('ix_automation_runs_user_id_started_at', table_name='automation_runs')
    op.drop_index('ix_automation_runs_status', table_name='automation_runs')
    op.drop_index('ix_automation_runs_obligation_id', table_name='automation_runs')
    op.drop_index('ix_automation_runs_correlation_id', table_name='automation_runs')
    op.drop_table('automation_runs')
    op.drop_index('ix_approval_requests_user_id_status', table_name='approval_requests')
    op.drop_index('ix_approval_requests_status_expires_at', table_name='approval_requests')
    op.drop_index('ix_approval_requests_obligation_id', table_name='approval_requests')
    op.drop_index('ix_approval_requests_created_at', table_name='approval_requests')
    op.drop_table('approval_requests')
    op.drop_index('ix_obligations_user_id_status', table_name='obligations')
    op.drop_index('ix_obligations_user_id_source', table_name='obligations')
    op.drop_index('ix_obligations_user_id_fingerprint', table_name='obligations')
    op.drop_index('ix_obligations_user_id_due_at', table_name='obligations')
    op.drop_index('ix_obligations_user_id_created_at', table_name='obligations')
    op.drop_index('ix_obligations_user_id', table_name='obligations')
    op.drop_index('ix_obligations_status', table_name='obligations')
    op.drop_index('ix_obligations_source', table_name='obligations')
    op.drop_index('ix_obligations_monitor', table_name='obligations', postgresql_where=sa.text("next_action_at IS NOT NULL AND status IN ('OPEN','ACTION_REQUIRED','SCHEDULED','OVERDUE','ESCALATED')"))
    op.drop_index('ix_obligations_due_at', table_name='obligations')
    op.drop_index('ix_obligations_created_at', table_name='obligations')
    op.drop_table('obligations')
    op.drop_table('users')
    op.drop_table('system_settings')
