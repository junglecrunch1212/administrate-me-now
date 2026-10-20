"""Explicit forwarded recovery through the production application and skill seam."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from adminme.lib.skill_runner import run_skill
from adminme.runtime.application import create_application
from adminme.source_assurance import audit_source_assurance
from adminme.submission_recovery import (
    RECOVERY_EVENT,
    REFUSED_EVENT,
    SubmissionRecoveryRefused,
    build_submission_recovery_requests,
    validate_submission_recovery_target,
)
from bootstrap.submission_repair import SubmissionRepairManifest
from tests.acceptance.test_forwarded_submission_extraction import (
    _forward,
    _items,
    _settings,
)
from tests.acceptance.test_triage_vertical_slice import (
    AUTH,
    MEMBER,
    _events,
    _grant_cos_private_context,
)


@contextmanager
def gateway_boundary(client: TestClient):
    """Replace only credential-free Gateway HTTP, retaining real skill receipts."""
    runtime = client.app.state.runtime
    state: dict[str, Any] = {
        "fail": True,
        "calls": [],
        "entered": None,
        "release": None,
        "entered_after": 1,
        "active": 0,
        "max_active": 0,
    }

    async def gateway(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["calls"].append(body)
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        if state["entered"] is not None:
            if state["active"] >= state["entered_after"]:
                state["entered"].set()
            await state["release"].wait()
        state["active"] -= 1
        if state["fail"]:
            raise httpx.ReadTimeout("synthetic extraction deadline", request=request)
        quote = "Call the school office."
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "details": {
                        "json": {
                            "candidates": [
                                {
                                    "target_kind": "task",
                                    "title": quote,
                                    "candidate": {},
                                    "evidence_quote": quote,
                                    "confidence": 0.9,
                                    "reasons": ["explicit selected obligation"],
                                }
                            ],
                            "reasons": [],
                        },
                        "provider": "fixture",
                        "model": "recovery-fixture",
                    },
                    "invocation_id": "fixture-recovery-call",
                },
            },
        )

    model_client = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    original = runtime.run_skill_fn
    assert original.func is run_skill
    runtime.run_skill_fn = partial(
        run_skill,
        _runtime=replace(
            original.keywords["_runtime"], httpx_client_factory=lambda: model_client
        ),
    )
    try:
        yield state
    finally:
        runtime.run_skill_fn = original
        client.portal.call(model_client.aclose)


def failed_forward(
    client: TestClient, key: str = "recovery-forward"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Real private forward and encrypted failed feature; no source fabrication."""
    runtime = client.app.state.runtime
    _grant_cos_private_context(client, "recovery-context-" + key)
    _, source = _forward(
        client, key, "Preserved selected background.\nCall the school office."
    )
    features = client.portal.call(_events, runtime, "triage.intent.features_extracted")
    failed = [
        e for e in features if e["payload"]["source_event_id"] == source["event_id"]
    ][-1]
    assert failed["payload"]["assessment_outcome"] == "failed"
    assert failed["payload"]["reason_codes"][0] != "language_seam_OpenClawTimeout"
    assert "language_seam_OpenClawTimeout" in failed["payload"]["reason_codes"]
    return source, failed


def repair_manifest(
    source: dict[str, Any], failed: dict[str, Any]
) -> SubmissionRepairManifest:
    return SubmissionRepairManifest.model_validate(
        {
            "contract": "forwarded-submission-repair-v1",
            "predecessor_commit": "a" * 40,
            "candidate_commit": "b" * 40,
            "operator_principal_id": "operator:fixture-repair",
            "owner_member_id": MEMBER,
            "owner_authorization_sha256": "c" * 64,
            "minimum_source_facts": 1,
            "targets": [
                {
                    "source_event_id": source["event_id"],
                    "failed_assessment_event_id": failed["event_id"],
                    "member_id": MEMBER,
                    "expected_failure_reason": "language_seam_OpenClawTimeout",
                }
            ],
        }
    )


def authorize_repair(runtime: Any, manifest: SubmissionRepairManifest) -> None:
    runtime.settings.host_topology["deployment_binding"] = {
        "release_commit": manifest.candidate_commit
    }
    runtime.settings.deployment_evidence.setdefault("activation_evidence", {})[
        "submission_repair"
    ] = manifest.public_binding()


async def append_repair(runtime: Any, manifest: SubmissionRepairManifest) -> list[str]:
    envelopes = await build_submission_recovery_requests(
        runtime.event_log,
        request_id="guarded-submission-repair:" + manifest.digest(),
        targets=manifest.targets,
        tenant_id=runtime.settings.instance.tenant_id,
        operator_principal_id=manifest.operator_principal_id,
        owner_member_id=manifest.owner_member_id,
        owner_authorization_sha256=manifest.owner_authorization_sha256,
        candidate_commit=manifest.candidate_commit,
    )
    return await runtime.events.write_batch(envelopes)


async def source_audit(runtime: Any) -> dict[str, Any]:
    return await audit_source_assurance(
        runtime.event_log,
        repository_root=runtime.settings.repository_root,
        capabilities={},
    )


