"""Reconcile one retained CoS list fact without restoring source authority.

Run from an immutable protected successor using the installed predecessor Python.
The default validates only. --append records one idempotent audit event; it never
changes configuration, polls EventKit, refreshes consent, or changes source policy.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.adapters.cos_apple_reminders import policy_from_config
from adminme.cos_apple_source_history import (
    EVENT_TYPE,
    SOURCE_ADAPTER,
    event_fingerprint,
    receipt_idempotency_key,
    source_owner_matches,
    validate_evidence,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.schemas.cos_apple_source_history import (
    ServiceSourceOwnershipReconciledV1,
)
from adminme.lib.instance_config import load_instance_config
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID, service_private_scope
from bootstrap.candidate_readiness import _pointer_snapshot, _read_private_file
from bootstrap.contracts import DeploymentPaths
from bootstrap.cos_channel_activation import (
    ActivationRequest,
    _payload,
    prepare_configuration,
)
from bootstrap.htr10_export import _runtime_supports_read_only_repair_audit
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.source_identity import _run_git, reviewed_candidate_commit
from bootstrap.upgrade import UpgradeManager


class HistoryRepairRequest(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract: Literal["cos-apple-source-history-repair-v1"]
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_active_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_event_id: str = Field(pattern=r"^ev_[0-9A-Z]+$")
    authentication_event_id: str = Field(pattern=r"^ev_[0-9A-Z]+$")
    activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    activation_journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    activation_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recovery_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object(payload: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("history evidence contains duplicate keys")
            result[key] = value
        return result

    result = json.loads(payload, object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("history evidence must be an object")
    return result


def _private(path: Path) -> bytes:
    payload, _ = _read_private_file(path)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("history evidence exceeds its bound")
    return payload


async def reconcile(
    paths: DeploymentPaths,
    *,
    repository_root: Path,
    request: HistoryRepairRequest,
    activation_request: Path,
    ownership_evidence: Path,
    append: bool = False,
    test_mode: bool = False,
) -> dict[str, Any]:
    # Serialize against activation/recovery; normal running acquisition is safe:
    # all targets are immutable and the receipt has no reactive source consumer.
    manager = UpgradeManager(paths, root=repository_root, test_mode=test_mode)
    with manager._operation_lock():
        if reviewed_candidate_commit(repository_root) != request.controller_commit:
            raise ValueError("history controller is not the selected clean commit")
        if _pointer_snapshot(paths)[0] != request.expected_active_commit:
            raise ValueError("history repair active release changed")
        if not test_mode:
            for ancestor, descendant in (
                (request.expected_active_commit, request.controller_commit),
                (request.controller_commit, "origin/main"),
            ):
                if _run_git(
                    repository_root,
                    ["merge-base", "--is-ancestor", ancestor, descendant],
                    check=False,
                ).returncode:
                    raise ValueError("history controller has no protected lineage")
        operation = paths.app_dir / "cos-channel-operation"
        if (operation / "active.json").exists():
            raise ValueError("history repair cannot overlap an active operation")
        journal_bytes = _private(
            operation / "history" / f"{request.activation_id}.json"
        )
        original_bytes = _private(activation_request)
        if (
            _sha(journal_bytes) != request.activation_journal_sha256
            or _sha(original_bytes) != request.activation_request_sha256
        ):
            raise ValueError("history evidence digest differs")
        journal = _object(journal_bytes)
        original = ActivationRequest.model_validate(_object(original_bytes))
        if (
            journal.get("contract") != "cos-channel-operation-journal-v1"
            or journal.get("phase") != "recovered"
            or journal.get("activation_id") != request.activation_id
            or journal.get("release_commit") != request.expected_active_commit
            or type(journal.get("enabled_live")) is not bool
            or any(
                journal.get(key) != getattr(original, key)
                for key in (
                    "owner_member_id",
                    "operator_principal_id",
                    "owner_authorization_sha256",
                )
            )
        ):
            raise ValueError("history evidence is not the exact recovered activation")
        mapping = _object(_private(paths.instance_dir / "config/member-mapping.json"))
        if (
            sum(
                row.get("member_id") == original.owner_member_id
                and row.get("role") == "principal"
                for row in mapping.get("members", [])
                if isinstance(row, dict)
            )
            != 1
        ):
            raise ValueError("history owner is not an enrolled household principal")
        prior = _object(
            base64.b64decode(journal["initial_files"]["adapters.json"], validate=True)
        )
        prepared = prepare_configuration(
            prior, original.adapter_config, enable_live=journal["enabled_live"]
        )
        if _sha(_payload(prepared)) != journal.get(
            "prepared_adapter_configuration_sha256"
        ):
            raise ValueError("history prepared configuration differs")
        policy = policy_from_config(prepared)
        if policy is None or policy.selection is None:
            raise ValueError("history activation has no exact Reminders selection")
        selection = policy.selection
        if (
            selection.owner_authorization_sha256 != original.owner_authorization_sha256
            or selection.source_ownership_evidence_sha256
            != _sha(_private(ownership_evidence))
        ):
            raise ValueError("history original source authority differs")
        diagnostics = ServiceManager(paths, test_mode=test_mode).runtime_diagnostics()
        if not _runtime_supports_read_only_repair_audit(diagnostics):
            raise ValueError("history repair requires a healthy contained runtime")
        references = _object(
            _private(paths.instance_dir / "config/secret-references.json")
        )
        key = secret_store(paths, test_mode=test_mode).get(
            references["references"]["event_log"]
        )
        instance = load_instance_config(paths.instance_dir)
        log = EventLog(instance, key, read_only=not append)
        try:
            watermark = await log.latest_event_id()
            source, authenticated = await validate_evidence(
                log,
                selection=selection,
                source_event_id=request.source_event_id,
                authentication_event_id=request.authentication_event_id,
                through=watermark or "",
            )
            if source["tenant_id"] != instance.tenant_id:
                raise ValueError("history source belongs to another tenant")
            existing = await log.get_by_idempotency(
                instance.tenant_id, receipt_idempotency_key(request.source_event_id)
            )
            if existing is not None:
                if not await source_owner_matches(log, source, through=watermark or ""):
                    raise ValueError("history receipt conflicts with retained evidence")
                return {
                    "status": "already_reconciled",
                    "recorded": False,
                    "provider_effect_count": 0,
                }
            if not append:
                return {
                    "status": "validated_not_recorded",
                    "recorded": False,
                    "provider_effect_count": 0,
                }
            now = datetime.now(timezone.utc)
            receipt = ServiceSourceOwnershipReconciledV1(
                source_event_id=request.source_event_id,
                source_fingerprint=event_fingerprint(source),
                authentication_event_id=request.authentication_event_id,
                authentication_fingerprint=event_fingerprint(authenticated),
                selection=selection,
                activation_id=request.activation_id,
                activation_journal_sha256=request.activation_journal_sha256,
                activation_request_sha256=request.activation_request_sha256,
                prepared_adapter_configuration_sha256=journal[
                    "prepared_adapter_configuration_sha256"
                ],
                owner_member_id=original.owner_member_id,
                operator_principal_id=original.operator_principal_id,
                recovery_authorization_sha256=request.recovery_authorization_sha256,
                reconciled_at=now.isoformat(),
                authority="historical_attribution_only",
            )
            scope = service_private_scope(COS_SERVICE_PRINCIPAL_ID)
            await log.append(
                EventEnvelope(
                    event_at_ms=int(now.timestamp() * 1000),
                    tenant_id=instance.tenant_id,
                    type=EVENT_TYPE,
                    schema_version=1,
                    occurred_at=now.isoformat(),
                    source_adapter=SOURCE_ADAPTER,
                    source_account_id=selection.provider_account_id,
                    owner_scope=scope,
                    visibility_scope=scope,
                    sensitivity="privileged",
                    actor_identity=original.operator_principal_id,
                    idempotency_key=receipt_idempotency_key(request.source_event_id),
                    payload=receipt.model_dump(mode="json"),
                ),
                causation_id=request.source_event_id,
                correlation_id=request.activation_id,
            )
            if not await source_owner_matches(
                log, source, through=await log.latest_event_id() or ""
            ):
                raise ValueError("history receipt failed read-back")
            return {
                "status": "reconciled",
                "recorded": True,
                "provider_effect_count": 0,
            }
        finally:
            await log.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--activation-request", type=Path, required=True)
    parser.add_argument("--ownership-evidence", type=Path, required=True)
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        reconcile(
            DeploymentPaths.from_root(args.instance_root),
            repository_root=Path(__file__).resolve().parents[1],
            request=HistoryRepairRequest.model_validate(
                _object(_private(args.request))
            ),
            activation_request=args.activation_request,
            ownership_evidence=args.ownership_evidence,
            append=args.append,
            test_mode=os.environ.get("ADMINME_TEST_MODE") == "1",
        )
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
