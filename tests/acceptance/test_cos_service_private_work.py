"""Service-private work through the real encrypted application composition."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.openclaw_topology import service_agent_id, service_session_key
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import (
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    service_private_scope,
)
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.wizard import BootstrapWizard
from tests.acceptance.test_cos_identity_delegation_kernel import (
    A,
    B,
    A_HEADERS,
    B_HEADERS,
    OPENCLAW_HEADERS,
    SECRET,
    REPO_ROOT,
    _provenance,
    _settings,
)

CREATE = "/api/openclaw/v1/tools/service-private-work"
READ = "/api/openclaw/v1/tools/service-private-state"
PRIVATE_TITLE = "Private service continuity canary"


def _service(correlation: str) -> dict[str, Any]:
    member = _provenance(A, correlation)
    return {
        **{k: member[k] for k in ("openclaw_version", "openclaw_commit")},
        "actor_id": COS_SERVICE_PRINCIPAL_ID,
        "agent_id": service_agent_id(COS_SERVICE_PRINCIPAL_ID),
        "session_key": service_session_key(
            COS_SERVICE_PRINCIPAL_ID, SECRET, correlation
        ),
        "channel_id": "administrateme-service",
        "call_kind": "tool",
        "correlation_id": correlation,
    }


def _create(
    client: TestClient, request_id: str = "private-work-create"
) -> dict[str, Any]:
    response = client.post(
        CREATE,
        headers=OPENCLAW_HEADERS,
        json={
            "provenance": _service(request_id),
            "request_id": request_id,
            "title": PRIVATE_TITLE,
            "description": "Reconcile my own outstanding work",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _read(client: TestClient, correlation: str, **kwargs: Any) -> dict[str, Any]:
    response = client.post(
        READ,
        headers=OPENCLAW_HEADERS,
        json={"provenance": _service(correlation), **kwargs},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _events(runtime: Any) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since()]


def test_private_work_lifecycle_audit_isolation_replay_restart(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        created = _create(client)
        task_id = created["task_id"]
        work = created["work"]
        assert work["owner_member_id"] is None
        assert work["owner_party_id"] == work["assignee_party"] == COS_PARTY_ID
        assert work["authority_grant_id"] is None
        assert (
            work["owner_scope"]
            == work["visibility_scope"]
            == service_private_scope(COS_SERVICE_PRINCIPAL_ID)
        )
        assert created["timer_registered"] is False
        assert _create(client)["duplicate"] is True
        conflict = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("conflicting-body"),
                "request_id": "private-work-create",
                "title": "Different content under the same request id",
            },
        )
        assert conflict.status_code == 409

        previous = created["event_id"]
        for index, action in enumerate(("start", "hold", "resume", "complete")):
            request = {
                "provenance": _service(f"transition-{index}"),
                "request_id": f"transition-{index}",
                "expected_last_event_id": previous,
                "action": action,
                "reason_code": "completed" if action == "complete" else "other",
            }
            if action == "hold":
                request["follow_up_at"] = (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat()
            changed = client.post(
                f"{CREATE}/{task_id}/state", headers=OPENCLAW_HEADERS, json=request
            )
            assert changed.status_code == 200, changed.text
            response = changed.json()
            assert (
                response["provider_effect_count"] == 0
                and response["timer_registered"] is False
            )
            assert (
                client.post(
                    f"{CREATE}/{task_id}/state", headers=OPENCLAW_HEADERS, json=request
                ).json()["duplicate"]
                is True
            )
            stale = {
                **request,
                "request_id": f"stale-event-{index}",
                "provenance": _service(f"stale-{index}"),
            }
            assert (
                client.post(
                    f"{CREATE}/{task_id}/state", headers=OPENCLAW_HEADERS, json=stale
                ).status_code
                == 409
            )
            previous = response["event_id"]

        state = _read(client, "read-completed", task_id=task_id)
        assert state["work"][0]["status"] == "done"
        assert state["work"][0]["completed_by"] is None
        assert state["work"][0]["completed_by_party_id"] == COS_PARTY_ID
        assert any(
            event["type"] == "openclaw.tool_call.recorded" for event in state["history"]
        )
        assert all(
            event["actor_identity"] == COS_SERVICE_PRINCIPAL_ID
            for event in state["history"]
        )
        assert all(
            "service-session" not in str(event)
            and "administrateme-service-" not in str(event)
            for event in state["history"]
        )
        assert _read(client, "read-all")["work"][0]["task_id"] == task_id
        page = _read(client, "page-first", task_id=task_id, limit=2)
        paged = list(page["history"])
        for index in range(10):
            if page["next_cursor"] is None:
                break
            page = _read(
                client,
                f"page-{index}",
                task_id=task_id,
                limit=2,
                after_event_id=page["next_cursor"],
            )
            paged.extend(page["history"])
        assert page["next_cursor"] is None
        assert {event["event_id"] for event in paged} == {
            event["event_id"] for event in state["history"]
        }

        for member, headers in ((A, A_HEADERS), (B, B_HEADERS)):
            grant = client.post(
                "/api/member/v1/cos/delegations",
                headers=headers,
                json={
                    "request_id": f"read-grant-{member}",
                    "context_scope": "member_private",
                    "permissions": [
                        "read_context",
                        "create_internal_work",
                        "manage_internal_work",
                    ],
                },
            )
            assert grant.status_code == 201, grant.text
            member_state = client.post(
                "/api/openclaw/v1/tools/state",
                headers=OPENCLAW_HEADERS,
                json={"provenance": _provenance(member, f"read-{member}")},
            )
            assert member_state.status_code == 200, member_state.text
            assert (
                PRIVATE_TITLE not in member_state.text
                and task_id not in member_state.text
            )
            activity = client.get("/api/core/v1/activity", headers=headers)
            assert activity.status_code == 200, activity.text
            assert PRIVATE_TITLE not in activity.text and task_id not in activity.text
            denied = client.post(
                READ,
                headers=OPENCLAW_HEADERS,
                json={"provenance": _provenance(member, f"denied-{member}")},
            )
            assert denied.status_code == 403

        generic = client.post(
            "/api/openclaw/v1/tools/state",
            headers=OPENCLAW_HEADERS,
            json={"provenance": _service("no-member-impersonation")},
        )
        assert generic.status_code == 403
        events = client.portal.call(_events, runtime)
        private = [
            event
            for event in events
            if event["owner_scope"] == service_private_scope(COS_SERVICE_PRINCIPAL_ID)
        ]
        creates = [event for event in private if event["type"] == "task.created"]
        assert len(creates) == 1 and creates[0]["schema_version"] == 4
        transitions = [
            event for event in private if event["type"] == "cos.work.state_changed"
        ]
        assert len(transitions) == 4 and all(
            event["schema_version"] == 2 for event in transitions
        )
        assert all(
            event["payload"]["authority_kind"] == "service_self"
            for event in creates + transitions
        )
        assert all(
            event["payload"]["authority_grant_id"] is None
            for event in creates + transitions
        )
        assert not any(
            event["type"]
            in {
                "external_sync.write_requested",
                "provider.command.requested",
                "notification.delivery.requested",
            }
            for event in events
        )
        assert runtime.fixture_adapter.delivered == []
        before = state["work"]
        client.portal.call(runtime.projections.rebuild, "tasks")
        assert _read(client, "after-replay", task_id=task_id)["work"] == before

    with TestClient(create_application(settings)) as restarted:
        assert _read(restarted, "after-restart", task_id=task_id)["work"] == before
        assert _create(restarted)["duplicate"] is True
        assert restarted.app.state.runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "service-principal:other"},
        {"agent_id": "member-agent"},
        {"session_key": "invalid"},
        {"channel_id": "imessage"},
        {"account_id": "member-account"},
        {"message_id": "private-message"},
        {"thread_id": "member-thread"},
        {"call_kind": "dashboard"},
    ],
)
def test_service_route_rejects_contradictory_identity(
    tmp_path: Path, change: dict[str, str]
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        response = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": {**_service("denied-private-work"), **change},
                "request_id": "denied-private-work",
                "title": PRIVATE_TITLE,
            },
        )
        assert response.status_code == 403
        anonymous = client.post(READ, json={"provenance": _service("anonymous")})
        assert anonymous.status_code == 401
        assert _read(client, "empty-after-denial")["work"] == []


def test_private_service_cannot_read_or_transition_member_work(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        for index, scope in enumerate(
            (
                f"private:{A}",
                f"private:{B}",
                "shared:household",
                "service-private:service-principal:other",
            )
        ):
            event = EventEnvelope(
                event_at_ms=1,
                tenant_id=runtime.settings.instance.tenant_id,
                type="task.created",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="fixture",
                source_account_id="scope-canary",
                owner_scope=scope,
                visibility_scope=scope,
                payload={"task_id": f"canary-{index}", "title": "Private scope canary"},
            )
            event_id = client.portal.call(runtime.events.write, event)
            client.portal.call(runtime.wait_for_projection, "tasks", event_id)
            read = client.post(
                READ,
                headers=OPENCLAW_HEADERS,
                json={
                    "provenance": _service(f"canary-read-{index}"),
                    "task_id": f"canary-{index}",
                },
            )
            assert read.status_code == 404
            changed = client.post(
                f"{CREATE}/canary-{index}/state",
                headers=OPENCLAW_HEADERS,
                json={
                    "provenance": _service(f"canary-change-{index}"),
                    "request_id": f"canary-change-{index}",
                    "expected_last_event_id": event_id,
                    "action": "complete",
                    "reason_code": "completed",
                },
            )
            assert changed.status_code == 404
        assert _read(client, "canary-list")["work"] == []
        cursor = client.post(
            READ,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("forbidden-history-cursor"),
                "after_event_id": event_id,
            },
        )
        assert cursor.status_code == 404
        for field, value in (
            ("owner_member_id", A),
            ("authority_grant_id", "forged-grant"),
            ("provider_id", "gmail"),
            ("source_event_id", "private-message"),
        ):
            denied = client.post(
                CREATE,
                headers=OPENCLAW_HEADERS,
                json={
                    "provenance": _service(f"forged-{field}"),
                    "request_id": f"forged-{field}",
                    "title": PRIVATE_TITLE,
                    field: value,
                },
            )
            assert denied.status_code == 422


def test_long_valid_correlation_is_audited_and_idempotent(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        body = {
            "provenance": _service("c" * 300),
            "request_id": "long-correlation-work",
            "title": PRIVATE_TITLE,
        }
        first = client.post(CREATE, headers=OPENCLAW_HEADERS, json=body)
        assert first.status_code == 201, first.text
        second = client.post(CREATE, headers=OPENCLAW_HEADERS, json=body)
        assert second.status_code == 201 and second.json()["duplicate"] is True
        state = _read(client, "long-correlation-read", task_id=first.json()["task_id"])
        assert len(state["history"]) == 2
        assert state["history"][-1]["payload"]["request_correlation_id"] == "c" * 300


def test_encrypted_restore_preserves_service_work_and_client_restrictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore new event versions, retained grants and derived-source restrictions."""
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
    member = next(
        str(value["member_id"])
        for value in mapping["members"]
        if value["role"] == "principal"
    )
    member_headers = {"Tailscale-User-Login": "service-backup-member.ts.net"}
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=event_key,
        repository_root=REPO_ROOT,
        member_identities={"service-backup-member.ts.net": member},
        member_roles={
            str(value["member_id"]): str(value["role"]) for value in mapping["members"]
        },
        household_mapping=mapping,
        deterministic_openclaw_fixture=True,
        fixture_member_id=member,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
    )
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        granted = client.post(
            "/api/member/v1/cos/delegations",
            headers=member_headers,
            json={
                "request_id": "backup-client-context-grant",
                "context_scope": "member_private",
                "permissions": ["read_context"],
            },
        )
        assert granted.status_code == 201, granted.text
        grant_id = granted.json()["grant_id"]
        source = EventEnvelope(
            event_at_ms=1,
            tenant_id=runtime.settings.instance.tenant_id,
            type="task.created",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="fixture-canonical-product",
            source_account_id="fixture-account",
            owner_scope=f"private:{member}",
            visibility_scope=f"private:{member}",
            sensitivity="privileged",
            actor_identity=member,
            payload={
                "task_id": "client-backup-source-task",
                "title": "Client-private fixture",
                "owner_member_id": member,
            },
        )
        source_id = client.portal.call(runtime.events.write, source)
        client.portal.call(runtime.wait_for_projection, "tasks", source_id)
        create_body = {
            "provenance": _service("backup-service-note"),
            "request_id": "backup-service-note",
            "title": PRIVATE_TITLE,
            "description": "Retain an authorized client follow-up",
            "record_kind": "note",
            "source_event_ids": [source_id],
        }
        created = client.post(CREATE, headers=OPENCLAW_HEADERS, json=create_body)
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]
        hold_body = {
            "provenance": _service("backup-service-hold"),
            "request_id": "backup-service-hold",
            "expected_last_event_id": created.json()["event_id"],
            "action": "hold",
            "reason_code": "scheduled_hold",
            "follow_up_at": (
                datetime.now(timezone.utc) + timedelta(days=2)
            ).isoformat(),
        }
        held = client.post(
            f"{CREATE}/{task_id}/state", headers=OPENCLAW_HEADERS, json=hold_body
        )
        assert held.status_code == 200, held.text
        expected = _read(client, "backup-service-before", task_id=task_id)
        assert expected["work"][0]["record_kind"] == "note"
        assert expected["work"][0]["status"] == "deferred"
        assert expected["work"][0]["sensitivity"] == "privileged"
        authority = expected["work"][0]["source_authorities"][0]
        assert authority["source_event_id"] == source_id
        assert authority["authority_grant_id"] == grant_id
        assert authority["restriction"] == "internal_processing_only"
        assert authority["source_sensitivity"] == "privileged"
        assert authority["source_policy_key"]
        before_events = client.portal.call(_events, runtime)
        assert runtime.fixture_adapter.delivered == []

    ServiceManager(paths, test_mode=True).start()
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    assert backup["encrypted"] is True
    assert PRIVATE_TITLE.encode() not in Path(backup["path"]).read_bytes()
    restored = manager.restore(Path(backup["path"]))
    assert restored["status"] == "restored"
    assert restored["projections_rebuilt"] == 15

    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        after_events = client.portal.call(_events, runtime)
        before_by_id = {event["event_id"]: event for event in before_events}
        assert {
            event["event_id"]: event
            for event in after_events
            if event["event_id"] in before_by_id
        } == before_by_id
        after = _read(client, "backup-service-after", task_id=task_id)
        assert after["work"] == expected["work"]
        assert after["history"] == expected["history"]
        assert (
            client.post(CREATE, headers=OPENCLAW_HEADERS, json=create_body).json()[
                "duplicate"
            ]
            is True
        )
        repeated = client.post(
            f"{CREATE}/{task_id}/state", headers=OPENCLAW_HEADERS, json=hold_body
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["duplicate"] is True
        assert repeated.json()["timer_registered"] is False
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant_id}",
            headers=member_headers,
            json={"request_id": "backup-client-revocation", "reason": "fixture"},
        )
        assert revoked.status_code == 200, revoked.text
        denied = client.post(
            READ,
            headers=OPENCLAW_HEADERS,
            json={"provenance": _service("backup-revoked-read"), "task_id": task_id},
        )
        assert denied.status_code == 404
        assert runtime.fixture_adapter.delivered == []
