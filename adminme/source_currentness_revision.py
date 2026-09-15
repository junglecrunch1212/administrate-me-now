"""One separate, finite currentness correction of the selected disposable sample.

The closed semantic revision retains its original names, receipts and budget.
Distinct event types let its predecessor ignore this new control suffix.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.adapters.disposable_test_acquisition import DisposableTestAcquisition
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.source_assessment_revision import (
    REVISION as CLOSED_REVISION,
    REQUEST_TYPE as CLOSED_REQUEST_TYPE,
    RevisionManifest,
    RevisionRefused,
    RevisionTarget,
    _execution_policy_for_manifest,
    assessment_key as closed_assessment_key,
    execution_policy as closed_execution_policy,
    request_key as closed_request_key,
)


REVISION: Literal["currentness-v1"] = "currentness-v1"
REQUEST_TYPE = "source.currentness.revision_requested"
REFUSAL_TYPE = "source.currentness.revision_refused"
ASSESSMENT_TYPE = "triage.currentness.features_extracted"
PLAN_TYPE = "triage.currentness_publication.planned"
COMPLETION_TYPE = "triage.currentness_publication.completed"
MAX_TARGETS = 3
ATTEMPT_LIMIT = 3
CLASSIFIER_SHA256 = "d47d77ec45eb3d123002ff69878da13c80c3892feffe31e36bb5760733d7a9a9"


class CurrentnessPreview(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_ids: list[str] = Field(min_length=1, max_length=MAX_TARGETS)


class CurrentnessManifest(BaseModel):
    model_config = {"extra": "forbid"}
    revision: Literal["currentness-v1"] = REVISION
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    targets: list[RevisionTarget] = Field(min_length=1, max_length=MAX_TARGETS)

    @model_validator(mode="after")
    def disjoint_episodes(self) -> CurrentnessManifest:
        # Reuse the exact episode/context validation without widening v1.
        RevisionManifest(dataset_id=self.dataset_id, targets=self.targets)
        return self

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()


def request_key(owner_scope: str) -> str:
    digest = hashlib.sha256(owner_scope.encode()).hexdigest()
    return f"source-assessment-revision:{REVISION}:{digest}"


def assessment_key(source_event_id: str) -> str:
    return f"triage:message-features:revision:{REVISION}:{source_event_id}"


def manifest_for_request(
    command: dict[str, Any],
) -> RevisionManifest | CurrentnessManifest:
    if command.get("type") == CLOSED_REQUEST_TYPE:
        return RevisionManifest.model_validate(command["payload"]["manifest"])
    if command.get("type") == REQUEST_TYPE:
        return CurrentnessManifest.model_validate(command["payload"]["manifest"])
    raise RevisionRefused("source_scope_mismatch")


def request_key_for_revision(revision: str, owner_scope: str) -> str:
    if revision == CLOSED_REVISION:
        return closed_request_key(owner_scope)
    if revision == REVISION:
        return request_key(owner_scope)
    raise RevisionRefused("source_scope_mismatch")


def assessment_key_for_revision(revision: str, source_event_id: str) -> str:
    if revision == CLOSED_REVISION:
        return closed_assessment_key(source_event_id)
    if revision == REVISION:
        return assessment_key(source_event_id)
    raise RevisionRefused("source_scope_mismatch")


def execution_policy(
    command: dict[str, Any],
    *,
    adapter_config: dict[str, Any],
    dataset_marker: dict[str, Any],
    repository_root: Path,
) -> DisposableTestAcquisition:
    manifest = manifest_for_request(command)
    if isinstance(manifest, RevisionManifest):
        return closed_execution_policy(
            command,
            adapter_config=adapter_config,
            dataset_marker=dataset_marker,
            repository_root=repository_root,
        )
    return _execution_policy_for_manifest(
        command,
        dataset_id=manifest.dataset_id,
        classifier_sha256=CLASSIFIER_SHA256,
        adapter_config=adapter_config,
        dataset_marker=dataset_marker,
        repository_root=repository_root,
    )


def request_envelope(
    manifest: CurrentnessManifest, *, tenant_id: str, owner_scope: str, actor: str
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=time.time_ns() // 1_000_000,
        tenant_id=tenant_id,
        type=REQUEST_TYPE,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="source_assessment_revision",
        source_account_id="operator",
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity="normal",
        actor_identity=actor,
        idempotency_key=request_key(owner_scope),
        payload={
            "manifest": manifest.model_dump(mode="json"),
            "manifest_sha256": manifest.fingerprint(),
            "requested_by_actor": actor,
            "attempt_limit_per_source": ATTEMPT_LIMIT,
        },
    )


async def command_status(
    event_log: EventLog, request: dict[str, Any]
) -> dict[str, Any]:
    manifest = CurrentnessManifest.model_validate(request["payload"]["manifest"])
    from adminme.source_assessment_revision import refusal_key

    targets = []
    for target in manifest.targets:
        terminal = await event_log.get_by_idempotency(
            request["tenant_id"], assessment_key(target.source_event_id)
        )
        refusal = await event_log.get_by_idempotency(
            request["tenant_id"],
            refusal_key(request["event_id"], target.source_event_id),
        )
        targets.append(
            {
                "source_event_id": target.source_event_id,
                "state": "assessed"
                if terminal
                else "refused"
                if refusal
                else "pending",
                "assessment_event_id": terminal["event_id"] if terminal else None,
                "outcome": terminal["payload"]["assessment_outcome"]
                if terminal
                else None,
                "refusal_reason": refusal["payload"]["reason"] if refusal else None,
            }
        )
    return {
        "revision": REVISION,
        "request_event_id": request["event_id"],
        "manifest_sha256": request["payload"]["manifest_sha256"],
        "targets": targets,
        "terminal": all(target["state"] != "pending" for target in targets),
        "max_classifier_attempts": ATTEMPT_LIMIT * len(targets),
        "source_acquisition": False,
        "external_write_requests": 0,
    }
