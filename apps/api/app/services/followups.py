"""Follow-up email drafts (Follow-up Assistant): who needs a nudge, and what the draft says.

Deliberately template-based, not LLM-generated: a draft is text the user will send under their own
name, so it must be predictable and must never assert something the user has not done ("I have
sent the documents"). It is always shown to the user for review/editing before anything is sent.

Which commitments qualify is a rule set, not a model:
  * something *someone else* promised us that is overdue (or, with no date, has been open for 3 days), or
  * something we owe that is overdue/escalated and has a counterparty to update,
  and only if a real counterparty address is known (never the user's own) and no follow-up was proposed for it
  in the last 72 hours - so a scan can run as often as it likes without nagging.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ACTIVE_STATUSES, ApprovalAction
from app.enums import ObligationStatus as S
from app.models import ApprovalRequest, Obligation, Source, User
from app.services.messages import fmt_when
from app.services.timeutil import get_zone

COOLDOWN = timedelta(hours=72)
STALE_WAITING = timedelta(days=3)


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0] if name and name.strip() else ""


def draft_follow_up(ob: Obligation, user: User, now: datetime, tz: ZoneInfo) -> dict[str, str]:
    recipient = _first_name(ob.counterparty_name)
    greeting = f"Hi {recipient}," if recipient else "Hi,"
    when = fmt_when(ob.due_at, ob.due_precision, tz) if ob.due_at else None
    sender = user.display_name.strip() or user.email.split("@")[0]
    overdue = ob.status in (S.OVERDUE, S.ESCALATED)

    if ob.owner != "me":  # we are waiting on them
        subject = f"Checking in: {ob.title}"
        lines = [greeting, "", f"Just checking in on \"{ob.title}\"" + (f" (expected {when})." if when else "."),
                 "Could you let me know where it stands?", "", "Thanks,", sender]
    elif ob.requires_confirmation:
        subject = f"Re: {ob.title}"
        lines = [greeting, "", f"Thanks for your message. I'm writing to confirm regarding \"{ob.title}\"."]
        if when:
            lines.append(f"As I understand it, this is for {when}.")
        lines += ["Please let me know if anything else is needed on my side.", "", "Best regards,", sender]
    else:
        subject = f"Following up: {ob.title}"
        lines = [greeting, "", f"Just following up regarding \"{ob.title}\"" + (f" (due {when})." if when else ".")]
        if overdue:
            lines.append("I wanted to check in on where this stands.")
        lines += ["Could you let me know if there is anything else you need from me?", "", "Thanks,", sender]
    return {"subject": subject, "body": "\n".join(lines)}


@dataclass(frozen=True)
class FollowUpProposal:
    obligation_id: str
    user_email: str
    title: str
    rationale: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "user_email": self.user_email,
            "action_type": ApprovalAction.SEND_FOLLOW_UP.value,
            "title": self.title,
            "rationale": self.rationale,
            "payload": self.payload,
        }


def scan(db: Session, now: datetime, settings: Settings, limit: int = 25) -> list[FollowUpProposal]:
    recently_proposed = exists().where(
        ApprovalRequest.obligation_id == Obligation.id,
        ApprovalRequest.action_type == ApprovalAction.SEND_FOLLOW_UP,
        ApprovalRequest.created_at > now - COOLDOWN,
    )
    waiting_on_them = and_(
        Obligation.owner != "me",
        Obligation.status.in_(ACTIVE_STATUSES),
        or_(Obligation.due_at < now, and_(Obligation.due_at.is_(None), Obligation.created_at < now - STALE_WAITING)),
    )
    overdue_of_ours = and_(Obligation.owner == "me", Obligation.status.in_([S.OVERDUE, S.ESCALATED]))
    rows = db.execute(
        select(Obligation, User)
        .join(User, User.id == Obligation.user_id)
        .where(
            Obligation.counterparty_email.is_not(None),
            func.lower(Obligation.counterparty_email) != func.lower(User.email),
            or_(waiting_on_them, overdue_of_ours),
            ~recently_proposed,
        )
        .order_by(func.coalesce(Obligation.due_at, Obligation.created_at))
        .limit(limit)
    ).all()

    proposals: list[FollowUpProposal] = []
    for ob, user in rows:
        tz = get_zone(user.timezone, settings.default_timezone)
        draft = draft_follow_up(ob, user, now, tz)
        origin = db.scalar(select(Source).where(Source.obligation_id == ob.id).order_by(Source.created_at).limit(1))
        who = ob.counterparty_name or ob.counterparty_email
        if ob.owner != "me":
            expected = f" ({fmt_when(ob.due_at, ob.due_precision, tz)})" if ob.due_at else ""
            rationale = f"{ob.owner} was expected to deliver \"{ob.title}\"{expected}. A short check-in may help. It is only sent if you approve."
        else:
            rationale = f"\"{ob.title}\" is {ob.status.value.lower()}. A quick note to {who} may help. It is only sent if you approve."
        payload: dict[str, Any] = {"to": ob.counterparty_email, **draft}
        if origin is not None:
            payload["thread_id"], payload["in_reply_to"] = origin.thread_id, origin.rfc_message_id
        proposals.append(
            FollowUpProposal(
                obligation_id=str(ob.id),
                user_email=user.email,
                title=f"Send a follow-up to {who}"[:300],
                rationale=rationale,
                payload={k: v for k, v in payload.items() if v is not None},
            )
        )
    return proposals
