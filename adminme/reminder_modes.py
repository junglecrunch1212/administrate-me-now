"""Deterministic reminder-mode language and identity; no executor or scheduler."""

from __future__ import annotations

import json
from typing import Any

from adminme.events.schemas.reminder_modes import ReminderModeOrigin

MODE_OFFERED = "cos.reminder.mode_offered"
MODE_PRESENTED = "cos.reminder.mode_presented"
MODE_RULE = "current-human-request.cos-reminder-mode-choice.v1"


def mode_origin(provenance: Any) -> ReminderModeOrigin:
    values = provenance if isinstance(provenance, dict) else provenance.model_dump()
    return ReminderModeOrigin.model_validate(
        {name: values.get(name) for name in ReminderModeOrigin.model_fields}
    )


def mode_prompt(title: str, due_at: str, notes: str | None) -> str:
    """Fixed ordinals and wording, with quoted content clearly separated."""
    text = (
        "Choose how I should handle this request. The reminder would be in my "
        "authorized Apple Reminders work list.\n"
        f"Title: {json.dumps(title, ensure_ascii=False)}\nDue: {due_at}\n"
        f"Notes: {json.dumps(notes, ensure_ascii=False)}\n\n"
        "1. Create only this reminder. Do not send or schedule an email.\n"
        "2. I want an automatic email instead. Keep that workflow unresolved; "
        "this choice does not schedule it or create the reminder.\n\n"
        "Reply 1 or 2."
    )
    # Match the existing native final-reply transport normalization.
    return " ".join(text.split())


def mode_answer(text: str) -> str | None:
    return {"1": "reminder_only", "2": "email_unresolved"}.get(text.strip())
