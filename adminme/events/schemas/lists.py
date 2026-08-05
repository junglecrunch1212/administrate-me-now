"""
List-item canonical event family — emitted by adapters that own list-style
external state (Apple Reminders today; Apple Notes checklists + Google Tasks
later per [D18]).

Per ADMINISTRATEME_BUILD.md §L1, SYSTEM_INVARIANTS.md §1 invariants 3 + 9
(event log append-only, typed registry), §6.20 (observation wrap unchanged),
§7.3 + §8.2 (adapters never write projections; no LLM SDKs in adminme/),
DECISIONS.md §D6 (no LLM SDK), §D7 (schemas immutable at v1 — this module
ADDS new v1 schemas; the legacy ``reminder.list_item.synced@v1`` schema in
``reminders.py`` is unchanged and continues to dual-emit during the migration
window), §D8 (causation_id / correlation_id propagation across the dual-emit
pair), §D17 (adapters never write projections — the ``lists`` projection in
prompt 11f consumes these events), §D18 (Reminders + Tasks + Notes-checklists
emit the ``list_item.*`` canonical taxonomy — this module closes that
obligation for Reminders; the other two families register here in follow-up
prompts).

This module is the list-item peer of ``adminme/events/schemas/reminders.py``
(legacy ``reminder.list_item.synced@v1``), ``ingest.py`` (calendar / messaging
/ artifact ingest events), ``crm.py``, ``domain.py``, ``governance.py``.
Plugin-introduced list-item event types still register via the
``hearth.event_types`` entry point per §D9; this module covers first-party
L1 list-item taxonomy only.

Four event types register here at v1:

- ``list_item.added`` — emitted when a new list item appears on the external
  source (inbound, first observation in a sync cycle) OR when AdministrateMe
  writes a new item to the external source (outbound).
- ``list_item.updated`` — emitted on subsequent observations where one or
  more non-completion fields changed; ``field_updates`` carries the changed
  fields per the ``CalendarEventUpdatedV1`` pattern in ``ingest.py``.
- ``list_item.completed`` — emitted on the completion-flip transition
  (``completed: false → true``). ``completed`` is constrained to ``True`` to
  make the kind self-describing for downstream consumers.
- ``list_item.deleted`` — emitted when a list item is removed; carries only
  the minimal identifier set + deletion timestamp since the source body is
  already gone. The Apple Reminders adapter (per prompt 11b-i §1 item 3 +
  §6) emits this from the outbound write-error / cancellation path only;
  inbound-deletion derivation requires negative-set logic across sync cycles
  and ships in a follow-up.

The field set MIRRORS ``ReminderListItemSyncedV1`` for migrability — a
downstream consumer subscribing to either family receives the same logical
record, modulo the renames (``list_identifier → list_external_id``,
``remind_id → item_external_id``) that align with the broader ``list_item.*``
family naming per memo 05 §5.3. The ``direction`` field from the legacy
schema is dropped because direction is now encoded by the source emitter
context, not the payload (parent draft §1).

Priority follows EventKit's ordinal scale [BUILD.md APPLE REMINDERS
BIDIRECTIONAL]: ``0`` = none, ``1`` = high, ``5`` = medium, ``9`` = low.
Any other value is rejected by Pydantic ``Literal`` before the event reaches
the log. Other list-item families that map onto this taxonomy (Tasks, Notes)
will adapt their native priorities into this ordinal scale at their adapter
boundary in the follow-up prompts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class ListItemAddedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    list_external_id: str = Field(min_length=1)
    item_external_id: str = Field(min_length=1)
    list_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = None
    priority: Literal[0, 1, 5, 9] = Field(
        description="EventKit ordinal priority: 0=none, 1=high, 5=medium, 9=low.",
    )
    completed: bool
    last_modified_at: datetime
    sync_cycle_id: str = Field(min_length=1)


class ListItemUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    list_external_id: str = Field(min_length=1)
    item_external_id: str = Field(min_length=1)
    list_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = None
    priority: Literal[0, 1, 5, 9] = Field(
        description="EventKit ordinal priority: 0=none, 1=high, 5=medium, 9=low.",
    )
    completed: bool
    last_modified_at: datetime
    sync_cycle_id: str = Field(min_length=1)
    field_updates: dict[str, Any] = Field(
        description=(
            "Map of changed-field-name → new value (per CalendarEventUpdatedV1). "
            "Consumers diff against the prior known state via this field."
        ),
    )


class ListItemCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    list_external_id: str = Field(min_length=1)
    item_external_id: str = Field(min_length=1)
    list_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = None
    priority: Literal[0, 1, 5, 9] = Field(
        description="EventKit ordinal priority: 0=none, 1=high, 5=medium, 9=low.",
    )
    completed: Literal[True] = Field(
        description=(
            "Always True for this kind — the completion-flip transition is the "
            "emission trigger, so a False value would violate the kind contract."
        ),
    )
    last_modified_at: datetime
    sync_cycle_id: str = Field(min_length=1)


class ListItemDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    list_external_id: str = Field(min_length=1)
    item_external_id: str = Field(min_length=1)
    deleted_at: datetime
    sync_cycle_id: str = Field(min_length=1)


ListProvider = Literal["apple_reminders", "google_tasks", "apple_notes_checklist"]


class ListChangedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: ListProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    list_external_id: str = Field(min_length=1, max_length=1000)
    list_name: str = Field(min_length=1, max_length=500)
    color: str | None = None
    is_default: bool = False
    provider_revision: str | None = None
    changed_at: datetime


class ListDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: ListProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    list_external_id: str = Field(min_length=1, max_length=1000)
    deleted_at: datetime


class ReminderDateComponentsV2(BaseModel):
    """EventKit-compatible date components, including floating dates."""

    model_config = {"extra": "forbid"}
    year: int | None = None
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    second: int | None = Field(default=None, ge=0, le=59)
    time_zone: str | None = None
    all_day: bool = False
    floating: bool = False


class ReminderLocationV2(BaseModel):
    model_config = {"extra": "forbid"}
    title: str = Field(min_length=1, max_length=500)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_meters: float = Field(default=100.0, gt=0)


class ReminderAlarmV2(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["absolute", "relative", "location"]
    absolute_at: datetime | None = None
    relative_offset_seconds: int | None = None
    location: ReminderLocationV2 | None = None
    proximity: Literal["enter", "leave", "none"] = "none"


class ListItemV2(BaseModel):
    """Complete provider-neutral list/reminder snapshot.

    Fields that EventKit cannot expose (for example Apple Reminders tags and
    subtasks) remain nullable so the canonical model can preserve them from
    future providers without falsely advertising an EventKit capability.
    """

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: ListProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    list_external_id: str = Field(min_length=1, max_length=1000)
    item_external_id: str = Field(min_length=1, max_length=1000)
    list_name: str = Field(min_length=1, max_length=500)
    list_color: str | None = None
    title: str = Field(min_length=1, max_length=1000)
    notes: str | None = None
    url: str | None = None
    priority: Literal[0, 1, 5, 9] = 0
    completed: bool = False
    completion_at: datetime | None = None
    start: ReminderDateComponentsV2 | None = None
    due: ReminderDateComponentsV2 | None = None
    alarms: list[ReminderAlarmV2] = Field(default_factory=list)
    recurrence_rules: list[str] = Field(default_factory=list)
    flagged: bool | None = None
    tags: list[str] | None = None
    parent_external_id: str | None = None
    sort_order: int | None = None
    linked_item_kind: Literal["task", "commitment"] | None = None
    linked_item_id: str | None = None
    completion_policy: Literal["manual", "mirror"] = "manual"
    provider_revision: str | None = None
    last_modified_at: datetime
    sync_cycle_id: str = Field(min_length=1)


class ListItemDeletedV2(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1, max_length=240)
    provider: ListProvider
    provider_account_id: str = Field(min_length=1, max_length=500)
    list_external_id: str = Field(min_length=1, max_length=1000)
    item_external_id: str = Field(min_length=1, max_length=1000)
    deleted_at: datetime
    sync_cycle_id: str = Field(min_length=1)


registry.register("list_item.added", 1, ListItemAddedV1)
registry.register("list_item.updated", 1, ListItemUpdatedV1)
registry.register("list_item.completed", 1, ListItemCompletedV1)
registry.register("list_item.deleted", 1, ListItemDeletedV1)
registry.register("list.created", 1, ListChangedV1)
registry.register("list.updated", 1, ListChangedV1)
registry.register("list.deleted", 1, ListDeletedV1)
registry.register("list_item.added", 2, ListItemV2)
registry.register("list_item.updated", 2, ListItemV2)
registry.register("list_item.completed", 2, ListItemV2)
registry.register("list_item.uncompleted", 1, ListItemV2)
registry.register("list_item.deleted", 2, ListItemDeletedV2)
