"""Exact maintenance repairs retained encrypted state for the real predecessor."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.skill_runner.fixture import build_fixture_runtime
from adminme.runtime.application import RuntimeSettings
import adminme.runtime.source_contract_recovery as maintenance
from adminme.source_contract_recovery import (
    ASSESSMENT_CONTRACT,
    CONTRACT_ATTEMPT_TYPE,
    CONTRACT_REQUEST_TYPE,
    CONTRACT_RESULT_TYPE,
    SourceAssessmentContractRecoveryBatch,
    contract_recovery_terminal,
)
from tests.acceptance.test_triage_rollback_compatibility import _archive_predecessor
from tests.fixtures.source_contract_recovery import seed_lineage, target_payload


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-contract-owner"  # fixture:tenant_data:ok
OTHER = "fixture-contract-other"  # fixture:tenant_data:ok
KEY = b"c" * 32


# Executed from the exact Git archive, never imported against current packs.
# All module origins and all data paths are checked before opening the runtime.
_PREDECESSOR_WORKER = r"""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
import adminme.events.log as log_module
import adminme.projections.triage.handlers as triage_module
import adminme.runtime.application as application


async def settle(runtime):
    deadline = asyncio.get_running_loop().time() + 60
    prior, stable = None, 0
    while asyncio.get_running_loop().time() < deadline:
        statuses = {
            name: await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)
        }
        assert not any(row["degraded"] for row in statuses.values()), statuses
        latest = await runtime.event_log.latest_event_id()
        stable = stable + 1 if latest == prior and all(
            row["lag_count"] == 0 for row in statuses.values()
        ) else 0
        prior = latest
        if stable >= 2:
            return statuses
        await asyncio.sleep(0.01)
    raise AssertionError("predecessor consumers did not settle")


async def events(runtime):
    return [event async for event in runtime.event_log.read_since()]


