"""Import every model so ``Base.metadata`` (and Alembic autogenerate) sees all tables."""

from app.models.approval import ApprovalRequest
from app.models.audit import AuditEvent
from app.models.automation import AutomationRun, SystemSetting
from app.models.base import Base
from app.models.calendar_event import CalendarEvent
from app.models.notification import Notification
from app.models.obligation import Obligation
from app.models.source import DetectionCandidate, Source
from app.models.user import User

__all__ = [
    "ApprovalRequest",
    "AuditEvent",
    "AutomationRun",
    "Base",
    "CalendarEvent",
    "DetectionCandidate",
    "Notification",
    "Obligation",
    "Source",
    "SystemSetting",
    "User",
]
