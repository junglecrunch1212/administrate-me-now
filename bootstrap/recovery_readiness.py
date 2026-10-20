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
        and recovery_consumers_settled(diagnostics)
        and diagnostics["capabilities"]["policy:source-assurance"]["details"][
            "freshness"
        ].get("refresh_in_progress")
        is False
        and set(diagnostics["deployment_readiness"]["blocking_dimensions"])
        <= set(baseline["blocking_dimensions"])
    )


def _recovery_consumers(diagnostics: Any) -> list[dict[str, Any]] | None:
    if not isinstance(diagnostics, dict):
        return None
    capabilities = diagnostics.get("capabilities")
    if not isinstance(capabilities, dict) or any(
        not isinstance(key, str) for key in capabilities
    ):
        return None
    consumers = []
    for prefix, kind in (
        ("projection:", "projection"),
        ("pipeline:", "reactive_pipeline"),
    ):
        rows = [row for key, row in capabilities.items() if key.startswith(prefix)]
        if not rows:
            return None
        for row in rows:
            if not isinstance(row, dict) or row.get("kind") != kind:
                return None
            details = row.get("details")
            if not isinstance(details, dict):
                return None
            lag = details.get("lag_count")
            version = details.get("version")
            checkpoint = details.get("checkpoint_event_id")
            if (
                type(lag) is not int
                or lag < 0
                or "checkpoint_event_id" not in details
                or not (
                    (type(version) is int and version > 0)
                    or (isinstance(version, str) and bool(version.strip()))
                )
                or not (
                    checkpoint is None
                    or (isinstance(checkpoint, str) and bool(checkpoint.strip()))
                )
                or details.get("poison_event_id")
                or type(details.get("consecutive_failures")) is not int
                or details["consecutive_failures"] != 0
            ):
                return None
            consumers.append(row)
    return consumers


def recovery_consumers_settled(diagnostics: Any) -> bool:
    """A current audit cannot settle recovery ahead of its consumer checkpoints."""
    consumers = _recovery_consumers(diagnostics)
    return consumers is not None and all(
        row["details"]["lag_count"] == 0 for row in consumers
    )


def recovery_settlement_pending(baseline: dict[str, Any], diagnostics: Any) -> bool:
    """Allow waiting for replay/audit only; this predicate never accepts recovery."""
    from bootstrap.htr10_export import _INTEGRITY_ZERO_COUNTS

    if (
        not isinstance(diagnostics, dict)
        or any(
            diagnostics.get(key) is not expected
            for key, expected in (
                ("ready", False),
                ("service_ready", True),
                ("safe_reads", True),
                ("event_ingest_ready", True),
                ("observation_active", True),
            )
        )
        or diagnostics.get("status") not in {"healthy", "observation_only", "degraded"}
        or diagnostics.get("write_admission") != "blocked"
        or diagnostics.get("startup_error") is not None
    ):
        return False
    readiness = diagnostics.get("deployment_readiness")
    if not isinstance(readiness, dict):
        return False
    blockers = readiness.get("blocking_dimensions")
    if (
        readiness.get("contract") != "deployment-readiness-v1"
        or readiness.get("ready") is not False
        or not isinstance(blockers, list)
        or not blockers
        or any(not isinstance(key, str) for key in blockers)
        or len(blockers) != len(set(blockers))
        or not set(blockers)
        <= set(baseline["blocking_dimensions"]) | {"observation_effects"}
    ):
        return False
    if _recovery_consumers(diagnostics) is None:
        return False
    capabilities = diagnostics["capabilities"]
    for key, row in capabilities.items():
        if not isinstance(row, dict):
            return False
        if row.get("state") not in {"healthy", "observation_only", "disabled"} and not (
            key
            in {
                "policy:source-assurance",
                "product:member",
                "surface:triage-processing",
            }
            and row.get("state") == "degraded"
        ):
            return False
    source = (capabilities.get("policy:source-assurance") or {}).get("details") or {}
    if not isinstance(source, dict) or source.get("state") not in {
        "healthy",
        "degraded",
    }:
        return False
    freshness = source.get("freshness") or {}
    counts = source.get("counts") or {}
    effects = source.get("provider_effects") or {}
    if not all(isinstance(value, dict) for value in (freshness, counts, effects)):
        return False
    window = effects.get("observation_window") or {}
    effect_counts = effects.get("counts") or {}
    if not isinstance(window, dict) or not isinstance(effect_counts, dict):
        return False
    window_counts = window.get("counts") or {}
    if not isinstance(window_counts, dict):
        return False
    return bool(
        freshness.get("state") in {"fresh", "stale"}
        and type(freshness.get("revision_current")) is bool
        and type(freshness.get("refresh_in_progress")) is bool
        and freshness.get("refresh_error_type") is None
        and all(
            type(counts.get(name)) is int and counts[name] == 0
            for name in ("failed", "missing")
        )
        and type(counts.get("pending")) is int
        and counts["pending"] >= 0
        and effects.get("state") == "healthy"
        and window.get("state") == "healthy"
        and window.get("started_at_configured") is True
        and all(effect_counts.get(name) == 0 for name in _INTEGRITY_ZERO_COUNTS)
        and all(window_counts.get(name) == 0 for name in _INTEGRITY_ZERO_COUNTS)
        and window_counts.get("provider_called_true") == 0
        and window_counts.get("suppressed_false_or_unknown") == 0
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
