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
    ReminderChoiceSelectionV1,
    ReminderChoiceCandidateV1,
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


def selected_task(
    snapshot: ReminderChoiceSelectionV1, authored_answer: str
) -> tuple[ReminderChoiceCandidateV1, str] | None:
    """Select using authored words or a genuine thread, never a tool argument.

    An exact ID/title prefix followed by a colon is explicit. Duplicate titles
    remain ambiguous even across threads. Without a prefix, one pending offer
    in the authenticated email thread wins; otherwise exactly one overall does.
    The returned remainder is still literal member-authored text.
    """
    answer = authored_answer.strip()
    references = []
    for candidate in snapshot.eligible_tasks:
        prefix = candidate.task_id + ":"
        if answer.startswith(prefix):
            references.append((candidate, answer[len(prefix) :].strip()))
    if references:
        return references[0] if len(references) == 1 else None
    # The snapshot carries hashes instead of copying other tasks' private
    # titles into a new source/session. Only a literal human prefix can match.
    prefixes = [
        (digest(answer[:index].strip().lower()), answer[index + 1 :].strip())
        for index, character in enumerate(answer)
        if character == ":"
    ]
    references = [
        (candidate, remainder)
        for candidate in snapshot.eligible_tasks
        for fingerprint, remainder in prefixes
        if fingerprint == candidate.title_fingerprint
    ]
    if references:
        return references[0] if len(references) == 1 else None
    candidates = snapshot.eligible_tasks
    if snapshot.answer_conversation is not None:
        same_thread = [
            candidate
            for candidate in candidates
            if candidate.conversation == snapshot.answer_conversation
        ]
        if same_thread:
            candidates = same_thread
    return (candidates[0], answer) if len(candidates) == 1 else None
