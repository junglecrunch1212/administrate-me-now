"""
Reminders-family L1 event schemas — emitted by adapters in the ``reminders``
family.

Per ADMINISTRATEME_BUILD.md §L1 + ``APPLE REMINDERS BIDIRECTIONAL — DETAILED
SPEC``, SYSTEM_INVARIANTS.md §1 invariants 3 + 9 (event log append-only,
typed registry), §6 invariant 10 (reminders-family adapters hardcode
``sensitivity_floor: normal`` — the never-sync filter is the item-level
privacy boundary, distinct from the adapter-level floor; the config loader
rejects any override that would raise it to ``privileged`` because adapters
don't auto-elevate), §8 invariant 3 (no LLM SDKs in adminme/), DECISIONS.md
§D6 (no LLM SDK), §D7 (schemas register at v1), §D8 (correlation_id
propagation), §D17 (adapters never write projections — this adapter READS
from the ``reminders`` projection slice for dedupe but never writes).

This module is the reminders-family peer of
``adminme/events/schemas/adapters.py`` (cross-cutting),
``adminme/events/schemas/financial.py`` (financial-family), ``ingest.py``
(messaging / calendar / telephony / artifact), ``crm.py``, ``domain.py``,
``governance.py``. Plugin-introduced reminders event types still register
via the ``hearth.event_types`` entry point per §D9; this module covers
first-party L1 reminders adapters only.

NOTE on the ``reminder.dispatched`` vs ``reminder.list_item.synced``
distinction: ``reminder.dispatched`` (in ``domain.py``) is the
proactive-pipeline event that fires when a member's reminder feed
dispatches a new reminder downstream; ``reminder.list_item.synced`` (this
module) is the bidirectional-sync audit event emitted by the Apple
Reminders adapter when an iCloud reminder is genuinely new/modified
(``direction="inbound"``) or when AdministrateMe writes a reminder TO
iCloud (``direction="outbound"``). The two event families are distinct.

One event type registers here at v1:

- ``reminder.list_item.synced`` — emitted once per genuinely-new or
  modified reminder per sync cycle. NEVER per unchanged poll-cycle entry
  (anti-vision §"Quiet by default" — the dedupe-by-``remind_id`` predicate
  in ``packs/adapters/reminders/apple-eventkit/`` is the gate that keeps
  emission load-bearing instead of noisy). ``direction="inbound"`` for
  iCloud→event-log; ``direction="outbound"`` for event-log→iCloud writes
  with the returned ``calendarItemIdentifier`` recorded for round-trip
  traceability.

Priority follows EventKit's ordinal scale [BUILD.md APPLE REMINDERS
BIDIRECTIONAL]: ``0`` = none, ``1`` = high, ``5`` = medium, ``9`` = low.
Any other value is a constitutional violation of the EventKit contract and
the Pydantic ``Literal`` rejects it before the event reaches the log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class ReminderListItemSyncedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    remind_id: str = Field(
        min_length=1,
        description="Apple's EKReminder.calendarItemIdentifier — stable per reminder; the dedupe key.",
    )
    list_identifier: str = Field(min_length=1)
    list_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = None
    priority: Literal[0, 1, 5, 9] = Field(
        description="EventKit ordinal priority: 0=none, 1=high, 5=medium, 9=low.",
    )
    completed: bool
    last_modified_at: datetime
    direction: Literal["inbound", "outbound"] = Field(
        description=(
            "`inbound` = iCloud→event-log (poll cycle saw a new/modified reminder); "
            "`outbound` = event-log→iCloud (adapter wrote a household-tagged reminder)."
        ),
    )
    sync_cycle_id: str = Field(min_length=1)


registry.register("reminder.list_item.synced", 1, ReminderListItemSyncedV1)
