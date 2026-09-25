"""Fire-and-record dispatch of an approved action to n8n.

Runs *after* the request's transaction has committed (so n8n never sees uncommitted state). If n8n
cannot be reached the action is NOT lost: it stays APPROVED in the database and the scheduled
n8n dispatcher claims it on its next run. The failure is written to the automation history so it
is visible to the user, not swallowed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.clock import clock
from app.db import get_sessionmaker
from app.services import automation
from app.services.n8n_client import N8nClient

log = logging.getLogger("commitmentos.dispatch")

ACTION_WEBHOOK = "commitmentos-action"


def dispatch_approval(approval_id: uuid.UUID, user_id: uuid.UUID, n8n: N8nClient) -> None:
    payload: dict[str, Any] = {"approval_id": str(approval_id)}
    result = n8n.trigger(ACTION_WEBHOOK, payload)
    if result.ok:
        return
    with get_sessionmaker()() as db:
        automation.record_backend_failure(
            db,
            workflow_key="api-dispatch",
            now=clock.now(),
            user_id=user_id,
            error=(
                f"Could not reach n8n ({result.error}). The approved action stays queued and the "
                "scheduled dispatcher will pick it up when n8n is back."
            ),
            correlation_id=str(approval_id),
        )
        db.commit()
