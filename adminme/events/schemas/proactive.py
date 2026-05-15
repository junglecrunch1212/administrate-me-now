"""
Proactive-pipeline composed-event schemas — reminder_dispatch + crm_surface
+ custody_brief.

Per SYSTEM_INVARIANTS.md §1 invariant 9 and DECISIONS.md §D7, each event
type registers at v1 at module import time via ``registry.register(...)``;
``SchemaRegistry.autoload()`` walks ``adminme.events.schemas.*`` and
imports this module.

Shape reference: ``DigestComposedV1`` (adminme.events.schemas.domain) and
``ParalysisTriggeredV1`` — envelope-level fields (tenant_id,
correlation_id, causation_id, occurred_at) live on the EventEnvelope; the
payload models defined here carry only the event-specific fields per
[§1 invariant 9].

Emit-side discipline:
- ``reminder.dispatched`` is appended at composition time by
  ``ReminderDispatchPipeline``; the dispatcher's actual delivery result
  is logged separately via the on-main ``outbound/dispatcher.py`` contract
  (``delivery.suppressed`` / ``external.sent``) per [§6.13].
- ``crm_surface.composed`` and ``custody_brief.composed`` follow the same
  proactive-pipeline causation pattern from 10c-i: ``causation_id`` is the
  standing-order tick event.

[D17] is load-bearing: none of these three pipelines write projections
directly. Projection state changes flow through the existing subscribers
of these emitted events.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class ReminderDispatchedV1(BaseModel):
    """Reminder-dispatch composition contract emitted by the
    ``reminder_dispatch`` proactive pipeline at composition time.

    Emitted before ``outbound.deliver`` so the composition is auditable
    even when observation mode suppresses delivery per [§6.13]. The
    actual delivery outcome (``delivery.suppressed`` / ``external.sent``)
    is logged separately by ``adminme/outbound/dispatcher.py`` per its
    existing contract from 10c-i.

    Dedupe discipline per 10c-iii Operating Context: a recent
    ``reminder.dispatched`` event for the same ``commitment_id`` within
    ``dedupe_window_minutes`` suppresses re-emission. Registered at v1
    per [D7]."""

    model_config = {"extra": "forbid"}
    commitment_id: str = Field(min_length=1)
    owner_member_id: str = Field(min_length=1)
    recipient_party_id: str = Field(min_length=1)
    composed_text: str = Field(min_length=1)
    channel: Literal["imessage", "sms", "email", "push"]
    due_at: datetime
    scheduled_send_at: datetime | None = None


class CrmSurfaceComposedV1(BaseModel):
    """Weekly CRM-surface composition contract emitted by the
    ``crm_surface`` proactive pipeline per opt-in member that has at
    least one stale party at scan time.

    Per [D22] opt-in not opt-out: ``crm_proactive_enabled`` defaults
    false; this event is only emitted for members who explicitly enable
    proactive CRM surfacing. ``surfaced_party_ids`` carries the (clamped
    to ``max_surfaced_per_week``) ordered list of party_ids the skill
    composed about; downstream consumers can correlate to the parties
    projection.

    The composed text is auditable even when observation mode suppresses
    delivery per [§6.13]. Registered at v1 per [D7]."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    surfaced_party_ids: list[str] = Field(min_length=1)
    composed_text: str = Field(min_length=1)
    channel: Literal["imessage", "sms", "email", "push"]
    week_of_date: datetime


class CustodyBriefComposedV1(BaseModel):
    """Nightly custody-brief composition contract emitted by the
    ``custody_brief`` proactive pipeline per parent member with a
    coparent relationship set.

    ``child_member_ids`` has ``min_length=1`` — a brief with no children
    is suppressed before emit at the pipeline boundary per the 10c-iii
    Operating Context, not allowed past the schema. The composed text
    covers next-day calendar events, handoff timing, and allergen/med
    reminders per [BUILD.md §L4].

    Per [D22] opt-out: ``custody_brief_enabled`` defaults true when a
    coparent relationship exists; the per-member override flips it off.
    Registered at v1 per [D7]."""

    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    coparent_member_id: str = Field(min_length=1)
    child_member_ids: list[str] = Field(min_length=1)
    composed_text: str = Field(min_length=1)
    channel: Literal["imessage", "sms", "email", "push"]
    brief_date: datetime


registry.register("reminder.dispatched", 1, ReminderDispatchedV1)
registry.register("crm_surface.composed", 1, CrmSurfaceComposedV1)
registry.register("custody_brief.composed", 1, CustodyBriefComposedV1)
