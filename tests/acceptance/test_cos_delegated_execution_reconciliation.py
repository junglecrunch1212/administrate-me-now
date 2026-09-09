"""Process acceptance for G07 delegated execution and reconciliation."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.delegated_execution import ACTION_SPECS, execution_policy_digest
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.openclaw_topology import binding_fingerprints, member_agent_id
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.projections.tasks.queries import get_delegated_action
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"x" * 32
SECRET = "g07-loopback-secret-at-least-32-bytes"
A = "member-g07-a"  # fixture:tenant_data:ok
B = "member-g07-b"  # fixture:tenant_data:ok
A_HEADERS = {"Tailscale-User-Login": "g07-a.ts.net"}
B_HEADERS = {"Tailscale-User-Login": "g07-b.ts.net"}
OPENCLAW_HEADERS = {"Authorization": f"Bearer {SECRET}"}


def _settings(instance_dir: Path) -> RuntimeSettings:
    actors = {
        "fixture|g07-account-a|g07-actor-a": A,
        "fixture|g07-account-b|g07-actor-b": B,
    }
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"g07-a.ts.net": A, "g07-b.ts.net": B},
        member_roles={A: "principal", B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "G07 fixture household",  # fixture:tenant_data:ok
            "members": [
                {"member_id": A, "label": "Fixture adult A", "role": "principal"},
                {"member_id": B, "label": "Fixture adult B", "role": "principal"},
            ],
            "memberships": [
                {
                    "membership_id": "g07-membership-a",
                    "party_id": A,
                    "role": "principal",
                },
                {
                    "membership_id": "g07-membership-b",
                    "party_id": B,
                    "role": "principal",
                },
            ],
            "relationships": [],
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=A,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities=actors,
    )


def _provenance(
    correlation_id: str,
    *,
    member: str = A,
    operator_kind: str = "tool",
) -> dict[str, Any]:
    suffix = "a" if member == A else "b"
    return {
        "actor_id": f"g07-actor-{suffix}",
        "agent_id": f"g07-agent-{suffix}",
        "session_key": f"g07-session-key-{suffix}",
        "session_id": f"g07-session-{suffix}",
        "channel_id": "fixture",
        "account_id": f"g07-account-{suffix}",
        "message_id": f"g07-message-{correlation_id}",
        "thread_id": f"g07-thread-{suffix}",
        "correlation_id": correlation_id,
        "call_kind": operator_kind,
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _post(
    client: TestClient,
    path: str,
    body: dict[str, Any],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.post(path, json=body, headers=headers or OPENCLAW_HEADERS)
    assert response.status_code == status, response.text
    return response.json()


def _grant(client: TestClient, *, member: str = A, request_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/member/v1/cos/delegations",
        headers=A_HEADERS if member == A else B_HEADERS,
        json={
            "request_id": request_id,
            "context_scope": "member_private",
            "permissions": [
                "read_context",
                "create_internal_work",
                "manage_internal_work",
                "manage_member_obligations",
                "coordinate_household_work",
                "prepare_external_actions",
                "execute_fixture_actions",
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _report_health(client: TestClient, state: str = "healthy") -> dict[str, Any]:
    actors = {
        "fixture|g07-account-a|g07-actor-a": A,
        "fixture|g07-account-b|g07-actor-b": B,
    }
    return _post(
        client,
        "/api/openclaw/v1/health/report",
        {
            "state": state,
            "reason": f"G07 fixture is {state}",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "dm_scope": "per_account_channel_peer",
            "member_agent_ids": sorted(member_agent_id(member) for member in {A, B}),
            "binding_fingerprints": list(binding_fingerprints(actors, frozenset())),
            "unique_agents": True,
            "unique_accounts": True,
        },
    )


def _allow_once(
    client: TestClient,
    *,
    receipt_id: str,
    params: dict[str, Any],
) -> str:
    fingerprint = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    issued_at_ms = int(time.time() * 1000)
    expires_at_ms = issued_at_ms + 120_000
    signature = sign_approval_receipt(
        SECRET,
        receipt_id=receipt_id,
        tool_name="administrateme_execute_delegated_action",
        request_fingerprint=fingerprint,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        openclaw_version=OPENCLAW_VERSION,
        openclaw_commit=OPENCLAW_COMMIT,
    )
    _post(
        client,
        "/api/openclaw/v1/approvals/resolve",
        {
            "receipt_id": receipt_id,
            "tool_name": "administrateme_execute_delegated_action",
            "request_fingerprint": fingerprint,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            "decision": "allow-once",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "signature": signature,
        },
    )
    return receipt_id


def _create_canonical_task(
    client: TestClient, *, request_id: str, title: str = "Fixture canonical work"
) -> dict[str, Any]:
    return _post(
        client,
        "/api/openclaw/v1/tools/service-work",
        {
            "provenance": _provenance(request_id),
            "request_id": request_id,
            "context_scope": "member_private",
            "service_assignment_role": "manages_member_obligation",
            "service_work_type": "operational_task",
            "title": title,
        },
        status=201,
    )


def _action_payload(action_class: str) -> dict[str, Any]:
    spec = ACTION_SPECS[action_class]  # type: ignore[index]
    if spec.operation in {"complete_task", "complete_reminder"}:
        return {"completed": True}
    if spec.operation in {"uncomplete_task", "uncomplete_reminder"}:
        return {"completed": False}
    if spec.operation in {
        "delete_event",
        "delete_task",
        "delete_list",
        "delete_reminder",
    }:
        return {}
    if spec.provider_family in {"google_calendar", "apple_calendar"}:
        return (
            {
                "summary": f"Fixture {action_class}",
                "start": "2031-01-02T15:00:00Z",
                "end": "2031-01-02T16:00:00Z",
                "recurrence": ["RRULE:FREQ=WEEKLY"],
            }
            if spec.recurrence
            else {
                "summary": f"Fixture {action_class}",
                "start": "2031-01-02T15:00:00Z",
                "end": "2031-01-02T16:00:00Z",
            }
        )
    return {"title": f"Fixture {action_class}", "notes": "G07 exact effect"}


def _prepare_body(
    *,
    request_id: str,
    task: dict[str, Any],
    action_class: str,
    operator_kind: str = "openclaw_automation",
    destination_id: str | None = None,
    desired_representation_id: str | None = None,
    facts_observed_at: datetime | None = None,
    facts_valid_until: datetime | None = None,
    expires_at: datetime | None = None,
    risk_class: str = "ordinary",
    execution_mode: str = "execute",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    spec = ACTION_SPECS[action_class]  # type: ignore[index]
    recurrence = "provider_series" if spec.recurrence else "none"
    return {
        "provenance": _provenance(request_id),
        "request_id": request_id,
        "context_scope": "member_private",
        "action_class": action_class,
        "canonical_object_kind": "task",
        "canonical_object_id": task["task_id"],
        "series_id": f"series:{request_id}" if spec.recurrence else None,
        "recurrence_strategy": recurrence,
        "desired_representation_id": desired_representation_id
        or f"representation:{request_id}",
        "operator_kind": operator_kind,
        "provider_account_id": f"fixture-account:member:{A}",
        "provider_account_owner_principal_id": A,
        "destination_id": destination_id or f"fixture:member:{A}:primary",
        "audience_ids": [A],
        "representation_kind": "member_delegated",
        "target_external_id": f"fixture-target:{request_id}"
        if spec.requires_target
        else None,
        "expected_provider_revision": None,
        "desired_payload": _action_payload(action_class),
        "fact_event_ids": [task["event_id"]],
        "facts_observed_at": (facts_observed_at or now).isoformat(),
        "facts_valid_until": (
            facts_valid_until or now + timedelta(minutes=4)
        ).isoformat(),
        "risk_class": risk_class,
        "execution_mode": execution_mode,
        "expires_at": (expires_at or now + timedelta(minutes=15)).isoformat(),
        "expected_policy_digest": execution_policy_digest(),
    }


def _prepare(
    client: TestClient,
    body: dict[str, Any],
    *,
    status: int = 201,
    bind_current_fact: bool = True,
) -> dict[str, Any]:
    if bind_current_fact and body.get("canonical_object_kind") == "task":
        runtime = client.app.state.runtime
        row = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT last_event_id FROM tasks WHERE tenant_id=? AND task_id=?",
                (runtime.settings.instance.tenant_id, body["canonical_object_id"]),
            )
            .fetchone()
        )
        assert row is not None
        event_id = str(row["last_event_id"])
        event = client.portal.call(runtime.event_log.get, event_id)
        assert event is not None
        observed_at = datetime.fromisoformat(
            str(event["occurred_at"]).replace("Z", "+00:00")
        )
        body["fact_event_ids"] = [event_id]
        body["facts_observed_at"] = observed_at.isoformat()
        body["facts_valid_until"] = (observed_at + timedelta(minutes=4)).isoformat()
    return _post(
        client,
        "/api/openclaw/v1/tools/delegated-actions/prepare",
        body,
        status=status,
    )


def _action(runtime: Any, action_id: str) -> dict[str, Any]:
    from adminme.lib.session import Session

    item = get_delegated_action(
        runtime.projections.connection("tasks"),
        Session(
            tenant_id=runtime.settings.instance.tenant_id,
            auth_member_id=A,
            auth_role="principal",
            view_member_id=A,
            view_role="principal",
            dm_scope="per_account_channel_peer",
            source="member_portal",
        ),
        action_id=action_id,
    )
    assert item is not None
    return item


def _wait_action(runtime: Any, action_id: str, states: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        item = _action(runtime, action_id)
        if item["state"] in states:
            return item
        time.sleep(0.01)
    raise AssertionError(
        f"delegated action did not reach {sorted(states)}: {_action(runtime, action_id)}"
    )


def _approve_one_off(
    client: TestClient, runtime: Any, prepared: dict[str, Any], *, suffix: str
) -> dict[str, Any]:
    action = _action(runtime, prepared["action_id"])
    return _post(
        client,
        f"/api/member/v1/cos/delegated-actions/{prepared['action_id']}/approve",
        {
            "request_id": f"approve-{suffix}",
            "expected_last_event_id": action["last_event_id"],
            "expected_action_fingerprint": action["action_fingerprint"],
        },
        headers=A_HEADERS,
    )


def _execute(
    client: TestClient,
    runtime: Any,
    prepared: dict[str, Any],
    *,
    suffix: str,
    operator_kind: str = "openclaw_automation",
    add_receipt: bool = True,
    status: int = 202,
    expected_policy_digest: str | None = None,
) -> dict[str, Any]:
    action = _action(runtime, prepared["action_id"])
    params = {
        "request_id": f"execute-{suffix}",
        "context_scope": "member_private",
        "action_id": action["action_id"],
        "expected_last_event_id": action["last_event_id"],
        "expected_action_fingerprint": action["action_fingerprint"],
        "expected_policy_digest": expected_policy_digest or execution_policy_digest(),
        "operator_kind": operator_kind,
    }
    body = {"provenance": _provenance(f"execute-{suffix}"), **params}
    if add_receipt:
        body["openclaw_approval_receipt_id"] = _allow_once(
            client, receipt_id=f"g07-receipt-{suffix}", params=params
        )
    return _post(
        client,
        "/api/openclaw/v1/tools/delegated-actions/execute",
        body,
        status=status,
    )


def _observe(
    client: TestClient, runtime: Any, action_id: str, *, suffix: str
) -> dict[str, Any]:
    action = _wait_action(runtime, action_id, {"awaiting_observation"})
    return _post(
        client,
        "/api/openclaw/v1/tools/delegated-actions/observe",
        {
            "provenance": _provenance(f"observe-{suffix}"),
            "request_id": f"observe-{suffix}",
            "context_scope": "member_private",
            "action_id": action_id,
            "expected_last_event_id": action["last_event_id"],
        },
    )


async def _provider_effect_count(runtime: Any) -> int:
    count = 0
    async for event in runtime.event_log.read_since(
        types=["external_sync.write_completed", "external_sync.write_failed"]
    ):
        if event["payload"].get("provider_called"):
            count += 1
    return count


def test_g07_supported_fixture_actions_close_exactly_through_observation(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        _grant(client, request_id="g07-supported-grant")
        _report_health(client)
        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:test",
            "credential-free G07 process acceptance",
        )
        task = _create_canonical_task(client, request_id="g07-supported-task")
        supported = [
            action_class
            for action_class, spec in ACTION_SPECS.items()
            if spec.fixture_executable
        ]
        assert supported
        for index, action_class in enumerate(supported):
            suffix = f"supported-{index}-{action_class}"
            prepared = _prepare(
                client,
                _prepare_body(
                    request_id=f"prepare-{suffix}",
                    task=task,
                    action_class=action_class,
                ),
            )
            assert prepared["state"] == "awaiting_member_approval"
            approval = _approve_one_off(client, runtime, prepared, suffix=suffix)
            assert approval["openclaw_permission_included"] is False
            executed = _execute(client, runtime, prepared, suffix=suffix)
            assert executed["domain_approval_event_id"] == approval["event_id"]
            terminal = _wait_action(
                runtime, prepared["action_id"], {"awaiting_observation"}
            )
            assert terminal["results"][0]["provider_called"] == 0
            result = _observe(client, runtime, prepared["action_id"], suffix=suffix)
            assert result["state"] == "reconciled"
            closed = _action(runtime, prepared["action_id"])
            assert closed["state"] == "reconciled"
            assert (
                closed["reconciliations"][0]["desired_state_digest"]
                == closed["reconciliations"][0]["observed_state_digest"]
            )
            assert {
                "awaiting_member_approval",
                "awaiting_openclaw_approval",
                "dispatched",
                "awaiting_result",
                "awaiting_observation",
                "active",
                "reconciled",
            } <= {row["state"] for row in closed["state_history"]}

        # Service-owned operational representations and variable occurrences
        # use the same approvals/results, but never a member destination.
        service_body = _prepare_body(
            request_id="g07-service-occurrence",
            task=task,
            action_class="google_calendar_create",
        )
        service_body.update(
            {
                "occurrence_id": "occurrence:g07-variable-1",
                "recurrence_strategy": "individual_occurrence",
                "representation_kind": "cos_operational",
                "provider_account_id": "fixture-account:service:chief-of-staff",
                "provider_account_owner_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "destination_id": "fixture:service:chief-of-staff:calendar",
            }
        )
        service = _prepare(client, service_body)
        _approve_one_off(client, runtime, service, suffix="service-occurrence")
        _execute(client, runtime, service, suffix="service-occurrence")
        _observe(client, runtime, service["action_id"], suffix="service-occurrence")

        adapter = runtime.fixture_action_adapter
        assert adapter is not None
        assert len(adapter.effects) == len(supported) + 1
        for effect in adapter.effects:
            action = _action(runtime, effect["action_id"])
            assert effect["action_fingerprint"] == action["action_fingerprint"]
            assert effect["destination_id"] == action["destination_id"]
            assert effect["provider_account_id"] == action["provider_account_id"]
            assert (
                effect["provider_account_owner_principal_id"]
                == action["provider_account_owner_principal_id"]
            )
            assert effect["desired_state"] == action["desired_state"]
            assert (
                effect["domain_approval_event_id"] == action["domain_approval_event_id"]
            )
            assert (
                effect["openclaw_approval_event_id"]
                == action["openclaw_approval_event_id"]
            )
        assert client.portal.call(_provider_effect_count, runtime) == 0

        # Completion and reopening are explicit canonical outcome events.
        canonical = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?",
                (runtime.settings.instance.tenant_id, task["task_id"]),
            )
            .fetchone()
        )
        assert canonical is not None
        assert canonical["lifecycle_action"] == "reopen_from_delegated_provider"
        assert canonical["lifecycle_actor_party_id"] == COS_PARTY_ID
        assert canonical["lifecycle_authority_grant_id"]

        # Replay preserves the separate lineage and stable recurrence identity.
        client.portal.call(runtime.projections.rebuild, "tasks")
        recurrence = next(
            item
            for item in (
                _action(runtime, effect["action_id"]) for effect in adapter.effects
            )
            if item["recurrence_strategy"] == "provider_series"
        )
        assert recurrence["series_id"]
        assert recurrence["occurrence_id"] is None
        assert recurrence["reconciliations"][0]["outcome"] == "reconciled"
        individual = _action(runtime, service["action_id"])
        assert individual["recurrence_strategy"] == "individual_occurrence"
        assert individual["occurrence_id"] == "occurrence:g07-variable-1"
        assert individual["series_id"] is None
        assert (
            individual["provider_account_owner_principal_id"]
            == COS_SERVICE_PRINCIPAL_ID
        )
        assert individual["destination_id"].startswith(
            "fixture:service:chief-of-staff:"
        )


def test_g07_observation_duplicate_missing_result_drift_and_manual_operator(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        _grant(client, request_id="g07-edge-grant")
        _report_health(client)
        task = _create_canonical_task(client, request_id="g07-edge-task")

        # New instances are observation-only: the normal dispatch is suppressed.
        suppressed = _prepare(
            client,
            _prepare_body(
                request_id="g07-suppressed-prepare",
                task=task,
                action_class="google_calendar_create",
            ),
        )
        _approve_one_off(client, runtime, suppressed, suffix="suppressed")
        _execute(client, runtime, suppressed, suffix="suppressed")
        suppressed_action = _wait_action(
            runtime, suppressed["action_id"], {"suppressed"}
        )
        assert suppressed_action["results"][0]["suppressed"] == 1
        assert runtime.fixture_action_adapter.effects == []

        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:test",
            "credential-free G07 process acceptance",
        )

        # A missing terminal result stays awaiting-result and cannot be retried.
        missing = _prepare(
            client,
            _prepare_body(
                request_id="g07-missing-result-prepare",
                task=task,
                action_class="google_tasks_create",
            ),
        )
        _approve_one_off(client, runtime, missing, suffix="missing-result")
        runtime.fixture_action_adapter.suppress_terminal_for.add(missing["action_id"])
        first = _execute(client, runtime, missing, suffix="missing-result")
        unresolved = _wait_action(runtime, missing["action_id"], {"awaiting_result"})
        retry = _execute(client, runtime, missing, suffix="missing-result-retry")
        assert retry["duplicate"] is True
        assert retry["event_id"] == first["event_id"]
        assert unresolved["results"] == []
        assert (
            len(
                [
                    effect
                    for effect in runtime.fixture_action_adapter.effects
                    if effect["action_id"] == missing["action_id"]
                ]
            )
            == 1
        )

        # The simulated individual operator uses the identical authority path.
        manual = _prepare(
            client,
            _prepare_body(
                request_id="g07-manual-prepare",
                task=task,
                action_class="google_calendar_update",
                operator_kind="simulated_human",
            ),
        )
        _approve_one_off(client, runtime, manual, suffix="manual")
        _execute(
            client,
            runtime,
            manual,
            suffix="manual",
            operator_kind="simulated_human",
        )
        _wait_action(runtime, manual["action_id"], {"awaiting_observation"})
        runtime.fixture_action_adapter.direct_edit(
            manual["action_id"],
            {
                "summary": "Direct provider change",
                "destination_id": f"fixture:member:{A}:primary",
            },
        )
        drift = _observe(client, runtime, manual["action_id"], suffix="manual-drift")
        assert drift["state"] == "conflicted"
        manual_action = _action(runtime, manual["action_id"])
        assert manual_action["operator_kind"] == "simulated_human"
        assert (
            manual_action["operator_principal_id"]
            == "operator:fixture:individual-human"
        )
        assert (
            manual_action["reconciliations"][0]["reason_code"] == "direct_provider_edit"
        )

        non_edit_drift = _prepare(
            client,
            _prepare_body(
                request_id="g07-provider-drift",
                task=task,
                action_class="google_tasks_update",
            ),
        )
        _approve_one_off(client, runtime, non_edit_drift, suffix="provider-drift")
        _execute(client, runtime, non_edit_drift, suffix="provider-drift")
        _wait_action(runtime, non_edit_drift["action_id"], {"awaiting_observation"})
        runtime.fixture_action_adapter.drift(
            non_edit_drift["action_id"],
            {
                "title": "Provider-normalized title",
                "destination_id": f"fixture:member:{A}:primary",
            },
        )
        drifted = _observe(
            client,
            runtime,
            non_edit_drift["action_id"],
            suffix="provider-drift",
        )
        assert drifted["state"] == "drifted"

        conflict_body = _prepare_body(
            request_id="g07-provider-revision-conflict",
            task=task,
            action_class="google_calendar_update",
        )
        conflict_body["expected_provider_revision"] = "fixture-rev:999"
        conflict = _prepare(client, conflict_body)
        _approve_one_off(client, runtime, conflict, suffix="provider-conflict")
        _execute(client, runtime, conflict, suffix="provider-conflict")
        conflicted = _wait_action(runtime, conflict["action_id"], {"conflicted"})
        assert conflicted["results"][0]["error_kind"] == "conflict"
        assert conflicted["results"][0]["retryable"] == 1

        failed = _prepare(
            client,
            _prepare_body(
                request_id="g07-provider-failure",
                task=task,
                action_class="google_tasks_create",
            ),
        )
        _approve_one_off(client, runtime, failed, suffix="provider-failure")
        runtime.fixture_action_adapter.fail_for.add(failed["action_id"])
        _execute(client, runtime, failed, suffix="provider-failure")
        failed_action = _wait_action(runtime, failed["action_id"], {"failed"})
        assert failed_action["results"][0]["error_kind"] == "provider"
        assert client.portal.call(_provider_effect_count, runtime) == 0


def test_g07_exact_authority_and_failure_states_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        grant = _grant(client, request_id="g07-fail-closed-grant")
        _grant(client, member=B, request_id="g07-fail-closed-grant-b")
        _report_health(client)
        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:test",
            "credential-free G07 process acceptance",
        )
        task = _create_canonical_task(client, request_id="g07-fail-closed-task")

        # Unsupported accepted architectures remain dashboard-only; ceilings refuse.
        dashboard = _prepare(
            client,
            _prepare_body(
                request_id="g07-apple-held",
                task=task,
                action_class="apple_calendar_create",
            ),
        )
        assert dashboard["state"] == "dashboard_only"
        prohibited = _prepare(
            client,
            _prepare_body(
                request_id="g07-hard-ceiling",
                task=task,
                action_class="google_calendar_create",
                risk_class="money",
            ),
        )
        assert prohibited["state"] == "prohibited"
        shadow = _prepare(
            client,
            _prepare_body(
                request_id="g07-shadow",
                task=task,
                action_class="google_calendar_create",
                execution_mode="shadow",
            ),
        )
        assert shadow["state"] == "shadow"
        desired = _prepare(
            client,
            _prepare_body(
                request_id="g07-desired",
                task=task,
                action_class="google_calendar_create",
                execution_mode="desired_only",
            ),
        )
        assert desired["state"] == "desired"

        # No adult can authorize another adult's private provider or destination.
        foreign = _prepare_body(
            request_id="g07-wrong-owner",
            task=task,
            action_class="google_calendar_create",
        )
        foreign["provider_account_owner_principal_id"] = B
        assert _prepare(client, foreign, status=403)["detail"]
        cross_destination = _prepare_body(
            request_id="g07-wrong-destination",
            task=task,
            action_class="google_calendar_create",
            destination_id="fixture:service:chief-of-staff:calendar",
        )
        assert _prepare(client, cross_destination, status=403)["detail"]

        # Stale facts and changed policy never create executable intent.
        now = datetime.now(timezone.utc)
        stale = _prepare_body(
            request_id="g07-stale-facts",
            task=task,
            action_class="google_calendar_create",
            facts_observed_at=now - timedelta(minutes=10),
            facts_valid_until=now - timedelta(minutes=1),
        )
        assert _prepare(client, stale, status=409, bind_current_fact=False)["detail"]
        changed = _prepare_body(
            request_id="g07-changed-policy",
            task=task,
            action_class="google_calendar_create",
        )
        changed["expected_policy_digest"] = "0" * 64
        assert _prepare(client, changed, status=409)["detail"]

        changed_fact = _prepare(
            client,
            _prepare_body(
                request_id="g07-canonical-change",
                task=task,
                action_class="google_calendar_create",
            ),
        )
        _approve_one_off(client, runtime, changed_fact, suffix="canonical-change")
        task_row = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT last_event_id FROM tasks WHERE tenant_id=? AND task_id=?",
                (runtime.settings.instance.tenant_id, task["task_id"]),
            )
            .fetchone()
        )
        assert task_row is not None
        _post(
            client,
            f"/api/openclaw/v1/tools/service-work/{task['task_id']}/state",
            {
                "provenance": _provenance("g07-canonical-state-change"),
                "request_id": "g07-canonical-state-change",
                "context_scope": "member_private",
                "expected_last_event_id": task_row["last_event_id"],
                "action": "start",
                "reason_code": "other",
            },
        )
        assert _execute(
            client,
            runtime,
            changed_fact,
            suffix="canonical-change",
            status=403,
        )["detail"]

        # Standing envelopes bind every effect coordinate, including target/revision.
        standing_body = _prepare_body(
            request_id="g07-standing-prepare",
            task=task,
            action_class="google_tasks_update",
        )
        envelope_request = {
            "request_id": "g07-standing-envelope",
            "envelope_id": "g07-standing-envelope",
            "authority_grant_id": grant["grant_id"],
            "action_class": standing_body["action_class"],
            "canonical_object_kind": standing_body["canonical_object_kind"],
            "canonical_object_id": standing_body["canonical_object_id"],
            "recurrence_strategy": standing_body["recurrence_strategy"],
            "desired_representation_id": standing_body["desired_representation_id"],
            "provider_account_id": standing_body["provider_account_id"],
            "provider_account_owner_principal_id": standing_body[
                "provider_account_owner_principal_id"
            ],
            "destination_id": standing_body["destination_id"],
            "audience_ids": standing_body["audience_ids"],
            "representation_kind": standing_body["representation_kind"],
            "target_external_id": standing_body["target_external_id"],
            "expected_provider_revision": standing_body["expected_provider_revision"],
            "desired_payload": standing_body["desired_payload"],
            "expires_at": (now + timedelta(minutes=20)).isoformat(),
        }
        envelope = _post(
            client,
            "/api/member/v1/cos/execution-envelopes",
            envelope_request,
            status=201,
            headers=A_HEADERS,
        )
        assert envelope["item"]["openclaw_permission_included"] == 0
        standing = _prepare(client, standing_body)
        assert standing["standing_match_count"] == 1
        assert standing["state"] == "awaiting_openclaw_approval"

        mismatch_body = _prepare_body(
            request_id="g07-standing-mismatch",
            task=task,
            action_class="google_tasks_update",
            desired_representation_id="representation:not-authorized",
        )
        mismatch = _prepare(client, mismatch_body)
        assert mismatch["standing_match_count"] == 0
        assert mismatch["state"] == "awaiting_member_approval"

        # Domain authority never supplies OpenClaw permission.
        assert _execute(
            client,
            runtime,
            standing,
            suffix="domain-only",
            add_receipt=False,
            status=403,
        )["detail"]

        # Revocation and runtime degradation invalidate even exact standing approval.
        _post(
            client,
            "/api/member/v1/cos/execution-envelopes/g07-standing-envelope/revoke",
            {"request_id": "g07-revoke-envelope", "reason_code": "member_request"},
            headers=A_HEADERS,
        )
        assert _execute(
            client,
            runtime,
            standing,
            suffix="revoked-envelope",
            status=403,
        )["detail"]

        degraded = _prepare(
            client,
            _prepare_body(
                request_id="g07-degraded-prepare",
                task=task,
                action_class="google_calendar_create",
            ),
        )
        _approve_one_off(client, runtime, degraded, suffix="degraded")
        assert _execute(
            client,
            runtime,
            degraded,
            suffix="changed-policy-at-dispatch",
            expected_policy_digest="0" * 64,
            status=403,
        )["detail"]
        _report_health(client, "degraded")
        assert _execute(
            client,
            runtime,
            degraded,
            suffix="degraded",
            status=403,
        )["detail"]
        _report_health(client, "healthy")

        # An exact OpenClaw approval cannot create missing domain authority.
        missing_domain = _prepare(
            client,
            _prepare_body(
                request_id="g07-missing-domain",
                task=task,
                action_class="google_calendar_create",
            ),
        )
        allow_always = client.post(
            "/api/openclaw/v1/approvals/resolve",
            headers=OPENCLAW_HEADERS,
            json={
                "receipt_id": "g07-allow-always",
                "tool_name": "administrateme_execute_delegated_action",
                "request_fingerprint": "0" * 64,
                "issued_at_ms": int(time.time() * 1000),
                "expires_at_ms": int(time.time() * 1000) + 120_000,
                "decision": "allow-always",
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
                "signature": "0" * 64,
            },
        )
        assert allow_always.status_code == 422
        assert _execute(
            client,
            runtime,
            missing_domain,
            suffix="openclaw-only",
            status=403,
        )["detail"]

        # Two exact envelopes are ambiguous and therefore do not auto-approve.
        for suffix in ("a", "b"):
            duplicate_envelope = {
                **envelope_request,
                "request_id": f"g07-ambiguous-envelope-{suffix}",
                "envelope_id": f"g07-ambiguous-envelope-{suffix}",
                "desired_representation_id": "representation:g07-ambiguous",
                "action_class": "google_calendar_create",
                "target_external_id": None,
                "desired_payload": _action_payload("google_calendar_create"),
            }
            _post(
                client,
                "/api/member/v1/cos/execution-envelopes",
                duplicate_envelope,
                status=201,
                headers=A_HEADERS,
            )
        ambiguous_body = _prepare_body(
            request_id="g07-ambiguous",
            task=task,
            action_class="google_calendar_create",
            desired_representation_id="representation:g07-ambiguous",
        )
        ambiguous = _prepare(client, ambiguous_body)
        assert ambiguous["standing_match_count"] == 2
        assert ambiguous["state"] == "awaiting_member_approval"

        # Hold only the domain API clock across prepare/approval, then cross
        # the exact expiry boundary. Real API scheduling and projection waits
        # cannot consume the 50ms window before approval. OpenClaw signatures,
        # receipt clocks, governance, and the provider boundary remain real.
        clock = [datetime.now(timezone.utc)]

        class ExecutionClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return (
                    clock[0].replace(tzinfo=None)
                    if tz is None
                    else clock[0].astimezone(tz)
                )

        with monkeypatch.context() as expiry_clock:
            expiry_clock.setattr("adminme.products.member.cos.datetime", ExecutionClock)
            expiry_clock.setattr(
                "adminme.products.openclaw.main.datetime", ExecutionClock
            )
            expires_at = clock[0] + timedelta(milliseconds=50)
            expiring = _prepare(
                client,
                _prepare_body(
                    request_id="g07-expiring",
                    task=task,
                    action_class="google_calendar_create",
                    expires_at=expires_at,
                ),
            )
            approved = _approve_one_off(client, runtime, expiring, suffix="expiring")
            assert approved["state"] == "awaiting_openclaw_approval"
            before_expiry = _action(runtime, expiring["action_id"])
            assert datetime.fromisoformat(before_expiry["expires_at"]) == expires_at
            assert (
                datetime.fromisoformat(before_expiry["approvals"][0]["approved_at"])
                == clock[0]
            )
            clock[0] = expires_at
            assert _execute(
                client,
                runtime,
                expiring,
                suffix="expired",
                status=403,
            )["detail"] == {
                "code": "write_denied",
                "layer": "governance",
                "reason": "action_expired",
            }
            after_expiry = _action(runtime, expiring["action_id"])
            assert after_expiry["state"] == "awaiting_openclaw_approval"
            assert after_expiry["last_event_id"] == before_expiry["last_event_id"]
            assert after_expiry["command_event_id"] is None

        # Pending intent can still be withdrawn after the scoped clock restores.
        pending = _action(runtime, mismatch["action_id"])
        withdrawn = _post(
            client,
            f"/api/member/v1/cos/delegated-actions/{mismatch['action_id']}/withdraw",
            {
                "request_id": "g07-withdraw-pending",
                "expected_last_event_id": pending["last_event_id"],
                "reason_code": "no_longer_needed",
            },
            headers=A_HEADERS,
        )
        assert withdrawn["state"] == "withdrawn"
        assert client.portal.call(_provider_effect_count, runtime) == 0
        assert runtime.fixture_action_adapter.effects == []

        # Revoking the service grant closes both automation and manual paths.
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant['grant_id']}",
            headers=A_HEADERS,
            json={"request_id": "g07-revoke-service-grant", "reason": "fixture"},
        )
        assert revoked.status_code == 200, revoked.text
        after_revoke = _prepare_body(
            request_id="g07-after-grant-revocation",
            task=task,
            action_class="google_calendar_create",
        )
        assert _prepare(client, after_revoke, status=403)["detail"]

        assert COS_SERVICE_PRINCIPAL_ID != A
