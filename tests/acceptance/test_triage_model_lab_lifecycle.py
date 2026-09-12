"""Production lifecycle proof for the lab's exact-duplicate task selection.

Only the external llm-task transport is fixed. This tests the capture harness,
grounding, reconciliation and actual member approval/completion, not model accuracy.
"""

from __future__ import annotations

import copy
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import adminme.lib.skill_runner.wrapper as wrapper
import adminme.runtime.application as application
from tools.triage_model_lab import (
    HEADERS,
    MEMBER,
    _complete_g12a,
    _events,
    _g12a_items,
    _immutable_counts,
    _member_surface,
    _settle,
    _source_envelope,
    _task_rows,
    _validate_g12a_link,
    load_frozen_cases,
)


ROOT = Path(__file__).resolve().parents[2]


def _model(monkeypatch, cases):
    calls = []

    def build(*, event_sink, event_log, raw_events_dir):
        class Client:
            async def post(self, url, *, json, headers):
                assert json["tool"] == "llm-task"
                assert json["args"]["schema"]["title"].startswith(
                    "classify_message_nature"
                )
                pack = json["args"]["input"]["context_pack"]
                alias = pack["current_source_id"]
                current = next(m for m in pack["messages"] if m["source_id"] == alias)
                case_id = next(
                    key
                    for key, case in cases.items()
                    if case["body_text"] == current["body_text"]
                )
                calls.append(case_id)
                actions = []
                if case_id in {"G10", "G12a"}:
                    actions.append((cases["G12a"]["body_text"], "2026-09-17", "17:00"))
                if case_id == "G10":
                    actions.append(
                        (
                            cases["G10"]["body_text"].removeprefix(
                                cases["G12a"]["body_text"] + " "
                            ),
                            "2026-09-19",
                            "12:00",
                        )
                    )
                candidates = [
                    {
                        "target_kind": "task",
                        "title": quote,
                        "evidence_source_id": alias,
                        "evidence_quote": quote,
                        "candidate": {"due": f"{day}T{clock}:00Z"},
                        "confidence": 0.9,
                        "reasons": ["fixed_lifecycle_transport_fixture"],
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
                            "purpose": "preparation",
                            "actionability": "required",
                            "condition": None,
                            "condition_state": "not_applicable",
                            "beneficiary": None,
                            "date_role": "deadline",
                            "qualifying_evidence": [
                                {
                                    "source_id": alias,
                                    "quote": f"{day} at {clock} UTC",
                                    "role": "date",
                                }
                            ],
                        },
                    }
                    for quote, day, clock in actions
                ]
                output = {
                    "assessment_contract": "personal-obligation-assessment-v1",
                    "classification": "transactional",
                    "confidence": 0.9,
                    "reasons": ["fixed_lifecycle_transport_fixture"],
                    "reply_signal": "not_required",
                    "automated": False,
                    "transactional_due": bool(candidates),
                    "security_code": False,
                    "person_to_person": False,
                    "deadline": None,
                    "candidates": candidates,
                }
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "details": {
                                "json": output,
                                "provider": "fixture",
                                "model": "lifecycle",
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


def _refuse_mutated_links(selected, primary, events, tenant_id):
    """Each refusal starts from real production receipts and member fields."""
    mutations = [
        ("selected", ("duplicate_of_proposal_id",), "missing-primary"),
        ("selected", ("last_event_id",), "different-latest-event"),
        ("primary", ("owner_member_id",), "fixture-other-member"),
        ("primary", ("tenant_id",), "fixture-other-tenant"),
        ("primary", ("state",), "confirmed"),
        ("primary", ("candidate", "due"), "2026-09-19T12:00:00Z"),
        ("source", ("source_account_id",), "fixture-other-account"),
        ("source", ("payload", "provider_account_id"), "fixture-other-account"),
        ("source", ("payload", "body_text"), "The action is absent."),
        ("assessment", ("causation_id",), "different-source"),
        ("assessment", ("payload", "extraction_state"), "failed"),
        ("proposal", ("payload", "extraction_event_id"), "different-assessment"),
        ("reconciliation", ("payload", "primary_proposal_id"), "different-primary"),
        ("reconciliation", ("payload", "admission_active"), False),
        ("reconciliation", ("payload", "relationships"), []),
    ]
    relationships = events[selected["reconciliation"]["event_id"]]["payload"][
        "relationships"
    ]
    for field, value in (
        ("relationship_type", "same_conversation"),
        ("automatic_effect", False),
        ("authority", "review_suggestion"),
    ):
        changed = copy.deepcopy(relationships)
        for relationship in changed:
            relationship[field] = value
        mutations.append(("reconciliation", ("payload", "relationships"), changed))
    for target, path, value in mutations:
        selected_copy, primary_copy, evidence = copy.deepcopy(
            (selected, primary, events)
        )
        targets = {
            "selected": selected_copy,
            "primary": primary_copy,
            "source": evidence[primary["source_event_id"]],
            "proposal": evidence[primary["proposal_event_id"]],
            "assessment": evidence[primary["extraction_event_id"]],
            "reconciliation": evidence[selected["reconciliation"]["event_id"]],
        }
        item = targets[target]
        for key in path[:-1]:
            item = item[key]
        item[path[-1]] = value
        with pytest.raises(RuntimeError, match="G12a"):
            _validate_g12a_link(selected_copy, primary_copy, evidence, tenant_id)
    for event_id in (
        primary["source_event_id"],
        primary["proposal_event_id"],
        primary["extraction_event_id"],
        selected["reconciliation"]["event_id"],
    ):
        evidence = {key: value for key, value in events.items() if key != event_id}
        with pytest.raises(RuntimeError, match="G12a"):
            _validate_g12a_link(selected, primary, evidence, tenant_id)
    # Even internally consistent typed due changes cannot follow an exact pointer
    # whose selected and primary actions disagree.
    for field, value in (
        ("due", "2026-09-19T12:00:00Z"),
        ("due", "2026-09-17T17:00:00"),
    ):
        selected_copy, primary_copy, evidence = copy.deepcopy(
            (selected, primary, events)
        )
        primary_copy["candidate"][field] = value
        evidence[primary["proposal_event_id"]]["payload"]["candidate"][field] = value
        with pytest.raises(RuntimeError, match="G12a"):
            _validate_g12a_link(selected_copy, primary_copy, evidence, tenant_id)


@pytest.mark.parametrize("prior_g10", [False, True])
def test_lab_completes_actual_g12_task_through_exact_duplicate_and_retains_singular_work(
    tmp_path, monkeypatch, prior_g10
):
    cases = {
        case["case_id"]: case
        for case in load_frozen_cases()
        if case["case_id"] in {"G10", "G12a", "G12b"}
    }
    calls = _model(monkeypatch, cases)
    config = application.RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=b"g" * 32,
        repository_root=ROOT,
        member_identities={HEADERS["Tailscale-User-Login"]: MEMBER},
        member_roles={MEMBER: "principal"},
        adapter_config={"adapter_supervisor_port": 0, "adapters": []},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        ids = set()
        source_by_case = {}
        for case_id in (["G10"] if prior_g10 else []) + ["G12a"]:
            envelope = _source_envelope(cases[case_id], config.instance.tenant_id)
            source_id = client.portal.call(runtime.events.write, envelope)
            source_by_case[case_id] = source_id
            ids.add(source_id)
            client.portal.call(_settle, runtime, ids, source_id)
        surface = _member_surface(client, "triage")
        selected, primary = _g12a_items(surface, source_by_case["G12a"])
        before = client.portal.call(_events, runtime)
        evidence = {event["event_id"]: event for event in before}
        link = _validate_g12a_link(
            selected, primary, evidence, config.instance.tenant_id
        )
        assert link["followed_exact_duplicate"] is prior_g10
        assert link["selected_source_event_id"] == source_by_case["G12a"]
        assert (
            link["effective_source_event_id"]
            == source_by_case["G10" if prior_g10 else "G12a"]
        )
        assert len(
            [event for event in before if event["type"] == "triage.item.proposed"]
        ) == (3 if prior_g10 else 1)
        if prior_g10:
            assert selected["state"] == "dismissed"
            assert selected["admission_decision"] == "duplicate"
            _refuse_mutated_links(
                selected, primary, evidence, config.instance.tenant_id
            )
        calls_before = list(calls)
        result = _complete_g12a(client, source_by_case["G12a"])
        assert result["source_proposal_link"] == link
        assert result["task_id"] == f"triage_task_{primary['proposal_id']}"
        assert 3 <= len(result["selection_evidence"]) <= 7
        client.portal.call(_settle, runtime, ids)
        completed = _task_rows(runtime)
        assert len(completed) == 1
        assert completed[0]["task_id"] == result["task_id"]
        assert completed[0]["status"] == "done"
        assert completed[0]["due_date"] == "2026-09-17T17:00:00Z"
        assert calls == calls_before
        after = client.portal.call(_events, runtime)
        for event in before:
            assert client.portal.call(runtime.event_log.get, event["event_id"]) == {
                key: value for key, value in event.items() if key != "lab_capture"
            }
        assert len([e for e in after if e["type"] == "task.created"]) == 1
        assert len([e for e in after if e["type"] == "task.completed"]) == 1
        followup = client.portal.call(
            runtime.events.write,
            _source_envelope(cases["G12b"], config.instance.tenant_id),
        )
        ids.add(followup)
        client.portal.call(_settle, runtime, ids, followup)
        assert _task_rows(runtime) == completed
        counts = _immutable_counts(client.portal.call(_events, runtime))
        calls_before = list(calls)
        assert (
            client.portal.call(runtime.events.write, envelope) == source_by_case["G12a"]
        )
        client.portal.call(_settle, runtime, ids)
        assert _immutable_counts(client.portal.call(_events, runtime)) == counts
        assert _task_rows(runtime) == completed
        assert (
            calls == calls_before == (["G10"] if prior_g10 else []) + ["G12a", "G12b"]
        )
        assert runtime.adapters.inventory() == {}
        assert not [
            event
            for event in client.portal.call(_events, runtime)
            if event["type"].startswith("external_sync.write_")
            or event["type"] == "external.sent"
            or event["payload"].get("provider_called") is True
        ]