def test_explicit_retry_preserves_failure_then_reaches_triage_and_survives_restart(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "recovery"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)
    with TestClient(app) as client, gateway_boundary(client) as gateway:
        runtime = app.state.runtime
        source, failed = failed_forward(client)
        manifest = repair_manifest(source, failed)
        authorize_repair(runtime, manifest)
        gateway["fail"] = False
        [request_id] = client.portal.call(append_repair, runtime, manifest)
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:recovery", request_id
        )
        assert len(gateway["calls"]) == 2
        features = client.portal.call(
            _events, runtime, "triage.intent.features_extracted"
        )
        assert features[0] == failed
        assert len(features) == 2
        assert features[-1]["schema_version"] == 6
        assert (
            features[-1]["payload"]["supersedes_assessment_event_id"]
            == failed["event_id"]
        )
        assert features[-1]["payload"]["recovery_request_event_id"] == request_id
        assert any(
            item.get("parent_intent_id") == source["payload"]["intent_id"]
            and item["state"] == "pending_review"
            for item in _items(client)
        )
        audit = client.portal.call(source_audit, runtime)
        assert audit["state"] == "healthy", audit
        assert audit["submission_recovery"]["counts"] == {
            "requested": 1,
            "succeeded": 1,
            "failed": 0,
            "refused": 0,
            "pending": 0,
            "invalid": 0,
        }
        assert client.portal.call(append_repair, runtime, manifest) == [request_id]
        request = client.portal.call(runtime.event_log.get, request_id)
        client.portal.call(runtime.submission_interpreter.handle_recovery, request)
        client.portal.call(runtime.submission_interpreter.handle, source)
        assert len(gateway["calls"]) == 2
        assert (
            client.portal.call(
                _events,
                runtime,
                "external.sent",
                "external_sync.write_requested",
                "task.created",
            )
            == []
        )
    restarted = create_application(settings)
    with TestClient(restarted) as client, gateway_boundary(client) as gateway:
        runtime = restarted.state.runtime
        client.portal.call(
            runtime.observation.disable, "operator:fixture", "completed recovery"
        )
        runtime.settings.host_topology["deployment_binding"]["release_commit"] = (
            "d" * 40
        )
        runtime.settings.deployment_evidence["activation_evidence"].pop(
            "submission_repair"
        )
        request = client.portal.call(runtime.event_log.get, request_id)
        client.portal.call(runtime.submission_interpreter.handle_recovery, request)
        assert gateway["calls"] == []
        assert client.portal.call(_events, runtime, REFUSED_EVENT) == []
        assert client.portal.call(source_audit, runtime)["state"] == "healthy"
        assert (
            len(
                client.portal.call(_events, runtime, "triage.intent.features_extracted")
            )
            == 2
        )


@pytest.mark.parametrize(
    "defect", ["candidate", "manifest", "targets", "owner", "observation"]
)
def test_recovery_refuses_changed_authority_without_model_contact(
    tmp_path: Path, defect: str
) -> None:
    instance = tmp_path / defect
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client, gateway_boundary(client) as gateway:
        runtime = app.state.runtime
        source, failed = failed_forward(client)
        manifest = repair_manifest(source, failed)
        authorize_repair(runtime, manifest)
        binding = runtime.settings.deployment_evidence["activation_evidence"][
            "submission_repair"
        ]
        if defect == "candidate":
            runtime.settings.host_topology["deployment_binding"]["release_commit"] = (
                "d" * 40
            )
        elif defect == "manifest":
            binding["manifest_sha256"] = "d" * 64
        elif defect == "targets":
            binding["targets_sha256"] = "d" * 64
        elif defect == "owner":
            runtime.settings.member_roles[MEMBER] = "child"
        else:
            client.portal.call(
                runtime.observation.disable, "operator:fixture", "fixture refusal"
            )
        [request_id] = client.portal.call(append_repair, runtime, manifest)
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:recovery", request_id
        )
        assert len(gateway["calls"]) == 1
        [refusal] = client.portal.call(_events, runtime, REFUSED_EVENT)
        assert refusal["payload"]["request_event_id"] == request_id
        assert (
            len(
                client.portal.call(_events, runtime, "triage.intent.features_extracted")
            )
            == 1
        )


