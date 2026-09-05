from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from adminme.google_tasks_execution import (
    B03PolicyError,
    ExecutionCoordinate,
    canonical_task_content,
    task_content_fingerprint,
)
from adminme.google_tasks_execution_reconciliation import (
    GoogleTasksExecutionReconciler,
)
from adminme.lib.observation import OutboundAuditContext, ScopedOutboundAdmission
from adminme.provider_bindings import validate_provider_bindings


def _coordinate() -> ExecutionCoordinate:
    now = datetime.now(timezone.utc)
    return ExecutionCoordinate(
        command_id="command-1",
        initiating_event_id="event-1",
        envelope_id="envelope-1",
        envelope_revision=1,
        represented_cos="service-principal:chief-of-staff",
        actual_operator="operator:openclaw:automation",
        context_scope="member_private",
        on_behalf_of="member-a",
        authority_grant_id="grant-1",
        operating_agreement_id="agreement-1",
        resource_owner="member-a",
        provider_owner="service-principal:chief-of-staff",
        provider="google_tasks",
        provider_account="provider-account-a",
        adapter_identity="tasks-google-tasks",
        destination_task_list="existing-list-a",
        content_fingerprint="a" * 64,
        domain_approval_event_id="domain-approval-1",
        openclaw_approval_event_id="openclaw-approval-1",
        valid_from=(now - timedelta(seconds=1)).isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        idempotency_key="idempotency-1",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("represented_cos", "service-principal:other"),
        ("actual_operator", "operator:other"),
        ("context_scope", "external_contact"),
        ("on_behalf_of", "member-b"),
        ("resource_owner", "member-b"),
        ("provider_owner", "member-a"),
        ("provider", "google_calendar"),
        ("adapter_identity", "wrong-adapter"),
        ("maximum_effect_count", 2),
    ],
)
def test_exact_execution_coordinate_fails_closed(field: str, value: object) -> None:
    coordinate = replace(_coordinate(), **{field: value})
    with pytest.raises(B03PolicyError):
        coordinate.validate()


def test_expired_execution_coordinate_fails_closed() -> None:
    coordinate = replace(
        _coordinate(), expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    )
    with pytest.raises(B03PolicyError, match="trial window"):
        coordinate.validate()


@pytest.mark.parametrize(
    "mutation",
    [
        {"title": "changed", "notes": "same"},
        {"title": "same", "notes": "changed"},
        {"title": "same", "notes": "same", "due": "2030-01-01T00:00:00Z"},
        {"title": "same", "notes": "same", "parent": "changed"},
        {"title": "same", "notes": "same", "previous": "changed"},
        {"title": "same", "notes": "same", "status": "needsAction"},
    ],
)
def test_every_approved_task_field_changes_fingerprint(mutation: dict[str, object]) -> None:
    assert task_content_fingerprint(mutation) != task_content_fingerprint(
        {"title": "same", "notes": "same"}
    )


def test_unapproved_task_field_and_completed_create_are_rejected() -> None:
    with pytest.raises(B03PolicyError, match="unapproved fields"):
        canonical_task_content({"title": "x", "links": []})
    with pytest.raises(B03PolicyError, match="completed"):
        canonical_task_content({"title": "x", "status": "completed"})
    for key in ("due", "parent", "previous"):
        with pytest.raises(B03PolicyError, match=key):
            canonical_task_content({"title": "x", key: {"not": "text"}})


