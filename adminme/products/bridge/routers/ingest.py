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
import copy
from contextlib import asynccontextmanager
import hashlib
import json
import re
from typing import Any, AsyncIterator, Literal

import time

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError

from adminme.adapters.cos_channel_acquisition import (
    MEMBER_BRIDGE_ACQUISITION_CONTRACT,
    authorized_member_bridge_packs,
    member_bridge_capability_ceiling,
)
from adminme.apple_service_permissions import (
    EVENT_TYPE as APPLE_PERMISSION_EVENT,
    PACKS as APPLE_SERVICE_PACKS,
    PROVIDERS as APPLE_SERVICE_PROVIDERS,
    command_permission,
    command_proof,
    permission_marker,
    validate_command_scope,
    write_policy,
)
from adminme.bridge_source_contract import validate_member_source_coordinates
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
    reviewed_release,
    validate_capability_transition,
    validate_enrollment_transition,
)
from adminme.bridge_history import (
    BridgeHistory,
    LIFECYCLE_TYPES,
    SOURCE_AUTHORIZED,
    SOURCE_AUTHORITY_ACTOR,
    SOURCE_AUTHORITY_ADAPTER,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import AppendValidationError
from adminme.events.writer import EventSink
from adminme.messages_reply import REPLY_CONTRACT, approval_is_current
from adminme.products.auth import session_from_request
from adminme.settings_control import SettingsControlStore
from adminme.products.bridge.auth import (
    EnrollmentMemberIdResolver,
    MemberIdResolver,
    resolve_member_id,
    resolve_tailscale_identity,
    unexpired_exported_enrollment,
)

router = APIRouter(prefix="/api/bridge", tags=["bridge"])
ENROLLMENT_HEADER = "X-AdministrateMe-Bridge-Enrollment"
PERMISSION_RECOVERY_CONTRACT = "administrateme-bridge-permission-recovery-v1"
PERMISSION_STATE_HEADER = "X-AdministrateMe-Bridge-State-SHA256"
INITIAL_START_RECOVERY_HEADER = "X-AdministrateMe-Bridge-Initial-Start-Recovery"


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


def _member_source_acquisition(
    request: Request, tailscale_identity: str, *, require_device: bool = True
) -> frozenset[str] | None:
    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    if settings is None:
        return None
    ceiling = member_bridge_capability_ceiling(settings.adapter_config)
    if ceiling is None:
        return None
    if not ceiling:
        raise HTTPException(403, detail="bridge_ingestion_paused_for_disposable_test")
    record = _bridge_record(request, tailscale_identity)
    member_id = str(record.get("member_id") or "")
    control = getattr(runtime, "settings_control", None)
    wizard = control.bridge_request_for(member_id) if control is not None else None
    if not isinstance(wizard, dict):
        raise HTTPException(403, detail="member_bridge_explicit_enrollment_required")
    try:
        return authorized_member_bridge_packs(
            settings.adapter_config,
            member_id=member_id,
            instance_id=_expected_tenant_id(request) or "",
            record=record,
            wizard=wizard,
            require_device=require_device,
        )
    except ValueError as exc:
        raise HTTPException(403, detail=str(exc)) from exc


def _require_acquisition_envelope(
    request: Request,
    tailscale_identity: str,
    envelope: EventEnvelope,
    packs: frozenset[str] | None,
    *,
    provisional: bool,
) -> None:
    if packs is None:
        return
    record = _bridge_record(request, tailscale_identity)
    wizard = request.app.state.runtime.settings_control.bridge_request_for(
        str(record["member_id"])
    )
    if provisional:
        node = envelope.payload.get("bridge_node_id")
        if (
            wizard.get("state") != "device_identity_verified"
            or not isinstance(node, str)
            or hashlib.sha256(node.encode()).hexdigest()
            != record.get("device_identity_sha256")
        ):
            raise HTTPException(403, detail="member_bridge_device_proof_required")
        return
    # Already attempted actions still need their exact terminal audit. Existing
    # command/result correlation guards below remain mandatory; admitting a
    # result neither delivers a command nor authorizes a provider effect.
    if envelope.type in {"external_sync.write_completed", "external_sync.write_failed"}:
        return
    selected = [
        BRIDGE_PACK_POLICIES[pack]
        for pack in packs
        if BRIDGE_PACK_POLICIES[pack].source_adapter == envelope.source_adapter
    ]
    if len(selected) != 1:
        raise HTTPException(403, detail="member_bridge_source_not_selected")
    policy = selected[0]
    if policy.pack not in record.get("configured_packs", []):
        raise HTTPException(403, detail="member_bridge_source_not_configured")
    if envelope.type in {"adapter.authenticated", "adapter.error"}:
        # The established exact capability-evidence guard validates these before
        # attestation. It does not admit content or an enabled connector.
        return
    capability_id = next(
        key for key, value in CAPABILITIES.items() if value["pack"] == policy.pack
    )
    capability = (wizard.get("capability_states") or {}).get(capability_id) or {}
    if (
        policy.pack not in record.get("enabled_packs", [])
        or policy.pack not in record.get("authorized_packs", [])
        or capability.get("state")
        not in {"observation_starting", "observing", "settling", "settled", "degraded"}
    ):
        raise HTTPException(403, detail="member_bridge_observation_not_enabled")


def _stamp_acquisition_device(
    request: Request,
    identity: str,
    envelope: EventEnvelope,
    packs: frozenset[str] | None,
) -> EventEnvelope:
    if packs is None or envelope.type.startswith("external_sync."):
        return envelope
    if envelope.event_id or envelope.recorded_at:
        raise HTTPException(403, detail="member_bridge_central_event_identity_required")
    actor = (
        f"bridge-device:{_bridge_record(request, identity)['device_identity_sha256']}"
    )
    if envelope.actor_identity not in {None, actor}:
        raise HTTPException(403, detail="member_bridge_device_actor_mismatch")
    return envelope.model_copy(
        update={"actor_identity": actor, "event_at_ms": int(time.time() * 1000)}
    )


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
    if (
        provider == "apple_notes"
        and APPLE_SERVICE_PACKS[provider] in authorization.packs
    ):
        policy = write_policy(provider)
    expected_account = (
        authorization.provider_account_for(policy.pack) if policy is not None else None
    )
    # A completed attempt is audited under its immutable original grant even
    # after revocation. This lookup grants no new dispatch authority.
    event_log = getattr(request.app.state, "event_log", None)
    original = (
        await event_log.get(payload.get("command_event_id"))
        if event_log is not None and isinstance(payload.get("command_event_id"), str)
        else None
    )
    original_payload = original.get("payload", {}) if isinstance(original, dict) else {}
    original_body = original_payload.get("payload") or {}
    marker = (
        original_body.get("apple_service_permission")
        if isinstance(original_body, dict)
        else None
    )
    historical_grant = None
    if provider in APPLE_SERVICE_PROVIDERS and isinstance(marker, dict):
        grant_event = (
            await event_log.get(marker.get("event_id"))
            if event_log is not None and isinstance(marker.get("event_id"), str)
            else None
        )
        if (
            isinstance(grant_event, dict)
            and grant_event.get("type") == APPLE_PERMISSION_EVENT
            and grant_event.get("tenant_id") == envelope.tenant_id
            and grant_event.get("owner_scope") == envelope.owner_scope
            and grant_event.get("visibility_scope") == envelope.visibility_scope
        ):
            grant_payload = grant_event.get("payload", {})
            if (
                grant_payload.get("enabled") is True
                and grant_payload.get("member_id") == authorization.member_id
                and grant_event.get("actor_identity") == authorization.member_id
            ):
                historical_grant = {
                    **grant_payload["coordinates"],
                    "event_id": grant_event["event_id"],
                    "revision": grant_payload["revision"],
                    "coordinates_sha256": grant_payload["coordinates_sha256"],
                }
                try:
                    validate_command_scope(historical_grant, original_payload)
                    if (
                        permission_marker(historical_grant) != marker
                        or historical_grant["enrollment_id"]
                        != authorization.enrollment_id
                    ):
                        historical_grant = None
                except (ValueError, KeyError):
                    historical_grant = None
        if historical_grant is not None:
            expected_account = historical_grant["provider_account_id"]
    violations: list[str] = []
    if (provider == "apple_notes" or marker is not None) and historical_grant is None:
        violations.append("original_apple_service_permission")
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
        if command.get("tenant_id") != envelope.tenant_id:
            violations.append("command_tenant_id")
        if policy is not None and policy.container_key is not None:
            request_body = requested.get("payload")
            container = (
                _container_from_payload(request_body, policy)
                if isinstance(request_body, dict)
                else None
            )
            allowed = (
                historical_grant["allowed_container_ids"]
                if historical_grant is not None
                else authorization.pack_config.get(policy.pack, {}).get(
                    "allowed_container_ids", []
                )
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
            or payload.get("target_list_id")
            or payload.get("destination_list_external_id")
            or payload.get("list_external_id")
            or payload.get("list_identifier")
        )
    elif policy.container_key == "container_external_id":
        value = payload.get("container_external_id")
    elif policy.container_key == "folder_external_id":
        value = payload.get("folder_external_id")
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


@asynccontextmanager
async def _bridge_control_lock(request: Request) -> AsyncIterator[None]:
    """Serialize admission and progress with owner control and source settlement."""

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        # The standalone ingest composition has no enrollment control plane.
        yield
        return
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        yield


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
    leaking provider content into wizard state. The caller holds the same
    settings-control lock as admission, helper progress and owner revocation.
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
    current = runtime.settings_control.bridge_request_for(member_id)
    if (
        not isinstance(current, dict)
        or current.get("request_id") != request_id
        or record.get("status") != "active"
        or current.get("state") in {"paused", "revoked", "expired", "failed"}
        or not isinstance(current.get("capability_states"), dict)
    ):
        return
    capability_row = current["capability_states"].get(capability_id)
    # Admission is allowed during daemon startup, but only the helper's
    # genuine observing report opens settlement (including the catch-up path).
    if (
        not isinstance(capability_row, dict)
        or capability_row.get("state") != "observing"
    ):
        return
    observed = await runtime.event_log.get(observation_event_id)
    if observed is None:
        raise RuntimeError("admitted bridge observation is not durable")
    evidence_sha256 = hashlib.sha256(canonical_bytes(observed)).hexdigest()
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
    async with _bridge_control_lock(request):
        return await _ingest_single_locked(request, payload)


async def _ingest_single_locked(
    request: Request, payload: dict[str, Any]
) -> dict[str, str]:
    tailscale_identity = resolve_tailscale_identity(request)
    acquisition_packs = _member_source_acquisition(request, tailscale_identity)
    member_id, provisional = _resolve_single_ingest_member(
        request, tailscale_identity, payload
    )
    envelope = _stamp_owner_scope(payload, member_id, _expected_tenant_id(request))
    _require_acquisition_envelope(
        request,
        tailscale_identity,
        envelope,
        acquisition_packs,
        provisional=provisional,
    )
    envelope = await _enforce_active_bridge_contract(
        request,
        tailscale_identity,
        envelope,
        member_id,
        provisional_proof_validated=provisional,
    )
    _enforce_member_knowledge_contract(request, envelope, member_id)
    envelope = _stamp_acquisition_device(
        request, tailscale_identity, envelope, acquisition_packs
    )
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
    async with _bridge_control_lock(request):
        return await _ingest_batch_locked(request, body)


async def _ingest_batch_locked(
    request: Request, body: BatchIngestRequest
) -> dict[str, Any]:
    tailscale_identity = resolve_tailscale_identity(request)
    acquisition_packs = _member_source_acquisition(request, tailscale_identity)
    member_id = resolve_member_id(tailscale_identity, _get_resolver(request))
    record = _bridge_record(request, tailscale_identity)
    if record.get("status") != "active" or record.get("member_id") != member_id:
        raise HTTPException(403, detail="bridge_enrollment_not_active")

    stamped: list[EventEnvelope] = []
    for offset, raw in enumerate(body.envelopes):
        try:
            envelope = _stamp_owner_scope(raw, member_id, _expected_tenant_id(request))
            _require_acquisition_envelope(
                request,
                tailscale_identity,
                envelope,
                acquisition_packs,
                provisional=False,
            )
            envelope = await _enforce_active_bridge_contract(
                request,
                tailscale_identity,
                envelope,
                member_id,
                provisional_proof_validated=False,
            )
            _enforce_member_knowledge_contract(request, envelope, member_id)
            envelope = _stamp_acquisition_device(
                request, tailscale_identity, envelope, acquisition_packs
            )
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


def _progress_stale_before_mutation(
    body: EnrollmentProgressRequest, *, reason: str
) -> HTTPException:
    # This is an owner-authenticated refusal, not a claim that the signature
    # was accepted. It must only be returned before progress_operation (which
    # consumes the nonce), index changes, or event writes.
    return HTTPException(
        409,
        detail={
            "error": "bridge_progress_stale_before_mutation",
            "contract": PERMISSION_RECOVERY_CONTRACT,
            "request_id": body.request_id,
            "nonce_sha256": hashlib.sha256(body.nonce.encode("utf-8")).hexdigest(),
            "effects": "none",
            "reason": reason,
        },
    )


def _permission_state(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": current["state"],
        "capability_states": {
            key: row["state"] for key, row in current["capability_states"].items()
        },
    }


async def _permission_history(
    runtime: Any, member_id: str, through: str
) -> BridgeHistory:
    # Use the predecessor's unchanged attribution validator, restricted to this
    # owner and fixed snapshot. No new lifecycle/schema is needed for catch-up.
    rows = [
        event
        async for event in runtime.event_log.read_since(
            through=through,
            owner_scope=f"private:{member_id}",
            types=[*LIFECYCLE_TYPES, SOURCE_AUTHORIZED],
        )
    ]
    return BridgeHistory(runtime.event_log, rows)


async def _catch_up_started_observation(
    request: Request,
    *,
    identity: str,
    member_id: str,
    capability_id: str,
    through: str,
) -> None:
    """Consume existing admitted evidence after the genuine observing report.

    A first event can arrive while the helper is still returning from daemon
    startup. It stays an ordinary immutable source event; the helper's later
    observing report opens settlement, without re-reading a provider, emitting
    content again, or inventing a new lifecycle understood only by this release.
    """

    runtime = request.app.state.runtime
    record = _bridge_record(request, identity)
    if record.get("acquisition_policy") != MEMBER_BRIDGE_ACQUISITION_CONTRACT:
        return
    history = await _permission_history(runtime, member_id, through)
    authorities = [
        row
        for row in history.rows
        if row["type"] == SOURCE_AUTHORIZED
        and row.get("payload", {}).get("request_id") == record["request_id"]
        and row.get("payload", {}).get("capability_id") == capability_id
    ]
    if not authorities:
        return
    authority = authorities[-1]
    receipt = authority["payload"]
    pack = str(CAPABILITIES[capability_id]["pack"])
    if receipt["pack_config"] != record.get("pack_config", {}).get(pack) or any(
        receipt[key] != record.get(key)
        for key in (
            "request_id",
            "enrollment_id",
            "member_id",
            "instance_id",
            "release_commit",
            "capability_set_sha256",
            "device_identity_sha256",
        )
    ):
        return
    policy = BRIDGE_PACK_POLICIES[pack]
    async for row in runtime.event_log.read_attribution_metadata_since(
        cursor=str(authority["event_id"]),
        through=through,
        types=list(policy.event_types),
    ):
        if (
            row["owner_scope"] != f"private:{member_id}"
            or row["source_adapter"] != policy.source_adapter
            or row["type"] in {"adapter.authenticated", "adapter.error"}
        ):
            continue
        coordinates = await runtime.event_log.get_bridge_attribution_coordinates(
            row["event_id"]
        )
        if coordinates is None or not await history.matches(coordinates):
            continue
        actual_authority = history.grants.get(
            (runtime.settings.instance.tenant_id, member_id, pack)
        )
        if (
            actual_authority is None
            or actual_authority["event_id"] != authority["event_id"]
        ):
            continue
        observed = await runtime.event_log.get(row["event_id"])
        if observed is None:
            raise RuntimeError("admitted bridge observation is not durable")
        envelope = EventEnvelope(
            **{
                key: value
                for key, value in observed.items()
                if key in EventEnvelope.model_fields
            }
        )
        if _capability_for_observation(envelope) != capability_id:
            continue
        await _settle_bridge_capability_from_observation(
            request,
            tailscale_identity=identity,
            member_id=member_id,
            envelope=envelope,
            observation_event_id=row["event_id"],
        )
        return


async def _observation_reconciliation(
    runtime: Any, current: dict[str, Any], record: dict[str, Any], through: str
) -> dict[str, Any] | None:
    """Prove only the observation suffix after the last accepted helper report.

    A snapshot is not a command receipt: an unknown helper POST still requires
    recovery. Owner control, another helper command, or changed coordinates
    cannot masquerade as a monotonic observation acknowledgement.
    """

    replay: dict[str, Any] = {
        "bridge_requests": {},
        "bridge_request_revisions": {},
        "revision": 0,
    }
    member_id = str(current["member_id"])
    base: dict[str, Any] | None = None
    advances: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    count = 0
    try:
        async for event in runtime.event_log.read_since(
            through=through,
            owner_scope=f"private:{member_id}",
            types=list(LIFECYCLE_TYPES),
        ):
            count += 1
            if count > 4096:
                return None
            payload = event.get("payload") or {}
            SettingsControlStore._apply_bridge_enrollment_payload(
                replay, payload, increment_global=False
            )
            if payload.get("request_id") != current["request_id"]:
                continue
            if (
                event.get("source_adapter") == "member-bridge-enrollment-helper"
                and payload.get("reported_by") == "target_helper"
                and payload.get("signature_verified") is True
                and event.get("actor_identity")
                == f"bridge-device:{record['device_identity_sha256']}"
            ):
                base = {
                    "base_event_id": event["event_id"],
                    "base": _permission_state(replay["bridge_requests"][member_id]),
                }
                pending = []
            elif base is not None:
                if (
                    event.get("source_adapter") != "member-bridge-enrollment-runtime"
                    or payload.get("reported_by") != "central_runtime"
                    or payload.get("signature_verified") is not False
                    or event.get("actor_identity") != SOURCE_AUTHORITY_ACTOR
                    or payload.get("capability_state") not in {"settling", "settled"}
                ):
                    base = None
                    pending = []
                else:
                    pending.append(event)
        if replay["bridge_requests"].get(member_id) != current or base is None:
            return None
        history = await _permission_history(runtime, member_id, through)
        latest_authority = {
            row["payload"]["capability_id"]: row["event_id"]
            for row in history.rows
            if row["type"] == SOURCE_AUTHORIZED
            and row.get("tenant_id") == runtime.settings.instance.tenant_id
            and row.get("payload", {}).get("request_id") == current["request_id"]
            and row.get("payload", {}).get("member_id") == member_id
        }
        for event in pending:
            payload = event["payload"]
            capability = str(payload["capability_id"])
            pack = str(CAPABILITIES[capability]["pack"])
            source_id = str(event.get("raw_ref") or "").removeprefix("event:")
            observed = await runtime.event_log.get(source_id)
            if (
                observed is None
                or source_id >= str(event["event_id"])
                or not await history.matches(observed)
                or hashlib.sha256(canonical_bytes(observed)).hexdigest()
                != payload.get("evidence_sha256")
                or payload.get("device_identity_sha256")
                != record["device_identity_sha256"]
                or payload.get("enrollment_id") != record["enrollment_id"]
            ):
                return None
            authority = history.grants.get(
                (runtime.settings.instance.tenant_id, member_id, pack)
            )
            if authority is None or authority["event_id"] != latest_authority.get(
                capability
            ):
                return None
            receipt = authority["payload"]
            if receipt["pack_config"] != record.get("pack_config", {}).get(pack) or any(
                receipt[key] != record.get(key)
                for key in (
                    "request_id",
                    "enrollment_id",
                    "member_id",
                    "instance_id",
                    "release_commit",
                    "capability_set_sha256",
                    "device_identity_sha256",
                )
            ):
                return None
            advances.append(
                {
                    "event_id": event["event_id"],
                    "capability_id": capability,
                    "capability_state": payload["capability_state"],
                    "previous_state": payload["previous_state"],
                    "state": payload["state"],
                    "observation_event_id": source_id,
                    "evidence_sha256": payload["evidence_sha256"],
                    "last_successful_observation": payload.get(
                        "last_successful_observation"
                    ),
                    "source_authorization_event_id": authority["event_id"],
                    "configured_source_sha256": hashlib.sha256(
                        canonical_bytes(receipt["pack_config"])
                    ).hexdigest(),
                }
            )
    except (KeyError, TypeError, ValueError):
        return None
    return {**base, "advances": advances}


async def _initial_permission_retry_capabilities(
    runtime: Any, current: dict[str, Any], record: dict[str, Any], through: str
) -> list[str]:
    """Retry only initial permission failures, never an activated source repair."""

    candidates = {
        capability
        for capability, row in current["capability_states"].items()
        if row.get("state") in {"permission_denied", "prerequisite_missing", "degraded"}
    }
    count = 0
    async for event in runtime.event_log.read_since(
        through=through,
        owner_scope=f"private:{current['member_id']}",
        types=["bridge.enrollment_progressed"],
    ):
        count += 1
        if count > 4096:
            return []
        payload = event.get("payload") or {}
        if payload.get("request_id") == current["request_id"] and payload.get(
            "capability_state"
        ) in {
            "adapter_authenticated",
            "observation_starting",
            "observing",
            "settling",
            "settled",
            "active",
        }:
            candidates.discard(payload.get("capability_id"))
    # Durable history is primary. A retained index that already records later
    # authority also closes retry eligibility, even if its history is damaged.
    activated = (
        set(record.get("authorized_packs") or [])
        | set(record.get("enabled_packs") or [])
        | set(record.get("capability_attestations") or {})
    )
    return sorted(
        capability
        for capability in candidates
        if str(CAPABILITIES[capability]["pack"]) not in activated
    )


async def _initial_start_recovery(
    runtime: Any, current: dict[str, Any], record: dict[str, Any], through: str
) -> dict[str, dict[str, str]]:
    """Prove a never-observed initial start, without resetting its permission.

    This is only central evidence. The sealed helper must independently prove
    the failed local start rolled back and no daemon or unknown operation is
    outstanding. Previously observing sources never acquire this continuation.
    """

    member = str(current["member_id"])
    tenant = runtime.settings.instance.tenant_id
    request_id = current["request_id"]
    if (
        record.get("status") != "active"
        or record.get("acquisition_policy") != MEMBER_BRIDGE_ACQUISITION_CONTRACT
        or current.get("state")
        not in {"degraded", "capabilities_testing", "partially_active", "settling"}
        or current.get("observation_only") is not True
        or current.get("writes_authorized") is not False
    ):
        return {}
    rows: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since(
        through=through,
        owner_scope=f"private:{member}",
        types=[*LIFECYCLE_TYPES, SOURCE_AUTHORIZED],
    ):
        rows.append(event)
        if len(rows) > 4096:
            return {}
    history = BridgeHistory(runtime.event_log, rows)
    try:
        # Use the established immutable lifecycle/source validator. No synthetic
        # source event is needed to advance its audit cursor to this snapshot.
        for event in rows:
            if event["type"] == SOURCE_AUTHORIZED:
                await history._authorize(event)
            else:
                history._lifecycle(event)
        replay = history.states.get(tenant, {}).get("bridge_requests", {}).get(member)
        if (tenant, member) in history.invalid or replay != current:
            return {}
        relevant = [
            event
            for event in rows
            if event.get("payload", {}).get("request_id") == request_id
        ]
        if any(
            event["type"] == "settings.bridge_enrollment_controlled"
            for event in relevant
        ):
            return {}
        result: dict[str, dict[str, str]] = {}
        for capability in ("apple_calendar", "apple_reminders", "apple_notes"):
            capability_row = current["capability_states"].get(capability) or {}
            if capability_row.get("state") != "degraded":
                continue
            pack = str(CAPABILITIES[capability]["pack"])
            policy = BRIDGE_PACK_POLICIES[pack]
            config = record.get("pack_config", {}).get(pack)
            if (
                not isinstance(config, dict)
                or pack not in record.get("configured_packs", [])
                or pack not in record.get("authorized_packs", [])
                or pack not in record.get("enabled_packs", [])
                or record.get("pack_capability_states", {}).get(pack)
                != (
                    "attested"
                    if policy.requires_capability_attestation
                    else "not_required"
                )
            ):
                continue
            progress = [
                event
                for event in relevant
                if event["type"] == "bridge.enrollment_progressed"
                and event["payload"].get("capability_id") == capability
            ]
            states = [event["payload"].get("capability_state") for event in progress]
            if "permission_verified" not in states:
                continue
            start = states.index("permission_verified")
            chain = progress[start:]
            if (
                states[start:]
                != [
                    "permission_verified",
                    "adapter_authenticated",
                    "observation_starting",
                    "degraded",
                ]
                or any(
                    state
                    not in {
                        "requested",
                        "implementation_available",
                        "permission_required",
                        "permission_denied",
                        "prerequisite_missing",
                    }
                    for state in states[:start]
                )
                or chain[-1]["payload"].get("reason") != "local_capability_test_failed"
                or any(
                    event.get("source_adapter") != "member-bridge-enrollment-helper"
                    or event["payload"].get("reported_by") != "target_helper"
                    or event["payload"].get("signature_verified") is not True
                    or event["payload"].get("device_identity_sha256")
                    != record.get("device_identity_sha256")
                    for event in chain
                )
            ):
                continue
            authorities = [
                event
                for event in relevant
                if event["type"] == SOURCE_AUTHORIZED
                and event["payload"].get("capability_id") == capability
            ]
            if len(authorities) != 1:
                continue
            authority = authorities[0]
            receipt = authority["payload"]
            if (
                history.grants.get((tenant, member, pack)) != authority
                or not chain[2]["event_id"]
                < authority["event_id"]
                < chain[3]["event_id"]
                or receipt["pack_config"] != config
                or any(
                    receipt[key] != record.get(key)
                    for key in (
                        "request_id",
                        "enrollment_id",
                        "member_id",
                        "instance_id",
                        "release_commit",
                        "capability_set_sha256",
                        "device_identity_sha256",
                    )
                )
                or receipt["enrollment_event_id"] != record.get("event_id")
                or receipt["authentication_event_id"]
                != (record.get("capability_attestations", {}).get(pack) or {}).get(
                    "event_id"
                )
            ):
                continue
            observed = False
            count = 0
            async for event in runtime.event_log.read_attribution_metadata_since(
                cursor=receipt["enrollment_event_id"],
                through=through,
                types=sorted(
                    policy.event_types - {"adapter.authenticated", "adapter.error"}
                ),
            ):
                count += 1
                if count > 4096 or (
                    event.get("owner_scope") == f"private:{member}"
                    and event.get("source_adapter") == policy.source_adapter
                ):
                    observed = True
                    break
            if observed:
                continue
            configuration_sha = hashlib.sha256(canonical_bytes(config)).hexdigest()
            result[capability] = {
                "configured_source_sha256": configuration_sha,
                "proof_sha256": hashlib.sha256(
                    canonical_bytes(
                        {
                            "contract": "administrateme-bridge-initial-start-recovery-v1",
                            "source_authority": authority,
                            "initial_start_history": chain,
                            "current_state": _permission_state(current),
                        }
                    )
                ).hexdigest(),
            }
        return result
    except (KeyError, TypeError, ValueError):
        return {}


@router.get("/enrollment/status")
async def enrollment_status(
    request: Request, request_id: str = Query(min_length=8, max_length=120)
) -> dict[str, Any]:
    """Exact-owner, content-free state; never reconcile an unknown command."""

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or runtime.event_log is None:
        raise HTTPException(503, detail="bridge_enrollment_runtime_unavailable")
    session = session_from_request(request)
    if (
        session.auth_role != "principal"
        or session.auth_member_id != session.view_member_id
    ):
        raise HTTPException(403, detail="bridge_status_requires_self_principal")
    identity = resolve_tailscale_identity(request)
    async with _bridge_control_lock(request):
        record = _bridge_record(request, identity)
        current = runtime.settings_control.bridge_request_for(session.auth_member_id)
        if (
            record.get("member_id") != session.auth_member_id
            or record.get("request_id") != request_id
            or not isinstance(current, dict)
            or current.get("request_id") != request_id
            or record.get("status") != "active"
        ):
            raise HTTPException(403, detail="bridge_status_coordinate_mismatch")
        _member_source_acquisition(request, identity)
        through = await runtime.event_log.latest_event_id()
        if through is None:
            raise HTTPException(503, detail="bridge_status_history_unavailable")
        try:
            central_commit = reviewed_release(
                runtime.settings.repository_root,
                deployment_release_commit=runtime.bridge_enrollment.deployment_release_commit,
                require_deployment_release=runtime.settings.deployment_evidence_required,
            )
        except BridgeEnrollmentError as exc:
            raise HTTPException(
                503, detail="bridge_status_release_unavailable"
            ) from exc
        bound_current = copy.deepcopy(current)
        bound_current["instance_id"] = record["instance_id"]
        return {
            "contract": PERMISSION_RECOVERY_CONTRACT,
            "central_release_commit": central_commit,
            "identity": {
                "authenticated_member_id": session.auth_member_id,
                "view_member_id": session.view_member_id,
                "actions_as": session.auth_member_id,
                "reads_as": session.view_member_id,
                "role": session.auth_role,
                "authentication_method": "Tailscale",
                "viewing_as": False,
                "can_mutate": True,
            },
            "current": bound_current,
            "configured_source_sha256": {
                capability: hashlib.sha256(canonical_bytes(config)).hexdigest()
                for capability in current["capabilities"]
                if isinstance(
                    (
                        config := record.get("pack_config", {}).get(
                            str(CAPABILITIES[capability]["pack"])
                        )
                    ),
                    dict,
                )
            },
            "reconciliation": await _observation_reconciliation(
                runtime, current, record, through
            ),
            "permission_retry_capabilities": await _initial_permission_retry_capabilities(
                runtime, current, record, through
            ),
            "initial_start_recovery": await _initial_start_recovery(
                runtime, current, record, through
            ),
            "content_exposed": False,
            "provider_effects": False,
        }


@router.post("/enrollment/progress", status_code=status.HTTP_202_ACCEPTED)
async def enrollment_progress(
    request: Request, body: EnrollmentProgressRequest
) -> dict[str, Any]:
    async with _bridge_control_lock(request):
        return await _enrollment_progress_locked(request, body)


async def _enrollment_progress_locked(
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
    expected_state = request.headers.get(PERMISSION_STATE_HEADER)
    if expected_state is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_state):
            raise HTTPException(
                422, detail="bridge_progress_state_precondition_invalid"
            )
        if (
            expected_state
            != hashlib.sha256(canonical_bytes(_permission_state(wizard))).hexdigest()
        ):
            raise _progress_stale_before_mutation(
                body, reason="state_snapshot_mismatch"
            )
    acquisition_packs = _member_source_acquisition(
        request, tailscale_identity, require_device=False
    )
    if (
        acquisition_packs is not None
        and body.pack_config is not None
        and body.pack_config.get("shared_container_ids", []) != []
    ):
        raise HTTPException(403, detail="member_bridge_private_sources_required")
    if acquisition_packs is not None and body.pack_config is not None:
        try:
            validate_member_source_coordinates(
                str(CAPABILITIES.get(str(body.capability_id), {}).get("pack") or ""),
                body.pack_config,
            )
        except ValueError as exc:
            raise HTTPException(
                422, detail="member_bridge_source_coordinate_invalid"
            ) from exc
    if wizard.get("state") != body.previous_state:
        raise _progress_stale_before_mutation(body, reason="previous_state_mismatch")
    recovery_proof = request.headers.get(INITIAL_START_RECOVERY_HEADER)
    initial_start = (
        transport_record.get("acquisition_policy") == MEMBER_BRIDGE_ACQUISITION_CONTRACT
        and body.capability_id in {"apple_calendar", "apple_reminders", "apple_notes"}
        and body.capability_state == "observation_starting"
        and ((wizard.get("capability_states") or {}).get(body.capability_id) or {}).get(
            "state"
        )
        == "degraded"
    )
    if initial_start or recovery_proof is not None:
        through = await runtime.event_log.latest_event_id()
        eligible = (
            await _initial_start_recovery(runtime, wizard, transport_record, through)
            if through is not None and initial_start
            else {}
        )
        if (
            recovery_proof is None
            or not re.fullmatch(r"[0-9a-f]{64}", recovery_proof)
            or body.state != body.previous_state
            or body.pack_config is not None
            or body.capability_event_id is not None
            or recovery_proof
            != eligible.get(str(body.capability_id), {}).get("proof_sha256")
        ):
            raise _progress_stale_before_mutation(
                body, reason="initial_start_recovery_unavailable"
            )
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
            if body.capability_state == "observing" and capability_row.get("state") in {
                "settling",
                "settled",
            }:
                raise _progress_stale_before_mutation(
                    body, reason="capability_observation_advanced"
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
        with runtime.bridge_enrollment.progress_operation(
            request_id=body.request_id,
            enrollment_id=body.enrollment_id,
            member_id=body.member_id,
            tailscale_identity=tailscale_identity,
            payload=unsigned,
            signature=body.signature,
        ):
            device_digest = hashlib.sha256(
                body.device_node_id.encode("utf-8")
            ).hexdigest()
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
                resolver = EnrollmentMemberIdResolver(
                    runtime.settings.bridge_enrollments
                )
                runtime.member_id_resolver = resolver
                request.app.state.member_id_resolver = resolver

            if body.capability_id is not None:
                capability = CAPABILITIES.get(body.capability_id)
                if (
                    capability is None
                    or capability.get("implementation") != "available"
                ):
                    raise HTTPException(422, detail="bridge_capability_unavailable")
                if body.capability_state == "permission_verified":
                    if not isinstance(body.pack_config, dict):
                        raise HTTPException(
                            422, detail="exact_source_coordinate_required"
                        )
                    try:
                        evidence_sha256 = (
                            runtime.bridge_enrollment.configure_capability(
                                request_id=body.request_id,
                                member_id=body.member_id,
                                capability_id=body.capability_id,
                                pack_config=body.pack_config,
                            )
                        )
                    except BridgeEnrollmentError as exc:
                        raise HTTPException(
                            422, detail={"error": exc.code, "message": str(exc)}
                        ) from exc
                elif body.pack_config is not None:
                    raise HTTPException(422, detail="source_coordinate_not_expected")
                if body.capability_state == "adapter_authenticated":
                    if body.capability_event_id:
                        evidence_event = await runtime.event_log.get(
                            body.capability_event_id
                        )
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
                    if (
                        not body.capability_event_id
                        or not body.last_successful_observation
                    ):
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
                    evidence_sha256 = hashlib.sha256(
                        canonical_bytes(observed)
                    ).hexdigest()

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
    progress_envelope = EventEnvelope(
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
    envelopes = [progress_envelope]
    if (
        acquisition_packs is not None
        and body.capability_state == "observation_starting"
    ):
        record = _bridge_record(request, tailscale_identity)
        pack = str(CAPABILITIES[str(body.capability_id)]["pack"])
        authentication = (record.get("capability_attestations") or {}).get(pack) or {}
        envelopes.append(
            EventEnvelope(
                event_at_ms=progress_envelope.event_at_ms,
                tenant_id=progress_envelope.tenant_id,
                type=SOURCE_AUTHORIZED,
                schema_version=1,
                occurred_at=progress_at,
                source_adapter=SOURCE_AUTHORITY_ADAPTER,
                source_account_id=body.enrollment_id,
                owner_scope=f"private:{body.member_id}",
                visibility_scope=f"private:{body.member_id}",
                actor_identity=SOURCE_AUTHORITY_ACTOR,
                idempotency_key=f"bridge-source-authorized:{progress_envelope.idempotency_key}",
                raw_ref=f"event:{record['event_id']}",
                payload={
                    "contract": MEMBER_BRIDGE_ACQUISITION_CONTRACT,
                    "request_id": body.request_id,
                    "enrollment_id": body.enrollment_id,
                    "member_id": body.member_id,
                    "instance_id": body.instance_id,
                    "release_commit": body.release_commit,
                    "capability_set_sha256": body.capability_set_sha256,
                    "device_identity_sha256": device_digest,
                    "tailscale_identity_sha256": hashlib.sha256(
                        tailscale_identity.casefold().encode()
                    ).hexdigest(),
                    "capability_id": body.capability_id,
                    "pack": pack,
                    "pack_config": record["pack_config"][pack],
                    "enrollment_event_id": record["event_id"],
                    "authentication_event_id": authentication.get("event_id"),
                    "progress_idempotency_key": progress_envelope.idempotency_key,
                    "observation_only": True,
                    "provider_writes": "deny",
                    "content_exposed": False,
                },
            )
        )
    # Progress and frozen admission coordinates commit together, before the
    # helper can start observations. A partial receipt cannot grant access.
    event_id = (await runtime.events.write_batch(envelopes))[0]
    try:
        mutation = runtime.settings_control.apply_bridge_enrollment_event(event_payload)
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if body.capability_state == "observing" and body.capability_id is not None:
        await _catch_up_started_observation(
            request,
            identity=tailscale_identity,
            member_id=body.member_id,
            capability_id=body.capability_id,
            through=event_id,
        )
    # Acknowledge exactly the immutable helper mutation, even when catch-up
    # has already settled its source. The next read-only status proves that
    # separate central suffix; it never rewrites the command acknowledgement.
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
    acquisition_packs = _member_source_acquisition(request, identity)
    acquisition_read_only = acquisition_packs is not None
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
    if APPLE_SERVICE_PACKS["apple_notes"] in authorization.packs:
        allowed.add("apple_notes")
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
        if event.get("tenant_id") != (_expected_tenant_id(request) or "tenant-default"):
            continue
        payload = event["payload"]
        command_policy = authorization.policy_for_provider(
            str(payload.get("provider") or "")
        )
        apple_grant = None
        apple_authorization_denied = False
        if payload.get("provider") in APPLE_SERVICE_PROVIDERS and (
            acquisition_read_only
            or payload.get("provider") == "apple_notes"
            or (payload.get("payload") or {}).get("apple_service_permission")
        ):
            runtime = getattr(request.app.state, "runtime", None)
            if runtime is None:
                continue
            try:
                apple_grant = command_permission(runtime, member_id, payload)
            except (ValueError, KeyError):
                # Keep an attempted command reachable for its cached terminal
                # receipt. An unattempted stale command is explicitly denied at
                # the edge, never released by a later grant.
                marker = (payload.get("payload") or {}).get("apple_service_permission")
                grant_event = (
                    await event_log.get(marker["event_id"])
                    if isinstance(marker, dict)
                    and isinstance(marker.get("event_id"), str)
                    else None
                )
                if (
                    not isinstance(grant_event, dict)
                    or grant_event.get("type") != APPLE_PERMISSION_EVENT
                    or grant_event.get("tenant_id") != event.get("tenant_id")
                    or grant_event.get("owner_scope") != owner_scope
                    or grant_event.get("actor_identity") != member_id
                ):
                    continue
                previous = grant_event.get("payload", {})
                if (
                    previous.get("enabled") is not True
                    or previous.get("member_id") != member_id
                ):
                    continue
                apple_grant = {
                    **previous["coordinates"],
                    "event_id": grant_event["event_id"],
                    "revision": previous["revision"],
                    "coordinates_sha256": previous["coordinates_sha256"],
                }
                try:
                    validate_command_scope(apple_grant, payload)
                    if (
                        permission_marker(apple_grant) != marker
                        or apple_grant["enrollment_id"] != authorization.enrollment_id
                    ):
                        continue
                except (ValueError, KeyError):
                    continue
                apple_authorization_denied = True
            command_policy = write_policy(str(payload["provider"]))
        expected_account = (
            apple_grant["provider_account_id"]
            if apple_grant is not None
            else authorization.provider_account_for(command_policy.pack)
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
                in (
                    apple_grant["allowed_container_ids"]
                    if apple_grant is not None
                    else authorization.pack_config.get(command_policy.pack, {}).get(
                        "allowed_container_ids", []
                    )
                )
            )
        ):
            commands.append(
                {
                    "event_id": event["event_id"],
                    "schema_version": event.get("schema_version", 1),
                    **payload,
                    **(
                        {"apple_authorization_denied": True}
                        if apple_authorization_denied
                        else {}
                    ),
                }
            )
            if len(commands) >= limit:
                break

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None and commands:
        permitted: list[dict[str, Any]] = []
        sink = _get_event_sink(request)
        for command in commands:
            command_body = command.get("payload")
            if (
                command.get("provider") != "bluebubbles"
                or not isinstance(command_body, dict)
                or command_body.get("contract") != REPLY_CONTRACT
            ):
                permitted.append(command)
                continue
            current, reason = approval_is_current(
                runtime,
                member_id=member_id,
                command_payload=command_body,
            )
            if current:
                permitted.append(command)
                continue
            now = EventEnvelope.now_utc_iso()
            await sink.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=_expected_tenant_id(request) or "tenant-default",
                    type="external_sync.write_failed",
                    schema_version=2,
                    occurred_at=now,
                    source_adapter="bridge-command-current-authority-gate",
                    source_account_id=str(command["provider_account_id"]),
                    owner_scope=owner_scope,
                    visibility_scope=owner_scope,
                    sensitivity="sensitive",
                    causation_id=str(command["event_id"]),
                    idempotency_key=f"external-sync-result:{command['command_id']}",
                    payload={
                        "command_id": command["command_id"],
                        "member_id": member_id,
                        "provider": command["provider"],
                        "operation": command["operation"],
                        "error_kind": "permission",
                        "message": f"Messages reply authority is no longer current: {reason}",
                        "retryable": False,
                        "failed_at": now,
                        "command_event_id": command["event_id"],
                        "domain_approval_event_id": command["domain_approval_event_id"],
                        "openclaw_approval_event_id": command[
                            "openclaw_approval_event_id"
                        ],
                        "provider_account_id": command["provider_account_id"],
                        "request_fingerprint": command["request_fingerprint"],
                        "provider_called": False,
                        "origin_proposal_id": command.get("origin_proposal_id"),
                    },
                )
            )
        commands = permitted
    observation = getattr(runtime, "observation", None)
    observation_active = True if observation is None else await observation.is_active()
    if (observation_active or acquisition_read_only) and commands:
        sink = _get_event_sink(request)
        permitted_apple_commands = []
        for command in commands:
            if command.get("provider") in APPLE_SERVICE_PROVIDERS and (
                command.get("payload") or {}
            ).get("apple_service_permission"):
                # The edge may already hold an attempted or completed receipt.
                # Observation must not invent a no-effect terminal for it.
                if observation_active:
                    command["apple_authorization_denied"] = True
                permitted_apple_commands.append(command)
                continue
            now = EventEnvelope.now_utc_iso()
            is_v2 = int(command.get("schema_version") or 1) >= 2
            result_payload: dict[str, Any] = {
                "command_id": command["command_id"],
                "member_id": member_id,
                "provider": command["provider"],
                "operation": command["operation"],
                "external_id": None,
                "provider_revision": None,
                "result": {
                    "outcome": "suppressed",
                    "reason": (
                        "member_bridge_acquisition_read_only"
                        if acquisition_read_only
                        else "observation_mode_active"
                    ),
                    "interaction_id": (command.get("payload") or {}).get(
                        "interaction_id"
                    ),
                    "automatic_retry": False,
                },
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
                    source_adapter=(
                        "bridge-command-acquisition-gate"
                        if acquisition_read_only
                        else "bridge-command-observation-gate"
                    ),
                    source_account_id=str(command["provider_account_id"]),
                    owner_scope=owner_scope,
                    visibility_scope=owner_scope,
                    sensitivity="normal",
                    causation_id=str(command["event_id"]),
                    idempotency_key=f"external-sync-result:{command['command_id']}",
                    payload=result_payload,
                )
            )
        commands = permitted_apple_commands
    return {
        "commands": commands,
        "count": len(commands),
        "observation_active": observation_active,
        "acquisition_read_only": acquisition_read_only,
    }


