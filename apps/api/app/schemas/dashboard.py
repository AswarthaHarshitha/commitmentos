from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.misc import AutomationRunOut
from app.schemas.obligation import ObligationOut


class DashboardSummary(BaseModel):
    headline: str
    subline: str | None
    tone: Literal["calm", "attention", "critical"]
    attention_count: int
    overdue: int
    due_today: int
    due_next_24h: int
    inbox_unreviewed: int
    approvals_pending: int


class DayGroup(BaseModel):
    date: dt.date
    label: str
    items: list[ObligationOut]


class TodaySection(BaseModel):
    overdue: list[ObligationOut]
    due_today: list[ObligationOut]
    appointments: list[ObligationOut]


class AutomationGlance(BaseModel):
    recent_runs: list[AutomationRunOut]
    waiting: int
    failed_24h: int


class DashboardOut(BaseModel):
    now: dt.datetime
    timezone: str
    part_of_day: Literal["morning", "afternoon", "evening"]
    display_name: str
    summary: DashboardSummary
    focus: ObligationOut | None
    today: TodaySection
    upcoming: list[DayGroup]
    recent_detections: list[ObligationOut]
    automation: AutomationGlance
    status_counts: dict[str, int]


class SystemStatus(BaseModel):
    now: dt.datetime
    email_delivery: Literal["local_test_inbox", "smtp"]
    llm_provider: str
    llm_model: str | None = None
    llm_configured: bool
    calendar_provider: str
    n8n: dict[str, Any]
    version: str
