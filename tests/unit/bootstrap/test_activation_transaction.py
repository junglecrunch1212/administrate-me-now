from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bootstrap.activation_transaction import (
    ActivationJournal,
    ActivationJournalError,
)
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.upgrade import UpgradeManager


PREDECESSOR_COMMIT = "1" * 40
CANDIDATE_COMMIT = "2" * 40
PENDING_ID = "3" * 32
PENDING_SHA = "4" * 64
SETTINGS_PAYLOAD = b'{"revision":0,"schema_version":2}\n'


def _paths(tmp_path: Path) -> DeploymentPaths:
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    (paths.app_dir / "releases").mkdir(mode=0o700)
    (paths.instance_dir / "config").mkdir(mode=0o700, exist_ok=True)
    atomic_write(
        paths.instance_dir / "config" / "adapters.json",
        b'{"adapters":[]}',
        mode=0o600,
    )
    atomic_write(
        paths.instance_dir / "config" / "settings-control.json",
        SETTINGS_PAYLOAD,
        mode=0o600,
    )
    return paths


def _documents(paths: DeploymentPaths) -> tuple[bytes, dict[str, object], bytes]:
    predecessor = paths.app_dir / "releases" / PREDECESSOR_COMMIT
    predecessor.mkdir(mode=0o700, exist_ok=True)
    pointer: dict[str, object] = {
        "path": str(predecessor),
        "commit": PREDECESSOR_COMMIT,
    }
    pointer_payload = json.dumps(pointer, indent=2).encode("utf-8")
    readiness_payload = json.dumps(
        {
            "contract": "deployment-readiness-v1",
            "sentinel": "predecessor",
        },
        indent=2,
    ).encode("utf-8")
    return pointer_payload, pointer, readiness_payload


def _journal_value(
    paths: DeploymentPaths,
    *,
    operation: str = "activate",
) -> dict[str, object]:
    pointer_payload, pointer, readiness_payload = _documents(paths)
    predecessor = paths.app_dir / "releases" / PREDECESSOR_COMMIT
    candidate = paths.app_dir / "releases" / CANDIDATE_COMMIT
    candidate.mkdir(mode=0o700, exist_ok=True)
    if operation == "rollback":
        pointer = {
            "path": str(candidate),
            "commit": CANDIDATE_COMMIT,
            "previous": pointer,
        }
        pointer_payload = json.dumps(pointer, indent=2).encode("utf-8")
    settings_payload = (
        paths.instance_dir / "config" / "settings-control.json"
    ).read_bytes()
    return {
        "transaction_id": "5" * 32,
        "operation": operation,
        "phase": "created",
        "candidate_commit": CANDIDATE_COMMIT,
        "candidate_path": str(candidate),
        "target_commit": (
            CANDIDATE_COMMIT if operation == "activate" else PREDECESSOR_COMMIT
        ),
        "target_path": str(candidate if operation == "activate" else predecessor),
        "predecessor_commit": (
            PREDECESSOR_COMMIT if operation == "activate" else CANDIDATE_COMMIT
        ),
        "predecessor_path": str(predecessor if operation == "activate" else candidate),
        "initial_pointer": pointer,
        "initial_pointer_bytes": base64.b64encode(pointer_payload).decode("ascii"),
        "initial_pointer_sha256": hashlib.sha256(pointer_payload).hexdigest(),
        "initial_readiness": json.loads(readiness_payload),
        "initial_readiness_bytes": base64.b64encode(readiness_payload).decode("ascii"),
        "initial_readiness_sha256": hashlib.sha256(readiness_payload).hexdigest(),
        "initial_settings_control": json.loads(settings_payload),
        "initial_settings_control_bytes": base64.b64encode(settings_payload).decode(
            "ascii"
        ),
        "initial_settings_control_sha256": hashlib.sha256(settings_payload).hexdigest(),
        "was_running": True,
        "pending_attestation_id": PENDING_ID if operation == "activate" else None,
        "pending_attestation_sha256": (
            PENDING_SHA if operation == "activate" else None
        ),
        "fresh_backup": None,
        "rollback_eligibility": None,
        "active_readiness": None,
    }


def _commit_activation(
    owner: ActivationJournal,
    value: dict[str, Any],
) -> dict[str, Any]:
    return owner.transition(
        value,
        "committed",
        fresh_backup={"artifact_sha256": "6" * 64},
        rollback_eligibility={"sha256": "7" * 64},
        active_readiness={"contract": "deployment-readiness-v1"},
    )


