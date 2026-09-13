"""Currentness regressions through production composition, with synthetic mail.

ADR-0010 supplies exact cross-thread context authority; CO-030 keeps completion
and conditions action-specific. Only the external llm-task transport is fixed.
These tests do not establish actual-model accuracy or acquire provider data.
"""

from __future__ import annotations

import copy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import adminme.runtime.application as application
from tests.fixtures.source_assessment_revision_worker import (
    AUTH,
    OTHER_AUTH,
    SOURCE_TYPES,
    events,
    runtime_factory,
    settings,
    settle,
    source,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = "Please schedule the annual inspection for the blue workshop."
PREPARE = "Please prepare the separate tool inventory."
OTHER_SCHEDULE = "Please schedule the inspection for the green workshop."
COMPLETION = (
    "Scheduling the annual inspection for the blue workshop is complete; "
    "your booking is confirmed."
)
REVIEW = "Review the attached statement for errors."
PAYMENT_DATE = "Payment due date: October 18, 2034."
PAYMENT_LINK = "Open the payment page."
EXPLICIT_PAYMENT = "Please pay the outstanding $42 by October 18, 2034."


def _candidate(alias, quote, *, purpose="preparation", **updates):
    result = {
        "target_kind": "task",
        "title": quote,
        "evidence_source_id": alias,
        "evidence_quote": quote,
        "candidate": {},
        "confidence": 0.9,
        "reasons": ["synthetic_currentness_interpretation"],
        "action_owner": "member",
        "lifecycle_state": "open",
        "compact_summary": quote,
        "case_summary": None,
        "why_member": None,
        "next_step": None,
        "thread_state": "waiting_on_member",
        "unresolved_questions": [],
        "related_source_ids": [],
        "obligation_assessment": {
            "purpose": purpose,
            "actionability": "required",
            "condition": None,
            "condition_state": "not_applicable",
            "beneficiary": None,
            "date_role": "none",
            "qualifying_evidence": [],
        },
    }
    result.update(updates)
    return result


class _Model:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    async def respond(self, request, args):
        inputs = args["input"]
        output = {
            "assessment_contract": "personal-obligation-assessment-v1",
            "classification": "transactional",
            "confidence": 0.9,
            "reasons": ["synthetic_currentness_interpretation"],
            "reply_signal": "not_required",
            "automated": True,
            "transactional_due": False,
            "security_code": False,
            "person_to_person": False,
            "deadline": None,
            "candidates": self.candidates(inputs),
        }
        self.calls.append({"inputs": copy.deepcopy(inputs), "output": output})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "details": {
                        "json": output,
                        "provider": "fixture",
                        "model": "synthetic-currentness-regression",
                    }
                },
            },
            request=request,
        )


def _source(config, index, body, *, references=()):
    envelope = source(config, index)
    return envelope.model_copy(
        update={
            "payload": {
                **envelope.payload,
                # Deliberately shared weak coordinates; each native thread is
                # distinct. Only the explicit RFC edge may admit related text.
                "subject": "Synthetic workshop notices",
                "body_text": body,
                "rfc_message_id": f"<currentness-{index}@example.invalid>",
                "references": list(references),
                "in_reply_to": None,
            }
        }
    )


