"""Retained Apple authority/results forbid an unaware central recovery target."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from adminme.apple_service_permissions import CONTRACT, EVENT_TYPE, OPERATIONS, digest
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from bootstrap.activation_transaction import ActivationJournal
from bootstrap.communication_intake import (
    CommunicationIntakeInspectionError,
    required_instance_policies,
)
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.upgrade import UpgradeError, UpgradeManager, validate_candidate
from tests.unit.bootstrap.test_activation_transaction import _journal_value, _paths
from tests.unit.bootstrap.test_communication_intake_upgrade_safety import (
    KEY,
    POLICY,
    _append_epoch,
    _initialize_log,
    _release as _intake_release,
    test_production_inspection_uses_bounded_value_free_child as _assert_bounded_child,
)


MEMBER = "fixture-apple-recovery-member"
AT = "2034-02-01T10:00:00Z"


def _release(path: Path, *, supports: bool) -> Path:
    _intake_release(path, supports=True, activates=True)
    manifest = path / "bootstrap/versions.json"
    versions = json.loads(manifest.read_text())
    versions["administrateme"].pop("supported_apple_service_write_contracts", None)
    if supports:
        versions["administrateme"]["supported_apple_service_write_contracts"] = [
            CONTRACT
        ]
    manifest.write_text(json.dumps(versions))
    return path


def _append(
    paths: DeploymentPaths,
    event_type: str,
    payload: dict[str, Any],
    *,
    version: int = 1,
) -> None:
    config = load_instance_config(paths.instance_dir)

    async def append() -> None:
        log = EventLog(config, KEY)
        try:
            await log.append(
                EventEnvelope(
                    event_at_ms=2_022_408_000_000,
                    tenant_id=config.tenant_id,
                    type=event_type,
                    schema_version=version,
                    occurred_at=AT,
                    source_adapter="fixture-apple-recovery",
                    source_account_id="fixture-apple-provider",
                    actor_identity=MEMBER,
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    idempotency_key=event_type + ":" + digest(payload),
                    payload=payload,
                )
            )
        finally:
            await log.close()

    asyncio.run(append())


def _permission(
    paths: DeploymentPaths, *, provider: str = "apple_calendar", revoke: bool = False
) -> None:
    coordinates = {
        "member_id": MEMBER,
        "provider": provider,
        "enrollment_id": "fixture-apple-enrollment",
        "device_identity_sha256": "a" * 64,
        "configuration_sha256": "b" * 64,
        "provider_account_id": "fixture-apple-provider",
        "account_external_id": "fixture-apple-account",
        "allowed_container_ids": ["fixture-apple-container"],
        "operations": OPERATIONS[provider],
    }
    _append(
        paths,
        EVENT_TYPE,
        {
            "member_id": MEMBER,
            "provider": provider,
            "request_id": "fixture-revoke" if revoke else "fixture-enable",
            "action": "revoke" if revoke else "enable",
            "enabled": not revoke,
            "revision": 2 if revoke else 1,
            "scope_ids": [] if revoke else ["fixture-apple-scope"],
            "coordinates": coordinates,
            "coordinates_sha256": digest(coordinates),
            "changed_by_member_id": MEMBER,
            "changed_at": AT,
            "provider_effects": False,
        },
    )


def _action(
    paths: DeploymentPaths,
    event_type: str,
    provider: str,
    *,
    marked: bool = True,
) -> None:
    operation = {
        "apple_calendar": "create_event",
        "apple_reminders": "create_reminder",
        "apple_notes": "create_note",
    }[provider]
    payload: dict[str, Any] = {
        "member_id": MEMBER,
        "provider": provider,
        "provider_account_id": "fixture-apple-provider",
        "operation": operation,
        "request_fingerprint": "c" * 64,
    }
    marker: dict[str, Any] = {"apple_service_permission": {}} if marked else {}
    version = 2
    if event_type == "member.provider_action.confirmed":
        version = 1
        payload.update(
            action_id="fixture-apple-action",
            command_payload=marker,
            confirmed_at=AT,
        )
    else:
        payload.update(
            command_id="fixture-apple-command",
            domain_approval_event_id="fixture-apple-domain",
            openclaw_approval_event_id="fixture-apple-native",
        )
        if event_type == "external_sync.write_requested":
            payload.update(
                payload=marker, requested_at=AT, privacy_guard_version="fixture-v1"
            )
        else:
            payload.update(
                command_event_id="fixture-apple-command-event", provider_called=True
            )
            if event_type == "external_sync.write_completed":
                payload.update(completed_at=AT, suppressed=False)
            else:
                payload.update(
                    failed_at=AT,
                    error_kind="provider",
                    message="Fixture ambiguous provider result",
                    retryable=False,
                )
    _append(paths, event_type, payload, version=version)


def test_pre_permission_upgrade_and_rollback_do_not_require_new_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    old = _release(tmp_path / "old", supports=False)
    new = _release(tmp_path / "new", supports=True)
    manager = UpgradeManager(paths, root=new, test_mode=True)
    for provider in ("apple_calendar", "apple_reminders"):
        _action(paths, "member.provider_action.confirmed", provider, marked=False)
    assert required_instance_policies(paths, test_mode=True) == frozenset()
    for active, target in ((old, new), (new, old)):
        result = manager._validate_instance_candidate(target, active_root=active)
        assert result["apple_service_write_contracts"] == []


@pytest.mark.parametrize(
    "provider", ["apple_calendar", "apple_reminders", "apple_notes"]
)
@pytest.mark.parametrize("revoke", [False, True])
def test_durable_permission_survives_revoke_and_empty_cache_and_checks_only_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, revoke: bool
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    _append_epoch(paths)
    _permission(paths, provider=provider)
    if revoke:
        _permission(paths, provider=provider, revoke=True)
    # The mutable cache is deliberately still the pre-grant fixture. Recovery
    # must rely on the encrypted log, and never restore that log from a backup.
    cache = paths.instance_dir / "config/settings-control.json"
    assert "apple_service_permissions" not in json.loads(cache.read_text())
    database = paths.instance_dir / "events/events.db"
    before = hashlib.sha256(database.read_bytes()).digest()
    old = _release(tmp_path / "old", supports=False)
    new = _release(tmp_path / "new", supports=True)
    manager = UpgradeManager(paths, root=new, test_mode=True)
    assert required_instance_policies(paths, test_mode=True) == frozenset(
        {POLICY, CONTRACT}
    )
    result = manager._validate_instance_candidate(new, active_root=old)
    assert result["apple_service_write_contracts"] == [CONTRACT]
    assert result["communication_intake_policies"] == [POLICY]
    with pytest.raises(UpgradeError, match="cannot preserve retained Apple"):
        manager._validate_instance_candidate(old, active_root=new)
    assert hashlib.sha256(database.read_bytes()).digest() == before


@pytest.mark.parametrize(
    "event_type,provider",
    [
        ("member.provider_action.confirmed", "apple_calendar"),
        ("member.provider_action.confirmed", "apple_reminders"),
        ("member.provider_action.confirmed", "apple_notes"),
        ("external_sync.write_requested", "apple_calendar"),
        ("external_sync.write_requested", "apple_reminders"),
        ("external_sync.write_requested", "apple_notes"),
        ("external_sync.write_completed", "apple_notes"),
        ("external_sync.write_failed", "apple_notes"),
    ],
)
def test_marked_action_or_notes_result_cannot_hide_behind_missing_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_type: str, provider: str
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    _action(paths, event_type, provider)
    assert required_instance_policies(paths, test_mode=True) == frozenset({CONTRACT})


@pytest.mark.parametrize("transactional", [False, True])
def test_rollback_refuses_before_service_pointer_or_journal_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transactional: bool
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    _permission(paths)
    old = _release(paths.app_dir / "releases" / ("1" * 40), supports=False)
    new = _release(paths.app_dir / "releases" / ("2" * 40), supports=True)
    pointer = paths.app_dir / "current-release.json"
    atomic_write(
        pointer,
        json.dumps(
            {
                "path": str(new),
                "commit": "2" * 40,
                "previous": {"path": str(old), "commit": "1" * 40},
            }
        ).encode(),
    )
    before = pointer.read_bytes()
    manager = UpgradeManager(paths, root=new, test_mode=True)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("retained-history guard must run before mutation")

    monkeypatch.setattr(manager.services, "stop", forbidden)
    monkeypatch.setattr(manager.services, "start", forbidden)
    monkeypatch.setattr(manager.activation_journal, "create", forbidden)
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", forbidden)
    with pytest.raises(UpgradeError, match="cannot preserve retained Apple"):
        if transactional:
            manager._rollback_transactional_locked()
        else:
            manager._rollback_locked()
    assert pointer.read_bytes() == before
    assert manager.activation_journal.load() is None


def test_recovery_rechecks_permission_race_after_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    journal = ActivationJournal(paths)
    value = _journal_value(paths)
    _release(Path(str(value["predecessor_path"])), supports=False)
    _release(Path(str(value["target_path"])), supports=True)
    journal.create(value)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    stops: list[str] = []

    def stop() -> None:
        stops.append("quiesced")
        _permission(paths)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a raced permission must block old selector restoration")

    monkeypatch.setattr(manager.services, "stop", stop)
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", forbidden)
    monkeypatch.setattr(manager, "_restore_journal_control_files", forbidden)
    with pytest.raises(UpgradeError, match="cannot preserve retained Apple"):
        manager._recover_interrupted_transaction()
    assert stops == ["quiesced"]
    retained_journal = journal.load()
    assert retained_journal is not None
    assert retained_journal["phase"] == "recovering"
    with pytest.raises(UpgradeError, match="cannot preserve retained Apple"):
        manager._recover_interrupted_transaction()
    assert stops == ["quiesced"]


def test_changed_tenant_cannot_hide_apple_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    _permission(paths)
    atomic_write(
        paths.instance_dir / "config/instance.yaml",
        b"tenant_id: fixture-different-tenant\ntimezone: Etc/UTC\n",
    )
    with pytest.raises(CommunicationIntakeInspectionError, match="cannot be verified"):
        required_instance_policies(paths, test_mode=True)


@pytest.mark.parametrize("value", [None, "incorrect", [True]])
def test_invalid_target_capability_refuses(tmp_path: Path, value: object) -> None:
    old = _release(tmp_path / "old", supports=False)
    new = _release(tmp_path / "new", supports=True)
    manifest = new / "bootstrap/versions.json"
    versions = json.loads(manifest.read_text())
    versions["administrateme"]["supported_apple_service_write_contracts"] = value
    manifest.write_text(json.dumps(versions))
    with pytest.raises(UpgradeError, match="Apple service write capability is invalid"):
        validate_candidate(new, active_root=old)


@pytest.mark.parametrize(
    "code,expected",
    [(12, frozenset({CONTRACT})), (13, frozenset({CONTRACT, POLICY}))],
)
def test_production_child_returns_only_fixed_contract_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    expected: frozenset[str],
) -> None:
    _assert_bounded_child(tmp_path, monkeypatch, code, expected)