def digest(event):
    return hashlib.sha256(json.dumps(
        event, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


root, instance, expected_path, result_path = map(Path, sys.argv[1:5])
assert root.name == "protected-predecessor"
assert instance.name == "synthetic-encrypted-state"
assert root.parent == instance.parent == expected_path.parent == result_path.parent
for module in (application, log_module, triage_module):
    assert Path(module.__file__).resolve().is_relative_to(root.resolve())
expected = json.loads(expected_path.read_text())
member, other = "fixture-contract-owner", "fixture-contract-other"
settings = application.RuntimeSettings.credential_free(
    instance,
    encryption_key=b"c" * 32,
    repository_root=root,
    member_identities={"contract-owner.ts.net": member, "contract-other.ts.net": other},
    member_roles={member: "principal", other: "principal"},
    deterministic_openclaw_fixture=True,
    fixture_member_id=member,
)
assert settings.instance.instance_dir.resolve() == instance.resolve()
assert settings.openclaw_gateway_token is None and not settings.openclaw_enabled
with TestClient(application.create_application(settings)) as client:
    runtime = client.app.state.runtime
    statuses = client.portal.call(settle, runtime)
    noise = statuses["pipeline:pipeline:noise_filtering"]
    assert noise["consumer_version"] == "22.0.0", noise
    assert noise["checkpoint_event_id"] >= expected["source_event_id"]
    retained = client.portal.call(events, runtime)
    by_id = {event["event_id"]: event for event in retained}
    for event_id, value in expected["manifest"].items():
        assert digest(by_id[event_id]) == value
    assessments = [event for event in retained
        if event["type"] == "triage.intent.features_extracted"
        and event["payload"].get("source_event_id") == expected["source_event_id"]]
    assert assessments[-1]["event_id"] == expected["assessment_event_id"]
    assert not any(event["type"] in {
        "external_sync.write_requested", "external.sent"
    } for event in retained)
    # No new old-contract language call is permitted on either retained restart.
    assert {event["event_id"] for event in retained if event["type"] in {
        "skill.call.failed", "skill.call.recorded"
    }} == set(expected["skill_event_ids"])
    if expected["proposal_ids"]:
        conn = runtime.projections.connection("triage")
        for proposal_id in expected["proposal_ids"]:
            row = conn.execute(
                "SELECT * FROM triage_items WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            assert row is not None and row["owner_member_id"] == member
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        ready = client.get("/api/system/ready")
        assurance = ready.json()["capabilities"]["policy:source-assurance"]
        details = assurance["details"]
        counts = details["counts"]
        if (counts["failed"] == expected["failed"]
                and counts["pending"] == counts["missing"] == 0
                and details["freshness"]["revision_current"]):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("predecessor assurance did not converge: " + str(assurance))
    assert assurance["state"] == ("degraded" if expected["failed"] else "healthy")
    assert details["provider_effects"]["zero_effect_state"] == "healthy"
    auth = {"Tailscale-User-Login": "contract-owner.ts.net"}
    other_auth = {"Tailscale-User-Login": "contract-other.ts.net"}
    member_page = client.get("/api/member/v1/surfaces/triage", headers=auth,
        params={"member_id": member})
    assert member_page.status_code == 200, member_page.text
    evidence_url = "/api/member/v1/evidence/" + expected["source_event_id"]
    assert client.get(evidence_url, headers=auth).status_code == 200
    assert client.get(evidence_url, headers=other_auth).status_code == 404
    assert client.portal.call(runtime.observation.is_active)
    result = {
        "imports_from_archive": True,
        "consumer_version": noise["consumer_version"],
        "source_health": assurance["state"],
        "history_preserved": True,
        "member_api_status": member_page.status_code,
        "private_evidence_statuses": [200, 404],
        "external_write_requests": 0,
    }
result_path.write_text(json.dumps(result))
"""


def _manifest(events: list[dict[str, Any]]) -> dict[str, str]:
    return {
        event["event_id"]: hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for event in events
    }


async def _retained(settings: RuntimeSettings) -> list[dict[str, Any]]:
    log = EventLog(settings.instance, settings.encryption_key, read_only=True)
    try:
        return [event async for event in log.read_since()]
    finally:
        await log.close()


async def _terminal(settings: RuntimeSettings, request_id: str) -> dict[str, Any]:
    log = EventLog(settings.instance, settings.encryption_key, read_only=True)
    try:
        return await contract_recovery_terminal(log, request_id)
    finally:
        await log.close()


def _checkpoint_bytes(settings: RuntimeSettings) -> dict[str, bytes | None]:
    """No maintenance path may open or replace any active checkpoint sidecar."""
    return {
        str(path): path.read_bytes() if path.exists() else None
        for base in (
            settings.instance.bus_checkpoint_path,
            settings.instance.projection_checkpoint_path,
            settings.instance.adapter_cursor_path,
        )
        for suffix in ("", "-wal", "-shm")
        for path in (Path(str(base) + suffix),)
    }


def _run_predecessor(
    archive: Path,
    settings: RuntimeSettings,
    lineage: dict[str, str],
    *,
    assessment_id: str,
    failed: int,
    retained: list[dict[str, Any]],
) -> dict[str, Any]:
    instance = settings.instance.instance_dir
    expected_path = instance.parent / "predecessor-expectation.json"
    result_path = instance.parent / "predecessor-result.json"
    expected_path.write_text(
        json.dumps(
            {
                "source_event_id": lineage["source_event_id"],
                "assessment_event_id": assessment_id,
                "failed": failed,
                "manifest": _manifest(retained),
                "skill_event_ids": [
                    event["event_id"]
                    for event in retained
                    if event["type"] in {"skill.call.recorded", "skill.call.failed"}
                ],
                "proposal_ids": [
                    event["payload"]["proposal_id"]
                    for event in retained
                    if event["type"] == "triage.item.proposed"
                ],
            }
        )
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ADMINME_", "OPENCLAW_", "PYTHON"))
    }
    environment["PYTHONPATH"] = os.pathsep.join((str(archive), str(archive / "bridge")))
    environment["PYTHONNOUSERSITE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _PREDECESSOR_WORKER,
            str(archive),
            str(instance),
            str(expected_path),
            str(result_path),
        ],
        cwd=archive,
        env=environment,
        capture_output=True,
        text=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result_path.read_text())


class _CommittedMaintenanceInterruption(BaseException):
    """Interrupt after a durable receipt without entering a failure fallback."""


@pytest.mark.parametrize("crash_after_receipt", [False, True])
def test_exact_maintenance_and_publication_resume_converge_on_actual_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_after_receipt: bool
) -> None:
    archive = _archive_predecessor(tmp_path)
    settings = RuntimeSettings.credential_free(
        tmp_path / "synthetic-encrypted-state",
        encryption_key=KEY,
        repository_root=ROOT,
        member_identities={
            "contract-owner.ts.net": MEMBER,
            "contract-other.ts.net": OTHER,
        },
        member_roles={MEMBER: "principal", OTHER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
    )
    lineage = asyncio.run(seed_lineage(settings))
    initial = asyncio.run(_retained(settings))
    # Establish the real old consumer version/checkpoint through its own app.
    # Maintenance must not borrow a current-runtime startup or edit that offset.
    baseline = _run_predecessor(
        archive,
        settings,
        lineage,
        assessment_id=lineage["latest_assessment_event_id"],
        failed=1,
        retained=initial,
    )
    assert baseline["source_health"] == "degraded"
    before = asyncio.run(_retained(settings))
    checkpoints = _checkpoint_bytes(settings)
    assert checkpoints[str(settings.instance.bus_checkpoint_path)] is not None
    operation = tmp_path / "private-maintenance-operation"
    operation.mkdir(mode=0o700)
    batch = SourceAssessmentContractRecoveryBatch.model_validate(
        {
            "request_id": "fixture-exact-predecessor-contract-recovery",
            "required_assessment_contract": ASSESSMENT_CONTRACT,
            "targets": [target_payload(lineage)],
        }
    )
    calls: list[int] = []

    def fixture_runtime(*, event_sink, event_log, raw_events_dir, gateway_token):
        assert gateway_token is None
        runtime = build_fixture_runtime(
            event_sink=event_sink, event_log=event_log, raw_events_dir=raw_events_dir
        )
        delegate = runtime.httpx_client_factory()

        class Client:
            async def post(self, url, *, json, headers):
                calls.append(json["args"]["timeoutMs"])
                return await delegate.post(url, json=json, headers=headers)

        return replace(runtime, httpx_client_factory=Client)

    monkeypatch.setattr(maintenance, "build_skill_runtime", fixture_runtime)

    def execute() -> dict[str, Any]:
        return asyncio.run(
            maintenance.run_source_contract_recovery(
                instance_dir=settings.instance.instance_dir,
                repository_root=ROOT,
                operation_dir=operation,
                encryption_key=KEY,
                gateway_token=None,
                batch=batch,
                requested_by_actor="fixture-reviewed-release-operator",
            )
        )

    if crash_after_receipt:
        original_write = EventWriter.write_batch

        async def interrupt_after_commit(self, envelopes, **kwargs):
            result = await original_write(self, envelopes, **kwargs)
            types = {envelope.type for envelope in envelopes}
            if {CONTRACT_RESULT_TYPE, "triage.source_publication.planned"} <= types:
                raise _CommittedMaintenanceInterruption()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(EventWriter, "write_batch", interrupt_after_commit)
            with pytest.raises(_CommittedMaintenanceInterruption):
                execute()
        interrupted = asyncio.run(_retained(settings))
        [request] = [
            event for event in interrupted if event["type"] == CONTRACT_REQUEST_TYPE
        ]
        terminal = asyncio.run(_terminal(settings, request["event_id"]))
        assert terminal["status"] == "completed"
        assert terminal["publication_complete"] is False
        assert (
            len(
                [
                    event
                    for event in interrupted
                    if event["type"] == CONTRACT_RESULT_TYPE
                ]
            )
            == 1
        )
        assert not any(
            event["type"]
            in {"triage.item.proposed", "triage.source_publication.completed"}
            for event in interrupted
        )
        assert _checkpoint_bytes(settings) == checkpoints

    result = execute()
    assert result["status"] == "completed"
    assert result["completed_assessment_contract"] == ASSESSMENT_CONTRACT
    assert result["publication_complete"] is True
    assert calls == [120_000]
    assert _checkpoint_bytes(settings) == checkpoints
    repaired = asyncio.run(_retained(settings))
    repaired_manifest = _manifest(repaired)
    assert {
        event_id: repaired_manifest[event_id] for event_id in _manifest(before)
    } == _manifest(before)
    for event_type in (
        CONTRACT_REQUEST_TYPE,
        CONTRACT_ATTEMPT_TYPE,
        CONTRACT_RESULT_TYPE,
        "triage.source_publication.planned",
        "triage.source_publication.completed",
    ):
        assert len([event for event in repaired if event["type"] == event_type]) == 1
    assessment = next(
        event
        for event in repaired
        if event["event_id"] == result["assessment_event_id"]
    )
    assert assessment["schema_version"] == 4
    assert (
        assessment["payload"]["supersedes_assessment_event_id"]
        == lineage["latest_assessment_event_id"]
    )
    proposals = [event for event in repaired if event["type"] == "triage.item.proposed"]
    assert proposals and all(event["schema_version"] == 5 for event in proposals)
    assert all(
        event["payload"]["extraction_event_id"] == assessment["event_id"]
        for event in proposals
    )
    assert not any(
        event["type"] in {"external_sync.write_requested", "external.sent"}
        for event in repaired
    )

    # An explicit repeat is idempotent before the actual predecessor starts.
    assert execute() == result
    assert calls == [120_000]
    assert asyncio.run(_retained(settings)) == repaired
    assert _checkpoint_bytes(settings) == checkpoints
    predecessor = _run_predecessor(
        archive,
        settings,
        lineage,
        assessment_id=assessment["event_id"],
        failed=0,
        retained=repaired,
    )
    assert predecessor == {
        "imports_from_archive": True,
        "consumer_version": "22.0.0",
        "source_health": "healthy",
        "history_preserved": True,
        "member_api_status": 200,
        "private_evidence_statuses": [200, 404],
        "external_write_requests": 0,
    }