def _items(client, *, lens="all_candidates", auth=AUTH):
    response = client.get(
        f"/api/member/v1/surfaces/triage?triage_lens={lens}&triage_limit=100",
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _append(client, envelope):
    runtime = client.app.state.runtime
    event_id = client.portal.call(runtime.events.write, envelope)
    client.portal.call(settle, runtime)
    return event_id


def _assert_private_no_effects(client):
    runtime = client.app.state.runtime
    assert not runtime.adapters.inventory()
    assert _items(client, auth=OTHER_AUTH) == []
    assert not [
        event
        for event in client.portal.call(events, runtime)
        if event["type"].startswith("external_sync.write_")
        or event["type"] == "external.sent"
        or event["payload"].get("provider_called") is True
    ]


def _assert_redelivery_unchanged(client, envelope, event_id, model):
    runtime = client.app.state.runtime
    before = client.portal.call(events, runtime)
    calls = copy.deepcopy(model.calls)
    items = _items(client)
    assert _append(client, envelope) == event_id
    assert client.portal.call(events, runtime) == before
    assert model.calls == calls
    assert _items(client) == items


@pytest.mark.parametrize(
    "member_decided", [False, True], ids=["untouched", "member-decided"]
)
def test_exact_cross_thread_completion_retires_only_its_requested_action(
    tmp_path, monkeypatch, member_decided
):
    config = settings(
        tmp_path / "instance", ROOT, selected=[f"message-{i}" for i in range(3)]
    )
    original_body = f"{SCHEDULE} {PREPARE}"
    completion_body = f"{COMPLETION} The separate tool inventory is still required."

    def candidates(inputs):
        pack = inputs["context_pack"]
        alias = pack["current_source_id"]
        current = next(m for m in pack["messages"] if m["source_id"] == alias)
        if current["body_text"] == original_body:
            assert len(pack["messages"]) == 1
            return [_candidate(alias, SCHEDULE), _candidate(alias, PREPARE)]
        if current["body_text"] == OTHER_SCHEDULE:
            # Same sender and subject do not join the two scheduling requests.
            assert len(pack["messages"]) == 1
            return [_candidate(alias, OTHER_SCHEDULE)]
        assert current["body_text"] == completion_body
        [related] = [m for m in pack["messages"] if m["relation"] == "related"]
        assert len(pack["messages"]) == 2
        assert related["body_text"] == original_body
        assert "rfc_reference" in related["relation_reason_codes"]
        assert related["occurred_at"] < current["occurred_at"]
        completed = _candidate(
            related["source_id"], SCHEDULE, lifecycle_state="completed"
        )
        completed["obligation_assessment"].update(
            actionability="none",
            qualifying_evidence=[
                {"source_id": alias, "role": "completion", "quote": COMPLETION}
            ],
        )
        return [completed]

    model = _Model(candidates)
    monkeypatch.setattr(application, "build_fixture_runtime", runtime_factory(model))
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        original_id = _append(client, _source(config, 0, original_body))
        unrelated_id = _append(client, _source(config, 1, OTHER_SCHEDULE))
        by_title = {item["title"]: item for item in _items(client, lens="needs_you")}
        assert set(by_title) == {SCHEDULE, PREPARE, OTHER_SCHEDULE}
        decision_id = None
        if member_decided:
            item = by_title[SCHEDULE]
            response = client.post(
                f"/api/member/v1/triage/{item['proposal_id']}/dismiss",
                headers=AUTH,
                json={
                    "request_id": "synthetic-currentness-member-decision",
                    "expected_last_event_id": item["last_event_id"],
                    "reason": "Handled independently",
                },
            )
            assert response.status_code == 200, response.text
            decision_id = response.json()["event_id"]
            client.portal.call(settle, runtime)
        before = client.portal.call(events, runtime)
        completion = _source(
            config,
            2,
            completion_body,
            references=["<currentness-0@example.invalid>"],
        )
        completion_id = _append(client, completion)
        assert len(model.calls) == 3
        assert model.calls[-1]["output"]["candidates"][0]["lifecycle_state"] == (
            "completed"
        )
        after = client.portal.call(events, runtime)
        recorded = [event for event in after if event["type"] == "skill.call.recorded"]
        assert len(recorded) == 3
        assert recorded[-1]["payload"]["outputs"] == model.calls[-1]["output"]
        assert not [
            event
            for event in after
            if event["type"] in {"skill.call.failed", "skill.call.suppressed"}
        ]
        assert len([event for event in after if event["type"] in SOURCE_TYPES]) == 3
        for event in before:
            assert client.portal.call(runtime.event_log.get, event["event_id"]) == event
        active = _items(client, lens="needs_you")
        active_by_title = {item["title"]: item for item in active}
        assert SCHEDULE not in active_by_title, (
            "The exact RFC-linked, later completion was recorded but the old scheduling ask remained active",
            active,
        )
        assert set(active_by_title) == {PREPARE, OTHER_SCHEDULE}
        assert (
            active_by_title[PREPARE]["proposal_id"] == by_title[PREPARE]["proposal_id"]
        )
        assert active_by_title[PREPARE]["source_event_id"] == original_id
        assert (
            active_by_title[OTHER_SCHEDULE]["proposal_id"]
            == by_title[OTHER_SCHEDULE]["proposal_id"]
        )
        assert active_by_title[OTHER_SCHEDULE]["source_event_id"] == unrelated_id
        if member_decided:
            row = (
                runtime.projections.connection("triage")
                .execute(
                    "SELECT state,last_event_id FROM triage_items WHERE proposal_id=?",
                    (by_title[SCHEDULE]["proposal_id"],),
                )
                .fetchone()
            )
            assert tuple(row) == ("dismissed", decision_id)
            assert not [
                event
                for event in after
                if event["type"] == "triage.item.superseded"
                and event["payload"]["proposal_id"] == by_title[SCHEDULE]["proposal_id"]
            ]
        _assert_private_no_effects(client)
        _assert_redelivery_unchanged(client, completion, completion_id, model)


@pytest.mark.parametrize(
    "balance_known", [False, True], ids=["unknown-obligation", "method-only-unknown"]
)
def test_payment_uncertainty_cannot_borrow_independent_statement_review(
    tmp_path, monkeypatch, balance_known
):
    config = settings(tmp_path / "instance", ROOT, selected=["message-0"])
    payment_quote = EXPLICIT_PAYMENT if balance_known else PAYMENT_DATE
    body = f"{REVIEW} {payment_quote} {PAYMENT_LINK}"

    def candidates(inputs):
        pack = inputs["context_pack"]
        assert len(pack["messages"]) == 1
        alias = pack["current_source_id"]
        assert pack["messages"][0]["body_text"] == body
        review = _candidate(alias, REVIEW, purpose="preparation")
        payment = _candidate(
            alias,
            payment_quote,
            purpose="payment",
            candidate={"due": "2034-10-18T00:00:00Z"},
            unresolved_questions=["Which payment method should I use?"]
            if balance_known
            else [
                "What amount remains payable?",
                "Has payment already been made or scheduled?",
            ],
            next_step="Choose a payment method."
            if balance_known
            else "Inspect the statement to establish whether anything remains payable.",
        )
        payment["obligation_assessment"].update(
            date_role="deadline",
            # The unknown-obligation fixture treats a date label as the
            # action and a generic navigation link as ownership. Its own
            # unresolved questions leave the obligation unestablished.
            qualifying_evidence=[
                {
                    "source_id": alias,
                    "role": "date",
                    "quote": "October 18, 2034" if balance_known else PAYMENT_DATE,
                },
                {
                    "source_id": alias,
                    "role": "ownership",
                    "quote": EXPLICIT_PAYMENT if balance_known else PAYMENT_LINK,
                },
            ],
        )
        return [review, payment]

    model = _Model(candidates)
    monkeypatch.setattr(application, "build_fixture_runtime", runtime_factory(model))
    with TestClient(application.create_application(config)) as client:
        envelope = _source(config, 0, body)
        source_id = _append(client, envelope)
        assert len(model.calls) == 1
        raw_payment = model.calls[0]["output"]["candidates"][1]
        assert raw_payment["obligation_assessment"]["actionability"] == "required"
        assert (
            raw_payment["obligation_assessment"]["condition_state"] == "not_applicable"
        )
        assert raw_payment["unresolved_questions"]
        by_title = {item["title"]: item for item in _items(client)}
        assert set(by_title) == {REVIEW, payment_quote}
        review = by_title[REVIEW]
        assert (
            review["interpretation"]["obligation_assessment"]["independent_obligation"]
            is True
        )
        assert review["admission_decision"] == "admit"
        assert review["attention_view"] == "needs_you"
        assert review["review_state"]["state"] == "ready"
        payment = by_title[payment_quote]
        assessment = payment["interpretation"]["obligation_assessment"]
        assert assessment["evidence_verified"] is True
        assert all(
            item["source_event_id"] == source_id and item["quote"] in body
            for item in assessment["evidence"]
        )
        if balance_known:
            assert assessment["actionability"] == "required"
            assert assessment["condition_state"] == "not_applicable"
            assert assessment["independent_obligation"] is True
            assert payment["admission_decision"] == "admit"
            assert payment["candidate"]["due"] == "2034-10-18T00:00:00Z"
            assert assessment["verified_date_fields"] == ["due"]
            assert payment["review_state"]["state"] == "ready"
        else:
            assert assessment["actionability"] in {"unknown", "conditional"}, (
                "A date label and unresolved payment requirement cannot establish required payment",
                payment,
            )
            assert assessment["independent_obligation"] is False
            assert payment["admission_decision"] == "unresolved"
            assert payment["disposition"]["lane"] == "clarification_conflict"
            assert payment["attention_view"] == "needs_you"
        _assert_private_no_effects(client)
        _assert_redelivery_unchanged(client, envelope, source_id, model)
