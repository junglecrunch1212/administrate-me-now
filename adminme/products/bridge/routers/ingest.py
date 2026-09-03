"""
Bridge ingest endpoints — /api/bridge/{ingest,ingest/batch,health}.

Per memo 04 §2.2 (F-3: Tailscale-User-Login binds ``owner_scope``
non-overridably) + [§6.19] (bridge sovereignty) + [§7.3, D17 corollary 1]
(bridges emit canonical events; the ingest router persists). [D7] payload
schemas validated by ``EventLog.append`` — malformed payloads surface as
422.

The router lifts ``EventLog`` + ``MemberIdResolver`` off
``request.app.state`` so the production startup and test wiring share
one code path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Literal

import time

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError

from adminme.bridge_authorization import (
    BRIDGE_PACK_POLICIES,
    BridgeAuthorization,
    BridgeConfiguration,
    authorization_from_record,
    configuration_from_record,
    provider_capability_summary,
)
from adminme.bridge_enrollment import (
    CAPABILITIES,
    CAPABILITY_STATES,
    ENROLLMENT_STATES,
    BridgeEnrollmentError,
    canonical_bytes,
    validate_capability_transition,
    validate_enrollment_transition,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import AppendValidationError
from adminme.events.writer import EventSink
from adminme.products.bridge.auth import (
    EnrollmentMemberIdResolver,
    MemberIdResolver,
    resolve_member_id,
    resolve_tailscale_identity,
    unexpired_exported_enrollment,
)

router = APIRouter(prefix="/api/bridge", tags=["bridge"])
ENROLLMENT_HEADER = "X-AdministrateMe-Bridge-Enrollment"


class BatchIngestRequest(BaseModel):
    model_config = {"extra": "forbid"}
    envelopes: list[dict[str, Any]] = Field(min_length=1)


class EnrollmentProgressRequest(BaseModel):
    """Signed content-free progress from the reviewed target-Mac helper."""

    model_config = {"extra": "forbid"}

    contract: Literal["administrateme-member-bridge-progress-v1"]
    request_id: str = Field(min_length=8, max_length=120)
    enrollment_id: str = Field(min_length=1, max_length=160)
    member_id: str = Field(min_length=1, max_length=240)
    instance_id: str = Field(min_length=1, max_length=240)
    release_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    capability_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_state: str
    state: str
    nonce: str = Field(min_length=16, max_length=160)
    reported_at: str = Field(min_length=1, max_length=80)
    device_node_id: str = Field(min_length=1, max_length=240)
    enrollment_event_id: str | None = Field(default=None, max_length=240)
    capability_id: str | None = Field(default=None, max_length=80)
    capability_state: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=240)
    last_successful_observation: str | None = Field(default=None, max_length=80)
    pack_config: dict[str, Any] | None = None
    capability_event_id: str | None = Field(default=None, max_length=240)
    signature: str = Field(min_length=40, max_length=120)


def _get_event_sink(request: Request) -> EventSink:
    events = getattr(request.app.state, "event_sink", None)
    if events is None:
        raise HTTPException(503, detail="event_sink_unavailable")
    return events


def _get_resolver(request: Request) -> MemberIdResolver:
    resolver = getattr(request.app.state, "member_id_resolver", None)
    if resolver is None:
        raise HTTPException(503, detail="member_id_resolver_unavailable")
    return resolver


def _bridge_record(request: Request, tailscale_identity: str) -> dict[str, Any]:
    records = getattr(request.app.state, "bridge_enrollments", {})
    matches = [
        record
        for identity, record in records.items()
        if isinstance(identity, str)
        and identity.casefold() == tailscale_identity.casefold()
        and isinstance(record, dict)
    ]
    if len(matches) != 1:
        raise HTTPException(403, detail="bridge_enrollment_missing_or_ambiguous")
    record = matches[0]
    claimed = request.headers.get(ENROLLMENT_HEADER)
    if not claimed or claimed != record.get("enrollment_id"):
        raise HTTPException(403, detail="bridge_enrollment_coordinate_mismatch")
    return record


def _active_configuration(
    request: Request, tailscale_identity: str
) -> BridgeConfiguration | None:
    return configuration_from_record(_bridge_record(request, tailscale_identity))


def _active_authorization(
    request: Request, tailscale_identity: str
) -> BridgeAuthorization | None:
    return authorization_from_record(_bridge_record(request, tailscale_identity))


def _stamp_owner_scope(
    payload: dict[str, Any],
    resolved_member_id: str,
    expected_tenant_id: str | None = None,
) -> EventEnvelope:
    """Validate envelope shape + enforce F-3 owner_scope binding.

    Per memo 04 §2.2: a claimed ``owner_scope`` that disagrees with the
    Tailscale-resolved identity is a HARD 403 refusal — never silently
    overwritten."""
    try:
        envelope_in = EventEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "envelope_validation_failed", "errors": exc.errors()},
        ) from exc

    expected_scope = f"private:{resolved_member_id}"
    if expected_tenant_id is not None and envelope_in.tenant_id != expected_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tenant_claim_mismatch",
                "claimed": envelope_in.tenant_id,
                "expected": expected_tenant_id,
            },
        )
    if envelope_in.owner_scope != expected_scope:
        # F-3: hard 403, NOT silent overwrite. Memo 04 §7(d).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "owner_scope_claim_mismatch",
                "claimed": envelope_in.owner_scope,
                "expected": expected_scope,
            },
        )
    return envelope_in


def _expected_tenant_id(request: Request) -> str | None:
    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    instance = getattr(settings, "instance", None)
    value = getattr(instance, "tenant_id", None)
    return str(value) if value else None


def _resolve_single_ingest_member(
    request: Request, tailscale_identity: str, raw: dict[str, Any]
) -> tuple[str, bool]:
    """Authorize active ingest or the one provisional enrollment proof."""

    active_member = _get_resolver(request).resolve(tailscale_identity)
    if active_member is not None:
        record = _bridge_record(request, tailscale_identity)
        if record.get("status") != "active" or record.get("member_id") != active_member:
            raise HTTPException(403, detail="bridge_enrollment_not_active")
        return active_member, False
    records = getattr(request.app.state, "bridge_enrollments", {})
    provisional_record = unexpired_exported_enrollment(records, tailscale_identity)
    if provisional_record is None:
        return resolve_member_id(tailscale_identity, _get_resolver(request)), False
    # Bind the one provisional proof to this exact enrollment, not merely to
    # a Tailscale login that may later be re-enrolled.
    bound_record = _bridge_record(request, tailscale_identity)
    if bound_record is not provisional_record and bound_record != provisional_record:
        raise HTTPException(403, detail="bridge_enrollment_coordinate_mismatch")
    try:
        envelope = EventEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "envelope_validation_failed", "errors": exc.errors()},
        ) from exc
    member_id = provisional_record.get("member_id")
    enrollment_id = provisional_record.get("enrollment_id")
    expected_scope = f"private:{member_id}"
    event_payload = envelope.payload
    violations: list[str] = []
    if envelope.type != "bridge.enrolled" or envelope.schema_version != 1:
        violations.append("event_type")
    if envelope.source_adapter != "member-bridge-bootstrap":
        violations.append("source_adapter")
    if (
        not isinstance(enrollment_id, str)
        or envelope.source_account_id != enrollment_id
    ):
        violations.append("source_account_id")
    if envelope.idempotency_key != f"bridge-enrollment:{enrollment_id}":
        violations.append("idempotency_key")
    if envelope.owner_scope != expected_scope:
        violations.append("owner_scope")
    if envelope.visibility_scope != expected_scope:
        violations.append("visibility_scope")
    if event_payload.get("member_id") != member_id:
        violations.append("payload.member_id")
    claimed_identity = event_payload.get("tailscale_identity")
    if not isinstance(claimed_identity, str) or (
        claimed_identity.casefold() != tailscale_identity.casefold()
    ):
        violations.append("payload.tailscale_identity")
    if violations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "provisional_enrollment_contract_denied",
                "violations": violations,
            },
        )
    assert isinstance(member_id, str)
    return member_id, True


def _enforce_member_knowledge_contract(
    request: Request,
    envelope: EventEnvelope,
    member_id: str,
) -> None:
    if not envelope.type.startswith("member_knowledge.note_"):
        return
    expected_scope = f"private:{member_id}"
    source_kind = envelope.payload.get("source_kind")
    expected_adapters = {
        "apple_notes": "knowledge-apple-notes",
        "apple_voice_memos": "knowledge-apple-voice-memos",
        "obsidian": "knowledge-obsidian",
        "fixture": "fixture-claim",
    }
    violations: list[str] = []
    if envelope.visibility_scope != expected_scope:
        violations.append("visibility_scope")
    if envelope.payload.get("member_id") != member_id:
        violations.append("payload.member_id")
    expected_adapter = (
        expected_adapters.get(source_kind) if isinstance(source_kind, str) else None
    )
    if expected_adapter != envelope.source_adapter:
        violations.append("source_adapter")
    if (
        source_kind in {"apple_voice_memos", "obsidian"}
        and envelope.sensitivity == "normal"
    ):
        violations.append("sensitivity_floor")
    runtime = getattr(request.app.state, "runtime", None)
    roles = getattr(getattr(runtime, "settings", None), "member_roles", {})
    if source_kind == "obsidian" and roles.get(member_id) == "child":
        violations.append("adult_only")
    if violations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "member_knowledge_contract_denied",
                "violations": violations,
            },
        )


async def _enforce_terminal_result(
    request: Request,
    envelope: EventEnvelope,
    authorization: BridgeAuthorization,
) -> None:
    if envelope.schema_version != 2:
        raise HTTPException(403, detail="bridge_command_result_requires_v2")
    payload = envelope.payload
    provider = payload.get("provider")
    policy = (
        authorization.policy_for_provider(provider)
        if isinstance(provider, str)
        else None
    )
    expected_account = (
        authorization.provider_account_for(policy.pack) if policy is not None else None
    )
    violations: list[str] = []
    if policy is None:
        violations.append("provider")
    if envelope.source_adapter != "adminme-bridge-command-dispatcher":
        violations.append("source_adapter")
    if envelope.source_account_id != expected_account:
        violations.append("source_account_id")
    if payload.get("member_id") != authorization.member_id:
        violations.append("payload.member_id")
    if (
        expected_account is None
        or payload.get("provider_account_id") != expected_account
    ):
        violations.append("payload.provider_account_id")
    if policy is None or payload.get("operation") not in policy.write_operations:
        violations.append("operation")
    command_event_id = payload.get("command_event_id")
    if envelope.causation_id != command_event_id:
        violations.append("causation_id")
    if envelope.idempotency_key != f"external-sync-result:{payload.get('command_id')}":
        violations.append("idempotency_key")
    event_log = getattr(request.app.state, "event_log", None)
    if event_log is None:
        raise HTTPException(503, detail="event_log_unavailable")
    command = (
        await event_log.get(command_event_id)
        if isinstance(command_event_id, str)
        else None
    )
    if (
        not isinstance(command, dict)
        or command.get("type") != "external_sync.write_requested"
        or int(command.get("schema_version") or 1) != 2
    ):
        violations.append("command_event")
    else:
        requested = command.get("payload", {})
        for key in (
            "command_id",
            "member_id",
            "provider",
            "provider_account_id",
            "operation",
            "domain_approval_event_id",
            "openclaw_approval_event_id",
            "request_fingerprint",
            "origin_proposal_id",
        ):
            if payload.get(key) != requested.get(key):
                violations.append(f"payload.{key}")
        if command.get("owner_scope") != envelope.owner_scope:
            violations.append("command_owner_scope")
        if policy is not None and policy.container_key is not None:
            request_body = requested.get("payload")
            container = (
                _container_from_payload(request_body, policy)
                if isinstance(request_body, dict)
                else None
            )
            allowed = authorization.pack_config.get(policy.pack, {}).get(
                "allowed_container_ids", []
            )
            if container is None or container not in allowed:
                violations.append("command_container")
    existing = (
        await event_log.get_by_idempotency(envelope.tenant_id, envelope.idempotency_key)
        if envelope.idempotency_key
        else None
    )
    if existing is not None:
        comparable = {
            "type": envelope.type,
            "schema_version": envelope.schema_version,
            "source_adapter": envelope.source_adapter,
            "source_account_id": envelope.source_account_id,
            "owner_scope": envelope.owner_scope,
            "visibility_scope": envelope.visibility_scope,
            "causation_id": envelope.causation_id,
            "payload": envelope.payload,
        }
        if any(existing.get(key) != value for key, value in comparable.items()):
            violations.append("terminal_idempotency_conflict")
    if violations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "bridge_command_result_denied",
                "violations": sorted(set(violations)),
            },
        )


def _container_from_payload(payload: dict[str, Any], policy: Any) -> str | None:
    if policy.container_key == "calendar_identifier":
        value = (
            payload.get("calendar_identifier")
            or payload.get("calendar_external_id")
            or payload.get("calendar_id")
        )
    elif policy.container_key == "target_list_identifier":
        value = (
            payload.get("target_list_identifier")
            or payload.get("destination_list_external_id")
            or payload.get("list_external_id")
            or payload.get("list_identifier")
        )
    elif policy.container_key == "container_external_id":
        value = payload.get("container_external_id")
    elif policy.container_key == "protected_source_id":
        value = payload.get("protected_source_id")
    else:
        value = None
    return str(value) if value is not None else None


def _payload_container(envelope: EventEnvelope, policy: Any) -> str | None:
    return _container_from_payload(envelope.payload, policy)


def _capability_evidence_violations(
    envelope: EventEnvelope,
    configuration: BridgeConfiguration,
    policy: Any,
) -> list[str]:
    expected_account = configuration.source_account_for(policy)
    configuration_sha256 = hashlib.sha256(
        json.dumps(
            configuration.pack_config,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    violations: list[str] = []
    if envelope.type not in {"adapter.authenticated", "adapter.error"}:
        violations.append("capability_event_type")
    if envelope.source_account_id != expected_account:
        violations.append("source_account_id")
    if envelope.type == "adapter.authenticated":
        if envelope.schema_version != 2:
            violations.append("schema_version")
        if envelope.payload.get("adapter_name") != policy.source_adapter:
            violations.append("payload.adapter_name")
        if envelope.payload.get("adapter_family") != policy.family:
            violations.append("payload.adapter_family")
        if envelope.payload.get("token_storage_ref") != policy.permission_reference:
            violations.append("payload.token_storage_ref")
        if frozenset(envelope.payload.get("scopes") or []) != policy.permission_scopes:
            violations.append("payload.scopes")
        if envelope.payload.get("bridge_enrollment_id") != configuration.enrollment_id:
            violations.append("payload.bridge_enrollment_id")
        if envelope.payload.get("configuration_sha256") != configuration_sha256:
            violations.append("payload.configuration_sha256")
        if envelope.payload.get("provider_account_id") != expected_account:
            violations.append("payload.provider_account_id")
    return violations


def _promote_reviewed_sharing(
    envelope: EventEnvelope,
    authorization: BridgeAuthorization,
    policy: Any,
) -> EventEnvelope:
    container = _payload_container(envelope, policy)
    if container is None or container not in authorization.shared_container_ids(policy):
        return envelope
    # Native Apple sharing is never accepted as a bridge-supplied scope
    # claim.  Only the content-free container grant sealed into the central
    # authorization may promote both ownership and visibility.
    return envelope.model_copy(
        update={
            "owner_scope": "shared:household",
            "visibility_scope": "shared:household",
        }
    )


async def _enforce_active_bridge_contract(
    request: Request,
    tailscale_identity: str,
    envelope: EventEnvelope,
    member_id: str,
    *,
    provisional_proof_validated: bool,
) -> EventEnvelope:
    if provisional_proof_validated:
        return envelope
    configuration = _active_configuration(request, tailscale_identity)
    configured_policy = (
        configuration.policy_for_source(envelope.source_adapter)
        if configuration is not None
        else None
    )
    if (
        configuration is not None
        and configuration.member_id == member_id
        and configured_policy is not None
        and configured_policy.requires_capability_attestation
        and envelope.type in {"adapter.authenticated", "adapter.error"}
    ):
        evidence_violations = _capability_evidence_violations(
            envelope, configuration, configured_policy
        )
        if not evidence_violations:
            return envelope
        raise HTTPException(
            403,
            detail={
                "error": "bridge_capability_evidence_denied",
                "violations": sorted(set(evidence_violations)),
            },
        )
    authorization = _active_authorization(request, tailscale_identity)
    if authorization is None:
        raise HTTPException(403, detail="bridge_pack_authorization_missing")
    if authorization.member_id != member_id:
        raise HTTPException(403, detail="bridge_authorization_member_mismatch")
    if envelope.type in {
        "external_sync.write_completed",
        "external_sync.write_failed",
    }:
        await _enforce_terminal_result(request, envelope, authorization)
        return envelope
    policy = authorization.policy_for_source(envelope.source_adapter)
    bridge_record = _bridge_record(request, tailscale_identity)
    violations: list[str] = []
    if policy is None:
        violations.append("source_adapter")
    elif envelope.type not in policy.event_types:
        violations.append("event_type")
    elif (
        policy.pack == "messaging-bluebubbles"
        and policy.pack not in bridge_record.get("enabled_packs", [])
    ):
        violations.append("pack_not_enabled")
    expected_account = (
        authorization.source_account_for(policy) if policy is not None else None
    )
    if expected_account is None or envelope.source_account_id != expected_account:
        violations.append("source_account_id")
    if envelope.visibility_scope != f"private:{member_id}":
        violations.append("visibility_scope")
    payload_member = envelope.payload.get("member_id")
    if payload_member is not None and payload_member != member_id:
        violations.append("payload.member_id")
    if policy is not None and policy.provider is not None:
        payload_provider = envelope.payload.get("provider")
        if payload_provider is not None and payload_provider != policy.provider:
            violations.append("payload.provider")
        payload_account = envelope.payload.get("provider_account_id")
        if payload_account is not None and payload_account != expected_account:
            violations.append("payload.provider_account_id")
        if policy.pack == "messaging-bluebubbles" and envelope.type in {
            "messaging.received",
            "messaging.sent",
            "messaging.source_observed",
        }:
            config = authorization.pack_config[policy.pack]
            if (
                envelope.payload.get("bridge_enrollment_id")
                != authorization.enrollment_id
            ):
                violations.append("payload.bridge_enrollment_id")
            if envelope.payload.get("device_identity_sha256") != config.get(
                "device_identity_sha256"
            ):
                violations.append("payload.device_identity_sha256")
            if envelope.payload.get("connector_id") != "messaging-bluebubbles":
                violations.append("payload.connector_id")
            if envelope.payload.get("workspace") != "personal":
                violations.append("payload.workspace")
            expected_version = 1 if envelope.type == "messaging.source_observed" else 7
            if envelope.schema_version != expected_version:
                violations.append("schema_version")
        if not envelope.type.startswith("adapter."):
            container = _payload_container(envelope, policy)
            allowed = authorization.pack_config.get(policy.pack, {}).get(
                "allowed_container_ids", []
            )
            if container is None or container not in allowed:
                violations.append("payload.container")
    if violations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "bridge_pack_contract_denied",
                "violations": sorted(set(violations)),
            },
        )
    assert policy is not None
    if policy.pack == "messaging-bluebubbles":
        device_digest = authorization.pack_config[policy.pack].get(
            "device_identity_sha256"
        )
        envelope = envelope.model_copy(
            update={"actor_identity": f"bridge-device:{device_digest}"}
        )
    return _promote_reviewed_sharing(envelope, authorization, policy)


def _capability_for_observation(envelope: EventEnvelope) -> str | None:
    if envelope.type in {
        "adapter.authenticated",
        "adapter.error",
        "external_sync.write_completed",
        "external_sync.write_failed",
    }:
        return None
    matches = [
        capability_id
        for capability_id, descriptor in CAPABILITIES.items()
        if descriptor.get("implementation") == "available"
        and BRIDGE_PACK_POLICIES[str(descriptor["pack"])].source_adapter
        == envelope.source_adapter
    ]
    return matches[0] if len(matches) == 1 else None


async def _settle_bridge_capability_from_observation(
    request: Request,
    *,
    tailscale_identity: str,
    member_id: str,
    envelope: EventEnvelope,
    observation_event_id: str,
) -> None:
    """Advance Settings from the first admitted source observation.

    The central runtime has already authenticated, authorized, validated, and
    durably appended this exact observation.  It can therefore settle the
    corresponding capability without requiring a second device-side token or
    leaking provider content into wizard state.
    """

    capability_id = _capability_for_observation(envelope)
    runtime = getattr(request.app.state, "runtime", None)
    if capability_id is None or runtime is None or runtime.events is None:
        return
    record = _bridge_record(request, tailscale_identity)
    request_id = record.get("request_id")
    enrollment_id = record.get("enrollment_id")
    if not isinstance(request_id, str) or not isinstance(enrollment_id, str):
        return
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        current = runtime.settings_control.bridge_request_for(member_id)
        if (
            not isinstance(current, dict)
            or current.get("request_id") != request_id
            or not isinstance(current.get("capability_states"), dict)
        ):
            return
        capability_row = current["capability_states"].get(capability_id)
        if (
            not isinstance(capability_row, dict)
            or capability_row.get("state") != "observing"
        ):
            return
        evidence_sha256 = hashlib.sha256(
            canonical_bytes(envelope.model_dump(mode="json"))
        ).hexdigest()
        for next_capability_state in ("settling", "settled"):
            current = runtime.settings_control.bridge_request_for(member_id)
            if not isinstance(current, dict):
                return
            previous_state = str(current["state"])
            next_state = previous_state
            if next_capability_state == "settling" and previous_state in {
                "capabilities_testing",
                "degraded",
                "partially_active",
            }:
                next_state = "settling"
            elif next_capability_state == "settled":
                predicted = {
                    key: str(value.get("state") or "")
                    for key, value in current["capability_states"].items()
                    if isinstance(value, dict)
                }
                predicted[capability_id] = "settled"
                terminal_outcomes = {
                    "settled",
                    "permission_denied",
                    "prerequisite_missing",
                    "degraded",
                    "unsupported",
                    "revoked",
                }
                if predicted and set(predicted.values()) == {"settled"}:
                    next_state = "active_observation"
                elif (
                    "settled" in predicted.values()
                    and set(predicted.values()) <= terminal_outcomes
                ):
                    next_state = "partially_active"
            revision = runtime.settings_control.prepare_bridge_progress(
                member_id, request_id=request_id
            )
            progress_at = EventEnvelope.now_utc_iso()
            payload = {
                "request_id": request_id,
                "enrollment_id": enrollment_id,
                "member_id": member_id,
                "previous_state": previous_state,
                "state": next_state,
                "revision": revision,
                "progress_at": progress_at,
                "reported_by": "central_runtime",
                "signature_verified": False,
                "device_identity_sha256": current.get("device_identity_sha256"),
                "capability_id": capability_id,
                "capability_state": next_capability_state,
                "evidence_sha256": evidence_sha256,
                "reason": None,
                "last_successful_observation": (
                    envelope.occurred_at if next_capability_state == "settled" else None
                ),
                "content_exposed": False,
                "writes_authorized": False,
                "provider_effects": False,
            }
            await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="bridge.enrollment_progressed",
                    schema_version=1,
                    occurred_at=progress_at,
                    source_adapter="member-bridge-enrollment-runtime",
                    source_account_id=enrollment_id,
                    owner_scope=f"private:{member_id}",
                    visibility_scope=f"private:{member_id}",
                    sensitivity="normal",
                    actor_identity="service:member-bridge-ingest",
                    idempotency_key=(
                        f"bridge-observation:{request_id}:{capability_id}:"
                        f"{observation_event_id}:{next_capability_state}"
                    ),
                    raw_ref=f"event:{observation_event_id}",
                    payload=payload,
                )
            )
            runtime.settings_control.apply_bridge_enrollment_event(payload)


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_single(request: Request, payload: dict[str, Any]) -> dict[str, str]:
    tailscale_identity = resolve_tailscale_identity(request)
    member_id, provisional = _resolve_single_ingest_member(
        request, tailscale_identity, payload
    )
    envelope = _stamp_owner_scope(payload, member_id, _expected_tenant_id(request))
    envelope = await _enforce_active_bridge_contract(
        request,
        tailscale_identity,
        envelope,
        member_id,
        provisional_proof_validated=provisional,
    )
    _enforce_member_knowledge_contract(request, envelope, member_id)
    try:
        event_id = await _get_event_sink(request).write(envelope)
    except AppendValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "append_validation_failed", "message": str(exc)},
        ) from exc
    request.app.state.ingest_count += 1
    await _settle_bridge_capability_from_observation(
        request,
        tailscale_identity=tailscale_identity,
        member_id=member_id,
        envelope=envelope,
        observation_event_id=event_id,
    )
    return {"event_id": event_id, "owner_scope": envelope.owner_scope}


@router.post("/ingest/batch", status_code=status.HTTP_202_ACCEPTED)
async def ingest_batch(request: Request, body: BatchIngestRequest) -> dict[str, Any]:
    tailscale_identity = resolve_tailscale_identity(request)
    member_id = resolve_member_id(tailscale_identity, _get_resolver(request))
    record = _bridge_record(request, tailscale_identity)
    if record.get("status") != "active" or record.get("member_id") != member_id:
        raise HTTPException(403, detail="bridge_enrollment_not_active")

    stamped: list[EventEnvelope] = []
    for offset, raw in enumerate(body.envelopes):
        try:
            envelope = _stamp_owner_scope(raw, member_id, _expected_tenant_id(request))
            envelope = await _enforce_active_bridge_contract(
                request,
                tailscale_identity,
                envelope,
                member_id,
                provisional_proof_validated=False,
            )
            _enforce_member_knowledge_contract(request, envelope, member_id)
            stamped.append(envelope)
        except HTTPException as exc:
            # Per F-3 + §7 c2: whole-batch reject with offset of first bad envelope.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "batch_envelope_rejected",
                    "offset": offset,
                    "reason": exc.detail,
                },
            ) from exc

    try:
        event_ids = await _get_event_sink(request).write_batch(stamped)
    except AppendValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "append_validation_failed", "message": str(exc)},
        ) from exc
    request.app.state.ingest_count += len(event_ids)
    for envelope, event_id in zip(stamped, event_ids, strict=True):
        await _settle_bridge_capability_from_observation(
            request,
            tailscale_identity=tailscale_identity,
            member_id=member_id,
            envelope=envelope,
            observation_event_id=event_id,
        )
    return {"event_ids": event_ids, "count": len(event_ids)}


@router.post("/enrollment/progress", status_code=status.HTTP_202_ACCEPTED)
async def enrollment_progress(
    request: Request, body: EnrollmentProgressRequest
) -> dict[str, Any]:
    """Accept signed, replay-resistant, content-free target-helper progress."""

    tailscale_identity = resolve_tailscale_identity(request)
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or runtime.events is None or runtime.event_log is None:
        raise HTTPException(503, detail="bridge_enrollment_runtime_unavailable")
    if (
        body.state not in ENROLLMENT_STATES
        or body.previous_state not in ENROLLMENT_STATES
    ):
        raise HTTPException(422, detail="unknown_bridge_enrollment_state")
    if (
        body.capability_state is not None
        and body.capability_state not in CAPABILITY_STATES
    ):
        raise HTTPException(422, detail="unknown_bridge_capability_state")
    if (body.capability_id is None) != (body.capability_state is None):
        raise HTTPException(422, detail="incomplete_bridge_capability_progress")
    transport_record = _bridge_record(request, tailscale_identity)
    if (
        transport_record.get("request_id") != body.request_id
        or transport_record.get("member_id") != body.member_id
    ):
        raise HTTPException(403, detail="bridge_progress_coordinate_mismatch")
    wizard = runtime.settings_control.bridge_request_for(body.member_id)
    if not isinstance(wizard, dict) or wizard.get("request_id") != body.request_id:
        raise HTTPException(403, detail="bridge_wizard_missing_or_ambiguous")
    if wizard.get("state") != body.previous_state:
        raise HTTPException(409, detail="bridge_progress_previous_state_mismatch")
    try:
        if body.state != body.previous_state or body.capability_id is None:
            validate_enrollment_transition(body.previous_state, body.state)
        if body.capability_id is not None:
            capability_rows = wizard.get("capability_states") or {}
            capability_row = capability_rows.get(body.capability_id)
            if not isinstance(capability_row, dict):
                raise BridgeEnrollmentError(
                    "capability_not_selected", "Capability was not selected"
                )
            validate_capability_transition(
                str(capability_row.get("state") or ""),
                str(body.capability_state or ""),
            )
    except BridgeEnrollmentError as exc:
        raise HTTPException(
            409, detail={"error": exc.code, "message": str(exc)}
        ) from exc

    unsigned = body.model_dump(mode="json", exclude={"signature"})
    try:
        runtime.bridge_enrollment.verify_progress(
            request_id=body.request_id,
            enrollment_id=body.enrollment_id,
            member_id=body.member_id,
            tailscale_identity=tailscale_identity,
            payload=unsigned,
            signature=body.signature,
        )
    except BridgeEnrollmentError as exc:
        if exc.code == "expired":
            try:
                runtime.bridge_enrollment.expire(
                    request_id=body.request_id, member_id=body.member_id
                )
            except BridgeEnrollmentError:
                pass
        code = (
            status.HTTP_410_GONE if exc.code == "expired" else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(
            code, detail={"error": exc.code, "message": str(exc)}
        ) from exc

    device_digest = hashlib.sha256(body.device_node_id.encode("utf-8")).hexdigest()
    try:
        runtime.bridge_enrollment.bind_device(
            request_id=body.request_id,
            member_id=body.member_id,
            node_id=body.device_node_id,
        )
    except BridgeEnrollmentError as exc:
        raise HTTPException(
            403, detail={"error": exc.code, "message": str(exc)}
        ) from exc

    evidence_event: dict[str, Any] | None = None
    evidence_sha256: str | None = None
    if body.state == "bridge_paired":
        if not body.enrollment_event_id:
            raise HTTPException(422, detail="bridge_enrollment_event_required")
        evidence_event = await runtime.event_log.get(body.enrollment_event_id)
        payload = evidence_event.get("payload") if evidence_event else None
        if (
            not evidence_event
            or evidence_event.get("type") != "bridge.enrolled"
            or evidence_event.get("source_account_id") != body.enrollment_id
            or evidence_event.get("owner_scope") != f"private:{body.member_id}"
            or not isinstance(payload, dict)
            or payload.get("member_id") != body.member_id
            or payload.get("bridge_node_id") != body.device_node_id
            or str(payload.get("tailscale_identity", "")).casefold()
            != tailscale_identity.casefold()
        ):
            raise HTTPException(403, detail="bridge_enrollment_event_mismatch")
        try:
            runtime.bridge_enrollment.activate_transport(
                request_id=body.request_id,
                member_id=body.member_id,
                bridge_node_id=body.device_node_id,
                event_id=body.enrollment_event_id,
            )
        except BridgeEnrollmentError as exc:
            raise HTTPException(
                409, detail={"error": exc.code, "message": str(exc)}
            ) from exc
        resolver = EnrollmentMemberIdResolver(runtime.settings.bridge_enrollments)
        runtime.member_id_resolver = resolver
        request.app.state.member_id_resolver = resolver

    if body.capability_id is not None:
        capability = CAPABILITIES.get(body.capability_id)
        if capability is None or capability.get("implementation") != "available":
            raise HTTPException(422, detail="bridge_capability_unavailable")
        if body.capability_state == "permission_verified":
            if not isinstance(body.pack_config, dict):
                raise HTTPException(422, detail="exact_source_coordinate_required")
            try:
                evidence_sha256 = runtime.bridge_enrollment.configure_capability(
                    request_id=body.request_id,
                    member_id=body.member_id,
                    capability_id=body.capability_id,
                    pack_config=body.pack_config,
                )
            except BridgeEnrollmentError as exc:
                raise HTTPException(
                    422, detail={"error": exc.code, "message": str(exc)}
                ) from exc
        elif body.pack_config is not None:
            raise HTTPException(422, detail="source_coordinate_not_expected")
        if body.capability_state == "adapter_authenticated":
            if body.capability_event_id:
                evidence_event = await runtime.event_log.get(body.capability_event_id)
                if evidence_event is not None:
                    evidence_sha256 = hashlib.sha256(
                        canonical_bytes(evidence_event)
                    ).hexdigest()
            try:
                runtime.bridge_enrollment.attest_capability(
                    request_id=body.request_id,
                    member_id=body.member_id,
                    capability_id=body.capability_id,
                    evidence_event=evidence_event,
                )
            except BridgeEnrollmentError as exc:
                raise HTTPException(
                    409, detail={"error": exc.code, "message": str(exc)}
                ) from exc
        if body.capability_state == "observation_starting":
            try:
                runtime.bridge_enrollment.mark_observation_started(
                    request_id=body.request_id,
                    member_id=body.member_id,
                    capability_id=body.capability_id,
                )
            except BridgeEnrollmentError as exc:
                raise HTTPException(
                    409, detail={"error": exc.code, "message": str(exc)}
                ) from exc
        if body.capability_state == "settled":
            if not body.capability_event_id or not body.last_successful_observation:
                raise HTTPException(
                    422, detail="settlement_observation_evidence_required"
                )
            observed = await runtime.event_log.get(body.capability_event_id)
            policy = BRIDGE_PACK_POLICIES[str(capability["pack"])]
            if (
                observed is None
                or observed.get("source_adapter") != policy.source_adapter
                or observed.get("owner_scope") != f"private:{body.member_id}"
            ):
                raise HTTPException(
                    403, detail="settlement_observation_evidence_mismatch"
                )
            evidence_sha256 = hashlib.sha256(canonical_bytes(observed)).hexdigest()

    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        try:
            revision = runtime.settings_control.prepare_bridge_progress(
                body.member_id, request_id=body.request_id
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        progress_at = EventEnvelope.now_utc_iso()
        event_payload = {
            "request_id": body.request_id,
            "enrollment_id": body.enrollment_id,
            "member_id": body.member_id,
            "previous_state": body.previous_state,
            "state": body.state,
            "revision": revision,
            "progress_at": progress_at,
            "reported_by": "target_helper",
            "signature_verified": True,
            "device_identity_sha256": device_digest,
            "capability_id": body.capability_id,
            "capability_state": body.capability_state,
            "evidence_sha256": evidence_sha256,
            "reason": body.reason,
            "last_successful_observation": body.last_successful_observation,
            "content_exposed": False,
            "writes_authorized": False,
            "provider_effects": False,
        }
        nonce_digest = hashlib.sha256(body.nonce.encode("utf-8")).hexdigest()
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="bridge.enrollment_progressed",
                schema_version=1,
                occurred_at=progress_at,
                source_adapter="member-bridge-enrollment-helper",
                source_account_id=body.enrollment_id,
                owner_scope=f"private:{body.member_id}",
                visibility_scope=f"private:{body.member_id}",
                sensitivity="normal",
                actor_identity=f"bridge-device:{device_digest}",
                idempotency_key=f"bridge-progress:{body.request_id}:{nonce_digest}",
                raw_ref="signature-sha256:"
                + hashlib.sha256(body.signature.encode("ascii")).hexdigest(),
                payload=event_payload,
            )
        )
        try:
            mutation = runtime.settings_control.apply_bridge_enrollment_event(
                event_payload
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
    return {
        "accepted": True,
        "event_id": event_id,
        "request_id": body.request_id,
        "state": mutation.current["state"],
        "capability_id": body.capability_id,
        "capability_state": body.capability_state,
        "central_attested": body.capability_state
        in {
            "adapter_authenticated",
            "observation_starting",
            "observing",
            "settling",
            "settled",
        },
        "may_start_connector": body.capability_state
        in {
            "adapter_authenticated",
            "observation_starting",
            "observing",
            "settling",
            "settled",
        },
        "content_exposed": False,
        "provider_effect_count": 0,
    }


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    records = getattr(request.app.state, "bridge_enrollments", {})
    statuses = [
        record.get("status") for record in records.values() if isinstance(record, dict)
    ]
    active = statuses.count("active")
    provisional = sum(
        status in {"package-created-not-live", "package-exported"}
        for status in statuses
    )
    capability_counts = provider_capability_summary(
        record for record in records.values() if isinstance(record, dict)
    )
    invalid_active = 0
    provider_blocked = 0
    for record in records.values():
        if not isinstance(record, dict) or record.get("status") != "active":
            continue
        configuration = configuration_from_record(record)
        if configuration is None:
            invalid_active += 1
            continue
        provider_packs = {
            pack
            for pack in configuration.packs
            if BRIDGE_PACK_POLICIES[pack].requires_capability_attestation
        }
        authorization = authorization_from_record(record)
        if provider_packs and (
            authorization is None or not provider_packs <= authorization.packs
        ):
            provider_blocked += 1
    if invalid_active:
        capability_state = "invalid"
    elif active == 0:
        capability_state = "not_applicable"
    elif capability_counts["not_attested"] == active:
        capability_state = "not_attested"
    elif capability_counts["not_required"] == active:
        capability_state = "not_required"
    elif capability_counts["attested"] + capability_counts["not_required"] == active:
        capability_state = "attested"
    else:
        capability_state = "partial"
    return {
        "status": "ok",
        "endpoint_available": True,
        "bridge_endpoint": "/api/bridge/ingest",
        "ingest_count": int(getattr(request.app.state, "ingest_count", 0)),
        "enrollment_state": (
            "none"
            if not statuses
            else "transport-active"
            if active + statuses.count("revoked") == len(statuses)
            else "provisional"
        ),
        "enrollment_count": len(statuses),
        "active_count": active,
        "provisional_count": provisional,
        "provider_capability_state": capability_state,
        "provider_capability_counts": capability_counts,
        "invalid_active_count": invalid_active,
        "provider_blocked_count": provider_blocked,
        "provider_ready_count": active - invalid_active - provider_blocked,
    }


@router.get("/commands")
async def pending_commands(
    request: Request,
    providers: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Return unresolved commands for the authenticated member bridge.

    Results are derived from the append-only log.  Observation mode turns
    each would-have-run command into a terminal suppressed result, preventing
    it from unexpectedly executing when observation is later disabled.
    """

    identity = resolve_tailscale_identity(request)
    member_id = resolve_member_id(identity, _get_resolver(request))
    authorization = _active_authorization(request, identity)
    if authorization is None or authorization.member_id != member_id:
        raise HTTPException(403, detail="bridge_pack_authorization_missing")
    event_log = getattr(request.app.state, "event_log", None)
    if event_log is None:
        raise HTTPException(503, detail="event_log_unavailable")
    allowed = {
        policy.provider
        for pack in authorization.packs
        if (policy := BRIDGE_PACK_POLICIES[pack]).provider is not None
        and policy.write_operations
    }
    selected = (
        allowed
        if providers is None
        else {item.strip() for item in providers.split(",") if item.strip()}
    )
    if not selected or selected - allowed:
        raise HTTPException(403, detail="bridge provider is not authorized")
    owner_scope = f"private:{member_id}"
    terminal: set[str] = set()
    async for event in event_log.read_since(
        owner_scope=owner_scope,
        types=["external_sync.write_completed", "external_sync.write_failed"],
    ):
        terminal.add(str(event["payload"]["command_id"]))
    commands: list[dict[str, Any]] = []
    async for event in event_log.read_since(
        owner_scope=owner_scope, types=["external_sync.write_requested"]
    ):
        payload = event["payload"]
        command_policy = authorization.policy_for_provider(
            str(payload.get("provider") or "")
        )
        expected_account = (
            authorization.provider_account_for(command_policy.pack)
            if command_policy
            else None
        )
        if (
            payload["command_id"] not in terminal
            and payload["provider"] in selected
            and payload["member_id"] == member_id
            and int(event.get("schema_version") or 1) == 2
            and command_policy is not None
            and payload.get("provider_account_id") == expected_account
            and payload.get("operation") in command_policy.write_operations
            and (
                command_policy.container_key is None
                or _container_from_payload(
                    payload.get("payload")
                    if isinstance(payload.get("payload"), dict)
                    else {},
                    command_policy,
                )
                in authorization.pack_config.get(command_policy.pack, {}).get(
                    "allowed_container_ids", []
                )
            )
        ):
            commands.append(
                {
                    "event_id": event["event_id"],
                    "schema_version": event.get("schema_version", 1),
                    **payload,
                }
            )
            if len(commands) >= limit:
                break

    runtime = getattr(request.app.state, "runtime", None)
    observation = getattr(runtime, "observation", None)
    observation_active = True if observation is None else await observation.is_active()
    if observation_active and commands:
        sink = _get_event_sink(request)
        for command in commands:
            now = EventEnvelope.now_utc_iso()
            is_v2 = int(command.get("schema_version") or 1) >= 2
            result_payload: dict[str, Any] = {
                "command_id": command["command_id"],
                "member_id": member_id,
                "provider": command["provider"],
                "operation": command["operation"],
                "external_id": None,
                "provider_revision": None,
                "result": {"reason": "observation_mode_active"},
                "completed_at": now,
                "suppressed": True,
            }
            if is_v2:
                result_payload.update(
                    {
                        "command_event_id": command["event_id"],
                        "domain_approval_event_id": command["domain_approval_event_id"],
                        "openclaw_approval_event_id": command[
                            "openclaw_approval_event_id"
                        ],
                        "provider_account_id": command["provider_account_id"],
                        "request_fingerprint": command["request_fingerprint"],
                        "provider_called": False,
                        "origin_proposal_id": command.get("origin_proposal_id"),
                    }
                )
            await sink.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=_expected_tenant_id(request) or "tenant-default",
                    type="external_sync.write_completed",
                    schema_version=2 if is_v2 else 1,
                    occurred_at=now,
                    source_adapter="bridge-command-observation-gate",
                    source_account_id=str(command["provider_account_id"]),
                    owner_scope=owner_scope,
                    visibility_scope=owner_scope,
                    sensitivity="normal",
                    causation_id=str(command["event_id"]),
                    idempotency_key=f"external-sync-result:{command['command_id']}",
                    payload=result_payload,
                )
            )
        commands = []
    return {
        "commands": commands,
        "count": len(commands),
        "observation_active": observation_active,
    }


__all__ = ["router", "BatchIngestRequest"]
