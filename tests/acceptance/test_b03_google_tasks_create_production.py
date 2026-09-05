"""Process acceptance for B03's single production Google Tasks effect."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from adminme.adapters.base import AdapterContext, AdapterStatus
from adminme.events.envelope import EventEnvelope
from adminme.google_tasks_execution import canonical_sha256, task_content_fingerprint
from adminme.lib.adapters import WriteOperation, WriteResult
from adminme.lib.observation import outbound
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.openclaw_topology import binding_fingerprints, member_agent_id
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID


REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET = "b03-loopback-secret-at-least-32-bytes"
KEY = b"3" * 32
MEMBER = "member-b03-principal"
MEMBER_HEADERS = {"Tailscale-User-Login": "b03-principal.ts.net"}
OPENCLAW_HEADERS = {"Authorization": f"Bearer {SECRET}"}
ACCOUNT = "provider-account:" + "a" * 32
DESTINATION = "task-list:dedicated-b03-existing"


class _FakeGoogleTasksAdapter:
    name = "tasks-google-tasks"
    family = "tasks"
    version = "b03-test"
    sensitivity_floor = "normal"
    write_capabilities = ["create_task"]
    instance_name = "b03-google-tasks"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._ctx: AdapterContext | None = None
        self._stopped = asyncio.Event()
        self.effects: list[dict[str, Any]] = []

    async def start(self, *, ctx: AdapterContext) -> None:
        self._ctx = ctx
        await self._stopped.wait()

    async def stop(self) -> None:
        self._stopped.set()

    def status(self) -> AdapterStatus:
        return AdapterStatus(state="running")

    async def apply_write(self, operation: WriteOperation) -> WriteResult:
        assert self._ctx is not None
        assert operation.kind == "create_task"
        assert operation.payload["tasklist_id"] == DESTINATION

        async def contact() -> dict[str, Any]:
            self.effects.append(dict(operation.payload))
            return {
                "id": "native-b03-task-1",
                "etag": "provider-revision-b03-1",
                "status": "needsAction",
                "title": operation.payload["title"],
                "notes": operation.payload.get("notes"),
            }

        sent = await outbound(
            self._ctx.observation_session,
            "tasks.create_task",
            {"tasklist_id": DESTINATION, "fields": ["notes", "title"]},
            contact,
            manager=self._ctx.observation_manager,
            event_sink=self._ctx.observation_event_sink,
            target_channel=self.name,
            target_identifier=DESTINATION,
        )
        if sent.suppressed:
            return WriteResult(applied=False, suppressed=True)
        response = sent.result
        return WriteResult(
            applied=True,
            external_id=response["id"],
            provider_revision=response["etag"],
            provider_status=response["status"],
            returned_state={"title": response["title"], "notes": response["notes"]},
        )


def _provenance(correlation: str) -> dict[str, Any]:
    return {
        "actor_id": "b03-actor",
        "agent_id": "b03-agent",
        "session_key": "b03-session-key",
        "session_id": "b03-session",
        "channel_id": "fixture",
        "account_id": "b03-account",
        "message_id": f"message-{correlation}",
        "thread_id": "b03-thread",
        "correlation_id": correlation,
        "call_kind": "tool",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _settings(instance: Path) -> tuple[RuntimeSettings, dict[str, Any]]:
    fixture = yaml.safe_load(
        (REPO_ROOT / "config/cos-service-sources.example.yaml").read_text()
    )
    task_config = dict(
        next(
            row["config"]
            for row in fixture["adapters"]
            if row["kind"] == "tasks-google-tasks"
        )
    )
    task_config.update(
        {
            "provider_account_id": ACCOUNT,
            "user_email": ACCOUNT,
            "tasklist_id": DESTINATION,
            "read_only": False,
            "enable_writes": True,
            "permission_scope": ["https://www.googleapis.com/auth/tasks"],
            "b03_google_tasks_create": {
                "action_class": "google_tasks_create",
                "represented_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "provider_account_id": ACCOUNT,
                "destination_task_list_id": DESTINATION,
                "destination_preexisting": True,
                "permitted_operation": "create_task",
                "required_permission_scope": "https://www.googleapis.com/auth/tasks",
                "context_scope": "member_private",
                "on_behalf_of": MEMBER,
                "resource_owner_principal_id": MEMBER,
                "state": "trial",
                "revision": 1,
                "review_provenance": "fixture:b03-reviewed-binding",
            },
        }
    )
    adapter_config = {
        "service_identities": {
            "principals": fixture["service_identities"]["principals"],
            "channels": [],
        },
        "adapter_supervisor_port": 0,
        "adapters": [
            {
                "id": "b03-google-tasks",
                "kind": "tasks-google-tasks",
                "enabled": True,
                "config": task_config,
            }
        ],
    }
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=KEY,
        repository_root=REPO_ROOT,
        member_identities={"b03-principal.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "B03 fixture household",
            "members": [
                {"member_id": MEMBER, "label": "B03 adult", "role": "principal"}
            ],
            "memberships": [
                {
                    "membership_id": "b03-membership",
                    "party_id": MEMBER,
                    "role": "principal",
                }
            ],
            "relationships": [],
        },
        adapter_config=adapter_config,
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={"fixture|b03-account|b03-actor": MEMBER},
    )
    return settings, task_config


def _agreement_rule() -> dict[str, Any]:
    return {
        "rule_id": "b03-google-tasks-create-rule-v1",
        "acting_principal_ids": [COS_SERVICE_PRINCIPAL_ID],
        "represented_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "active_member_ids": [MEMBER],
        "on_behalf_of_member_ids": [MEMBER],
        "resource_owner_principal_ids": [MEMBER],
        "provider_account_owner_principal_ids": [COS_SERVICE_PRINCIPAL_ID],
        "responsible_party_ids": [COS_PARTY_ID],
        "beneficiary_party_ids": [MEMBER],
        "purpose": "One supervised Google Tasks creation trial",
        "domains": ["household_operations"],
        "trigger": "Exact member-approved B03 command",
        "object_kinds": ["task"],
        "provider_ids": ["google_tasks"],
        "provider_account_references": [ACCOUNT],
        "workspace_references": ["service"],
        "resource_references": [MEMBER],
        "destination_ids": [DESTINATION],
        "audience_ids": [MEMBER],
        "required_fact_types": ["domain_approval"],
        "maximum_fact_age_seconds": 300,
        "required_fact_coverage": "exact",
        "permitted_observations": ["normal_google_tasks_source"],
        "permitted_internal_processing": ["prepare_exact_command"],
        "permitted_disclosures": ["content_fingerprint"],
        "permitted_proposal_preparation": ["prepare", "hold"],
        "prohibitions": ["all_other_provider_writes", "automatic_retry_after_contact"],
        "independent_openclaw_approval_required": True,
        "ceilings": {
            "financial_micros": 0,
            "maximum_frequency_count": 1,
            "frequency_period": "occurrence",
            "maximum_quantity": 1,
            "permitted_destination_ids": [DESTINATION],
        },
        "verification_requirements": ["exact_content_fingerprint"],
        "reconciliation_requirements": ["independent_later_observation"],
        "trust_scope": {
            "granting_member_ids": [MEMBER],
            "capability_ids": ["google_tasks_create"],
            "domains": ["household_operations"],
            "providers": ["google_tasks"],
            "provider_account_references": [ACCOUNT],
            "action_verbs": ["create_task"],
            "destination_ids": [DESTINATION],
            "sensitivity_levels": ["normal"],
            "stage": "supervised_trial",
        },
        "provider_execution_permitted": False,
        "source_policy_embedded": False,
    }


def _allow_once(client: TestClient, params: dict[str, Any]) -> str:
    fingerprint = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    issued = int(time.time() * 1000)
    expires = issued + 120_000
    receipt_id = "b03-openclaw-allow-once"
    response = client.post(
        "/api/openclaw/v1/approvals/resolve",
        headers=OPENCLAW_HEADERS,
        json={
            "receipt_id": receipt_id,
            "tool_name": "administrateme_execute_google_tasks_create_once",
            "request_fingerprint": fingerprint,
            "issued_at_ms": issued,
            "expires_at_ms": expires,
            "decision": "allow-once",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "signature": sign_approval_receipt(
                SECRET,
                receipt_id=receipt_id,
                tool_name="administrateme_execute_google_tasks_create_once",
                request_fingerprint=fingerprint,
                issued_at_ms=issued,
                expires_at_ms=expires,
                openclaw_version=OPENCLAW_VERSION,
                openclaw_commit=OPENCLAW_COMMIT,
            ),
        },
    )
    assert response.status_code == 200, response.text
    return receipt_id


def _wait_row(runtime: Any, command_id: str, state: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT * FROM cos_google_tasks_execution_v2 WHERE tenant_id=? AND command_id=?",
                (runtime.settings.instance.tenant_id, command_id),
            )
            .fetchone()
        )
        if row is not None and row["state"] == state:
            return dict(row)
        time.sleep(0.01)
    raise AssertionError(f"B03 command did not reach {state}")


def test_b03_one_effect_later_observation_reconciliation_and_revocation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings, task_config = _settings(instance)
    fake = _FakeGoogleTasksAdapter(task_config)
    monkeypatch.setattr(
        "adminme.runtime.application.load_builtin_adapters",
        lambda *_args, **_kwargs: [fake],
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        grant = client.post(
            "/api/member/v1/cos/delegations",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "b03-exact-delegation",
                "context_scope": "member_private",
                "permissions": [
                    "read_context",
                    "prepare_external_actions",
                    "execute_google_tasks_create_trial",
                ],
            },
        )
        assert grant.status_code == 201, grant.text
        agreement = client.post(
            "/api/member/v1/cos/agreements",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "b03-operating-agreement",
                "agreement_id": "b03-google-tasks-create",
                "revision": 1,
                "layer": "member_private",
                "authority_scope": "member_private",
                "rule": _agreement_rule(),
            },
        )
        assert agreement.status_code == 201, agreement.text
        approved_agreement = client.post(
            "/api/member/v1/cos/agreements/b03-google-tasks-create/1/decision",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "b03-agreement-approve",
                "expected_last_event_id": agreement.json()["event_id"],
                "action": "approve",
            },
        )
        assert approved_agreement.status_code == 200, approved_agreement.text
        actors = {"fixture|b03-account|b03-actor": MEMBER}
        health = client.post(
            "/api/openclaw/v1/health/report",
            headers=OPENCLAW_HEADERS,
            json={
                "state": "healthy",
                "reason": "B03 fixture healthy",
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
                "dm_scope": "per_account_channel_peer",
                "member_agent_ids": [member_agent_id(MEMBER)],
                "binding_fingerprints": list(binding_fingerprints(actors, frozenset())),
                "unique_agents": True,
                "unique_accounts": True,
            },
        )
        assert health.status_code == 200, health.text
        now = datetime.now(timezone.utc)
        command_id = "b03-command-one"
        task_content = {
            "title": "Fixture exact task",
            "notes": "B03 process acceptance",
        }
        domain_request = {
            "request_id": "b03-domain-approval",
            "command_id": command_id,
            "execution_envelope_id": "b03-envelope-one",
            "execution_envelope_revision": 1,
            "authority_grant_id": grant.json()["grant_id"],
            "operating_agreement_id": "b03-google-tasks-create",
            "context_scope": "member_private",
            "on_behalf_of": MEMBER,
            "resource_owner": MEMBER,
            "provider_account_coordinate": ACCOUNT,
            "destination_task_list_coordinate": DESTINATION,
            "task_content": task_content,
            "valid_from": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
        for field, value in (
            ("on_behalf_of", "member-b03-wrong"),
            ("resource_owner", "member-b03-wrong"),
            ("provider_account_coordinate", "provider-account:wrong"),
            ("destination_task_list_coordinate", "task-list:wrong"),
        ):
            refused = client.post(
                "/api/member/v1/cos/google-tasks-create/trial-approvals",
                headers=MEMBER_HEADERS,
                json={
                    **domain_request,
                    "request_id": f"b03-refuse-{field}",
                    field: value,
                },
            )
            assert refused.status_code == 403, refused.text
        expired = client.post(
            "/api/member/v1/cos/google-tasks-create/trial-approvals",
            headers=MEMBER_HEADERS,
            json={
                **domain_request,
                "request_id": "b03-refuse-expired",
                "valid_from": (now - timedelta(minutes=2)).isoformat(),
                "expires_at": (now - timedelta(minutes=1)).isoformat(),
            },
        )
        assert expired.status_code == 422, expired.text
        domain = client.post(
            "/api/member/v1/cos/google-tasks-create/trial-approvals",
            headers=MEMBER_HEADERS,
            json=domain_request,
        )
        assert domain.status_code == 201, domain.text
        missing_domain = client.post(
            "/api/openclaw/v1/tools/google-tasks-create/prepare",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("b03-missing-domain"),
                "request_id": "b03-prepare-missing-domain",
                "context_scope": "member_private",
                "command_id": command_id,
                "domain_approval_event_id": "domain-approval:missing",
                "expected_approval_fingerprint": domain.json()["approval_fingerprint"],
                "task_content": task_content,
            },
        )
        assert missing_domain.status_code == 403
        changed_content = client.post(
            "/api/openclaw/v1/tools/google-tasks-create/prepare",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("b03-changed-content"),
                "request_id": "b03-prepare-changed-content",
                "context_scope": "member_private",
                "command_id": command_id,
                "domain_approval_event_id": domain.json()["event_id"],
                "expected_approval_fingerprint": domain.json()["approval_fingerprint"],
                "task_content": {**task_content, "title": "Changed title"},
            },
        )
        assert changed_content.status_code == 403
        assert fake.effects == []
        prepare = client.post(
            "/api/openclaw/v1/tools/google-tasks-create/prepare",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("b03-prepare"),
                "request_id": "b03-prepare-command",
                "context_scope": "member_private",
                "command_id": command_id,
                "domain_approval_event_id": domain.json()["event_id"],
                "expected_approval_fingerprint": domain.json()["approval_fingerprint"],
                "task_content": task_content,
            },
        )
        assert prepare.status_code == 201, prepare.text
        row = _wait_row(runtime, command_id, "held_for_approval")
        execute_params = {
            "request_id": "b03-execute-command",
            "context_scope": "member_private",
            "command_id": command_id,
            "expected_last_event_id": row["last_event_id"],
            "expected_approval_fingerprint": domain.json()["approval_fingerprint"],
        }
        changed_fingerprint = client.post(
            "/api/openclaw/v1/tools/google-tasks-create/execute",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("b03-changed-fingerprint"),
                **execute_params,
                "expected_approval_fingerprint": "f" * 64,
            },
        )
        assert changed_fingerprint.status_code == 403
        assert fake.effects == []
        missing_approval = client.post(
            "/api/openclaw/v1/tools/google-tasks-create/execute",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance("b03-missing-approval"), **execute_params},
        )
        assert missing_approval.status_code == 403
        assert fake.effects == []
        receipt = _allow_once(client, execute_params)
        executed = client.post(
            "/api/openclaw/v1/tools/google-tasks-create/execute",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("b03-execute"),
                "openclaw_approval_receipt_id": receipt,
                **execute_params,
            },
        )
        assert executed.status_code == 202, executed.text
        terminal = _wait_row(runtime, command_id, "terminal_success")
        assert terminal["provider_effect_count"] == 1
        assert len(fake.effects) == 1
        assert client.portal.call(runtime.observation.is_active) is True
        duplicate = client.post(
            "/api/openclaw/v1/tools/google-tasks-create/execute",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("b03-duplicate"),
                "openclaw_approval_receipt_id": receipt,
                **execute_params,
            },
        )
        assert duplicate.status_code == 409
        assert len(fake.effects) == 1
        observed_at = datetime.now(timezone.utc).isoformat()
        observation_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="list_item.added",
                schema_version=4,
                occurred_at=observed_at,
                source_adapter="tasks-google-tasks",
                source_account_id=ACCOUNT,
                owner_scope=f"service-private:{COS_SERVICE_PRINCIPAL_ID}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                payload={
                    "member_id": None,
                    "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                    "workspace": "service",
                    "provider": "google_tasks",
                    "provider_account_id": ACCOUNT,
                    "list_external_id": DESTINATION,
                    "item_external_id": "native-b03-task-1",
                    "list_name": "Dedicated B03 fixture",
                    "title": task_content["title"],
                    "notes": task_content["notes"],
                    "priority": 0,
                    "completed": False,
                    "last_modified_at": observed_at,
                    "sync_cycle_id": "b03-normal-source-cycle",
                    "provider_revision": "provider-revision-b03-1",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "tasks", observation_id)
        reconciled = _wait_row(runtime, command_id, "reconciled")
        assert reconciled["reconciliation_state"] == "matched"

        outside_admission = client.portal.call(
            fake.apply_write,
            WriteOperation(
                kind="create_task",
                target_external_id=None,
                payload={"tasklist_id": DESTINATION, "title": "Outside admission"},
            ),
        )
        assert outside_admission.suppressed is True
        assert len(fake.effects) == 1

        drifted_at = datetime.now(timezone.utc).isoformat()
        drifted_observation_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="list_item.updated",
                schema_version=4,
                occurred_at=drifted_at,
                source_adapter="tasks-google-tasks",
                source_account_id=ACCOUNT,
                owner_scope=f"service-private:{COS_SERVICE_PRINCIPAL_ID}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                payload={
                    "member_id": None,
                    "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                    "workspace": "service",
                    "provider": "google_tasks",
                    "provider_account_id": ACCOUNT,
                    "list_external_id": DESTINATION,
                    "item_external_id": "native-b03-task-1",
                    "list_name": "Dedicated B03 fixture",
                    "title": "Provider-edited task",
                    "notes": task_content["notes"],
                    "priority": 0,
                    "completed": False,
                    "last_modified_at": drifted_at,
                    "sync_cycle_id": "b03-normal-source-drift-cycle",
                    "provider_revision": "provider-revision-b03-2",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "tasks", drifted_observation_id)
        drifted = _wait_row(runtime, command_id, "drifted")
        assert drifted["reconciliation_state"] == "drifted"
        assert len(fake.effects) == 1

        deleted_at = datetime.now(timezone.utc).isoformat()
        deleted_observation_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="list_item.deleted",
                schema_version=3,
                occurred_at=deleted_at,
                source_adapter="tasks-google-tasks",
                source_account_id=ACCOUNT,
                owner_scope=f"service-private:{COS_SERVICE_PRINCIPAL_ID}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                payload={
                    "member_id": None,
                    "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                    "workspace": "service",
                    "provider": "google_tasks",
                    "provider_account_id": ACCOUNT,
                    "list_external_id": DESTINATION,
                    "item_external_id": "native-b03-task-1",
                    "deleted_at": deleted_at,
                    "sync_cycle_id": "b03-normal-source-delete-cycle",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "tasks", deleted_observation_id)
        missing = _wait_row(runtime, command_id, "missing")
        assert missing["reconciliation_state"] == "missing"
        assert len(fake.effects) == 1

        revoked = client.post(
            f"/api/member/v1/cos/google-tasks-create/executions/{command_id}/revoke",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "b03-revoke-envelope",
                "expected_last_event_id": missing["last_event_id"],
                "reason_code": "single_use_trial_complete",
            },
        )
        assert revoked.status_code == 200, revoked.text
        _wait_row(runtime, command_id, "revoked")
        assert len(fake.effects) == 1
        diagnostics = client.get("/api/system/ready").json()
        assert diagnostics["observation_active"] is True
        assert diagnostics["google_tasks_create_trial"]["content_exposed"] is False
        assert diagnostics["google_tasks_create_trial"]["doctor"]["state"] == "degraded"
        doctor_prerequisites = diagnostics["google_tasks_create_trial"]["doctor"][
            "prerequisites"
        ]
        assert doctor_prerequisites["exact_binding"] is True
        assert doctor_prerequisites["exact_coordinate"] is True
        assert doctor_prerequisites["current_delegation"] is True
        assert doctor_prerequisites["effective_operating_agreement"] is True
        assert doctor_prerequisites["envelope_current"] is False
        assert doctor_prerequisites["single_use_available"] is False
        assert doctor_prerequisites["global_observation_active"] is True
        assert doctor_prerequisites["terminal_path"] is True
        assert doctor_prerequisites["observation_path"] is True
        assert doctor_prerequisites["reconciliation_path"] is True
        assert diagnostics["member_bridge_count"] == 0
        assert diagnostics["messages_read_enrollment"] == "not_enrolled"
        assert diagnostics["messages_reply"] == "disabled"
        events = client.portal.call(lambda: _collect_events(runtime))
        assert sum(event["type"] == "external.sent" for event in events) == 1
        assert (
            sum(
                event["type"] == "cos.google_tasks_execution.contact_attempt_committed"
                for event in events
            )
            == 1
        )
        assert (
            sum(
                event["type"] == "cos.google_tasks_execution.provider_result_received"
                for event in events
            )
            == 1
        )
        assert all(
            event["payload"].get("provider_effect_count", 0) == 0
            for event in events
            if event["type"] == "cos.google_tasks_execution.reconciled"
        )
        assert (
            task_content_fingerprint(task_content)
            == domain.json()["content_fingerprint"]
        )

        prepared_template = next(
            event
            for event in events
            if event["type"] == "cos.google_tasks_execution.prepared"
        )
        approval_template = next(
            event
            for event in events
            if event["type"] == "cos.google_tasks_execution.openclaw_approved"
        )
        contact_template = next(
            event
            for event in events
            if event["type"] == "cos.google_tasks_execution.contact_attempt_committed"
        )
        provider_result_template = next(
            event
            for event in events
            if event["type"] == "cos.google_tasks_execution.provider_result_received"
        )
        command_template = next(
            event
            for event in events
            if event["type"] == "external_sync.write_requested"
            and event["payload"].get("command_id") == command_id
        )

        def append_projection_event(
            event_type: str,
            schema_version: int,
            payload: dict[str, Any],
            idempotency_key: str,
        ) -> str:
            event_id = client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=runtime.settings.instance.tenant_id,
                    type=event_type,
                    schema_version=schema_version,
                    occurred_at=datetime.now(timezone.utc).isoformat(),
                    source_adapter="b03-crash-recovery-acceptance",
                    source_account_id="b03-recovery-fixture",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    idempotency_key=idempotency_key,
                    payload=payload,
                ),
            )
            client.portal.call(runtime.wait_for_projection, "tasks", event_id)
            return event_id

        def seed_approved_recovery(
            command: str,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            envelope_id = f"envelope:{command}"
            domain_event_id = f"domain:{command}"
            openclaw_event_id = f"openclaw:{command}"
            approval_fingerprint = canonical_sha256(
                {
                    "command_id": command,
                    "action_class": "google_tasks_create",
                    "represented_cos": COS_SERVICE_PRINCIPAL_ID,
                    "context_scope": "member_private",
                    "on_behalf_of": MEMBER,
                    "provider": "google_tasks",
                    "provider_account": ACCOUNT,
                    "destination_task_list": DESTINATION,
                    "content_fingerprint": domain.json()["content_fingerprint"],
                    "expires_at": prepared_template["payload"]["expires_at"],
                    "maximum_effect_count": 1,
                }
            )
            prepared_payload = dict(prepared_template["payload"])
            prepared_payload.update(
                {
                    "command_id": command,
                    "execution_envelope_id": envelope_id,
                    "domain_approval_event_id": domain_event_id,
                    "approval_fingerprint": approval_fingerprint,
                    "idempotency_key": f"google-tasks-create:{command}",
                }
            )
            append_projection_event(
                "cos.google_tasks_execution.prepared",
                2,
                prepared_payload,
                f"recovery-prepared:{command}",
            )
            approval_payload = dict(approval_template["payload"])
            approval_payload.update(
                {
                    "command_id": command,
                    "execution_envelope_id": envelope_id,
                    "domain_approval_event_id": domain_event_id,
                    "openclaw_approval_event_id": openclaw_event_id,
                    "approval_fingerprint": approval_fingerprint,
                }
            )
            append_projection_event(
                "cos.google_tasks_execution.openclaw_approved",
                2,
                approval_payload,
                f"recovery-openclaw-approved:{command}",
            )
            command_payload = dict(command_template["payload"])
            command_details = dict(command_payload["payload"])
            command_details["execution_envelope_id"] = envelope_id
            command_payload.update(
                {
                    "command_id": command,
                    "domain_approval_event_id": domain_event_id,
                    "openclaw_approval_event_id": openclaw_event_id,
                    "origin_proposal_id": command,
                    "payload": command_details,
                }
            )
            return command_payload, prepared_payload

        ambiguous_command = "b03-command-ambiguous-recovery"
        ambiguous_payload, ambiguous_prepared = seed_approved_recovery(
            ambiguous_command
        )
        contact_payload = dict(contact_template["payload"])
        contact_material = {
            **contact_payload,
            "command_id": ambiguous_command,
            "execution_envelope_id": ambiguous_prepared["execution_envelope_id"],
            "domain_approval_event_id": ambiguous_prepared["domain_approval_event_id"],
            "openclaw_approval_event_id": f"openclaw:{ambiguous_command}",
        }
        contact_material.pop("scoped_admission_fingerprint", None)
        contact_material["scoped_admission_fingerprint"] = canonical_sha256(
            {
                key: value
                for key, value in contact_material.items()
                if key
                not in {
                    "scoped_admission_fingerprint",
                    "committed_at",
                    "retry_permitted",
                }
            }
        )
        append_projection_event(
            "cos.google_tasks_execution.contact_attempt_committed",
            2,
            contact_material,
            f"b03-contact:{ambiguous_command}",
        )
        append_projection_event(
            "external_sync.write_requested",
            2,
            ambiguous_payload,
            f"b03-provider-command:{ambiguous_command}",
        )
        ambiguous = _wait_row(
            runtime, ambiguous_command, "ambiguous_result_after_possible_contact"
        )
        assert ambiguous["provider_effect_count"] == 0
        assert len(fake.effects) == 1

        recovered_command = "b03-command-result-recovery"
        recovered_payload, recovered_prepared = seed_approved_recovery(
            recovered_command
        )
        recovered_contact_payload = dict(contact_template["payload"])
        recovered_contact_payload.update(
            {
                "command_id": recovered_command,
                "execution_envelope_id": recovered_prepared["execution_envelope_id"],
                "domain_approval_event_id": recovered_prepared[
                    "domain_approval_event_id"
                ],
                "openclaw_approval_event_id": f"openclaw:{recovered_command}",
            }
        )
        recovered_contact_payload["scoped_admission_fingerprint"] = canonical_sha256(
            {
                key: value
                for key, value in recovered_contact_payload.items()
                if key
                not in {
                    "scoped_admission_fingerprint",
                    "committed_at",
                    "retry_permitted",
                }
            }
        )
        recovered_contact_event_id = append_projection_event(
            "cos.google_tasks_execution.contact_attempt_committed",
            2,
            recovered_contact_payload,
            f"b03-contact:{recovered_command}",
        )
        recovered_provider_result = dict(provider_result_template["payload"])
        recovered_provider_result.update(
            {
                "command_id": recovered_command,
                "execution_envelope_id": recovered_prepared["execution_envelope_id"],
                "command_event_id": f"pre-crash-command:{recovered_command}",
                "contact_attempt_event_id": recovered_contact_event_id,
            }
        )
        append_projection_event(
            "cos.google_tasks_execution.provider_result_received",
            2,
            recovered_provider_result,
            f"b03-provider-result:{recovered_command}",
        )
        append_projection_event(
            "external_sync.write_requested",
            2,
            recovered_payload,
            f"b03-provider-command:{recovered_command}",
        )
        recovered_terminal = _wait_row(runtime, recovered_command, "terminal_success")
        assert recovered_terminal["provider_effect_count"] == 1
        assert len(fake.effects) == 1

        revoked_command = "b03-command-revoked-before-contact"
        revoked_payload, _ = seed_approved_recovery(revoked_command)
        approved_row = _wait_row(runtime, revoked_command, "approved")
        revoked_before_contact = client.post(
            f"/api/member/v1/cos/google-tasks-create/executions/{revoked_command}/revoke",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "b03-revoke-before-delayed-command",
                "expected_last_event_id": approved_row["last_event_id"],
                "reason_code": "member_revoked_before_provider_contact",
            },
        )
        assert revoked_before_contact.status_code == 200
        append_projection_event(
            "external_sync.write_requested",
            2,
            revoked_payload,
            f"b03-provider-command:{revoked_command}",
        )
        deadline = time.monotonic() + 5
        terminal_event = None
        while time.monotonic() < deadline:
            terminal_event = client.portal.call(
                runtime.event_log.get_by_idempotency,
                runtime.settings.instance.tenant_id,
                f"b03-terminal:{revoked_command}",
            )
            if terminal_event is not None:
                break
            time.sleep(0.01)
        assert terminal_event is not None
        assert terminal_event["payload"]["outcome"] == "failure_before_contact"
        assert terminal_event["payload"]["provider_called"] is False
        assert (
            _wait_row(runtime, revoked_command, "revoked")["provider_effect_count"] == 0
        )
        assert len(fake.effects) == 1

    restarted_settings, restarted_task_config = _settings(instance)
    restarted_fake = _FakeGoogleTasksAdapter(restarted_task_config)
    monkeypatch.setattr(
        "adminme.runtime.application.load_builtin_adapters",
        lambda *_args, **_kwargs: [restarted_fake],
    )
    restarted_app = create_application(restarted_settings)
    with TestClient(restarted_app) as restarted_client:
        restarted_runtime = restarted_app.state.runtime
        replayed_success = _wait_row(restarted_runtime, command_id, "revoked")
        assert replayed_success["used_at"] is not None
        assert replayed_success["terminal_result_event_id"] is not None
        assert replayed_success["provider_result_event_id"] is not None
        assert replayed_success["provider_effect_count"] == 1
        assert (
            _wait_row(
                restarted_runtime,
                ambiguous_command,
                "ambiguous_result_after_possible_contact",
            )["provider_effect_count"]
            == 0
        )
        assert (
            _wait_row(restarted_runtime, recovered_command, "terminal_success")[
                "provider_effect_count"
            ]
            == 1
        )
        assert (
            _wait_row(restarted_runtime, revoked_command, "revoked")[
                "provider_effect_count"
            ]
            == 0
        )
        replayed_events = restarted_client.portal.call(
            lambda: _collect_events(restarted_runtime)
        )
        assert sum(event["type"] == "external.sent" for event in replayed_events) == 1
        assert restarted_fake.effects == []


async def _collect_events(runtime: Any) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since()]
