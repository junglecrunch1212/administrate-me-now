"""Restore documented pre-existing readiness gaps without declaring them ready."""

from __future__ import annotations

import re
from typing import Any

_ATTESTATION_REFRESH_DIMENSIONS = frozenset(
    {
        "attestation_binding",
        "service_account_recovery",
        "identity_acceptance",
        "backup_rollback",
        "observation_effects",
    }
)


def _safe_runtime(diagnostics: Any) -> bool:
    # Journal schema validation must remain independent of application imports.
    from bootstrap.htr10_export import _runtime_supports_read_only_attestation_refresh

    return _runtime_supports_read_only_attestation_refresh(diagnostics)


BASELINE_CONTROLS = (
    "deployment.json",
    "instance.yaml",
    "member-mapping.json",
    "adapters.json",
    "secret-references.json",
    "settings-control.json",
    "disposable-test-dataset.json",
)

CONTRACT = "administrateme-recovery-readiness-baseline-v1"
READINESS_CHECK = "deployment_topology_and_recovery_readiness"


def validate_baseline(value: Any, readiness_sha256: str | None) -> None:
    if not isinstance(value, dict) or set(value) != {
        "contract",
        "blocking_dimensions",
        "readiness_sha256",
        "host_fingerprint",
        "instance_fingerprint",
        "control_sha256",
    }:
        raise ValueError("recovery readiness baseline schema is invalid")
    controls = value["control_sha256"]
    if (
        not isinstance(controls, dict)
        or set(controls) != set(BASELINE_CONTROLS)
        or any(
            digest is not None
            and (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            )
            for digest in controls.values()
        )
    ):
        raise ValueError("recovery readiness control digests are invalid")
    blockers = value["blocking_dimensions"]
    if (
        value["contract"] != CONTRACT
        or value["readiness_sha256"] != readiness_sha256
        or not isinstance(blockers, list)
        or not blockers
        or any(not isinstance(name, str) for name in blockers)
        or blockers != sorted(set(blockers))
        or not set(blockers) <= _ATTESTATION_REFRESH_DIMENSIONS
        or any(
            not isinstance(value[name], str)
            or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None
            for name in ("readiness_sha256", "host_fingerprint", "instance_fingerprint")
        )
    ):
        raise ValueError("recovery readiness baseline is invalid")


def capture_baseline(
    diagnostics: Any,
    *,
    readiness_sha256: str | None,
    host_fingerprint: str,
    instance_fingerprint: str,
    control_sha256: dict[str, str | None],
) -> dict[str, Any] | None:
    if readiness_sha256 is None or not _safe_runtime(diagnostics):
        return None
    value = {
        "contract": CONTRACT,
        "blocking_dimensions": sorted(
            diagnostics["deployment_readiness"]["blocking_dimensions"]
        ),
        "readiness_sha256": readiness_sha256,
        "host_fingerprint": host_fingerprint,
        "instance_fingerprint": instance_fingerprint,
        "control_sha256": control_sha256,
    }
    validate_baseline(value, readiness_sha256)
    return value


def runtime_matches_baseline(baseline: dict[str, Any], diagnostics: Any) -> bool:
    return bool(
        _safe_runtime(diagnostics)
        and set(diagnostics["deployment_readiness"]["blocking_dimensions"])
        <= set(baseline["blocking_dimensions"])
    )


def full_doctor_matches_baseline(result: dict[str, Any]) -> bool:
    """Every other full Doctor check must pass; readiness remains failed."""
    checks = result.get("checks")
    if (
        result.get("status") != "failed"
        or not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("ok"), bool)
            or not isinstance(row.get("name"), str)
            for row in checks
        )
    ):
        return False
    by_name = {row["name"]: row for row in checks}
    if len(by_name) != len(checks) or not {
        READINESS_CHECK,
        "launchd_and_process_state",
        "source_assurance_terminal_dispositions",
    } <= set(by_name):
        return False
    failed = {name for name, row in by_name.items() if row["ok"] is not True}
    if (
        not {READINESS_CHECK}
        <= failed
        <= {READINESS_CHECK, "launchd_and_process_state"}
    ):
        return False
    if "launchd_and_process_state" in failed:
        evidence = by_name["launchd_and_process_state"].get("evidence") or {}
        if not isinstance(evidence, dict):
            return False
        definitions = evidence.get("definitions") or {}
        services = evidence.get("services") or {}
        if (
            not isinstance(definitions, dict)
            or set(definitions)
            != {"com.administrateme.runtime", "com.administrateme.openclaw"}
            or any(value is not True for value in definitions.values())
            or not isinstance(services, dict)
            or services.get("runtime")
            not in {"healthy", "observation_only", "degraded"}
            or services.get("openclaw") != "running"
        ):
            return False
    return True