def test_scoped_observation_admission_is_exact_and_single_effect() -> None:
    coordinate = _coordinate()
    audit = OutboundAuditContext(
        initiating_command_id=coordinate.command_id,
        initiating_event_id=coordinate.initiating_event_id,
        domain_approval_event_id=coordinate.domain_approval_event_id,
        openclaw_approval_event_id=coordinate.openclaw_approval_event_id,
        provider=coordinate.provider,
        provider_account_id=coordinate.provider_account,
        operation="create_task",
        content_fingerprint=coordinate.content_fingerprint,
        owner_scope="private:member-a",
        visibility_scope="private:member-a",
        sensitivity="normal",
    )
    admission = ScopedOutboundAdmission(
        command_id=coordinate.command_id,
        initiating_event_id=coordinate.initiating_event_id,
        execution_envelope_id=coordinate.envelope_id,
        execution_envelope_revision=1,
        domain_approval_event_id=coordinate.domain_approval_event_id,
        openclaw_approval_event_id=coordinate.openclaw_approval_event_id,
        provider=coordinate.provider,
        provider_account_id=coordinate.provider_account,
        operation="create_task",
        destination_id=coordinate.destination_task_list,
        content_fingerprint=coordinate.content_fingerprint,
        owner_scope=audit.owner_scope,
        visibility_scope=audit.visibility_scope,
        expires_at=coordinate.expires_at,
        maximum_effect_count=1,
        contact_attempt_event_id="contact-attempt-1",
    )
    assert admission.admits(
        audit, action="tasks.create_task", target_identifier=coordinate.destination_task_list
    )
    assert not replace(admission, command_id="command-2").admits(
        audit, action="tasks.create_task", target_identifier=coordinate.destination_task_list
    )
    assert not replace(admission, destination_id="list-b").admits(
        audit, action="tasks.create_task", target_identifier=coordinate.destination_task_list
    )
    assert not replace(admission, maximum_effect_count=2).admits(
        audit, action="tasks.create_task", target_identifier=coordinate.destination_task_list
    )
    assert not admission.admits(
        audit, action="tasks.update_task", target_identifier=coordinate.destination_task_list
    )


def _binding_config() -> dict[str, object]:
    return {
        "adapters": [
            {
                "id": "b03-tasks",
                "kind": "tasks-google-tasks",
                "enabled": True,
                "config": {
                    "service_account_key_ref": "op://Fixture/b03/key",
                    "provider_account_id": "provider-account-a",
                    "service_principal_id": "service-principal:chief-of-staff",
                    "workspace": "service",
                    "read_only": False,
                    "enable_writes": True,
                    "permission_scope": ["https://www.googleapis.com/auth/tasks"],
                    "binding_provenance": "service-account-delegation",
                    "ownership_attested": True,
                    "tasklist_id": "existing-list-a",
                    "b03_google_tasks_create": {
                        "action_class": "google_tasks_create",
                        "represented_service_principal_id": "service-principal:chief-of-staff",
                        "provider_account_id": "provider-account-a",
                        "destination_task_list_id": "existing-list-a",
                        "destination_preexisting": True,
                        "permitted_operation": "create_task",
                        "required_permission_scope": "https://www.googleapis.com/auth/tasks",
                        "context_scope": "member_private",
                        "on_behalf_of": "member-a",
                        "resource_owner_principal_id": "member-a",
                        "state": "trial",
                        "revision": 1,
                        "review_provenance": "fixture:review",
                    },
                },
            }
        ]
    }


def test_b03_binding_has_full_owner_destination_operation_coordinate() -> None:
    bindings = validate_provider_bindings(
        _binding_config(),
        member_roles={"member-a": "principal"},
        household_mapping={},
        service_principal_ids={"service-principal:chief-of-staff"},
    )
    assert len(bindings) == 1
    assert bindings[0].b03_dispatch_coordinate == _coordinate().dispatch_coordinate


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("destination_preexisting", False),
        ("permitted_operation", "update_task"),
        ("state", "permanent"),
        ("provider_account_id", "provider-account-b"),
        ("resource_owner_principal_id", "member-b"),
        ("resource_owner_principal_id", ""),
    ],
)
def test_b03_binding_rejects_widened_or_ownerless_coordinates(
    key: str, value: object
) -> None:
    config = _binding_config()
    adapter = config["adapters"][0]  # type: ignore[index]
    adapter["config"]["b03_google_tasks_create"][key] = value  # type: ignore[index]
    with pytest.raises(ValueError):
        validate_provider_bindings(
            config,  # type: ignore[arg-type]
            member_roles={"member-a": "principal", "member-b": "principal"},
            household_mapping={},
            service_principal_ids={"service-principal:chief-of-staff"},
        )


