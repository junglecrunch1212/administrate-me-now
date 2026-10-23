"""Process proof for the first-class household CoS identity kernel."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.lib.session import Session
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.projections.parties import PartiesProjection
from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    resolve_service_delegation,
)
from adminme.projections.tasks.queries import get_task
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_DETERMINISTIC_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    ServiceRequestContext,
)
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.wizard import BootstrapWizard


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"c" * 32
SECRET = "cos-kernel-loopback-secret-32-bytes"
A = "member-cos-a"
B = "member-cos-b"
EXTERNAL = "party-cos-external-contact"
A_HEADERS = {"Tailscale-User-Login": "cos-a.ts.net"}
B_HEADERS = {"Tailscale-User-Login": "cos-b.ts.net"}
OPENCLAW_HEADERS = {"Authorization": f"Bearer {SECRET}"}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"cos-a.ts.net": A, "cos-b.ts.net": B},
        member_roles={A: "principal", B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Fixture household",
            "members": [
                {"member_id": A, "label": "Adult A", "role": "principal"},
                {"member_id": B, "label": "Adult B", "role": "principal"},
            ],
            "memberships": [
                {
                    "membership_id": "membership-cos-a",
                    "party_id": A,
                    "role": "principal",
                },
                {
                    "membership_id": "membership-cos-b",
                    "party_id": B,
                    "role": "principal",
                },
            ],
            "parties": [
                {
                    "party_id": EXTERNAL,
                    "label": "Reviewed external contact",
                    "kind": "person",
                    "visibility_scope": "shared:household",
                }
            ],
            "relationships": [
                {
                    "relationship_id": "relationship-cos-adults",
                    "from_party_id": A,
                    "to_party_id": B,
                    "kind": "partner",
                    "direction": "mutual",
                }
            ],
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=A,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "fixture|account-a|actor-a": A,
            "fixture|account-b|actor-b": B,
        },
    )


def _provenance(member: str, correlation_id: str) -> dict[str, Any]:
    suffix = "a" if member == A else "b"
    return {
        "actor_id": f"actor-{suffix}",
        "agent_id": f"agent-{suffix}",
        "session_key": f"session-key-{suffix}",
        "session_id": f"session-{suffix}",
        "channel_id": "fixture",
        "account_id": f"account-{suffix}",
        "message_id": f"message-{correlation_id}",
        "thread_id": f"thread-{suffix}",
        "correlation_id": correlation_id,
        "call_kind": "tool",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _grant_private(client: TestClient, member: str, request_id: str) -> str:
    headers = A_HEADERS if member == A else B_HEADERS
    response = client.post(
        "/api/member/v1/cos/delegations",
        headers=headers,
        json={
            "request_id": request_id,
            "context_scope": "member_private",
            "permissions": ["read_context", "create_internal_work"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["provider_authority"] is False
    assert response.json()["external_write_requests"] == 0
    return str(response.json()["grant_id"])


def test_exact_delegation_request_readback_is_private_and_read_only(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        request_id = "member-permission-readback"
        path = f"/api/member/v1/cos/delegations?request_id={request_id}"
        missing = client.get(path, headers=A_HEADERS)
        assert missing.status_code == 200, missing.text
        assert missing.json()["request_confirmation"] == {
            "request_id": request_id,
            "state": "not_recorded",
            "grant_id": None,
        }
        grant = _grant_private(client, A, request_id)
        runtime = client.app.state.runtime
        before = client.portal.call(runtime.event_log.count)
        found = client.get(path, headers=A_HEADERS)
        assert found.status_code == 200, found.text
        assert found.json()["request_confirmation"] == {
            "request_id": request_id,
            "state": "recorded",
            "grant_id": grant,
        }
        assert any(row["grant_id"] == grant for row in found.json()["items"])
        other = client.get(path, headers=B_HEADERS)
        assert other.status_code == 200, other.text
        assert other.json()["request_confirmation"]["state"] == "not_recorded"
        assert other.json()["request_confirmation"]["grant_id"] is None
        assert other.json()["items"] == []
        assert client.portal.call(runtime.event_log.count) == before
        assert (
            client.get(
                "/api/member/v1/cos/delegations?request_id=short", headers=A_HEADERS
            ).status_code
            == 422
        )

        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant}",
            headers=A_HEADERS,
            json={"request_id": "readback-revoke", "reason": "fixture"},
        )
        assert revoked.status_code == 200, revoked.text
        before = client.portal.call(runtime.event_log.count)
        retained = client.get(path, headers=A_HEADERS)
        assert retained.status_code == 200, retained.text
        assert retained.json()["request_confirmation"]["grant_id"] == grant
        row = next(
            item for item in retained.json()["items"] if item["grant_id"] == grant
        )
        assert row["active"] is False
        assert row["revoked_at"]
        assert client.portal.call(runtime.event_log.count) == before


def test_private_permission_setup_serializes_stale_tabs_without_granting_twice(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        path = "/api/member/v1/cos/delegations"
        assert client.get(path, headers=A_HEADERS).json()["items"] == []
        assert client.get(path, headers=A_HEADERS).json()["items"] == []

        def submit(suffix: str) -> Any:
            return client.post(
                path,
                headers=A_HEADERS,
                json={
                    "request_id": f"concurrent-private-setup-{suffix}",
                    "context_scope": "member_private",
                    "permissions": ["read_context", "prepare_external_actions"],
                    "require_no_active_private_grant": True,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit, suffix) for suffix in ("a", "b")]
            responses = [future.result(timeout=20) for future in futures]
        assert sorted(response.status_code for response in responses) == [201, 409]
        rejected = next(
            response for response in responses if response.status_code == 409
        )
        assert rejected.json()["detail"]["code"] == "private_delegation_already_active"
        grants = client.get(path, headers=A_HEADERS).json()["items"]
        assert len(grants) == 1 and grants[0]["active"]
        # The guard applies only to this member's private grant, not another
        # adult's independent authority or a household approval quorum.
        other = client.post(
            path,
            headers=B_HEADERS,
            json={
                "request_id": "independent-private-setup",
                "context_scope": "member_private",
                "permissions": ["read_context"],
                "require_no_active_private_grant": True,
            },
        )
        assert other.status_code == 201, other.text
        wrong_scope = client.post(
            path,
            headers=A_HEADERS,
            json={
                "request_id": "invalid-shared-setup-guard",
                "context_scope": "household_shared",
                "permissions": ["read_context"],
                "require_no_active_private_grant": True,
            },
        )
        assert wrong_scope.status_code == 422
        runtime = client.app.state.runtime

        async def sent_count() -> int:
            return await runtime.event_log.count_since(None, types=["external.sent"])

        assert client.portal.call(sent_count) == 0


def test_private_permission_setup_waits_for_durable_state_after_uncertain_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        original_wait = runtime.wait_for_projection
        baseline = client.portal.call(runtime.event_log.latest_event_id)
        checkpoints: list[str] = []

        async def interrupted_settlement(
            projection_name: str, event_id: str, *, timeout_s: float = 8.0
        ) -> None:
            if projection_name == "parties":
                checkpoints.append(event_id)
                if baseline is None or event_id > baseline:
                    raise TimeoutError("fixture: grant committed before settlement")
            await original_wait(projection_name, event_id, timeout_s=timeout_s)

        monkeypatch.setattr(runtime, "wait_for_projection", interrupted_settlement)
        path = "/api/member/v1/cos/delegations"

        def submit(request_id: str) -> Any:
            return client.post(
                path,
                headers=A_HEADERS,
                json={
                    "request_id": request_id,
                    "context_scope": "member_private",
                    "permissions": ["read_context", "prepare_external_actions"],
                    "require_no_active_private_grant": True,
                },
            )

        first_id = "private-setup-uncertain-first"
        first = submit(first_id)
        assert first.status_code == 503, first.text
        assert first.json()["detail"]["code"] == "private_delegation_state_unsettled"
        exact = client.get(f"{path}?request_id={first_id}", headers=A_HEADERS)
        assert exact.json()["request_confirmation"]["state"] == "recorded"
        grant_id = exact.json()["request_confirmation"]["grant_id"]
        committed_checkpoint = client.portal.call(runtime.event_log.latest_event_id)
        before = client.portal.call(runtime.event_log.count)
        wait_count = len(checkpoints)

        second_id = "private-setup-uncertain-second"
        second = submit(second_id)
        assert second.status_code == 503, second.text
        assert second.json()["detail"]["code"] == "private_delegation_state_unsettled"
        assert checkpoints[wait_count:] == [committed_checkpoint]
        assert client.portal.call(runtime.event_log.count) == before
        absent = client.get(f"{path}?request_id={second_id}", headers=A_HEADERS)
        assert absent.json()["request_confirmation"]["state"] == "not_recorded"

        monkeypatch.setattr(runtime, "wait_for_projection", original_wait)
        settled = submit("private-setup-after-settlement")
        assert settled.status_code == 409, settled.text
        assert settled.json()["detail"]["code"] == "private_delegation_already_active"
        grants = client.get(path, headers=A_HEADERS).json()["items"]
        assert len(grants) == 1 and grants[0]["active"]
        assert grants[0]["grant_id"] == grant_id
        assert client.portal.call(runtime.event_log.count) == before
        assert runtime.fixture_adapter.delivered == []


def test_private_permission_setup_ignores_unrelated_log_tail(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _grant_private(client, A, "permission-before-unrelated-tail")
        _service_work(client, A, "unrelated-service-work-tail", "Prepare fixture brief")
        runtime = client.app.state.runtime

        async def parties_tail() -> str | None:
            return await runtime.event_log.latest_event_id(
                types=PartiesProjection.subscribes_to
            )

        relevant = client.portal.call(parties_tail)
        latest = client.portal.call(runtime.event_log.latest_event_id)
        assert relevant is not None and latest is not None and latest > relevant
        for member, expected_status in ((A, 409), (B, 201)):
            response = client.post(
                "/api/member/v1/cos/delegations",
                headers=A_HEADERS if member == A else B_HEADERS,
                json={
                    "request_id": f"permission-after-unrelated-tail-{member}",
                    "context_scope": "member_private",
                    "permissions": ["read_context"],
                    "require_no_active_private_grant": True,
                },
            )
            assert response.status_code == expected_status, response.text
        assert runtime.fixture_adapter.delivered == []


def _service_work(
    client: TestClient, member: str, request_id: str, title: str
) -> dict[str, Any]:
    response = client.post(
        "/api/openclaw/v1/tools/service-work",
        headers=OPENCLAW_HEADERS,
        json={
            "provenance": _provenance(member, request_id),
            "request_id": request_id,
            "title": title,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


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


def test_cos_identity_delegation_context_isolation_replay_and_revocation(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    settings = _settings(instance_dir)
    app = create_application(settings)
    b_task_id: str

    with TestClient(app) as client:
        runtime = app.state.runtime
        parties = runtime.projections.connection("parties")
        cos_party = parties.execute(
            "SELECT * FROM parties WHERE tenant_id=? AND party_id=?",
            (runtime.settings.instance.tenant_id, COS_PARTY_ID),
        ).fetchone()
        assert cos_party is not None
        assert cos_party["kind"] == "organization"
        attributes = json.loads(str(cos_party["attributes_json"]))
        assert attributes == {
            "source": "service_identity_reconciliation",
            "nonhuman_service": True,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "service_kind": "chief_of_staff",
            "private_scope": f"service-private:{COS_SERVICE_PRINCIPAL_ID}",
            "provider_authority": False,
        }
        membership = parties.execute(
            "SELECT role FROM memberships WHERE tenant_id=? AND party_id=?",
            (runtime.settings.instance.tenant_id, COS_PARTY_ID),
        ).fetchone()
        assert membership is not None and membership["role"] == "household_service"

        denied = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(A, "no-grant")},
        )
        assert denied.status_code == 403

        cross_member_grant = client.post(
            "/api/member/v1/cos/delegations",
            headers=A_HEADERS,
            json={
                "request_id": "cross-member-private",
                "context_scope": "member_private",
                "context_member_id": B,
                "permissions": ["read_context"],
            },
        )
        assert cross_member_grant.status_code == 403

        a_grant = _grant_private(client, A, "cos-private-a")
        conflicting_grant = client.post(
            "/api/member/v1/cos/delegations",
            headers=A_HEADERS,
            json={
                "request_id": "cos-private-a",
                "context_scope": "member_private",
                "permissions": ["read_context"],
            },
        )
        assert conflicting_grant.status_code == 409
        a_work = _service_work(client, A, "service-work-a", "Prepare A's brief")
        assert a_work["owner_party_id"] == COS_PARTY_ID
        assert a_work["responsible_party_id"] == COS_PARTY_ID
        assert a_work["assignee_party_id"] == COS_PARTY_ID
        assert a_work["beneficiary_party_id"] == A
        assert a_work["member_obligation_created"] is False
        assert a_work["provider_effects"] == 0

        denied_b = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(B, "b-no-grant")},
        )
        assert denied_b.status_code == 403
        _grant_private(client, B, "cos-private-b")
        b_work = _service_work(client, B, "service-work-b", "Prepare B's brief")
        b_task_id = str(b_work["task_id"])

        tasks = runtime.projections.connection("tasks")
        raw_b = tasks.execute(
            "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?",
            (runtime.settings.instance.tenant_id, b_task_id),
        ).fetchone()
        assert raw_b is not None
        assert raw_b["work_kind"] == "service_operational"
        assert raw_b["owner_member_id"] is None
        assert raw_b["owner_party_id"] == COS_PARTY_ID
        assert (
            get_task(
                tasks,
                _member_session(A, runtime.settings.instance.tenant_id),
                task_id=b_task_id,
            )
            is None
        )
        assert (
            get_task(
                tasks,
                _member_session(B, runtime.settings.instance.tenant_id),
                task_id=b_task_id,
            )
            is not None
        )

        events = client.portal.call(
            runtime.event_log.get_by_correlation, "service-work-a"
        )
        task_event = next(row for row in events if row["type"] == "task.created")
        audit = next(
            row for row in events if row["type"] == "openclaw.tool_call.recorded"
        )
        assert task_event["actor_identity"] == COS_SERVICE_PRINCIPAL_ID
        assert audit["schema_version"] == 2
        assert audit["payload"]["operator_principal_id"] == (COS_AUTOMATION_OPERATOR_ID)
        assert audit["payload"]["context_member_id"] == A
        assert audit["payload"]["authority_grant_id"] == a_grant
        assert audit["payload"]["request_correlation_id"] == "service-work-a"
        assert audit["payload"]["causation_event_ids"] == [task_event["event_id"]]
        assert audit["payload"]["result_event_ids"] == [task_event["event_id"]]
        assert audit["payload"]["observation_event_ids"] == []
        assert audit["payload"]["reconciliation_event_ids"] == []

        before_replay = tuple(raw_b)
        client.portal.call(runtime.projections.rebuild, "parties")
        client.portal.call(runtime.projections.rebuild, "tasks")
        after_replay = tasks.execute(
            "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?",
            (runtime.settings.instance.tenant_id, b_task_id),
        ).fetchone()
        assert after_replay is not None and tuple(after_replay) == before_replay

        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{a_grant}",
            headers=A_HEADERS,
            json={"request_id": "revoke-private-a", "reason": "member revoked"},
        )
        assert revoked.status_code == 200, revoked.text
        after_revoke = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(A, "after-revoke")},
        )
        assert after_revoke.status_code == 403
        b_still_active = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(B, "b-still-active")},
        )
        assert b_still_active.status_code == 200, b_still_active.text
        assert runtime.fixture_adapter.delivered == []

    restarted = create_application(settings)
    with TestClient(restarted) as client:
        revoked_stays_closed = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(A, "restart-a")},
        )
        assert revoked_stays_closed.status_code == 403
        active_survives = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(B, "restart-b")},
        )
        assert active_survives.status_code == 200, active_survives.text
        row = (
            restarted.state.runtime.projections.connection("tasks")
            .execute(
                "SELECT beneficiary_party_id FROM tasks WHERE tenant_id=? AND task_id=?",
                (restarted.state.runtime.settings.instance.tenant_id, b_task_id),
            )
            .fetchone()
        )
        assert row is not None and row["beneficiary_party_id"] == B
        assert restarted.state.runtime.fixture_adapter.delivered == []


def test_create_only_grant_cannot_read_member_context(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    app = create_application(_settings(instance_dir))

    with TestClient(app) as client:
        created = client.post(
            "/api/member/v1/cos/delegations",
            headers=A_HEADERS,
            json={
                "request_id": "cos-create-only",
                "context_scope": "member_private",
                "permissions": ["create_internal_work"],
            },
        )
        assert created.status_code == 201, created.text
        denied_read = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _provenance(A, "create-only-read")},
        )
        assert denied_read.status_code == 403
        unknown_actor = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": {
                    **_provenance(A, "unknown-route"),
                    "actor_id": "actor-unknown",
                }
            },
        )
        assert unknown_actor.status_code == 403
        created_work = _service_work(
            client,
            A,
            "create-only-work",
            "Create-only authority fixture",
        )
        assert created_work["provider_effects"] == 0
        conflicting = client.post(
            "/api/member/v1/cos/delegations",
            headers=A_HEADERS,
            json={
                "request_id": "cos-create-only-conflict",
                "context_scope": "member_private",
                "permissions": ["create_internal_work"],
            },
        )
        assert conflicting.status_code == 201, conflicting.text
        ambiguous = client.post(
            "/api/openclaw/v1/tools/service-work",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "ambiguous-context"),
                "request_id": "ambiguous-work",
                "title": "Must not be created",
            },
        )
        assert ambiguous.status_code == 403
        assert app.state.runtime.fixture_adapter.delivered == []


def test_external_contact_context_requires_exact_reviewed_party(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    app = create_application(_settings(instance_dir))

    with TestClient(app) as client:
        requested = client.post(
            "/api/member/v1/cos/delegations",
            headers=A_HEADERS,
            json={
                "request_id": "cos-external-contact",
                "context_scope": "external_contact",
                "owner_party_id": EXTERNAL,
                "permissions": ["read_context"],
            },
        )
        assert requested.status_code == 201, requested.text
        runtime = app.state.runtime
        context = resolve_service_delegation(
            runtime.projections.connection("parties"),
            tenant_id=runtime.settings.instance.tenant_id,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
            context_scope="external_contact",
            context_member_id=A,
            owner_party_id=EXTERNAL,
        )
        assert context.owner_party_id == EXTERNAL
        assert context.resource_owner_principal_id == A
        assert context.allowed_scopes == {
            f"external-contact:{EXTERNAL}",
        }
        assert runtime.fixture_adapter.delivered == []


def test_household_quorum_and_future_human_operator_are_independent(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    app = create_application(_settings(instance_dir))

    with TestClient(app) as client:
        requested = client.post(
            "/api/member/v1/cos/delegations",
            headers=A_HEADERS,
            json={
                "request_id": "cos-shared-quorum",
                "context_scope": "household_shared",
                "permissions": ["read_context"],
            },
        )
        assert requested.status_code == 201, requested.text
        grant_id = str(requested.json()["grant_id"])
        assert requested.json()["active"] is False
        runtime = app.state.runtime
        with pytest.raises(ServiceDelegationResolutionError):
            resolve_service_delegation(
                runtime.projections.connection("parties"),
                tenant_id=runtime.settings.instance.tenant_id,
                service_principal_id=COS_SERVICE_PRINCIPAL_ID,
                operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
                context_scope="household_shared",
                context_member_id=None,
            )

        approved = client.post(
            f"/api/member/v1/cos/delegations/{grant_id}/approvals",
            headers=B_HEADERS,
            json={"request_id": "cos-shared-approval-b"},
        )
        assert approved.status_code == 200, approved.text
        context = resolve_service_delegation(
            runtime.projections.connection("parties"),
            tenant_id=runtime.settings.instance.tenant_id,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id="operator:human:future-chief-of-staff",
            context_scope="household_shared",
            context_member_id=None,
        )
        assert context.actor_principal_id == COS_SERVICE_PRINCIPAL_ID
        assert context.operator_principal_id == "operator:human:future-chief-of-staff"
        assert context.context_member_id is None
        assert context.approving_member_ids == (A, B)
        assert context.approval_quorum == "all_adult_principals"
        assert context.provider_account_owner_principal_id is None

        service_private = ServiceRequestContext(
            tenant_id=runtime.settings.instance.tenant_id,
            actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id="operator:human:future-chief-of-staff",
            represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            context_scope="service_private",
        )
        assert service_private.allowed_scopes == {
            f"service-private:{COS_SERVICE_PRINCIPAL_ID}"
        }
        deterministic = ServiceRequestContext(
            tenant_id=runtime.settings.instance.tenant_id,
            actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id=COS_DETERMINISTIC_OPERATOR_ID,
            represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            context_scope="service_private",
        )
        assert deterministic.actor_principal_id == service_private.actor_principal_id
        assert deterministic.operator_principal_id != context.operator_principal_id
        assert runtime.fixture_adapter.delivered == []


def test_encrypted_backup_restore_preserves_cos_identity_and_revocation(
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
    member_id = principals[0]
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=event_key,
        repository_root=REPO_ROOT,
        member_identities={"backup-cos-a.ts.net": member_id},
        member_roles={
            str(member["member_id"]): str(member["role"])
            for member in mapping["members"]
        },
        household_mapping=mapping,
    )
    grant_id: str
    with TestClient(create_application(settings)) as client:
        created = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "backup-cos-a.ts.net"},
            json={
                "request_id": "backup-cos-delegation",
                "context_scope": "member_private",
                "permissions": ["read_context"],
            },
        )
        assert created.status_code == 201, created.text
        grant_id = str(created.json()["grant_id"])
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant_id}",
            headers={"Tailscale-User-Login": "backup-cos-a.ts.net"},
            json={"request_id": "backup-cos-revocation", "reason": "fixture"},
        )
        assert revoked.status_code == 200, revoked.text

    ServiceManager(paths, test_mode=True).start()
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    restored = manager.restore(Path(backup["path"]))
    assert restored["projections_rebuilt"] == 15

    with TestClient(create_application(settings)) as client:
        result = client.get(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "backup-cos-a.ts.net"},
        )
        assert result.status_code == 200, result.text
        grant = next(
            item for item in result.json()["items"] if item["grant_id"] == grant_id
        )
        assert grant["active"] is False
        assert grant["revoked_at"]
        party = (
            client.app.state.runtime.projections.connection("parties")
            .execute(
                "SELECT attributes_json FROM parties WHERE tenant_id=? AND party_id=?",
                (client.app.state.runtime.settings.instance.tenant_id, COS_PARTY_ID),
            )
            .fetchone()
        )
        assert party is not None
        assert json.loads(str(party["attributes_json"]))["nonhuman_service"] is True
