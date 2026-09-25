from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user, get_now
from app.enums import ApprovalStatus
from app.models import ApprovalRequest, User
from app.schemas.misc import ApprovalOut, ApprovalPatch
from app.services import approvals
from app.services.dispatch import dispatch_approval
from app.services.n8n_client import N8nClient, get_n8n_client

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    status: list[ApprovalStatus] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ApprovalOut]:
    stmt = select(ApprovalRequest).where(ApprovalRequest.user_id == user.id)
    if status:
        stmt = stmt.where(ApprovalRequest.status.in_(status))
    rows = db.scalars(stmt.order_by(ApprovalRequest.created_at.desc()).limit(limit)).all()
    return [ApprovalOut.model_validate(a) for a in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ApprovalOut:
    return ApprovalOut.model_validate(approvals.get_owned(db, user, approval_id))


@router.patch("/{approval_id}", response_model=ApprovalOut)
def edit_approval(
    approval_id: uuid.UUID,
    body: ApprovalPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> ApprovalOut:
    approval = approvals.get_owned(db, user, approval_id, lock=True)
    approvals.edit(db, user, approval, body, now)
    db.commit()
    return ApprovalOut.model_validate(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
def approve_action(
    approval_id: uuid.UUID,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
    n8n: N8nClient = Depends(get_n8n_client),
) -> ApprovalOut:
    approval = approvals.get_owned(db, user, approval_id, lock=True)
    approval, needs_n8n = approvals.approve(db, user, approval, now, settings)
    db.commit()
    if needs_n8n:
        background.add_task(dispatch_approval, approval.id, user.id, n8n)
    return ApprovalOut.model_validate(approval)


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
def reject_action(
    approval_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> ApprovalOut:
    approval = approvals.get_owned(db, user, approval_id, lock=True)
    approvals.reject(db, user, approval, now)
    db.commit()
    return ApprovalOut.model_validate(approval)
