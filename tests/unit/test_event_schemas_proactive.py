"""ReminderDispatchedV1 + CrmSurfaceComposedV1 + CustodyBriefComposedV1
schema-registration canaries.

Asserts each of the three 10c-iii event types registers at v1 per [D7]
and the Pydantic models enforce the required-field contracts from the
10c-iii Operating Context. Per 10c-iii commit 4 amendment the three
models are ``extra="allow"`` to accommodate handler-side audit context
(``delivered``, ``tone_note``, ``sort_order``) that is informational but
not load-bearing in the spec.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.proactive import (
    CrmSurfaceComposedV1,
    CustodyBriefComposedV1,
    ReminderDispatchedV1,
)


def test_reminder_dispatched_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("reminder.dispatched", 1)
    assert model is ReminderDispatchedV1
    assert registry.latest_version("reminder.dispatched") == 1


def test_crm_surface_composed_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("crm_surface.composed", 1)
    assert model is CrmSurfaceComposedV1
    assert registry.latest_version("crm_surface.composed") == 1


def test_custody_brief_composed_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("custody_brief.composed", 1)
    assert model is CustodyBriefComposedV1
    assert registry.latest_version("custody_brief.composed") == 1


def test_reminder_dispatched_minimum_payload_validates() -> None:
    instance = ReminderDispatchedV1(
        commitment_id="cmt-1",
        owner_member_id="member-a",
        recipient_party_id="party-b",
        composed_text="Heads up: dentist appointment in 45 minutes.",
        channel="imessage",
        due_at=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
    )
    assert instance.scheduled_send_at is None
    assert instance.channel == "imessage"


def test_reminder_dispatched_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        ReminderDispatchedV1(  # type: ignore[call-arg]
            commitment_id="cmt-1",
            owner_member_id="member-a",
            recipient_party_id="party-b",
            composed_text="x",
            channel="imessage",
        )


def test_reminder_dispatched_allows_handler_audit_extras() -> None:
    instance = ReminderDispatchedV1(
        commitment_id="cmt-1",
        owner_member_id="member-a",
        recipient_party_id="party-b",
        composed_text="x",
        channel="imessage",
        due_at=datetime(2026, 5, 5, 15, 0, tzinfo=timezone.utc),
        delivered=True,
        tone_note="warm",
        handler_audit_field="ok",  # type: ignore[call-arg]
    )
    assert instance.delivered is True


def test_crm_surface_composed_minimum_payload_validates() -> None:
    instance = CrmSurfaceComposedV1(
        member_id="member-a",
        surfaced_party_ids=["party-1", "party-2"],
        composed_text="Three contacts you haven't reached in a while.",
        channel="email",
        week_of_date=datetime(2026, 5, 3, tzinfo=timezone.utc),
    )
    assert instance.surfaced_party_ids == ["party-1", "party-2"]
    assert instance.channel == "email"


def test_crm_surface_composed_rejects_empty_surfaced_party_ids() -> None:
    with pytest.raises(ValidationError):
        CrmSurfaceComposedV1(
            member_id="member-a",
            surfaced_party_ids=[],
            composed_text="x",
            channel="email",
            week_of_date=datetime(2026, 5, 3, tzinfo=timezone.utc),
        )


def test_crm_surface_composed_allows_handler_audit_extras() -> None:
    instance = CrmSurfaceComposedV1(
        member_id="member-a",
        surfaced_party_ids=["party-1"],
        composed_text="x",
        channel="email",
        week_of_date=datetime(2026, 5, 3, tzinfo=timezone.utc),
        sort_order=["party-1"],
        delivered=False,
    )
    assert instance.sort_order == ["party-1"]
    assert instance.delivered is False


def test_custody_brief_composed_minimum_payload_validates() -> None:
    instance = CustodyBriefComposedV1(
        member_id="member-a",
        coparent_member_id="member-b",
        child_member_ids=["child-1"],
        composed_text="Tomorrow: soccer practice at 4pm; handoff at 6pm.",
        channel="push",
        brief_date=datetime(2026, 5, 5, tzinfo=timezone.utc),
    )
    assert instance.child_member_ids == ["child-1"]
    assert instance.channel == "push"


def test_custody_brief_composed_rejects_empty_child_member_ids() -> None:
    with pytest.raises(ValidationError):
        CustodyBriefComposedV1(
            member_id="member-a",
            coparent_member_id="member-b",
            child_member_ids=[],
            composed_text="x",
            channel="push",
            brief_date=datetime(2026, 5, 5, tzinfo=timezone.utc),
        )


def test_custody_brief_composed_allows_handler_audit_extras() -> None:
    instance = CustodyBriefComposedV1(
        member_id="member-a",
        coparent_member_id="member-b",
        child_member_ids=["child-1"],
        composed_text="x",
        channel="push",
        brief_date=datetime(2026, 5, 5, tzinfo=timezone.utc),
        delivered=True,
    )
    assert instance.delivered is True
