"""Production runtime facts govern every direction of the staged upgrade chain.

Only temporary fixture sources, encrypted state, and the test secret provider
are used. The updater reads the same log written by the production application;
no manifest assertion substitutes for the durable post-cutover epoch.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.communication_intake_policy import POLICY_ID, POLICY_REASON
from adminme.runtime.application import create_application
from adminme.settings_control import SettingsControlStore
from bootstrap.activation_transaction import ActivationJournal
from bootstrap.communication_intake import required_instance_policies
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.secrets import secret_store
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.acceptance.test_explicit_communication_intake_policy import (
    ROOT,
    _events,
    _preload,
    _settings,
    _settle,
    _triage_rows,
)
from tests.fixtures.communication_intake_release import communication_intake_release
from tests.unit.bootstrap.test_activation_transaction import _journal_value
from tests.unit.bootstrap.test_communication_intake_upgrade_safety import _release


def test_runtime_cutover_allows_precursor_rollback_but_refuses_second_rollback_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    releases = paths.app_dir / "releases"
    releases.mkdir(mode=0o700)
    old = _release(releases / ("1" * 40), supports=False, activates=False)
    precursor = communication_intake_release(
        ROOT, releases / ("2" * 40), "rollback-prerequisite"
    )
    cutover = communication_intake_release(ROOT, releases / ("3" * 40), "cutover")
    before = _settings(paths.instance_dir, repository_root=precursor)
    atomic_write(
        paths.instance_dir / "config" / "instance.yaml",
        f"tenant_id: {before.instance.tenant_id}\ntimezone: Etc/UTC\n".encode(),
    )
    reference = secret_store(paths, test_mode=True).put(
        "event-log", before.encryption_key
    )
    atomic_write(
        paths.instance_dir / "config" / "secret-references.json",
        json.dumps({"references": {"event_log": reference}}).encode(),
    )
    settings_path = paths.instance_dir / "config" / "settings-control.json"
    atomic_write(
        settings_path, json.dumps(SettingsControlStore(settings_path).read()).encode()
    )
    atomic_write(paths.instance_dir / "config" / "adapters.json", b'{"adapters":[]}')
    asyncio.run(_preload(before))
    manager = UpgradeManager(paths, root=ROOT, test_mode=True)

    # Precursor has no default: genuine absence of the encrypted epoch admits
    # the old -> precursor transition and its pre-cutover reversal.
    assert required_instance_policies(paths, test_mode=True) == frozenset()
    manager._validate_instance_candidate(precursor, active_root=old)
    manager._validate_instance_candidate(old, active_root=precursor)
    application = create_application(before)
    with TestClient(application) as client:
        runtime = application.state.runtime
        client.portal.call(_settle, runtime)
        rows = client.portal.call(_triage_rows, runtime)
        assert rows["untouched"]["reason"] != POLICY_REASON
        assert (
            client.portal.call(_events, runtime, "communication_intake.policy_applied")
            == []
        )
    assert required_instance_policies(paths, test_mode=True) == frozenset()

    manager._validate_instance_candidate(cutover, active_root=precursor)
    after = _settings(paths.instance_dir, repository_root=cutover)
    application = create_application(after)
    with TestClient(application) as client:
        runtime = application.state.runtime
        client.portal.call(_settle, runtime)
        retired = client.portal.call(_triage_rows, runtime)
        assert retired["untouched"]["reason"] == POLICY_REASON
        assert retired["member-edited"]["title"] == "Member retained fixture work"
        [epoch] = client.portal.call(
            _events, runtime, "communication_intake.policy_applied"
        )
    assert required_instance_policies(paths, test_mode=True) == frozenset({POLICY_ID})
    manager._validate_instance_candidate(precursor, active_root=cutover)

    # Start the actual prerequisite composition against the cutover log. It
    # honors retirement even though its own manifest still has no default.
    application = create_application(before)
    with TestClient(application) as client:
        runtime = application.state.runtime
        client.portal.call(_settle, runtime)
        assert client.portal.call(_triage_rows, runtime) == retired
        assert client.portal.call(
            _events, runtime, "communication_intake.policy_applied"
        ) == [epoch]
        assert (
            client.portal.call(
                _events, runtime, "skill.call.recorded", "skill.call.failed"
            )
            == []
        )
    assert required_instance_policies(paths, test_mode=True) == frozenset({POLICY_ID})
    pointer = paths.app_dir / "current-release.json"
    atomic_write(
        pointer,
        json.dumps(
            {
                "path": str(precursor),
                "commit": "2" * 40,
                "previous": {"path": str(old), "commit": "1" * 40},
            }
        ).encode(),
    )
    pointer_before = pointer.read_bytes()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a second rollback must fail before stopping the runtime")

    monkeypatch.setattr(manager.services, "stop", forbidden)
    with pytest.raises(UpgradeError, match="policy-aware rollback baseline"):
        manager.rollback()
    assert pointer.read_bytes() == pointer_before

    # A stale activation journal cannot recover around the same durable fact.
    owner = ActivationJournal(paths)
    old_manifest = (old / "bootstrap" / "versions.json").read_bytes()
    precursor_manifest = (precursor / "bootstrap" / "versions.json").read_bytes()
    journal = _journal_value(paths)
    # The generic journal fixture installs current manifests; restore the exact
    # two fixture release roles before exercising production recovery.
    (old / "bootstrap" / "versions.json").write_bytes(old_manifest)
    (precursor / "bootstrap" / "versions.json").write_bytes(precursor_manifest)
    owner.create(journal)
    journal_before = owner.path.read_bytes()
    with pytest.raises(UpgradeError, match="policy-aware rollback baseline"):
        manager._recover_interrupted_transaction()
    assert owner.path.read_bytes() == journal_before
    assert pointer.read_bytes() == pointer_before
    assert required_instance_policies(paths, test_mode=True) == frozenset({POLICY_ID})
