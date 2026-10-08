"""Recorded G06 output through the real commitment review and approval path.

The fixed transport reuses the synthetic round-3 response from classifier 8.0.3
at 56f14290f7f5441b31bbed8120f68e0ee41cc06d. It is not another model inference.
Only the external llm-task transport is substituted; receipts, extraction,
encrypted state, scope checks, projections and member decisions are production.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import adminme.lib.skill_runner.wrapper as wrapper
import adminme.runtime.application as application
from adminme.events.envelope import EventEnvelope
from tools.triage_model_lab import (
    HEADERS,
    MEMBER,
    _events,
    _member_surface,
    _settle,
    _source_envelope,
    load_frozen_cases,
)


ROOT = Path(__file__).resolve().parents[2]
OTHER = "fixture-counterparty-other-member"
VISIBLE_PARTY = "fixture-counterparty-visible"
HIDDEN_PARTY = "fixture-counterparty-private"
RECORDED_OUTPUT = json.loads(r"""{
  "classification": "personal",
  "confidence": 0.98,
  "reasons": [
    "Direct outbound person-to-person message",
    "The member explicitly promises to bring chairs"
  ],
  "reply_signal": "not_required",
  "automated": false,
  "transactional_due": false,
  "security_code": false,
  "person_to_person": true,
  "deadline": null,
  "assessment_contract": "personal-obligation-assessment-v1",
  "candidates": [
    {
      "target_kind": "commitment",
      "title": "Bring the chairs",
      "evidence_source_id": "m01",
      "evidence_quote": "I will bring the chairs on 2026-09-21 at 18:00 UTC.",
      "candidate": {
        "kind": "bring chairs",
        "owed_to_party_id": "fixture-peer@example.invalid",
        "due": "2026-09-21T18:00:00Z",
        "source_message_preview": "I will bring the chairs on 2026-09-21 at 18:00 UTC."
      },
      "confidence": 0.99,
      "reasons": [
        "Outbound first-person promise uses explicit commitment language",
        "The promised action has an explicit date and time"
      ],
      "action_owner": "member",
      "lifecycle_state": "open",
      "compact_summary": "Member committed to bring the chairs on September 21, 2026 at 18:00 UTC.",
      "case_summary": "In an outbound iMessage, the member explicitly said they will bring the chairs at the stated date and time.",
      "why_member": "The outbound first-person phrase “I will” assigns the action to the member.",
      "next_step": "Bring the chairs at the stated date and time.",
      "thread_state": "in_progress",
      "unresolved_questions": [],
      "related_source_ids": [],
      "obligation_assessment": {
        "purpose": "member_promise",
        "actionability": "required",
        "condition": null,
        "condition_state": "not_applicable",
        "beneficiary": "fixture-peer@example.invalid",
        "date_role": "deadline",
        "qualifying_evidence": [
          {
            "source_id": "m01",
            "quote": "I will bring the chairs",
            "role": "ownership"
          },
          {
            "source_id": "m01",
            "quote": "on 2026-09-21 at 18:00 UTC",
            "role": "date"
          }
        ]
      }
    }
  ]
}""")


def _recorded_transport(monkeypatch):
    calls = []

    def build(*, event_sink, event_log, raw_events_dir):
        class Client:
            async def post(self, url, *, json, headers):
                assert json["tool"] == "llm-task"
                assert json["args"]["schema"]["title"].startswith(
                    "classify_message_nature"
                )
                inputs = json["args"]["input"]
                assert (
                    inputs["body_text"]
                    == "I will bring the chairs on 2026-09-21 at 18:00 UTC."
                )
                assert inputs["from_party_known"] is False
                assert inputs["message_direction"] == "outbound"
                calls.append(copy.deepcopy(inputs))
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "details": {
                                "json": copy.deepcopy(RECORDED_OUTPUT),
                                "provider": "fixture",
                                "model": "recorded-g06-round3",
                            }
                        },
                    },
                    request=httpx.Request("POST", url, json=json),
                )

        return wrapper._Runtime(
            event_sink=event_sink,
            event_log=event_log,
            raw_events_dir=raw_events_dir,
            httpx_client_factory=Client,
            gateway_url="http://fixture.invalid/tools/invoke",
            gateway_token="fixture-token-never-used",
        )

    monkeypatch.setattr(application, "build_fixture_runtime", build)
    return calls


def _party(config, party_id, owner):
    return EventEnvelope(
        event_at_ms=1_789_000_000_000,
        tenant_id=config.instance.tenant_id,
        type="party.created",
        schema_version=1,
        occurred_at="2026-09-10T10:00:00Z",
        source_adapter="fixture-counterparty-source",
        source_account_id=owner,
        owner_scope=f"private:{owner}",
        visibility_scope=f"private:{owner}",
        sensitivity="normal",
        idempotency_key=f"fixture-counterparty:{party_id}",
        payload={
            "party_id": party_id,
            "kind": "person",
            "display_name": "Fixture Person",
            "sort_name": "Person, Fixture",
            "nickname": None,
            "pronouns": None,
            "notes": None,
            "attributes": {},
        },
    )


def _item(client, source_id):
    items = [
        item
        for item in _member_surface(client, "triage")["items"]
        if item.get("source_event_id") == source_id
    ]
    assert len(items) == 1
    return items[0]


def _enrich(client, item, value, request_id):
    response = client.post(
        f"/api/member/v1/triage/{item['proposal_id']}/enrich",
        headers=HEADERS,
        json={
            "request_id": request_id,
            "expected_last_event_id": item["last_event_id"],
            "candidate_updates": {"owed_to_party_id": value},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["triage"]


def _canonical_events(client):
    return [
        event
        for event in client.portal.call(_events, client.app.state.runtime)
        if event["type"]
        in {
            "triage.item.confirmed",
            "triage.item.materialized",
            "commitment.proposed",
            "commitment.confirmed",
        }
    ]


def _refuse_approval(client, item, request_id):
    before = client.portal.call(_events, client.app.state.runtime)
    response = client.post(
        f"/api/member/v1/triage/{item['proposal_id']}/approve",
        headers=HEADERS,
        json={
            "request_id": request_id,
            "expected_last_event_id": item["last_event_id"],
        },
    )
    assert response.status_code == 422, response.text
    assert "party" in response.json()["detail"].lower()
    assert client.portal.call(_events, client.app.state.runtime) == before
    assert _canonical_events(client) == []


def test_recorded_g06_requires_explicit_visible_party_without_losing_the_promise(
    tmp_path, monkeypatch
):
    calls = _recorded_transport(monkeypatch)
    config = application.RuntimeSettings.credential_free(
        tmp_path / "instance",
        # Preserve this isolated historical passive-extraction contract.
        legacy_personal_obligation_compatibility=True,
        encryption_key=b"p" * 32,
        repository_root=ROOT,
        member_identities={HEADERS["Tailscale-User-Login"]: MEMBER},
        member_roles={MEMBER: "principal"},
        fixture_member_id=MEMBER,
        adapter_config={"adapter_supervisor_port": 0, "adapters": []},
        deterministic_openclaw_fixture=True,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )
    case = next(case for case in load_frozen_cases() if case["case_id"] == "G06")
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        for party_id, owner in ((VISIBLE_PARTY, MEMBER), (HIDDEN_PARTY, OTHER)):
            party_event = client.portal.call(
                runtime.events.write, _party(config, party_id, owner)
            )
            client.portal.call(runtime.wait_for_projection, "parties", party_event)
        source_id = client.portal.call(
            runtime.events.write, _source_envelope(case, config.instance.tenant_id)
        )
        client.portal.call(_settle, runtime, {source_id}, source_id)
        item = _item(client, source_id)
        assert item["target_kind"] == "commitment"
        assert item["title"] == case["body_text"]
        assert item["candidate"]["due"] == "2026-09-21T18:00:00Z"
        assert "owed_to_party_id" not in item["candidate"]
        assert item["interpretation"]["action_owner"] == "member"
        assert item["interpretation"]["lifecycle_state"] == "open"
        assert (
            item["interpretation"]["obligation_assessment"]["independent_obligation"]
            is True
        )
        assert item["review_state"]["state"] == "needs_details"
        assert item["review_state"]["missing"] == ["owed_to_party"]
        assert item["admission_decision"] == "admit"
        events = client.portal.call(_events, runtime)
        receipts = [event for event in events if event["type"] == "skill.call.recorded"]
        assert len(receipts) == len(calls) == 1
        receipt_id = receipts[0]["event_id"]
        original_receipt = client.portal.call(runtime.event_log.get, receipt_id)
        assert original_receipt["payload"]["outputs"] == RECORDED_OUTPUT
        assert original_receipt["payload"]["provider"] == "fixture/recorded-g06-round3"
        assert (
            original_receipt["payload"]["outputs"]["candidates"][0]["candidate"][
                "owed_to_party_id"
            ]
            == "fixture-peer@example.invalid"
        )
        _refuse_approval(client, item, "fixture-g06-missing-party")
        for index, value in enumerate(
            (
                None,
                "fixture-peer@example.invalid",
                "fixture-nonexistent-party",
                HIDDEN_PARTY,
            )
        ):
            item = _enrich(
                client, item, value, f"fixture-g06-invalid-enrichment-{index}"
            )
            assert item["review_state"]["state"] == "needs_details"
            assert item["review_state"]["missing"] == ["owed_to_party"]
            assert _item(client, source_id)["review_state"] == item["review_state"]
            client.portal.call(_settle, runtime, {source_id})
            item = _item(client, source_id)
            _refuse_approval(client, item, f"fixture-g06-invalid-approval-{index}")
        # Persist an invalid reference as older releases could, then verify that
        # current reads and approval still fail closed after an unchanged restart.
        item = _enrich(
            client,
            item,
            "fixture-peer@example.invalid",
            "fixture-g06-retained-reference",
        )
        client.portal.call(_settle, runtime, {source_id})
        before_restart = client.portal.call(_events, runtime)

    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime, {source_id})
        assert len(calls) == 1
        assert client.portal.call(runtime.event_log.get, receipt_id) == original_receipt
        for event in before_restart:
            assert client.portal.call(runtime.event_log.get, event["event_id"]) == {
                key: value for key, value in event.items() if key != "lab_capture"
            }
        item = _item(client, source_id)
        assert item["candidate"]["owed_to_party_id"] == "fixture-peer@example.invalid"
        assert item["review_state"]["state"] == "needs_details"
        _refuse_approval(client, item, "fixture-g06-retained-approval")
        item = _enrich(
            client, item, VISIBLE_PARTY, "fixture-g06-explicit-member-choice"
        )
        assert item["review_state"]["state"] == "ready"
        assert item["review_state"]["missing"] == []
        assert _item(client, source_id)["review_state"] == item["review_state"]
        client.portal.call(_settle, runtime, {source_id})
        item = _item(client, source_id)
        body = {
            "request_id": "fixture-g06-confirm",
            "expected_last_event_id": item["last_event_id"],
        }
        response = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=HEADERS,
            json=body,
        )
        assert response.status_code == 200, response.text
        assert response.json()["destination"]["state"] == "materialized"
        client.portal.call(_settle, runtime, {source_id})
        canonical = _canonical_events(client)
        assert sorted(event["type"] for event in canonical) == sorted(
            [
                "triage.item.confirmed",
                "triage.item.materialized",
                "commitment.proposed",
                "commitment.confirmed",
            ]
        )
        proposal = next(
            event for event in canonical if event["type"] == "commitment.proposed"
        )
        assert proposal["payload"]["owed_by_member_id"] == MEMBER
        assert proposal["payload"]["owed_to_party_id"] == VISIBLE_PARTY
        assert proposal["payload"]["suggested_due"] == "2026-09-21T18:00:00Z"
        commitment = (
            runtime.projections.connection("commitments")
            .execute(
                "SELECT owed_to_party,description,status,confirmed_at,confirmed_by FROM commitments WHERE commitment_id=?",
                (proposal["payload"]["commitment_id"],),
            )
            .fetchone()
        )
        assert tuple(commitment)[:3] == (VISIBLE_PARTY, case["body_text"], "pending")
        assert commitment["confirmed_at"] is not None
        assert commitment["confirmed_by"] == MEMBER
        repeated = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=HEADERS,
            json=body,
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["duplicate"] is True
        assert _canonical_events(client) == canonical
        assert len(calls) == 1
        assert client.portal.call(runtime.event_log.get, receipt_id) == original_receipt
        assert runtime.adapters.inventory() == {}
        assert not [
            event
            for event in client.portal.call(_events, runtime)
            if event["type"].startswith("external_sync.write_")
            or event["type"] == "external.sent"
            or event["payload"].get("provider_called") is True
        ]