def test_failed_retries_are_retained_bounded_and_require_the_newest_failure(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "retry-limit"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client, gateway_boundary(client) as gateway:
        runtime = app.state.runtime
        source, original = failed_forward(client)
        failed = original
        for attempt in range(1, 4):
            manifest = repair_manifest(source, failed)
            authorize_repair(runtime, manifest)
            [request_id] = client.portal.call(append_repair, runtime, manifest)
            client.portal.call(
                runtime.wait_for_subscriber, "member-submissions:recovery", request_id
            )
            features = client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            assert len(features) == attempt + 1
            assert features[0] == original
            failed = features[-1]
            assert failed["payload"]["assessment_outcome"] == "failed"
            assert failed["payload"]["recovery_attempt"] == attempt
        assert len(gateway["calls"]) == 4
        with pytest.raises(SubmissionRecoveryRefused, match="recovery_attempt_limit"):
            client.portal.call(append_repair, runtime, repair_manifest(source, failed))

        async def stale() -> None:
            await validate_submission_recovery_target(
                runtime.event_log,
                repair_manifest(source, original).targets[0],
                tenant_id=runtime.settings.instance.tenant_id,
            )

        with pytest.raises(
            SubmissionRecoveryRefused, match="failed_assessment_not_current"
        ):
            client.portal.call(stale)
        audit = client.portal.call(source_audit, runtime)
        assert audit["state"] == "degraded"
        assert audit["submission_recovery"]["counts"]["failed"] == 3
        assert len(client.portal.call(_events, runtime, RECOVERY_EVENT)) == 3
        assert (
            client.portal.call(
                _events, runtime, "triage.item.proposed", "external.sent"
            )
            == []
        )


def test_member_decision_during_recovery_prevents_late_proposal(tmp_path: Path) -> None:
    instance = tmp_path / "decision-race"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client, gateway_boundary(client) as gateway:
        runtime = app.state.runtime
        source, failed = failed_forward(client)
        manifest = repair_manifest(source, failed)
        authorize_repair(runtime, manifest)
        gateway["fail"] = False

        async def prepare() -> None:
            gateway["entered"] = asyncio.Event()
            gateway["release"] = asyncio.Event()

        client.portal.call(prepare)
        [request_id] = client.portal.call(append_repair, runtime, manifest)

        async def entered() -> None:
            await asyncio.wait_for(gateway["entered"].wait(), 5)

        client.portal.call(entered)
        try:
            holding = next(
                row
                for row in _items(client)
                if row["proposal_id"] == source["payload"]["intent_id"]
            )
            dismissed = client.post(
                f"/api/member/v1/triage/{source['payload']['intent_id']}/dismiss",
                headers=AUTH,
                json={
                    "request_id": "recovery-race-dismiss",
                    "expected_last_event_id": holding["last_event_id"],
                    "reason": "Already handled",
                },
            )
            assert dismissed.status_code == 200, dismissed.text
        finally:
            client.portal.call(gateway["release"].set)
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:recovery", request_id
        )
        assert len(gateway["calls"]) == 2
        assert (
            client.portal.call(
                _events,
                runtime,
                "triage.item.proposed",
                "task.created",
                "external.sent",
            )
            == []
        )
        client.portal.call(runtime.projections.rebuild, "triage")
        row = (
            runtime.projections.connection("triage")
            .execute(
                "SELECT state FROM triage_items WHERE proposal_id=?",
                (source["payload"]["intent_id"],),
            )
            .fetchone()
        )
        assert row[0] == "dismissed"


def test_recovery_never_replays_an_unselected_failed_forward(tmp_path: Path) -> None:
    instance = tmp_path / "selected-only"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client, gateway_boundary(client) as gateway:
        runtime = app.state.runtime
        source, failed = failed_forward(client, "selected")
        other, untouched = failed_forward(client, "unselected")
        manifest = repair_manifest(source, failed)
        authorize_repair(runtime, manifest)
        gateway["fail"] = False
        [request_id] = client.portal.call(append_repair, runtime, manifest)
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:recovery", request_id
        )
        assert len(gateway["calls"]) == 3
        other_features = [
            e
            for e in client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            if e["payload"]["source_event_id"] == other["event_id"]
        ]
        assert other_features == [untouched]


def test_recovery_batch_has_two_in_flight_and_pending_commands_block_health(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "bounded-batch"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client, gateway_boundary(client) as gateway:
        runtime = app.state.runtime
        originals = [failed_forward(client, f"batch-{index}") for index in range(3)]
        manifest_data = repair_manifest(*originals[0]).model_dump(mode="json")
        manifest_data["targets"] = [
            repair_manifest(source, failed).targets[0].model_dump(mode="json")
            for source, failed in originals
        ]
        manifest = SubmissionRepairManifest.model_validate(manifest_data)
        authorize_repair(runtime, manifest)
        gateway["fail"] = False
        gateway["entered_after"] = 2

        async def prepare() -> None:
            gateway["entered"] = asyncio.Event()
            gateway["release"] = asyncio.Event()

        client.portal.call(prepare)
        request_ids = client.portal.call(append_repair, runtime, manifest)

        async def entered() -> None:
            await asyncio.wait_for(gateway["entered"].wait(), 5)

        try:
            client.portal.call(entered)
            assert gateway["active"] == gateway["max_active"] == 2
            assert len(gateway["calls"]) == 5
            audit = client.portal.call(source_audit, runtime)
            assert audit["state"] == "degraded"
            assert audit["submission_recovery"]["counts"]["pending"] == 3
        finally:
            client.portal.call(gateway["release"].set)
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:recovery", request_ids[-1]
        )
        assert gateway["max_active"] == 2
        assert len(gateway["calls"]) == 6
        audit = client.portal.call(source_audit, runtime)
        assert audit["state"] == "healthy", audit
        assert audit["submission_recovery"]["counts"]["succeeded"] == 3
        assert audit["submission_recovery"]["counts"]["pending"] == 0
