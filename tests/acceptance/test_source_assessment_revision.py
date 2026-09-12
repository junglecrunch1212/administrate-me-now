"""Finite semantic revision through encrypted state and the production wrapper.

The model transport returns fixed synthetic language results. These tests
exercise authorization and durability, not actual-model semantic accuracy.
"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import adminme.runtime.application as application
from adminme.events.envelope import EventEnvelope
from adminme.events.log import AppendValidationError, EventLog
from adminme.events.writer import EventWriter
from adminme.source_assessment_revision import (
    REFUSAL_TYPE,
    REQUEST_TYPE,
    REVISION,
    REVISION_COMPLETION_TYPE,
    REVISION_PLAN_TYPE,
    RevisionRefused,
)
from tests.fixtures.source_assessment_revision_worker import (
    AUTH,
    MODEL_TYPES,
    OTHER,
    OTHER_AUTH,
    SOURCE_TYPES,
    Model,
    dismiss,
    events,
    runtime_factory,
    settings,
    settle,
    snapshot,
    source,
)


ROOT = Path(__file__).resolve().parents[2]
PREDECESSOR = "d54d2e4ce43cd78f317e9a7bb8d13ad3d644ce84"
API = "/api/system/source-assessment-revisions"


def _model(monkeypatch):
    model = Model()
    monkeypatch.setattr(application, "build_fixture_runtime", runtime_factory(model))
    return model


def _seed(client, config, count=1):
    runtime = client.app.state.runtime
    ids = []
    for index in range(count):
        ids.append(client.portal.call(runtime.events.write, source(config, index)))
        client.portal.call(settle, runtime)
    return ids


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


def _status(client, event_id):
    response = client.get(f"{API}/{event_id}", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def _of_type(values, *types):
    return [event for event in values if event["type"] in types]


def _unchanged_history(client, before):
    for event in before:
        assert (
            client.portal.call(
                client.app.state.runtime.event_log.get, event["event_id"]
            )
            == event
        )


def test_one_nine_target_manifest_preserves_decisions_and_cannot_be_renewed(
    tmp_path, monkeypatch
):
    config = settings(tmp_path / "instance", ROOT)
    model = _model(monkeypatch)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        ids = _seed(client, config, 10)
        dismissed, decision_id = dismiss(client, ids[1])
        before = snapshot(client)["events"]
        calls = list(model.calls)
        blocked = client.post(
            API + "/preview", headers=AUTH, json={"source_event_ids": [ids[1]]}
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "member_decision_preserved"
        selected = [ids[0], *ids[2:]]
        preview = _preview(client, selected)
        manifest = preview["manifest"]
        assert len(manifest["targets"]) == 9
        assert preview["max_classifier_attempts"] == 27
        assert preview["source_acquisition"] is False
        assert preview["external_write_requests"] == 0
        assert model.calls == calls
        assert client.portal.call(events, runtime) == before
        for target in manifest["targets"]:
            assert target["context_source_event_ids"] == [target["source_event_id"]]
            assert target["episode_source_event_ids"] == [target["source_event_id"]]
        too_many = client.post(
            API + "/preview", headers=AUTH, json={"source_event_ids": ids}
        )
        assert too_many.status_code == 422
        model.mode = "revision"
        accepted = _request(client, manifest)
        client.portal.call(settle, runtime)
        status = _status(client, accepted["request_event_id"])
        assert status["terminal"] is True
        assert {target["outcome"] for target in status["targets"]} == {
            "eligible_candidates"
        }
        assert model.calls[len(calls) :] == [
            ("revision", index) for index in (0, 2, 3, 4, 5, 6, 7, 8, 9)
        ]
        after = snapshot(client)["events"]
        _unchanged_history(client, before)
        assert _of_type(after, *SOURCE_TYPES) == _of_type(before, *SOURCE_TYPES)
        assert len(_of_type(after, REQUEST_TYPE)) == 1
        revisions = [
            event
            for event in _of_type(after, "triage.intent.features_extracted")
            if event["schema_version"] == 5
        ]
        assert len(revisions) == 9
        for event in revisions:
            target = next(
                item
                for item in manifest["targets"]
                if item["source_event_id"] == event["payload"]["source_event_id"]
            )
            assert (
                event["payload"]["supersedes_assessment_event_id"]
                == target["assessment_event_id"]
            )
            assert (
                event["payload"]["revision_request_event_id"]
                == accepted["request_event_id"]
            )
            assert event["payload"]["assessment_revision"] == REVISION
        row = (
            runtime.projections.connection("triage")
            .execute(
                "SELECT state,last_event_id FROM triage_items WHERE proposal_id=?",
                (dismissed,),
            )
            .fetchone()
        )
        assert tuple(row) == ("dismissed", decision_id)
        unchanged_calls = list(model.calls)
        for _ in range(2):
            assert (
                _request(client, manifest)["request_event_id"]
                == accepted["request_event_id"]
            )
        original = _of_type(before, *SOURCE_TYPES)[0]
        redelivery = EventEnvelope.model_validate(
            {
                key: value
                for key, value in original.items()
                if key not in {"version", "source"}
            }
        )
        assert client.portal.call(runtime.events.write, redelivery) == ids[0]
        client.portal.call(settle, runtime)
        assert model.calls == unchanged_calls
        assert client.portal.call(events, runtime) == after
        changed = {**manifest, "targets": manifest["targets"][:-1]}
        response = client.post(API, headers=AUTH, json=changed)
        assert response.status_code == 409
        assert response.json()["detail"] == "reviewed_revision_manifest_already_spent"
        assert (
            client.get(
                f"{API}/{accepted['request_event_id']}", headers=OTHER_AUTH
            ).status_code
            == 404
        )
        assert client.post(API, headers=OTHER_AUTH, json=manifest).status_code == 409
        assert (
            client.get("/api/member/v1/surfaces/triage", headers=OTHER_AUTH).json()[
                "items"
            ]
            == []
        )


def test_preview_requires_selected_owner_account_adapter_and_latest_exact_context(
    tmp_path, monkeypatch
):
    config = settings(
        tmp_path / "instance", ROOT, selected=["message-0", "message-1", "message-7"]
    )
    model = _model(monkeypatch)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        oldest = client.portal.call(
            runtime.events.write, source(config, 0, thread="shared")
        )
        client.portal.call(settle, runtime)
        latest = client.portal.call(
            runtime.events.write, source(config, 1, thread="shared")
        )
        client.portal.call(settle, runtime)
        before = client.portal.call(events, runtime)
        calls = list(model.calls)
        response = client.post(
            API + "/preview", headers=AUTH, json={"source_event_ids": [oldest]}
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "latest_episode_anchor_required"
        manifest = _preview(client, [latest])["manifest"]
        assert set(manifest["targets"][0]["context_source_event_ids"]) == {
            oldest,
            latest,
        }
        assert set(manifest["targets"][0]["episode_source_event_ids"]) == {
            oldest,
            latest,
        }
        assert model.calls == calls and client.portal.call(events, runtime) == before
        for index, changes in enumerate(
            (
                {"owner": OTHER},
                {"account": "other-revision@example.invalid"},
                {"adapter": "other-revision-adapter"},
                {},
            ),
            start=2,
        ):
            wrong = client.portal.call(
                runtime.events.write, source(config, index, **changes)
            )
            client.portal.call(settle, runtime)
            previous_calls = list(model.calls)
            response = client.post(
                API + "/preview", headers=AUTH, json={"source_event_ids": [wrong]}
            )
            assert response.status_code == 409
            assert model.calls == previous_calls
        mismatched_envelope = source(config, 7).model_copy(
            update={"source_account_id": "other-envelope@example.invalid"}
        )
        previous_calls = list(model.calls)
        before_mismatch = client.portal.call(events, runtime)
        with pytest.raises(AppendValidationError, match="source_account_id"):
            client.portal.call(runtime.events.write, mismatched_envelope)
        assert model.calls == previous_calls
        assert client.portal.call(events, runtime) == before_mismatch
        wrong_dataset = {**manifest, "dataset_id": "f" * 32}
        assert client.post(API, headers=AUTH, json=wrong_dataset).status_code == 409
        changed_assessment = copy.deepcopy(manifest)
        changed_assessment["targets"][0]["assessment_event_id"] = (
            "fixture-stale-assessment"
        )
        assert (
            client.post(API, headers=AUTH, json=changed_assessment).status_code == 409
        )
        changed_fingerprint = copy.deepcopy(manifest)
        changed_fingerprint["targets"][0]["context_fingerprint"] = "f" * 64
        assert (
            client.post(API, headers=AUTH, json=changed_fingerprint).status_code == 409
        )
        changed_source = source(config, 1, thread="shared")
        changed_source = changed_source.model_copy(
            update={
                "idempotency_key": "fixture-revision-source:changed-content",
                "payload": {
                    **changed_source.payload,
                    "body_text": "Please complete the revised fixture preparation form.",
                },
            }
        )
        changed_id = client.portal.call(runtime.events.write, changed_source)
        client.portal.call(settle, runtime)
        assert changed_id != latest
        previous_calls = list(model.calls)
        assert client.post(API, headers=AUTH, json=manifest).status_code == 409
        assert model.calls == previous_calls
        newer = client.portal.call(
            runtime.events.write, source(config, 6, thread="shared")
        )
        client.portal.call(settle, runtime)
        previous_calls = list(model.calls)
        assert client.post(API, headers=AUTH, json=manifest).status_code == 409
        assert (
            client.post(
                API + "/preview", headers=AUTH, json={"source_event_ids": [newer]}
            ).status_code
            == 409
        )
        assert model.calls == previous_calls
        assert _of_type(client.portal.call(events, runtime), REQUEST_TYPE) == []


@pytest.mark.parametrize("change", ["dataset", "selection", "context"])
def test_queued_manifest_rechecks_authority_and_context_after_restart(
    tmp_path, monkeypatch, change
):
    instance = tmp_path / "instance"
    config = settings(instance, ROOT)
    model = _model(monkeypatch)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        [source_id] = _seed(client, config)
        manifest = _preview(client, [source_id])["manifest"]
        # The actual bus is stopped before accepting the request. This is a
        # durable queued command, not a stubbed consumer or classifier result.
        client.portal.call(runtime.bus.stop)
        command = _request(client, manifest)
        assert command["terminal"] is False
        if change == "context":
            client.portal.call(
                runtime.events.write,
                source(config, 2, thread="fixture-revision-thread-0"),
            )
        before = client.portal.call(events, runtime)
    if change == "dataset":
        config = settings(instance, ROOT, dataset="c" * 32)
    elif change == "selection":
        config = settings(instance, ROOT, selected=["message-2"])
    model.mode = "revision" if change == "context" else "forbid"
    before_calls = list(model.calls)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        client.portal.call(settle, runtime)
        status = _status(client, command["request_event_id"])
        assert status["terminal"] is True
        assert status["targets"][0]["state"] == "refused"
        assert status["targets"][0]["outcome"] is None
        assert not [call for call in model.calls[len(before_calls) :] if call[1] == 0]
        _unchanged_history(client, before)
        snapshot(client)


@pytest.mark.parametrize("change", ["member_decision", "context"])
def test_change_during_model_await_spends_receipt_without_publishing_revision(
    tmp_path, monkeypatch, change
):
    config = settings(tmp_path / "instance", ROOT)
    model = _model(monkeypatch)
    responding = model.respond
    entered = threading.Event()
    release = asyncio.Event()
    changed_quote = "complete the fixture preparation form for activity 1."

    async def delayed_transport(request, args):
        index = int(args["input"]["subject"].rsplit(" ", 1)[-1])
        revising = model.mode == "revision" and index == 1
        if revising:
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=30)
        response = await responding(request, args)
        payload = response.json()
        output = payload["result"]["details"]["json"]
        if revising:
            # This remains an exact source substring but changes both title
            # and candidate identity, exercising more than exact-key dedupe.
            [candidate] = output["candidates"]
            candidate.update(
                title=changed_quote,
                evidence_quote=changed_quote,
                compact_summary=changed_quote,
            )
        elif index == 2:
            # The newer context follows its ordinary production assessment;
            # the external fixture deliberately finds no additional work.
            output["candidates"] = []
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(model, "respond", delayed_transport)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        source_id = client.portal.call(runtime.events.write, source(config, 1))
        client.portal.call(settle, runtime)
        before = snapshot(client)["events"]
        [original] = _of_type(before, "triage.item.proposed")
        assert original["payload"]["title"] != changed_quote
        manifest = _preview(client, [source_id])["manifest"]
        model.mode = "revision"
        command = _request(client, manifest)
        assert entered.wait(30), "revision did not enter the real model transport"
        decision_id = None
        try:
            if change == "member_decision":
                response = client.get(
                    "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
                    headers=AUTH,
                )
                assert response.status_code == 200, response.text
                [item] = response.json()["items"]
                response = client.post(
                    f"/api/member/v1/triage/{item['proposal_id']}/dismiss",
                    headers=AUTH,
                    json={
                        "request_id": "fixture-revision-during-inference",
                        "expected_last_event_id": item["last_event_id"],
                        "reason": "Already handled during source assessment",
                    },
                )
                assert response.status_code == 200, response.text
                decision_id = response.json()["event_id"]
            else:
                client.portal.call(
                    runtime.events.write,
                    source(config, 2, thread="fixture-revision-thread-1"),
                )
        finally:
            client.portal.call(release.set)
        client.portal.call(settle, runtime)
        after = snapshot(client)["events"]
        _unchanged_history(client, before)
        status = _status(client, command["request_event_id"])
        assert status["terminal"] is True
        [target] = status["targets"]
        assert target["state"] == "refused" and target["outcome"] is None
        assert target["refusal_reason"] in (
            {"member_decision_preserved"}
            if change == "member_decision"
            else {"latest_episode_anchor_required", "assessment_or_context_changed"}
        )
        [receipt] = [
            event
            for event in _of_type(after, "skill.call.recorded")
            if "message-nature:revision:" in event["idempotency_key"]
        ]
        assert receipt["causation_id"] == source_id
        assert (
            receipt["payload"]["outputs"]["candidates"][0]["evidence_quote"]
            == changed_quote
        )
        [refusal] = _of_type(after, REFUSAL_TYPE)
        assert receipt["event_id"] < refusal["event_id"]
        assert _of_type(after, "triage.item.proposed") == [original]
        assert _of_type(after, REVISION_PLAN_TYPE, REVISION_COMPLETION_TYPE) == []
        assert not [
            event
            for event in _of_type(after, "triage.intent.features_extracted")
            if event["schema_version"] == 5
        ]
        if decision_id is not None:
            row = (
                runtime.projections.connection("triage")
                .execute(
                    "SELECT state,last_event_id FROM triage_items WHERE proposal_id=?",
                    (original["payload"]["proposal_id"],),
                )
                .fetchone()
            )
            assert tuple(row) == ("dismissed", decision_id)
        calls = list(model.calls)
        assert (
            _request(client, manifest)["request_event_id"]
            == command["request_event_id"]
        )
        client.portal.call(settle, runtime)
        assert model.calls == calls
        assert client.portal.call(events, runtime) == after


def test_failed_revision_spends_three_attempts_across_duplicate_and_three_restarts(
    tmp_path, monkeypatch
):
    config = settings(tmp_path / "instance", ROOT)
    model = _model(monkeypatch)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        [source_id] = _seed(client, config)
        before = client.portal.call(events, runtime)
        manifest = _preview(client, [source_id])["manifest"]
        model.mode = "fail"
        command = _request(client, manifest)
        client.portal.call(settle, runtime)
        status = _status(client, command["request_event_id"])
        assert status["terminal"] and status["targets"][0]["outcome"] == "failed"
        assert model.calls == [("initial", 0), ("fail", 0), ("fail", 0), ("fail", 0)]
        after = snapshot(client)["events"]
        failures = _of_type(after, "skill.call.failed")
        assert len(failures) == 3
        assert all(event["payload"]["skill_version"] == "8.0.3" for event in failures)
        assert any(":recovery-1:" in event["idempotency_key"] for event in failures)
        assert any(":recovery-2:" in event["idempotency_key"] for event in failures)
        _unchanged_history(client, before)
        [failed_assessment] = [
            event
            for event in _of_type(after, "triage.intent.features_extracted")
            if event["schema_version"] == 5
        ]
        calls = list(model.calls)
        response = client.post(
            "/api/system/source-assessment-recoveries",
            headers=AUTH,
            json={
                "request_id": "fixture-revision-cannot-renew-as-ordinary-recovery",
                "targets": [
                    {
                        "source_event_id": source_id,
                        "failed_assessment_event_id": failed_assessment["event_id"],
                        "expected_failure_reason": "language_seam_OpenClawTimeout",
                    }
                ],
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "failure_not_retryable"
        client.portal.call(settle, runtime)
        assert model.calls == calls
        assert client.portal.call(events, runtime) == after
    model.mode = "forbid"
    calls = list(model.calls)
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
            restarted = snapshot(client)["events"]
            _unchanged_history(client, after)
            # Normal startup appends its own lifecycle evidence. The complete
            # source/model/assessment/publication/decision history stays fixed.
            assert [
                event for event in restarted if event["type"] != "runtime.started"
            ] == [event for event in after if event["type"] != "runtime.started"]


@pytest.mark.parametrize(
    ("authority", "model_outcome"),
    [
        ("unchanged", "success"),
        ("dataset", "success"),
        ("selection", "success"),
        ("classifier", "success"),
        ("unchanged", "failed"),
    ],
)
def test_interrupted_revision_publication_requires_unchanged_authority_without_repeating_model(
    tmp_path, monkeypatch, authority, model_outcome
):
    instance = tmp_path / "instance"
    config = settings(instance, ROOT)
    model = _model(monkeypatch)
    blocked = threading.Event()
    original_write = EventWriter.write
    crash = False

    async def interrupted(writer, envelope, **kwargs):
        if (
            crash
            and envelope.type == "triage.item.proposed"
            and ":revision:" in (envelope.idempotency_key or "")
        ):
            blocked.set()
            await asyncio.Future()
        return await original_write(writer, envelope, **kwargs)

    monkeypatch.setattr(EventWriter, "write", interrupted)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        [source_id] = _seed(client, config)
        manifest = _preview(client, [source_id])["manifest"]
        crash = True
        model.mode = "fail" if model_outcome == "failed" else "revision"
        command = _request(client, manifest)
        assert blocked.wait(30), (
            "real revision publication did not reach interruption boundary"
        )
        before = client.portal.call(events, runtime)
        [plan] = _of_type(before, REVISION_PLAN_TYPE)
        assert plan["payload"]["assessment_schema_version"] == 5
        assert len(plan["payload"]["proposals"]) == 1
        assert _of_type(before, REVISION_COMPLETION_TYPE) == []
        expected_outcome = (
            "failed" if model_outcome == "failed" else "eligible_candidates"
        )
        [assessment] = [
            event
            for event in _of_type(before, "triage.intent.features_extracted")
            if event["schema_version"] == 5
        ]
        assert assessment["payload"]["assessment_outcome"] == expected_outcome
        assert assessment["event_id"] < plan["event_id"]
        assert len(_of_type(before, "skill.call.recorded")) == (
            1 if model_outcome == "failed" else 2
        )
        assert len(_of_type(before, "skill.call.failed")) == (
            3 if model_outcome == "failed" else 0
        )
        if model_outcome == "failed":
            assert model.calls == [
                ("initial", 0),
                ("fail", 0),
                ("fail", 0),
                ("fail", 0),
            ]
        assert _of_type(before, "triage.item.proposed") == []
    crash = False
    model.mode = "forbid"
    calls = list(model.calls)
    if authority != "unchanged":

        async def read_retained_events():
            log = EventLog(config.instance, config.encryption_key)
            try:
                return [event async for event in log.read_since()]
            finally:
                await log.close()

        retained = asyncio.run(read_retained_events())
        if authority == "dataset":
            config = settings(instance, ROOT, dataset="c" * 32)
            reason = "disposable_dataset_or_selection_changed"
        elif authority == "selection":
            config = settings(instance, ROOT, selected=["message-2"])
            reason = "context_outside_selected_test_sample"
        else:
            # A real alternate pack tree changes only the classifier bytes.
            # No authorization, pipeline or receipt reader is substituted.
            altered_root = tmp_path / "unreviewed-classifier"
            for directory in ("packs", "bootstrap", "docs/refactor"):
                shutil.copytree(ROOT / directory, altered_root / directory)
            prompt = altered_root / "packs/skills/classify_message_nature/prompt.jinja2"
            prompt.write_text(prompt.read_text() + "\nSynthetic unreviewed revision.\n")
            config = settings(instance, altered_root)
            reason = "reviewed_classifier_contract_changed"
        app = application.create_application(config)
        with pytest.raises(RevisionRefused, match=f"^{reason}$"):
            with TestClient(app):
                pytest.fail("changed publication authority admitted runtime startup")
        assert app.state.runtime.started is False
        assert model.calls == calls
        # Startup refusal preserves the completed inference and unfinished
        # plan exactly; it publishes neither a proposal nor a refusal result.
        assert asyncio.run(read_retained_events()) == retained
        return
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        client.portal.call(settle, runtime)
        _unchanged_history(client, before)
        after = snapshot(client)["events"]
        assert model.calls == calls
        assert len(_of_type(after, REVISION_COMPLETION_TYPE)) == 1
        [proposal] = _of_type(after, "triage.item.proposed")
        assert proposal["payload"]["source_event_id"] == source_id
        assert (
            _status(client, command["request_event_id"])["targets"][0]["outcome"]
            == expected_outcome
        )


def _predecessor(tmp_path):
    resolved = subprocess.check_output(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(ROOT),
            "rev-parse",
            f"{PREDECESSOR}^{{commit}}",
        ],
        text=True,
        timeout=30,
    ).strip()
    assert resolved == PREDECESSOR
    archive_bytes = subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(ROOT), "archive", PREDECESSOR],
        timeout=60,
    )
    root = tmp_path / "protected-predecessor"
    root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        archive.extractall(root, filter="data")
    return root


def _run_predecessor(root, instance, mode):
    result = instance.parent / f"predecessor-{mode}.json"
    worker = (ROOT / "tests/fixtures/source_assessment_revision_worker.py").read_text()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ADMINME_", "OPENCLAW_")) and key != "PYTHONPATH"
    }
    environment["PYTHONPATH"] = os.pathsep.join((str(root), str(root / "bridge")))
    environment["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", worker, str(root), str(instance), str(result), mode],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(result.read_text())


def test_protected_predecessor_still_serves_after_explicit_revision_without_reclassification(
    tmp_path, monkeypatch
):
    predecessor = _predecessor(tmp_path)
    instance = tmp_path / "instance"
    retained = _run_predecessor(predecessor, instance, "prepare")
    receipts = _of_type(retained["events"], "skill.call.recorded")
    assert len(receipts) == 2
    assert {event["payload"]["skill_version"] for event in receipts} == {"8.0.1"}
    [source_id] = [
        event["event_id"]
        for event in _of_type(retained["events"], *SOURCE_TYPES)
        if event["payload"]["native_message_id"] == "message-0"
    ]
    config = settings(instance, ROOT)
    model = _model(monkeypatch)
    model.mode = "forbid"
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        client.portal.call(settle, runtime)
        assert model.calls == []
        _unchanged_history(client, retained["events"])
        manifest = _preview(client, [source_id])["manifest"]
        model.mode = "revision"
        command = _request(client, manifest)
        client.portal.call(settle, runtime)
        assert model.calls == [("revision", 0)]
        current = snapshot(client)
        revised = current["events"]
        assert (
            len(
                [
                    event
                    for event in revised
                    if event["type"] == "triage.intent.features_extracted"
                    and event["schema_version"] == 5
                ]
            )
            == 1
        )
        assert len(_of_type(revised, REVISION_PLAN_TYPE)) == 1
        assert len(_of_type(revised, REVISION_COMPLETION_TYPE)) == 1
    rolled_back = _run_predecessor(predecessor, instance, "verify")
    assert rolled_back["model_calls"] == []
    subscriber = "pipeline:pipeline:noise_filtering"
    retained_consumer = retained["consumers"][subscriber]
    current_consumer = current["consumers"][subscriber]
    predecessor_consumer = rolled_back["consumers"][subscriber]
    assert {
        state["consumer_version"]
        for state in (retained_consumer, current_consumer, predecessor_consumer)
    } == {"23.0.0"}
    assert (
        retained_consumer["checkpoint_event_id"]
        <= current_consumer["checkpoint_event_id"]
        <= predecessor_consumer["checkpoint_event_id"]
    )
    for types in (
        SOURCE_TYPES,
        MODEL_TYPES,
        ("triage.item.proposed",),
        ("triage.item.dismissed",),
    ):
        assert _of_type(rolled_back["events"], *types) == _of_type(revised, *types)
    model.mode = "forbid"
    calls = list(model.calls)
    with TestClient(application.create_application(config)) as client:
        client.portal.call(settle, client.app.state.runtime)
        assert model.calls == calls
        assert (
            _request(client, manifest)["request_event_id"]
            == command["request_event_id"]
        )
        _unchanged_history(client, revised)
        snapshot(client)
