"""The production updater preserves recovery time before its guardian expires."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

import pytest

from bootstrap import service_settlement, upgrade
from bootstrap.contracts import DeploymentPaths
from bootstrap.deployment_budget import (
    CONTROLLER_SECONDS,
    FINALIZATION_RESERVE_SECONDS,
    RECOVERY_SECONDS,
    SETTLEMENT_SECONDS,
)
from bootstrap.services import ServiceManager
from bootstrap.upgrade import UpgradeError, UpgradeManager
from bootstrap.wizard import BootstrapWizard
from tests.acceptance.test_macos_bootstrap_operations import (
    _committed_candidate_from_worktree,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.mark.parametrize("preparation_exhausts_reserve", [False, True])
def test_candidate_deadline_recovers_verified_predecessor_before_guardian(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preparation_exhausts_reserve: bool,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    for name in (
        "ADMINME_UPDATE_SUPERVISED",
        "ADMINME_CONTROLLER_STARTED_MONOTONIC",
        "ADMINME_CONTROLLER_DEADLINE_MONOTONIC",
    ):
        monkeypatch.delenv(name, raising=False)
    paths = DeploymentPaths.from_root(tmp_path / "deployment-deadline")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    monkeypatch.setattr(
        UpgradeManager,
        "_attest_controller_provenance",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        upgrade,
        "reconcile_administrateme_plugin",
        lambda *_args, **_kwargs: {"ok": True},
    )
    UpgradeManager(paths, root=REPO_ROOT, test_mode=True).activate(REPO_ROOT)
    pointer_path = paths.app_dir / "current-release.json"
    predecessor = json.loads(pointer_path.read_text("utf-8"))
    predecessor_release = Path(predecessor["path"])
    services = ServiceManager(paths, test_mode=True)
    services.start()

    candidate = tmp_path / "candidate-deadline"
    candidate_commit = _committed_candidate_from_worktree(REPO_ROOT, candidate)
    manager = UpgradeManager(paths, root=candidate, test_mode=True)
    clock = _Clock()
    monkeypatch.setattr(upgrade, "time", clock)
    monkeypatch.setattr(service_settlement, "time", clock)
    monkeypatch.setattr(upgrade, "POST_SWITCH_POLL_SECONDS", 120)
    original_publish = manager._publish_release

    def publish(*args: Any, **kwargs: Any) -> Path:
        result = original_publish(*args, **kwargs)
        if preparation_exhausts_reserve:
            clock.sleep(
                CONTROLLER_SECONDS
                - SETTLEMENT_SECONDS
                - RECOVERY_SECONDS
                - FINALIZATION_RESERVE_SECONDS
                + 1
            )
        return result

    monkeypatch.setattr(manager, "_publish_release", publish)
    original_wait = service_settlement.wait_for_settled_doctor
    settlement_calls: list[dict[str, Any]] = []

    def production_wait(**kwargs: Any) -> dict[str, Any]:
        # Keep fixture services and encrypted bootstrap state. Only the shared
        # deadline policy follows its production waiting branch.
        kwargs["test_mode"] = False
        started = clock.now
        result = original_wait(**kwargs)
        settlement_calls.append(
            {
                "started": started,
                "finished": clock.now,
                "deadline": kwargs["absolute_deadline"],
                "result": result,
            }
        )
        return result

    monkeypatch.setattr(upgrade, "wait_for_settled_doctor", production_wait)
    original_doctor = manager._run_release_doctor
    original_diagnostics = manager.services.runtime_diagnostics
    actual_full_proofs: list[tuple[Path, float, dict[str, Any]]] = []
    recovery_started: float | None = None
    polls = 0

    def pending() -> bool:
        selected = json.loads(pointer_path.read_text("utf-8"))
        return selected["commit"] == candidate_commit or (
            recovery_started is not None
            and clock.now < recovery_started + RECOVERY_SECONDS - 30 * 60
        )

    def doctor(
        release: Path,
        *,
        include_synthetic_proof: bool,
        timeout_seconds: float = 120,
    ) -> dict[str, Any]:
        nonlocal recovery_started
        if release == predecessor_release and recovery_started is None:
            recovery_started = clock.now
        if pending():
            assert include_synthetic_proof is False
            return {
                "status": "failed",
                "checks": [
                    {"name": "source_assurance_terminal_dispositions", "ok": False}
                ],
            }
        result = original_doctor(
            release,
            include_synthetic_proof=include_synthetic_proof,
            timeout_seconds=timeout_seconds,
        )
        if include_synthetic_proof:
            actual_full_proofs.append((release, clock.now, result))
        return result

    def diagnostics() -> dict[str, Any]:
        nonlocal polls
        polls += 1
        result = copy.deepcopy(original_diagnostics())
        assert result is not None
        if pending():
            source = result["capabilities"]["policy:source-assurance"]
            source["state"] = "degraded"
            source["details"]["state"] = "degraded"
            source["details"]["counts"]["accounted"] = 0
            source["details"]["counts"]["pending"] = 1
        result["capabilities"]["pipeline:fixture-replay"] = {
            "kind": "reactive_pipeline",
            "state": "healthy",
            "details": {
                "version": "fixture-v1",
                "checkpoint_event_id": f"ev_fixture_{polls:08d}",
                "lag_count": 1 if pending() else 0,
                "consecutive_failures": 0,
                "poison_event_id": None,
            },
        }
        return result

    monkeypatch.setattr(manager, "_run_release_doctor", doctor)
    monkeypatch.setattr(manager.services, "runtime_diagnostics", diagnostics)
    failure = (
        "insufficient controller time before switch"
        if preparation_exhausts_reserve
        else "candidate failed post-switch Doctor"
    )
    with pytest.raises(UpgradeError, match=failure):
        manager.activate(candidate)

    assert json.loads(pointer_path.read_text("utf-8")) == predecessor
    assert services.status().status == "healthy"
    budget = manager._deployment_budget
    assert budget is not None
    assert budget.deadline - budget.started_at == CONTROLLER_SECONDS
    assert clock.now < budget.deadline - FINALIZATION_RESERVE_SECONDS
    if preparation_exhausts_reserve:
        assert settlement_calls == []
        assert actual_full_proofs == []
        assert manager._candidate_settlement_deadline is None
        return

    assert len(settlement_calls) == 2
    candidate_wait, recovery_wait = settlement_calls
    assert candidate_wait["result"]["status"] == "failed"
    assert any(
        check["name"] == "bounded_service_settlement" and not check["ok"]
        for check in candidate_wait["result"]["checks"]
    )
    assert candidate_wait["deadline"] - candidate_wait["started"] == SETTLEMENT_SECONDS
    assert recovery_wait["started"] >= candidate_wait["finished"]
    assert recovery_wait["deadline"] - recovery_wait["started"] == RECOVERY_SECONDS
    assert recovery_wait["result"]["status"] == "healthy"
    assert (
        recovery_wait["finished"] - recovery_wait["started"]
        >= RECOVERY_SECONDS - 30 * 60
    )
    assert len(actual_full_proofs) == 1
    release, proved_at, full_proof = actual_full_proofs[0]
    assert release == predecessor_release
    assert full_proof["status"] == "healthy"
    canonical = next(
        check
        for check in full_proof["checks"]
        if check["name"] == "canonical_synthetic_round_trip"
    )
    assert canonical["ok"] is True
    assert canonical["evidence"]["external_side_effects"] == 0
    assert canonical["evidence"]["review_suppressed_by_observation"] is True
    assert proved_at < recovery_wait["deadline"] < budget.deadline
    # A repeated recovery phase on this same manager cannot renew its allowance.
    prior_deadline = manager._recovery_settlement_deadline
    clock.sleep(60)
    manager._begin_recovery_settlement()
    assert manager._settlement_deadline == prior_deadline
