"""Restore an authentic predecessor offset after a real downgrade replay conflict."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.simulations.source_pipeline_stress import _wait_for_composition_quiescence
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.deployment_budget import deployment_clock
from bootstrap.secrets import secret_store
from bootstrap.upgrade import UpgradeManager
from tests.acceptance.test_triage_rollback_compatibility import (
    PREDECESSOR,
    _archive_predecessor,
)


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-checkpoint-owner"  # fixture:tenant_data:ok
SUBSCRIBER = "pipeline:pipeline:noise_filtering"


_OLD_WORKER = r"""
import asyncio
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
import adminme.events.log as log_module
import adminme.runtime.application as application
import bootstrap.wizard as wizard_module
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store

root, deployment, result_path = map(Path, sys.argv[1:4])
mode = sys.argv[4]
assert root.name == "890a0b5b5a86650806fcda54ac319f00a0d636cb"
assert deployment.name == "checkpoint-recovery-lab"
for module in (application, log_module, wizard_module):
    assert Path(module.__file__).resolve().is_relative_to(root.resolve())
paths = DeploymentPaths.from_root(deployment)
if mode == "initialize":
    wizard_module.BootstrapWizard(paths, root=root, test_mode=True, dry_run=True,
        fixture="two-adult-one-child", preflight_overrides={
            "system": "Darwin", "machine": "arm64", "macos_version": "26.0"
        }).run()
    result_path.write_text(json.dumps({"imports_from_archive": True}))
    raise SystemExit(0)

references = json.loads((paths.instance_dir / "config/secret-references.json").read_text())["references"]
key = secret_store(paths, test_mode=True).get(references["event_log"])
member = "fixture-checkpoint-owner"
settings = application.RuntimeSettings.credential_free(
    paths.instance_dir, encryption_key=key, repository_root=root,
    member_identities={"checkpoint-owner.ts.net": member},
    member_roles={member: "principal"}, deterministic_openclaw_fixture=True,
    fixture_member_id=member,
)
assert not settings.openclaw_enabled and settings.openclaw_gateway_token is None

async def wait(runtime):
    deadline = asyncio.get_running_loop().time() + 60
    stable, prior = 0, None
    while asyncio.get_running_loop().time() < deadline:
        statuses = {name: await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)}
        noise = statuses["pipeline:pipeline:noise_filtering"]
        assert noise["consumer_version"] == "22.0.0"
        others = [row for name, row in statuses.items()
            if name != "pipeline:pipeline:noise_filtering"]
        assert not any(row["degraded"] for row in others), statuses
        if mode == "poison":
            if noise["degraded"] and all(row["lag_count"] == 0 for row in others):
                assert noise["consecutive_failures"] == 5
                assert noise["poison_event_id"] == sys.argv[5]
                return noise
        else:
            assert not noise["degraded"], statuses
            latest = await runtime.event_log.latest_event_id()
            stable = stable + 1 if latest == prior and all(
                row["lag_count"] == 0 for row in statuses.values()) else 0
            prior = latest
            if stable >= 2:
                return noise
        await asyncio.sleep(0.01)
    raise AssertionError("archived predecessor did not reach expected terminal state")

async def history(runtime):
    return [event async for event in runtime.event_log.read_since()]

with TestClient(application.create_application(settings)) as client:
    runtime = client.app.state.runtime
    noise = client.portal.call(wait, runtime)
    events = client.portal.call(history, runtime)
    assert not any(event["type"] in {"external_sync.write_requested", "external.sent"}
        for event in events)
    if mode != "poison":
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            assurance = client.get("/api/system/ready").json()["capabilities"]["policy:source-assurance"]
            details = assurance["details"]
            counts = details["counts"]
            if (assurance["state"] == "healthy"
                    and counts["failed"] == counts["pending"] == counts["missing"] == 0
                    and details["freshness"]["revision_current"]):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("predecessor source assurance did not become healthy")
        assert details["provider_effects"]["zero_effect_state"] == "healthy"
        response = client.get("/api/member/v1/surfaces/triage",
            headers={"Tailscale-User-Login": "checkpoint-owner.ts.net"},
            params={"member_id": member})
        assert response.status_code == 200, response.text
    result = {"imports_from_archive": True, "noise": noise,
        "source_health": "degraded" if mode == "poison" else "healthy",
        "external_write_requests": 0}
