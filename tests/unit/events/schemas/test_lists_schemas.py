"""``list_item.*@v1`` canonical event-family schema canaries.

Closes prompt 11b-i §7 c3 deliverable 1 (schemas tests). Asserts the four
new event types added in 11b-i c1 register at v1 per [§D7], the Pydantic
models enforce the field contracts from the prompt §7 Deliverables table,
``priority`` accepts only EventKit's ordinal scale ``{0, 1, 5, 9}`` (per
F-6 — the parent draft erroneously listed three values; the on-main
``ReminderListItemSyncedV1`` declares four, and the new schemas mirror
that), ``extra="forbid"`` rejects unknown payload keys, ``ListItemUpdatedV1``
requires the ``field_updates`` field (mirroring ``CalendarEventUpdatedV1``
per §1 item 1), and ``ListItemCompletedV1`` constrains ``completed``
to ``Literal[True]`` so the kind is self-describing for downstream
consumers — matching the shape established by ``test_reminders_schemas.py``
(11c c1) and ``test_adapter_schemas.py`` (11a c1).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.lists import (
    ListItemAddedV1,
    ListItemCompletedV1,
    ListItemDeletedV1,
    ListItemUpdatedV1,
    ListItemV3,
)


_FIXED_LAST_MODIFIED = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
_FIXED_DUE = datetime(2026, 5, 17, 9, 0, 0, tzinfo=timezone.utc)
_FIXED_DELETED = datetime(2026, 5, 18, 8, 30, 0, tzinfo=timezone.utc)


def _added_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        member_id="member-001",
        list_external_id="list-household-001",
        item_external_id="x-coredata://AdministrateMe/EKReminder/abc-123",
        list_name="AdministrateMe Household",
        title="Pick up milk",
        notes=None,
        due_at=None,
        priority=5,
        completed=False,
        last_modified_at=_FIXED_LAST_MODIFIED,
        sync_cycle_id="cycle-2026-05-16T12:00:00Z",
    )
    base.update(overrides)
    return base


def _updated_payload(**overrides: object) -> dict[str, object]:
    base = _added_payload()
    base["field_updates"] = {"title": "Pick up milk and bread"}
    base.update(overrides)
    return base


def _completed_payload(**overrides: object) -> dict[str, object]:
    base = _added_payload(completed=True)
    base.update(overrides)
    return base


def _deleted_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        member_id="member-001",
        list_external_id="list-household-001",
        item_external_id="x-coredata://AdministrateMe/EKReminder/abc-123",
        deleted_at=_FIXED_DELETED,
        sync_cycle_id="cycle-2026-05-18T08:30:00Z",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Registration — all four new v1 schemas resolve through the registry
# ---------------------------------------------------------------------------


def test_list_item_schemas_preserve_v1_and_publish_rich_v2() -> None:
    """Each of the four new ``list_item.*@v1`` event types resolves
    through the canonical registry, with ``latest_version == 1``. Per
    [§D7] these are NEW v1 schemas — the legacy
    ``reminder.list_item.synced@v1`` is preserved unchanged on main."""
    ensure_autoloaded()
    assert registry.get("list_item.added", 1) is ListItemAddedV1
    assert registry.get("list_item.updated", 1) is ListItemUpdatedV1
    assert registry.get("list_item.completed", 1) is ListItemCompletedV1
    assert registry.get("list_item.deleted", 1) is ListItemDeletedV1
    for name in (
        "list_item.added",
        "list_item.updated",
        "list_item.completed",
    ):
        assert registry.latest_version(name) == 3
        assert registry.get(name, 3) is ListItemV3
    assert registry.latest_version("list_item.deleted") == 2


# ---------------------------------------------------------------------------
# Minimum payload + optional fields
# ---------------------------------------------------------------------------


def test_added_minimum_payload_validates() -> None:
    instance = ListItemAddedV1(**_added_payload())  # type: ignore[arg-type]
    assert instance.priority == 5
    assert instance.completed is False
    assert instance.notes is None
    assert instance.due_at is None
    assert instance.last_modified_at == _FIXED_LAST_MODIFIED


def test_added_optional_due_and_notes_populated() -> None:
    instance = ListItemAddedV1(
        **_added_payload(  # type: ignore[arg-type]
            notes="2% milk only",
            due_at=_FIXED_DUE,
            priority=1,
        )
    )
    assert instance.notes == "2% milk only"
    assert instance.due_at == _FIXED_DUE
    assert instance.priority == 1


def test_updated_minimum_payload_validates() -> None:
    instance = ListItemUpdatedV1(**_updated_payload())  # type: ignore[arg-type]
    assert instance.field_updates == {"title": "Pick up milk and bread"}


def test_completed_minimum_payload_validates() -> None:
    instance = ListItemCompletedV1(**_completed_payload())  # type: ignore[arg-type]
    assert instance.completed is True


def test_deleted_minimum_payload_validates() -> None:
    """``ListItemDeletedV1`` carries only the minimal identifier set per
    §1 item 1 — the source body is already gone."""
    instance = ListItemDeletedV1(**_deleted_payload())  # type: ignore[arg-type]
    assert instance.item_external_id == (
        "x-coredata://AdministrateMe/EKReminder/abc-123"
    )
    assert instance.deleted_at == _FIXED_DELETED


# ---------------------------------------------------------------------------
# priority — EventKit ordinal scale only (F-6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [0, 1, 5, 9])
@pytest.mark.parametrize(
    "model_cls,builder",
    [
        (ListItemAddedV1, _added_payload),
        (ListItemUpdatedV1, _updated_payload),
        (ListItemCompletedV1, _completed_payload),
    ],
)
def test_priority_accepts_eventkit_ordinals(
    priority: int,
    model_cls: type,
    builder: object,
) -> None:
    """F-6 (HARD/new for 11b-i): the on-main ``ReminderListItemSyncedV1``
    declares ``Literal[0, 1, 5, 9]`` — the new family MUST mirror that
    four-value set. The parent draft erroneously listed three values; the
    schemas land four."""
    instance = model_cls(**builder(priority=priority))  # type: ignore[operator,arg-type]
    assert instance.priority == priority


@pytest.mark.parametrize("priority", [2, 3, 4, 6, 7, 8, 10, -1, 100])
def test_priority_rejects_off_scale_values_on_added(priority: int) -> None:
    """A non-EventKit-ordinal value MUST fail Pydantic ``Literal`` at the
    registration boundary — defends against priority-scale drift."""
    with pytest.raises(ValidationError):
        ListItemAddedV1(**_added_payload(priority=priority))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# extra="forbid" — leaked keys rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls,builder",
    [
        (ListItemAddedV1, _added_payload),
        (ListItemUpdatedV1, _updated_payload),
        (ListItemCompletedV1, _completed_payload),
        (ListItemDeletedV1, _deleted_payload),
    ],
)
def test_rejects_extra_keys(model_cls: type, builder: object) -> None:
    """An unknown payload key — e.g. a leaked raw access token — must be
    rejected by ``extra="forbid"`` on every model in the family."""
    payload = builder()  # type: ignore[operator]
    payload["raw_access_token"] = "leaked"
    with pytest.raises(ValidationError):
        model_cls(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# String length caps + required-field min_length
# ---------------------------------------------------------------------------


def test_title_max_length_500_enforced() -> None:
    with pytest.raises(ValidationError):
        ListItemAddedV1(**_added_payload(title="a" * 501))  # type: ignore[arg-type]


def test_title_max_length_500_accepted_at_boundary() -> None:
    instance = ListItemAddedV1(**_added_payload(title="a" * 500))  # type: ignore[arg-type]
    assert len(instance.title) == 500


def test_notes_max_length_2000_enforced() -> None:
    with pytest.raises(ValidationError):
        ListItemAddedV1(**_added_payload(notes="b" * 2001))  # type: ignore[arg-type]


def test_required_strings_reject_empty() -> None:
    """``member_id``, ``list_external_id``, ``item_external_id``,
    ``list_name``, ``title``, ``sync_cycle_id`` all have
    ``min_length=1`` — empty strings must be rejected to keep downstream
    consumers from receiving zero-info identifiers."""
    for empty_field in (
        "member_id",
        "list_external_id",
        "item_external_id",
        "list_name",
        "title",
        "sync_cycle_id",
    ):
        with pytest.raises(ValidationError):
            ListItemAddedV1(**_added_payload(**{empty_field: ""}))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ListItemUpdatedV1 — field_updates contract per CalendarEventUpdatedV1
# ---------------------------------------------------------------------------


def test_updated_requires_field_updates() -> None:
    """``ListItemUpdatedV1`` MUST carry ``field_updates`` per §1 item 1.
    Omitting it is a ValidationError — consumers depend on this field for
    differential reconciliation against the prior known state."""
    payload = _added_payload()  # no field_updates
    with pytest.raises(ValidationError):
        ListItemUpdatedV1(**payload)  # type: ignore[arg-type]


def test_updated_field_updates_accepts_arbitrary_value_types() -> None:
    """``field_updates: dict[str, Any]`` accepts heterogeneous values per
    the ``CalendarEventUpdatedV1`` precedent — strings, ints, datetimes,
    booleans, nested dicts all flow through. Consumers diff-reconcile
    against the prior snapshot."""
    instance = ListItemUpdatedV1(
        **_updated_payload(  # type: ignore[arg-type]
            field_updates={
                "title": "new title",
                "priority": 1,
                "completed": True,
                "due_at": "2026-06-01T09:00:00Z",
            }
        )
    )
    assert instance.field_updates["priority"] == 1
    assert instance.field_updates["completed"] is True


# ---------------------------------------------------------------------------
# ListItemCompletedV1 — completed is Literal[True] (self-describing kind)
# ---------------------------------------------------------------------------


def test_completed_rejects_false_completed() -> None:
    """``ListItemCompletedV1.completed`` is ``Literal[True]`` — emitting
    this kind with ``completed=False`` would violate the kind contract.
    The Pydantic Literal validator is the load-bearing guard."""
    with pytest.raises(ValidationError):
        ListItemCompletedV1(**_completed_payload(completed=False))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ListItemDeletedV1 — minimal identifier set (no body fields)
# ---------------------------------------------------------------------------


def test_deleted_rejects_body_fields() -> None:
    """``ListItemDeletedV1`` carries only ``member_id`` /
    ``list_external_id`` / ``item_external_id`` / ``deleted_at`` /
    ``sync_cycle_id``. Body fields (``title``, ``notes``, ``priority``,
    ``completed``, ``last_modified_at``, ``list_name``, ``due_at``) are
    NOT present — the source data is already gone. ``extra="forbid"``
    enforces this so consumers can rely on the minimal shape."""
    for body_field in (
        "title",
        "notes",
        "priority",
        "completed",
        "last_modified_at",
        "list_name",
        "due_at",
    ):
        payload = _deleted_payload()
        payload[body_field] = "anything"
        with pytest.raises(ValidationError):
            ListItemDeletedV1(**payload)  # type: ignore[arg-type]