def test_activation_journal_is_private_durable_and_archived(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)

    value = owner.create(_journal_value(paths))
    assert stat.S_IMODE(owner.path.stat().st_mode) == 0o600
    assert owner.load() == value

    value = owner.transition(value, "pointer_selecting")
    value = _commit_activation(owner, value)
    history = owner.finish(value)

    assert not owner.path.exists()
    assert stat.S_IMODE(history.stat().st_mode) == 0o600
    assert json.loads(history.read_text("utf-8"))["phase"] == "committed"
    assert (
        owner.committed_activation(
            target_commit=CANDIDATE_COMMIT,
            predecessor_commit=PREDECESSOR_COMMIT,
        )["transaction_id"]
        == value["transaction_id"]
    )


def test_activation_journal_refuses_unsafe_active_file(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    value = owner.create(_journal_value(paths))
    owner.path.chmod(0o644)

    with pytest.raises(ActivationJournalError, match="unsafe"):
        owner.load()

    assert value["phase"] == "created"


def test_activation_journal_refuses_symlinked_directory_without_outside_write(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    outside = tmp_path / "outside-activation"
    outside.mkdir(mode=0o700)
    owner.directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ActivationJournalError, match="unsafe"):
        owner.create(_journal_value(paths))

    assert tuple(outside.iterdir()) == ()


def test_activation_journal_refuses_hardlinked_active_record(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    owner.create(_journal_value(paths))
    outside = tmp_path / "journal-hardlink"
    os.link(owner.path, outside)

    with pytest.raises(ActivationJournalError, match="unsafe"):
        owner.load()

    assert outside.read_bytes() == owner.path.read_bytes()


def test_activation_journal_refuses_unknown_phase_and_mismatched_predecessor(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    unknown_phase = _journal_value(paths)
    unknown_phase["phase"] = "selector_maybe_finished"
    with pytest.raises(ActivationJournalError, match="phase"):
        owner.create(unknown_phase)

    mismatched = _journal_value(paths)
    mismatched["predecessor_commit"] = "9" * 40
    with pytest.raises(ActivationJournalError, match="binding"):
        owner.create(mismatched)


def test_activation_journal_refuses_duplicate_pointer_keys(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    value = _journal_value(paths)
    predecessor = paths.app_dir / "releases" / PREDECESSOR_COMMIT
    duplicate_pointer = (
        '{"commit":"0","commit":"'
        + PREDECESSOR_COMMIT
        + '","path":"'
        + str(predecessor)
        + '"}'
    ).encode("utf-8")
    value["initial_pointer_bytes"] = base64.b64encode(duplicate_pointer).decode("ascii")
    value["initial_pointer_sha256"] = hashlib.sha256(duplicate_pointer).hexdigest()

    with pytest.raises(ActivationJournalError, match="pointer bytes"):
        owner.create(value)


def test_activation_journal_refuses_unproved_committed_activation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    value = _journal_value(paths)
    value["phase"] = "committed"

    with pytest.raises(ActivationJournalError, match="mechanical evidence"):
        owner.create(value)


def test_activation_journal_accepts_legacy_record_without_settings_snapshot(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    value = _journal_value(paths)
    for name in (
        "initial_settings_control",
        "initial_settings_control_bytes",
        "initial_settings_control_sha256",
    ):
        del value[name]

    created = owner.create(value)

    assert "initial_settings_control" not in created


def test_activation_journal_rejects_changed_settings_snapshot_digest(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    value = _journal_value(paths)
    value["initial_settings_control_sha256"] = "9" * 64

    with pytest.raises(ActivationJournalError, match="settings control"):
        owner.create(value)


@pytest.mark.parametrize(
    "phase",
    (
        "backup_creating",
        "backup_rehearsing",
        "openclaw_selected",
        "pointer_selected",
        "readiness_promoted",
        "services_started",
        "doctor_running",
    ),
)
def test_interrupted_activation_restores_exact_predecessor_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    paths = _paths(tmp_path)
    pointer_payload, _pointer, readiness_payload = _documents(paths)
    pointer_path = paths.app_dir / "current-release.json"
    readiness_path = paths.instance_dir / "config" / "deployment-readiness.json"
    atomic_write(pointer_path, b'{"candidate":true}\n', mode=0o600)
    readiness_path.parent.mkdir(mode=0o700, exist_ok=True)
    atomic_write(readiness_path, b'{"candidate":true}\n', mode=0o600)
    owner = ActivationJournal(paths)
    value = owner.create(_journal_value(paths))
    value = owner.transition(value, phase)
    settings_path = paths.instance_dir / "config" / "settings-control.json"
    atomic_write(
        settings_path,
        b'{"revision":0,"schema_version":6}\n',
        mode=0o600,
    )

    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    events: list[str] = []
    manager.services = SimpleNamespace(
        stop=lambda *args, **kwargs: events.append("stop"),
        start=lambda: (
            events.append("start")
            or SimpleNamespace(
                status="healthy",
                runtime="running",
                openclaw="running",
            )
        ),
    )
    monkeypatch.setattr(
        manager,
        "_install_reviewed_openclaw",
        lambda _release: events.append("openclaw"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: events.append("plugin"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: events.append("launchagents"),
    )

    result = manager._recover_interrupted_transaction()  # noqa: SLF001

    assert result is not None and result["status"] == "recovered"
    assert pointer_path.read_bytes() == pointer_payload
    assert readiness_path.read_bytes() == readiness_payload
    assert settings_path.read_bytes() == SETTINGS_PAYLOAD
    assert events == ["stop", "openclaw", "plugin", "launchagents", "start"]
    assert not owner.path.exists()
    history = (
        paths.app_dir / "activation" / "history" / f"{value['transaction_id']}.json"
    )
    assert json.loads(history.read_text("utf-8"))["phase"] == "recovered"


def test_activation_recovery_failure_keeps_journal_and_services_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    owner.create(_journal_value(paths))
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    events: list[str] = []
    manager.services = SimpleNamespace(
        stop=lambda *args, **kwargs: events.append("stop"),
        start=lambda: events.append("start"),
    )

    def fail_openclaw(_release: Path) -> None:
        raise RuntimeError("injected selector failure")

    monkeypatch.setattr(manager, "_install_reviewed_openclaw", fail_openclaw)
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="did not converge"):
        manager._recover_interrupted_transaction()  # noqa: SLF001

    assert owner.path.exists()
    assert events == ["stop", "stop"]


def test_activation_recovery_waits_for_bounded_source_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _documents(paths)
    owner = ActivationJournal(paths)
    owner.create(_journal_value(paths))
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    events: list[str] = []
    manager.services = SimpleNamespace(
        stop=lambda *args, **kwargs: events.append("stop"),
        start=lambda: (
            events.append("start")
            or SimpleNamespace(
                status="failed",
                runtime="failed",
                openclaw="running",
            )
        ),
        runtime_is_live=lambda: events.append("liveness") or True,
    )
    monkeypatch.setattr(
        manager,
        "_post_switch_doctor",
        lambda release: (
            events.append(f"doctor:{release.name}")
            or {"status": "healthy", "checks": []}
        ),
    )
    monkeypatch.setattr(
        manager,
        "_install_reviewed_openclaw",
        lambda _release: events.append("openclaw"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: events.append("plugin"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: events.append("launchagents"),
    )

    result = manager._recover_interrupted_transaction()  # noqa: SLF001

    assert result is not None and result["status"] == "recovered"
    assert events == [
        "stop",
        "openclaw",
        "plugin",
        "launchagents",
        "start",
        "liveness",
        f"doctor:{('1' * 40)}",
    ]
    assert not owner.path.exists()


def test_activation_recovery_accepts_only_isolated_degraded_adapter_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _documents(paths)
    owner = ActivationJournal(paths)
    owner.create(_journal_value(paths))
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    diagnostics = {
        "status": "degraded",
        "ready": False,
        "safe_reads": True,
        "event_ingest_ready": True,
        "observation_active": True,
        "write_admission": "observation_suppressed",
        "capabilities": {
            "adapter:provider": {"kind": "adapter", "state": "degraded"},
            "projection:items": {"kind": "projection", "state": "healthy"},
        },
    }
    manager.services = SimpleNamespace(
        stop=lambda *args, **kwargs: None,
        start=lambda: SimpleNamespace(
            status="degraded",
            runtime="degraded",
            openclaw="running",
        ),
        runtime_diagnostics=lambda: diagnostics,
    )
    monkeypatch.setattr(
        manager,
        "_post_switch_doctor",
        lambda _release: {"status": "failed", "checks": []},
    )
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda _release: None)
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: None,
    )

    result = manager._recover_interrupted_transaction()  # noqa: SLF001

    assert result is not None and result["status"] == "recovered"
    assert not owner.path.exists()


def test_production_recovery_uses_the_supplied_reviewed_git_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    pointer_payload, _pointer, readiness_payload = _documents(paths)
    pointer_path = paths.app_dir / "current-release.json"
    readiness_path = paths.instance_dir / "config" / "deployment-readiness.json"
    atomic_write(pointer_path, b'{"candidate":true}\n', mode=0o600)
    atomic_write(readiness_path, b'{"candidate":true}\n', mode=0o600)
    owner = ActivationJournal(paths)
    value = owner.create(_journal_value(paths))
    value = owner.transition(value, "backup_creating")
    reviewed_candidate = tmp_path / "reviewed-candidate"
    reviewed_candidate.mkdir()
    fixed_controller = tmp_path / "materialized-controller"
    fixed_controller.mkdir()
    manager = UpgradeManager(paths, root=fixed_controller, test_mode=False)
    events: list[str] = []
    observed: dict[str, Path] = {}

    def reviewed_commit(repository: Path) -> str:
        assert repository == reviewed_candidate
        return "9" * 40

    def verify_source(
        _release: Path,
        *,
        repository: Path,
        **_kwargs: object,
    ) -> dict[str, object]:
        observed["repository"] = repository
        return {"tracked_source_tree_matches": True}

    monkeypatch.setattr("bootstrap.upgrade.reviewed_candidate_commit", reviewed_commit)
    monkeypatch.setattr(
        "bootstrap.upgrade.verify_release_source_identity",
        verify_source,
    )
    monkeypatch.setattr(manager, "_validate_prepared_runtime", lambda _release: None)
    manager.services = SimpleNamespace(
        stop=lambda *args, **kwargs: events.append("stop"),
        start=lambda: (
            events.append("start")
            or SimpleNamespace(
                status="healthy",
                runtime="running",
                openclaw="running",
            )
        ),
    )
    monkeypatch.setattr(
        manager,
        "_install_reviewed_openclaw",
        lambda _release: events.append("openclaw"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: events.append("plugin"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: events.append("launchagents"),
    )

    result = manager._recover_interrupted_transaction(  # noqa: SLF001
        reviewed_candidate
    )

    assert result is not None and result["status"] == "recovered"
    assert observed["repository"] == reviewed_candidate
    assert pointer_path.read_bytes() == pointer_payload
    assert readiness_path.read_bytes() == readiness_payload
    assert events == ["stop", "openclaw", "plugin", "launchagents", "start"]
    assert not owner.path.exists()
    history = (
        paths.app_dir / "activation" / "history" / f"{value['transaction_id']}.json"
    )
    assert json.loads(history.read_text("utf-8"))["phase"] == "recovered"


@pytest.mark.parametrize(
    "transition_contract",
    [
        "provider-binding-config-transition-v1",
        "provider-binding-config-transition-v2",
    ],
)
def test_interrupted_provider_config_selection_restores_exact_predecessor_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition_contract: str,
) -> None:
    paths = _paths(tmp_path)
    pointer_payload, _pointer, readiness_payload = _documents(paths)
    atomic_write(
        paths.app_dir / "current-release.json",
        pointer_payload,
        mode=0o600,
    )
    readiness_path = paths.instance_dir / "config" / "deployment-readiness.json"
    atomic_write(readiness_path, readiness_payload, mode=0o600)
    adapter_path = paths.instance_dir / "config" / "adapters.json"
    source = b'{"adapters":[{"config":{"legacy":true}}]}'
    prepared = b'{"adapters":[{"config":{"binding":"prepared","legacy":true}}]}'
    atomic_write(adapter_path, source, mode=0o600)
    transition: dict[str, Any] = {
        "contract": transition_contract,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "prepared_sha256": hashlib.sha256(prepared).hexdigest(),
        "migrated_adapter_count": 1,
        "credentials_changed": False,
        "capabilities_changed": False,
        "content_exposed": False,
    }
    if transition_contract.endswith("v2"):
        transition.update(
            {
                "enrolled_adapter_count": 3,
                "enrollment_profile": "personal-google-readonly",
                "observation_required": True,
                "capabilities_changed": True,
            }
        )
    value = _journal_value(paths)
    value.update(
        {
            "initial_adapter_config": json.loads(source),
            "initial_adapter_config_bytes": base64.b64encode(source).decode("ascii"),
            "initial_adapter_config_sha256": hashlib.sha256(source).hexdigest(),
            "selected_adapter_config": json.loads(prepared),
            "selected_adapter_config_bytes": base64.b64encode(prepared).decode("ascii"),
            "selected_adapter_config_sha256": hashlib.sha256(prepared).hexdigest(),
            "provider_binding_transition": transition,
        }
    )
    owner = ActivationJournal(paths)
    journal = owner.create(value)
    atomic_write(adapter_path, prepared, mode=0o600)
    owner.transition(journal, "provider_config_selected")

    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    manager.services = SimpleNamespace(
        stop=lambda *args, **kwargs: None,
        start=lambda: SimpleNamespace(
            status="healthy", runtime="running", openclaw="running"
        ),
    )
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda _release: None)

    topology_reconciled_after_restore = False

    def reconcile_after_restore(*_args: Any, **_kwargs: Any) -> None:
        nonlocal topology_reconciled_after_restore
        assert adapter_path.read_bytes() == source
        topology_reconciled_after_restore = True

    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        reconcile_after_restore,
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: None,
    )

    result = manager._recover_interrupted_transaction()  # noqa: SLF001

    assert result is not None and result["status"] == "recovered"
    assert adapter_path.read_bytes() == source
    assert topology_reconciled_after_restore is True


def test_rollback_journal_cannot_bind_pending_evidence(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    value = _journal_value(paths, operation="rollback")
    value["pending_attestation_id"] = PENDING_ID

    with pytest.raises(ActivationJournalError, match="rollback journal"):
        ActivationJournal(paths).create(value)


def test_production_activation_requires_pending_evidence_before_candidate_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=False)
    candidate_work_started = False

    def candidate_commit(_candidate: Path) -> str:
        nonlocal candidate_work_started
        candidate_work_started = True
        return CANDIDATE_COMMIT

    monkeypatch.setattr(manager, "_candidate_commit", candidate_commit)

    with pytest.raises(
        RuntimeError,
        match="requires an explicit candidate-pending attestation",
    ):
        manager.activate(tmp_path / "candidate")

    assert candidate_work_started is False
    assert tuple(paths.backup_dir.iterdir()) == ()
    assert not (paths.app_dir / "current-release.json").exists()


def test_rollback_refuses_ambiguous_committed_history_before_selector_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _pointer_payload, pointer, _readiness_payload = _documents(paths)
    candidate = paths.app_dir / "releases" / CANDIDATE_COMMIT
    current = {
        "path": str(candidate),
        "commit": CANDIDATE_COMMIT,
        "previous": pointer,
    }
    current_payload = json.dumps(current, indent=2).encode("utf-8")
    atomic_write(
        paths.app_dir / "current-release.json",
        current_payload,
        mode=0o600,
    )
    owner = ActivationJournal(paths)
    for transaction_id in ("5" * 32, "6" * 32):
        value = _journal_value(paths)
        value["transaction_id"] = transaction_id
        created = owner.create(value)
        committed = _commit_activation(owner, created)
        owner.finish(committed)

    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    selector_events: list[str] = []
    monkeypatch.setattr(
        "bootstrap.upgrade.validate_candidate",
        lambda *_args, **_kwargs: {"compatible": True},
    )
    monkeypatch.setattr(manager, "_validate_prepared_runtime", lambda _release: None)
    monkeypatch.setattr(
        manager,
        "_install_reviewed_openclaw",
        lambda _release: selector_events.append("openclaw"),
    )

    with pytest.raises(
        RuntimeError,
        match="exact committed activation history",
    ):
        manager.rollback()

    assert selector_events == []
    assert (paths.app_dir / "current-release.json").read_bytes() == current_payload


def test_explicit_rollback_restores_predecessor_settings_control_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    pointer_payload, pointer, readiness_payload = _documents(paths)
    readiness_path = paths.instance_dir / "config" / "deployment-readiness.json"
    atomic_write(readiness_path, readiness_payload, mode=0o600)
    owner = ActivationJournal(paths)
    created = owner.create(_journal_value(paths))
    committed = _commit_activation(owner, created)
    owner.finish(committed)

    candidate = paths.app_dir / "releases" / CANDIDATE_COMMIT
    current = {
        "path": str(candidate),
        "commit": CANDIDATE_COMMIT,
        "previous": pointer,
    }
    atomic_write(
        paths.app_dir / "current-release.json",
        json.dumps(current, indent=2).encode("utf-8"),
        mode=0o600,
    )
    settings_path = paths.instance_dir / "config" / "settings-control.json"
    atomic_write(
        settings_path,
        b'{"revision":0,"schema_version":6}\n',
        mode=0o600,
    )

    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    manager.services = SimpleNamespace(
        status=lambda: SimpleNamespace(status="healthy"),
        stop=lambda *args, **kwargs: None,
        start=lambda: SimpleNamespace(
            status="healthy",
            runtime="running",
            openclaw="running",
        ),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.validate_candidate",
        lambda *_args, **_kwargs: {"compatible": True},
    )
    monkeypatch.setattr(manager, "_validate_prepared_runtime", lambda _release: None)
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda _release: None)
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.configure_required_llm_tool",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        manager,
        "_post_switch_doctor",
        lambda _release: {"status": "healthy", "checks": []},
    )

    result = manager.rollback()

    assert result["status"] == "rolled_back"
    assert settings_path.read_bytes() == SETTINGS_PAYLOAD
    assert (paths.app_dir / "current-release.json").read_bytes() == pointer_payload


def test_transactional_activation_orders_fresh_rehearsal_before_selectors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    pointer_payload, pointer, readiness_payload = _documents(paths)
    atomic_write(
        paths.app_dir / "current-release.json",
        pointer_payload,
        mode=0o600,
    )
    readiness_path = paths.instance_dir / "config" / "deployment-readiness.json"
    readiness_path.parent.mkdir(mode=0o700, exist_ok=True)
    atomic_write(readiness_path, readiness_payload, mode=0o600)
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    release = paths.app_dir / "releases" / CANDIDATE_COMMIT
    release.mkdir(mode=0o700)
    manager = UpgradeManager(
        paths,
        root=candidate,
        test_mode=True,
        pending_attestation_id=PENDING_ID,
    )
    events: list[str] = []
    manager.services = SimpleNamespace(
        status=lambda: SimpleNamespace(status="healthy"),
        stop=lambda *args, **kwargs: events.append("action:stop"),
        start=lambda: (
            events.append("action:start")
            or SimpleNamespace(
                status="failed",
                runtime="failed",
                openclaw="running",
            )
        ),
        runtime_is_live=lambda: events.append("action:liveness") or True,
        runtime_diagnostics=lambda: {
            "ready": True,
            "capabilities": {"projection:items": {"state": "healthy"}},
        },
    )
    monkeypatch.setattr(
        manager, "_candidate_commit", lambda _candidate: CANDIDATE_COMMIT
    )
    monkeypatch.setattr(
        manager,
        "_attest_controller_provenance",
        lambda *_args: events.append("action:controller_attested"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.validate_candidate",
        lambda *_args, **_kwargs: {"compatible": True},
    )
    monkeypatch.setattr(manager, "_preflight_keychain_access", lambda _candidate: None)
    monkeypatch.setattr(manager, "_publish_release", lambda *_args: release)
    snapshot = SimpleNamespace(public=lambda: {"binding": "fixture"})
    pending = {
        "attestation_id": PENDING_ID,
        "deployment_binding": {"provider_binding_transition": None},
    }
    monkeypatch.setattr(
        "bootstrap.upgrade.load_pending_attestation_for_activation",
        lambda *_args, **_kwargs: (pending, PENDING_SHA, snapshot),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.build_active_attestation_from_pending",
        lambda *_args, **_kwargs: {
            "contract": "deployment-readiness-v1",
            "deployment_binding": {
                "release_commit": CANDIDATE_COMMIT,
                "instance_fingerprint": "8" * 64,
                "host_fingerprint": "9" * 64,
                "configuration_fingerprint": "a" * 64,
            },
            "activation_evidence": {"candidate_live_doctor": "pending_post_switch"},
        },
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(
            predecessor_commit=CANDIDATE_COMMIT,
            instance_fingerprint="8" * 64,
            host_fingerprint="9" * 64,
            configuration_fingerprint="b" * 64,
        ),
    )
    monkeypatch.setattr(
        manager,
        "_prove_predecessor_rollback_eligibility",
        lambda **_kwargs: {
            "contract": "administrateme-predecessor-rollback-eligibility-v1",
            "content_exposed": False,
        },
    )

    class FakeBackupManager:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create(self, target: Path) -> dict[str, Any]:
            events.append("action:backup")
            atomic_write(target, b"encrypted-fixture", mode=0o600)
            return {
                "path": str(target),
                "manifest_sha256": "6" * 64,
                "encrypted": True,
            }

        def rehearse(
            self,
            backup: Path,
            *,
            candidate: Path,
            receipt: Path,
        ) -> dict[str, Any]:
            del backup, candidate
            events.append("action:rehearsal")
            atomic_write(receipt, b'{"receipt":true}\n', mode=0o600)
            return {
                "status": "healthy",
                "tested_at": "2026-08-27T12:00:00+00:00",
                "candidate": {"commit": CANDIDATE_COMMIT},
                "artifact": {
                    "sha256": hashlib.sha256(b"encrypted-fixture").hexdigest()
                },
                "deployment_binding": {"binding": "fixture"},
                "receipt_sha256": "7" * 64,
            }

    monkeypatch.setattr("bootstrap.upgrade.BackupManager", FakeBackupManager)
    activity = paths.app_dir / "activity-fixture"
    activity_descriptor = os.open(activity, os.O_RDWR | os.O_CREAT, 0o600)
    monkeypatch.setattr(
        manager,
        "_consume_preparation_state",
        lambda *_args: activity_descriptor,
    )
    monkeypatch.setattr(
        manager,
        "_install_reviewed_openclaw",
        lambda _release: events.append("action:openclaw"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: events.append("action:plugin"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.configure_required_llm_tool",
        lambda *_args, **_kwargs: events.append("action:llm"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents",
        lambda *_args, **_kwargs: events.append("action:launchagents"),
    )
    monkeypatch.setattr(
        manager,
        "_post_switch_doctor",
        lambda _release, **_kwargs: (
            events.append("action:doctor") or {"status": "healthy"}
        ),
    )
    original_transition = manager.activation_journal.transition

    def observe_transition(
        value: dict[str, Any], phase: str, **updates: Any
    ) -> dict[str, Any]:
        events.append(f"phase:{phase}")
        return original_transition(value, phase, **updates)

    monkeypatch.setattr(manager.activation_journal, "transition", observe_transition)

    result = manager.activate(candidate)

    assert result["status"] == "upgraded"
    assert result["pending_attestation_id"] == PENDING_ID
    assert events.index("phase:backup_creating") < events.index("action:backup")
    assert events.index("phase:backup_rehearsing") < events.index("action:rehearsal")
    assert events.index("action:rehearsal") < events.index(
        "phase:selector_mutation_authorized"
    )
    assert events.index("phase:openclaw_selecting") < events.index("action:openclaw")
    assert events.index("phase:plugin_selecting") < events.index("action:plugin")
    assert events.index("phase:llm_tool_selecting") < events.index("action:llm")
    assert events.index("action:llm") < events.index("phase:runtime_binding_refreshed")
    assert events.index("phase:runtime_binding_refreshed") < events.index(
        "action:launchagents"
    )
    assert events.index("phase:launchagents_selecting") < events.index(
        "action:launchagents"
    )
    assert events.index("action:start") < events.index("action:liveness")
    assert events.index("action:liveness") < events.index("action:doctor")
    active_pointer = json.loads(
        (paths.app_dir / "current-release.json").read_text("utf-8")
    )
    assert active_pointer == {
        "path": str(release),
        "commit": CANDIDATE_COMMIT,
        "previous": pointer,
    }
    assert (
        json.loads(readiness_path.read_text("utf-8"))["activation_evidence"][
            "candidate_live_doctor"
        ]
        is True
    )
    assert (
        json.loads(readiness_path.read_text("utf-8"))["deployment_binding"][
            "configuration_fingerprint"
        ]
        == "b" * 64
    )
    assert not manager.activation_journal.path.exists()