result_path.write_text(json.dumps(result))
"""


def _old(archive: Path, paths: DeploymentPaths, mode: str, poison: str = ""):
    result_path = paths.root.parent / f"predecessor-{mode}.json"
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ADMINME_", "OPENCLAW_", "PYTHON"))
    }
    environment.update(
        ADMINME_TEST_MODE="1",
        ADMINME_REVIEWED_COMMIT=PREDECESSOR,
        ADMINME_REPOSITORY_ROOT=str(archive),
        PYTHONPATH=os.pathsep.join((str(archive), str(archive / "bridge"))),
        PYTHONNOUSERSITE="1",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _OLD_WORKER,
            str(archive),
            str(paths.root),
            str(result_path),
            mode,
            poison,
        ],
        cwd=archive,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "poison":
        assert "callback raised IdempotencyConflict" in result.stderr, result.stderr
    return json.loads(result_path.read_text())


def _settings(paths: DeploymentPaths) -> RuntimeSettings:
    references = json.loads(
        (paths.instance_dir / "config/secret-references.json").read_text()
    )["references"]
    return RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=secret_store(paths, test_mode=True).get(references["event_log"]),
        repository_root=ROOT,
        member_identities={"checkpoint-owner.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
    )


async def _source(
    settings: RuntimeSettings, name: str, *, thread: str, event_at_ms: int | None = None
) -> str:
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        return await log.append(
            EventEnvelope(
                event_at_ms=event_at_ms or time.time_ns() // 1_000_000,
                tenant_id=settings.instance.tenant_id,
                type="messaging.received",
                schema_version=3,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="fixture-checkpoint-source",
                source_account_id="fixture-checkpoint-account",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                actor_identity=MEMBER,
                sensitivity="normal",
                idempotency_key=f"fixture-checkpoint-message:{name}",
                payload={
                    "member_id": MEMBER,
                    "source_channel": "sms",
                    "provider": "fixture",
                    "provider_account_id": "fixture-checkpoint-account",
                    "workspace": "personal",
                    "native_message_id": f"fixture-message-{name}",
                    "thread_id": thread,
                    "from_identifier": "sender@example.invalid",
                    "to_identifier": "recipient@example.invalid",
                    "subject": "Synthetic neutral thread",
                    "body_text": f"Synthetic informational update {name}; nothing is requested.",
                    "received_at": EventEnvelope.now_utc_iso(),
                    "attachments": [],
                },
            )
        )
    finally:
        await log.close()


async def _history(settings: RuntimeSettings) -> list[dict[str, Any]]:
    log = EventLog(settings.instance, settings.encryption_key, read_only=True)
    try:
        return [event async for event in log.read_since()]
    finally:
        await log.close()


def _offsets(paths: DeploymentPaths) -> dict[str, dict[str, Any]]:
    path = paths.instance_dir / "events/bus_checkpoints.db"
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {
            row["subscriber_id"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM bus_consumer_offsets ORDER BY subscriber_id"
            )
        }
    finally:
        connection.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("mutation", [None, "prefix", "tail"])
def test_captured_predecessor_offset_recovers_real_downgrade_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "checkpoint-recovery-lab")
    paths.root.mkdir(mode=0o700)
    releases = paths.app_dir / "releases"
    releases.mkdir(mode=0o700, parents=True)
    paths.app_dir.chmod(0o700)
    archive = releases / PREDECESSOR
    _archive_predecessor(tmp_path).rename(archive)
    _old(archive, paths, "initialize")
    pointer = paths.app_dir / "current-release.json"
    pointer.write_text(json.dumps({"commit": PREDECESSOR, "path": str(archive)}))
    pointer.chmod(0o600)
    settings = _settings(paths)
    first = asyncio.run(_source(settings, "A", thread="fixture-shared-thread"))
    _old(archive, paths, "healthy")
    first_assessment = next(
        event
        for event in asyncio.run(_history(settings))
        if event["idempotency_key"] == f"triage:message-features:v22:{first}"
    )
    assert first_assessment["payload"]["assessment_outcome"] == "no_candidate"
    assert (
        "language_assessment_completed" in first_assessment["payload"]["reason_codes"]
    )
    second = asyncio.run(_source(settings, "B", thread="fixture-shared-thread"))
    captured = _old(archive, paths, "healthy")["noise"]
    assert captured["checkpoint_event_id"] >= second
    captured_row = _offsets(paths)[SUBSCRIBER]
    prefix = asyncio.run(_history(settings))

    backups = BackupManager(paths, root=ROOT, test_mode=True)
    artifact = backups.create()
    assert artifact["encrypted"] is True and artifact["services_restarted"] is False
    receipt_path = paths.backup_dir / "captured-predecessor-rehearsal.json"
    rehearsal = backups.rehearse(
        Path(artifact["path"]), candidate=ROOT, receipt=receipt_path, timeout_s=120
    )
    assert rehearsal["status"] == "healthy" and rehearsal["source_events_appended"] == 0
    assert _offsets(paths)[SUBSCRIBER] == captured_row
    assert asyncio.run(_history(settings)) == prefix

    unseen = asyncio.run(_source(settings, "C", thread="fixture-post-backup-thread"))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        status = client.portal.call(runtime.bus.subscriber_status, SUBSCRIBER)
        assert status["consumer_version"] == "23.0.0"
    before_poison = asyncio.run(_history(settings))
    first_current = next(
        event
        for event in before_poison
        if event["idempotency_key"] == f"triage:message-features:v23:{first}"
    )
    assert first_current["payload"]["extractor_id"] == "thread-anchor-deferred-v1"
    poisoned = _old(archive, paths, "poison", first)
    assert poisoned["noise"]["poison_event_id"] == first
    retained = asyncio.run(_history(settings))
    assert retained[: len(prefix)] == prefix
    current_rows = _offsets(paths)
    assert current_rows[SUBSCRIBER]["degraded"] == 1

    # The restoration API below consumes the authenticated archive and exact
    # rehearsal receipt, never a caller-provided or synthesized offset.
    from bootstrap import predecessor_checkpoint_recovery as recovery_module

    manager = UpgradeManager(paths, root=ROOT, test_mode=True)
    manager._settlement_deadline = deployment_clock() + 300
    journal = {
        "transaction_id": "c" * 32,
        "phase": "recovering",
        "operation": "activate",
        "candidate_commit": subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "predecessor_commit": PREDECESSOR,
        "predecessor_path": str(archive),
        "initial_pointer_sha256": _digest(pointer),
        "fresh_backup": {
            "path": artifact["path"],
            "artifact_sha256": _digest(Path(artifact["path"])),
            "manifest_sha256": artifact["manifest_sha256"],
            "encrypted": True,
            "rehearsal_receipt_path": str(receipt_path),
            "rehearsal_receipt_sha256": _digest(receipt_path),
            "rehearsal_self_sha256": rehearsal["receipt_sha256"],
            "content_exposed": False,
        },
    }
    if mutation == "prefix":
        # Insert a distinct earlier-ID fact into this disposable stream. No old
        # fact is edited, but the captured prefix no longer has exact identity.
        first_source = next(event for event in retained if event["event_id"] == first)
        inserted = asyncio.run(
            _source(
                settings,
                "D",
                thread="fixture-prefix-insertion",
                event_at_ms=first_source["event_at_ms"] - 1000,
            )
        )
        assert inserted < captured_row["checkpoint_event_id"]
    elif mutation == "tail":
        original_proof = recovery_module._supervised_proof

        def append_after_real_proof(*args, **kwargs):
            proof = original_proof(*args, **kwargs)
            appended = asyncio.run(_source(settings, "D", thread="fixture-proof-race"))
            assert appended > proof["current_watermark"]
            return proof

        monkeypatch.setattr(
            recovery_module, "_supervised_proof", append_after_real_proof
        )

    if mutation is not None:
        expected = (
            "checkpoint_prefix_proof_failed"
            if mutation == "prefix"
            else "checkpoint_event_log_changed_during_proof"
        )
        with pytest.raises(
            recovery_module.PredecessorCheckpointRecoveryError, match=expected
        ):
            recovery_module.recover_predecessor_checkpoint(manager, journal)
        assert _offsets(paths) == current_rows
        unchanged = {
            event["event_id"]: event for event in asyncio.run(_history(settings))
        }
        assert all(unchanged[event["event_id"]] == event for event in retained)
        return

    recovery = recovery_module.recover_predecessor_checkpoint(manager, journal)
    assert recovery is not None and recovery["status"] == "restored"
    restored = _offsets(paths)
    assert restored[SUBSCRIBER] == captured_row
    assert {key: value for key, value in restored.items() if key != SUBSCRIBER} == {
        key: value for key, value in current_rows.items() if key != SUBSCRIBER
    }
    assert asyncio.run(_history(settings)) == retained
    repeated = recovery_module.recover_predecessor_checkpoint(manager, journal)
    assert repeated is not None and repeated["status"] == "already_restored"
    assert _offsets(paths) == restored
    assert asyncio.run(_history(settings)) == retained
    assert _old(archive, paths, "healthy")["source_health"] == "healthy"
    final = asyncio.run(_history(settings))
    assert final[: len(retained)] == retained
    assert (
        next(
            event
            for event in final
            if event["idempotency_key"] == f"triage:message-features:v22:{unseen}"
        )["payload"]["assessment_outcome"]
        == "no_candidate"
    )
    assert (
        next(
            event
            for event in final
            if event["event_id"] == first_assessment["event_id"]
        )
        == first_assessment
    )
    assert not any(
        event["type"] in {"external_sync.write_requested", "external.sent"}
        for event in final
    )
