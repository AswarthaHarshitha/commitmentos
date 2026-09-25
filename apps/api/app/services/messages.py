"""Notification content. Plain text and a small HTML email are both derived from one structure,
so the wording lives in exactly one place and is easy to test."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.enums import NotificationKind, SourceType
from app.models import Obligation
from app.security import create_action_token
from app.services.timeutil import fmt_span, fmt_when


@dataclass
class Content:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    buttons: list[dict[str, str]] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {"paragraphs": self.paragraphs, "buttons": self.buttons}


_SOURCE_LABEL = {
    SourceType.GMAIL: "an email",
    SourceType.GOOGLE_CALENDAR: "your calendar",
    SourceType.WEBHOOK: "an incoming message",
    SourceType.MANUAL: "your notes",
    SourceType.IMPORTED: "an email you pasted in",
}


LOCAL_INBOX_NOTE = "into the local test inbox, not to a real mailbox"


_CHANNEL_PHRASE = {"in_app": "in the app", "email": "by email", "telegram": "on Telegram"}

_KIND_PHRASE = {
    NotificationKind.DETECTED: "New-commitment notice",
    NotificationKind.NEEDS_REVIEW: "Review request",
    NotificationKind.REMINDER: "Reminder",
    NotificationKind.HIGH_PRIORITY_REMINDER: "Final reminder",
    NotificationKind.OVERDUE: "Overdue notice",
    NotificationKind.ESCALATION: "Escalation notice",
    NotificationKind.APPROVAL_REQUESTED: "Approval request",
    NotificationKind.ACTION_RESULT: "Action result",
    NotificationKind.CALENDAR_SUGGESTION: "Calendar suggestion",
    NotificationKind.FOLLOW_UP_SUGGESTION: "Follow-up suggestion",
}


def channels_phrase(channels: list[str]) -> str:
    """['in_app', 'email'] -> 'in the app and by email'."""
    words = [_CHANNEL_PHRASE.get(c.lower(), c.lower()) for c in channels]
    return " and ".join([", ".join(words[:-1]), words[-1]] if len(words) > 2 else words)


def kind_phrase(kind: NotificationKind) -> str:
    return _KIND_PHRASE.get(kind, kind.value.replace("_", " ").capitalize())


def _links(ob: Obligation, settings: Settings, now: datetime, complete_button: bool) -> list[dict[str, str]]:
    base = settings.public_web_url.rstrip("/")
    buttons = [{"label": "Open in CommitmentOS", "url": f"{base}/obligations/{ob.id}"}]
    if complete_button:
        token = create_action_token(ob.user_id, ob.id, "complete", now, settings)
        buttons.insert(0, {"label": "Mark as done", "url": f"{base}/a/{token}"})
    return buttons


def build(
    kind: NotificationKind, ob: Obligation, tz: ZoneInfo, now: datetime, settings: Settings
) -> Content:
    when = fmt_when(ob.due_at, ob.due_precision, tz) if ob.due_at else None
    action = (ob.action or "").strip()
    what = action if len(action.split()) >= 2 else ob.title  # a one-word "action" (a weak extraction) reads badly: use the title

    if kind == NotificationKind.DETECTED:
        parts = [f"Found in {_SOURCE_LABEL.get(ob.source, 'a message')}: {what}."]
        if when:
            parts.append(f"Due {when}.")
        parts.append(f"Confidence {round(ob.confidence * 100)}%. It is now tracked and reminders are scheduled.")
        return Content(f"New commitment: {ob.title}", parts, _links(ob, settings, now, True))

    if kind == NotificationKind.NEEDS_REVIEW:
        parts = [f"Possible commitment found in {_SOURCE_LABEL.get(ob.source, 'a message')}: {what}."]
        if when:
            parts.append(f"Due {when} (please check).")
        if ob.ambiguity:
            parts.append(f"Why review: {ob.ambiguity}")
        parts.append("Nothing will remind you until you accept it.")
        return Content(f"Please review: {ob.title}", parts, _links(ob, settings, now, False))

    if kind in (NotificationKind.REMINDER, NotificationKind.HIGH_PRIORITY_REMINDER) and ob.due_at:
        remaining = fmt_span(ob.due_at - now)
        urgent = kind == NotificationKind.HIGH_PRIORITY_REMINDER
        return Content(
            f"{'Due soon' if urgent else 'Reminder'} - in {remaining}: {ob.title}",
            [f"{what}", f"Due {when} (in about {remaining})."],
            _links(ob, settings, now, True),
        )

    if kind == NotificationKind.OVERDUE and ob.due_at:
        return Content(
            f"Overdue: {ob.title}",
            [f"{what}", f"This was due {when} - about {fmt_span(now - ob.due_at)} ago and is not marked done."],
            _links(ob, settings, now, True),
        )

    if kind == NotificationKind.ESCALATION and ob.due_at:
        return Content(
            f"Still unresolved: {ob.title}",
            [f"{what}", f"Overdue for about {fmt_span(now - ob.due_at)} (was due {when}). This is the final reminder."],
            _links(ob, settings, now, True),
        )

    return Content(ob.title, [what], _links(ob, settings, now, False))


def to_text(content: Content) -> str:
    lines = [content.title, "", *content.paragraphs]
    if content.buttons:
        lines.append("")
        lines.extend(f"{b['label']}: {b['url']}" for b in content.buttons)
    return "\n".join(lines)


def to_html(content: Content) -> str:
    paras = "".join(
        f'<p style="margin:0 0 12px;font-size:15px;line-height:1.5;color:#3d362e">{html.escape(p)}</p>'
        for p in content.paragraphs
    )
    buttons = "".join(
        f'<a href="{html.escape(b["url"], quote=True)}" style="display:inline-block;margin:6px 8px 0 0;'
        f'padding:9px 14px;border-radius:6px;font-size:14px;text-decoration:none;'
        f'{"background:#2f5d50;color:#fff" if i == 0 else "background:#efe9df;color:#3d362e"}">'
        f"{html.escape(b['label'])}</a>"
        for i, b in enumerate(content.buttons)
    )
    return (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:#faf8f5;padding:24px">'
        '<div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #e7e2da;border-radius:10px;padding:24px">'
        f'<h2 style="margin:0 0 14px;font-size:18px;color:#1f1b16">{html.escape(content.title)}</h2>'
        f"{paras}<div>{buttons}</div>"
        '<p style="margin:18px 0 0;font-size:12px;color:#8a8073">CommitmentOS</p></div></div>'
    )
