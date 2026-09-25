from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import (
    CandidateStatus,
    ObligationType,
    SourceDisposition,
    SourceType,
)
from app.models.base import Base, TimestampMixin, enum_col, new_uuid
from app.models.obligation import Obligation


class Source(TimestampMixin, Base):
    """One external message/event that was processed.

    Doubles as the idempotency ledger: (user_id, source_type, external_id) is unique, so the
    same Gmail message or a re-delivered webhook can never be processed twice. Several sources
    can point at one obligation (original mail, reminder mail, forward, calendar entry).

    Privacy: for NOT_OBLIGATION messages only ids/hashes are kept - no subject, no excerpt.
    """

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("obligations.id", ondelete="CASCADE"), default=None
    )
    source_type: Mapped[SourceType] = mapped_column(enum_col(SourceType, "source_type"))
    external_id: Mapped[str] = mapped_column(String(512))
    thread_id: Mapped[str | None] = mapped_column(String(512), default=None)
    rfc_message_id: Mapped[str | None] = mapped_column(String(512), default=None)
    sender_email: Mapped[str | None] = mapped_column(String(320), default=None)
    sender_name: Mapped[str | None] = mapped_column(String(200), default=None)
    subject: Mapped[str | None] = mapped_column(String(500), default=None)
    excerpt: Mapped[str | None] = mapped_column(Text, default=None)
    received_at: Mapped[datetime | None] = mapped_column(default=None)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    disposition: Mapped[SourceDisposition] = mapped_column(enum_col(SourceDisposition, "source_disposition"))
    role: Mapped[str] = mapped_column(String(16), default="PRIMARY", server_default="PRIMARY")
    extraction: Mapped[dict[str, Any] | None] = mapped_column(default=None)

    obligation: Mapped[Obligation | None] = relationship(Obligation, back_populates="sources")

    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "external_id", name="uq_sources_user_type_external"),
        Index("ix_sources_user_id", "user_id"),
        Index("ix_sources_obligation_id", "obligation_id"),
        Index("ix_sources_user_id_thread_id", "user_id", "thread_id"),
        Index("ix_sources_content_hash", "content_hash"),
        Index("ix_sources_source_type", "source_type"),
        Index("ix_sources_created_at", "created_at"),
    )


class DetectionCandidate(TimestampMixin, Base):
    """LOW-confidence detection: kept for review, but not an active obligation."""

    __tablename__ = "detection_candidates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    action: Mapped[str | None] = mapped_column(String(500), default=None)
    obligation_type: Mapped[ObligationType] = mapped_column(
        enum_col(ObligationType, "obligation_type"), default=ObligationType.OTHER
    )
    confidence: Mapped[float] = mapped_column(default=0.0)
    reason: Mapped[str] = mapped_column(String(64), default="LOW_CONFIDENCE")
    extraction: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    status: Mapped[CandidateStatus] = mapped_column(
        enum_col(CandidateStatus, "candidate_status"), default=CandidateStatus.PENDING
    )
    promoted_obligation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("obligations.id", ondelete="SET NULL"), default=None
    )

    __table_args__ = (
        Index("ix_detection_candidates_user_id_status", "user_id", "status"),
        Index("ix_detection_candidates_source_id", "source_id"),
        Index("ix_detection_candidates_created_at", "created_at"),
    )
