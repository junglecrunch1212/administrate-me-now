"""Service-private work through the real encrypted application composition."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.service_context import source_facts
from adminme.products.openclaw.service_work import _audit, _internal_session, _key
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
    _grant_private,
    _provenance,
    _settings,
)

CREATE = "/api/openclaw/v1/tools/service-private-work"
READ = "/api/openclaw/v1/tools/service-private-state"
PRIVATE_TITLE = "Private service continuity canary"
PRIVATE_WORK_PREDECESSOR = "e78acbc98324db1dd88ca4e002d05eb017ecbc14"

# This runs only in a separate process rooted at the archived predecessor. It uses
# that archive's actual application, registry, projection and fixture settings;
# the candidate cannot impersonate the old reader by removing a schema entry.
_PREDECESSOR_PRIVATE_WORK_READBACK = """
import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from adminme.runtime import application
from adminme.events.registry import ensure_autoloaded, registry
from tests.acceptance.test_cos_service_private_work import (
    A_HEADERS, CREATE, OPENCLAW_HEADERS, READ, _events, _read, _service, _settings,
)

archive = Path.cwd().resolve()
expected = json.loads(Path(sys.argv[1]).read_text())
settings = _settings(Path(expected["instance"]))
assert settings.repository_root.resolve() == archive
assert str(settings.instance.event_log_path.resolve()) == expected["event_log_path"]
assert Path(application.__file__).resolve() == archive / "adminme/runtime/application.py"
ensure_autoloaded()
assert registry.get("task.created", 5) is not None
assert registry.get("task.created", 6) is None

