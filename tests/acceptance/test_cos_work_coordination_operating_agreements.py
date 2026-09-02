"""Process acceptance for G06 CoS work, coordination, and agreements."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.lib.session import Session
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.projections.parties.queries import list_operating_agreements
from adminme.projections.tasks.queries import get_coordination_record
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import (
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
)
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.wizard import BootstrapWizard


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"g" * 32
SECRET = "g06-loopback-secret-at-least-32-bytes"
A = "member-g06-a"
B = "member-g06-b"
A_HEADERS = {"Tailscale-User-Login": "g06-a.ts.net"}
B_HEADERS = {"Tailscale-User-Login": "g06-b.ts.net"}
OPENCLAW_HEADERS = {"Authorization": f"Bearer {SECRET}"}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"g06-a.ts.net": A, "g06-b.ts.net": B},
        member_roles={A: "principal", B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "G06 fixture household",
            "members": [
                {"member_id": A, "label": "Fixture adult A", "role": "principal"},
                {"member_id": B, "label": "Fixture adult B", "role": "principal"},
            ],
            "memberships": [
                {
                    "membership_id": "g06-membership-a",
                    "party_id": A,
                    "role": "principal",
                },
                {
                    "membership_id": "g06-membership-b",
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
        openclaw_actor_identities={
            "fixture|g06-account-a|g06-actor-a": A,
            "fixture|g06-account-b|g06-actor-b": B,
        },
    )


def _provenance(member: str, correlation_id: str) -> dict[str, Any]:
    suffix = "a" if member == A else "b"
    return {
        "actor_id": f"g06-actor-{suffix}",
        "agent_id": f"g06-agent-{suffix}",
        "session_key": f"g06-session-key-{suffix}",
        "session_id": f"g06-session-{suffix}",
        "channel_id": "fixture",
        "account_id": f"g06-account-{suffix}",
        "message_id": f"g06-message-{correlation_id}",
        "thread_id": f"g06-thread-{suffix}",
        "correlation_id": correlation_id,
        "call_kind": "tool",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _grant(
    client: TestClient,
    *,
    member: str,
    request_id: str,
    context_scope: str,
) -> dict[str, Any]:
    headers = A_HEADERS if member == A else B_HEADERS
    response = client.post(
        "/api/member/v1/cos/delegations",
        headers=headers,
        json={
            "request_id": request_id,
            "context_scope": context_scope,
            "permissions": [
                "read_context",
                "create_internal_work",
                "manage_internal_work",
                "manage_member_obligations",
                "coordinate_household_work",
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _rule(*, members: list[str], rule_id: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "acting_principal_ids": [COS_SERVICE_PRINCIPAL_ID],
        "represented_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "active_member_ids": members,
        "on_behalf_of_member_ids": members,
        "resource_owner_principal_ids": members,
        "provider_account_owner_principal_ids": [],
        "responsible_party_ids": [COS_PARTY_ID],
        "beneficiary_party_ids": members,
        "purpose": "Prepare bounded household operational work",
        "domains": ["household_operations"],
        "trigger": "A typed household work request",
        "object_kinds": ["task", "coordination"],
        "provider_ids": [],
        "provider_account_references": [],
        "workspace_references": [],
        "resource_references": [],
        "destination_ids": [],
        "audience_ids": members,
        "required_fact_types": ["exact_scope"],
        "maximum_fact_age_seconds": 300,
        "required_fact_coverage": "exact",
        "permitted_observations": ["authorized_scoped_observation"],
        "permitted_internal_processing": ["typed_coordination"],
        "permitted_disclosures": ["minimum_typed_fields"],
        "permitted_proposal_preparation": ["propose", "prepare", "hold"],
        "prohibitions": ["provider_execution"],
        "independent_openclaw_approval_required": True,
        "ceilings": {
            "financial_micros": 0,
            "maximum_quantity": 0,
            "permitted_destination_ids": [],
        },
        "verification_requirements": ["scope_check"],
        "reconciliation_requirements": ["typed_outcome"],
        "trust_scope": {
            "granting_member_ids": members,
            "capability_ids": ["prepare_household_work"],
            "domains": ["household_operations"],
            "providers": [],
            "provider_account_references": [],
            "action_verbs": ["prepare", "hold"],
            "destination_ids": [],
            "sensitivity_levels": ["normal", "sensitive"],
            "stage": "prepare_or_hold",
        },
        "provider_execution_permitted": False,
        "source_policy_embedded": False,
    }


def _member_session(member: str, tenant_id: str) -> Session:
    return Session(
        tenant_id=tenant_id,
        auth_member_id=member,
        auth_role="principal",
        view_member_id=member,
        view_role="principal",
        dm_scope="per_account_channel_peer",
        source="member_portal",
    )


async def _provider_effect_count(runtime: Any) -> int:
    types = [
        "external_sync.write_requested",
        "external_sync.write_sent",
        "provider.command.requested",
    ]
    count = 0
    async for _event in runtime.event_log.read_since(types=types):
        count += 1
    return count


def test_g06_work_coordination_agreements_replay_and_restart(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)
    saved_ids: dict[str, str] = {}

    with TestClient(app) as client:
        runtime = app.state.runtime
        tenant_id = runtime.settings.instance.tenant_id
        private_grant = _grant(
            client,
            member=A,
            request_id="g06-private-grant-a",
            context_scope="member_private",
        )
        _grant(
            client,
            member=B,
            request_id="g06-private-grant-b",
            context_scope="member_private",
        )
        shared_grant = _grant(
            client,
            member=A,
            request_id="g06-shared-grant",
            context_scope="household_shared",
        )
        assert shared_grant["active"] is False
        shared_approval = client.post(
            f"/api/member/v1/cos/delegations/{shared_grant['grant_id']}/approvals",
            headers=B_HEADERS,
            json={"request_id": "g06-shared-grant-b-approval"},
        )
        assert shared_approval.status_code == 200, shared_approval.text

        member_work = client.post(
            "/api/openclaw/v1/tools/service-work",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "g06-member-work"),
                "request_id": "g06-member-work",
                "context_scope": "member_private",
                "service_assignment_role": "manages_member_obligation",
                "service_work_type": "waiting_for",
                "title": "Fixture member obligation",
                "waiting_on_party_id": B,
                "provider_representation_owner_kind": "member",
                "provider_id": "fixture-provider",
                "provider_account_reference": "provider-account:fixture-a",
                "provider_external_reference_digest": "a" * 64,
            },
        )
        assert member_work.status_code == 201, member_work.text
        member_payload = member_work.json()
        assert member_payload["owner_member_id"] == A
        assert member_payload["owner_party_id"] is None
        assert member_payload["responsible_party_id"] == COS_PARTY_ID
        assert member_payload["member_obligation_managed_by_cos"] is True
        saved_ids["member_task"] = member_payload["task_id"]

        delegated = client.post(
            "/api/openclaw/v1/tools/service-work",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "g06-delegated-work"),
                "request_id": "g06-delegated-work",
                "service_assignment_role": "delegatee",
                "service_work_type": "preparation",
                "title": "Fixture delegated preparation",
                "recurrence_rrule": "FREQ=WEEKLY",
                "recurrence_next_occurrence": "2030-01-07T14:00:00Z",
            },
        )
        assert delegated.status_code == 201, delegated.text
        assert delegated.json()["owner_party_id"] == COS_PARTY_ID
        assert delegated.json()["recurrence_invokes_openclaw"] is False
        saved_ids["delegated_task"] = delegated.json()["task_id"]

        household_work = client.post(
            "/api/openclaw/v1/tools/service-work",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "g06-household-work"),
                "request_id": "g06-household-work",
                "context_scope": "household_shared",
                "service_assignment_role": "manages_household_obligation",
                "service_work_type": "operational_calendar",
                "title": "Fixture shared household deadline",
            },
        )
        assert household_work.status_code == 201, household_work.text
        assert household_work.json()["work_kind"] == "household_work"
        assert household_work.json()["owner_member_id"] is None
        saved_ids["household_task"] = household_work.json()["task_id"]

        tasks = runtime.projections.connection("tasks")
        member_row = tasks.execute(
            "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?",
            (tenant_id, saved_ids["member_task"]),
        ).fetchone()
        assert member_row is not None
        assert member_row["authority_grant_id"] == private_grant["grant_id"]
        transition = client.post(
            f"/api/openclaw/v1/tools/service-work/{saved_ids['member_task']}/state",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "g06-member-wait"),
                "request_id": "g06-member-wait",
                "expected_last_event_id": member_row["last_event_id"],
                "action": "wait_on_member",
                "waiting_on_member_id": A,
                "reason_code": "member_input",
            },
        )
        assert transition.status_code == 200, transition.text
        assert transition.json()["completed_by_member_id"] is None

        household_party_id = str(
            runtime.projections.connection("parties")
            .execute(
                "SELECT parent_party_id FROM memberships WHERE tenant_id=? "
                "AND party_id=? AND role='principal'",
                (tenant_id, A),
            )
            .fetchone()[0]
        )
        coordination = client.post(
            "/api/member/v1/cos/coordination",
            headers=A_HEADERS,
            json={
                "request_id": "g06-coordination-handoff",
                "coordination_kind": "household_obligation",
                "participant_member_ids": [A, B],
                "obligation_owner_kind": "household",
                "obligation_owner_id": household_party_id,
                "responsible_party_id": A,
                "beneficiary_party_id": household_party_id,
                "cos_assignment_task_id": saved_ids["household_task"],
                "fields": [
                    {
                        "name": "label",
                        "value": "Fixture private label",
                        "audience_member_ids": [A],
                    },
                    {
                        "name": "deadline_at",
                        "value": "2030-01-10T17:00:00Z",
                        "audience_member_ids": [A, B],
                    },
                ],
            },
        )
        assert coordination.status_code == 201, coordination.text
        saved_ids["coordination"] = coordination.json()["coordination_id"]
        assert coordination.json()["private_evidence_included"] is False
        created_events = [
            client.portal.call(runtime.event_log.get, event_id)
            for event_id in coordination.json()["event_ids"]
        ]
        shared_created = next(
            event
            for event in created_events
            if event is not None and event["type"] == "coordination.created"
        )
        assert shared_created["payload"]["private_evidence_included"] is False
        assert not (
            {"raw_evidence", "evidence_quote", "source_content"}
            & set(shared_created["payload"])
        )

        b_detail = client.get(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}",
            headers=B_HEADERS,
        )
        assert b_detail.status_code == 200, b_detail.text
        assert "label" not in b_detail.json()["item"]["fields"]
        latest = coordination.json()["item"]["last_event_id"]
        for member, headers in ((A, A_HEADERS), (B, B_HEADERS)):
            linked = client.post(
                f"/api/member/v1/cos/coordination/{saved_ids['coordination']}/conversation",
                headers=headers,
                json={
                    "request_id": f"g06-conversation-{member}",
                    "expected_last_event_id": latest,
                    "conversation_reference": f"private-conversation:{member}",
                },
            )
            assert linked.status_code == 200, linked.text
            latest = linked.json()["item"]["last_event_id"]
        a_view = client.get(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}",
            headers=A_HEADERS,
        ).json()["item"]
        b_view = client.get(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}",
            headers=B_HEADERS,
        ).json()["item"]
        assert a_view["conversation_reference"] == f"private-conversation:{A}"
        assert b_view["conversation_reference"] == f"private-conversation:{B}"

        proposed = client.post(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}/handoff",
            headers=A_HEADERS,
            json={
                "request_id": "g06-handoff-propose",
                "expected_last_event_id": latest,
                "action": "propose",
                "destination_member_id": B,
            },
        )
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["item"]["responsible_party_id"] == A
        accepted = client.post(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}/handoff",
            headers=B_HEADERS,
            json={
                "request_id": "g06-handoff-accept",
                "expected_last_event_id": proposed.json()["item"]["last_event_id"],
                "action": "accept",
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["item"]["responsible_party_id"] == B
        assert accepted.json()["item"]["obligation_owner_id"] == household_party_id
        waiting = client.post(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}/waiting",
            headers=B_HEADERS,
            json={
                "request_id": "g06-wait-on-member",
                "expected_last_event_id": accepted.json()["item"]["last_event_id"],
                "action": "set",
                "waiting_on_member_id": A,
                "follow_up_at": "2030-01-09T15:00:00Z",
            },
        )
        assert waiting.status_code == 200, waiting.text
        waiting_view = client.get(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}",
            headers=A_HEADERS,
        ).json()["item"]
        assert waiting_view["status"] == "waiting_on_member"
        assert waiting_view["waiting_on_member_id"] == A
        cleared = client.post(
            f"/api/member/v1/cos/coordination/{saved_ids['coordination']}/waiting",
            headers=A_HEADERS,
            json={
                "request_id": "g06-clear-wait-on-member",
                "expected_last_event_id": waiting.json()["event_id"],
                "action": "clear",
            },
        )
        assert cleared.status_code == 200, cleared.text

        rejected_flow = client.post(
            "/api/member/v1/cos/coordination",
            headers=A_HEADERS,
            json={
                "request_id": "g06-handoff-rejection-flow",
                "coordination_kind": "handoff",
                "participant_member_ids": [A, B],
                "obligation_owner_kind": "member",
                "obligation_owner_id": A,
                "responsible_party_id": A,
                "beneficiary_party_id": A,
                "fields": [],
            },
        )
        assert rejected_flow.status_code == 201, rejected_flow.text
        rejected_id = rejected_flow.json()["coordination_id"]
        rejected_proposal = client.post(
            f"/api/member/v1/cos/coordination/{rejected_id}/handoff",
            headers=A_HEADERS,
            json={
                "request_id": "g06-handoff-before-withdrawal",
                "expected_last_event_id": rejected_flow.json()["item"]["last_event_id"],
                "action": "propose",
                "destination_member_id": B,
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )
        assert rejected_proposal.status_code == 200, rejected_proposal.text
        assert rejected_proposal.json()["item"]["expires_at"] is None
        assert (
            rejected_proposal.json()["item"]["handoff_expires_at"]
            == "2099-01-01T00:00:00+00:00"
        )
        withdrawn = client.post(
            f"/api/member/v1/cos/coordination/{rejected_id}/handoff",
            headers=A_HEADERS,
            json={
                "request_id": "g06-handoff-withdrawal",
                "expected_last_event_id": rejected_proposal.json()["item"][
                    "last_event_id"
                ],
                "action": "withdraw",
            },
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["item"]["handoff_expires_at"] is None
        reassigned = client.post(
            f"/api/member/v1/cos/coordination/{rejected_id}/handoff",
            headers=A_HEADERS,
            json={
                "request_id": "g06-handoff-reassignment",
                "expected_last_event_id": withdrawn.json()["item"]["last_event_id"],
                "action": "reassign",
                "destination_member_id": B,
            },
        )
        assert reassigned.status_code == 200, reassigned.text
        rejected = client.post(
            f"/api/member/v1/cos/coordination/{rejected_id}/handoff",
            headers=B_HEADERS,
            json={
                "request_id": "g06-handoff-rejection",
                "expected_last_event_id": reassigned.json()["item"]["last_event_id"],
                "action": "reject",
                "reason_code": "capacity",
            },
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["item"]["status"] == "handoff_rejected"
        assert rejected.json()["item"]["responsible_party_id"] == A

        busy = client.post(
            "/api/member/v1/cos/coordination",
            headers=A_HEADERS,
            json={
                "request_id": "g06-busy-only",
                "coordination_kind": "busy_availability",
                "participant_member_ids": [A, B],
                "obligation_owner_kind": "none",
                "responsible_party_id": A,
                "fields": [
                    {
                        "name": "start_at",
                        "value": "2030-01-10T13:00:00Z",
                        "audience_member_ids": [A, B],
                    },
                    {
                        "name": "end_at",
                        "value": "2030-01-10T14:00:00Z",
                        "audience_member_ids": [A, B],
                    },
                    {
                        "name": "availability",
                        "value": "busy",
                        "audience_member_ids": [A, B],
                    },
                ],
            },
        )
        assert busy.status_code == 201, busy.text
        busy_item = busy.json()["item"]
        assert set(busy_item["fields"]) == {"start_at", "end_at", "availability"}
        assert "label" not in busy_item

        resource_ids: list[str] = []
        for request_id, kind, start, end in (
            (
                "g06-joint-schedule",
                "joint_schedule",
                "2030-01-10T13:15:00Z",
                "2030-01-10T14:15:00Z",
            ),
            (
                "g06-resource-conflict",
                "resource_conflict",
                "2030-01-10T13:30:00Z",
                "2030-01-10T14:30:00Z",
            ),
        ):
            resource = client.post(
                "/api/member/v1/cos/coordination",
                headers=A_HEADERS,
                json={
                    "request_id": request_id,
                    "coordination_kind": kind,
                    "participant_member_ids": [A, B],
                    "obligation_owner_kind": "none",
                    "responsible_party_id": A,
                    "fields": [
                        {
                            "name": "start_at",
                            "value": start,
                            "audience_member_ids": [A, B],
                        },
                        {"name": "end_at", "value": end, "audience_member_ids": [A, B]},
                        {
                            "name": "resource_id",
                            "value": "fixture-resource",
                            "audience_member_ids": [A, B],
                        },
                    ],
                },
            )
            assert resource.status_code == 201, resource.text
            resource_ids.append(resource.json()["coordination_id"])
        conflicts = client.get(
            f"/api/member/v1/cos/coordination/{resource_ids[0]}/conflicts",
            headers=B_HEADERS,
        )
        assert conflicts.status_code == 200, conflicts.text
        assert conflicts.json()["busy_only"] is True
        assert any(
            item["coordination_id"] == resource_ids[1]
            for item in conflicts.json()["conflicts"]
        )
        assert all(
            set(item)
            <= {
                "coordination_id",
                "start_at",
                "end_at",
                "availability",
                "resource_conflict",
            }
            for item in conflicts.json()["conflicts"]
        )
        assert all(
            item["availability"] == "busy" for item in conflicts.json()["conflicts"]
        )

        leaking_private_rule = _rule(
            members=[A], rule_id="g06-private-cross-member-rule-v1"
        )
        leaking_private_rule["audience_ids"] = [A, B]
        leaking_private = client.post(
            "/api/member/v1/cos/agreements",
            headers=A_HEADERS,
            json={
                "request_id": "g06-private-cross-member-scope",
                "agreement_id": "g06-private-cross-member-scope",
                "revision": 1,
                "layer": "member_private",
                "authority_scope": "member_private",
                "rule": leaking_private_rule,
            },
        )
        assert leaking_private.status_code == 403

        private_proposal = client.post(
            "/api/member/v1/cos/agreements",
            headers=A_HEADERS,
            json={
                "request_id": "g06-private-agreement",
                "agreement_id": "g06-private-preparation",
                "revision": 1,
                "layer": "member_private",
                "authority_scope": "member_private",
                "rule": _rule(members=[A], rule_id="g06-private-rule-v1"),
            },
        )
        assert private_proposal.status_code == 201, private_proposal.text
        assert private_proposal.json()["active"] is False
        assert private_proposal.json()["self_activated"] is False
        saved_ids["private_agreement_event"] = private_proposal.json()["event_id"]
        b_agreements = client.get(
            "/api/member/v1/cos/agreements", headers=B_HEADERS
        ).json()["items"]
        assert not any(
            item["agreement_id"] == "g06-private-preparation" for item in b_agreements
        )
        b_private_decision = client.post(
            "/api/member/v1/cos/agreements/g06-private-preparation/1/decision",
            headers=B_HEADERS,
            json={
                "request_id": "g06-private-agreement-cross-member",
                "expected_last_event_id": private_proposal.json()["event_id"],
                "action": "approve",
            },
        )
        assert b_private_decision.status_code == 404
        private_approved = client.post(
            "/api/member/v1/cos/agreements/g06-private-preparation/1/decision",
            headers=A_HEADERS,
            json={
                "request_id": "g06-private-agreement-approve",
                "expected_last_event_id": private_proposal.json()["event_id"],
                "action": "approve",
            },
        )
        assert private_approved.status_code == 200, private_approved.text
        assert private_approved.json()["item"]["effective_state"] == "active"

        expiring_exception = client.post(
            "/api/member/v1/cos/agreements",
            headers=A_HEADERS,
            json={
                "request_id": "g06-expiring-exception",
                "agreement_id": "g06-expiring-exception",
                "revision": 1,
                "layer": "exception",
                "authority_scope": "member_private",
                "expires_at": "2030-01-01T00:00:00Z",
                "rule": _rule(members=[A], rule_id="g06-expiring-exception-v1"),
            },
        )
        assert expiring_exception.status_code == 201, expiring_exception.text
        exception_approved = client.post(
            "/api/member/v1/cos/agreements/g06-expiring-exception/1/decision",
            headers=A_HEADERS,
            json={
                "request_id": "g06-expiring-exception-approve",
                "expected_last_event_id": expiring_exception.json()["event_id"],
                "action": "approve",
            },
        )
        assert exception_approved.status_code == 200, exception_approved.text
        future_agreements = list_operating_agreements(
            runtime.projections.connection("parties"),
            _member_session(A, tenant_id),
            as_of_iso="2031-01-01T00:00:00Z",
            include_inactive=True,
        )
        assert (
            next(
                item
                for item in future_agreements
                if item["agreement_id"] == "g06-expiring-exception"
            )["effective_state"]
            == "expired"
        )

        shared_proposal = client.post(
            "/api/member/v1/cos/agreements",
            headers=A_HEADERS,
            json={
                "request_id": "g06-shared-agreement",
                "agreement_id": "g06-shared-preparation",
                "revision": 1,
                "layer": "shared_household",
                "authority_scope": "household_shared",
                "rule": _rule(members=[A, B], rule_id="g06-shared-rule-v1"),
            },
        )
        assert shared_proposal.status_code == 201, shared_proposal.text
        proposal_event = shared_proposal.json()["event_id"]
        first_approval = client.post(
            "/api/member/v1/cos/agreements/g06-shared-preparation/1/decision",
            headers=A_HEADERS,
            json={
                "request_id": "g06-shared-agreement-approve-a",
                "expected_last_event_id": proposal_event,
                "action": "approve",
            },
        )
        assert first_approval.status_code == 200, first_approval.text
        assert first_approval.json()["item"]["effective_state"] == "proposed"
        second_approval = client.post(
            "/api/member/v1/cos/agreements/g06-shared-preparation/1/decision",
            headers=B_HEADERS,
            json={
                "request_id": "g06-shared-agreement-approve-b",
                "expected_last_event_id": proposal_event,
                "action": "approve",
            },
        )
        assert second_approval.status_code == 200, second_approval.text
        assert second_approval.json()["item"]["effective_state"] == "active"
        saved_ids["shared_agreement_last"] = second_approval.json()["event_id"]

        revoked = client.post(
            "/api/member/v1/cos/agreements/g06-private-preparation/1/decision",
            headers=A_HEADERS,
            json={
                "request_id": "g06-private-agreement-revoke",
                "expected_last_event_id": private_approved.json()["event_id"],
                "action": "revoke",
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["item"]["effective_state"] == "revoked"

        constitutional = client.get(
            "/api/member/v1/cos/agreements", headers=A_HEADERS
        ).json()
        assert constitutional["source_policies"] == {
            "contract": "separate",
            "embedded": False,
        }
        assert constitutional["global_trust_score"] is None
        assert any(
            item["layer"] == "constitutional" for item in constitutional["items"]
        )

        before = {
            "tasks": tasks.execute(
                "SELECT COUNT(*) FROM tasks WHERE tenant_id=? AND service_party_id=?",
                (tenant_id, COS_PARTY_ID),
            ).fetchone()[0],
            "coordination": tasks.execute(
                "SELECT COUNT(*) FROM coordination_records WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()[0],
            "agreements": runtime.projections.connection("parties")
            .execute(
                "SELECT COUNT(*) FROM operating_agreements WHERE tenant_id=?",
                (tenant_id,),
            )
            .fetchone()[0],
        }
        client.portal.call(runtime.projections.rebuild, "tasks")
        client.portal.call(runtime.projections.rebuild, "parties")
        client.portal.call(runtime.projections.rebuild, "recurrences")
        assert (
            tasks.execute(
                "SELECT COUNT(*) FROM tasks WHERE tenant_id=? AND service_party_id=?",
                (tenant_id, COS_PARTY_ID),
            ).fetchone()[0]
            == before["tasks"]
        )
        assert (
            tasks.execute(
                "SELECT COUNT(*) FROM coordination_records WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()[0]
            == before["coordination"]
        )
        assert (
            runtime.projections.connection("parties")
            .execute(
                "SELECT COUNT(*) FROM operating_agreements WHERE tenant_id=?",
                (tenant_id,),
            )
            .fetchone()[0]
            == before["agreements"]
        )
        assert runtime.fixture_adapter.delivered == []
        assert client.portal.call(_provider_effect_count, runtime) == 0

    restarted = create_application(settings)
    with TestClient(restarted) as client:
        runtime = restarted.state.runtime
        tenant_id = runtime.settings.instance.tenant_id
        tasks = runtime.projections.connection("tasks")
        member_row = tasks.execute(
            "SELECT status, completed_by FROM tasks WHERE tenant_id=? AND task_id=?",
            (tenant_id, saved_ids["member_task"]),
        ).fetchone()
        assert member_row is not None
        assert member_row["status"] == "waiting_on"
        assert member_row["completed_by"] is None
        coordination = get_coordination_record(
            tasks,
            _member_session(B, tenant_id),
            coordination_id=saved_ids["coordination"],
            as_of_iso=EventEnvelope.now_utc_iso(),
        )
        assert coordination is not None
        assert coordination["responsible_party_id"] == B
        agreements = list_operating_agreements(
            runtime.projections.connection("parties"),
            _member_session(A, tenant_id),
            as_of_iso=EventEnvelope.now_utc_iso(),
            include_inactive=True,
        )
        states = {item["agreement_id"]: item["effective_state"] for item in agreements}
        assert states["g06-private-preparation"] == "revoked"
        assert states["g06-shared-preparation"] == "active"
        assert runtime.fixture_adapter.delivered == []
        assert client.portal.call(_provider_effect_count, runtime) == 0


def test_g06_encrypted_backup_restore_preserves_structured_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    event_key = secret_store(paths, test_mode=True).get(references["event_log"])
    mapping = json.loads(
        (paths.instance_dir / "config" / "member-mapping.json").read_text("utf-8")
    )
    principals = [
        str(member["member_id"])
        for member in mapping["members"]
        if member["role"] == "principal"
    ]
    assert len(principals) == 2
    member_a, member_b = principals
    backup_secret = "g06-backup-loopback-secret-32-bytes"
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=event_key,
        repository_root=REPO_ROOT,
        member_identities={"g06-backup-a.ts.net": member_a},
        member_roles={
            str(member["member_id"]): str(member["role"])
            for member in mapping["members"]
        },
        household_mapping=mapping,
        deterministic_openclaw_fixture=True,
        fixture_member_id=member_a,
        openclaw_enabled=True,
        openclaw_shared_secret=backup_secret,
        openclaw_actor_identities={
            "fixture|g06-backup-account|g06-backup-actor": member_a
        },
    )
    headers = {"Tailscale-User-Login": "g06-backup-a.ts.net"}
    openclaw_headers = {"Authorization": f"Bearer {backup_secret}"}
    task_id: str
    coordination_id: str
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        granted = client.post(
            "/api/member/v1/cos/delegations",
            headers=headers,
            json={
                "request_id": "g06-backup-private-grant",
                "context_scope": "member_private",
                "permissions": [
                    "read_context",
                    "create_internal_work",
                    "manage_internal_work",
                ],
            },
        )
        assert granted.status_code == 201, granted.text
        provenance = {
            "actor_id": "g06-backup-actor",
            "agent_id": "g06-backup-agent",
            "session_key": "g06-backup-session-key",
            "session_id": "g06-backup-session",
            "channel_id": "fixture",
            "account_id": "g06-backup-account",
            "message_id": "g06-backup-message",
            "thread_id": "g06-backup-thread",
            "correlation_id": "g06-backup-work",
            "call_kind": "tool",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
        }
        work = client.post(
            "/api/openclaw/v1/tools/service-work",
            headers=openclaw_headers,
            json={
                "provenance": provenance,
                "request_id": "g06-backup-work",
                "service_assignment_role": "internal",
                "service_work_type": "reconciliation",
                "title": "Fixture backup reconciliation work",
            },
        )
        assert work.status_code == 201, work.text
        task_id = str(work.json()["task_id"])
        coordination = client.post(
            "/api/member/v1/cos/coordination",
            headers=headers,
            json={
                "request_id": "g06-backup-coordination",
                "coordination_kind": "busy_availability",
                "participant_member_ids": [member_a, member_b],
                "obligation_owner_kind": "none",
                "responsible_party_id": member_a,
                "fields": [
                    {
                        "name": "start_at",
                        "value": "2030-02-01T10:00:00Z",
                        "audience_member_ids": [member_a, member_b],
                    },
                    {
                        "name": "end_at",
                        "value": "2030-02-01T11:00:00Z",
                        "audience_member_ids": [member_a, member_b],
                    },
                    {
                        "name": "availability",
                        "value": "busy",
                        "audience_member_ids": [member_a, member_b],
                    },
                ],
            },
        )
        assert coordination.status_code == 201, coordination.text
        coordination_id = str(coordination.json()["coordination_id"])
        proposal = client.post(
            "/api/member/v1/cos/agreements",
            headers=headers,
            json={
                "request_id": "g06-backup-agreement",
                "agreement_id": "g06-backup-private-agreement",
                "revision": 1,
                "layer": "member_private",
                "authority_scope": "member_private",
                "rule": _rule(members=[member_a], rule_id="g06-backup-rule-v1"),
            },
        )
        assert proposal.status_code == 201, proposal.text
        approved = client.post(
            "/api/member/v1/cos/agreements/g06-backup-private-agreement/1/decision",
            headers=headers,
            json={
                "request_id": "g06-backup-agreement-approve",
                "expected_last_event_id": proposal.json()["event_id"],
                "action": "approve",
            },
        )
        assert approved.status_code == 200, approved.text
        assert runtime.fixture_adapter.delivered == []

    ServiceManager(paths, test_mode=True).start()
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    restored = manager.restore(Path(backup["path"]))
    assert restored["projections_rebuilt"] == 15

    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        tenant_id = runtime.settings.instance.tenant_id
        task = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT service_assignment_role, service_work_type FROM tasks "
                "WHERE tenant_id=? AND task_id=?",
                (tenant_id, task_id),
            )
            .fetchone()
        )
        assert task is not None
        assert task["service_assignment_role"] == "internal"
        assert task["service_work_type"] == "reconciliation"
        coordination = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT coordination_kind FROM coordination_records "
                "WHERE tenant_id=? AND coordination_id=?",
                (tenant_id, coordination_id),
            )
            .fetchone()
        )
        assert coordination is not None
        assert coordination["coordination_kind"] == "busy_availability"
        agreement = (
            runtime.projections.connection("parties")
            .execute(
                "SELECT state FROM operating_agreements WHERE tenant_id=? "
                "AND agreement_id='g06-backup-private-agreement' AND revision=1",
                (tenant_id,),
            )
            .fetchone()
        )
        assert agreement is not None and agreement["state"] == "active"
        assert runtime.fixture_adapter.delivered == []
        assert client.portal.call(_provider_effect_count, runtime) == 0
