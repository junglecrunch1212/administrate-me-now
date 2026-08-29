"""Append one exact, owner-reviewed legacy provider-effect reconciliation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.provider_bindings import validate_provider_bindings
from adminme.source_assurance import legacy_effect_event_fingerprint
from bootstrap.contracts import DeploymentPaths
from bootstrap.provider_binding_transition import prepare_provider_binding_transition
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager


class LegacyEffectReconciliationError(RuntimeError):
    pass


def _legacy_terminal_outcome(
    request: dict[str, Any],
    terminal: dict[str, Any],
    *,
    command_id: str,
) -> tuple[bool, bool] | None:
    """Return the provider-call and suppression facts proven by a v1 pair."""

    request_payload = request.get("payload") or {}
    terminal_payload = terminal.get("payload") or {}
    suppressed = terminal_payload.get("suppressed")
    if not isinstance(suppressed, bool):
        return None
    if not (
        terminal.get("type") == "external_sync.write_completed"
        and "domain_approval_event_id" not in request_payload
        and "openclaw_approval_event_id" not in request_payload
        and "request_fingerprint" not in request_payload
        and "provider_called" not in terminal_payload
        and terminal_payload.get("command_id") == command_id
        and terminal_payload.get("member_id") == request_payload.get("member_id")
        and terminal_payload.get("provider") == request_payload.get("provider")
        and terminal_payload.get("operation") == request_payload.get("operation")
    ):
        return None
    return not suppressed, suppressed


def _json(path: Path) -> tuple[bytes, dict[str, Any]]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise LegacyEffectReconciliationError(f"{path.name} is invalid")
    return payload, value


async def reconcile_legacy_provider_effect(
    paths: DeploymentPaths,
    *,
    owner_reviewed: bool,
    test_mode: bool = False,
) -> dict[str, Any]:
    if owner_reviewed is not True:
        raise LegacyEffectReconciliationError("owner review confirmation is required")
    diagnostics = ServiceManager(paths, test_mode=test_mode).runtime_diagnostics()
    if not isinstance(diagnostics, dict) or diagnostics.get("ready") is not True:
        raise LegacyEffectReconciliationError("active runtime readiness is unavailable")
    if diagnostics.get("observation_active") is not True and not test_mode:
        raise LegacyEffectReconciliationError("observation mode is not active")
    adapter_payload, adapters = _json(paths.instance_dir / "config" / "adapters.json")
    _mapping_payload, mapping = _json(
        paths.instance_dir / "config" / "member-mapping.json"
    )
    transition = prepare_provider_binding_transition(
        adapters,
        household_mapping=mapping,
        ownership_reviewed=True,
        source_payload=adapter_payload,
    )
    roles = {
        str(row["member_id"]): str(row["role"])
        for row in mapping.get("members", [])
        if isinstance(row, dict)
        and isinstance(row.get("member_id"), str)
        and isinstance(row.get("role"), str)
    }
    bindings = validate_provider_bindings(
        transition.prepared,
        member_roles=roles,
        household_mapping=mapping,
    )
    _secret_payload, references = _json(
        paths.instance_dir / "config" / "secret-references.json"
    )
    event_reference = (references.get("references") or {}).get("event_log")
    if not isinstance(event_reference, str) or not event_reference:
        raise LegacyEffectReconciliationError(
            "event-log credential reference is unavailable"
        )
    event_key = secret_store(paths, test_mode=test_mode).get(event_reference)
    event_log = EventLog(paths.instance_dir / "events" / "events.db", event_key)
    try:
        watermark = await event_log.latest_event_id()
        requests: dict[str, list[dict[str, Any]]] = {}
        terminals: dict[str, list[dict[str, Any]]] = {}
        reconciliations: dict[str, list[dict[str, Any]]] = {}
        event_types = [
            "external_sync.write_requested",
            "external_sync.write_completed",
            "external_sync.write_failed",
            "external_sync.legacy_effect_reconciled",
        ]
        async for event in event_log.read_metadata_since(
            through=watermark or "",
            types=event_types,
            include_payload_for=event_types,
        ):
            payload = event.get("payload") or {}
            command = str(payload.get("command_id") or "")
            if event.get("type") == "external_sync.write_requested":
                requests.setdefault(command, []).append(event)
            elif event.get("type") in {
                "external_sync.write_completed",
                "external_sync.write_failed",
            }:
                terminals.setdefault(command, []).append(event)
            else:
                reconciliations.setdefault(command, []).append(event)
        eligible: list[tuple[dict[str, Any], dict[str, Any], bool, bool]] = []
        already: list[tuple[bool, bool]] = []
        for command, request_rows in requests.items():
            terminal_rows = terminals.get(command, [])
            if len(request_rows) != 1 or len(terminal_rows) != 1:
                continue
            request = request_rows[0]
            terminal = terminal_rows[0]
            outcome = _legacy_terminal_outcome(
                request,
                terminal,
                command_id=command,
            )
            if outcome is None:
                continue
            provider_called, suppressed = outcome
            if reconciliations.get(command):
                already.append((provider_called, suppressed))
            else:
                eligible.append((request, terminal, provider_called, suppressed))
        if not eligible and len(already) == 1:
            provider_called, suppressed = already[0]
            return {
                "status": "already_reconciled",
                "reconciled_count": 1,
                "provider_called": provider_called,
                "suppressed": suppressed,
                "content_exposed": False,
            }
        if len(eligible) != 1 or already:
            raise LegacyEffectReconciliationError(
                "legacy provider-effect candidate is missing or ambiguous"
            )
        request_metadata, terminal_metadata, provider_called, suppressed = eligible[0]
        request_id = str(request_metadata["event_id"])
        terminal_id = str(terminal_metadata["event_id"])
        full_request = await event_log.get(request_id)
        full_terminal = await event_log.get(terminal_id)
        if full_request is None or full_terminal is None:
            raise LegacyEffectReconciliationError("legacy event pair changed")
        payload = full_request["payload"]
        provider_kind = {
            "google_tasks": "tasks-google-tasks",
            "google_calendar": "calendaring-google-calendar",
            "gmail": "messaging-gmail-api",
        }.get(str(payload.get("provider") or ""))
        exact_bindings = [
            binding
            for binding in bindings
            if binding.kind == provider_kind
            and binding.member_id == payload.get("member_id")
            and binding.provider_account_id == payload.get("provider_account_id")
        ]
        if len(exact_bindings) != 1:
            raise LegacyEffectReconciliationError(
                "legacy provider effect has no exact reviewed provider binding"
            )
        now = datetime.now(timezone.utc)
        pair_digest = hashlib.sha256(
            f"{request_id}\0{terminal_id}".encode()
        ).hexdigest()
        event_id = await event_log.append(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=str(full_request["tenant_id"]),
                type="external_sync.legacy_effect_reconciled",
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="migration:legacy-provider-effect",
                source_account_id=str(full_request["source_account_id"]),
                owner_scope=str(full_request["owner_scope"]),
                visibility_scope=str(full_request["visibility_scope"]),
                sensitivity="privileged",
                correlation_id=str(payload["command_id"]),
                causation_id=terminal_id,
                actor_identity="owner-reviewed-live-migration",
                idempotency_key=f"legacy-provider-effect-reconciliation:{pair_digest}",
                payload={
                    "request_event_id": request_id,
                    "terminal_event_id": terminal_id,
                    "command_id": str(payload["command_id"]),
                    "provider_account_id": str(payload["provider_account_id"]),
                    "provider_called": provider_called,
                    "suppressed": suppressed,
                    "legacy_approval_state": "pre_governance_absent",
                    "reconciliation_basis": "v1_completed_terminal_evidence",
                    "request_fingerprint": legacy_effect_event_fingerprint(
                        full_request
                    ),
                    "terminal_fingerprint": legacy_effect_event_fingerprint(
                        full_terminal
                    ),
                    "reconciled_at": now.isoformat(),
                },
            ),
            correlation_id=str(payload["command_id"]),
            causation_id=terminal_id,
        )
    finally:
        await event_log.close()
    return {
        "status": "legacy_provider_effect_reconciled",
        "reconciled_count": 1,
        "provider_called": provider_called,
        "suppressed": suppressed,
        "event_recorded": bool(event_id),
        "content_exposed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--confirm-owner-reviewed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = asyncio.run(
        reconcile_legacy_provider_effect(
            DeploymentPaths.from_root(args.instance_root),
            owner_reviewed=args.confirm_owner_reviewed,
            test_mode=os.environ.get("ADMINME_TEST_MODE") == "1",
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
