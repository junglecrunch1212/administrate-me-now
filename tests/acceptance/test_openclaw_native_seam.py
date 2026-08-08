"""Gate 4 process proof for the native OpenClaw/AdministrateMe boundary."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.demo import FIXTURE_MEMBER_ID
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"o" * 32
SECRET = "gate-four-loopback-secret-32-bytes"
PRINCIPAL = FIXTURE_MEMBER_ID
CHILD = "member_fixture_child"
OTHER = "member_fixture_other"
OPENCLAW_HEADERS = {"Authorization": f"Bearer {SECRET}"}
DEMO_HEADERS = {
    "Tailscale-User-Login": "fixture-principal.ts.net",
    "X-Correlation-Id": "gate4:demo",
}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"fixture-principal.ts.net": PRINCIPAL},
        member_roles={PRINCIPAL: "principal", CHILD: "child", OTHER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=PRINCIPAL,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "discord|account-a|actor-a": PRINCIPAL,
            "discord|account-c|actor-child": CHILD,
            "openclaw-dashboard|dashboard-a|actor-a": PRINCIPAL,
            "openclaw-dashboard|dashboard-c|actor-child": CHILD,
        },
    )


def _provenance(
    correlation_id: str,
    *,
    actor_id: str = "actor-a",
    channel_id: str = "discord",
    account_id: str = "account-a",
    call_kind: str = "tool",
) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "agent_id": "agent-a",
        "session_key": f"adminme:tenant:{actor_id}:dm",
        "session_id": f"session:{actor_id}",
        "channel_id": channel_id,
        "account_id": account_id,
        "message_id": f"message:{correlation_id}",
        "thread_id": "thread-a",
        "correlation_id": correlation_id,
        "call_kind": call_kind,
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _post(
    client: TestClient,
    path: str,
    body: dict[str, Any],
    *,
    status: int = 200,
) -> dict[str, Any]:
    response = client.post(path, json=body, headers=OPENCLAW_HEADERS)
    assert response.status_code == status, response.text
    return response.json()


def _allow_once(
    client: TestClient,
    *,
    receipt_id: str,
    tool_name: str,
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
        tool_name=tool_name,
        request_fingerprint=fingerprint,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        openclaw_version=OPENCLAW_VERSION,
        openclaw_commit=OPENCLAW_COMMIT,
    )
    result = _post(
        client,
        "/api/openclaw/v1/approvals/resolve",
        {
            "receipt_id": receipt_id,
            "tool_name": tool_name,
            "request_fingerprint": fingerprint,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            "decision": "allow-once",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "signature": signature,
        },
    )
    assert result["receipt_id"] == receipt_id
    return receipt_id


def _seed_slice(client: TestClient, request_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/comms/v1/demo/canonical-message",
        headers=DEMO_HEADERS,
        json={
            "request_id": request_id,
            "native_message_id": f"native-{request_id}",
            "body": "Could you reply today with the fixture household update?",
            "received_at": "2026-07-30T12:05:00Z",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["output"]


async def _events(runtime: Any, *types: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since(types=list(types)):
        rows.append(event)
    return rows


def test_actor_boundary_observation_bridge_model_and_dashboard_scope(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    app = create_application(_settings(instance_dir))

    with TestClient(app) as client:
        _seed_slice(client, "gate4-boundary")
        missing_transport = client.post(
            "/api/openclaw/v1/tools/state",
            json={"provenance": _provenance("missing-auth")},
        )
        assert missing_transport.status_code == 401
        wrong_transport = client.post(
            "/api/openclaw/v1/tools/state",
            headers={"Authorization": "Bearer definitely-wrong"},
            json={"provenance": _provenance("wrong-auth")},
        )
        assert wrong_transport.status_code == 401

        unmapped_operator = client.post(
            "/api/openclaw/v1/dashboard/today",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(
                    "operator-only",
                    actor_id="operator-token-holder",
                    channel_id="openclaw-dashboard",
                    account_id="operator-client",
                    call_kind="dashboard",
                )
            },
        )
        assert unmapped_operator.status_code == 403

        child_state = _post(
            client,
            "/api/openclaw/v1/tools/state",
            {
                "provenance": _provenance(
                    "child-state",
                    actor_id="actor-child",
                    account_id="account-c",
                )
            },
        )
        assert child_state["triage"]["items"] == []
        assert child_state["commitments"]["items"] == []

        proposal = _post(
            client,
            "/api/openclaw/v1/tools/action",
            {
                "provenance": _provenance("observation-action"),
                "request_id": "observation-action",
                "mode": "capture",
                "kind": "task",
                "title": "This remains a proposal",
            },
        )
        assert proposal["state"] == "proposal_only"
        assert proposal["observation_active"] is True
        created = (
            app.state.runtime.projections.connection("tasks")
            .execute(
                "SELECT COUNT(*) FROM tasks WHERE tenant_id=? AND task_id=?",
                (
                    app.state.runtime.settings.instance.tenant_id,
                    "oct_observation-action",
                ),
            )
            .fetchone()[0]
        )
        assert created == 0

        skill = _post(
            client,
            "/api/openclaw/v1/tools/skill",
            {
                "provenance": _provenance("skill-wrapper"),
                "skill_id": "classify_message_nature",
                "inputs": {
                    "body_text": "Please send the fixture update.",
                    "source_channel": "fixture-cat-a",
                    "from_identifier": "sender@example.invalid",
                },
                "input_sensitivities": {"body_text": "normal"},
            },
        )
        assert skill["openclaw_invocation_id"].startswith("fixture:")
        assert skill["provider"] == "fixture/deterministic-v1"

        standing = _post(
            client,
            "/api/openclaw/v1/standing-orders/trigger",
            {
                "provenance": _provenance(
                    "standing-trigger", call_kind="standing_order"
                ),
                "program_id": "morning_digest",
                "automation_id": "fixture-automation-1",
                "trigger_id": "fixture-trigger-1",
            },
        )
        assert standing["accepted"] is True
        standing_retry = _post(
            client,
            "/api/openclaw/v1/standing-orders/trigger",
            {
                "provenance": _provenance("standing-retry", call_kind="standing_order"),
                "program_id": "morning_digest",
                "automation_id": "fixture-automation-1",
                "trigger_id": "fixture-trigger-1",
            },
        )
        assert standing_retry["event_id"] == standing["event_id"]
        assert standing_retry["duplicate"] is True

        bridge = _post(
            client,
            "/api/openclaw/v1/bridge/events",
            {
                "provenance": _provenance("bridge-domain", call_kind="bridge"),
                "event_kind": "household_action",
                "content": "Explicitly capture the household action candidate.",
                "consent": True,
                "sensitivity": "sensitive",
            },
        )
        assert bridge["accepted"] is True
        privileged = client.post(
            "/api/openclaw/v1/bridge/events",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("bridge-privileged", call_kind="bridge"),
                "event_kind": "household_action",
                "content": "Must be excluded",
                "consent": True,
                "sensitivity": "privileged",
            },
        )
        assert privileged.status_code == 422

        model = _post(
            client,
            "/api/openclaw/v1/model-calls",
            {
                "provenance": _provenance("model-metadata"),
                "phase": "started",
                "run_id": "run-gate4",
                "provider": "fixture",
                "model": "deterministic-v1",
            },
        )
        assert model["accepted"] is True
        model_with_content = client.post(
            "/api/openclaw/v1/model-calls",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance("model-content"),
                "phase": "ended",
                "run_id": "run-content",
                "provider": "fixture",
                "model": "deterministic-v1",
                "prompt": "forbidden",
            },
        )
        assert model_with_content.status_code == 422

        seam_events = client.portal.call(
            _events,
            app.state.runtime,
            "skill.call.recorded",
            "openclaw.standing_order.triggered",
        )
        assert any(row["type"] == "skill.call.recorded" for row in seam_events)
        assert any(
            row["type"] == "openclaw.standing_order.triggered"
            and row["payload"]["program_id"] == "morning_digest"
            for row in seam_events
        )

        child_today = _post(
            client,
            "/api/openclaw/v1/dashboard/today",
            {
                "provenance": _provenance(
                    "child-dashboard",
                    actor_id="actor-child",
                    channel_id="openclaw-dashboard",
                    account_id="dashboard-c",
                    call_kind="dashboard",
                )
            },
        )
        visible_types = {item["type"] for item in child_today["activity"]}
        assert "commitment.proposed" not in visible_types
        assert "household.action.proposed" not in visible_types

        correlated = client.portal.call(
            app.state.runtime.event_log.get_by_correlation, "observation-action"
        )
        audit = next(
            row for row in correlated if row["type"] == "openclaw.tool_call.recorded"
        )
        assert audit["payload"] == {
            "operation": "tools.action",
            "result_event_ids": [proposal["event_id"]],
            "actor_id": "actor-a",
            "member_id": PRINCIPAL,
            "agent_id": "agent-a",
            "session_key": "adminme:tenant:actor-a:dm",
            "session_id": "session:actor-a",
            "channel_id": "discord",
            "account_id": "account-a",
            "message_id": "message:observation-action",
            "thread_id": "thread-a",
            "correlation_id": "observation-action",
            "call_kind": "tool",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
        }


def test_lifecycle_and_approval_are_durable_scope_filtered_and_exactly_once(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    app = create_application(_settings(instance_dir))

    with TestClient(app) as client:
        output = _seed_slice(client, "gate4-outcomes")
        runtime = app.state.runtime
        task_row = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT * FROM tasks WHERE tenant_id=? AND task_id=?",
                (runtime.settings.instance.tenant_id, "task_fixture_secondary"),
            )
            .fetchone()
        )
        assert task_row is not None

        from adminme.products.openclaw import main as openclaw_main

        original_what_now = openclaw_main.core._what_now
        rerank_calls = 0

        def counted_what_now(runtime_arg: Any, session_arg: Any) -> dict[str, Any]:
            nonlocal rerank_calls
            rerank_calls += 1
            return original_what_now(runtime_arg, session_arg)

        monkeypatch.setattr(openclaw_main.core, "_what_now", counted_what_now)
        lifecycle_body = {
            "provenance": _provenance("lifecycle-once"),
            "request_id": "complete-secondary",
            "item_kind": "task",
            "item_id": "task_fixture_secondary",
            "action": "complete",
            "expected_last_event_id": task_row["last_event_id"],
        }
        completed = _post(client, "/api/openclaw/v1/tools/lifecycle", lifecycle_body)
        assert completed["task"]["status"] == "done"
        assert completed["duplicate"] is False
        assert rerank_calls == 1
        rerank_calls = 0
        lifecycle_retry_body = {
            **lifecycle_body,
            "provenance": _provenance("lifecycle-retry"),
        }
        completed_retry = _post(
            client, "/api/openclaw/v1/tools/lifecycle", lifecycle_retry_body
        )
        assert completed_retry["event_id"] == completed["event_id"]
        assert completed_retry["duplicate"] is True
        assert rerank_calls == 1

        commitment_row = (
            runtime.projections.connection("commitments")
            .execute(
                "SELECT * FROM commitments WHERE tenant_id=? AND commitment_id=?",
                (runtime.settings.instance.tenant_id, output["commitment_id"]),
            )
            .fetchone()
        )
        review_params = {
            "request_id": "approve-through-openclaw",
            "action": "approve",
            "commitment_id": output["commitment_id"],
            "expected_last_event_id": commitment_row["last_event_id"],
            "send_acknowledgement": True,
        }
        receipt_id = _allow_once(
            client,
            receipt_id="ocapr-gate4-review",
            tool_name="administrateme_review",
            params=review_params,
        )
        review_body = {
            "provenance": _provenance("approval-once"),
            **review_params,
            "openclaw_approval_receipt_id": receipt_id,
        }
        approved = _post(client, "/api/openclaw/v1/tools/review", review_body)
        review_retry_body = {
            **review_body,
            "provenance": _provenance("approval-retry"),
        }
        approved_retry = _post(
            client, "/api/openclaw/v1/tools/review", review_retry_body
        )
        assert approved["duplicate"] is False
        assert approved_retry["duplicate"] is True
        assert approved_retry["event_id"] == approved["event_id"]

        rows = client.portal.call(
            _events,
            runtime,
            "task.completed",
            "commitment.confirmed",
            "observation.suppressed",
            "openclaw.tool_call.recorded",
        )
        assert len([row for row in rows if row["type"] == "task.completed"]) == 1
        assert len([row for row in rows if row["type"] == "commitment.confirmed"]) == 1
        suppressed = [row for row in rows if row["type"] == "observation.suppressed"]
        assert len(suppressed) == 1
        lifecycle_audits = [
            row
            for row in rows
            if row["type"] == "openclaw.tool_call.recorded"
            and row["payload"]["operation"] == "tools.lifecycle.task.complete"
        ]
        approval_audits = [
            row
            for row in rows
            if row["type"] == "openclaw.tool_call.recorded"
            and row["payload"]["operation"] == "tools.review.approve"
        ]
        assert len(lifecycle_audits) == 2
        assert len(approval_audits) == 2
        assert {row["correlation_id"] for row in lifecycle_audits} == {
            "lifecycle-once",
            "lifecycle-retry",
        }
        assert {row["correlation_id"] for row in approval_audits} == {
            "approval-once",
            "approval-retry",
        }
        assert runtime.fixture_adapter.delivered == []


def test_openclaw_outage_restart_and_incompatible_upgrade_preserve_truth(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    settings = _settings(instance_dir)
    app = create_application(settings)
    source_event_id: str

    with TestClient(app) as client:
        output = _seed_slice(client, "gate4-restart")
        source_event_id = output["source_event_id"]
        unavailable = _post(
            client,
            "/api/openclaw/v1/health/report",
            {
                "state": "unavailable",
                "reason": "fixture gateway stopped",
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
            },
        )
        assert unavailable["state"] == "unavailable"
        diagnostics = client.get("/api/system/ready").json()
        assert (
            diagnostics["capabilities"]["standing_order:morning_digest"]["state"]
            == "degraded"
        )
        assert (
            client.get("/api/core/v1/what-now", headers=DEMO_HEADERS).status_code == 200
        )

    restarted = create_application(settings)
    with TestClient(restarted) as client:
        assert restarted.state.runtime.openclaw_state == "unavailable"
        stored = client.portal.call(
            restarted.state.runtime.event_log.get, source_event_id
        )
        assert stored is not None
        incompatible = _post(
            client,
            "/api/openclaw/v1/health/report",
            {
                "state": "healthy",
                "reason": "future fixture",
                "openclaw_version": "2099.1.0",
                "openclaw_commit": "f" * 40,
            },
            status=409,
        )
        assert incompatible["detail"]["code"] == "incompatible_openclaw"
        assert restarted.state.runtime.openclaw_state == "incompatible"
        diagnostics = client.get("/api/system/ready").json()
        assert (
            diagnostics["capabilities"]["standing_order:morning_digest"]["state"]
            == "blocked"
        )
        assert (
            client.get("/api/core/v1/what-now", headers=DEMO_HEADERS).status_code == 200
        )
        states = client.portal.call(
            _events, restarted.state.runtime, "openclaw.integration.state_changed"
        )
        assert [row["payload"]["state"] for row in states][-2:] == [
            "unavailable",
            "incompatible",
        ]

    final_restart = create_application(settings)
    with TestClient(final_restart):
        assert final_restart.state.runtime.openclaw_state == "incompatible"
