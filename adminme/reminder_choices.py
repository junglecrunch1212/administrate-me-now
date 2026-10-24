"""Member-owned reminder choices reuse durable work and existing provider receipts.

A pending choice is a responsibility, not an approval. Only a fresh authentic
member answer, rechecked current authority, semantic grounding of both sources,
and an independent host decision can later create a provider command.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Any

from fastapi import HTTPException

from adminme.events.schemas.reminder_choices import (
    ReminderChoiceOfferedV1,
    ReminderListChoice,
)

OFFERED = "cos.reminder.choice_offered"
FINISHED = "cos.reminder.choice_finished"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def choice_task_id(tenant_id: str, member_id: str, action_id: str) -> str:
    return "cos-reminder-choice:" + digest([tenant_id, member_id, action_id])[:32]


def selected_choice(
    offered: ReminderChoiceOfferedV1,
    authored_answer: str,
    *,
    provider_account_id: str,
    current_choices: list[ReminderListChoice],
    now: datetime,
) -> ReminderListChoice:
    """Resolve an exact offered label/ID, never infer account or revive expiry."""
    if now >= offered.pending_expires_at:
        raise HTTPException(409, "This pending reminder choice has expired")
    if provider_account_id != offered.provider_account_id:
        raise HTTPException(409, "The original reminder account is no longer current")
    answer = authored_answer.strip().casefold()
    if not answer:
        raise HTTPException(409, "An exact offered list choice is required")
    candidates = [
        choice
        for choice in offered.choices
        if answer in {choice.list_id.casefold(), choice.label.strip().casefold()}
    ]
    if answer in {"yes", "yes please"}:
        candidates = list(offered.choices) if len(offered.choices) == 1 else []
    if len(candidates) != 1:
        raise HTTPException(409, "The answer does not uniquely select an offered list")
    selected = candidates[0]
    if not any(choice == selected for choice in current_choices):
        raise HTTPException(
            409, "The offered reminder list changed or is no longer authorized"
        )
    return selected


def execution_expiry(
    offered: ReminderChoiceOfferedV1, *, issued_at: datetime
) -> datetime:
    """A first execution ticket is short lived; pending work is not that ticket.

    Call only when creating a new grounding receipt. An existing receipt always
    retains its recorded issued_at and expires_at, including after restart.
    """
    if issued_at >= offered.pending_expires_at:
        raise HTTPException(409, "This pending reminder choice has expired")
    return min(issued_at + timedelta(minutes=10), offered.pending_expires_at)
