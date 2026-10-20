"""Content-free historical member/provider/workspace attribution audit."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from adminme.cos_response_assurance import APPROVED, ATTEMPTED
from adminme.cos_responses import RESPONSE_CONTRACT
from adminme.events.log import EventLog
from adminme.provider_bindings import ProviderBinding
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.source_assurance import load_source_obligations


def _digest(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:20]


async def _cos_response_owner_matches(event_log: EventLog, event_id: str) -> bool:
    """Attribute the exact internal command without inventing a provider binding.

    These envelope checks establish only ownership. Source assurance separately
    validates recipient policy, both approvals, fingerprints and terminal effects.
    Private payloads used for correlation never enter the exported report.
    """
    attempt = await event_log.get(event_id)
    if attempt is None or attempt.get("type") != ATTEMPTED:
        return False
    payload = attempt.get("payload") or {}
    member = payload.get("member_id")
    approval_id = payload.get("approval_event_id")
    if (
        not isinstance(member, str)
        or not member
        or not isinstance(approval_id, str)
        or not approval_id
        or approval_id >= event_id
    ):
        return False
    approved = await event_log.get(approval_id)
    if approved is None or approved.get("type") != APPROVED:
        return False
    approval = approved.get("payload") or {}
    scope = f"private:{member}"
    return bool(
        all(
            row.get("schema_version") == 1
            and row.get("source_adapter") == "openclaw-plugin"
            and row.get("source_account_id") == "cos-response"
            and row.get("owner_scope") == scope
            and row.get("visibility_scope") == scope
            and row.get("payload", {}).get("contract") == RESPONSE_CONTRACT
            for row in (attempt, approved)
        )
        and attempt.get("tenant_id")
        and attempt.get("tenant_id") == approved.get("tenant_id")
        and approved.get("actor_identity") == member
        and attempt.get("actor_identity") in {member, COS_SERVICE_PRINCIPAL_ID}
        and approval.get("member_id") == member
        and payload.get("automatic_retry_allowed") is False
        and payload.get("openclaw_approval_event_id")
        and attempt.get("causation_id") == payload["openclaw_approval_event_id"]
        and payload.get("content_fingerprint")
        and payload.get("content_fingerprint") == approval.get("content_fingerprint")
        and payload.get("policy_event_id")
        and payload.get("policy_event_id") == approval.get("policy_event_id")
    )


async def audit_historical_attribution(
    event_log: EventLog,
    *,
    repository_root: Any,
    provider_bindings: tuple[ProviderBinding, ...],
    hash_key: bytes,
    through_event_id: str | None = None,
) -> dict[str, Any]:
    event_types = sorted(
        {
            obligation.event_type
            for obligation in load_source_obligations(repository_root)
        }
    )
    watermark = through_event_id
    if watermark is None:
        watermark = await event_log.latest_event_id()
    bindings_by_account: dict[str, list[ProviderBinding]] = {}
    for binding in provider_bindings:
        bindings_by_account.setdefault(
            binding.provider_account_id.casefold(), []
        ).append(binding)
    groups: dict[tuple[str, str, str, str, str], int] = {}
    total = 0
    attributed = 0
    async for event in event_log.read_attribution_metadata_since(
        through=watermark or "",
        types=event_types,
    ):
        total += 1
        owner_scope = str(event.get("owner_scope") or "")
        account = str(event.get("source_account_id") or "")
        adapter = str(event.get("source_adapter") or "")
        event_type = str(event.get("type") or "")
        bindings = bindings_by_account.get(account.casefold(), [])
        matching_bindings = [
            binding for binding in bindings if owner_scope == binding.owner_scope
        ]
        matching_coordinates = {
            (binding.owner_kind, binding.owner_id, binding.workspace)
            for binding in matching_bindings
        }
        binding_state = "unattributed"
        workspace = "unattributed"
        if event_type == ATTEMPTED:
            # This command is emitted by the governed internal reply route,
            # not the enrolled provider adapter. Never fall back to provider
            # matching when its member/private correlation is malformed.
            if await _cos_response_owner_matches(event_log, str(event["event_id"])):
                binding_state = "internal_cos_member_source_reconciled"
                workspace = "personal"
                attributed += 1
        elif len(matching_coordinates) == 1:
            binding_state = "matched"
            workspace = next(iter(matching_coordinates))[2]
            attributed += 1
        elif owner_scope == "shared:household" and event_type == "messaging.received":
            # Early central Gmail facts used household-shared visibility even
            # though the provider account remained member-owned. Attribute
            # that legacy visibility only through one exact reviewed Gmail
            # account/adapter binding; visibility itself never grants owner
            # authority and no content inference is permitted.
            legacy_shared_bindings = [
                binding
                for binding in bindings
                if binding.owner_kind == "member"
                and binding.kind == "messaging-gmail-api"
                and adapter == binding.kind
            ]
            if len(legacy_shared_bindings) == 1:
                binding_state = "legacy_shared_visibility_reconciled"
                workspace = legacy_shared_bindings[0].workspace
                attributed += 1
            elif bindings:
                binding_state = "ambiguous_or_owner_mismatch"
        elif bindings:
            binding_state = "ambiguous_or_owner_mismatch"
        else:
            # Pre-binding Google Calendar adapters used the owning member id as
            # ``source_account_id``.  That coordinate is not a provider account
            # and must not be silently treated as one.  It is nevertheless
            # deterministic when the private owner scope agrees and exactly one
            # reviewed Calendar binding now exists for that member.
            legacy_member = (
                account if account and owner_scope == f"private:{account}" else ""
            )
            legacy_calendar = [
                binding
                for binding in provider_bindings
                if binding.member_id == legacy_member
                and binding.kind == "calendaring-google-calendar"
            ]
            if adapter == "calendaring-google-calendar" and len(legacy_calendar) == 1:
                binding_state = "legacy_member_coordinate_reconciled"
                workspace = legacy_calendar[0].workspace
                attributed += 1
            elif (
                adapter == "member-reminders"
                and event_type == "external_sync.write_requested"
                and legacy_member
                and any(
                    binding.member_id == legacy_member for binding in provider_bindings
                )
            ):
                # This is an internal command source, not a provider adapter.
                # The member/private coordinate itself is its complete historic
                # attribution and does not invent a provider-account binding.
                binding_state = "legacy_internal_member_source_reconciled"
                workspace = "personal"
                attributed += 1
        key = (
            _digest(hash_key, owner_scope),
            _digest(hash_key, account.casefold()),
            workspace,
            _digest(hash_key, f"{adapter}|{event_type}"),
            binding_state,
        )
        groups[key] = groups.get(key, 0) + 1
    rows = [
        {
            "member_scope_hash": key[0],
            "provider_account_hash": key[1],
            "workspace": key[2],
            "provenance_hash": key[3],
            "binding_state": key[4],
            "event_count": count,
        }
        for key, count in sorted(groups.items())
    ]
    return {
        "contract": "historical-attribution-v1",
        "state": "healthy" if total == attributed else "degraded",
        "watermark_event_id": watermark,
        "source_event_count": total,
        "attributed_event_count": attributed,
        "unattributed_event_count": total - attributed,
        "groups": rows,
        "content_exposed": False,
        "raw_identifiers_exposed": False,
    }


__all__ = ["audit_historical_attribution"]
