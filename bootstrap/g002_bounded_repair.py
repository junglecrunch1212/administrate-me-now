"""Exact, content-free admission for one G-002 degraded-baseline repair.

This module does not decide whether a pull request is eligible for G-002.  The
Outcome Harness does that before merge.  It binds the resulting activation to
the exact merge commit and independently proves that the live degraded baseline
and post-switch health sets match the admitted monotonic repair envelope.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class G002BoundedRepairError(RuntimeError):
    pass


class G002BoundedRepairPolicy(BaseModel):
    model_config = {"extra": "forbid"}

    contract: Literal["g002-bounded-repair-activation-v1"]
    pull_request: int = Field(ge=1)
    merge_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    merge_base_parent: str = Field(pattern=r"^[0-9a-f]{40}$")
    reviewed_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    admission_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_mode_required: Literal[True]
    provider_claims_required: Literal[0]
    baseline_required_unhealthy: list[str] = Field(min_length=1)
    target_degradations: list[str] = Field(min_length=1)
    baseline_failed_doctor_checks: list[str] = Field(min_length=1)
    target_doctor_checks: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_exact_subsets_and_unique_ids(self) -> "G002BoundedRepairPolicy":
        collections = (
            self.baseline_required_unhealthy,
            self.target_degradations,
            self.baseline_failed_doctor_checks,
            self.target_doctor_checks,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("G-002 repair capability and Doctor IDs must be unique")
        if not set(self.target_degradations).issubset(
            self.baseline_required_unhealthy
        ):
            raise ValueError("G-002 repair targets must be in the unhealthy baseline")
        if not set(self.target_doctor_checks).issubset(
            self.baseline_failed_doctor_checks
        ):
            raise ValueError("G-002 target Doctor checks must be baseline failures")
        return self


class G002ObservationReleasePolicy(BaseModel):
    model_config = {"extra": "forbid"}

    contract: Literal["g002-observation-release-activation-v1"]
    pull_request: int = Field(ge=1)
    merge_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    merge_base_parent: str = Field(pattern=r"^[0-9a-f]{40}$")
    reviewed_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    admission_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_mode_required: Literal[True]
    provider_claims_required: Literal[0]
    baseline_required_unhealthy: list[str] = Field(max_length=0)
    baseline_failed_doctor_checks: list[str] = Field(max_length=0)


def load_g002_bounded_repair_policy(path: Path) -> G002BoundedRepairPolicy:
    try:
        value = json.loads(path.read_text("utf-8"))
        return G002BoundedRepairPolicy.model_validate(value)
    except Exception as exc:
        raise G002BoundedRepairError(
            "G-002 bounded-repair activation policy is invalid"
        ) from exc


def load_g002_observation_release_policy(
    path: Path,
) -> G002ObservationReleasePolicy:
    try:
        value = json.loads(path.read_text("utf-8"))
        return G002ObservationReleasePolicy.model_validate(value)
    except Exception as exc:
        raise G002BoundedRepairError(
            "G-002 observation-release activation policy is invalid"
        ) from exc


def admission_manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_candidate_identity(
    policy: G002BoundedRepairPolicy | G002ObservationReleasePolicy,
    candidate: Path,
) -> None:
    try:
        line = subprocess.check_output(
            ["git", "-C", str(candidate), "rev-list", "--parents", "-n", "1", "HEAD"],
            text=True,
            timeout=15,
        ).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise G002BoundedRepairError(
            "G-002 bounded repair requires an immutable Git merge checkout"
        ) from exc
    parts = line.split()
    expected = [
        policy.merge_commit,
        policy.merge_base_parent,
        policy.reviewed_head,
    ]
    if parts != expected:
        raise G002BoundedRepairError(
            "G-002 activation policy does not match the exact two-parent merge"
        )


def _failed_doctor_checks(doctor: dict[str, Any]) -> set[str]:
    checks = doctor.get("checks")
    if not isinstance(checks, list):
        raise G002BoundedRepairError("Doctor check evidence is unavailable")
    failed: set[str] = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise G002BoundedRepairError("Doctor check evidence is malformed")
        if check.get("ok") is not True:
            failed.add(check["name"])
    return failed


def _unhealthy_capabilities(diagnostics: dict[str, Any]) -> set[str]:
    capabilities = diagnostics.get("capabilities")
    if not isinstance(capabilities, dict):
        raise G002BoundedRepairError("runtime capability evidence is unavailable")
    unhealthy: set[str] = set()
    for capability_id, capability in capabilities.items():
        if not isinstance(capability_id, str) or not isinstance(capability, dict):
            raise G002BoundedRepairError("runtime capability evidence is malformed")
        if capability.get("state") in {"degraded", "blocked"}:
            unhealthy.add(capability_id)
    return unhealthy


def validate_g002_observation_boundary(
    doctor: dict[str, Any], diagnostics: dict[str, Any]
) -> None:
    if doctor.get("mode") != "observation":
        raise G002BoundedRepairError("Doctor does not attest observation mode")
    if diagnostics.get("observation_active") is not True:
        raise G002BoundedRepairError("runtime observation mode is not active")
    claims = doctor.get("live_provider_claims")
    if not isinstance(claims, list) or claims:
        raise G002BoundedRepairError("live provider claims are not exactly empty")


def validate_g002_repair_baseline(
    policy: G002BoundedRepairPolicy,
    *,
    doctor: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    validate_g002_observation_boundary(doctor, diagnostics)
    unhealthy = _unhealthy_capabilities(diagnostics)
    failed = _failed_doctor_checks(doctor)
    if unhealthy != set(policy.baseline_required_unhealthy):
        raise G002BoundedRepairError(
            "live unhealthy capability set differs from G-002 admission"
        )
    if failed != set(policy.baseline_failed_doctor_checks):
        raise G002BoundedRepairError(
            "live Doctor failure set differs from G-002 admission"
        )
    return {
        "required_unhealthy": sorted(unhealthy),
        "failed_doctor_checks": sorted(failed),
        "observation_active": True,
        "provider_claims": 0,
    }


def validate_g002_observation_baseline(
    policy: G002ObservationReleasePolicy,
    *,
    doctor: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    validate_g002_observation_boundary(doctor, diagnostics)
    unhealthy = _unhealthy_capabilities(diagnostics)
    failed = _failed_doctor_checks(doctor)
    if (
        doctor.get("status") != "healthy"
        or unhealthy
        or failed
        or policy.baseline_required_unhealthy
        or policy.baseline_failed_doctor_checks
    ):
        raise G002BoundedRepairError(
            "G-002 observation release requires an exactly healthy live baseline"
        )
    return {
        "required_unhealthy": [],
        "failed_doctor_checks": [],
        "observation_active": True,
        "provider_claims": 0,
    }


def validate_g002_repair_settlement(
    policy: G002BoundedRepairPolicy,
    *,
    doctor: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    validate_g002_observation_boundary(doctor, diagnostics)
    analysis = analyze_g002_repair_settlement(
        policy, doctor=doctor, diagnostics=diagnostics
    )
    reasons: list[str] = []
    if analysis["unresolved_targets"]:
        reasons.append(
            "unresolved capabilities=" + ",".join(analysis["unresolved_targets"])
        )
    if analysis["disallowed_capabilities"]:
        reasons.append(
            "disallowed capabilities="
            + ",".join(analysis["disallowed_capabilities"])
        )
    if analysis["unresolved_target_checks"]:
        reasons.append(
            "unresolved Doctor checks="
            + ",".join(analysis["unresolved_target_checks"])
        )
    if analysis["disallowed_doctor_checks"]:
        reasons.append(
            "disallowed Doctor checks="
            + ",".join(analysis["disallowed_doctor_checks"])
        )
    if reasons:
        raise G002BoundedRepairError(
            "G-002 bounded repair did not settle monotonically: " + "; ".join(reasons)
        )
    return {
        "required_unhealthy": analysis["required_unhealthy"],
        "failed_doctor_checks": analysis["failed_doctor_checks"],
        "observation_active": True,
        "provider_effects_delta": 0,
    }


def analyze_g002_repair_settlement(
    policy: G002BoundedRepairPolicy,
    *,
    doctor: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, list[str]]:
    """Separate retryable target settlement from immediate boundary failures."""

    post_unhealthy = _unhealthy_capabilities(diagnostics)
    post_failed = _failed_doctor_checks(doctor)
    baseline_unhealthy = set(policy.baseline_required_unhealthy)
    targets = set(policy.target_degradations)
    baseline_failed = set(policy.baseline_failed_doctor_checks)
    target_checks = set(policy.target_doctor_checks)

    unresolved_targets = sorted(targets & post_unhealthy)
    new_or_disallowed = sorted(post_unhealthy - (baseline_unhealthy - targets))
    unresolved_target_checks = sorted(target_checks & post_failed)
    new_or_disallowed_checks = sorted(
        post_failed - (baseline_failed - target_checks)
    )
    return {
        "required_unhealthy": sorted(post_unhealthy),
        "failed_doctor_checks": sorted(post_failed),
        "unresolved_targets": unresolved_targets,
        "disallowed_capabilities": new_or_disallowed,
        "unresolved_target_checks": unresolved_target_checks,
        "disallowed_doctor_checks": new_or_disallowed_checks,
    }


__all__ = [
    "G002BoundedRepairError",
    "G002BoundedRepairPolicy",
    "G002ObservationReleasePolicy",
    "analyze_g002_repair_settlement",
    "admission_manifest_sha256",
    "load_g002_bounded_repair_policy",
    "load_g002_observation_release_policy",
    "validate_candidate_identity",
    "validate_g002_observation_boundary",
    "validate_g002_observation_baseline",
    "validate_g002_repair_baseline",
    "validate_g002_repair_settlement",
]
