"""One-click actions from links in notification emails ("Mark as done").

The token authorises exactly one action on exactly one obligation and is redeemed with a POST
from the web app (never a GET) so mail scanners / link pre-fetchers cannot trigger it. It works
without a login on purpose - it must be usable from a phone mail client - but is signed,
expiring, and bound to a user + obligation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import enforce, get_now, limiter
from app.errors import NotFoundError
from app.models import Obligation, User
from app.ratelimit import client_ip
from app.security import decode_action_token
from app.services import obligations as svc
from app.services.audit import Actor

router = APIRouter(prefix="/api/actions", tags=["email actions"])


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=2000)


def _load(db: Session, claims: dict) -> tuple[User, Obligation]:
    user = db.get(User, uuid.UUID(claims["sub"]))
    ob = db.scalar(
        select(Obligation)
        .where(Obligation.id == uuid.UUID(claims["oid"]), Obligation.user_id == uuid.UUID(claims["sub"]))
        .with_for_update()
    )
    if user is None or not user.is_active or ob is None:
        raise NotFoundError("This link no longer points to anything")
    return user, ob


@router.post("/preview")
def preview(
    body: TokenBody, request: Request, db: Session = Depends(get_db), now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> dict:
    enforce(limiter("action-token", 30, 60), client_ip(request))
    claims = decode_action_token(body.token, now, settings)
    _, ob = _load(db, claims)
    return {
        "action": claims["act"],
        "obligation": {"id": str(ob.id), "title": ob.title, "due_at": ob.due_at, "due_precision": ob.due_precision, "status": ob.status},
    }


@router.post("/redeem")
def redeem(
    body: TokenBody, request: Request, db: Session = Depends(get_db), now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> dict:
    enforce(limiter("action-token", 30, 60), client_ip(request))
    claims = decode_action_token(body.token, now, settings)
    user, ob = _load(db, claims)
    changed, _ = svc.complete(db, user, ob, now, settings, via="EMAIL_LINK", actor=Actor.user(user))
    db.commit()
    return {"completed": True, "changed": changed, "obligation": {"id": str(ob.id), "title": ob.title, "status": ob.status}}
