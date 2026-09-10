"""A coherent disposable reset before and after the real composition root.

Only Gmail/secret/service host boundaries use synthetic fixtures. Encrypted
events, derived state, backups, reset moves, startup and private routes are real.
Run only in hosted CI or a disposable lab.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.log import EventLog
from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap.backup_restore import BackupManager, _authenticate_backup_to_path
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.disposable_test_reset import (
    OfflineTestReset,
    build_manifest,
    manifest_digest,
)
from bootstrap.secrets import secret_store
from tests.acceptance.test_disposable_test_acquisition import (
    ACCOUNT,
    HEADERS,
    MEMBER,
    OTHER,
    OTHER_HEADERS,
    ROOT,
    _adapter,
    _config,
    _fake_provider,
    _policy,
    _settled_skill_receipts,
    _source_events,
)


KEY = b"s" * 32


def _json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2).encode())


def _settings_from_retained_configuration(paths: DeploymentPaths) -> RuntimeSettings:
    config = paths.instance_dir / "config"
    mapping = json.loads((config / "member-mapping.json").read_text())
    adapters = json.loads((config / "adapters.json").read_text())
    return RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=KEY,
        repository_root=ROOT,
        member_identities={
            row["tailscale_identity"]: row["member_id"] for row in mapping["members"]
        },
        member_roles={row["member_id"]: row["role"] for row in mapping["members"]},
        household_mapping=mapping,
        adapter_config=adapters,
        deterministic_openclaw_fixture=True,
        human_triage_management_enabled=True,
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


async def _all_events(runtime: Any) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since()]


def test_reset_first_start_preserves_identity_and_restores_original_encrypted_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    config = paths.instance_dir / "config"
    atomic_write(
        config / "instance.yaml",
        b"tenant_id: tenant-reset-fixture\ntimezone: Etc/UTC\n",
    )
    mapping = {
        "schema_version": 2,
        "household_label": "Synthetic reset household",
        "members": [
            {
                "member_id": MEMBER,
                "role": "principal",
                "label": "Fixture adult A",
                "tailscale_identity": "bounded-a.ts.net",
            },
            {
                "member_id": OTHER,
                "role": "principal",
                "label": "Fixture adult B",
                "tailscale_identity": "bounded-b.ts.net",
            },
        ],
        "memberships": [],
        "relationships": [],
        "parties": [],
    }
    _json(config / "member-mapping.json", mapping)
    adapter_config = _config(["selected-incoming"])
    _json(config / "adapters.json", adapter_config)
    _json(config / "observation.json", {"active": True})
    _json(paths.instance_dir / "bridges" / "member-mapping.json", {"members": []})
    _json(
        paths.instance_dir / "openclaw" / "runtime" / "openclaw.json",
        {"fixture": "retained-configuration"},
    )
    secrets = secret_store(paths, test_mode=True)
    references = {
        "event_log": secrets.put("event-log", KEY),
        "backup": secrets.put("backup", b"b" * 32),
    }
    _json(config / "secret-references.json", {"references": references})
    service = _fake_provider(monkeypatch, adapter_config)
    old_settings = _settings_from_retained_configuration(paths)
    old_app = create_application(old_settings)
    with TestClient(old_app) as client:
        old_runtime = old_app.state.runtime
        client.portal.call(_adapter, old_runtime)
        old_skill_receipts = client.portal.call(_settled_skill_receipts, old_runtime)
        old_sources = client.portal.call(_source_events, old_runtime)
        assert len(old_sources) == 1
        old_source = old_sources[0]
        assert old_source["payload"]["native_message_id"] == "selected-incoming"
        # Preferences are the explicitly retained operational control, while
        # source rules and decisions remain tied to disposable event truth.
        control = old_runtime.settings_control.read()
        old_runtime.settings_control.set_preferences(
            MEMBER, {"location_cues": True}, expected_revision=control["revision"]
        )
    assert service.get_ids == ["selected-incoming"]
    encrypted_path = old_settings.instance.event_log_path
    assert not encrypted_path.read_bytes().startswith(b"SQLite format 3")
    assert b"Informational fixture only" not in encrypted_path.read_bytes()

    _json(
        paths.app_dir / "current-release.json",
        {
            "commit": "a" * 40,
            "path": str(paths.app_dir / "releases" / ("a" * 40)),
        },
    )
    _json(
        paths.app_dir / "activation" / "active.json",
        {
            "transaction_id": "b" * 32,
            "phase": "recovering",
            "failure": "synthetic unresolved historical assessment",
        },
    )
    original_journal = (paths.app_dir / "activation" / "active.json").read_bytes()
    original_pointer = (paths.app_dir / "current-release.json").read_bytes()
    backup = paths.backup_dir / "retained-diagnostic.ambackup"
    backup_result = BackupManager(paths, root=ROOT, test_mode=True).create(backup)
    assert backup_result["encrypted"] is True
    _, authenticated_digest = _authenticate_backup_to_path(
        backup, b"b" * 32, tmp_path / "authenticated-diagnostic.tar"
    )
    assert authenticated_digest == hashlib.sha256(backup.read_bytes()).hexdigest()
    original_tree = _tree_hashes(paths.instance_dir)
    retained_configuration = {
        relative: (paths.instance_dir / relative).read_bytes()
        for relative in (
            "config/instance.yaml",
            "config/member-mapping.json",
            "config/secret-references.json",
            "bridges/member-mapping.json",
            "openclaw/runtime/openclaw.json",
        )
    }
    manifest = build_manifest(
        paths,
        candidate_commit="c" * 40,
        acquisition=_policy([]),
        diagnostic_backup=backup,
    )
    assert manifest["diagnostic_backup"]["sha256"] == authenticated_digest
    operation = OfflineTestReset(paths, manifest)
    # The instance is offline after the TestClient lifecycle has drained and
    # closed every production owner, before either state move is permitted.
    assert old_runtime.started is False
    initialized = operation.begin()
    assert initialized["phase"] == "initialized"
    assert _tree_hashes(paths.instance_dir) == original_tree
    operation.prepare()
    selected = operation.switch()
    assert selected["phase"] == "selected_unaccepted"
    assert selected["production_rollback_proven"] is False
    assert _tree_hashes(operation.original) == original_tree
    assert (
        operation.archive / "original-activation.json"
    ).read_bytes() == original_journal
    assert not (paths.app_dir / "activation" / "active.json").exists()
    for relative, payload in retained_configuration.items():
        assert (paths.instance_dir / relative).read_bytes() == payload
    for name in ("events", "data", "projections"):
        assert not list((paths.instance_dir / name).iterdir())
    assert not (config / "openclaw-approvals.sqlite3").exists()

    fresh_settings = _settings_from_retained_configuration(paths)
    assert fresh_settings.instance.tenant_id == old_settings.instance.tenant_id
    assert fresh_settings.member_identities == old_settings.member_identities
    assert fresh_settings.member_roles == old_settings.member_roles
    assert (
        fresh_settings.adapter_config["adapters"]
        == old_settings.adapter_config["adapters"]
    )
    fresh_app = create_application(fresh_settings)
    with TestClient(fresh_app) as client:
        runtime = fresh_app.state.runtime
        adapter = client.portal.call(_adapter, runtime)
        assert adapter._config["member_id"] == MEMBER
        assert adapter._config["provider_account_id"] == ACCOUNT
        assert adapter._config["enable_writes"] is False
        assert adapter.status().extra["test_acquisition"]["selected_message_count"] == 0
        assert (
            adapter.status().extra["test_acquisition"][
                "lifetime_selected_message_count"
            ]
            == 0
        )
        assert client.portal.call(_source_events, runtime) == []
        fresh_receipts = client.portal.call(_settled_skill_receipts, runtime)
        assert not fresh_receipts
        assert not fresh_receipts & old_skill_receipts
        events = client.portal.call(_all_events, runtime)
        assert old_source["event_id"] not in {event["event_id"] for event in events}
        assert not any(event["type"].startswith("external_sync.") for event in events)
        assert client.portal.call(runtime.observation.is_active) is True
        assert runtime.xlsx_reverse._observer is None
        assert set(runtime.adapters.inventory()) == {"fixture-bounded-gmail"}
        assert runtime.settings_control.preferences_for(MEMBER) == {
            "location_cues": True
        }
        assert runtime.settings_control.read()["bridge_requests"] == {}
        member = client.get("/api/member/v1/surfaces/settings", headers=HEADERS)
        other = client.get("/api/member/v1/surfaces/settings", headers=OTHER_HEADERS)
        assert member.status_code == other.status_code == 200
        assert "bounded_test_sample" in member.text and ACCOUNT in member.text
        assert ACCOUNT not in other.text and "bounded_test_sample" not in other.text
        assert old_source["event_id"] not in member.text + other.text
        assert service.get_ids == ["selected-incoming"]
    marker = json.loads((config / "disposable-test-dataset.json").read_text())
    assert marker["dataset_id"] == manifest["reset_id"]
    assert marker["reset_manifest_sha256"] == manifest_digest(manifest)
    assert _tree_hashes(operation.original) == original_tree

    restored = operation.restore()
    assert restored["phase"] == "original_restored_health_unproven"
    assert restored["production_rollback_proven"] is False
    assert "doctor_status" not in restored
    assert _tree_hashes(paths.instance_dir) == original_tree
    assert (
        paths.app_dir / "activation" / "active.json"
    ).read_bytes() == original_journal
    assert (paths.app_dir / "current-release.json").read_bytes() == original_pointer
    assert json.loads(original_journal)["phase"] == "recovering"
    assert (
        operation.archive
        / "failed-test-state"
        / "config"
        / "disposable-test-dataset.json"
    ).exists()

    async def read_restored_source() -> dict[str, Any] | None:
        log = EventLog(old_settings.instance, KEY, read_only=True)
        try:
            return await log.get(old_source["event_id"])
        finally:
            await log.close()

    assert asyncio.run(read_restored_source()) == old_source
