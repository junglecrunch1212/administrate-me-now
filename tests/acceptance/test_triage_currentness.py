"""Currentness regressions through production composition, with synthetic mail.

ADR-0010 supplies exact cross-thread context authority; CO-030 keeps completion
and conditions action-specific. Only the external llm-task transport is fixed.
These tests do not establish actual-model accuracy or acquire provider data.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import adminme.runtime.application as application
from tests.fixtures.source_assessment_revision_worker import (
    AUTH,
    MEMBER,
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

    def output(self, inputs):
        return {
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

    async def respond(self, request, args):
        inputs = args["input"]
        output = self.output(inputs)
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


def _source(config, index, body, *, references=(), thread=None):
    envelope = source(config, index)
    return envelope.model_copy(
        update={
            "payload": {
                **envelope.payload,
                # Deliberately shared weak coordinates. Cross-thread context
                # still requires the explicit RFC edge below.
                "subject": "Synthetic workshop notices",
                "body_text": body,
                "thread_id": thread or envelope.payload["thread_id"],
                "rfc_message_id": f"<currentness-{index}@example.invalid>",
                "references": list(references),
                "in_reply_to": None,
            }
        }
    )


def _items(client, *, lens="all_candidates", auth=AUTH):
    query_lens = "all_candidates" if lens == "needs_you" else lens
    response = client.get(
        f"/api/member/v1/surfaces/triage?triage_lens={query_lens}&triage_limit=100",
        headers=auth,
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    return (
        [item for item in items if item["attention_view"] == "needs_you"]
        if lens == "needs_you"
        else items
    )


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
@pytest.mark.parametrize(
    "later_anchor", [False, True], ids=["original-anchor", "later-anchor"]
)
def test_exact_cross_thread_completion_retires_only_its_requested_action(
    tmp_path, monkeypatch, member_decided, later_anchor
):
    source_count = 4 if later_anchor else 3
    config = settings(
        tmp_path / "instance",
        ROOT,
        selected=[f"message-{i}" for i in range(source_count)],
    )
    original_body = f"{SCHEDULE} {PREPARE}"
    anchor_body = "The two workshop requests above are still open."
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
        if current["body_text"] == anchor_body:
            assert later_anchor and len(pack["messages"]) == 2
            [earlier] = [m for m in pack["messages"] if m["source_id"] != alias]
            assert earlier["relation"] == "thread"
            assert earlier["body_text"] == original_body
            assert earlier["occurred_at"] < current["occurred_at"]
            # These proposals belong to this newer assessment anchor B,
            # while their exact action evidence remains the original A.
            return [
                _candidate(earlier["source_id"], SCHEDULE),
                _candidate(earlier["source_id"], PREPARE),
            ]
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
        continued = _candidate(related["source_id"], PREPARE)
        continued["obligation_assessment"]["qualifying_evidence"] = [
            {
                "source_id": alias,
                "role": "ownership",
                "quote": "The separate tool inventory is still required.",
            }
        ]
        # Open related-origin interpretations remain in the language receipt;
        # they neither create new work here nor retire the existing inventory.
        return [completed, continued]

    model = _Model(candidates)
    monkeypatch.setattr(application, "build_fixture_runtime", runtime_factory(model))
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        original_id = _append(client, _source(config, 0, original_body))
        unrelated_id = _append(client, _source(config, 1, OTHER_SCHEDULE))
        anchor_id = original_id
        if later_anchor:
            anchor_id = _append(
                client,
                _source(config, 2, anchor_body, thread="fixture-revision-thread-0"),
            )
            assert anchor_id != original_id
        before_items = _items(client, lens="needs_you")
        assert len(before_items) == 3, before_items
        by_title = {item["title"]: item for item in before_items}
        assert set(by_title) == {SCHEDULE, PREPARE, OTHER_SCHEDULE}
        for quote in (SCHEDULE, PREPARE):
            assert by_title[quote]["source_event_id"] == anchor_id
            assert by_title[quote]["interpretation"]["evidence_refs"] == [
                {"source_event_id": original_id, "quote": quote}
            ]
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
            source_count - 1,
            completion_body,
            references=["<currentness-0@example.invalid>"],
        )
        completion_id = _append(client, completion)
        assert len(model.calls) == source_count
        assert model.calls[-1]["output"]["candidates"][0]["lifecycle_state"] == (
            "completed"
        )
        after = client.portal.call(events, runtime)
        recorded = [event for event in after if event["type"] == "skill.call.recorded"]
        assert len(recorded) == source_count
        assert recorded[-1]["payload"]["outputs"] == model.calls[-1]["output"]
        assert not [
            event
            for event in after
            if event["type"] in {"skill.call.failed", "skill.call.suppressed"}
        ]
        assert (
            len([event for event in after if event["type"] in SOURCE_TYPES])
            == source_count
        )
        for event in before:
            assert client.portal.call(runtime.event_log.get, event["event_id"]) == event
        active = _items(client, lens="needs_you")
        assert len(active) == 2, active
        active_by_title = {item["title"]: item for item in active}
        assert SCHEDULE not in active_by_title, (
            "The exact RFC-linked, later completion was recorded but the old scheduling ask remained active",
            active,
        )
        assert set(active_by_title) == {PREPARE, OTHER_SCHEDULE}
        assert (
            active_by_title[PREPARE]["proposal_id"] == by_title[PREPARE]["proposal_id"]
        )
        assert active_by_title[PREPARE]["source_event_id"] == anchor_id
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


class _RecordedCurrentnessModel(_Model):
    """Replay recorded synthetic outputs; never claim a fresh provider call."""

    def __init__(self, records, *, original_action_grounding):
        super().__init__(None)
        self.records = records
        self.original_action_grounding = original_action_grounding

    def output(self, inputs):
        assert len(self.calls) < len(self.records), "Unexpected extra classifier call"
        record = self.records[len(self.calls)]
        pack = inputs["context_pack"]
        assert [
            {key: message[key] for key in ("source_id", "relation", "body_text")}
            for message in pack["messages"]
        ] == record["context"]
        output = copy.deepcopy(record["outputs"])
        if record["case_id"] == "U04" and self.original_action_grounding:
            [inventory] = [
                candidate
                for candidate in output["candidates"]
                if candidate["evidence_quote"]
                == "The separate tool inventory is still required."
            ]
            [related] = [
                message
                for message in pack["messages"]
                if message["relation"] == "related"
            ]
            assert PREPARE in related["body_text"]
            inventory["evidence_source_id"] = related["source_id"]
            inventory["evidence_quote"] = PREPARE
        return output


@pytest.mark.parametrize(
    "original_action_grounding",
    [False, True],
    ids=["recorded-restatement-duplicates", "original-action-grounding-retains"],
)
def test_recorded_related_continuation_preserves_exact_original_action(
    tmp_path, monkeypatch, original_action_grounding
):
    fixture_root = ROOT / "tests/fixtures/triage_currentness_model_lab"
    observed = json.loads(
        (fixture_root.parent / "triage_currentness_observed_round1.json").read_text()
    )
    frozen = json.loads((fixture_root / "sources-frozen.json").read_text())
    sources = frozen["sources"][:4]
    assert (
        [row["case_id"] for row in observed["cases"]]
        == [row["case_id"] for row in sources]
        == ["U01", "U02", "U03", "U04"]
    )
    config = settings(
        tmp_path / "instance",
        ROOT,
        selected=[row["message_id"] for row in sources],
    )
    model = _RecordedCurrentnessModel(
        observed["cases"], original_action_grounding=original_action_grounding
    )
    monkeypatch.setattr(application, "build_fixture_runtime", runtime_factory(model))
    envelopes = []
    for index, row in enumerate(sources):
        envelope = source(config, index)
        envelopes.append(
            envelope.model_copy(
                update={
                    "occurred_at": row["occurred_at"],
                    "payload": {
                        **envelope.payload,
                        **{
                            key: row[key]
                            for key in (
                                "subject",
                                "body_text",
                                "thread_id",
                                "rfc_message_id",
                                "references",
                                "in_reply_to",
                            )
                        },
                        "native_message_id": row["message_id"],
                        "received_at": row["occurred_at"],
                        "provider_labels": ["INBOX"],
                    },
                }
            )
        )
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        initial_ids = [_append(client, envelope) for envelope in envelopes[:3]]
        before = [item for item in _items(client) if item["state"] == "pending_review"]
        assert len(before) == 3, before
        by_title = {item["title"]: item for item in before}
        assert set(by_title) == {SCHEDULE, PREPARE, OTHER_SCHEDULE}
        for quote in (SCHEDULE, PREPARE):
            assert by_title[quote]["source_event_id"] == initial_ids[2]
            assert by_title[quote]["interpretation"]["evidence_refs"] == [
                {"source_event_id": initial_ids[0], "quote": quote}
            ]
        assert by_title[OTHER_SCHEDULE]["source_event_id"] == initial_ids[1]
        before_events = client.portal.call(events, runtime)
        completion_id = _append(client, envelopes[-1])
        after = [item for item in _items(client) if item["state"] == "pending_review"]
        tasks = [item for item in after if item["target_kind"] == "task"]
        # This branch intentionally characterizes the observed failure. Exact
        # scope/quote guards cannot infer action identity from a paraphrase.
        assert len(tasks) == (2 if original_action_grounding else 3), tasks
        assert len(after) == len(tasks) + 1, after
        for quote in (PREPARE, OTHER_SCHEDULE):
            [retained] = [item for item in tasks if item["title"] == quote]
            assert retained["proposal_id"] == by_title[quote]["proposal_id"]
            assert retained["last_event_id"] == by_title[quote]["last_event_id"]
            assert retained["interpretation"] == by_title[quote]["interpretation"]
            assert retained["admission_decision"] == "admit"
        assert all(item["title"] != SCHEDULE for item in tasks)
        continued = [item for item in tasks if item["source_event_id"] == completion_id]
        assert len(continued) == (0 if original_action_grounding else 1), continued
        if continued:
            assert continued[0]["interpretation"]["evidence_refs"] == [
                {
                    "source_event_id": completion_id,
                    "quote": "The separate tool inventory is still required.",
                }
            ]
            assert continued[0]["duplicate_of_proposal_id"] is None
        [calendar] = [item for item in after if item["target_kind"] == "calendar_event"]
        assert calendar["source_event_id"] == completion_id
        assert calendar["review_state"]["state"] == "needs_details"
        assert all(item["owner_member_id"] == MEMBER for item in after)
        assert all(item["tenant_id"] == config.instance.tenant_id for item in after)
        all_events = client.portal.call(events, runtime)
        new_events = all_events[len(before_events) :]
        supersessions = [
            event for event in new_events if event["type"] == "triage.item.superseded"
        ]
        [anchor_retirement] = [
            event
            for event in supersessions
            if event["payload"]["proposal_id"] == by_title[SCHEDULE]["proposal_id"]
        ]
        assert (
            anchor_retirement["payload"]["replacement_source_event_id"] == completion_id
        )
        assert not any(
            event["payload"]["proposal_id"]
            in {
                by_title[PREPARE]["proposal_id"],
                by_title[OTHER_SCHEDULE]["proposal_id"],
            }
            for event in supersessions
        )
        assert all_events[: len(before_events)] == before_events
        assert (
            len([event for event in all_events if event["type"] in SOURCE_TYPES]) == 4
        )
        receipts = [
            event for event in all_events if event["type"] == "skill.call.recorded"
        ]
        assert len(receipts) == len(model.calls) == 4
        assert [event["payload"]["outputs"] for event in receipts] == [
            call["output"] for call in model.calls
        ]
        assert all(
            event["payload"]["provider"] == "fixture/synthetic-currentness-regression"
            for event in receipts
        )
        expected = copy.deepcopy([record["outputs"] for record in observed["cases"]])
        if original_action_grounding:
            # Only these two grounding fields differ from the observed output;
            # summaries, qualifiers, lifecycle and independent calendar stay.
            expected[-1]["candidates"][2]["evidence_source_id"] = "m02"
            expected[-1]["candidates"][2]["evidence_quote"] = PREPARE
        assert [call["output"] for call in model.calls] == expected
        assert not [
            event
            for event in all_events
            if event["type"]
            in {
                "skill.call.failed",
                "skill.call.suppressed",
                "triage.item.confirmed",
                "triage.item.materialized",
                "commitment.created",
                "commitment.fulfilled",
            }
            or event["type"].startswith(("task.", "calendar.", "reminder."))
        ]
        _assert_private_no_effects(client)
        _assert_redelivery_unchanged(client, envelopes[-1], completion_id, model)


@pytest.mark.parametrize(
    "balance_known, declare_question_scope",
    [(False, False), (False, True), (True, True)],
    ids=[
        "legacy-unknown-obligation",
        "declared-unknown-obligation",
        "method-only-unknown",
    ],
)
def test_payment_uncertainty_cannot_borrow_independent_statement_review(
    tmp_path, monkeypatch, balance_known, declare_question_scope
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
        if declare_question_scope:
            # The sources and expected outcomes are frozen. The new schema
            # response states question scope explicitly; the legacy unknown
            # response above remains unchanged as a regression.
            payment["obligation_assessment"]["blocking_question_indices"] = (
                [] if balance_known else [0, 1]
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
            assert (
                payment["human_lane"]
                == payment["disposition"]["lane"]
                == "scheduled_deadline"
            )
        else:
            assert assessment["actionability"] in {"unknown", "conditional"}, (
                "A date label and unresolved payment requirement cannot establish required payment",
                payment,
            )
            assert assessment["independent_obligation"] is False
            assert payment["admission_decision"] == "unresolved"
            assert (
                payment["human_lane"]
                == payment["disposition"]["lane"]
                == "clarification_conflict"
            )
            assert payment["attention_view"] == "needs_you"
        _assert_private_no_effects(client)
        _assert_redelivery_unchanged(client, envelope, source_id, model)
