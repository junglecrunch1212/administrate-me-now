"""Synthetic release selection, interruption recovery and exact transport pins."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.adapters.cos_channel_acquisition import CosAcquisitionPolicy
from bootstrap.activation_transaction import ActivationJournalError
from bootstrap.contracts import atomic_write
from bootstrap.cos_guard_release import COORDINATES, WRAPPER, snapshot_bytes
from bootstrap.cos_imessage import prepare_channel, verify_guard_files
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.unit.bootstrap.intake_fixture import stub_uninitialized_intake
from tests.unit.bootstrap.test_activation_transaction import (
    CANDIDATE_COMMIT,
    _commit_activation,
    _journal_value,
    _paths,
)
from tests.unit.bootstrap.test_cos_imessage import SENDERS, _adapters, _guard, _previous


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[UpgradeManager, dict[str, Any], dict[str, Any]]:
    paths = _paths(tmp_path.resolve())
    value = _journal_value(paths)
    source, target = Path(value["predecessor_path"]), Path(value["target_path"])
    for release in (source, target):
        atomic_write(
            release / WRAPPER, f"fixture reviewed guard {release.name}".encode()
        )
    binary = tmp_path.resolve() / "synthetic-provider"
    atomic_write(binary, b"nonexecutable synthetic provider")
    pin = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr("bootstrap.cos_imessage.BINARY_SHA256", pin)
    guard = _guard(tmp_path.resolve())
    guard.update(
        cli_path=str(binary),
        cli_sha256=pin,
        wrapper_path=str(source / WRAPPER),
        wrapper_sha256=hashlib.sha256((source / WRAPPER).read_bytes()).hexdigest(),
    )
    gateway = prepare_channel(_previous(), guard, allowed_senders=SENDERS)
    adapters = _adapters()
    adapters["cos_channel_acquisition"] = CosAcquisitionPolicy(
        retained_test_policy_sha256="a" * 64,
        gmail_adapter_id="fixture-cos-gmail",
        gmail_channel_id="fixture-cos-gmail-channel",
        gmail_provider_account_id="cos@example.invalid",
        imessage_channel_id="fixture-cos-imessage-channel",
    ).model_dump(mode="json")
    for relative, document in (
        (COORDINATES["guard"], guard),
        (COORDINATES["gateway"], gateway),
        (Path("config/adapters.json"), adapters),
    ):
        atomic_write(
            paths.instance_dir / relative, json.dumps(document, indent=2).encode()
        )
    atomic_write(
        paths.app_dir / "current-release.json",
        base64.b64decode(value["initial_pointer_bytes"]),
    )
    atomic_write(
        paths.instance_dir / "config/deployment-readiness.json",
        base64.b64decode(value["initial_readiness_bytes"]),
    )
    stub_uninitialized_intake(monkeypatch, paths)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    record = manager._prepare_cos_guard_rebind(
        adapter_config=adapters, source_release=source, target_release=target
    )
    assert record is not None
    value["cos_imessage_rebind"] = record
    return manager, value, adapters


def _assert_selected(manager: UpgradeManager, release: Path) -> None:
    guard = json.loads((manager.paths.instance_dir / COORDINATES["guard"]).read_bytes())
    gateway = json.loads(
        (manager.paths.instance_dir / COORDINATES["gateway"]).read_bytes()
    )
    verify_guard_files(guard, reviewed_release=release)
    assert (
        gateway["channels"]["imessage"]["accounts"][guard["account_id"]]["cliPath"]
        == guard["wrapper_path"]
    )


def _services(manager: UpgradeManager, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def start() -> SimpleNamespace:
        pointer = json.loads(
            (manager.paths.app_dir / "current-release.json").read_bytes()
        )
        _assert_selected(manager, Path(pointer["path"]))
        calls.append("start")
        return SimpleNamespace(status="healthy", runtime="running", openclaw="running")

    manager.services = SimpleNamespace(
        status=lambda: SimpleNamespace(status="healthy"),
        stop=lambda *args, **kwargs: calls.append("stop"),
        start=start,
    )
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda *_args: None)
    monkeypatch.setattr(manager, "_validate_runtime", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "bootstrap.upgrade.validate_candidate",
        lambda *_args, **_kwargs: {"compatible": True},
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: calls.append("plugin"),
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.configure_required_llm_tool", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "bootstrap.upgrade.install_launch_agents", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        manager, "_write_refreshed_runtime_binding", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        manager, "_post_switch_doctor", lambda *_args, **_kwargs: {"status": "healthy"}
    )
    return calls


def test_selected_release_changes_only_guard_coordinates_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, value, _adapters_config = _fixture(tmp_path, monkeypatch)
    calls = _services(manager, monkeypatch)
    owner = manager.activation_journal
    journal = owner.create(value)
    with pytest.raises(UpgradeError, match="stopped-service"):
        manager._select_cos_guard_rebind(journal)
    manager.services.stop()
    journal = owner.transition(journal, "openclaw_selected")
    selected = manager._select_cos_guard_rebind(journal)
    assert selected["phase"] == "cos_guard_selected"
    _assert_selected(manager, Path(value["target_path"]))
    for name, relative in COORDINATES.items():
        assert (manager.paths.instance_dir / relative).read_bytes() == snapshot_bytes(
            value["cos_imessage_rebind"][name]["after"]
        )
    atomic_write(
        manager.paths.app_dir / "current-release.json",
        json.dumps(
            {
                "commit": CANDIDATE_COMMIT,
                "path": value["target_path"],
                "previous": value["initial_pointer"],
            }
        ).encode(),
    )
    owner.finish(_commit_activation(owner, selected))
    result = manager.rollback()
    assert result["status"] == "rolled_back"
    _assert_selected(manager, Path(value["predecessor_path"]))
    assert calls[-2:] == ["plugin", "start"]
    assert (
        manager.paths.instance_dir / COORDINATES["guard"]
    ).read_bytes() == snapshot_bytes(value["cos_imessage_rebind"]["guard"]["before"])


@pytest.mark.parametrize(
    "crash_phase", ["cos_guard_selecting", "cos_guard_selected", "doctor_running"]
)
def test_partial_write_or_post_switch_failure_recovers_exact_predecessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_phase: str
) -> None:
    manager, value, _adapters_config = _fixture(tmp_path, monkeypatch)
    calls = _services(manager, monkeypatch)
    owner = manager.activation_journal
    journal = owner.transition(owner.create(value), "openclaw_selected")
    manager.services.stop()

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise KeyboardInterrupt("synthetic interrupted selector")

    monkeypatch.setattr(manager, "_test_crash_after_journal_phase", crash)
    if crash_phase == "doctor_running":
        journal = manager._select_cos_guard_rebind(journal)
        owner.transition(journal, "doctor_running")
    else:
        with pytest.raises(KeyboardInterrupt):
            manager._select_cos_guard_rebind(journal)
    # Simulate the later selector being written too. Recovery must restore
    # both configurations before it can start the predecessor.
    atomic_write(
        manager.paths.app_dir / "current-release.json",
        json.dumps({"commit": CANDIDATE_COMMIT, "path": value["target_path"]}).encode(),
    )
    result = manager._recover_interrupted_transaction()
    assert result is not None and result["status"] == "recovered"
    assert calls[-3:] == ["stop", "plugin", "start"]
    _assert_selected(manager, Path(value["predecessor_path"]))
    for name, relative in COORDINATES.items():
        assert (manager.paths.instance_dir / relative).read_bytes() == snapshot_bytes(
            value["cos_imessage_rebind"][name]["before"]
        )
    assert not owner.path.exists()


@pytest.mark.parametrize("coordinate", ["guard", "gateway"])
def test_changed_control_file_cannot_be_overwritten_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coordinate: str
) -> None:
    manager, value, _adapters_config = _fixture(tmp_path, monkeypatch)
    owner = manager.activation_journal
    journal = owner.transition(owner.create(value), "openclaw_selected")
    path = manager.paths.instance_dir / COORDINATES[coordinate]
    changed = json.loads(path.read_bytes())
    changed["unreviewed"] = True
    atomic_write(path, json.dumps(changed).encode())
    before = {
        name: (manager.paths.instance_dir / relative).read_bytes()
        for name, relative in COORDINATES.items()
    }
    with pytest.raises(UpgradeError, match="changed before"):
        manager._select_cos_guard_rebind(journal)
    assert before == {
        name: (manager.paths.instance_dir / relative).read_bytes()
        for name, relative in COORDINATES.items()
    }


def test_wrapper_tamper_or_broader_journal_delta_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, value, adapters = _fixture(tmp_path, monkeypatch)
    source, target = Path(value["predecessor_path"]), Path(value["target_path"])
    atomic_write(source / WRAPPER, b"tampered reviewed wrapper")
    with pytest.raises(UpgradeError, match="cannot be prepared"):
        manager._prepare_cos_guard_rebind(
            adapter_config=adapters, source_release=source, target_release=target
        )
    changed = copy.deepcopy(value)
    snapshot = changed["cos_imessage_rebind"]["guard"]["after"]
    document = json.loads(snapshot_bytes(snapshot))
    document["account_id"] = "unapproved-account"
    payload = json.dumps(document).encode()
    snapshot.update(
        bytes=base64.b64encode(payload).decode(),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    with pytest.raises(ActivationJournalError, match="CoS guard"):
        manager.activation_journal.create(changed)
    assert not manager.activation_journal.path.exists()


def test_real_plugin_replacement_keeps_reverse_guard_preparation_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bootstrap.openclaw_activation import reconcile_member_topology
    from bootstrap.openclaw_tools import reconcile_administrateme_plugin
    from tests.unit.bootstrap.test_openclaw_plugin_reconciliation import (
        _destructive_plugin_cli,
    )

    manager, value, adapters = _fixture(tmp_path, monkeypatch)
    source, target = Path(value["predecessor_path"]), Path(value["target_path"])
    _destructive_plugin_cli(manager.paths, target, monkeypatch)
    reconcile_member_topology(manager.paths)
    gateway_path = manager.paths.instance_dir / COORDINATES["gateway"]
    before = json.loads(gateway_path.read_bytes())["plugins"]["entries"][
        "administrateme"
    ]
    assert before["config"]["forwardIntakeAccounts"]
    forward = manager._prepare_cos_guard_rebind(
        adapter_config=adapters, source_release=source, target_release=target
    )
    assert forward is not None
    for name, relative in COORDINATES.items():
        atomic_write(
            manager.paths.instance_dir / relative,
            snapshot_bytes(forward[name]["after"]),
        )
    assert reconcile_administrateme_plugin(manager.paths, target)["ok"] is True
    assert (
        json.loads(gateway_path.read_bytes())["plugins"]["entries"]["administrateme"]
        == before
    )
    reverse = manager._prepare_cos_guard_rebind(
        adapter_config=adapters, source_release=target, target_release=source
    )
    assert reverse is not None
    restored_gateway = json.loads(snapshot_bytes(reverse["gateway"]["after"]))
    assert restored_gateway["plugins"]["entries"]["administrateme"] == before