__all__ = ["router", "BatchIngestRequest"]


class AppleCommandAuthorizationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    event_id: str = Field(min_length=1, max_length=240)
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/commands/{command_id}/apple-authorization")
async def authorize_apple_command(
    command_id: str, body: AppleCommandAuthorizationRequest, request: Request
) -> dict[str, Any]:
    """Recheck current permission at the edge immediately before an attempt."""
    identity = resolve_tailscale_identity(request)
    member_id = resolve_member_id(identity, _get_resolver(request))
    _member_source_acquisition(request, identity)
    authorization = _active_authorization(request, identity)
    runtime = getattr(request.app.state, "runtime", None)
    if authorization is None or authorization.member_id != member_id or runtime is None:
        raise HTTPException(403, "Apple bridge authority is unavailable")
    if runtime._observation_active:
        raise HTTPException(409, "Observation mode suppresses Apple writes")
    event_log = getattr(request.app.state, "event_log", None)
    if event_log is None:
        raise HTTPException(503, "Apple command history is unavailable")
    event = await event_log.get(body.event_id)
    if (
        not isinstance(event, dict)
        or event.get("type") != "external_sync.write_requested"
        or event.get("schema_version") != 2
        or event.get("tenant_id") != runtime.settings.instance.tenant_id
        or event.get("owner_scope") != f"private:{member_id}"
        or event.get("visibility_scope") != f"private:{member_id}"
        or event.get("payload", {}).get("command_id") != command_id
    ):
        raise HTTPException(404, "Apple command not found")
    if (
        await event_log.get_by_idempotency(
            event["tenant_id"], f"external-sync-result:{command_id}"
        )
        is not None
    ):
        raise HTTPException(409, "Apple command already has a terminal result")
    command = {
        "event_id": event["event_id"],
        "schema_version": event["schema_version"],
        **event["payload"],
    }
    lock = runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())
    async with lock:
        await runtime.settings_control.replay_apple_service_permission_events(
            event_log, tenant_id=runtime.settings.instance.tenant_id
        )
        if runtime._observation_active:
            raise HTTPException(409, "Observation mode suppresses Apple writes")
        try:
            proof = command_proof(runtime, member_id, command)
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from exc
        grant = proof["grant"]
        if (
            grant["enrollment_id"] != authorization.enrollment_id
            or grant["configuration_sha256"] != body.configuration_sha256
        ):
            raise HTTPException(403, "The Apple bridge configuration changed")
        return {"authorization": proof}
