"""The shared-log intake cutover requires a rollback runtime that honors it."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from adminme.communication_intake_policy import POLICY_EVENT_KEY, POLICY_EVENT_TYPE
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from bootstrap.activation_transaction import ActivationJournal
from bootstrap.communication_intake import (
    CommunicationIntakeInspectionError,
    required_instance_policies,
)
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.secrets import secret_store
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
    application.pop("communication_intake_release_role", None)
    if supports:
        application["supported_communication_intake_policies"] = [POLICY]
        application["communication_intake_release_role"] = (
            "cutover" if activates else "rollback-prerequisite"
        )
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
    _initialize_log(paths, monkeypatch)
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


KEY = b"i" * 32


def _initialize_log(paths: DeploymentPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    atomic_write(
        paths.instance_dir / "config" / "instance.yaml",
        b"tenant_id: fixture-upgrade-intake\ntimezone: Etc/UTC\n",
    )
    reference = secret_store(paths, test_mode=True).put("event-log", KEY)
    atomic_write(
        paths.instance_dir / "config" / "secret-references.json",
        json.dumps({"references": {"event_log": reference}}).encode(),
    )

    async def create() -> None:
        log = EventLog(load_instance_config(paths.instance_dir), KEY)
        await log.close()

    asyncio.run(create())


def _append_epoch(paths: DeploymentPaths, *, actor: str | None = None) -> None:
    config = load_instance_config(paths.instance_dir)

    async def append() -> None:
        log = EventLog(config, KEY)
        try:
            await log.append(
                EventEnvelope(
                    event_at_ms=2_022_408_000_000,
                    tenant_id=config.tenant_id,
                    type=POLICY_EVENT_TYPE,
                    schema_version=1,
                    occurred_at="2034-02-01T10:00:00Z",
                    source_adapter="communication-intake-policy",
                    source_account_id="system",
                    actor_identity=actor or "system:communication-intake-policy",
                    owner_scope="shared:household",
                    visibility_scope="shared:household",
                    sensitivity="normal",
                    idempotency_key=POLICY_EVENT_KEY,
                    payload={
                        "policy_id": POLICY,
                        "previous_source_watermark": None,
                        "automatic_personal_obligations_enabled": False,
                    },
                )
            )
        finally:
            await log.close()

    asyncio.run(append())


def test_encrypted_epoch_survives_a_manifest_without_default_and_blocks_old_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    old = _release(tmp_path / "old", supports=False, activates=False)
    precursor = _release(tmp_path / "precursor", supports=True, activates=False)
    cutover = _release(tmp_path / "cutover", supports=True, activates=True)
    manager = UpgradeManager(paths, root=precursor, test_mode=True)
    assert required_instance_policies(paths, test_mode=True) == frozenset()
    # The prerequisite can be installed and backed out before retirement.
    manager._validate_instance_candidate(precursor, active_root=old)
    manager._validate_instance_candidate(old, active_root=precursor)
    manager._validate_instance_candidate(cutover, active_root=precursor)
    _append_epoch(paths)
    before = hashlib.sha256(
        (paths.instance_dir / "events" / "events.db").read_bytes()
    ).hexdigest()
    assert required_instance_policies(paths, test_mode=True) == frozenset({POLICY})
    manager._validate_instance_candidate(precursor, active_root=cutover)
    with pytest.raises(UpgradeError, match="policy-aware rollback baseline"):
        manager._validate_instance_candidate(old, active_root=precursor)
    assert (
        hashlib.sha256(
            (paths.instance_dir / "events" / "events.db").read_bytes()
        ).hexdigest()
        == before
    )


@pytest.mark.parametrize("failure", ["missing_log", "bad_key", "invalid_epoch"])
def test_unverifiable_epoch_refuses_before_candidate_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    if failure == "missing_log":
        (paths.instance_dir / "events" / "events.db").unlink()
    elif failure == "bad_key":
        secret_store(paths, test_mode=True).put("event-log", b"x" * 32)
    else:
        _append_epoch(paths, actor="fixture-imposter")
    release = _release(tmp_path / "precursor", supports=True, activates=False)
    manager = UpgradeManager(paths, root=release, test_mode=True)
    monkeypatch.setattr(manager, "_active_release_root", lambda: release)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("policy inspection must precede publication mutation")

    monkeypatch.setattr(manager, "_ensure_private_directory", forbidden)
    with pytest.raises(UpgradeError, match="durable communication intake policy"):
        manager._publish_release(release, "a" * 40)
    assert not (paths.app_dir / "releases" / ("a" * 40)).exists()
    if failure == "missing_log":
        assert not (paths.instance_dir / "events" / "events.db").exists()


def test_instance_inspection_rejects_symlinked_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    database = paths.instance_dir / "events" / "events.db"
    redirected = tmp_path / "other.db"
    database.rename(redirected)
    database.symlink_to(redirected)
    with pytest.raises(CommunicationIntakeInspectionError, match="cannot be verified"):
        required_instance_policies(paths, test_mode=True)


def test_recovery_rechecks_epoch_after_quiescence_before_restoring_old_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    _initialize_log(paths, monkeypatch)
    journal = ActivationJournal(paths)
    value = _journal_value(paths)
    _release(Path(str(value["predecessor_path"])), supports=False, activates=False)
    _release(Path(str(value["target_path"])), supports=True, activates=False)
    journal.create(value)
    manager = UpgradeManager(paths, root=tmp_path, test_mode=True)
    stops: list[str] = []

    def stop() -> None:
        stops.append("quiesced")
        _append_epoch(paths)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a raced epoch must block old runtime restoration")

    monkeypatch.setattr(manager.services, "stop", stop)
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", forbidden)
    monkeypatch.setattr(manager, "_restore_journal_control_files", forbidden)
    with pytest.raises(UpgradeError, match="policy-aware rollback baseline"):
        manager._recover_interrupted_transaction()
    assert stops == ["quiesced"]
    assert journal.load()["phase"] == "recovering"
    # Crash recovery cannot subsequently erase or bypass that fact.
    with pytest.raises(UpgradeError, match="policy-aware rollback baseline"):
        manager._recover_interrupted_transaction()
    assert stops == ["quiesced"]
