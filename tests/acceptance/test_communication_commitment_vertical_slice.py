"""Gate 3 black-box proof of the first complete household slice."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from adminme.demo import FIXTURE_MEMBER_EMAIL, FIXTURE_MEMBER_ID, FIXTURE_SENDER_EMAIL
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import RuntimeSettings, create_application

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"g" * 32
AUTH = {
    "Tailscale-User-Login": "fixture-principal.ts.net",
    "X-Correlation-Id": "demo:golden-1",
}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "fixture-principal.ts.net": FIXTURE_MEMBER_ID,
            "fixture-child.ts.net": "member_fixture_child",
            "fixture-other.ts.net": "member_fixture_other",
        },
        member_roles={
            FIXTURE_MEMBER_ID: "principal",
            "member_fixture_child": "child",
            "member_fixture_other": "principal",
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=FIXTURE_MEMBER_ID,
    )


def _post(client: TestClient, path: str, payload: dict, *, headers: dict | None = None):
    response = client.post(path, json=payload, headers=headers or AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def test_communication_to_completed_commitment_is_durable_scoped_and_replay_safe(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    settings = _settings(instance_dir)
    app = create_application(settings)
    body_text = "Could you reply today with the fixture household update?"

    with TestClient(app) as client:
        runtime = app.state.runtime
        assert client.get("/api/comms/v1/commitments/pending").status_code == 401
        malformed = client.post(
            "/api/comms/v1/demo/canonical-message",
            headers=AUTH,
            json={"request_id": "bad", "native_message_id": "bad", "extra": True},
        )
        assert malformed.status_code == 422

        result = _post(
            client,
            "/api/comms/v1/demo/canonical-message",
            {
                "request_id": "golden-1",
                "native_message_id": "fixture-native-001",
                "body": body_text,
                "received_at": "2026-07-30T12:05:00Z",
            },
        )
        output = result["output"]
        source_event_id = output["source_event_id"]
        interaction_id = output["interaction_id"]
        commitment_id = output["commitment_id"]
        assert output["state"] == "proposal_only"

        duplicate_ingest = _post(
            client,
            "/api/comms/v1/demo/canonical-message",
            {
                "request_id": "golden-duplicate",
                "native_message_id": "fixture-native-001",
                "body": body_text,
                "received_at": "2026-07-30T12:05:00Z",
            },
        )
        assert duplicate_ingest["output"] == output
        conflicting_duplicate = client.post(
            "/api/comms/v1/demo/canonical-message",
            headers=AUTH,
            json={
                "request_id": "golden-conflict",
                "native_message_id": "fixture-native-001",
                "body": "Different payload for the same native id",
                "received_at": "2026-07-30T12:05:00Z",
            },
        )
        assert conflicting_duplicate.status_code == 409

        pending = client.get("/api/comms/v1/commitments/pending", headers=AUTH).json()[
            "items"
        ]
        assert [row["commitment_id"] for row in pending] == [commitment_id]
        proposal = pending[0]
        assert proposal["confirmed_at"] is None
        assert proposal["source_interaction_id"] == interaction_id
        assert proposal["source_skill"] == "commitment_extraction"
        triage = client.get("/api/core/v1/triage", headers=AUTH).json()
        assert {(item["kind"], item["id"]) for item in triage["items"]} == {
            ("commitment", commitment_id),
        }
        proposal_item = next(
            item for item in triage["items"] if item["id"] == commitment_id
        )
        assert proposal_item["actions"] == ["edit", "approve", "dismiss"]

        participants = (
            runtime.projections.connection("interactions")
            .execute(
                "SELECT party_id, role FROM interaction_participants "
                "WHERE tenant_id=? AND interaction_id=? ORDER BY role",
                (settings.instance.tenant_id, interaction_id),
            )
            .fetchall()
        )
        assert {tuple(row) for row in participants} >= {
            ("party_fixture_sender", "from"),
        }

        related = client.portal.call(
            runtime.event_log.get_by_causation, source_event_id
        )
        recorded = [e for e in related if e["type"] == "skill.call.recorded"]
        assert {e["payload"]["skill_name"] for e in recorded} >= {
            "skill:classify_commitment_candidate",
            "skill:extract_commitment_fields",
        }
        assert all(
            e["payload"]["provider"] == "fixture/deterministic-v1" for e in recorded
        )
        assert all(
            e["owner_scope"] == f"private:{FIXTURE_MEMBER_ID}"
            and e["visibility_scope"] == f"private:{FIXTURE_MEMBER_ID}"
            and e["sensitivity"] == "normal"
            for e in recorded
        )
        assert len([e for e in related if e["type"] == "commitment.proposed"]) == 1

        child = {"Tailscale-User-Login": "fixture-child.ts.net"}
        other = {"Tailscale-User-Login": "fixture-other.ts.net"}
        assert (
            client.get("/api/comms/v1/commitments/pending", headers=child).json()[
                "items"
            ]
            == []
        )
        assert (
            client.get("/api/comms/v1/commitments/pending", headers=other).json()[
                "items"
            ]
            == []
        )
        view_other = {**AUTH, "X-AdminMe-View-Member": "member_fixture_other"}
        assert (
            client.get("/api/comms/v1/commitments/pending", headers=view_other).json()[
                "items"
            ]
            == []
        )
        denied = client.post(
            f"/api/comms/v1/commitments/{commitment_id}/approve",
            headers=child,
            json={
                "request_id": "child",
                "expected_last_event_id": proposal["last_event_id"],
            },
        )
        assert denied.status_code == 403

        stale = client.post(
            f"/api/comms/v1/commitments/{commitment_id}/edit",
            headers=AUTH,
            json={
                "request_id": "edit-stale",
                "expected_last_event_id": "ev_stale",
                "description": "Reply with the verified household update",
            },
        )
        assert stale.status_code == 409
        edited = _post(
            client,
            f"/api/comms/v1/commitments/{commitment_id}/edit",
            {
                "request_id": "edit-1",
                "expected_last_event_id": proposal["last_event_id"],
                "description": "Reply with the verified household update",
                "due_at": "2026-08-01T17:00:00Z",
            },
        )
        assert (
            edited["commitment"]["description"]
            == "Reply with the verified household update"
        )

        before_approval = client.get("/api/core/v1/what-now", headers=AUTH).json()
        assert before_approval["horizons"]["right_now"]["kind"] == "task"
        approved = _post(
            client,
            f"/api/comms/v1/commitments/{commitment_id}/approve",
            {
                "request_id": "approve-1",
                "expected_last_event_id": edited["commitment"]["last_event_id"],
                "send_acknowledgement": True,
            },
        )
        assert approved["commitment"]["confirmed_at"] is not None
        assert approved["delivery"]["held"] is True
        assert approved["delivery"]["reason"] == "openclaw_approval_required"
        assert approved["pending_provider_action_event_id"] == (
            approved["delivery"]["domain_approval_event_id"]
        )
        assert runtime.fixture_adapter.delivered == []
        approved_again = _post(
            client,
            f"/api/comms/v1/commitments/{commitment_id}/approve",
            {
                "request_id": "approve-1",
                "expected_last_event_id": edited["commitment"]["last_event_id"],
                "send_acknowledgement": True,
            },
        )
        assert approved_again["duplicate"] is True
        assert approved_again["event_id"] == approved["event_id"]

        after_approval = client.get("/api/core/v1/what-now", headers=AUTH).json()
        assert list(after_approval["horizons"]) == [
            "right_now",
            "next",
            "today",
            "this_week",
            "this_month",
            "someday",
        ]
        assert after_approval["horizons"]["right_now"]["id"] == commitment_id
        assert "obligation" in after_approval["horizons"]["right_now"]["explanation"]

        current = approved["commitment"]
        blocked = _post(
            client,
            f"/api/core/v1/commitments/{commitment_id}/block",
            {
                "request_id": "block-1",
                "expected_last_event_id": current["last_event_id"],
                "reason": "Waiting for fixture information",
            },
        )
        assert blocked["commitment"]["status"] == "blocked"
        assert blocked["what_now"]["horizons"]["right_now"]["kind"] == "task"
        blocked_again = _post(
            client,
            f"/api/core/v1/commitments/{commitment_id}/block",
            {
                "request_id": "block-1",
                "expected_last_event_id": current["last_event_id"],
                "reason": "Waiting for fixture information",
            },
        )
        assert blocked_again["duplicate"] is True
        unblocked = _post(
            client,
            f"/api/core/v1/commitments/{commitment_id}/unblock",
            {
                "request_id": "unblock-1",
                "expected_last_event_id": blocked["commitment"]["last_event_id"],
                "note": "Fixture information arrived",
            },
        )
        snoozed = _post(
            client,
            f"/api/core/v1/commitments/{commitment_id}/snooze",
            {
                "request_id": "snooze-1",
                "expected_last_event_id": unblocked["commitment"]["last_event_id"],
                "until": "2026-08-02T09:00:00Z",
            },
        )
        deferred = _post(
            client,
            f"/api/core/v1/commitments/{commitment_id}/defer",
            {
                "request_id": "defer-1",
                "expected_last_event_id": snoozed["commitment"]["last_event_id"],
                "until": "2026-08-03T09:00:00Z",
                "reason": "Fixture scheduling",
            },
        )
        completed = _post(
            client,
            f"/api/core/v1/commitments/{commitment_id}/complete",
            {
                "request_id": "complete-1",
                "expected_last_event_id": deferred["commitment"]["last_event_id"],
                "note": "Replied through the synthetic workflow",
            },
        )
        assert completed["commitment"]["status"] == "done"
        assert (
            completed["what_now"]["horizons"]["right_now"]["id"]
            == "task_fixture_secondary"
        )
        completed_again = _post(
            client,
            f"/api/core/v1/commitments/{commitment_id}/complete",
            {
                "request_id": "complete-1",
                "expected_last_event_id": deferred["commitment"]["last_event_id"],
                "note": "Replied through the synthetic workflow",
            },
        )
        assert completed_again["duplicate"] is True

        evidence = client.get(
            f"/api/comms/v1/interactions/{interaction_id}/evidence", headers=AUTH
        ).json()
        assert {e["type"] for e in evidence["events"]} >= {
            "messaging.received",
            "interaction.participant_resolved",
            "skill.call.recorded",
            "commitment.proposed",
        }
        decision_chain = client.portal.call(
            runtime.event_log.get_by_correlation, "demo:golden-1"
        )
        assert any(
            e["type"] == "member.provider_action.confirmed" for e in decision_chain
        )
        assert not any(
            e["type"].startswith("external_sync.write_") for e in decision_chain
        )

        workbook_path = settings.instance.xlsx_workbooks_dir / "adminme-ops.xlsx"
        workbook = load_workbook(workbook_path, data_only=False)
        sheet = workbook["Commitments"]
        headers = [cell.value for cell in sheet[1]]
        row = dict(zip(headers, [cell.value for cell in sheet[2]], strict=True))
        assert row["commitment_id"] == commitment_id
        assert row["status"] == "done"
        assert row["source_skill"] == "commitment_extraction"
        assert row["owner_scope"] == f"private:{FIXTURE_MEMBER_ID}"
        assert (
            sheet.cell(2, headers.index("source_skill") + 1).protection.locked is True
        )

        before_rebuild = tuple(
            completed["commitment"][key]
            for key in (
                "commitment_id",
                "description",
                "status",
                "confirmed_at",
                "completed_at",
                "source_interaction_id",
                "source_skill",
            )
        )
        client.portal.call(runtime.projections.rebuild, "commitments")
        rebuilt = client.get(
            f"/api/core/v1/commitments/{commitment_id}", headers=AUTH
        ).json()
        after_rebuild = tuple(
            rebuilt[key]
            for key in (
                "commitment_id",
                "description",
                "status",
                "confirmed_at",
                "completed_at",
                "source_interaction_id",
                "source_skill",
            )
        )
        assert after_rebuild == before_rebuild
        client.portal.call(runtime.xlsx_projection.regenerate_now, "adminme-ops.xlsx")

        # A separate proposal exercises dismissal and duplicate dismissal.
        dismiss_input = _post(
            client,
            "/api/comms/v1/demo/canonical-message",
            {
                "request_id": "dismiss-input",
                "native_message_id": "fixture-native-dismiss",
                "body": "Could you reply today with a second fixture update?",
                "received_at": "2026-07-30T12:06:00Z",
            },
        )
        dismiss_id = dismiss_input["output"]["commitment_id"]
        dismiss_row = next(
            row
            for row in client.get(
                "/api/comms/v1/commitments/pending", headers=AUTH
            ).json()["items"]
            if row["commitment_id"] == dismiss_id
        )
        dismissed = _post(
            client,
            f"/api/comms/v1/commitments/{dismiss_id}/dismiss",
            {
                "request_id": "dismiss-1",
                "expected_last_event_id": dismiss_row["last_event_id"],
                "reason": "Synthetic proposal is not an obligation",
            },
        )
        assert dismissed["commitment"]["status"] == "cancelled"
        dismissed_again = _post(
            client,
            f"/api/comms/v1/commitments/{dismiss_id}/dismiss",
            {
                "request_id": "dismiss-1",
                "expected_last_event_id": dismiss_row["last_event_id"],
                "reason": "Synthetic proposal is not an obligation",
            },
        )
        assert dismissed_again["duplicate"] is True
        assert dismissed_again["event_id"] == dismissed["event_id"]
        corrupt_dismiss = client.post(
            f"/api/comms/v1/commitments/{dismiss_id}/dismiss",
            headers=AUTH,
            json={
                "request_id": "dismiss-1",
                "expected_last_event_id": dismiss_row["last_event_id"],
                "reason": "Different body under a reused command id",
            },
        )
        assert corrupt_dismiss.status_code == 409

        # Bad remote shapes remain durable inputs but resolve to defensive suppression.
        for index, marker in enumerate(
            ("malformed", "unreachable", "timeout"), start=1
        ):
            failed = client.post(
                "/api/comms/v1/demo/canonical-message",
                headers=AUTH,
                json={
                    "request_id": f"bad-{index}",
                    "native_message_id": f"fixture-bad-{index}",
                    "body": f"[fixture:{marker}] request",
                    "received_at": "2035-01-01T00:00:00Z",
                },
            )
            assert failed.status_code == 503
            failed_source = client.portal.call(
                runtime.event_log.get_by_idempotency,
                settings.instance.tenant_id,
                f"fixture-message:fixture-bad-{index}",
            )
            failures_before = len(
                [
                    event
                    for event in client.portal.call(
                        runtime.event_log.get_by_causation,
                        failed_source["event_id"],
                    )
                    if event["type"] == "skill.call.failed"
                ]
            )
            failed_duplicate = client.post(
                "/api/comms/v1/demo/canonical-message",
                headers=AUTH,
                json={
                    "request_id": f"bad-{index}-duplicate",
                    "native_message_id": f"fixture-bad-{index}",
                    "body": f"[fixture:{marker}] request",
                    "received_at": "2035-01-01T00:00:00Z",
                },
            )
            assert failed_duplicate.status_code == 503
            failures_after = len(
                [
                    event
                    for event in client.portal.call(
                        runtime.event_log.get_by_causation,
                        failed_source["event_id"],
                    )
                    if event["type"] == "skill.call.failed"
                ]
            )
            assert failures_after == failures_before

        original_projections = runtime.projections
        runtime.projections = None
        try:
            assert (
                client.get(
                    "/api/comms/v1/commitments/pending", headers=AUTH
                ).status_code
                == 503
            )
            assert client.get("/api/core/v1/triage", headers=AUTH).status_code == 503
            assert client.get("/api/core/v1/what-now", headers=AUTH).status_code == 503
        finally:
            runtime.projections = original_projections

        stored = client.portal.call(runtime.event_log.get, source_event_id)
        assert stored["schema_version"] == 2
        assert stored["idempotency_key"] == "fixture-message:fixture-native-001"
        skill_count = len(
            [
                e
                for e in client.portal.call(
                    runtime.event_log.get_by_causation, source_event_id
                )
                if e["type"] == "skill.call.recorded"
            ]
        )

    assert body_text.encode() not in settings.instance.event_log_path.read_bytes()
    assert (
        FIXTURE_SENDER_EMAIL.encode()
        not in settings.instance.event_log_path.read_bytes()
    )

    # Append without EventWriter.notify to model a crash after durable commit.
    async def append_without_notify() -> str:
        log = EventLog(settings.instance, TEST_KEY)
        try:
            return await log.append(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=settings.instance.tenant_id,
                    type="messaging.received",
                    schema_version=2,
                    occurred_at="2036-01-01T00:00:00Z",
                    source_adapter="fixture-crash",
                    source_account_id="fixture-account",
                    owner_scope=f"private:{FIXTURE_MEMBER_ID}",
                    visibility_scope=f"private:{FIXTURE_MEMBER_ID}",
                    sensitivity="privileged",
                    idempotency_key="fixture-message:crash-after-append",
                    payload={
                        "native_message_id": "crash-after-append",
                        "source_channel": "fixture-cat-a",
                        "from_identifier": FIXTURE_SENDER_EMAIL,
                        "to_identifier": FIXTURE_MEMBER_EMAIL,
                        "thread_id": "fixture-thread:crash",
                        "subject": None,
                        "body_text": "Privileged synthetic crash recovery message",
                        "body_html": None,
                        "received_at": "2036-01-01T00:00:00Z",
                        "attachments": [],
                    },
                )
            )
        finally:
            await log.close()

    crash_event_id = asyncio.run(append_without_notify())
    restarted_app = create_application(settings)
    with TestClient(restarted_app) as client:
        runtime = restarted_app.state.runtime
        deadline = time.monotonic() + 8
        interaction_ids: set[str] = set()
        while time.monotonic() < deadline:
            interaction_ids = {
                row["interaction_id"]
                for row in client.get(
                    "/api/comms/v1/interactions", headers=AUTH
                ).json()["items"]
            }
            if f"ix_{crash_event_id}" in interaction_ids:
                break
            time.sleep(0.05)
        assert f"ix_{crash_event_id}" in interaction_ids
        privileged_derived = client.portal.call(
            runtime.event_log.get_by_causation, crash_event_id
        )
        assert not any(e["type"] == "skill.call.recorded" for e in privileged_derived)
        assert (
            len(
                [
                    e
                    for e in privileged_derived
                    if e["type"] == "commitment.suppressed"
                    and e["payload"]["reason"] == "privileged_input"
                ]
            )
            == 2
        )
        replay = _post(
            client,
            "/api/comms/v1/demo/canonical-message",
            {
                "request_id": "golden-after-restart",
                "native_message_id": "fixture-native-001",
                "body": body_text,
                "received_at": "2026-07-30T12:05:00Z",
            },
        )
        assert replay["output"]["source_event_id"] == source_event_id
        assert replay["output"]["commitment_id"] == commitment_id
        assert (
            len(
                [
                    e
                    for e in client.portal.call(
                        runtime.event_log.get_by_causation, source_event_id
                    )
                    if e["type"] == "skill.call.recorded"
                ]
            )
            == skill_count
        )
        rows = (
            runtime.projections.connection("commitments")
            .execute(
                "SELECT COUNT(*) FROM commitments WHERE tenant_id=? AND commitment_id=?",
                (settings.instance.tenant_id, commitment_id),
            )
            .fetchone()
        )
        assert rows[0] == 1
