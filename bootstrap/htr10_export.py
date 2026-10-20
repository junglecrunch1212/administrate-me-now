"""Candidate-side, read-only HTR-10 audit export.

The active predecessor may not understand the prepared provider-binding
schema.  This command therefore reads the live event log without modifying it,
derives the exact candidate configuration in memory, and emits two private,
content-free audits at one immutable event watermark.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.events.log import EventLog
from adminme.historical_attribution import audit_historical_attribution
from adminme.provider_bindings import validate_provider_bindings
from adminme.source_assurance import audit_source_assurance
from adminme.service_identities import validate_service_identity_config
from bootstrap.contracts import DeploymentPaths, ensure_within
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    capture_deployment_snapshot,
    provider_effect_window_evidence,
)
from bootstrap.recovery_readiness import _ATTESTATION_REFRESH_DIMENSIONS
from bootstrap.provider_binding_transition import (
    NoEligibleProviderBindingTransitionError,
    prepare_live_provider_binding_transition,
)
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager, isolated_degraded_adapter_ids


class HTR10ExportError(RuntimeError):
    pass


_OPERATIONAL_DIMENSIONS = frozenset(
    {
        "topology",
        "provider_bindings",
        "openclaw",
        "bridges",
        "service_channel_identities",
    }
)
_INTEGRITY_ZERO_COUNTS = (
    "provider_called_unknown",
    "missing_terminal",
    "duplicate_terminal",
    "approval_correlation_missing",
    "duplicate_request",
    "malformed_request",
    "orphan_terminal",
    "invalid_legacy_reconciliation",
    "duplicate_legacy_reconciliation",
)


def _runtime_supports_read_only_attestation_refresh(diagnostics: object) -> bool:
    """Permit fresh audit collection, never activation, while attestations expire.

    Old runtimes may reject a historical observation origin solely because it
    is older than 31 days. Require their current zero-effect and operational
    proof, then recompute the full origin-bound audit with this candidate.
    No stale physical attestation becomes a successful check through this path.
    """

    if not isinstance(diagnostics, dict) or (
        diagnostics.get("ready") is not False
        or diagnostics.get("status") not in {"healthy", "observation_only"}
        or diagnostics.get("service_ready") is not True
        or diagnostics.get("safe_reads") is not True
        or diagnostics.get("event_ingest_ready") is not True
        or diagnostics.get("observation_active") is not True
        or diagnostics.get("write_admission") != "blocked"
        or diagnostics.get("startup_error") is not None
    ):
        return False
    readiness = diagnostics.get("deployment_readiness")
    if not isinstance(readiness, dict) or (
        readiness.get("contract") != "deployment-readiness-v1"
        or readiness.get("ready") is not False
    ):
        return False
    dimensions = readiness.get("dimensions")
    blockers = readiness.get("blocking_dimensions")
    if (
        not isinstance(dimensions, dict)
        or not (_ATTESTATION_REFRESH_DIMENSIONS | _OPERATIONAL_DIMENSIONS)
        <= set(dimensions)
        or not isinstance(blockers, list)
        or not blockers
        or not all(isinstance(name, str) for name in blockers)
        or len(blockers) != len(set(blockers))
        or not set(blockers) <= _ATTESTATION_REFRESH_DIMENSIONS
    ):
        return False
    observed_blockers: set[str] = set()
    for name, row in dimensions.items():
        if not isinstance(row, dict) or not isinstance(row.get("required"), bool):
            return False
        state = row.get("state")
        if name in _ATTESTATION_REFRESH_DIMENSIONS:
            if row["required"] is not True or state not in {"healthy", "blocked"}:
                return False
            if state == "blocked":
                observed_blockers.add(name)
        elif row["required"] is True:
            if state != "healthy":
                return False
        elif state not in {
            "healthy",
            "disabled",
            "not_applicable",
            "planned",
            "account_created",
            "channel_not_enrolled",
            "observation",
        }:
            return False
    if observed_blockers != set(blockers):
        return False
    capabilities = diagnostics.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        return False
    if not any(str(name).startswith("pipeline:") for name in capabilities) or not any(
        str(name).startswith("projection:") for name in capabilities
    ):
        return False
    if any(
        not isinstance(row, dict)
        or row.get("state") not in {"healthy", "observation_only", "disabled"}
        for row in capabilities.values()
    ):
        return False
    source = capabilities.get("policy:source-assurance", {}).get("details")
    if not isinstance(source, dict) or source.get("state") != "healthy":
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
        freshness.get("state") == "fresh"
        and freshness.get("revision_current") is True
        and freshness.get("refresh_error_type") is None
        and all(counts.get(name) == 0 for name in ("missing", "pending", "failed"))
        and effects.get("state") == "healthy"
        and window.get("state") == "healthy"
        and window.get("started_at_configured") is True
        and all(effect_counts.get(name) == 0 for name in _INTEGRITY_ZERO_COUNTS)
        and all(window_counts.get(name) == 0 for name in _INTEGRITY_ZERO_COUNTS)
        and window_counts.get("provider_called_true") == 0
        and window_counts.get("suppressed_false_or_unknown") == 0
    )


def _private_json(path: Path) -> tuple[bytes, dict[str, Any]]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise HTR10ExportError(f"{path.name} must contain one object")
    return payload, value


def _write_new_private(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise HTR10ExportError("private HTR-10 export write was incomplete")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reviewed_binding_audit_view(
    paths: DeploymentPaths,
    *,
    adapter_payload: bytes,
    adapter_config: dict[str, Any],
    mapping: dict[str, Any],
    test_mode: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return a pending transition or the already-active reviewed config.

    HTR-10 is required for code-only successor releases after a provider-binding
    transition has already been applied. Only the explicit no-eligible-change
    result permits the current configuration; every other transition error
    continues to fail closed.
    """

    try:
        transition = prepare_live_provider_binding_transition(
            paths,
            adapter_config,
            household_mapping=mapping,
            ownership_reviewed=True,
            source_payload=adapter_payload,
            test_mode=test_mode,
        )
    except NoEligibleProviderBindingTransitionError:
        return adapter_config, None
    return transition.prepared, transition.public()


