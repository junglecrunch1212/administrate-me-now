"""Immutable private bridge attribution; current enrollment files are not evidence.

The central admission receipt and its preceding signed lifecycle are replayed
only as far as each source event. Revocation closes future admission without
rewriting attribution of observations that were authorized when appended.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import ValidationError

from adminme.adapters.cos_channel_acquisition import MEMBER_BRIDGE_CAPABILITIES
from adminme.bridge_authorization import (
    BRIDGE_PACK_POLICIES,
    validate_configured_pack_config,
)
from adminme.bridge_enrollment import (
    CAPABILITIES,
    canonical_bytes,
    capability_set_digest,
)
from adminme.events.log import EventLog
from adminme.events.schemas.bridge import BridgeSourceAuthorizedV1
from adminme.settings_control import SettingsControlStore

SOURCE_AUTHORIZED = "bridge.source_authorized"
SOURCE_AUTHORITY_ADAPTER = "member-bridge-source-authority"
SOURCE_AUTHORITY_ACTOR = "service:member-bridge-ingest"
LIFECYCLE_TYPES = (
    "settings.bridge_enrollment_started",
    "bridge.enrollment_progressed",
    "settings.bridge_enrollment_controlled",
)
_SOURCE_PACKS = {
    str(CAPABILITIES[capability]["pack"]): capability
    for capability in MEMBER_BRIDGE_CAPABILITIES
}
SOURCE_ADAPTERS = frozenset(
    BRIDGE_PACK_POLICIES[pack].source_adapter for pack in _SOURCE_PACKS
)
_OBSERVING = {"observation_starting", "observing", "settling", "settled", "degraded"}
_INACTIVE = {"paused", "revoked", "expired", "failed"}


def _sha(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _private(event: dict[str, Any], member: str, tenant: str) -> bool:
    return bool(
        member
        and event.get("tenant_id") == tenant
        and event.get("owner_scope")
        == event.get("visibility_scope")
        == f"private:{member}"
    )


def _container(payload: dict[str, Any], pack: str) -> str | None:
    keys = (
        ("calendar_identifier", "calendar_external_id", "calendar_id")
        if pack == "calendaring-apple-calendar-bridge"
        else (
            "target_list_identifier",
            "destination_list_external_id",
            "list_external_id",
            "list_identifier",
        )
    )
    values = {str(payload[key]) for key in keys if payload.get(key) is not None}
    return next(iter(values)) if len(values) == 1 else None


class BridgeHistory:
    """A forward-only audit cursor, owned by one fixed event-log snapshot."""

    def __init__(self, event_log: EventLog, rows: list[dict[str, Any]]) -> None:
        self.log = event_log
        self.rows = rows
        self.offset = 0
        self.states: dict[str, dict[str, Any]] = {}
        self.invalid: set[tuple[str, str]] = set()
        self.proofs: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        self.progress: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.grants: dict[tuple[str, str, str], dict[str, Any]] = {}

    @classmethod
    async def load(cls, event_log: EventLog, through: str) -> BridgeHistory:
        rows = [
            event
            async for event in event_log.read_since(
                through=through, types=[*LIFECYCLE_TYPES, SOURCE_AUTHORIZED]
            )
        ]
        return cls(event_log, rows)

    def _state(self, tenant: str) -> dict[str, Any]:
        return self.states.setdefault(
            tenant,
            {"bridge_requests": {}, "bridge_request_revisions": {}, "revision": 0},
        )

    def _lifecycle(self, event: dict[str, Any]) -> None:
        payload = event.get("payload") or {}
        tenant = str(event.get("tenant_id") or "")
        member = str(payload.get("member_id") or "")
        request = str(payload.get("request_id") or "")
        key = (tenant, member)
        state = self._state(tenant)
        current = state["bridge_requests"].get(member) or {}
        valid = bool(
            event.get("schema_version") == 1
            and _private(event, member, tenant)
            and payload.get("writes_authorized") is False
            and payload.get("provider_effects") is False
        )
        if event.get("source_adapter") == "member-settings-control-plane":
            valid = (
                valid
                and event.get("actor_identity") == member
                and event.get("source_account_id") == member
            )
            if event["type"] == "settings.bridge_enrollment_started":
                valid = (
                    valid
                    and payload.get("requested_by_member_id") == member
                    and payload.get("observation_only") is True
                )
            elif event["type"] == "settings.bridge_enrollment_controlled":
                valid = valid and payload.get("changed_by_member_id") == member
            else:
                valid = valid and payload.get("reported_by") == "central_runtime"
        elif event.get("source_adapter") == "member-bridge-enrollment-helper":
            digest = str(payload.get("device_identity_sha256") or "")
            valid = bool(
                valid
                and event["type"] == "bridge.enrollment_progressed"
                and payload.get("reported_by") == "target_helper"
                and payload.get("signature_verified") is True
                and re.fullmatch(r"[0-9a-f]{64}", digest)
                and event.get("actor_identity") == f"bridge-device:{digest}"
                and event.get("source_account_id")
                == payload.get("enrollment_id")
                == current.get("enrollment_id")
                and current.get("device_identity_sha256") in {None, digest}
                and re.fullmatch(
                    r"signature-sha256:[0-9a-f]{64}", str(event.get("raw_ref") or "")
                )
                and str(event.get("idempotency_key") or "").startswith(
                    f"bridge-progress:{request}:"
                )
            )
        elif event.get("source_adapter") == "member-bridge-enrollment-runtime":
            valid = bool(
                valid
                and event["type"] == "bridge.enrollment_progressed"
                and payload.get("reported_by") == "central_runtime"
                and payload.get("signature_verified") is False
                and event.get("actor_identity") == SOURCE_AUTHORITY_ACTOR
                and event.get("source_account_id")
                == payload.get("enrollment_id")
                == current.get("enrollment_id")
                and payload.get("device_identity_sha256")
                == current.get("device_identity_sha256")
                and payload.get("capability_state") in {"settling", "settled"}
            )
        else:
            valid = False
        if not valid or key in self.invalid:
            self.invalid.add(key)
            return
        try:
            SettingsControlStore._apply_bridge_enrollment_payload(
                state, payload, increment_global=False
            )
        except (ValueError, TypeError, KeyError):
            self.invalid.add(key)
            return
        self.progress[(tenant, member, str(event.get("idempotency_key") or ""))] = event
        label = str(payload.get("capability_state") or payload.get("state") or "")
        self.proofs[
            (tenant, member, request, str(payload.get("capability_id") or ""), label)
        ] = event

    async def _authorize(self, event: dict[str, Any]) -> None:
        try:
            receipt = BridgeSourceAuthorizedV1.model_validate(event.get("payload"))
        except ValidationError:
            return
        tenant, member = str(event.get("tenant_id") or ""), receipt.member_id
        current = self._state(tenant)["bridge_requests"].get(member) or {}
        pack = receipt.pack
        if pack not in _SOURCE_PACKS or _SOURCE_PACKS[pack] != receipt.capability_id:
            return
        policy = BRIDGE_PACK_POLICIES[pack]
        progress = (
            self.progress.get((tenant, member, receipt.progress_idempotency_key)) or {}
        )
        progress_payload = progress.get("payload") or {}
        proof_key = (tenant, member, receipt.request_id)
        paired = self.proofs.get((*proof_key, "", "bridge_paired")) or {}
        permission = (
            self.proofs.get((*proof_key, receipt.capability_id, "permission_verified"))
            or {}
        )
        attested = (
            self.proofs.get(
                (*proof_key, receipt.capability_id, "adapter_authenticated")
            )
            or {}
        )
        package = self.proofs.get((*proof_key, "", "package_ready")) or {}
        selected = current.get("capabilities") or []
        if not (
            (tenant, member) not in self.invalid
            and event.get("schema_version") == 1
            and _private(event, member, tenant)
            and event.get("source_adapter") == SOURCE_AUTHORITY_ADAPTER
            and event.get("actor_identity") == SOURCE_AUTHORITY_ACTOR
            and event.get("source_account_id") == receipt.enrollment_id
            and event.get("idempotency_key")
            == f"bridge-source-authorized:{receipt.progress_idempotency_key}"
            and event.get("raw_ref") == f"event:{receipt.enrollment_event_id}"
            and receipt.instance_id == tenant
            and current.get("request_id") == receipt.request_id
            and current.get("requested_by_member_id") == member
            and current.get("enrollment_id") == receipt.enrollment_id
            and current.get("release_commit") == receipt.release_commit
            and current.get("capability_set_sha256")
            == receipt.capability_set_sha256
            == capability_set_digest(selected)
            and bool(selected)
            and set(selected) <= MEMBER_BRIDGE_CAPABILITIES
            and receipt.capability_id in selected
            and current.get("device_identity_sha256") == receipt.device_identity_sha256
            and current.get("state") not in _INACTIVE
            and (
                current.get("capability_states", {}).get(receipt.capability_id) or {}
            ).get("state")
            == "observation_starting"
            and progress.get("source_adapter") == "member-bridge-enrollment-helper"
            and progress_payload.get("request_id") == receipt.request_id
            and progress_payload.get("capability_id") == receipt.capability_id
            and progress_payload.get("capability_state") == "observation_starting"
            and all(row for row in (paired, permission, attested, package))
            and all(
                package["payload"].get(field) == getattr(receipt, field)
                for field in (
                    "enrollment_id",
                    "release_commit",
                    "capability_set_sha256",
                )
            )
            and str(package["event_id"])
            < str(paired["event_id"])
            < str(permission["event_id"])
            < str(attested["event_id"])
            < str(progress["event_id"])
            < str(event["event_id"])
            and all(
                row.get("source_adapter") == "member-bridge-enrollment-helper"
                for row in (paired, permission, attested)
            )
        ):
            return
        try:
            normalized = validate_configured_pack_config(
                member_id=member,
                enrollment_id=receipt.enrollment_id,
                configured_packs=[pack],
                pack_config={pack: receipt.pack_config},
            )[pack]
        except ValueError:
            return
        if (
            normalized != receipt.pack_config
            or normalized.get("shared_container_ids", []) != []
            or _sha(normalized) != permission["payload"].get("evidence_sha256")
        ):
            return
        enrolled = await self.log.get(receipt.enrollment_event_id)
        if enrolled is None:
            return
        enrolled_payload = enrolled.get("payload") or {}
        if not (
            receipt.enrollment_event_id < str(paired["event_id"])
            and enrolled.get("type") == "bridge.enrolled"
            and enrolled.get("schema_version") == 1
            and _private(enrolled, member, tenant)
            and enrolled.get("source_adapter") == "member-bridge-bootstrap"
            and enrolled.get("source_account_id") == receipt.enrollment_id
            and enrolled.get("actor_identity")
            == f"bridge-device:{receipt.device_identity_sha256}"
            and enrolled.get("idempotency_key")
            == f"bridge-enrollment:{receipt.enrollment_id}"
            and enrolled_payload.get("member_id") == member
            and enrolled_payload.get("adapters_active") == []
            and hashlib.sha256(
                str(enrolled_payload.get("bridge_node_id") or "").encode()
            ).hexdigest()
            == receipt.device_identity_sha256
            and hashlib.sha256(
                str(enrolled_payload.get("tailscale_identity") or "")
                .casefold()
                .encode()
            ).hexdigest()
            == receipt.tailscale_identity_sha256
        ):
            return
        if policy.requires_capability_attestation:
            auth = await self.log.get(receipt.authentication_event_id or "")
            if auth is None:
                return
            payload = auth.get("payload") or {}
            account = normalized.get("provider_account_id")
            if not (
                str(permission["event_id"])
                < str(auth["event_id"])
                < str(attested["event_id"])
                and auth.get("type") == "adapter.authenticated"
                and auth.get("schema_version") == 2
                and _private(auth, member, tenant)
                and auth.get("source_adapter") == policy.source_adapter
                and auth.get("source_account_id") == account
                and auth.get("actor_identity")
                == f"bridge-device:{receipt.device_identity_sha256}"
                and payload.get("adapter_name") == policy.source_adapter
                and payload.get("adapter_family") == policy.family
                and payload.get("provider_account_id") == account
                and payload.get("bridge_enrollment_id") == receipt.enrollment_id
                and payload.get("token_storage_ref") == policy.permission_reference
                and frozenset(payload.get("scopes") or []) == policy.permission_scopes
                and re.fullmatch(
                    r"[0-9a-f]{64}", str(payload.get("configuration_sha256") or "")
                )
                and _sha(auth) == attested["payload"].get("evidence_sha256")
            ):
                return
        elif (
            receipt.authentication_event_id is not None
            or attested["payload"].get("evidence_sha256") is not None
        ):
            return
        self.grants[(tenant, member, pack)] = event

    async def matches(self, source: dict[str, Any]) -> bool:
        source_id = str(source["event_id"])
        while (
            self.offset < len(self.rows)
            and str(self.rows[self.offset]["event_id"]) < source_id
        ):
            row = self.rows[self.offset]
            self.offset += 1
            if row["type"] == SOURCE_AUTHORIZED:
                await self._authorize(row)
            else:
                self._lifecycle(row)
        tenant = str(source.get("tenant_id") or "")
        scope = str(source.get("owner_scope") or "")
        member = scope.removeprefix("private:") if scope.startswith("private:") else ""
        current = self._state(tenant)["bridge_requests"].get(member) or {}
        if (
            (tenant, member) in self.invalid
            or not _private(source, member, tenant)
            or current.get("state") in _INACTIVE
        ):
            return False
        candidates = [
            (pack, event)
            for (grant_tenant, grant_member, pack), event in self.grants.items()
            if grant_tenant == tenant
            and grant_member == member
            and BRIDGE_PACK_POLICIES[pack].source_adapter
            == source.get("source_adapter")
        ]
        if len(candidates) != 1:
            return False
        pack, grant = candidates[0]
        receipt = grant["payload"]
        policy = BRIDGE_PACK_POLICIES[pack]
        config = receipt["pack_config"]
        payload = source.get("payload") or {}
        account = (
            config.get("provider_account_id")
            if policy.requires_provider_account
            else policy.source_account_kind
        )
        if not (
            current.get("request_id") == receipt["request_id"]
            and current.get("enrollment_id") == receipt["enrollment_id"]
            and current.get("device_identity_sha256")
            == receipt["device_identity_sha256"]
            and (
                self.proofs.get(
                    (
                        tenant,
                        member,
                        receipt["request_id"],
                        receipt["capability_id"],
                        "observation_starting",
                    )
                )
                or {}
            ).get("idempotency_key")
            == receipt["progress_idempotency_key"]
            and (
                current.get("capability_states", {}).get(receipt["capability_id"]) or {}
            ).get("state")
            in _OBSERVING
            and source.get("actor_identity")
            == f"bridge-device:{receipt['device_identity_sha256']}"
            and source.get("source_account_id") == account
            and source.get("type") in policy.event_types
            and payload.get("member_id", member) == member
            and payload.get("provider_account_id", account) == account
            and payload.get("provider", policy.provider) == policy.provider
            and (
                policy.source_account_kind is None
                or payload.get("source_kind") == policy.source_account_kind
            )
        ):
            return False
        return not policy.container_key or _container(payload, pack) in config.get(
            "allowed_container_ids", []
        )
