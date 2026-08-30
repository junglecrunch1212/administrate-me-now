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
from pathlib import Path
from typing import Any

from adminme.events.log import EventLog
from adminme.historical_attribution import audit_historical_attribution
from adminme.provider_bindings import validate_provider_bindings
from adminme.source_assurance import audit_source_assurance
from bootstrap.contracts import DeploymentPaths, ensure_within
from bootstrap.provider_binding_transition import (
    NoEligibleProviderBindingTransitionError,
    prepare_live_provider_binding_transition,
)
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager


class HTR10ExportError(RuntimeError):
    pass


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


async def export_htr10_audits(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    historical_output: Path,
    source_output: Path,
    ownership_reviewed: bool,
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
    bindings = validate_provider_bindings(
        reviewed_config,
        member_roles=roles,
        household_mapping=mapping,
    )
    diagnostics = ServiceManager(paths, test_mode=test_mode).runtime_diagnostics()
    if not isinstance(diagnostics, dict) or diagnostics.get("ready") is not True:
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
    window = (readiness.get("provider_effect_window") or {}).get("started_at")
    if not isinstance(window, str) or not window:
        raise HTR10ExportError("provider-effect observation window is unavailable")
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
            provider_effect_window_started_at=window,
        )
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
        "content_exposed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--historical-output", type=Path, required=True)
    parser.add_argument("--source-output", type=Path, required=True)
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
            test_mode=os.environ.get("ADMINME_TEST_MODE") == "1",
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
