"""Production-composition proofs for the reviewed source × candidate matrix."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "member_lifecycle_matrix"
TENANT_LOGIN = "lifecycle-matrix.ts.net"
SECRET = "lifecycle-matrix-shared-secret-at-least-32-bytes"
AUTH = {"Tailscale-User-Login": TENANT_LOGIN}
OPENCLAW_AUTH = {"Authorization": f"Bearer {SECRET}"}
TRIAGE_KINDS = (
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "list",
    "list_item",
    "recurrence",
    "party_identifier",
    "party_candidate",
    "party_relationship",
    "relationship_fact",
)


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=b"l" * 32,
        repository_root=REPO_ROOT,
        member_identities={TENANT_LOGIN: MEMBER},
        member_roles={MEMBER: "principal"},
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "administrateme-portal|member-portal|actor-lifecycle-matrix": MEMBER
        },
        deterministic_openclaw_fixture=True,
        legacy_personal_obligation_compatibility=True,
    )


def _grant_cos(client: TestClient, request_id: str) -> None:
    response = client.post(
        "/api/member/v1/cos/delegations",
        headers=AUTH,
        json={
            "request_id": request_id,
            "context_scope": "member_private",
            "permissions": ["read_context", "manage_internal_work"],
        },
    )
    assert response.status_code == 201, response.text


def _private_envelope(
    runtime: Any,
    *,
    event_type: str,
    key: str,
    payload: dict[str, Any],
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=runtime.settings.instance.tenant_id,
        type=event_type,
        schema_version=(
            4
            if event_type == "messaging.received"
            else 4
            if event_type == "messaging.sent"
            else 1
        ),
        occurred_at="2026-08-09T12:00:00Z",
        source_adapter="lifecycle-matrix-fixture",
        source_account_id="matrix-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity="normal",
        idempotency_key=f"lifecycle-matrix:{key}",
        payload=payload,
    )


async def _proposals_for_source(runtime: Any, source_event_id: str) -> list[dict]:
    direct = await runtime.event_log.get_by_causation(source_event_id)
    proposals: list[dict] = []
    for event in direct:
        if event["type"] != "triage.intent.features_extracted":
            continue
        proposals.extend(
            item
            for item in await runtime.event_log.get_by_causation(event["event_id"])
            if item["type"] == "triage.item.proposed"
        )
    return proposals


def test_message_and_note_sources_reach_every_triage_target(tmp_path: Path) -> None:
    app = create_application(_settings(tmp_path / "instance-sources"))
    with TestClient(app) as client:
        runtime = app.state.runtime
        for index, kind in enumerate(TRIAGE_KINDS):
            message_body = f"[fixture:triage-kind={kind}] Exact {kind} message fact"
            if kind == "recurrence":
                message_body = "[fixture:triage-kind=recurrence] Please review the fixture checklist every Monday."
            message_event_type = (
                "messaging.sent" if kind == "commitment" else "messaging.received"
            )
            message_payload = {
                "provider": "gmail",
                "provider_account_id": "matrix-account",
                "member_id": MEMBER,
                "source_channel": "gmail",
                "native_message_id": f"message-{index}",
                "thread_id": f"thread-{index}",
                "to_identifier": (
                    "source@example.invalid"
                    if message_event_type == "messaging.sent"
                    else "member@example.invalid"
                ),
                "workspace": "personal",
                "subject": "Lifecycle matrix",
                "body_text": message_body,
            }
            if message_event_type == "messaging.sent":
                message_payload.update(
                    {
                        "sent_at": "2026-08-09T12:00:00Z",
                        "delivery_status": "sent",
                    }
                )
            else:
                message_payload.update(
                    {
                        "from_identifier": "source@example.invalid",
                        "received_at": "2026-08-09T12:00:00Z",
                        "provider_labels": ["INBOX"],
                        "attachments": [],
                    }
                )
            message_id = client.portal.call(
                runtime.events.write,
                _private_envelope(
                    runtime,
                    event_type=message_event_type,
                    key=f"message-{kind}",
                    payload=message_payload,
                ),
            )
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:noise_filtering",
                message_id,
            )
            message_proposals = client.portal.call(
                _proposals_for_source, runtime, message_id
            )
            assert {item["payload"]["target_kind"] for item in message_proposals} == {
                kind
            }
            if kind == "recurrence":
                [recurrence_proposal] = message_proposals
                assessment = recurrence_proposal["payload"]["interpretation"][
                    "obligation_assessment"
                ]
                assert assessment["independent_obligation"] is True
                assert assessment["recurrence_grounded"] is True
                assert any(
                    evidence["role"] == "recurrence"
                    and evidence["quote"] == "every Monday"
                    for evidence in assessment["evidence"]
                )

            note_body = f"[fixture:triage-kind={kind}] Exact {kind} note fact"
            if kind == "recurrence":
                note_body = "[fixture:triage-kind=recurrence] Review the fixture checklist every Monday."
            note_id = client.portal.call(
                runtime.events.write,
                _private_envelope(
                    runtime,
                    event_type="member_knowledge.note_added",
                    key=f"note-{kind}",
                    payload={
                        "knowledge_id": f"matrix-note-{index}",
                        "member_id": MEMBER,
                        "title": f"Matrix {kind}",
                        "body": note_body,
                        "source_kind": "fixture",
                        "source_external_id": f"matrix-note-{index}",
                        "content_sha256": hashlib.sha256(
                            note_body.encode()
                        ).hexdigest(),
                        "captured_at": "2026-08-09T12:00:00Z",
                    },
                ),
            )
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:member_knowledge_indexing",
                note_id,
            )
            note_proposals = client.portal.call(_proposals_for_source, runtime, note_id)
            assert {item["payload"]["target_kind"] for item in note_proposals} == {kind}

        triage = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()
        assert set(TRIAGE_KINDS) <= {item["target_kind"] for item in triage["items"]}


def test_member_can_reassess_visible_source_without_losing_provenance(
    tmp_path: Path,
) -> None:
    """Browser command -> source-linked intent -> proposal through production root."""

    app = create_application(_settings(tmp_path / "instance-source-review"))
    with TestClient(app) as client:
        runtime = app.state.runtime
        source_event_id = client.portal.call(
            runtime.events.write,
            _private_envelope(
                runtime,
                event_type="messaging.received",
                key="manual-source-review",
                payload={
                    "provider": "gmail",
                    "provider_account_id": "matrix-account",
                    "member_id": MEMBER,
                    "source_channel": "gmail",
                    "native_message_id": "manual-source-review",
                    "thread_id": "manual-source-review-thread",
                    "from_identifier": "source@example.invalid",
                    "to_identifier": "member@example.invalid",
                    "workspace": "personal",
                    "subject": "School form",
                    "body_text": "Please submit the school form tomorrow.",
                    "received_at": "2026-08-09T12:00:00Z",
                    "provider_labels": ["INBOX"],
                    "attachments": [],
                },
            ),
        )

        response = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": "Please submit the school form tomorrow.",
                "source_surface": "comms",
                "kind_hint": "task",
                "source_event_id": source_event_id,
                "client_request_id": "manual-source-review-request",
            },
        )
        assert response.status_code == 202, response.text
        result = response.json()
        assert result["state"] == "pending_review"
        assert len(result["proposal_ids"]) == 1

        linked = client.portal.call(runtime.event_log.get_by_causation, source_event_id)
        intent = next(
            event for event in linked if event["type"] == "triage.intent.submitted"
        )
        assert intent["payload"]["source_event_id"] == source_event_id
        assert intent["payload"]["source_surface"] == "comms"
        assert intent["sensitivity"] == "normal"

        triage = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()
        proposal = next(
            item
            for item in triage["items"]
            if item["proposal_id"] == result["proposal_ids"][0]
        )
        assert proposal["source_event_id"] == intent["event_id"]

        browser_code = client.get("/member-assets/member.js").text
        assert '"Send to Triage"' in browser_code
        assert "sourceEventId: message.evidence_event_ids[0]" in browser_code
        assert "source_event_id: sourceEventId" in browser_code


def test_member_defines_and_edits_recurrence_and_ordered_routine(
    tmp_path: Path,
) -> None:
    """Member UI commands preserve typed definitions and optimistic history."""

    app = create_application(_settings(tmp_path / "instance-rhythm-definitions"))
    with TestClient(app) as client:
        recurrence = client.put(
            "/api/member/v1/recurrences/matrix-weekly-reset",
            headers=AUTH,
            json={
                "request_id": "matrix-recurrence-create",
                "scope": "member",
                "kind": "weekly_reset",
                "rrule": "FREQ=WEEKLY;BYDAY=SU",
                "next_occurrence": "2026-08-09T14:00:00Z",
                "lead_time_days": 1,
                "trackable": True,
                "notes": "Reset the launch pad",
            },
        )
        assert recurrence.status_code == 200, recurrence.text
        first = recurrence.json()["recurrence"]
        assert first["kind"] == "weekly_reset"
        assert first["status"] == "active"

        updated = client.put(
            "/api/member/v1/recurrences/matrix-weekly-reset",
            headers=AUTH,
            json={
                "request_id": "matrix-recurrence-update",
                "expected_last_event_id": first["last_event_id"],
                "scope": "member",
                "kind": "weekly_household_reset",
                "rrule": "FREQ=WEEKLY;BYDAY=SA",
                "next_occurrence": "2026-08-15T14:00:00Z",
                "lead_time_days": 2,
                "trackable": True,
                "notes": "Reviewed Saturday rhythm",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["recurrence"]["kind"] == "weekly_household_reset"
        assert updated.json()["recurrence"]["last_event_id"] != first["last_event_id"]

        routine = client.put(
            "/api/member/v1/routines/matrix-morning-launch",
            headers=AUTH,
            json={
                "request_id": "matrix-routine-create",
                "title": "Morning launch",
                "timezone": "America/New_York",
                "starts_on": "2026-08-09",
                "rrule": "FREQ=DAILY",
                "window_start_local": "07:00",
                "window_end_local": "08:00",
                "steps": [
                    {
                        "step_id": "water",
                        "title": "Drink water",
                        "position": 0,
                        "micro_step": "Fill the glass",
                    },
                    {
                        "step_id": "bag",
                        "title": "Check launch bag",
                        "position": 1,
                    },
                ],
            },
        )
        assert routine.status_code == 200, routine.text
        first_routine = routine.json()["routine"]
        assert [step["step_id"] for step in first_routine["steps"]] == [
            "water",
            "bag",
        ]

        edited_routine = client.put(
            "/api/member/v1/routines/matrix-morning-launch",
            headers=AUTH,
            json={
                "request_id": "matrix-routine-update",
                "expected_last_event_id": first_routine["last_event_id"],
                "title": "School-day launch",
                "timezone": "America/New_York",
                "starts_on": "2026-08-10",
                "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
                "window_start_local": "07:00",
                "window_end_local": "07:45",
                "steps": [
                    {
                        "step_id": "water",
                        "title": "Drink water",
                        "position": 0,
                        "micro_step": "Fill the glass",
                    },
                    {
                        "step_id": "shoes",
                        "title": "Shoes by the door",
                        "position": 1,
                    },
                ],
            },
        )
        assert edited_routine.status_code == 200, edited_routine.text
        final_routine = edited_routine.json()["routine"]
        assert final_routine["title"] == "School-day launch"
        assert [step["step_id"] for step in final_routine["steps"]] == [
            "water",
            "shoes",
        ]
        assert (
            client.put(
                "/api/member/v1/routines/matrix-morning-launch",
                headers=AUTH,
                json={
                    "request_id": "matrix-routine-stale-update",
                    "expected_last_event_id": first_routine["last_event_id"],
                    "title": "Stale edit",
                    "timezone": "America/New_York",
                    "starts_on": "2026-08-10",
                    "rrule": "FREQ=DAILY",
                    "window_start_local": "07:00",
                    "window_end_local": "08:00",
                    "steps": [{"step_id": "water", "title": "Water", "position": 0}],
                },
            ).status_code
            == 409
        )

        scoreboard = client.get(
            "/api/member/v1/surfaces/scoreboard", headers=AUTH
        ).json()["scoreboard"]
        assert {item["object_kind"] for item in scoreboard["rhythms"]} == {
            "recurrence",
            "routine",
        }
        browser_code = client.get("/member-assets/member.js").text
        assert '"Add recurrence"' in browser_code
        assert '"Add routine"' in browser_code
        assert "/api/member/v1/routines/" in browser_code


def test_structured_finance_and_reviewed_context_rules_only_propose_to_triage(
    tmp_path: Path,
) -> None:
    """Privileged finance and typed context stay deterministic and human-gated."""

    app = create_application(_settings(tmp_path / "instance-structured-sources"))
    with TestClient(app) as client:
        runtime = app.state.runtime
        finance_source_ids = []
        for index, occurred_at in enumerate(
            ("2026-05-01T12:00:00Z", "2026-06-01T12:00:00Z", "2026-07-01T12:00:00Z")
        ):
            source_id = client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=1_778_000_000_000 + index,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="money_flow.provider_observed",
                    schema_version=1,
                    occurred_at=occurred_at,
                    source_adapter="plaid-fixture",
                    source_account_id="matrix-account",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="privileged",
                    idempotency_key=f"structured-finance:{index}",
                    payload={
                        "member_id": MEMBER,
                        "plaid_item_id": "item-fixture",
                        "account_id": "account-fixture",
                        "transaction_id": f"transaction-{index}",
                        "amount_minor": 12_500 + index * 100,
                        "currency": "USD",
                        "occurred_at": occurred_at,
                        "kind": "paid",
                        "merchant_name": "Household Utility",
                        "pending": False,
                        "provider_revision": f"revision-{index}",
                        "observed_at": occurred_at,
                    },
                ),
            )
            finance_source_ids.append(source_id)
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:deterministic_source_candidates",
                source_id,
            )

        finance_proposals = client.portal.call(
            _proposals_for_source, runtime, finance_source_ids[-1]
        )
        assert [row["payload"]["target_kind"] for row in finance_proposals] == [
            "recurrence"
        ]
        recurrence = finance_proposals[0]
        assert recurrence["sensitivity"] == "privileged"
        assert recurrence["payload"]["candidate"]["rrule"] == "FREQ=MONTHLY"
        assert recurrence["payload"]["candidate"]["trackable"] is False
        assert (
            not client.portal.call(
                runtime.event_log.get_by_causation,
                finance_source_ids[-1],
            )[0]
            .get("payload", {})
            .get("skill_call_event_id")
        )

        context_source_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_778_000_000_100,
                tenant_id=runtime.settings.instance.tenant_id,
                type="context.signal.observed",
                schema_version=1,
                occurred_at="2026-08-09T12:00:00Z",
                source_adapter="home-assistant-fixture",
                source_account_id="matrix-home",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="structured-context:filter",
                payload={
                    "signal_id": "signal-filter-dirty",
                    "signal_key": "hvac:filter-state",
                    "member_id": MEMBER,
                    "source": "home_assistant",
                    "kind": "device_state",
                    "state": "dirty",
                    "observed_at": "2026-08-09T12:00:00Z",
                    "expires_at": "2026-08-09T13:00:00Z",
                    "attributes": {
                        "_administrateme_candidate_rules": [
                            {
                                "rule_id": "hvac-filter-task",
                                "target_kind": "task",
                                "title": "Replace the HVAC filter",
                                "operator": "equals",
                                "expected_state": "dirty",
                                "candidate": {"energy": "low", "effort_min": 10},
                            },
                            {
                                "rule_id": "hvac-filter-reminder",
                                "target_kind": "reminder",
                                "title": "Remember the replacement filter",
                                "operator": "equals",
                                "expected_state": "dirty",
                                "candidate": {"priority": 1},
                            },
                            {
                                "rule_id": "hvac-filter-recurrence",
                                "target_kind": "recurrence",
                                "title": "Review HVAC filter every quarter",
                                "operator": "equals",
                                "expected_state": "dirty",
                                "candidate": {
                                    "rrule": "FREQ=MONTHLY;INTERVAL=3",
                                    "next_occurrence": "2026-11-09T12:00:00Z",
                                    "kind": "maintenance",
                                    "trackable": True,
                                },
                            },
                        ]
                    },
                },
            ),
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:deterministic_source_candidates",
            context_source_id,
        )
        context_proposals = client.portal.call(
            _proposals_for_source, runtime, context_source_id
        )
        assert {row["payload"]["target_kind"] for row in context_proposals} == {
            "task",
            "reminder",
            "recurrence",
        }
        assert all(
            row["payload"]["eligibility_rule_ids"][0].startswith(
                "reviewed_context_rule:"
            )
            for row in context_proposals
        )

        triage = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()
        assert any(
            item["title"] == "Recurring payment: Household Utility"
            for item in triage["items"]
        )
        assert any(
            item["title"] == "Replace the HVAC filter" for item in triage["items"]
        )
        assert (
            not client.portal.call(
                runtime.event_log.get_by_causation, context_source_id
            )[0]
            .get("payload", {})
            .get("skill_call_event_id")
        )


def test_openclaw_clock_expires_only_stale_unconfirmed_commitments(
    tmp_path: Path,
) -> None:
    """OpenClaw owns time; fixed code owns the lifecycle decision."""

    app = create_application(_settings(tmp_path / "instance-lifecycle-clock"))
    with TestClient(app) as client:
        _grant_cos(client, "lifecycle-clock-cos")
        runtime = app.state.runtime

        async def seed(commitment_id: str, confirm: bool) -> str:
            proposed_id = await runtime.events.write(
                EventEnvelope(
                    event_at_ms=1_751_370_000_000,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="commitment.proposed",
                    schema_version=1,
                    occurred_at="2026-07-01T12:00:00Z",
                    source_adapter="lifecycle-clock-fixture",
                    source_account_id="matrix-account",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    idempotency_key=f"lifecycle-clock:{commitment_id}:proposed",
                    payload={
                        "commitment_id": commitment_id,
                        "kind": "other",
                        "owed_by_member_id": MEMBER,
                        "owed_to_party_id": "party-recipient",
                        "text_summary": "Old proposed promise",
                        "urgency": "this_week",
                        "confidence": 0.9,
                        "strength": "confident",
                    },
                )
            )
            await runtime.wait_for_projection("commitments", proposed_id)
            if not confirm:
                return proposed_id
            confirmed_id = await runtime.events.write(
                EventEnvelope(
                    event_at_ms=1_751_370_000_100,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="commitment.confirmed",
                    schema_version=1,
                    occurred_at="2026-07-02T12:00:00Z",
                    source_adapter="member-triage",
                    source_account_id=MEMBER,
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    causation_id=proposed_id,
                    idempotency_key=f"lifecycle-clock:{commitment_id}:confirmed",
                    payload={
                        "commitment_id": commitment_id,
                        "confirmed_by_member_id": MEMBER,
                        "confirmed_at": "2026-07-02T12:00:00Z",
                    },
                )
            )
            await runtime.wait_for_projection("commitments", confirmed_id)
            return confirmed_id

        client.portal.call(seed, "commitment-expire-me", False)
        client.portal.call(seed, "commitment-keep-me", True)
        response = client.post(
            "/api/openclaw/v1/standing-orders/trigger",
            headers=OPENCLAW_AUTH,
            json={
                "provenance": {
                    "actor_id": "actor-lifecycle-matrix",
                    "agent_id": "main",
                    "session_key": "adminme:lifecycle-matrix:standing-order",
                    "session_id": "run-lifecycle-maintenance",
                    "channel_id": "administrateme-portal",
                    "account_id": "member-portal",
                    "message_id": "message-lifecycle-maintenance",
                    "thread_id": None,
                    "correlation_id": "lifecycle-maintenance-trigger",
                    "call_kind": "standing_order",
                    "openclaw_version": OPENCLAW_VERSION,
                    "openclaw_commit": OPENCLAW_COMMIT,
                },
                "program_id": "reminder_dispatch",
                "automation_id": "administrateme-reminder-dispatch",
                "trigger_id": "2026-08-09T12:00:00Z",
            },
        )
        assert response.status_code == 200, response.text
        trigger_event_id = response.json()["event_id"]
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:lifecycle_maintenance",
            trigger_event_id,
        )
        client.portal.call(
            runtime.wait_for_projection,
            "commitments",
            client.portal.call(
                runtime.event_log.get_by_idempotency,
                runtime.settings.instance.tenant_id,
                "commitment-expiry:v1:commitment-expire-me",
            )["event_id"],
        )

        rows = (
            runtime.projections.connection("commitments")
            .execute(
                "SELECT commitment_id, status, confirmed_at FROM commitments ORDER BY commitment_id"
            )
            .fetchall()
        )
        states = {row["commitment_id"]: dict(row) for row in rows}
        assert states["commitment-expire-me"]["status"] == "expired"
        assert states["commitment-expire-me"]["confirmed_at"] is None
        assert states["commitment-keep-me"]["status"] == "pending"
        assert states["commitment-keep-me"]["confirmed_at"] is not None
        assert (
            client.portal.call(
                runtime.event_log.get_by_idempotency,
                runtime.settings.instance.tenant_id,
                "commitment-expiry:v1:commitment-keep-me",
            )
            is None
        )


def test_openclaw_add_for_review_reaches_every_triage_target(tmp_path: Path) -> None:
    app = create_application(_settings(tmp_path / "instance-openclaw"))
    with TestClient(app) as client:
        _grant_cos(client, "lifecycle-add-review-cos")
        for index, kind in enumerate(TRIAGE_KINDS):
            response = client.post(
                "/api/openclaw/v1/tools/add-for-review",
                headers=OPENCLAW_AUTH,
                json={
                    "provenance": {
                        "actor_id": "actor-lifecycle-matrix",
                        "agent_id": "main",
                        "session_key": "session-lifecycle-matrix",
                        "session_id": "run-lifecycle-matrix",
                        "channel_id": "administrateme-portal",
                        "account_id": "member-portal",
                        "message_id": f"message-{index}",
                        "thread_id": None,
                        "correlation_id": f"lifecycle-matrix:{index}",
                        "call_kind": "tool",
                        "openclaw_version": OPENCLAW_VERSION,
                        "openclaw_commit": OPENCLAW_COMMIT,
                    },
                    "request_id": f"openclaw-lifecycle-kind-{index:02d}",
                    "text": f"Exact OpenClaw submission for {kind}",
                    "kind_hint": kind,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["state"] == "pending_review"
            assert len(response.json()["proposal_event_ids"]) == 1
