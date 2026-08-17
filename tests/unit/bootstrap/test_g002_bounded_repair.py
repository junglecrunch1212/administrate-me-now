from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from bootstrap.cli import main as bootstrap_main
from bootstrap.g002_bounded_repair import (
    G002BoundedRepairError,
    G002BoundedRepairPolicy,
    G002ObservationReleasePolicy,
    load_g002_bounded_repair_policy,
    validate_candidate_identity,
    validate_g002_repair_baseline,
    validate_g002_repair_settlement,
    validate_g002_observation_baseline,
)


def _policy(**changes: object) -> G002BoundedRepairPolicy:
    value: dict[str, object] = {
        "contract": "g002-bounded-repair-activation-v1",
        "pull_request": 341,
        "merge_commit": "3" * 40,
        "merge_base_parent": "1" * 40,
        "reviewed_head": "2" * 40,
        "admission_manifest_sha256": "a" * 64,
        "observation_mode_required": True,
        "provider_claims_required": 0,
        "baseline_required_unhealthy": [
            "adapter:calendar",
            "pipeline:noise",
        ],
        "target_degradations": ["adapter:calendar"],
        "baseline_failed_doctor_checks": [
            "adapter_instances",
            "source_assurance_terminal_dispositions",
        ],
        "target_doctor_checks": ["adapter_instances"],
    }
    value.update(changes)
    return G002BoundedRepairPolicy.model_validate(value)


def _doctor(*failed: str) -> dict[str, object]:
    return {
        "status": "failed" if failed else "healthy",
        "mode": "observation",
        "checks": [
            {"name": name, "ok": name not in failed}
            for name in (
                "adapter_instances",
                "source_assurance_terminal_dispositions",
                "canonical_synthetic_round_trip",
            )
        ],
        "live_provider_claims": [],
    }


def _diagnostics(*unhealthy: str) -> dict[str, object]:
    all_ids = {"adapter:calendar", "pipeline:noise", "projection:tasks"}
    return {
        "status": "degraded" if unhealthy else "observation_only",
        "ready": True,
        "observation_active": True,
        "capabilities": {
            capability_id: {
                "state": "degraded" if capability_id in unhealthy else "healthy"
            }
            for capability_id in sorted(all_ids)
        },
    }


def test_policy_requires_unique_exact_target_subsets(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_policy().model_dump()), "utf-8")
    assert load_g002_bounded_repair_policy(path).target_degradations == [
        "adapter:calendar"
    ]

    with pytest.raises(ValueError, match="unhealthy baseline"):
        _policy(target_degradations=["adapter:missing"])
    with pytest.raises(ValueError, match="must be unique"):
        _policy(target_degradations=["adapter:calendar", "adapter:calendar"])


def test_baseline_must_match_exact_capability_doctor_and_observation_sets() -> None:
    policy = _policy()
    result = validate_g002_repair_baseline(
        policy,
        doctor=_doctor("adapter_instances", "source_assurance_terminal_dispositions"),
        diagnostics=_diagnostics("adapter:calendar", "pipeline:noise"),
    )
    assert result["required_unhealthy"] == ["adapter:calendar", "pipeline:noise"]

    with pytest.raises(G002BoundedRepairError, match="capability set differs"):
        validate_g002_repair_baseline(
            policy,
            doctor=_doctor(
                "adapter_instances", "source_assurance_terminal_dispositions"
            ),
            diagnostics=_diagnostics("adapter:calendar"),
        )
    changed = _diagnostics("adapter:calendar", "pipeline:noise")
    changed["observation_active"] = False
    with pytest.raises(G002BoundedRepairError, match="observation mode"):
        validate_g002_repair_baseline(
            policy,
            doctor=_doctor(
                "adapter_instances", "source_assurance_terminal_dispositions"
            ),
            diagnostics=changed,
        )


