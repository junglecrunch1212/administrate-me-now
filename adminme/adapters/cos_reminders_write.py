"""Exact CoS-owned Reminders writes using the existing EventKit conversion seam.

The central command ledger owns admission and retry decisions. This native leaf
accepts only a current, fully identified create request for the reviewed source.
It never requests OS permission, discovers a fallback list, or retries a save.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from adminme.adapters.cos_apple_reminders import CoSRemindersSelection, digest
from adminme.adapters.cos_apple_reminders_native import NativeRemindersReader, _shim
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID


class CoSReminderCreate(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    contract: Literal["cos-owned-reminder-create-v1"]
    command_id: str = Field(min_length=1, max_length=240)
    requesting_member_id: str = Field(min_length=1, max_length=240)
    resource_owner: Literal["service-principal:chief-of-staff"]
    operating_principal: Literal["service-principal:chief-of-staff"]
    domain_approval_event_id: str = Field(min_length=1, max_length=240)
    openclaw_approval_event_id: str = Field(min_length=1, max_length=240)
    source_event_id: str = Field(min_length=1, max_length=240)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_account_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=8000)
    due_at: AwareDatetime
    expires_at: AwareDatetime

    @field_validator("title")
    @classmethod
    def exact_title(cls, value: str) -> str:
        if (
            not value.strip()
            or value != value.strip()
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("A reminder requires an exact nonblank title")
        return value

    @field_validator("due_at")
    @classmethod
    def second_precision(cls, value: datetime) -> datetime:
        if value.microsecond:
            raise ValueError("EventKit reminder times require whole seconds")
        return value


def exact_due_observed(value: Any, due: datetime) -> bool:
    if not isinstance(value, dict):
        return False
    utc = due.astimezone(timezone.utc)
    return (
        value.get("all_day") is False
        and value.get("floating") is False
        and value.get("time_zone") in {"UTC", "GMT", "Etc/UTC", "Etc/GMT"}
        and all(
            type(value.get(key)) is int and value.get(key) == getattr(utc, key)
            for key in ("year", "month", "day", "hour", "minute", "second")
        )
    )


def selected_source_sha256(selection: CoSRemindersSelection) -> str:
    """The source identity remains stable across separately installed helpers."""
    return digest(selection.model_dump(mode="json", exclude={"native_helper"}))


def validate_native_create(
    command: CoSReminderCreate,
    selection: CoSRemindersSelection,
    *,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(timezone.utc)
    if (
        command.resource_owner != COS_SERVICE_PRINCIPAL_ID
        or command.operating_principal != COS_SERVICE_PRINCIPAL_ID
        or command.provider_account_id != selection.provider_account_id
        or command.selection_sha256 != selected_source_sha256(selection)
        or command.expires_at <= current
        or command.due_at <= current
    ):
        raise PermissionError("CoS reminder request is expired or its source changed")


class NativeRemindersWriter:
    """One exact-list save followed by same-store read-back; never automatic retry."""

    def __init__(self, *, reader: Any | None = None) -> None:
        self.reader = reader if reader is not None else NativeRemindersReader()

    def inspect(self, selection: CoSRemindersSelection) -> dict[str, Any]:
        """Read only native source metadata; do not request consent or save."""
        self.reader._require_permission()
        calendar = self.reader._calendar(selection)
        if calendar is None or not calendar.allowsContentModifications():
            raise PermissionError("The selected CoS list does not permit changes")
        self.reader._require_permission()
        return {
            "writable": True,
            "provider_account_id": selection.provider_account_id,
            "source_id": selection.source_id,
            "list_id": selection.list_id,
        }

    def create(
        self, selection: CoSRemindersSelection, command: CoSReminderCreate
    ) -> dict[str, Any]:
        validate_native_create(command, selection)
        reader = self.reader
        reader._require_permission()
        calendar = reader._calendar(selection)
        if calendar is None or not calendar.allowsContentModifications():
            raise PermissionError("The selected CoS list does not permit changes")
        shim = _shim()

        class SelectedStore(shim.PyObjCEventStore):
            def __init__(self) -> None:
                # Reuse the permission-checked native store. The generic store's
                # constructor may request consent; runtime writes must never do so.
                self._store = reader._store

            def _calendar(self, identifier: str) -> Any:
                if identifier != selection.list_id:
                    raise PermissionError("The reminder destination changed")
                selected = reader._calendar(selection)
                if selected is None or not selected.allowsContentModifications():
                    raise PermissionError("The selected CoS list is no longer writable")
                return selected

        store = SelectedStore()
        native = store.build_reminder(
            list_identifier=selection.list_id,
            title=command.title,
            notes=command.notes,
            due_at=command.due_at.astimezone(timezone.utc),
            priority=0,
        )
        reader._require_permission()
        validate_native_create(command, selection)
        store._calendar(selection.list_id)
        store.save(native, commit=True)
        external_id = str(native.calendarItemIdentifier)
        if not external_id:
            raise RuntimeError("Native reminder save returned no identity")
        observed = store.reminder_with_identifier(external_id)
        if observed is None:
            raise RuntimeError("Native reminder save has no read-back result")
        reader._require_permission()
        store._calendar(selection.list_id)
        if (
            observed.list_identifier != selection.list_id
            or observed.title != command.title
            or observed.notes != command.notes
            or observed.completed
            or not exact_due_observed(observed.due_components, command.due_at)
        ):
            raise RuntimeError("Native reminder read-back differs from the request")
        return {
            "applied": True,
            "suppressed": False,
            "external_id": external_id,
            "provider_account_id": selection.provider_account_id,
            "source_id": selection.source_id,
            "list_id": selection.list_id,
            "requested_due_at": command.due_at.isoformat(),
            "observed_due": observed.due_components,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
