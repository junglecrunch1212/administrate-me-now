"""AC-52 production-composition proof for G04 human-centered Triage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.source_policy import SourceCoordinate


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"h" * 32
TENANT_MEMBER_A = "fixture-g04-process-a"  # fixture:tenant_data:ok
TENANT_MEMBER_B = "fixture-g04-process-b"  # fixture:tenant_data:ok
A_HEADERS = {"Tailscale-User-Login": "g04-a.ts.net"}
B_HEADERS = {"Tailscale-User-Login": "g04-b.ts.net"}


def _settings(instance_dir: Path, *, active: bool) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "g04-a.ts.net": TENANT_MEMBER_A,
            "g04-b.ts.net": TENANT_MEMBER_B,
        },
        member_roles={TENANT_MEMBER_A: "principal", TENANT_MEMBER_B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Fixture household",
            "members": [
                {
                    "member_id": TENANT_MEMBER_A,
                    "label": "Fixture adult A",
                    "role": "principal",
                },
                {
                    "member_id": TENANT_MEMBER_B,
                    "label": "Fixture adult B",
                    "role": "principal",
                },
            ],
            "memberships": [],
            "relationships": [],
            "parties": [],
        },
        adapter_config={"adapter_supervisor_port": 0, "adapters": []},
        fixture_member_id=TENANT_MEMBER_A,
        reconciliation_admission_enabled=active,
        human_triage_management_enabled=active,
    )


def _coordinate(tenant_id: str) -> SourceCoordinate:
    return SourceCoordinate(
        tenant_id=tenant_id,
        owner_principal_id=TENANT_MEMBER_A,
        owner_kind="member",
        context_member_id=TENANT_MEMBER_A,
        adapter_category="communication",
        adapter_kind="fixture-communication",
        adapter_instance_id="fixture-g04-adapter",
        provider="fixture",
        provider_account_id="fixture-g04-account",
        workspace="personal",
        source_kind="messaging.received",
        source_class="passive_communication",
        trusted_route=f"member_private:{TENANT_MEMBER_A}",
    )


def _proposal(tenant_id: str, index: int, *, event_at_ms: int) -> EventEnvelope:
    proposal_id = f"g04-proposal-{index:03d}"
    return EventEnvelope(
        event_at_ms=event_at_ms,
        tenant_id=tenant_id,
        type="triage.item.proposed",
        schema_version=1,
        occurred_at=f"2030-01-01T00:{index % 60:02d}:00Z",
        source_adapter="fixture-g04-candidate",
        source_account_id="fixture-g04-account",
        owner_scope=f"private:{TENANT_MEMBER_A}",
        visibility_scope=f"private:{TENANT_MEMBER_A}",
        sensitivity="normal",
        idempotency_key=f"g04-proposal:{index}",
        payload={
            "proposal_id": proposal_id,
            "target_kind": "task",
            "owner_member_id": TENANT_MEMBER_A,
            "title": f"Fixture candidate {index:03d}",
            "candidate": {},
            "route": {"member_id": TENANT_MEMBER_A},
            "source_event_id": f"g04-source-{index:03d}",
            "confidence": 0.5,
            "reasons": ["fixture_candidate"],
        },
    )


def _decision(
    tenant_id: str,
    index: int,
    decision: str,
    *,
    event_at_ms: int,
) -> EventEnvelope:
    proposal_id = f"g04-proposal-{index:03d}"
    coordinate = _coordinate(tenant_id)
    alert = decision == "alert_only"
    basis = (
        ["explicit_member_instruction"]
        if index == 0
        else ["known_human_actionable_request"]
        if index == 1
        else ["standard_actionable_candidate"]
    )
    digest = hashlib.sha256(f"{tenant_id}|{proposal_id}".encode()).hexdigest()
    return EventEnvelope(
        event_at_ms=event_at_ms,
        tenant_id=tenant_id,
        type="triage.reconciliation.decided",
        schema_version=1,
        occurred_at=f"2030-01-01T01:{index % 60:02d}:00Z",
        source_adapter="triage-reconciliation-v1",
        source_account_id=TENANT_MEMBER_A,
        owner_scope=f"private:{TENANT_MEMBER_A}",
        visibility_scope=f"private:{TENANT_MEMBER_A}",
        sensitivity="normal",
        actor_identity="pipeline:triage-reconciliation-v1",
        idempotency_key=f"g04-reconciliation:{index}",
        payload={
            "reconciliation_id": f"recon_{digest[:32]}",
            "proposal_id": proposal_id,
            "primary_proposal_id": None,
            "matter_id": f"ctx_reconciled_{digest[32:64]}",
            "owner_member_id": TENANT_MEMBER_A,
            "source_event_id": f"g04-source-{index:03d}",
            "evidence_source_event_ids": [f"g04-source-{index:03d}"],
            "member_authored_text_digest": None,
            "relationships": [],
            "decision": decision,
            "decision_revision": 1,
            "supersedes_reconciliation_event_id": None,
            "policy": {
                "policy_key": coordinate.key,
                "policy_revision": 0,
                "policy_origin": "recommended_default",
                "triage_route": "review" if decision == "admit" else "none",
                "alert_route": "security" if alert else "none",
                "context_route": "metadata_only",
                "reason_codes": ["fixture_policy"],
            },
            "source_coordinate": coordinate.model_dump(mode="json"),
            "freshness": "current",
            "priority_rank": index if index < 2 else 60,
            "priority_basis": basis,
            "reason_codes": [f"fixture_{decision}"],
            "decided_at": f"2030-01-01T01:{index % 60:02d}:00Z",
            "admission_active": True,
            "raw_cross_member_join": False,
            "weak_similarity_changed_identity": False,
            "weak_similarity_transferred_ownership": False,
            "weak_similarity_authorized_action": False,
            "provider_effects": False,
            "llm_authority": False,
        },
    )


async def _event_count(runtime: Any) -> int:
    return await runtime.event_log.count()


async def _event_types(runtime: Any) -> list[str]:
    result: list[str] = []
    async for event in runtime.event_log.read_since():
        result.append(str(event["type"]))
    return result


def test_g04_admission_preview_actions_learning_isolation_and_replay(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "g04-active"
    instance.mkdir()
    settings = _settings(instance, active=True)
    first_snapshot: tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]] | None = None
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        tenant_id = runtime.settings.instance.tenant_id
        events: list[EventEnvelope] = []
        for index in range(105):
            decision = (
                "admit"
                if index < 2
                else "alert_only"
                if index == 2
                else "context_only"
                if index % 2
                else "source_policy_suppression"
            )
            events.extend(
                (
                    _proposal(
                        tenant_id, index, event_at_ms=1_900_000_000_000 + index * 2
                    ),
                    _decision(
                        tenant_id,
                        index,
                        decision,
                        event_at_ms=1_900_000_000_001 + index * 2,
                    ),
                )
            )
        event_ids = client.portal.call(runtime.events.write_batch, events)
        client.portal.call(runtime.wait_for_projection, "triage", event_ids[-1])
        client.portal.call(runtime.wait_for_projection, "context_fabric", event_ids[-1])

        page_response = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
            headers=A_HEADERS,
        )
        assert page_response.status_code == 200, page_response.text
        page = page_response.json()
        assert page["human_triage_management"] == {
            "active": True,
            "default_off": True,
            "backlog_preview_available": True,
            "backlog_application_available": False,
            "provider_effects": 0,
        }
        matters = page["triage"]["matters"]
        assert len(matters) == 100
        assert page["triage"]["inventory"]["total_candidates"] == 105
        assert page["triage"]["inventory"]["views"]["history"]["candidates"] == 102
        assert {
            candidate["proposal_id"]
            for matter in matters
            for candidate in matter["candidates"]
            if candidate["attention_view"] != "history"
        } == {
            "g04-proposal-000",
            "g04-proposal-001",
            "g04-proposal-002",
        }
        assert [value["queue_lane"] for value in matters[:2]] == [
            "direct_instruction",
            "known_human_request",
        ]
        assert page["triage"]["inventory"]["views"]["alerts"]["candidates"] == 1
        assert all(
            candidate["disposition"]["disposition"] in {"ordinary_queue", "alerts"}
            for matter in matters
            for candidate in matter["candidates"]
            if candidate["attention_view"] != "history"
        )
        ready_response = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=ready&triage_limit=100",
            headers=A_HEADERS,
        )
        assert ready_response.status_code == 200, ready_response.text
        assert {item["proposal_id"] for item in ready_response.json()["items"]} == {
            "g04-proposal-000",
            "g04-proposal-001",
        }
        assert (
            ready_response.json()["triage"]["inventory"]["views"]["ready"]["candidates"]
            == 2
        )
        browser_code = client.get("/member-assets/member.js", headers=A_HEADERS).text
        assert "/api/member/v1/triage/backlog-reassessment-preview" in browser_code
        assert "Reopen this local decision" in browser_code
        assert "Narrow one step" in browser_code
        assert "Nothing here contacts a provider" in browser_code

        before_preview = client.portal.call(_event_count, runtime)
        preview = client.get(
            "/api/member/v1/triage/backlog-reassessment-preview?limit=100",
            headers=A_HEADERS,
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["mutates_backlog"] is False
        assert preview.json()["source_events_deleted"] == 0
        assert preview.json()["provider_effects"] == 0
        assert preview.json()["application_available_in_phase_a"] is False
        assert preview.json()["batch"]["total"] == 105
        previewed = {value["proposal_id"]: value for value in preview.json()["changes"]}
        assert previewed["g04-proposal-002"]["old"]["disposition"] == "handled"
        assert previewed["g04-proposal-002"]["proposed"]["disposition"] == "alerts"
        assert previewed["g04-proposal-003"]["old"]["disposition"] == "handled"
        assert previewed["g04-proposal-003"]["proposed"]["disposition"] == "context"
        assert client.portal.call(_event_count, runtime) == before_preview

        managed = client.post(
            "/api/member/v1/triage/g04-proposal-000/manage",
            headers=A_HEADERS,
            json={
                "request_id": "g04-manage-context-000",
                "expected_last_event_id": next(
                    candidate["last_event_id"]
                    for matter in matters
                    for candidate in matter["candidates"]
                    if candidate["proposal_id"] == "g04-proposal-000"
                ),
                "action": "keep_context",
                "reason_codes": ["member_context_only"],
                "evidence_event_ids": ["g04-source-000"],
            },
        )
        assert managed.status_code == 200, managed.text
        assert managed.json()["reversible"] is True
        assert managed.json()["provider_effects"] == 0
        reopened = client.post(
            "/api/member/v1/triage/g04-proposal-000/manage",
            headers=A_HEADERS,
            json={
                "request_id": "g04-manage-reopen-000",
                "expected_last_event_id": managed.json()["event_id"],
                "action": "reopen",
                "reason_codes": ["member_reopened"],
                "evidence_event_ids": [],
            },
        )
        assert reopened.status_code == 200, reopened.text

        feedback_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_900_000_001_000,
                tenant_id=tenant_id,
                type="triage.feedback.recorded",
                schema_version=1,
                occurred_at="2030-01-01T02:00:00Z",
                source_adapter="member-portal",
                source_account_id=TENANT_MEMBER_A,
                owner_scope=f"private:{TENANT_MEMBER_A}",
                visibility_scope=f"private:{TENANT_MEMBER_A}",
                sensitivity="normal",
                actor_identity=TENANT_MEMBER_A,
                idempotency_key="g04-feedback-1",
                payload={
                    "feedback_id": "g04-feedback-1",
                    "proposal_id": "g04-proposal-001",
                    "member_id": TENANT_MEMBER_A,
                    "reason": "spam",
                    "context_id": None,
                    "target_kind": "task",
                    "source_adapter": "fixture-g04-candidate",
                    "provider_account_id": "fixture-g04-account",
                    "workspace": "personal",
                    "sender_digest": "c" * 64,
                    "recorded_at": "2030-01-01T02:00:00Z",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "context_fabric", feedback_id)
        preview_body = {
            "scope_kind": "candidate_kind",
            "scope_value": "task",
            "instruction": "never",
            "outcome_kind": "dismiss_not_useful",
        }
        rule_preview = client.post(
            "/api/member/v1/triage-learning/preview",
            headers=A_HEADERS,
            json=preview_body,
        )
        assert rule_preview.status_code == 200, rule_preview.text
        assert rule_preview.json()["self_activates"] is False
        proposed = client.post(
            "/api/member/v1/triage-learning/propose",
            headers=A_HEADERS,
            json={
                **preview_body,
                "request_id": "g04-policy-propose-1",
                "preview_digest": rule_preview.json()["preview_digest"],
            },
        )
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["self_activated"] is False
        policy_proposal_id = proposed.json()["policy_proposal_id"]
        isolated = client.post(
            f"/api/member/v1/triage-learning/{policy_proposal_id}/decide",
            headers=B_HEADERS,
            json={
                "request_id": "g04-policy-cross-adult",
                "decision": "approve",
                "instruction": "never",
                "preview_digest": rule_preview.json()["preview_digest"],
                "trial_proposal_ids": [],
            },
        )
        assert isolated.status_code == 404
        trial = client.post(
            f"/api/member/v1/triage-learning/{policy_proposal_id}/decide",
            headers=A_HEADERS,
            json={
                "request_id": "g04-policy-trial-1",
                "decision": "trial",
                "instruction": "never",
                "preview_digest": rule_preview.json()["preview_digest"],
                "trial_proposal_ids": ["g04-proposal-001"],
            },
        )
        assert trial.status_code == 200, trial.text
        rule_id = trial.json()["rule_id"]
        trial_page = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
            headers=A_HEADERS,
        )
        assert trial_page.status_code == 200, trial_page.text
        assert {
            candidate["proposal_id"]
            for value in trial_page.json()["triage"]["matters"]
            for candidate in value["candidates"]
            if candidate["attention_view"] != "history"
        } == {"g04-proposal-000", "g04-proposal-002"}
        assert (
            trial_page.json()["triage"]["inventory"]["views"]["history"]["candidates"]
            == 103
        )
        assert (
            trial_page.json()["triage"]["inventory"]["views"]["ready"]["candidates"]
            == 1
        )
        assert "g04-proposal-001" in {
            item["proposal_id"] for item in trial_page.json()["items"]
        }
        suspended = client.post(
            f"/api/member/v1/triage-learning/rules/{rule_id}/review",
            headers=A_HEADERS,
            json={
                "request_id": "g04-policy-suspend-1",
                "decision": "suspend",
                "negative_outcome": "missed_obligation",
                "replacement_instruction": None,
                "reason": "Fixture obligation was hidden",
            },
        )
        assert suspended.status_code == 200, suspended.text
        learning = client.get("/api/member/v1/triage-learning", headers=A_HEADERS)
        assert learning.status_code == 200, learning.text
        assert learning.json()["rules"][0]["status"] == "suspended"
        assert learning.json()["provider_effects"] == 0
        restored_page = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
            headers=A_HEADERS,
        )
        assert restored_page.status_code == 200, restored_page.text
        assert {
            candidate["proposal_id"]
            for value in restored_page.json()["triage"]["matters"]
            for candidate in value["candidates"]
            if candidate["attention_view"] != "history"
        } == {
            "g04-proposal-000",
            "g04-proposal-001",
            "g04-proposal-002",
        }
        assert (
            restored_page.json()["triage"]["inventory"]["views"]["ready"]["candidates"]
            == 2
        )
        assert (
            restored_page.json()["triage"]["inventory"]["views"]["history"][
                "candidates"
            ]
            == 102
        )

        event_types = client.portal.call(_event_types, runtime)
        assert not {
            "member.provider_action.confirmed",
            "external_sync.write_requested",
            "external_sync.write_completed",
        }.intersection(event_types)
        first_snapshot = (
            [
                tuple(row)
                for row in runtime.projections.connection("triage").execute(
                    """SELECT proposal_id, human_lane, management_state,
                              management_revision, admission_decision
                           FROM triage_items ORDER BY proposal_id"""
                )
            ],
            [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT rule_id, member_id, instruction, status,
                              last_review_decision
                           FROM triage_source_policy_rules ORDER BY rule_id"""
                )
            ],
        )

    assert first_snapshot is not None
    with TestClient(create_application(settings)) as replay_client:
        runtime = replay_client.app.state.runtime
        replay_snapshot = (
            [
                tuple(row)
                for row in runtime.projections.connection("triage").execute(
                    """SELECT proposal_id, human_lane, management_state,
                              management_revision, admission_decision
                           FROM triage_items ORDER BY proposal_id"""
                )
            ],
            [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT rule_id, member_id, instruction, status,
                              last_review_decision
                           FROM triage_source_policy_rules ORDER BY rule_id"""
                )
            ],
        )
        assert replay_snapshot == first_snapshot


def test_g04_management_is_default_off(tmp_path: Path) -> None:
    instance = tmp_path / "g04-default-off"
    instance.mkdir()
    with TestClient(create_application(_settings(instance, active=False))) as client:
        runtime = client.app.state.runtime
        event_id = client.portal.call(
            runtime.events.write,
            _proposal(
                runtime.settings.instance.tenant_id, 0, event_at_ms=1_910_000_000_000
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", event_id)
        response = client.post(
            "/api/member/v1/triage/g04-proposal-000/manage",
            headers=A_HEADERS,
            json={
                "request_id": "g04-default-off-action",
                "expected_last_event_id": event_id,
                "action": "mark_handled",
                "reason_codes": [],
                "evidence_event_ids": [],
            },
        )
        assert response.status_code == 409
        assert "separately activated" in response.text