def test_observation_release_policy_requires_exactly_healthy_baseline() -> None:
    policy = G002ObservationReleasePolicy.model_validate(
        {
            "contract": "g002-observation-release-activation-v1",
            "pull_request": 341,
            "merge_commit": "3" * 40,
            "merge_base_parent": "1" * 40,
            "reviewed_head": "2" * 40,
            "admission_manifest_sha256": "a" * 64,
            "observation_mode_required": True,
            "provider_claims_required": 0,
            "baseline_required_unhealthy": [],
            "baseline_failed_doctor_checks": [],
        }
    )
    assert (
        validate_g002_observation_baseline(
            policy, doctor=_doctor(), diagnostics=_diagnostics()
        )["required_unhealthy"]
        == []
    )
    with pytest.raises(G002BoundedRepairError, match="exactly healthy"):
        validate_g002_observation_baseline(
            policy,
            doctor=_doctor("source_assurance_terminal_dispositions"),
            diagnostics=_diagnostics("pipeline:noise"),
        )


def test_settlement_accepts_only_target_recovery_and_no_new_failure() -> None:
    policy = _policy()
    result = validate_g002_repair_settlement(
        policy,
        doctor=_doctor("source_assurance_terminal_dispositions"),
        diagnostics=_diagnostics("pipeline:noise"),
    )
    assert result == {
        "required_unhealthy": ["pipeline:noise"],
        "failed_doctor_checks": ["source_assurance_terminal_dispositions"],
        "observation_active": True,
        "provider_effects_delta": 0,
    }

    cases = [
        (
            _doctor("adapter_instances", "source_assurance_terminal_dispositions"),
            _diagnostics("adapter:calendar", "pipeline:noise"),
        ),
        (
            _doctor("source_assurance_terminal_dispositions"),
            _diagnostics("pipeline:noise", "projection:tasks"),
        ),
        (
            _doctor(
                "source_assurance_terminal_dispositions",
                "canonical_synthetic_round_trip",
            ),
            _diagnostics("pipeline:noise"),
        ),
    ]
    for doctor, diagnostics in cases:
        with pytest.raises(G002BoundedRepairError, match="did not settle"):
            validate_g002_repair_settlement(
                policy, doctor=doctor, diagnostics=diagnostics
            )


def test_candidate_identity_requires_exact_two_parent_merge(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=tmp_path, check=True)
    (tmp_path / "base").write_text("base\n", "utf-8")
    subprocess.run(["git", "add", "base"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    base_branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=tmp_path, text=True
    ).strip()
    subprocess.run(["git", "switch", "-qc", "candidate"], cwd=tmp_path, check=True)
    (tmp_path / "candidate").write_text("candidate\n", "utf-8")
    subprocess.run(["git", "add", "candidate"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=tmp_path, check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    subprocess.run(["git", "switch", "-q", base_branch], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "-qm", "merge", "candidate"],
        cwd=tmp_path,
        check=True,
    )
    merge = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()

    policy = _policy(
        merge_commit=merge,
        merge_base_parent=base,
        reviewed_head=head,
    )
    validate_candidate_identity(policy, tmp_path)
    with pytest.raises(G002BoundedRepairError, match="exact two-parent merge"):
        validate_candidate_identity(
            policy.model_copy(update={"reviewed_head": "f" * 40}), tmp_path
        )


def test_secret_free_runtime_diagnostics_export_is_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {
        "status": "observation_only",
        "ready": True,
        "observation_active": True,
        "capabilities": {},
    }
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    monkeypatch.setattr(
        "bootstrap.cli.ServiceManager.runtime_diagnostics",
        lambda _self: expected,
    )
    output = tmp_path / "diagnostics.json"

    assert (
        bootstrap_main(
            [
                "--instance-root",
                str(tmp_path / "instance"),
                "diagnostics",
                "--export",
                str(output),
            ]
        )
        == 0
    )
    stored = json.loads(output.read_text("utf-8"))
    assert stored.pop("observed_at").endswith("+00:00")
    assert stored == expected
