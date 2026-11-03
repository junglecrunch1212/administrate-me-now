"""Current tooling policy and one source-bound retained Doctor correction.

This does not rerun a predecessor with successor domain code. Its complete
original report remains available; only the reviewed Homebrew-6 ceiling can be
superseded by the current pure tooling predicate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.source_identity import (
    reviewed_candidate_commit,
    verify_release_source_identity,
)

TOOLING_POLICY = "homebrew-6-or-7-v1"
RETAINED_DOCTORS = frozenset(
    {
        "471d20ec71d40d59490ff763be524d73a3c3cb7d",
        "02c2957acc4e659a19720db5bfb296ebba710bbd",
    }
)
# Both immutable predecessors contain this exact Doctor implementation.
RETAINED_DOCTOR_SHA256 = (
    "1047f1de676c8bdf1ef04c2aa573cd7b974a21b1742fce40fa23aa7a6279b53c"
)
COMPATIBILITY_KEY = "retained_doctor_tooling_compatibility"

# Exact report contract of the two source-verified predecessors. A partial
# report cannot qualify even if its only visible failure is the known check.
RETAINED_CHECKS = frozenset(
    {
        "platform",
        "runtime_and_package_versions",
        "reviewed_openclaw_source",
        "validated_configuration",
        "bootstrap_sections",
        "secure_secret_references",
        "encrypted_event_log_and_migrations",
        "fifteen_projections_and_checkpoints",
        "required_reactive_pipeline_delivery",
        "workbook_forward_and_reverse_state",
        "adapter_categories_and_capabilities",
        "openclaw_compatibility_and_plugin_discovery",
        "openclaw_cron_reconciliation",
        "member_portal_domain_and_chat_health",
        "launchd_and_process_state",
        "source_assurance_terminal_dispositions",
        "deployment_topology_and_recovery_readiness",
        "ports_and_loopback_binding",
        "member_mapping_and_bridge_status",
        "observation_mode_and_trust_workflow",
        "disk_space_and_backup_target",
    }
)


def _version(value: str) -> tuple[int, int, int]:
    parts = value.strip().lstrip("v").split()
    if not parts:
        return (-1, -1, -1)
    try:
        numbers = [int(part) for part in parts[0].split(".")[:3]]
    except ValueError:
        return (-1, -1, -1)
    return tuple((numbers + [0, 0, 0])[:3])  # type: ignore[return-value]


def tooling_versions_supported(
    observed: dict[str, Any],
    openclaw_version: str,
    *,
    node_compatible: str | None = None,
) -> bool:
    if set(observed) != {"python", "node", "npm", "homebrew", "openclaw"} or any(
        not isinstance(value, str) for value in observed.values()
    ):
        return False
    if node_compatible is None:
        versions = load_versions()
        if openclaw_version == versions["openclaw"]["version"]:
            node_compatible = versions["node"]["compatible"]
        elif openclaw_version == "2026.7.2":
            # Preserve the reviewed retained-report rule. Changing Doctor's
            # source itself would also change maintenance source admission.
            node_compatible = ">=24.15,<25"
        else:
            return False
    python, node, npm = (_version(observed[key]) for key in ("python", "node", "npm"))
    node_bounds = re.fullmatch(r">=(\d+(?:\.\d+){1,2}),<(\d+)", node_compatible)
    if node_bounds is None:
        return False
    brew = (
        _version(observed["homebrew"].splitlines()[0].replace("Homebrew", "").strip())
        if observed["homebrew"].strip()
        else (-1, -1, -1)
    )
    return (
        python[:2] == (3, 14)
        and node[0] == 24
        and _version(node_bounds[1]) <= node < (int(node_bounds[2]), 0, 0)
        and npm[0] == 11
        and npm >= (11, 6, 0)
        and brew[0] in {6, 7}
        and bool(openclaw_version)
        and openclaw_version in observed["openclaw"]
    )


def verify_retained_tooling_source(
    paths: DeploymentPaths, release: Path, controller: Path
) -> str:
    if (
        release.name not in RETAINED_DOCTORS
        or release != paths.app_dir / "releases" / release.name
    ):
        raise ValueError("retained Doctor tooling source is not qualified")
    controller_commit = reviewed_candidate_commit(controller)
    verify_release_source_identity(
        release,
        repository=controller,
        commit=release.name,
        retained_by_commit=controller_commit,
        allow_legacy_tree=False,
        require_tracked_tree=True,
    )
    if (
        hashlib.sha256((release / "bootstrap/doctor.py").read_bytes()).hexdigest()
        != RETAINED_DOCTOR_SHA256
    ):
        raise ValueError("retained Doctor tooling predicate source differs")
    return controller_commit


def retained_tooling_report(
    paths: DeploymentPaths,
    release: Path,
    controller: Path,
    original: dict[str, Any],
    *,
    include_synthetic_proof: bool,
) -> dict[str, Any]:
    """Independently decide the sole known tooling rejection, never other faults."""
    if original.get("status") != "failed" or release.name not in RETAINED_DOCTORS:
        return original
    if (
        type(original.get("schema_version")) is not int
        or original["schema_version"] != 1
        or original.get("mode") != "observation"
        or original.get("safe_to_attach") is not True
        or original.get("live_provider_claims") != []
        or COMPATIBILITY_KEY in original
    ):
        return original
    checks = original.get("checks")
    if not isinstance(checks, list) or not checks:
        return original
    by_name: dict[str, dict[str, Any]] = {}
    for check in checks:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("name"), str)
            or type(check.get("ok")) is not bool
            or check["name"] in by_name
        ):
            return original
        by_name[check["name"]] = check
    expected = RETAINED_CHECKS | (
        {"canonical_synthetic_round_trip"} if include_synthetic_proof else set()
    )
    if set(by_name) != expected:
        return original
    failed = {name for name, check in by_name.items() if check["ok"] is False}
    if failed != {"runtime_and_package_versions"}:
        return original
    observed = by_name["runtime_and_package_versions"].get("evidence")
    if not isinstance(observed, dict):
        return original
    versions = load_versions(release)
    if not tooling_versions_supported(observed, versions["openclaw"]["version"]):
        return original
    if (
        _version(observed["homebrew"].splitlines()[0].replace("Homebrew", "").strip())[
            0
        ]
        != 7
    ):
        return original
    # Only the actual named selected/retained release may use this decision.
    if release != paths.app_dir / "releases" / release.name:
        return original
    controller_commit = verify_retained_tooling_source(paths, release, controller)
    result = copy.deepcopy(original)
    replacement = next(
        check
        for check in result["checks"]
        if check["name"] == "runtime_and_package_versions"
    )
    replacement.update(
        ok=True,
        detail="current protected tooling policy accepts the retained observed versions",
    )
    result["status"] = "healthy"
    result[COMPATIBILITY_KEY] = {
        "contract": "retained-doctor-tooling-compatibility-v1",
        "policy": TOOLING_POLICY,
        "controller_commit": controller_commit,
        "release_commit": release.name,
        "doctor_source_sha256": RETAINED_DOCTOR_SHA256,
        "original_report_sha256": hashlib.sha256(
            json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "original_report": copy.deepcopy(original),
        "superseded_check": "runtime_and_package_versions",
        "original_doctor_status": "failed",
        "decision": "compatible",
    }
    return result
