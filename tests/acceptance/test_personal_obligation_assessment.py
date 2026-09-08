"""Source-to-pipeline process proof for qualified personal obligations.

Only the external language seam is replaced. Native observation schemas,
context assembly, evidence validation, durable pipelines, proposal production,
reconciliation and member projections use the production composition root.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-obligation-process-member"  # fixture:tenant_data:ok
CONTRACT = "personal-obligation-assessment-v1"

CASES: dict[str, dict[str, Any]] = {
    "required_medical_form_in_bulk_mail": {
        "body": "Complete the required intake form before October 9, 2030.",
        "quote": "Complete the required intake form before October 9, 2030.",
        "purpose": "preparation",
        "actionability": "required",
        "owner": "member",
        "target_kind": "task",
        "candidate": {"due": "2030-10-09T00:00:00Z"},
        "date_role": "deadline",
        "qualifiers": [("date", "October 9, 2030")],
        "expected_kind": "task",
        "expected_decision": "admit",
        "bulk": True,
    },
    "paid_concert_has_future_attendance": {
        "body": "Payment received; your ticket is paid in full. Your concert ticket is confirmed for October 10, 2030 at 19:00 UTC in Fixture Hall.",
        "quote": "Your concert ticket is confirmed for October 10, 2030 at 19:00 UTC in Fixture Hall.",
        "purpose": "appointment",
        "actionability": "required",
        "owner": "member",
        "target_kind": "calendar_event",
        "candidate": {"start": "2030-10-10T19:00:00Z", "location": "Fixture Hall"},
        "date_role": "occurrence",
        "qualifiers": [("date", "October 10, 2030 at 19:00 UTC")],
        "expected_kind": "calendar_event",
        "expected_decision": "admit",
        "bulk": True,
    },
    "zero_balance_cannot_resurrect_payment": {
        "body": "Please pay by October 15, 2030. Your balance is $0; no payment is required.",
        "quote": "Please pay by October 15, 2030.",
        "purpose": "payment",
        "actionability": "none",
        "owner": "member",
        "target_kind": "task",
        "candidate": {"due": "2030-10-15T00:00:00Z"},
        "date_role": "deadline",
        "qualifiers": [
            ("negation", "Your balance is $0; no payment is required."),
            ("date", "October 15, 2030"),
        ],
        "expected_kind": None,
        "bulk": True,
    },
    "autopay_does_not_create_manual_payment": {
        "body": "Please pay your bill by October 16, 2030. Autopay will pay this bill on time; no action is needed.",
        "quote": "Please pay your bill by October 16, 2030.",
        "purpose": "payment",
        "actionability": "informational",
        "owner": "member",
        "target_kind": "task",
        "candidate": {"due": "2030-10-16T00:00:00Z"},
        "date_role": "deadline",
        "qualifiers": [
            ("negation", "Autopay will pay this bill on time; no action is needed.")
        ],
        "expected_kind": None,
        "bulk": True,
    },
    "explicit_empty_semantic_result_stays_empty": {
        "body": "Please call us if you have questions. No response or action is required.",
        "no_candidates": True,
        "expected_kind": None,
        "bulk": True,
    },
    "imessage_outbound_member_promise": {
        "body": "I will bring the chairs on October 12, 2030 at 18:00 UTC.",
        "quote": "I will bring the chairs on October 12, 2030 at 18:00 UTC.",
        "purpose": "member_promise",
        "actionability": "required",
        "owner": "member",
        "target_kind": "commitment",
        "candidate": {"due": "2030-10-12T18:00:00Z"},
        "date_role": "deadline",
        "qualifiers": [
            ("date", "October 12, 2030 at 18:00 UTC"),
            ("ownership", "I will bring the chairs"),
        ],
        "expected_kind": "commitment",
        "expected_decision": "admit",
        "provider": "bluebubbles",
        "channel": "imessage",
        "outbound": True,
    },
    "imessage_group_request_is_not_member_promise": {
        "body": "Can someone bring chairs? Please volunteer if you can.",
        "quote": "Can someone bring chairs?",
        "purpose": "personal_request",
        "actionability": "conditional",
        "owner": "unknown",
        "target_kind": "task",
        "candidate": {},
        "date_role": "none",
        "condition": "if you can",
        "condition_state": "unknown",
        "qualifiers": [("condition", "if you can")],
        "expected_kind": "task",
        "expected_decision": "unresolved",
        "provider": "bluebubbles",
        "channel": "imessage",
        "group": True,
    },
    "generic_weekly_report_is_not_personal_recurrence": {
        "body": "Your weekly account summary is ready. Report delivery repeats every Monday; no response is required.",
        "quote": "Report delivery repeats every Monday; no response is required.",
        "purpose": "status_update",
        "actionability": "informational",
        "owner": "member",
        "target_kind": "recurrence",
        "candidate": {"rrule": "FREQ=WEEKLY;BYDAY=MO"},
        "date_role": "reporting",
        "qualifiers": [
            ("recurrence", "every Monday"),
            ("negation", "no response is required"),
        ],
        "expected_kind": None,
        "provider": "fixture",
        "channel": "future_reviewed_channel",
        "bulk": True,
    },
}


async def _events(runtime: Any, event_type: str) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since(types=[event_type])]


def _settings(instance: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance,
        encryption_key=b"o" * 32,
        repository_root=REPO_ROOT,
        member_identities={"obligation-process.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Fixture household",
            "members": [
                {"member_id": MEMBER, "label": "Fixture adult", "role": "principal"}
            ],
            "memberships": [],
            "relationships": [],
            "parties": [],
        },
        adapter_config={"adapter_supervisor_port": 0, "adapters": []},
        fixture_member_id=MEMBER,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )


def _source(runtime: Any, name: str, case: dict[str, Any]) -> EventEnvelope:
    occurred_at = "2030-10-01T12:00:00Z"
    outbound = bool(case.get("outbound"))
    common = {
        "member_id": MEMBER,
        "provider": case.get("provider", "gmail"),
        "provider_account_id": "fixture-obligation-account",
        "workspace": "personal",
        "source_channel": case.get("channel", "gmail"),
        "thread_id": f"fixture-thread-{name}",
        "native_message_id": f"fixture-message-{name}",
        "subject": None,
        "body_text": case["body"],
        "to_identifier": "fixture-peer@example.invalid"
        if outbound
        else "fixture-member@example.invalid",
        "routing_scope": "member_private",
        "routing_target_id": MEMBER,
    }
    if outbound:
        payload = {
            **common,
            "sent_at": occurred_at,
            "delivery_status": "sent",
            "send_as": "member",
        }
    else:
        payload = {
            **common,
            "received_at": occurred_at,
            "attachments": [],
            "from_identifier": "no-reply@example.invalid"
            if case.get("bulk")
            else "fixture-peer@example.invalid",
            "to_identifiers": [
                "fixture-member@example.invalid",
                "fixture-other-peer@example.invalid",
            ]
            if case.get("group")
            else ["fixture-member@example.invalid"],
            "provider_labels": ["CATEGORY_UPDATES"] if case.get("bulk") else ["INBOX"],
        }
    return EventEnvelope(
        event_at_ms=1_917_086_400_000,
        tenant_id=runtime.settings.instance.tenant_id,
        type="messaging.sent" if outbound else "messaging.received",
        schema_version=5,
        occurred_at=occurred_at,
        source_adapter="fixture-obligation-source",
        source_account_id="fixture-obligation-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity="normal",
        idempotency_key=f"fixture-obligation-source:{name}",
        payload=payload,
    )


def _language_output(inputs: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    assert inputs["assessment_contract"] == CONTRACT
    pack = inputs["context_pack"]
    alias = pack["current_source_id"]
    current = next(
        message for message in pack["messages"] if message["source_id"] == alias
    )
    assert current["body_text"] == case["body"]
    assert current["direction"] == ("outbound" if case.get("outbound") else "inbound")
    candidates: list[dict[str, Any]] = []
    if not case.get("no_candidates"):
        candidate = {
            "target_kind": case["target_kind"],
            "title": case["quote"],
            "evidence_source_id": alias,
            "evidence_quote": case["quote"],
            "candidate": case["candidate"],
            "confidence": 0.9,
            "reasons": ["fixture_grounded_interpretation"],
            "action_owner": case["owner"],
            "lifecycle_state": "open",
            "compact_summary": case["quote"][:160],
            "case_summary": None,
            "why_member": None,
            "next_step": None,
            "thread_state": "unclear" if case.get("group") else "waiting_on_member",
            "unresolved_questions": ["Who is volunteering?"]
            if case.get("group")
            else [],
            "related_source_ids": [],
            "obligation_assessment": {
                "purpose": case["purpose"],
                "actionability": case["actionability"],
                "condition": case.get("condition"),
                "condition_state": case.get("condition_state", "not_applicable"),
                "beneficiary": None,
                "date_role": case["date_role"],
                "qualifying_evidence": [
                    {"source_id": alias, "quote": quote, "role": role}
                    for role, quote in case.get("qualifiers", [])
                ],
            },
        }
        candidates.append(candidate)
        if case.get("group"):
            # A model's additional commitment guess must be rejected because
            # the source is incoming and nobody has personally promised it.
            candidates.append({**candidate, "target_kind": "commitment"})
    return {
        "assessment_contract": CONTRACT,
        "classification": "transactional" if case.get("bulk") else "personal",
        "confidence": 0.9,
        "reasons": ["fixture_completed_semantic_assessment"],
        "reply_signal": "unknown",
        "automated": bool(case.get("bulk")),
        "transactional_due": False,
        "security_code": False,
        "person_to_person": not case.get("bulk", False),
        "deadline": None,
        "candidates": candidates,
    }


@pytest.mark.parametrize("name", list(CASES))
def test_source_qualified_obligation_reaches_production_triage(
    tmp_path: Path, name: str
) -> None:
    case = CASES[name]
    instance = tmp_path / name
    instance.mkdir()
    app = create_application(_settings(instance))
    schema = json.loads(
        (
            REPO_ROOT
            / "packs/skills/classify_message_nature/schemas/output.schema.json"
        ).read_text()
    )
    with TestClient(app) as client:
        runtime = app.state.runtime
        calls: list[dict[str, Any]] = []

        async def language_seam(
            skill_id: str, inputs: dict[str, Any], context: Any
        ) -> SimpleNamespace:
            assert skill_id == "skill:classify_message_nature"
            output = _language_output(inputs, case)
            jsonschema.validate(output, schema)
            calls.append(
                {"source_event_id": context.triggering_event_id, "output": output}
            )
            return SimpleNamespace(output=output)

        runtime.run_skill_fn = language_seam
        # The production runner captures the seam at startup. Replace that
        # same dependency too; no pipeline handler or classifier is invoked
        # directly by this test.
        runtime.pipelines._run_skill_fn = runtime.run_skill_fn
        source_id = client.portal.call(
            runtime.events.write, _source(runtime, name, case)
        )
        client.portal.call(
            runtime.wait_for_subscriber, "pipeline:pipeline:noise_filtering", source_id
        )
        assert len(calls) == 1
        assert calls[0]["source_event_id"] == source_id
        assessments = [
            event
            for event in client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            if event["payload"]["source_event_id"] == source_id
        ]
        assert len(assessments) == 1
        assert assessments[0]["payload"]["extraction_state"] == "complete"
        proposals = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.proposed")
            if event["payload"]["source_event_id"] == source_id
        ]
        if case["expected_kind"] is None:
            assert proposals == [], proposals
            assert assessments[0]["payload"]["assessment_outcome"] == "no_candidate"
            assert assessments[0]["payload"]["candidates"] == []
        else:
            assert len(proposals) == 1, proposals
            proposal = proposals[0]
            assert proposal["schema_version"] == 5
            assert proposal["source_adapter"] == "deterministic_candidate_eligibility"
            assert proposal["payload"]["target_kind"] == case["expected_kind"]
            interpretation = proposal["payload"]["interpretation"]
            assert interpretation["contract"] == "triage-context-interpretation-v3"
            assessment = interpretation["obligation_assessment"]
            assert assessment["evidence_verified"] is True
            assert assessment["purpose"] == case["purpose"]
            assert assessment["actionability"] == case["actionability"]
            assert assessment["independent_obligation"] == (not bool(case.get("group")))
            assert all(
                item["source_event_id"] == source_id and item["quote"] in case["body"]
                for item in assessment["evidence"]
            )
            client.portal.call(
                runtime.wait_for_subscriber,
                "triage-reconciliation:admission",
                proposal["event_id"],
            )
            decisions = [
                event
                for event in client.portal.call(
                    runtime.event_log.get_by_causation, proposal["event_id"]
                )
                if event["type"] == "triage.reconciliation.decided"
            ]
            assert decisions, (
                "The production reconciler must account for the actual pipeline proposal"
            )
            decision = decisions[-1]
            assert decision["payload"]["decision"] == case["expected_decision"]
            assert decision["payload"]["provider_effects"] is False
            client.portal.call(
                runtime.wait_for_projection, "triage", decision["event_id"]
            )
            row = (
                runtime.projections.connection("triage")
                .execute(
                    "SELECT admission_decision, priority_rank, owner_member_id FROM triage_items WHERE tenant_id=? AND proposal_id=?",
                    (
                        runtime.settings.instance.tenant_id,
                        proposal["payload"]["proposal_id"],
                    ),
                )
                .fetchone()
            )
            assert row["admission_decision"] == case["expected_decision"]
            assert row["owner_member_id"] == MEMBER
            if case.get("outbound"):
                assert row["priority_rank"] == 10
                assert decision["payload"]["member_authored_text_digest"]
            if case.get("group"):
                assert interpretation["action_owner"] == "unknown"
                assert interpretation["unresolved_questions"]
                assert (
                    "exact_member_commitment"
                    not in decision["payload"]["priority_basis"]
                )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_completed") == []
        )
        assert client.portal.call(_events, runtime, "external_sync.write_failed") == []
