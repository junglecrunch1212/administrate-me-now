"""Finite currentness authority through real encrypted production composition.

Only the external llm-task transport returns synthetic fixtures. These tests
prove durability and scope, not actual-model semantic accuracy.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import adminme.runtime.application as application
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.source_assessment_revision import RevisionRefused
from adminme import source_assessment_revision as closed
from adminme import source_currentness_revision as currentness
from adminme.source_assurance import audit_source_assurance
from tests.acceptance import test_source_assessment_revision as legacy
from tests.acceptance import test_triage_currentness as behavior
from tests.fixtures.source_assessment_revision_worker import (
    AUTH,
    OTHER_AUTH,
    SOURCE_TYPES,
    MODEL_TYPES,
    events,
    settings,
    settle,
    snapshot,
    source,
    runtime_factory,
)


ROOT = Path(__file__).resolve().parents[2]
PREDECESSOR = "e12156545c02bd6ed0673ce7b5c1dc3f69313903"
API = "/api/system/source-currentness-revisions"


def _historical_output(candidates):
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
        "candidates": candidates,
    }


def _predecessor_case(
    tmp_path,
    monkeypatch,
    config,
    envelopes,
    outputs,
    *,
    predecessor=None,
    recovery_target=None,
):
    if predecessor is None:
        monkeypatch.setattr(legacy, "PREDECESSOR", PREDECESSOR)
        predecessor = legacy._predecessor(tmp_path)
    root = predecessor
    fixture = tmp_path / "synthetic-predecessor-input.json"
    result = tmp_path / "synthetic-predecessor-output.json"
    fixture.write_text(
        json.dumps(
            {
                "selected": config.adapter_config["disposable_test_acquisition"][
                    "message_ids"
                ],
                "sources": [envelope.model_dump(mode="json") for envelope in envelopes],
                "outputs": outputs,
                "recovery_target": recovery_target,
            }
        )
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ADMINME_", "OPENCLAW_")) and key != "PYTHONPATH"
    }
    environment["PYTHONPATH"] = os.pathsep.join((str(root), str(root / "bridge")))
    environment["PYTHONNOUSERSITE"] = "1"
    worker = (ROOT / "tests/fixtures/source_currentness_revision_worker.py").read_text()
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            str(root),
            str(config.instance.instance_dir),
            str(fixture),
            str(result),
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(result.read_text())


def _history(client):
    return client.portal.call(events, client.app.state.runtime)


def _stable(history):
    return [event for event in history if event["type"] != "runtime.started"]


def _preview(client, ids):
    response = client.post(
        API + "/preview", headers=AUTH, json={"source_event_ids": ids}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _request(client, manifest):
    response = client.post(API, headers=AUTH, json=manifest)
    assert response.status_code == 202, response.text
    return response.json()


def _status(client, request_id):
    response = client.get(API + "/" + request_id, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def _assurance(client):
    runtime = client.app.state.runtime

    async def read():
        return await audit_source_assurance(
            runtime.event_log,
            repository_root=ROOT,
            capabilities=runtime._source_assurance_capability_snapshot(),
        )

    return client.portal.call(read)


def test_currentness_retires_missed_exact_completion_without_drafts_after_interruption(
    tmp_path, monkeypatch
):
    config = settings(
        tmp_path / "instance", ROOT, selected=[f"message-{i}" for i in range(3)]
    )
    original_body = f"{behavior.SCHEDULE} {behavior.PREPARE}"
    completion_body = (
        f"{behavior.COMPLETION} The separate tool inventory is still required."
    )
    envelopes = [
        behavior._source(config, 0, original_body),
        behavior._source(config, 1, behavior.OTHER_SCHEDULE),
        behavior._source(
            config, 2, completion_body, references=["<currentness-0@example.invalid>"]
        ),
    ]
    retained = _predecessor_case(
        tmp_path,
        monkeypatch,
        config,
        envelopes,
        {
            original_body: _historical_output(
                [
                    behavior._candidate("@current", behavior.SCHEDULE),
                    behavior._candidate("@current", behavior.PREPARE),
                ]
            ),
            behavior.OTHER_SCHEDULE: _historical_output(
                [behavior._candidate("@current", behavior.OTHER_SCHEDULE)]
            ),
            completion_body: _historical_output([]),
        },
    )
    [source_id] = [
        event["event_id"]
        for event in legacy._of_type(retained["events"], *SOURCE_TYPES)
        if event["payload"]["body_text"] == completion_body
    ]
    old = {item["title"]: item for item in retained["member_surface"]["items"]}
    assert set(old) == {behavior.SCHEDULE, behavior.PREPARE, behavior.OTHER_SCHEDULE}
    assert old[behavior.SCHEDULE]["state"] == "pending_review"

    def candidates(inputs):
        pack = inputs["context_pack"]
        assert len(pack["messages"]) == 2
        [related] = [
            message for message in pack["messages"] if message["relation"] == "related"
        ]
        assert related["body_text"] == original_body
        assert "rfc_reference" in related["relation_reason_codes"]
        completed = behavior._candidate(
            related["source_id"], behavior.SCHEDULE, lifecycle_state="completed"
        )
        completed["obligation_assessment"].update(
            actionability="none",
            qualifying_evidence=[
                {
                    "source_id": pack["current_source_id"],
                    "role": "completion",
                    "quote": behavior.COMPLETION,
                }
            ],
        )
        return [completed]

    model = behavior._Model(candidates)
    monkeypatch.setattr(application, "build_fixture_runtime", runtime_factory(model))
    blocked = threading.Event()
    original_write = EventWriter.write
    crash = True

    async def interrupted(writer, envelope, **kwargs):
        if crash and envelope.type == currentness.ASSESSMENT_TYPE:
            blocked.set()
            await asyncio.Future()
        return await original_write(writer, envelope, **kwargs)

    monkeypatch.setattr(EventWriter, "write", interrupted)
    with TestClient(application.create_application(config)) as client:
        client.portal.call(settle, client.app.state.runtime)
        assert model.calls == []
        manifest = _preview(client, [source_id])["manifest"]
        command = _request(client, manifest)
        assert blocked.wait(30), (
            "currentness did not reach its no-draft assessment boundary"
        )
        interrupted_history = _history(client)
        assert len(model.calls) == 1
        assert len(legacy._of_type(interrupted_history, "triage.item.superseded")) == 1
        assert (
            legacy._of_type(
                interrupted_history, currentness.ASSESSMENT_TYPE, currentness.PLAN_TYPE
            )
            == []
        )
    crash = False
    for _ in range(2):
        with TestClient(application.create_application(config)) as client:
            client.portal.call(settle, client.app.state.runtime)
            assert len(model.calls) == 1
            assert (
                _request(client, manifest)["request_event_id"]
                == command["request_event_id"]
            )
            assert (
                _status(client, command["request_event_id"])["targets"][0]["outcome"]
                == "no_candidate"
            )
            after = _history(client)
            assert len(legacy._of_type(after, currentness.ASSESSMENT_TYPE)) == 1
            assert (
                legacy._of_type(
                    after, currentness.PLAN_TYPE, currentness.COMPLETION_TYPE
                )
                == []
            )
            assert len(legacy._of_type(after, "triage.item.superseded")) == 1
            active = {
                item["title"]: item
                for item in behavior._items(client, lens="needs_you")
            }
            assert set(active) == {behavior.PREPARE, behavior.OTHER_SCHEDULE}
            for quote, item in active.items():
                assert item["proposal_id"] == old[quote]["proposal_id"]
            assert legacy._of_type(after, "triage.item.proposed") == legacy._of_type(
                retained["events"], "triage.item.proposed"
            )
            behavior._assert_private_no_effects(client)


def test_currentness_reassesses_retained_required_payment_to_clarification_only(
    tmp_path, monkeypatch
):
    config = settings(tmp_path / "instance", ROOT, selected=["message-0"])
    body = f"{behavior.REVIEW} {behavior.PAYMENT_DATE} {behavior.PAYMENT_LINK}"

    def candidates(alias, *, declared):
        review = behavior._candidate(alias, behavior.REVIEW)
        payment = behavior._candidate(
            alias,
            behavior.PAYMENT_DATE,
            purpose="payment",
            candidate={"due": "2034-10-18T00:00:00Z"},
            unresolved_questions=[
                "What amount remains payable?",
                "Has payment already been made or scheduled?",
            ],
            next_step="Inspect the statement to establish whether anything remains payable.",
        )
        payment["obligation_assessment"].update(
            date_role="deadline",
            qualifying_evidence=[
                {"source_id": alias, "role": "date", "quote": behavior.PAYMENT_DATE},
                {
                    "source_id": alias,
                    "role": "ownership",
                    "quote": behavior.PAYMENT_LINK,
                },
            ],
        )
        if declared:
            payment["obligation_assessment"]["blocking_question_indices"] = [0, 1]
        return [review, payment]

    retained = _predecessor_case(
        tmp_path,
        monkeypatch,
        config,
        [behavior._source(config, 0, body)],
        {body: _historical_output(candidates("@current", declared=False))},
    )
    old = {item["title"]: item for item in retained["member_surface"]["items"]}
    assert (
        old[behavior.PAYMENT_DATE]["interpretation"]["obligation_assessment"][
            "actionability"
        ]
        == "required"
    )
    assert old[behavior.PAYMENT_DATE]["admission_decision"] == "admit"
    source_id = old[behavior.PAYMENT_DATE]["source_event_id"]
    model = behavior._Model(
        lambda inputs: candidates(
            inputs["context_pack"]["current_source_id"], declared=True
        )
    )
    monkeypatch.setattr(application, "build_fixture_runtime", runtime_factory(model))
    with TestClient(application.create_application(config)) as client:
        client.portal.call(settle, client.app.state.runtime)
        assert model.calls == []
        manifest = _preview(client, [source_id])["manifest"]
        _request(client, manifest)
        client.portal.call(settle, client.app.state.runtime)
        assert len(model.calls) == 1
        active = {
            item["title"]: item for item in behavior._items(client, lens="needs_you")
        }
        assert set(active) == {behavior.REVIEW, behavior.PAYMENT_DATE}
        assert (
            active[behavior.REVIEW]["proposal_id"]
            == old[behavior.REVIEW]["proposal_id"]
        )
        payment = active[behavior.PAYMENT_DATE]
        assert payment["proposal_id"] != old[behavior.PAYMENT_DATE]["proposal_id"]
        assert (
            payment["interpretation"]["obligation_assessment"]["actionability"]
            == "unknown"
        )
        assert payment["admission_decision"] == "unresolved"
        assert (
            payment["human_lane"]
            == payment["disposition"]["lane"]
            == "clarification_conflict"
        )
        assert payment["candidate"]["due"] == "2034-10-18T00:00:00Z"
        assert len(payment["interpretation"]["unresolved_questions"]) == 2
        after = _history(client)
        for event in retained["events"]:
            assert (
                next(item for item in after if item["event_id"] == event["event_id"])
                == event
            )
        assert len(legacy._of_type(after, currentness.ASSESSMENT_TYPE)) == 1
        assert len(legacy._of_type(after, currentness.PLAN_TYPE)) == 1
        behavior._assert_private_no_effects(client)


def test_currentness_is_one_three_anchor_manifest_with_separate_closed_authority(
    tmp_path, monkeypatch
):
    config = settings(
        tmp_path / "instance", ROOT, selected=[f"message-{i}" for i in range(4)]
    )
    model = legacy._model(monkeypatch)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        ids = legacy._seed(client, config, 4)
        before, calls = _history(client), list(model.calls)
        response = client.post(
            API + "/preview", headers=AUTH, json={"source_event_ids": ids}
        )
        assert response.status_code == 422
        response = client.post(
            API + "/preview", headers=OTHER_AUTH, json={"source_event_ids": ids[:1]}
        )
        assert response.status_code == 409
        selected = [ids[i] for i in (0, 2, 3)]
        preview = _preview(client, selected)
        manifest = preview["manifest"]
        assert manifest["revision"] == "currentness-v1"
        assert preview["max_classifier_attempts"] == 9
        assert [target["source_event_id"] for target in manifest["targets"]] == selected
        assert (
            closed.MAX_TARGETS == 9 and closed.REVISION == "triage-semantics-2026-09-v1"
        )
        assert (
            closed.CLASSIFIER_SHA256
            == "a51260af6880c1093c9385ef6b9135387b00dc7035a7e78f977364dec149683c"
        )
        assert client.post(legacy.API, headers=AUTH, json=manifest).status_code == 422
        old = legacy._preview(client, selected)["manifest"]
        response = client.post(legacy.API, headers=AUTH, json=old)
        assert response.status_code == 409
        assert response.json()["detail"] == "reviewed_classifier_contract_changed"
        altered = copy.deepcopy(manifest)
        altered["dataset_id"] = "c" * 32
        assert client.post(API, headers=AUTH, json=altered).status_code == 409
        altered = copy.deepcopy(manifest)
        altered["targets"][0]["context_fingerprint"] = "0" * 64
        response = client.post(API, headers=AUTH, json=altered)
        assert response.status_code == 409
        assert response.json()["detail"] == "assessment_or_context_changed"
        assert _history(client) == before and model.calls == calls
        model.mode = "revision"
        command = _request(client, manifest)
        client.portal.call(settle, runtime)
        after = snapshot(client)["events"]
        assert model.calls[: len(calls)] == calls
        assert sorted(model.calls[len(calls) :]) == [("revision", i) for i in (0, 2, 3)]
        revised = legacy._of_type(after, currentness.ASSESSMENT_TYPE)
        assert len(revised) == 3
        assert all(event["schema_version"] == 1 for event in revised)
        assert {event["payload"]["source_event_id"] for event in revised} == set(
            selected
        )
        assert all(
            event["payload"]["assessment_revision"] == "currentness-v1"
            for event in revised
        )
        assert len(legacy._of_type(after, currentness.PLAN_TYPE)) == 3
        assert len(legacy._of_type(after, currentness.COMPLETION_TYPE)) == 3
        assert legacy._of_type(after, *SOURCE_TYPES) == legacy._of_type(
            before, *SOURCE_TYPES
        )
        assert _assurance(client)["counts"]["eligible_candidates"] == 4
        changed = copy.deepcopy(manifest)
        changed["targets"] = changed["targets"][:1]
        response = client.post(API, headers=AUTH, json=changed)
        assert response.status_code == 409
        assert response.json()["detail"] == "reviewed_revision_manifest_already_spent"
        assert (
            client.get(
                API + "/" + command["request_event_id"], headers=OTHER_AUTH
            ).status_code
            == 404
        )
        assert (
            _request(client, manifest)["request_event_id"]
            == command["request_event_id"]
        )
        client.portal.call(runtime.events.write, source(config, 0))
        client.portal.call(settle, runtime)
        assert _history(client) == after
        assert model.calls == calls + [("revision", i) for i in (0, 2, 3)]


def test_currentness_three_failed_attempts_cannot_renew_on_recovery_or_restart(
    tmp_path, monkeypatch
):
    config = settings(tmp_path / "instance", ROOT, selected=["message-0"])
    model = legacy._model(monkeypatch)
    with TestClient(application.create_application(config)) as client:
        [source_id] = legacy._seed(client, config)
        manifest = _preview(client, [source_id])["manifest"]
        model.mode = "fail"
        command = _request(client, manifest)
        client.portal.call(settle, client.app.state.runtime)
        assert model.calls == [("initial", 0)] + [("fail", 0)] * 3
        after = snapshot(client)["events"]
        [assessment] = legacy._of_type(after, currentness.ASSESSMENT_TYPE)
        assert assessment["payload"]["assessment_outcome"] == "failed"
        assert _assurance(client)["counts"]["failed"] == 1
        response = client.post(
            "/api/system/source-assessment-recoveries",
            headers=AUTH,
            json={
                "request_id": "fixture-currentness-cannot-renew",
                "targets": [
                    {
                        "source_event_id": source_id,
                        "failed_assessment_event_id": assessment["event_id"],
                        "expected_failure_reason": "language_seam_OpenClawTimeout",
                    }
                ],
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "failure_not_retryable"
        assert _history(client) == after
    calls = list(model.calls)
    model.mode = "forbid"
    for _ in range(3):
        with TestClient(application.create_application(config)) as client:
            client.portal.call(settle, client.app.state.runtime)
            assert (
                _request(client, manifest)["request_event_id"]
                == command["request_event_id"]
            )
            assert (
                _status(client, command["request_event_id"])["targets"][0]["outcome"]
                == "failed"
            )
            assert model.calls == calls
            assert _stable(_history(client)) == _stable(after)


@pytest.mark.parametrize(
    "authority,outcome",
    [("unchanged", "revision"), ("selection", "revision"), ("unchanged", "fail")],
)
def test_currentness_interrupted_publication_retains_authority_and_attempts(
    tmp_path, monkeypatch, authority, outcome
):
    instance = tmp_path / "instance"
    config = settings(instance, ROOT, selected=["message-0"])
    model = legacy._model(monkeypatch)
    blocked = threading.Event()
    original = EventWriter.write
    crash = False

    async def interrupted(writer, envelope, **kwargs):
        if (
            crash
            and envelope.type == "triage.item.proposed"
            and ":revision:currentness-v1:" in (envelope.idempotency_key or "")
        ):
            blocked.set()
            await asyncio.Future()
        return await original(writer, envelope, **kwargs)

    monkeypatch.setattr(EventWriter, "write", interrupted)
    with TestClient(application.create_application(config)) as client:
        [source_id] = legacy._seed(client, config)
        manifest = _preview(client, [source_id])["manifest"]
        crash, model.mode = True, outcome
        _request(client, manifest)
        assert blocked.wait(30), (
            "currentness never reached the real publication boundary"
        )
        before = _history(client)
        assert len(legacy._of_type(before, currentness.PLAN_TYPE)) == 1
        assert legacy._of_type(before, currentness.COMPLETION_TYPE) == []
    crash, model.mode = False, "forbid"
    calls = list(model.calls)
    assert len(calls) == (4 if outcome == "fail" else 2)
    if authority == "unchanged" and outcome == "revision":
        rolled_back = _predecessor_case(tmp_path, monkeypatch, config, [], {})
        assert rolled_back["model_calls"] == []
        assert _stable(rolled_back["events"]) == _stable(before)
        assert len(legacy._of_type(rolled_back["events"], currentness.PLAN_TYPE)) == 1
        assert legacy._of_type(rolled_back["events"], currentness.COMPLETION_TYPE) == []
    if authority == "selection":
        config = settings(instance, ROOT, selected=["message-1"])

        async def retained():
            log = EventLog(config.instance, config.encryption_key)
            try:
                return [event async for event in log.read_since()]
            finally:
                await log.close()

        frozen = asyncio.run(retained())
        with pytest.raises(
            RevisionRefused, match="context_outside_selected_test_sample"
        ):
            with TestClient(application.create_application(config)):
                pytest.fail("changed authority resumed currentness publication")
        assert asyncio.run(retained()) == frozen
    else:
        with TestClient(application.create_application(config)) as client:
            client.portal.call(settle, client.app.state.runtime)
            after = snapshot(client)["events"]
            assert len(legacy._of_type(after, currentness.COMPLETION_TYPE)) == 1
            assert len(legacy._of_type(after, "triage.item.proposed")) == 1
            for event in before:
                assert (
                    next(
                        item for item in after if item["event_id"] == event["event_id"]
                    )
                    == event
                )
    assert model.calls == calls


@pytest.mark.parametrize("outcome", ["revision", "fail"])
def test_currentness_preserves_closed_receipts_and_e121565_predecessor(
    tmp_path, monkeypatch, outcome
):
    monkeypatch.setattr(legacy, "PREDECESSOR", PREDECESSOR)
    predecessor = legacy._predecessor(tmp_path)
    instance = tmp_path / "instance"
    retained = legacy._run_predecessor(predecessor, instance, "prepare")
    [source_id] = [
        event["event_id"]
        for event in legacy._of_type(retained["events"], *SOURCE_TYPES)
        if event["payload"]["native_message_id"] == "message-0"
    ]
    model = legacy._model(monkeypatch)
    # Spend the closed revision once with its actual historical classifier.
    with TestClient(
        application.create_application(settings(instance, predecessor))
    ) as client:
        client.portal.call(settle, client.app.state.runtime)
        old_manifest = legacy._preview(client, [source_id])["manifest"]
        old_command = legacy._request(client, old_manifest)
        client.portal.call(settle, client.app.state.runtime)
        [old_assessment] = [
            event
            for event in _history(client)
            if event["type"] == "triage.intent.features_extracted"
            and event["schema_version"] == 5
        ]
        assert old_assessment["payload"]["assessment_outcome"] == "no_candidate"
        before = _history(client)
    model.mode = "forbid"
    config = settings(instance, ROOT)
    with TestClient(application.create_application(config)) as client:
        client.portal.call(settle, client.app.state.runtime)
        assert (
            legacy._request(client, old_manifest)["request_event_id"]
            == old_command["request_event_id"]
        )
        manifest = _preview(client, [source_id])["manifest"]
        assert (
            manifest["targets"][0]["assessment_event_id"] == old_assessment["event_id"]
        )
        model.mode = outcome
        command = _request(client, manifest)
        client.portal.call(settle, client.app.state.runtime)
        revised = snapshot(client)
        assert len(legacy._of_type(revised["events"], currentness.ASSESSMENT_TYPE)) == 1
        for event in before:
            assert (
                next(
                    item
                    for item in revised["events"]
                    if item["event_id"] == event["event_id"]
                )
                == event
            )
    [new_assessment] = legacy._of_type(revised["events"], currentness.ASSESSMENT_TYPE)
    rolled_back = _predecessor_case(
        tmp_path,
        monkeypatch,
        config,
        [],
        {},
        predecessor=predecessor,
        recovery_target={
            "source_event_id": source_id,
            "failed_assessment_event_id": new_assessment["event_id"],
            "expected_failure_reason": "language_seam_OpenClawTimeout",
        }
        if outcome == "fail"
        else None,
    )
    assert rolled_back["model_calls"] == []
    assurance = rolled_back["readiness"]["capabilities"]["policy:source-assurance"][
        "details"
    ]
    assert assurance["state"] == "healthy"
    assert (
        assurance["counts"]["failed"]
        == assurance["counts"]["missing"]
        == assurance["counts"]["pending"]
        == 0
    )
    if outcome == "fail":
        assert rolled_back["recovery"] == {
            "status": 409,
            "detail": "failed_assessment_not_current",
        }
    assert _stable(rolled_back["events"]) == _stable(revised["events"])
    assert legacy._of_type(rolled_back["events"], *MODEL_TYPES) == legacy._of_type(
        revised["events"], *MODEL_TYPES
    )
    # The predecessor ignores the new assessment, preserving old source health.
    assert legacy._of_type(
        rolled_back["events"], "triage.item.dismissed"
    ) == legacy._of_type(retained["events"], "triage.item.dismissed")
    model.mode = "forbid"
    calls = list(model.calls)
    with TestClient(application.create_application(config)) as client:
        client.portal.call(settle, client.app.state.runtime)
        assert (
            _request(client, manifest)["request_event_id"]
            == command["request_event_id"]
        )
        assert (
            legacy._request(client, old_manifest)["request_event_id"]
            == old_command["request_event_id"]
        )
        assert _stable(_history(client)) == _stable(rolled_back["events"])
        assert model.calls == calls
        assert _assurance(client)["counts"]["failed"] == (1 if outcome == "fail" else 0)
        subscriber = "pipeline:pipeline:noise_filtering"
        for state in (retained, revised, rolled_back, snapshot(client)):
            assert state["consumers"][subscriber]["consumer_version"] == "23.0.0"
