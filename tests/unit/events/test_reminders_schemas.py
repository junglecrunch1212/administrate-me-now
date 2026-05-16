"""``reminder.list_item.synced`` schema-registration canaries.

Asserts the reminders-family event type added in prompt 11c commit 1
registers at v1 per [D7], the Pydantic model enforces the required-field
contracts from the prompt §7 Deliverables table, ``priority`` accepts
only EventKit's ordinal scale ``{0,1,5,9}``, ``direction`` accepts only
``{"inbound","outbound"}``, ``title`` / ``notes`` length caps reject
oversize input, and ``extra="forbid"`` rejects unknown payload keys —
matching the shape established by ``test_adapter_schemas.py`` (11a c1)
and ``test_financial_schemas.py`` (11b c1).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.reminders import ReminderListItemSyncedV1


_FIXED_LAST_MODIFIED = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
_FIXED_DUE = datetime(2026, 5, 17, 9, 0, 0, tzinfo=timezone.utc)


def _minimum_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        member_id="member-001",
        remind_id="x-coredata://AdministrateMe/EKReminder/abc-123",
        list_identifier="list-household-001",
        list_name="AdministrateMe Household",
        title="Pick up milk",
        notes=None,
        due_at=None,
        priority=5,
        completed=False,
        last_modified_at=_FIXED_LAST_MODIFIED,
        direction="inbound",
        sync_cycle_id="cycle-2026-05-16T12:00:00Z",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_reminder_list_item_synced_registered_at_v1() -> None:
    ensure_autoloaded()
    assert registry.get("reminder.list_item.synced", 1) is ReminderListItemSyncedV1
    assert registry.latest_version("reminder.list_item.synced") == 1


# ---------------------------------------------------------------------------
# Minimum payload + optional fields
# ---------------------------------------------------------------------------


def test_minimum_payload_validates() -> None:
    instance = ReminderListItemSyncedV1(**_minimum_payload())  # type: ignore[arg-type]
    assert instance.priority == 5
    assert instance.direction == "inbound"
    assert instance.notes is None
    assert instance.due_at is None
    assert instance.completed is False


def test_optional_due_and_notes_populated() -> None:
    instance = ReminderListItemSyncedV1(
        **_minimum_payload(  # type: ignore[arg-type]
            notes="2% milk only",
            due_at=_FIXED_DUE,
            direction="outbound",
            priority=1,
            completed=True,
        )
    )
    assert instance.notes == "2% milk only"
    assert instance.due_at == _FIXED_DUE
    assert instance.direction == "outbound"
    assert instance.priority == 1
    assert instance.completed is True


# ---------------------------------------------------------------------------
# priority — EventKit ordinal scale only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [0, 1, 5, 9])
def test_priority_accepts_eventkit_ordinals(priority: int) -> None:
    instance = ReminderListItemSyncedV1(**_minimum_payload(priority=priority))  # type: ignore[arg-type]
    assert instance.priority == priority


@pytest.mark.parametrize("priority", [2, 3, 4, 6, 7, 8, 10, -1, 100])
def test_priority_rejects_off_scale_values(priority: int) -> None:
    with pytest.raises(ValidationError):
        ReminderListItemSyncedV1(**_minimum_payload(priority=priority))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# direction — inbound | outbound only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["inbound", "outbound"])
def test_direction_accepts_inbound_and_outbound(direction: str) -> None:
    instance = ReminderListItemSyncedV1(**_minimum_payload(direction=direction))  # type: ignore[arg-type]
    assert instance.direction == direction


@pytest.mark.parametrize("direction", ["both", "sideways", "", "INBOUND"])
def test_direction_rejects_off_enum_values(direction: str) -> None:
    with pytest.raises(ValidationError):
        ReminderListItemSyncedV1(**_minimum_payload(direction=direction))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# String length caps + required-field min_length
# ---------------------------------------------------------------------------


def test_title_max_length_500_enforced() -> None:
    with pytest.raises(ValidationError):
        ReminderListItemSyncedV1(**_minimum_payload(title="a" * 501))  # type: ignore[arg-type]


def test_title_max_length_500_accepted_at_boundary() -> None:
    instance = ReminderListItemSyncedV1(**_minimum_payload(title="a" * 500))  # type: ignore[arg-type]
    assert len(instance.title) == 500


def test_notes_max_length_2000_enforced() -> None:
    with pytest.raises(ValidationError):
        ReminderListItemSyncedV1(**_minimum_payload(notes="b" * 2001))  # type: ignore[arg-type]


def test_required_strings_reject_empty() -> None:
    """``member_id``, ``remind_id``, ``list_identifier``, ``list_name``,
    ``title``, ``sync_cycle_id`` all have ``min_length=1`` — empty strings
    must be rejected to keep downstream consumers from receiving zero-info
    identifiers."""
    for empty_field in (
        "member_id",
        "remind_id",
        "list_identifier",
        "list_name",
        "title",
        "sync_cycle_id",
    ):
        with pytest.raises(ValidationError):
            ReminderListItemSyncedV1(**_minimum_payload(**{empty_field: ""}))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# extra="forbid"
# ---------------------------------------------------------------------------


def test_rejects_extra_keys() -> None:
    """An unknown payload key — e.g. a leaked raw access token — must be
    rejected by ``extra="forbid"`` per the schema contract."""
    payload = _minimum_payload()
    payload["raw_access_token"] = "leaked"
    with pytest.raises(ValidationError):
        ReminderListItemSyncedV1(**payload)  # type: ignore[arg-type]
