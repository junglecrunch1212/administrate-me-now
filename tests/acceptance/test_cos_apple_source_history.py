"""Recovered source -> durable receipt -> real HTR-10 and predecessor replay."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.base import AdapterContext
from adminme.cos_apple_source_history import EVENT_TYPE
from adminme.runtime.application import create_application
from bootstrap import cos_apple_source_history as history
from bootstrap.candidate_readiness import CandidateReadinessError, build_htr10_review
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.cos_apple_reminders import prepare_configuration
from bootstrap.cos_channel_activation import _payload, prepare_configuration as activate
from bootstrap.htr10_export import export_htr10_audits
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.acceptance.test_cos_identity_delegation_kernel import A, _settings
from tests.acceptance.test_cos_service_working_context import _context, _mail_policy
from tests.unit.bootstrap import test_cos_apple_reminders as acquisition

ROOT = Path(__file__).resolve().parents[2]
PREDECESSOR = "3296b8d8be4424d408756d9b544d03b7445dcab4"


def _write(path: Path, value: Any) -> bytes:
    data = _payload(value)
    atomic_write(path, data)
    return data


@pytest.fixture
def retained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    original_selection = acquisition._selection()
    ownership = tmp_path / "original-ownership.json"
    evidence = _write(ownership, {"reviewed_source": "fixture-source", "kind": "local"})
    selection = original_selection.model_copy(
        update={
            "source_ownership_evidence_sha256": hashlib.sha256(evidence).hexdigest()
        }
    )
    monkeypatch.setattr(acquisition, "_selection", lambda: selection)
    settings = _settings(paths.instance_dir)
    config = paths.instance_dir / "config"
    reference = secret_store(paths, test_mode=True).put(
        "event-log", settings.encryption_key
    )
    now = datetime.now(timezone.utc)
    window = "2026-01-01T00:00:00Z"
    for name, value in (
        ("adapters.json", {"adapters": []}),
        ("member-mapping.json", {"members": [{"member_id": A, "role": "principal"}]}),
        ("secret-references.json", {"references": {"event_log": reference}}),
        (
            "deployment-readiness.json",
            {
                "contract": "deployment-readiness-v1",
                "provider_effect_window": {"started_at": window},
            },
        ),
    ):
        _write(config / name, value)
    prior = _configuration(tmp_path / "prior")
    requested = prepare_configuration(prior, selection)
    original = {
        "adapter_config": requested,
        "sender_proofs": [
            {
                "channel": "email",
                "provider_account_sha256": "f" * 64,
                "sender_evidence_sha256": "e" * 64,
                "transport_evidence_sha256": "d" * 64,
                "observed_at": now.isoformat(),
            }
        ],
        "owner_member_id": A,
        "operator_principal_id": "operator:fixture-recovery",
        "owner_authorization_sha256": selection.owner_authorization_sha256,
    }
    original_path = tmp_path / "original-activation.json"
    original_bytes = _write(original_path, original)
    prepared = activate(prior, requested, enable_live=False)
    activation_id = "a" * 32
    journal_path = (
        paths.app_dir / "cos-channel-operation/history" / f"{activation_id}.json"
    )
    journal = {
        "contract": "cos-channel-operation-journal-v1",
        "phase": "recovered",
        "activation_id": activation_id,
        "release_commit": PREDECESSOR,
        "enabled_live": False,
        "owner_member_id": A,
        "operator_principal_id": original["operator_principal_id"],
        "owner_authorization_sha256": selection.owner_authorization_sha256,
        "initial_files": {"adapters.json": base64.b64encode(_payload(prior)).decode()},
        "prepared_adapter_configuration_sha256": hashlib.sha256(
            _payload(prepared)
        ).hexdigest(),
    }
    journal_bytes = _write(journal_path, journal)
    # Only host/source selection seams are faked. Controller validation,
    # original activation preparation, provider binding, event sink and audits
    # operate on their genuine production structures.
    monkeypatch.setattr(history, "reviewed_candidate_commit", lambda _root: "b" * 40)
    monkeypatch.setattr(
        history, "_pointer_snapshot", lambda _paths: (PREDECESSOR, "", "")
    )
    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
        reader = acquisition.Reader()
        reader.snapshot = replace(reader.snapshot, items=())
        adapter = acquisition._adapter(tmp_path, reader)
        ctx = AdapterContext(
            event_sink=runtime.events,
            config={},
            correlation_id_factory=lambda: "retained-source-fixture",
            now_factory=lambda: datetime.now(timezone.utc),
            cursor_store=runtime.adapter_cursor_store,
            source_events=runtime.event_log,
        )

        async def start():
            await acquisition._start(adapter, ctx, runtime.event_log)
            await adapter.stop()
            facts = [row async for row in runtime.event_log.read_since()]
            source = next(row for row in facts if row["type"] == "list.created")
            auth = next(
                row
                for row in facts
                if row["type"] == "adapter.authenticated" and row["schema_version"] == 3
            )
            await runtime.wait_for_projection("lists", source["event_id"])
            return source, auth

        source, auth = client.portal.call(start)
        request = history.HistoryRepairRequest(
            contract="cos-apple-source-history-repair-v1",
            controller_commit="b" * 40,
            expected_active_commit=PREDECESSOR,
            source_event_id=source["event_id"],
            authentication_event_id=auth["event_id"],
            activation_id=activation_id,
            activation_journal_sha256=hashlib.sha256(journal_bytes).hexdigest(),
            activation_request_sha256=hashlib.sha256(original_bytes).hexdigest(),
            recovery_authorization_sha256="e" * 64,
        )
        monkeypatch.setattr(
            ServiceManager,
            "runtime_diagnostics",
            lambda _self: client.get("/api/system/ready").json(),
        )
        yield dict(
            paths=paths,
            client=client,
            runtime=runtime,
            source=source,
            auth=auth,
            request=request,
            original_path=original_path,
            ownership=ownership,
            journal_path=journal_path,
            settings=settings,
            reader=reader,
        )


def _repair(fixture: dict[str, Any], *, append: bool = True):
    return asyncio.run(
        history.reconcile(
            fixture["paths"],
            repository_root=ROOT,
            request=fixture["request"],
            activation_request=fixture["original_path"],
            ownership_evidence=fixture["ownership"],
            append=append,
            test_mode=True,
        )
    )


def _export(fixture: dict[str, Any], name: str):
    paths = fixture["paths"]
    historical = paths.backup_dir / f"{name}-historical.json"
    source = paths.backup_dir / f"{name}-source.json"
    result = asyncio.run(
        export_htr10_audits(
            paths,
            repository_root=ROOT,
            historical_output=historical,
            source_output=source,
            ownership_reviewed=True,
            test_mode=True,
        )
    )
    return result, historical, source


def _review(fixture: dict[str, Any], historical: Path, source: Path):
    return build_htr10_review(
        fixture["paths"],
        historical_attribution=historical,
        source_assurance=source,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def test_recovered_list_is_attributed_without_access_and_receipt_survives_replay(
    retained, monkeypatch, tmp_path
):
    client, runtime = retained["client"], retained["runtime"]
    before_config = {
        p: p.read_bytes()
        for p in (retained["paths"].instance_dir / "config").iterdir()
        if p.is_file()
    }
    assert runtime.settings.provider_bindings == ()
    result, historical, source = _export(retained, "before")
    assert result["historical_state"] == "degraded"
    assert json.loads(historical.read_bytes())["unattributed_event_count"] == 1
    with pytest.raises(CandidateReadinessError):
        _review(retained, historical, source)
    assert _repair(retained, append=False)["status"] == "validated_not_recorded"
    original_source = copy.deepcopy(retained["source"])
    original_auth = copy.deepcopy(retained["auth"])
    assert _repair(retained)["status"] == "reconciled"
    assert _repair(retained)["status"] == "already_reconciled"
    assert (
        client.portal.call(runtime.event_log.get, original_source["event_id"])
        == original_source
    )
    assert (
        client.portal.call(runtime.event_log.get, original_auth["event_id"])
        == original_auth
    )
    assert all(p.read_bytes() == value for p, value in before_config.items())
    assert retained["reader"].calls == 1
    # Present access policy stays authoritative after historical attribution.
    policy = _mail_policy(client, original_source["event_id"], monkeypatch, "none")
    assert policy["context_mode"] == "none"
    assert (
        _context(
            client,
            "historical-no-access",
            source_event_ids=[original_source["event_id"]],
        ).status_code
        == 404
    )
    assert runtime.settings.provider_bindings == ()
    result, historical, source = _export(retained, "after")
    assert result["historical_state"] == result["source_state"] == "healthy"
    _review(retained, historical, source)
    report = historical.read_text()
    assert "retained_cos_apple_source_reconciled" in report
    for private in (
        "Fixture list",
        "fixture-source",
        "fixture-list",
        original_source["source_account_id"],
    ):
        assert private not in report
    # Remove external artifacts: the encrypted receipt is sufficient forever.
    retained["original_path"].unlink()
    retained["ownership"].unlink()
    retained["journal_path"].unlink()
    client.portal.call(runtime.projections.rebuild, "lists")
    result, historical, source = _export(retained, "replayed")
    assert result["historical_state"] == "healthy"
    _review(retained, historical, source)
    assert runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize(
    "defect",
    [
        "journal_hash",
        "request_hash",
        "ownership",
        "prepared_hash",
        "owner",
        "original_authority",
        "active_release",
        "active_operation",
    ],
)
def test_reconciliation_refuses_missing_or_changed_original_authority(retained, defect):
    request = retained["request"]
    if defect in {"journal_hash", "request_hash", "active_release"}:
        field = {
            "journal_hash": "activation_journal_sha256",
            "request_hash": "activation_request_sha256",
            "active_release": "expected_active_commit",
        }[defect]
        retained["request"] = request.model_copy(
            update={field: "f" * (40 if defect == "active_release" else 64)}
        )
    elif defect == "ownership":
        _write(retained["ownership"], {"reviewed_source": "another-source"})
    elif defect == "active_operation":
        _write(retained["paths"].app_dir / "cos-channel-operation/active.json", {})
    else:
        journal = json.loads(retained["journal_path"].read_bytes())
        if defect == "prepared_hash":
            journal["prepared_adapter_configuration_sha256"] = "f" * 64
        elif defect == "owner":
            journal["owner_member_id"] = "another-member"
        else:
            journal["owner_authorization_sha256"] = "f" * 64
        data = _write(retained["journal_path"], journal)
        retained["request"] = request.model_copy(
            update={"activation_journal_sha256": hashlib.sha256(data).hexdigest()}
        )
    with pytest.raises(ValueError):
        _repair(retained)

    async def receipts():
        return [
            r
            async for r in retained["runtime"].event_log.read_since(types=[EVENT_TYPE])
        ]

    assert retained["client"].portal.call(receipts) == []
    result, historical, source = _export(retained, "rejected")
    assert result["historical_state"] == "degraded"
    with pytest.raises(CandidateReadinessError):
        _review(retained, historical, source)


def test_predecessor_runtime_reads_and_rebuilds_with_new_receipt(retained, tmp_path):
    assert _repair(retained)["status"] == "reconciled"
    # Export a complete consistent encrypted snapshot through SQLite's supported
    # backup API; then run the actual immutable predecessor, whose registry does
    # not know the new receipt type. It must retain and ignore that audit fact.
    from bootstrap.backup_restore import (
        BackupManager,
        _authenticate_backup,
        _extract_and_validate_payload,
    )

    paths = retained["paths"]
    retained["client"].portal.call(retained["runtime"].stop)
    manager = BackupManager(paths, root=ROOT, test_mode=True)
    entries, manifest = manager._inventory_archive()
    backup = paths.backup_dir / "history.ambackup"
    manager._write_streaming_backup(
        backup, entries=entries, manifest=manifest, backup_key=b"h" * 32
    )
    _, archive_payload, _ = _authenticate_backup(backup, b"h" * 32)
    restored = tmp_path / "restored"
    restored.mkdir(mode=0o700)
    _extract_and_validate_payload(archive_payload, restored)
    predecessor = tmp_path / "predecessor"
    predecessor.mkdir()
    archive = subprocess.run(
        ["git", "archive", PREDECESSOR], cwd=ROOT, check=True, capture_output=True
    ).stdout
    with tarfile.open(fileobj=BytesIO(archive)) as content:
        content.extractall(predecessor, filter="data")
    script = """
import asyncio, json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from adminme.events.registry import ensure_autoloaded, registry
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_identity_delegation_kernel import _settings
ensure_autoloaded()
assert registry.get("adapter.source_ownership_reconciled", 1) is None
app = create_application(_settings(Path(sys.argv[1])))
with TestClient(app) as client:
    runtime = app.state.runtime
    async def check():
        receipt = [r async for r in runtime.event_log.read_since(types=["adapter.source_ownership_reconciled"])]
        assert len(receipt) == 1
        source = await runtime.event_log.get(receipt[0]["payload"]["source_event_id"])
        assert source["type"] == "list.created"
        assert runtime.settings.provider_bindings == ()
        await runtime.projections.rebuild("lists")
        await runtime.projections.rebuild("tasks")
        assert runtime.fixture_adapter.delivered == []
        assert await runtime.event_log.get(receipt[0]["event_id"]) == receipt[0]
    client.portal.call(check)
print("predecessor-retained-receipt-and-replayed")
"""
    environment = {
        **os.environ,
        "PYTHONPATH": str(predecessor / "bridge"),
        "ADMINME_TEST_MODE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-c", script, str(restored / "state")],
        cwd=predecessor,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "predecessor-retained-receipt-and-replayed" in completed.stdout
