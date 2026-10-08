"""The shared-log intake cutover requires a rollback runtime that honors it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bootstrap.activation_transaction import ActivationJournal
from bootstrap.upgrade import UpgradeError, UpgradeManager, validate_candidate
from tests.unit.bootstrap.test_activation_transaction import _journal_value, _paths


ROOT = Path(__file__).resolve().parents[3]
POLICY = "explicit-member-submission-v1"


def _release(path: Path, *, supports: bool, activates: bool) -> Path:
    for relative in (
        "poetry.lock",
        "bootstrap/versions.json",
        "openclaw/administrateme/package.json",
        "openclaw/administrateme/openclaw.plugin.json",
    ):
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    manifest = path / "bootstrap/versions.json"
    versions = json.loads(manifest.read_text())
    application = versions["administrateme"]
    application.pop("supported_communication_intake_policies", None)
    application.pop("default_communication_intake_policy", None)
    if supports:
        application["supported_communication_intake_policies"] = [POLICY]
    if activates:
        application["default_communication_intake_policy"] = POLICY
    manifest.write_text(json.dumps(versions))
    return path


@pytest.mark.parametrize("rollback", [False, True])
def test_cutover_and_rollback_both_refuse_a_policy_unaware_release(
    tmp_path: Path, rollback: bool
) -> None:
    old = _release(tmp_path / "old", supports=False, activates=False)
    new = _release(tmp_path / "new", supports=True, activates=True)
    active, incoming = (new, old) if rollback else (old, new)
    with pytest.raises(UpgradeError, match="policy-aware rollback baseline"):
        validate_candidate(
            incoming,
            active_root=active,
            approve_runtime_migration=True,
            allow_openclaw_rollback=rollback,
        )


def test_guard_aware_predecessor_can_precede_activation_and_receive_rollback(
    tmp_path: Path,
) -> None:
    predecessor = _release(tmp_path / "guard-aware", supports=True, activates=False)
    candidate = _release(tmp_path / "candidate", supports=True, activates=True)
    for active, incoming in ((predecessor, candidate), (candidate, predecessor)):
        result = validate_candidate(incoming, active_root=active)
        assert result["communication_intake_policies"] == [POLICY]


def test_unrelated_legacy_release_validation_does_not_invent_a_policy(
    tmp_path: Path,
) -> None:
    active = _release(tmp_path / "legacy-active", supports=False, activates=False)
    incoming = _release(tmp_path / "legacy-next", supports=False, activates=False)
    assert (
        validate_candidate(incoming, active_root=active)[
            "communication_intake_policies"
        ]
        == []
    )


def test_interrupted_cutover_cannot_recover_to_a_runtime_that_resumes_mining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    owner = ActivationJournal(paths)
    value = _journal_value(paths)
    _release(Path(str(value["predecessor_path"])), supports=False, activates=False)
    _release(Path(str(value["target_path"])), supports=True, activates=True)
    owner.create(value)
    before = owner.path.read_bytes()
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsafe recovery must be refused before stopping services")

    monkeypatch.setattr(manager.services, "stop", forbidden)
    with pytest.raises(UpgradeError, match="policy-aware rollback baseline"):
        manager._recover_interrupted_transaction()
    assert owner.path.read_bytes() == before