def _runtime_supports_read_only_repair_audit(diagnostics: object) -> bool:
    """Accept healthy readiness or one isolated degraded adapter boundary.

    A protected repair candidate must be able to attest HTR-10 when the active
    adapter defect it repairs is the only reason overall readiness is false.
    Safe reads, event ingest, observation containment, and every non-adapter
    capability remain mandatory. The guarded updater still requires a fresh
    backup and a healthy post-switch Doctor or rolls back automatically.
    """

    if isinstance(diagnostics, dict) and diagnostics.get("ready") is True:
        return True
    return bool(isolated_degraded_adapter_ids(diagnostics)) or (
        _runtime_supports_read_only_attestation_refresh(diagnostics)
    )


async def export_htr10_audits(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    historical_output: Path,
    source_output: Path,
    ownership_reviewed: bool,
    submission_repair_manifest: Path | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    if ownership_reviewed is not True:
        raise HTR10ExportError("provider-binding transition approval is required")
    historical_output = ensure_within(historical_output, paths.backup_dir)
    source_output = ensure_within(source_output, paths.backup_dir)
    if historical_output == source_output:
        raise HTR10ExportError("HTR-10 outputs must be distinct")
    adapter_payload, adapter_config = _private_json(
        paths.instance_dir / "config" / "adapters.json"
    )
    _mapping_payload, mapping = _private_json(
        paths.instance_dir / "config" / "member-mapping.json"
    )
    reviewed_config, transition_public = _reviewed_binding_audit_view(
        paths,
        adapter_payload=adapter_payload,
        adapter_config=adapter_config,
        mapping=mapping,
        test_mode=test_mode,
    )
    roles = {
        str(row["member_id"]): str(row["role"])
        for row in mapping.get("members", [])
        if isinstance(row, dict)
        and isinstance(row.get("member_id"), str)
        and isinstance(row.get("role"), str)
    }
    member_ids = set(roles)
    member_ids.update(
        str(row["member_id"])
        for row in mapping.get("members", [])
        if isinstance(row, dict) and isinstance(row.get("member_id"), str)
    )
    household_party_ids = set(member_ids)
    household_party_ids.update(
        str(row["party_id"])
        for row in mapping.get("parties", [])
        if isinstance(row, dict) and isinstance(row.get("party_id"), str)
    )
    service_principals, _service_channels = validate_service_identity_config(
        reviewed_config.get("service_identities"),
        member_ids=member_ids,
        household_party_ids=household_party_ids,
    )
    bindings = validate_provider_bindings(
        reviewed_config,
        member_roles=roles,
        household_mapping=mapping,
        service_principal_ids={
            principal.service_principal_id for principal in service_principals
        },
    )
    diagnostics = ServiceManager(paths, test_mode=test_mode).runtime_diagnostics()
    if not isinstance(diagnostics, dict):
        raise HTR10ExportError("active runtime readiness is unavailable")
    repair = None
    if submission_repair_manifest is not None:
        from bootstrap.submission_repair import (
            load_submission_repair_manifest,
            validate_submission_repair_diagnostics,
        )

        repair = load_submission_repair_manifest(
            submission_repair_manifest,
            candidate=repository_root,
            active_commit=capture_deployment_snapshot(paths).predecessor_commit,
            test_mode=test_mode,
        )
        validate_submission_repair_diagnostics(
            repair, diagnostics, observation_active=True
        )
    elif not _runtime_supports_read_only_repair_audit(diagnostics):
        raise HTR10ExportError("active runtime readiness is unavailable")
    if diagnostics.get("observation_active") is not True and not test_mode:
        raise HTR10ExportError("observation mode is not active")
    capabilities = {
        str(key): value
        for key, value in (diagnostics.get("capabilities") or {}).items()
        if str(key).startswith(("pipeline:", "projection:"))
    }
    _readiness_payload, readiness = _private_json(
        paths.instance_dir / "config" / "deployment-readiness.json"
    )
    try:
        window_evidence = provider_effect_window_evidence(
            readiness, now=datetime.now(timezone.utc)
        )
    except CandidateReadinessError as exc:
        raise HTR10ExportError(
            "provider-effect observation windows are invalid"
        ) from exc
    window = window_evidence["provider_effect_window"]
    _secret_payload, secret_references = _private_json(
        paths.instance_dir / "config" / "secret-references.json"
    )
    event_reference = (secret_references.get("references") or {}).get("event_log")
    if not isinstance(event_reference, str) or not event_reference:
        raise HTR10ExportError("event-log credential reference is unavailable")
    event_key = secret_store(paths, test_mode=test_mode).get(event_reference)
    event_log = EventLog(
        paths.instance_dir / "events" / "events.db",
        event_key,
        read_only=True,
    )
    try:
        watermark = await event_log.latest_event_id()
        historical = await audit_historical_attribution(
            event_log,
            repository_root=repository_root,
            provider_bindings=bindings,
            hash_key=hashlib.sha256(
                b"administrateme-htr10-attribution-v1\0" + event_key
            ).digest(),
            through_event_id=watermark,
        )
        assurance = await audit_source_assurance(
            event_log,
            repository_root=repository_root,
            capabilities=capabilities,
            through_event_id=watermark,
            provider_effect_window_started_at=window["started_at"],
            provider_effect_window_ended_at=window.get("ended_at"),
        )
        prior_audits = []
        for prior in window_evidence["prior_provider_effect_windows"]:
            prior_assurance = await audit_source_assurance(
                event_log,
                repository_root=repository_root,
                capabilities=capabilities,
                through_event_id=watermark,
                provider_effect_window_started_at=prior["started_at"],
                provider_effect_window_ended_at=prior["ended_at"],
            )
            prior_audits.append(
                prior_assurance["provider_effects"]["observation_window"]
            )
        if prior_audits:
            assurance["provider_effects"]["prior_observation_windows"] = prior_audits
        assurance["provider_effect_windows"] = window_evidence
        if repair is not None:
            from bootstrap.submission_repair import (
                audit_submission_repair,
                repair_binding,
                validate_submission_repair_audit,
            )

            # This is evidence for an exact repair, never a healthy-source
            # attestation. Keep the failed audit and all interval counts intact.
            repair_audit = await audit_submission_repair(
                event_log,
                repair,
                repository_root=repository_root,
                through_event_id=watermark,
            )
            validate_submission_repair_audit(repair, assurance)
            assurance["submission_repair"] = repair_binding(repair)
            assurance["submission_repair_audit"] = repair_audit
            historical["submission_repair"] = repair_binding(repair)
    finally:
        await event_log.close()
    transition_sha256 = (
        hashlib.sha256(
            json.dumps(
                transition_public, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if transition_public is not None
        else None
    )
    historical["provider_binding_transition_sha256"] = transition_sha256
    assurance["provider_binding_transition_sha256"] = transition_sha256
    _write_new_private(historical_output, historical)
    try:
        _write_new_private(source_output, assurance)
    except BaseException:
        historical_output.unlink(missing_ok=True)
        raise
    return {
        "status": "htr_10_audits_exported",
        "watermark_bound": historical.get("watermark_event_id")
        == assurance.get("watermark_event_id"),
        "historical_state": historical.get("state"),
        "source_state": assurance.get("state"),
        "provider_binding_transition_sha256": transition_sha256,
        "activation_authorized": False,
        "physical_checks_attested": False,
        "content_exposed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--historical-output", type=Path, required=True)
    parser.add_argument("--source-output", type=Path, required=True)
    parser.add_argument("--submission-repair-manifest", type=Path)
    parser.add_argument(
        "--approve-provider-binding-migration",
        action="store_true",
        help=(
            "approve exact legacy attribution plus any uniquely discovered "
            "principal-bound read-only Google enrollment"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = DeploymentPaths.from_root(args.instance_root)
    result = asyncio.run(
        export_htr10_audits(
            paths,
            repository_root=Path(__file__).resolve().parents[1],
            historical_output=args.historical_output,
            source_output=args.source_output,
            ownership_reviewed=args.approve_provider_binding_migration,
            submission_repair_manifest=args.submission_repair_manifest,
            test_mode=os.environ.get("ADMINME_TEST_MODE") == "1",
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