def test_b03_binding_rejects_multiple_full_coordinate_matches() -> None:
    config = _binding_config()
    duplicate = copy.deepcopy(config["adapters"][0])  # type: ignore[index]
    duplicate["id"] = "b03-tasks-duplicate"
    config["adapters"].append(duplicate)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="duplicate full owner/account/adapter coordinate"):
        validate_provider_bindings(
            config,  # type: ignore[arg-type]
            member_roles={"member-a": "principal"},
            household_mapping={},
            service_principal_ids={"service-principal:chief-of-staff"},
        )


class _ReconciliationEvents:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events
        self.written: list[dict[str, object]] = []

    async def read_since(self, *, types: list[str]) -> object:
        for event in self.events:
            if event["type"] in types:
                yield event

    async def write(self, envelope: object) -> str:
        event = envelope.model_dump(mode="json")  # type: ignore[attr-defined]
        event["event_id"] = f"written-{len(self.written) + 1}"
        self.written.append(event)
        return str(event["event_id"])


def _reconciliation_event(
    event_id: str, event_type: str, payload: dict[str, object]
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "tenant_id": "tenant-a",
        "type": event_type,
        "owner_scope": "service-private:service-principal:chief-of-staff",
        "visibility_scope": "private:member-a",
        "sensitivity": "normal",
        "payload": payload,
    }


async def test_reconciliation_detects_later_exact_content_duplicate() -> None:
    desired = {"title": "Exact task", "notes": "One only"}
    desired_fingerprint = task_content_fingerprint(desired)
    events = _ReconciliationEvents(
        [
            _reconciliation_event(
                "prepared-1",
                "cos.google_tasks_execution.prepared",
                {
                    "command_id": "command-1",
                    "execution_envelope_id": "envelope-1",
                    "active_context": "member_private",
                    "resource_owner": "member-a",
                    "provider_owner": "service-principal:chief-of-staff",
                    "desired_task_content": desired,
                },
            ),
            _reconciliation_event(
                "terminal-1",
                "cos.google_tasks_execution.terminal",
                {
                    "command_id": "command-1",
                    "execution_envelope_id": "envelope-1",
                    "outcome": "success",
                    "provider_account_coordinate": "account-a",
                    "task_list_id": "list-a",
                    "native_task_id": "native-original",
                    "desired_state_fingerprint": desired_fingerprint,
                },
            ),
            _reconciliation_event(
                "source-original",
                "list_item.added",
                {
                    "provider": "google_tasks",
                    "provider_account_id": "account-a",
                    "service_principal_id": "service-principal:chief-of-staff",
                    "list_external_id": "list-a",
                    "item_external_id": "native-original",
                    **desired,
                },
            ),
            _reconciliation_event(
                "source-duplicate",
                "list_item.added",
                {
                    "provider": "google_tasks",
                    "provider_account_id": "account-a",
                    "service_principal_id": "service-principal:chief-of-staff",
                    "list_external_id": "list-a",
                    "item_external_id": "native-duplicate",
                    **desired,
                },
            ),
        ]
    )
    reconciler = GoogleTasksExecutionReconciler(events, events)

    await reconciler.handle(events.events[-1])

    reconciled = next(
        event
        for event in events.written
        if event["type"] == "cos.google_tasks_execution.reconciled"
    )
    assert reconciled["payload"]["outcome"] == "conflicted"  # type: ignore[index]
    assert reconciled["payload"]["duplicate_provider_objects"] == 1  # type: ignore[index]
    assert reconciled["payload"]["provider_effect_count"] == 0  # type: ignore[index]
    observed = next(
        event
        for event in events.written
        if event["type"] == "cos.google_tasks_execution.observed"
    )
    assert observed["payload"]["resource_owner"] == "member-a"  # type: ignore[index]


async def test_reconciliation_cannot_match_without_terminal_result() -> None:
    observation = _reconciliation_event(
        "source-1",
        "list_item.added",
        {
            "provider": "google_tasks",
            "provider_account_id": "account-a",
            "service_principal_id": "service-principal:chief-of-staff",
            "list_external_id": "list-a",
            "item_external_id": "native-1",
            "title": "Exact task",
        },
    )
    events = _ReconciliationEvents([observation])

    await GoogleTasksExecutionReconciler(events, events).handle(observation)

    assert events.written == []
