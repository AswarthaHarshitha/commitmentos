from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import ObligationStatus, SourceDisposition


class ImportEmail(BaseModel):
    """An email a person pastes in themselves. It goes through exactly the path a Gmail message would."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=100_000)
    subject: str | None = Field(default=None, max_length=500)
    sender: str | None = Field(default=None, max_length=400, description='"Name <address>" or just an address')
    received_at: dt.datetime | None = Field(
        default=None, description="When it arrived; relative words like 'tomorrow' are read from here. Defaults to now."
    )

    @field_validator("received_at")
    @classmethod
    def _aware(cls, v: dt.datetime | None) -> dt.datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("received_at must include a UTC offset (e.g. 2026-09-25T09:30:00Z)")
        return v


class ImportOutcome(BaseModel):
    """Where an imported email has got to. `processing` until the detection workflow has reported back."""

    external_id: str
    status: Literal["processing", "done", "failed"]
    requested_at: dt.datetime | None = None
    disposition: SourceDisposition | None = None
    obligation_id: uuid.UUID | None = None
    obligation_status: ObligationStatus | None = None
    title: str | None = None
    detail: str | None = None