def history_digest(events):
    return hashlib.sha256(
        json.dumps(events, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

with TestClient(application.create_application(settings)) as client:
    runtime = client.app.state.runtime
    client.portal.call(runtime.projections.rebuild, "tasks")
    result = _read(client, "predecessor-v6-read", task_id=expected["task_id"])
    assert result["work"] == [expected["work"]]
    assert len(result["work"][0]["source_authorities"]) == 26
    retained = next(row for row in result["history"] if row["event_id"] == expected["event_id"])
    assert retained["schema_version"] == 6
    assert retained["payload"]["source_authorities"] == expected["work"]["source_authorities"]
    assert result["provider_effect_count"] == 0 and result["timer_registered"] is False
    revoked = client.request(
        "DELETE", f"/api/member/v1/cos/delegations/{expected['grant_id']}",
        headers=A_HEADERS,
        json={"request_id": "predecessor-v6-revoke", "reason": "fixture revocation"},
    )
    assert revoked.status_code == 200, revoked.text
    denied = client.post(
        READ, headers=OPENCLAW_HEADERS,
        json={"provenance": _service("predecessor-v6-denied"), "task_id": expected["task_id"]},
    )
    assert denied.status_code == 404, denied.text
    reused = client.post(CREATE, headers=OPENCLAW_HEADERS, json=expected["create_body"])
    assert reused.status_code == 404, reused.text
    events = client.portal.call(_events, runtime)
    assert history_digest(events[:expected["history_count"]]) == expected["history_sha256"]
    assert sum(
        row["type"] == "task.created" and row["payload"].get("task_id") == expected["task_id"]
        for row in events
    ) == 1
    assert not any(row["type"] in {
        "external_sync.write_requested", "provider.command.requested",
        "notification.delivery.requested",
    } for row in events)
    assert runtime.fixture_adapter.delivered == []

# Check the entire loaded implementation namespace, not just a reported root.
for name, module in tuple(sys.modules.items()):
    filename = getattr(module, "__file__", None)
    if filename and (name == "adminme" or name.startswith("adminme.")):
        assert Path(filename).resolve().is_relative_to(archive), (name, filename)
assert registry.get("task.created", 6) is None
print(json.dumps({
    "predecessor_commit": expected["predecessor_commit"],
    "application_path": str(Path(application.__file__).resolve()),
    "registered_task_created_v6": False, "history_preserved": True,
    "source_authorities": 26, "read_status": 200,
    "revoked_read_status": denied.status_code, "revoked_reuse_status": reused.status_code,
    "provider_effect_count": 0,
}))
"""


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


def _member_sources(client: TestClient, count: int, prefix: str) -> list[str]:
    runtime = client.app.state.runtime
    source_ids = []
    for index in range(count):
        event = EventEnvelope(
            event_at_ms=index + 1,
            tenant_id=runtime.settings.instance.tenant_id,
            type="task.created",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="member-fixture",
            source_account_id=A,
            owner_scope=f"private:{A}",
            visibility_scope=f"private:{A}",
            payload={
                "task_id": f"{prefix}-source-task-{index}",
                "title": f"Authorized source {index}",
                "owner_member_id": A,
            },
        )
        event_id = client.portal.call(runtime.events.write, event)
        client.portal.call(runtime.wait_for_projection, "tasks", event_id)
        source_ids.append(event_id)
    return source_ids


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


def test_private_state_read_26_then_scratchpad_retains_v6_across_replay_and_restart(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        grant_id = _grant_private(client, A, "retained-26-grant")
        source_ids = _member_sources(client, 26, "retained-26")
        for index, source_id in enumerate(source_ids):
            seeded = client.post(
                CREATE,
                headers=OPENCLAW_HEADERS,
                json={
                    "provenance": _service(f"seed-retained-{index}"),
                    "request_id": f"seed-retained-{index}",
                    "title": f"Seed retained source {index}",
                    "record_kind": "note",
                    "source_event_ids": [source_id],
                },
            )
            assert seeded.status_code == 201, seeded.text
            event = client.portal.call(runtime.event_log.get, seeded.json()["event_id"])
            assert event["schema_version"] == 4

        # The real state route records all source chains consumed by this run.
        state = _read(client, "read-retained-26", limit=50)
        assert len(state["work"]) == 26
        expected_authorities = sorted(
            [source for item in state["work"] for source in item["source_authorities"]],
            key=lambda source: source["source_event_id"],
        )
        expected_digest = hashlib.sha256(
            json.dumps(expected_authorities, sort_keys=True).encode()
        ).hexdigest()
        body = {
            "provenance": _service("read-retained-26"),
            "request_id": "create-after-retained-26",
            "title": "Scratchpad after broad authorized state read",
            "record_kind": "scratchpad",
        }
        created = client.post(CREATE, headers=OPENCLAW_HEADERS, json=body)
        assert created.status_code == 201, created.text
        work = created.json()["work"]
        task_id = created.json()["task_id"]
        assert {row["source_event_id"] for row in work["source_authorities"]} == set(
            source_ids
        )
        event = client.portal.call(runtime.event_log.get, created.json()["event_id"])
        assert event["schema_version"] == 6
        assert work["source_authorities"] == expected_authorities
        assert event["payload"]["source_authorities"] == expected_authorities
        assert (
            hashlib.sha256(
                json.dumps(
                    event["payload"]["source_authorities"], sort_keys=True
                ).encode()
            ).hexdigest()
            == expected_digest
        )
        assert created.json()["provider_effect_count"] == 0
        assert created.json()["timer_registered"] is False

        repeated = client.post(CREATE, headers=OPENCLAW_HEADERS, json=body)
        assert repeated.status_code == 201 and repeated.json()["duplicate"] is True
        conflict = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={**body, "title": "Changed body under the same request id"},
        )
        assert conflict.status_code == 409
        creates = [
            row
            for row in client.portal.call(_events, runtime)
            if row["type"] == "task.created"
            and row["payload"].get("task_id") == task_id
        ]
        assert len(creates) == 1
        before = work
        client.portal.call(runtime.projections.rebuild, "tasks")
        assert _read(client, "retained-26-replay", task_id=task_id)["work"][0] == before
        assert runtime.fixture_adapter.delivered == []

    with TestClient(create_application(settings)) as restarted:
        after = _read(restarted, "retained-26-restart", task_id=task_id)["work"][0]
        assert after == before
        assert len(after["source_authorities"]) == 26
        assert (
            hashlib.sha256(
                json.dumps(after["source_authorities"], sort_keys=True).encode()
            ).hexdigest()
            == expected_digest
        )
        revoked = restarted.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant_id}",
            headers=A_HEADERS,
            json={"request_id": "revoke-retained-26", "reason": "fixture revocation"},
        )
        assert revoked.status_code == 200, revoked.text
        denied = restarted.post(
            READ,
            headers=OPENCLAW_HEADERS,
            json={"provenance": _service("revoked-v6-read"), "task_id": task_id},
        )
        assert denied.status_code == 404
        replay = restarted.post(CREATE, headers=OPENCLAW_HEADERS, json=body)
        assert replay.status_code == 404
        retained_events = restarted.portal.call(_events, restarted.app.state.runtime)
        assert (
            sum(
                row["type"] == "task.created"
                and row["payload"].get("task_id") == task_id
                for row in retained_events
            )
            == 1
        )
        assert restarted.app.state.runtime.fixture_adapter.delivered == []


def test_exact_predecessor_replays_v6_private_work_and_enforces_current_grant(
    tmp_path: Path,
) -> None:
    """Real e78 history/read compatibility, not full updater qualification."""
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        grant_id = _grant_private(client, A, "predecessor-v6-grant")
        source_ids = _member_sources(client, 26, "predecessor-v6")
        for offset in (0, 13):
            request_id = f"predecessor-v6-seed-{offset}"
            seeded = client.post(
                CREATE,
                headers=OPENCLAW_HEADERS,
                json={
                    "provenance": _service(request_id),
                    "request_id": request_id,
                    "title": "Retained fixture source set",
                    "record_kind": "note",
                    "source_event_ids": source_ids[offset : offset + 13],
                },
            )
            assert seeded.status_code == 201, seeded.text
        source_read = _read(client, "predecessor-v6-context")
        authorities = sorted(
            [
                source
                for work in source_read["work"]
                for source in work["source_authorities"]
            ],
            key=lambda source: source["source_event_id"],
        )
        assert {source["source_event_id"] for source in authorities} == set(source_ids)
        body = {
            "provenance": _service("predecessor-v6-context"),
            "request_id": "predecessor-v6-scratchpad",
            "title": "Retained scratchpad compatibility fixture",
            "record_kind": "scratchpad",
        }
        response = client.post(CREATE, headers=OPENCLAW_HEADERS, json=body)
        assert response.status_code == 201, response.text
        created = response.json()
        event = client.portal.call(runtime.event_log.get, created["event_id"])
        assert event["schema_version"] == 6
        assert created["work"]["source_authorities"] == authorities
        history = client.portal.call(_events, runtime)
        expected = {
            "predecessor_commit": PRIVATE_WORK_PREDECESSOR,
            "instance": str(instance.resolve()),
            "event_log_path": str(settings.instance.event_log_path.resolve()),
            "task_id": created["task_id"],
            "event_id": created["event_id"],
            "work": created["work"],
            "grant_id": grant_id,
            "create_body": body,
            "history_count": len(history),
            "history_sha256": hashlib.sha256(
                json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        assert created["provider_effect_count"] == 0
        assert runtime.fixture_adapter.delivered == []

    # The candidate is closed before another process touches the same log.
    archive = (tmp_path / "predecessor").resolve()
    archive.mkdir(mode=0o700)
    payload = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(REPO_ROOT),
            "archive",
            PRIVATE_WORK_PREDECESSOR,
        ],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as release:
        release.extractall(archive, filter="data")
    expected_path = tmp_path / "predecessor-expectations.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    expected_path.chmod(0o600)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(archive), str(archive / "bridge")))
    environment["ADMINME_REPOSITORY_ROOT"] = str(archive)
    completed = subprocess.run(
        [sys.executable, "-c", _PREDECESSOR_PRIVATE_WORK_READBACK, str(expected_path)],
        cwd=archive,
        env=environment,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = json.loads(completed.stdout)
    assert result == {
        "predecessor_commit": PRIVATE_WORK_PREDECESSOR,
        "application_path": str(archive / "adminme/runtime/application.py"),
        "registered_task_created_v6": False,
        "history_preserved": True,
        "source_authorities": 26,
        "read_status": 200,
        "revoked_read_status": 404,
        "revoked_reuse_status": 404,
        "provider_effect_count": 0,
    }


def test_retained_source_limit_accepts_256_and_refuses_257_before_append(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        _grant_private(client, A, "retained-limit-grant")
        source_ids = _member_sources(client, 257, "retained-limit")

        async def record_read(correlation: str, selected: list[str]) -> None:
            provenance = seam.Provenance.model_validate(_service(correlation))
            session = _internal_session(runtime, provenance.correlation_id)
            facts = await source_facts(runtime, session, selected)
            await _audit(
                runtime,
                session,
                provenance,
                "tools.service-private-state",
                [],
                [fact["authority"] for fact in facts],
            )

        client.portal.call(record_read, "retained-limit-256", source_ids[:256])
        accepted_body = {
            "provenance": _service("retained-limit-256"),
            "request_id": "retained-limit-256-create",
            "title": "Maximum retained provenance",
            "record_kind": "scratchpad",
        }
        accepted = client.post(CREATE, headers=OPENCLAW_HEADERS, json=accepted_body)
        assert accepted.status_code == 201, accepted.text
        assert len(accepted.json()["work"]["source_authorities"]) == 256
        accepted_event = client.portal.call(
            runtime.event_log.get, accepted.json()["event_id"]
        )
        assert accepted_event["schema_version"] == 6

        correlation = "retained-limit-257"
        client.portal.call(record_read, correlation, source_ids)
        provenance = seam.Provenance.model_validate(_service(correlation))
        session = _internal_session(runtime, provenance.correlation_id)
        request_id = "retained-limit-257-create"
        idempotency_key = _key(session, "task.created", request_id)
        assert (
            client.portal.call(
                runtime.event_log.get_by_idempotency,
                session.tenant_id,
                idempotency_key,
            )
            is None
        )
        refused = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service(correlation),
                "request_id": request_id,
                "title": "One source too many",
                "record_kind": "scratchpad",
            },
        )
        assert refused.status_code == 409
        assert refused.json() == {
            "detail": {"code": "source_provenance_limit_exceeded"}
        }
        assert (
            client.portal.call(
                runtime.event_log.get_by_idempotency,
                session.tenant_id,
                idempotency_key,
            )
            is None
        )
        assert runtime.fixture_adapter.delivered == []


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


@pytest.mark.parametrize("inherited_count", [0, 22], ids=["v5", "v6"])
def test_encrypted_restore_preserves_service_work_and_client_restrictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited_count: int
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
        openclaw_actor_identities={
            "fixture|backup-member-account|backup-member-actor": member,
        },
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
        from tests.acceptance.test_cos_service_working_context import _own_mail

        mail_id = _own_mail(client, "backup-service-mail")
        from tests.acceptance.test_cos_service_planning_context import _emit
        from tests.acceptance.test_cos_service_working_context import (
            _context,
            _run_context,
        )

        calendar_id = _emit(client, "calendar.event_added")
        item_id = _emit(client, "list_item.added")
        inherited_ids = [
            _own_mail(client, f"backup-inherited-mail-{index}")
            for index in range(inherited_count)
        ]
        for index in range(0, inherited_count, 20):
            consumed = _run_context(
                client,
                "backup-service-note",
                f"backup-consumed-{index}",
                source_event_ids=inherited_ids[index : index + 20],
            )
            assert consumed.status_code == 200, consumed.text
        create_body = {
            "provenance": _service("backup-service-note"),
            "request_id": "backup-service-note",
            "title": PRIVATE_TITLE,
            "description": "Retain an authorized client follow-up",
            "record_kind": "note",
            "source_event_ids": [source_id, mail_id, calendar_id, item_id],
        }
        created = client.post(CREATE, headers=OPENCLAW_HEADERS, json=create_body)
        assert created.status_code == 201, created.text
        created_event = client.portal.call(
            runtime.event_log.get, created.json()["event_id"]
        )
        assert created_event["schema_version"] == (6 if inherited_count else 5)
        assert (
            len(created_event["payload"]["source_authorities"]) == 4 + inherited_count
        )
        assert {
            source["source_event_id"]
            for source in created_event["payload"]["source_authorities"]
        } == {source_id, mail_id, calendar_id, item_id, *inherited_ids}
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
        own_authority = expected["work"][0]["source_authorities"][1]
        assert own_authority["source_event_id"] == mail_id
        assert own_authority["authority_kind"] == "service_self"
        assert own_authority["authority_grant_id"] is None
        assert (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT 1 FROM cos_source_read_receipts WHERE source_event_id=?",
                (mail_id,),
            )
            .fetchone()
        )
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
        assert (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT 1 FROM cos_source_read_receipts WHERE source_event_id=?",
                (mail_id,),
            )
            .fetchone()
        )
        assert (
            _context(
                client,
                "restored-planning-sources",
                source_event_ids=[calendar_id, item_id],
            ).status_code
            == 200
        )
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
