"""One explicit, finite semantic correction of a disposable source sample.

The command contains identifiers only. It freezes the selected episode and
context, never acquires sources, and cannot renew attempts by changing a
request identifier. Normal source delivery and consumer versions stay intact.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.adapters.disposable_test_acquisition import (
    DisposableTestAcquisition,
    policy_from_adapter_config,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.message_context import build_message_context_pack
from adminme.source_assessment_recovery import latest_source_assessment


REVISION: Literal["triage-semantics-2026-09-v1"] = "triage-semantics-2026-09-v1"
REQUEST_TYPE = "source.assessment.revision_requested"
REFUSAL_TYPE = "source.assessment.revision_refused"
REVISION_PLAN_TYPE = "triage.source_revision_publication.planned"
REVISION_COMPLETION_TYPE = "triage.source_revision_publication.completed"
MAX_TARGETS = 9
ATTEMPT_LIMIT = 3
CLASSIFIER_SHA256 = "8d6efcd66dc9d0b8cfdb460444ae5369147d66532af1c78a575ebb50a5124b59"


class RevisionRefused(ValueError):
    """Content-free refusal suitable for the authenticated operator API."""


def execution_policy(
    command: dict[str, Any],
    *,
    adapter_config: dict[str, Any],
    dataset_marker: dict[str, Any],
    repository_root: Path,
) -> DisposableTestAcquisition:
    """Revalidate runtime authority after a queued command or process restart."""
    manifest = RevisionManifest.model_validate(command["payload"]["manifest"])
    try:
        policy = policy_from_adapter_config(adapter_config)
    except ValueError as exc:
        raise RevisionRefused("disposable_dataset_or_selection_changed") from exc
    if (
        policy is None
        or dataset_marker.get("dataset_id") != manifest.dataset_id
        or dataset_marker.get("contract") != "disposable-test-dataset-v1"
        or command.get("owner_scope") != f"private:{policy.member_id}"
    ):
        raise RevisionRefused("disposable_dataset_or_selection_changed")
    digest = hashlib.sha256()
    root = repository_root / "packs/skills/classify_message_nature"
    for name in (
        "SKILL.md",
        "pack.yaml",
        "prompt.jinja2",
        "schemas/input.schema.json",
        "schemas/output.schema.json",
    ):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    if digest.hexdigest() != CLASSIFIER_SHA256:
        raise RevisionRefused("reviewed_classifier_contract_changed")
    return policy


class RevisionTarget(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_id: str = Field(min_length=1, max_length=240)
    assessment_event_id: str = Field(min_length=1, max_length=240)
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_source_event_ids: list[str] = Field(min_length=1, max_length=18)
    episode_source_event_ids: list[str] = Field(min_length=1, max_length=100)


class RevisionPreview(BaseModel):
    model_config = {"extra": "forbid"}
    source_event_ids: list[str] = Field(min_length=1, max_length=MAX_TARGETS)


class RevisionManifest(BaseModel):
    model_config = {"extra": "forbid"}
    revision: Literal["triage-semantics-2026-09-v1"] = REVISION
    dataset_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    targets: list[RevisionTarget] = Field(min_length=1, max_length=MAX_TARGETS)

    @model_validator(mode="after")
    def disjoint_episodes(self) -> RevisionManifest:
        seen: set[str] = set()
        for target in self.targets:
            episode = set(target.episode_source_event_ids)
            if (
                target.source_event_id not in episode
                or seen.intersection(episode)
                or len(episode) != len(target.episode_source_event_ids)
                or len(set(target.context_source_event_ids))
                != len(target.context_source_event_ids)
            ):
                raise ValueError("revision requires distinct, nonoverlapping episodes")
            seen.update(episode)
        return self

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()


def request_key(owner_scope: str) -> str:
    # One manifest per owner and reviewed revision in this immutable dataset.
    scope_digest = hashlib.sha256(owner_scope.encode()).hexdigest()
    return f"source-assessment-revision:{REVISION}:{scope_digest}"


def assessment_key(source_event_id: str) -> str:
    return f"triage:message-features:revision:{REVISION}:{source_event_id}"


def refusal_key(request_event_id: str, source_event_id: str) -> str:
    return f"source-assessment-revision-refused:{request_event_id}:{source_event_id}"


async def inspect_target(
    event_log: EventLog,
    source_event_id: str,
    *,
    tenant_id: str,
    owner_scope: str,
    policy: DisposableTestAcquisition | None = None,
) -> RevisionTarget:
    source = await event_log.get(source_event_id)
    if (
        source is None
        or source.get("tenant_id") != tenant_id
        or source.get("owner_scope") != owner_scope
        or source.get("visibility_scope") != owner_scope
        or not owner_scope.startswith("private:")
        or source.get("sensitivity") not in {"normal", "sensitive"}
        or source.get("type") not in {"messaging.received", "messaging.sent"}
        or source.get("payload", {}).get("member_id")
        != owner_scope.removeprefix("private:")
    ):
        raise RevisionRefused("source_scope_mismatch")
    current = await latest_source_assessment(event_log, source_event_id)
    if current is None or current.get("payload", {}).get("assessment_outcome") not in {
        "eligible_candidates",
        "no_candidate",
    }:
        raise RevisionRefused("completed_assessment_required")
    from adminme.source_publication import source_assessment_identity_valid

    if not source_assessment_identity_valid(source=source, assessment=current):
        raise RevisionRefused("source_scope_mismatch")
    context = await build_message_context_pack(source, event_log)
    if not context.current_is_thread_anchor:
        raise RevisionRefused("latest_episode_anchor_required")
    context_ids = sorted(message.source_event_id for message in context.messages)
    episode_ids = sorted(context.episode_source_event_ids)
    # A member decision freezes that episode for this one test correction.
    async for proposal in event_log.read_since(
        owner_scope=owner_scope, types=["triage.item.proposed"]
    ):
        if proposal.get("payload", {}).get("source_event_id") not in episode_ids:
            continue
        descendants = await event_log.get_by_causation(proposal["event_id"])
        if any(
            child.get("type")
            in {
                "triage.item.enriched",
                "triage.item.managed",
                "triage.item.confirmed",
                "triage.item.materialized",
                "triage.item.dismissed",
            }
            for child in descendants
        ):
            raise RevisionRefused("member_decision_preserved")
    if policy is not None:
        for event_id in sorted(set(context_ids) | set(episode_ids)):
            item = await event_log.get(event_id)
            payload = (item or {}).get("payload") or {}
            if (
                item is None
                or item.get("source_adapter") != policy.adapter_id
                or item.get("source_account_id") != policy.provider_account_id
                or item.get("owner_scope") != f"private:{policy.member_id}"
                or item.get("visibility_scope") != item.get("owner_scope")
                or item.get("tenant_id") != tenant_id
                or payload.get("provider") != "gmail"
                or payload.get("provider_account_id") != policy.provider_account_id
                or payload.get("native_message_id") not in policy.message_ids
            ):
                raise RevisionRefused("context_outside_selected_test_sample")
    return RevisionTarget(
        source_event_id=source_event_id,
        assessment_event_id=str(current["event_id"]),
        context_fingerprint=context.fingerprint,
        context_source_event_ids=context_ids,
        episode_source_event_ids=episode_ids,
    )


async def validate_target(
    event_log: EventLog,
    target: RevisionTarget,
    *,
    tenant_id: str,
    owner_scope: str,
    policy: DisposableTestAcquisition | None = None,
) -> dict[str, Any]:
    actual = await inspect_target(
        event_log,
        target.source_event_id,
        tenant_id=tenant_id,
        owner_scope=owner_scope,
        policy=policy,
    )
    if actual != target:
        raise RevisionRefused("assessment_or_context_changed")
    source = await event_log.get(target.source_event_id)
    assert source is not None
    return source


def request_envelope(
    manifest: RevisionManifest, *, tenant_id: str, owner_scope: str, actor: str
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
    manifest = RevisionManifest.model_validate(request["payload"]["manifest"])
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
