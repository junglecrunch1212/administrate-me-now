"""A selected CoS native write never falls back, requests consent or repeats a save."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from adminme.adapters import cos_reminders_write as writes
from tests.unit.bootstrap.test_cos_apple_reminders import _selection


def _command(**changes: Any) -> writes.CoSReminderCreate:
    due = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    values = {
        "contract": "cos-owned-reminder-create-v1",
        "command_id": "fixture-command",
        "requesting_member_id": "member-fixture",
        "resource_owner": "service-principal:chief-of-staff",
        "operating_principal": "service-principal:chief-of-staff",
        "domain_approval_event_id": "fixture-domain",
        "openclaw_approval_event_id": "fixture-host",
        "source_event_id": "fixture-instruction",
        "request_fingerprint": "e" * 64,
        "selection_sha256": writes.selected_source_sha256(_selection()),
        "provider_account_id": _selection().provider_account_id,
        "title": "Prepare the household brief",
        "notes": "Retained private work",
        "due_at": due,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=1),
    }
    return writes.CoSReminderCreate.model_validate({**values, **changes})


def _native(monkeypatch: pytest.MonkeyPatch):
    state = SimpleNamespace(
        saves=0, checks=0, writable=True, error=None, observed_change=None
    )
    native_store = object()
    calendar = SimpleNamespace(allowsContentModifications=lambda: state.writable)

    def permission():
        state.checks += 1
        if state.error:
            raise PermissionError("Synthetic permission failure")

    def selected(selection):
        assert selection == _selection()
        permission()
        return calendar

    reader = SimpleNamespace(
        _require_permission=permission, _calendar=selected, _store=native_store
    )

    class Store:
        def __init__(self):
            pytest.fail("Generic EventKit constructor would request consent")

        def build_reminder(self, *, list_identifier, title, notes, due_at, priority):
            assert self._store is native_store
            assert list_identifier == _selection().list_id
            assert str(due_at.tzinfo) == "UTC"
            self._calendar(list_identifier)
            self.record = SimpleNamespace(
                calendarItemIdentifier="fixture-created-reminder",
                list_identifier=list_identifier,
                title=title,
                notes=notes,
                completed=False,
                due_components={
                    **{
                        key: getattr(due_at, key)
                        for key in ("year", "month", "day", "hour", "minute", "second")
                    },
                    "time_zone": "GMT",
                    "all_day": False,
                    "floating": False,
                },
            )
            return self.record

        def save(self, reminder, *, commit):
            assert commit is True
            assert reminder is self.record
            state.saves += 1

        def reminder_with_identifier(self, identity):
            assert identity == self.record.calendarItemIdentifier
            if state.observed_change:
                state.observed_change(self.record)
            return self.record

    monkeypatch.setattr(
        writes, "_shim", lambda: SimpleNamespace(PyObjCEventStore=Store)
    )
    return writes.NativeRemindersWriter(reader=reader), state


def test_native_create_preserves_exact_offset_time_and_reads_back(monkeypatch):
    writer, state = _native(monkeypatch)
    offset = timezone(timedelta(hours=-4))
    command = _command(
        due_at=(datetime.now(offset) + timedelta(days=1)).replace(
            hour=9, minute=30, second=0, microsecond=0
        )
    )
    result = writer.create(_selection(), command)
    assert state.saves == 1 and state.checks >= 4
    assert result["applied"] is True
    assert result["external_id"] == "fixture-created-reminder"
    assert result["requested_due_at"] == command.due_at.isoformat()
    assert result["observed_due"]["hour"] == 13
    assert result["list_id"] == _selection().list_id


def test_native_writability_inspection_never_saves(monkeypatch):
    writer, state = _native(monkeypatch)
    result = writer.inspect(_selection())
    assert result["writable"] is True
    assert result["source_id"] == _selection().source_id
    assert state.saves == 0
    state.writable = False
    with pytest.raises(PermissionError):
        writer.inspect(_selection())
    assert state.saves == 0


@pytest.mark.parametrize(
    "change",
    [
        {"provider_account_id": "other-account"},
        {"selection_sha256": "f" * 64},
        {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
        {"due_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
    ],
)
def test_invalid_native_binding_never_reaches_save(monkeypatch, change):
    writer, state = _native(monkeypatch)
    with pytest.raises(PermissionError):
        writer.create(_selection(), _command(**change))
    assert state.saves == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource_owner", "requester"),
        ("operating_principal", "member-fixture"),
        ("title", " "),
        ("due_at", "2099-01-01T09:30:00"),
        ("due_at", "2099-01-01T09:30:00.123Z"),
    ],
)
def test_invalid_owner_or_unsupported_time_has_no_native_request(field, value):
    with pytest.raises(ValidationError):
        _command(**{field: value})


@pytest.mark.parametrize("defect", ["permission", "readonly"])
def test_revoked_os_permission_or_readonly_list_has_no_save(monkeypatch, defect):
    writer, state = _native(monkeypatch)
    state.error = defect == "permission"
    state.writable = defect != "readonly"
    with pytest.raises(PermissionError):
        writer.create(_selection(), _command())
    assert state.saves == 0


@pytest.mark.parametrize("defect", ["time", "floating", "list", "title"])
def test_post_save_mismatch_is_unknown_and_does_not_repeat(monkeypatch, defect):
    writer, state = _native(monkeypatch)

    def change(record):
        if defect == "time":
            record.due_components["hour"] = (record.due_components["hour"] + 1) % 24
        elif defect == "floating":
            record.due_components["floating"] = True
        elif defect == "list":
            record.list_identifier = "other-list"
        else:
            record.title = "Changed title"

    state.observed_change = change
    with pytest.raises(RuntimeError, match="read-back differs"):
        writer.create(_selection(), _command())
    assert state.saves == 1
