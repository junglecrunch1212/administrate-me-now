"""Content-free historical member/provider/workspace attribution audit."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from adminme.bridge_history import BridgeHistory, SOURCE_ADAPTERS
from adminme.cos_response_assurance import APPROVED, ATTEMPTED
from adminme.cos_responses import RESPONSE_CONTRACT
from adminme.direct_intake import DirectIntakeProcessor
from adminme.events.log import EventLog
from adminme.provider_bindings import HistoricalProviderBinding, ProviderBinding
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_SERVICE_PRINCIPAL_ID,
    service_private_scope,
)
from adminme.source_assurance import load_source_obligations


type AttributionBinding = ProviderBinding | HistoricalProviderBinding


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
    standing = approval.get("authorization_method") == "standing_self_reply"
    if standing:
        from adminme.cos_response_assurance import _standing_valid

        references = [
            await event_log.get(approval.get(key, ""))
            for key in ("standing_authorization_event_id", "source_message_event_id")
        ]
        if not _standing_valid(
            approved, {row["event_id"]: row for row in references if row is not None}
        ):
            return False
    return bool(
        attempt.get("schema_version") == 1
        and approved.get("schema_version") in ({2} if standing else {1, 2})
        and all(
            row.get("source_adapter") == "openclaw-plugin"
            and row.get("source_account_id") == "cos-response"
            and row.get("owner_scope") == scope
            and row.get("visibility_scope") == scope
            and row.get("payload", {}).get("contract") == RESPONSE_CONTRACT
            for row in (attempt, approved)
        )
        and attempt.get("tenant_id")
        and attempt.get("tenant_id") == approved.get("tenant_id")
        and approved.get("actor_identity")
        == (COS_SERVICE_PRINCIPAL_ID if standing else member)
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


async def _direct_intake_owner_matches(
    event_log: EventLog,
    cue: dict[str, Any],
    bindings: tuple[AttributionBinding, ...],
    watermark: str,
) -> bool:
    """Reconcile frozen member ownership through its original service receipt.

    The service message precedes its member cue; the routing receipt is written
    immediately after that cue. All three must exist inside this audit snapshot.
    This does not re-resolve today's route table or grant provider authority.
    """
    payload = cue.get("payload") or {}
    member = payload.get("member_id")
    source_id = payload.get("source_event_id")
    tenant = cue.get("tenant_id")
    if not all(
        isinstance(value, str) and value for value in (member, source_id, tenant)
    ):
        return False
    member, source_id, tenant = str(member), str(source_id), str(tenant)
    source = await event_log.get(source_id)
    route = await event_log.get_by_idempotency(
        tenant, f"direct-intake-route:{source_id}"
    )
    if source is None or route is None:
        return False
    parent = source.get("payload") or {}
    routed = route.get("payload") or {}
    service = parent.get("service_principal_id")
    account = parent.get("provider_account_id")
    provider = parent.get("provider") or parent.get("source_channel")
    native = parent.get("native_message_id")
    channel = parent.get("channel_id")
    if not all(
        isinstance(value, str) and value
        for value in (service, account, provider, native, channel)
    ):
        return False
    service, account, provider, native, channel = (
        str(value) for value in (service, account, provider, native, channel)
    )
    service_scope = service_private_scope(service)
    service_bindings = [
        binding
        for binding in bindings
        if binding.owner_kind == "service_principal"
        and binding.owner_id == service
        and binding.service_principal_id == service
        and binding.member_id is None
        and binding.provider_account_id == account
        and binding.owner_scope == service_scope
        and source.get("source_adapter")
        in {binding.kind, f"{binding.kind}:{binding.adapter_id}"}
    ]
    receipt_seed = json.dumps(
        [tenant, provider, account, native], separators=(",", ":")
    )
    receipt_key = (
        "direct-intake-receipt:" + hashlib.sha256(receipt_seed.encode()).hexdigest()
    )
    text, complete, issues = DirectIntakeProcessor._submission_evidence(parent)
    correlation = source.get("correlation_id") or source_id
    scope = f"private:{member}"
    return bool(
        len(service_bindings) == 1
        and source_id
        < str(cue.get("event_id"))
        < str(route.get("event_id"))
        <= watermark
        and source.get("tenant_id") == route.get("tenant_id") == tenant
        and source.get("type") == "messaging.received"
        and source.get("schema_version") == 8
        and source.get("owner_scope") == source.get("visibility_scope") == service_scope
        and source.get("actor_identity") in {None, service}
        and source.get("source_account_id") == account
        and parent.get("member_id") is None
        and parent.get("authenticated_member_route") is True
        and isinstance(parent.get("trusted_route_key"), str)
        and parent.get("trusted_route_key")
        and parent.get("route_source") == "exact_authenticated_metadata"
        and cue.get("type") == "triage.intent.submitted"
        and cue.get("schema_version") == 2
        and cue.get("idempotency_key") == receipt_key
        and payload.get("submission_kind") == "member_forwarded"
        and payload.get("intent_id") == f"direct-intake-cue:{source_id}"
        and payload.get("client_request_id")
        == hashlib.sha256(f"direct-intake:{source_id}".encode()).hexdigest()[:32]
        and payload.get("source_surface") == "openclaw"
        and payload.get("intake_channel") == provider
        and payload.get("text") == text
        and payload.get("evidence_complete") is complete
        and payload.get("evidence_issues") == issues
        and route.get("type") == "direct_intake.routed"
        and route.get("schema_version") == 1
        and route.get("idempotency_key") == f"direct-intake-route:{source_id}"
        and routed.get("source_event_id") == source_id
        and routed.get("channel_id") == channel
        and routed.get("service_principal_id") == service
        and routed.get("route_source") == "exact_authenticated_metadata"
        and routed.get("route_state") == "resolved"
        and routed.get("routing_scope") == "member_private"
        and routed.get("routing_target_id") == member
        and routed.get("cue_kind") == "primary_triage_cue"
        and routed.get("cue_event_id") == cue.get("event_id")
        and routed.get("authority") == "proposal_only"
        and routed.get("provider_effect_permitted") is False
        and routed.get("source_content_exposed") is False
        and all(
            row.get("source_adapter") == "direct-intake"
            and row.get("source_account_id") == account
            and row.get("owner_scope") == row.get("visibility_scope") == scope
            and row.get("actor_identity") is None
            and row.get("causation_id") == source_id
            and row.get("correlation_id") == correlation
            and row.get("sensitivity") == source.get("sensitivity")
            for row in (cue, route)
        )
    )


async def _openclaw_submission_owner_matches(
    event_log: EventLog, source: dict[str, Any], watermark: str
) -> bool:
    """Attribute native forwards through their immutable authenticated receipt.

    An OpenClaw account alias is not a provider account. The original or a
    completed duplicate request must link this exact private source to its
    authenticated member inside the audit snapshot. Current grants and channel
    configuration cannot retrospectively grant or revoke historical ownership.
    """
    payload = source.get("payload") or {}
    member = payload.get("member_id")
    digest = payload.get("client_request_id")
    account = source.get("source_account_id")
    source_id = str(source.get("event_id") or "")
    scope = f"private:{member}"
    issues = payload.get("evidence_issues")
    if not (
        isinstance(member, str)
        and member
        and isinstance(digest, str)
        and re.fullmatch(r"[a-f0-9]{64}", digest)
        and isinstance(account, str)
        and account
        and source_id
        and source.get("tenant_id")
        and source.get("type") == "triage.intent.submitted"
        and source.get("schema_version") == 2
        and source.get("source_adapter") == "openclaw-submission"
        and source.get("owner_scope") == source.get("visibility_scope") == scope
        and source.get("actor_identity") == member
        and source.get("sensitivity") in {"normal", "sensitive"}
        and source.get("causation_id") is None
        and source.get("idempotency_key") == f"openclaw-forward:{digest}"
        and re.fullmatch(r"request-sha256:[a-f0-9]{64}", str(source.get("raw_ref")))
        and payload.get("intent_id") == f"forward_{digest[:48]}"
        and payload.get("source_surface") == "openclaw"
        and payload.get("submission_kind") == "member_forwarded"
        and payload.get("source_event_id") is None
        and payload.get("kind_hint") is None
        and payload.get("intake_channel") in {"apple_imessage", "imessage"}
        and isinstance(payload.get("text"), str)
        and 0 < len(payload["text"]) <= 32_000
        and issues in ([], ["unsupported_channel_attachment"])
        and payload.get("evidence_complete") is (not issues)
    ):
        return False
    for receipt in await event_log.get_by_causation(source_id):
        audit = receipt.get("payload") or {}
        correlation = receipt.get("correlation_id")
        if not (
            source_id < str(receipt.get("event_id")) <= watermark
            and receipt.get("type") == "openclaw.tool_call.recorded"
            and receipt.get("tenant_id") == source.get("tenant_id")
            and receipt.get("source_adapter") == "openclaw-plugin"
            and receipt.get("source_account_id") == account
            and receipt.get("owner_scope") == receipt.get("visibility_scope") == scope
            and receipt.get("causation_id") == source_id
            and isinstance(correlation, str)
            and correlation
            and receipt.get("idempotency_key")
            == f"openclaw:{member}:openclaw.tool_call.recorded:submissions.forward:{correlation}"
            and receipt.get("raw_ref")
            == "request-sha256:"
            + hashlib.sha256(
                json.dumps(audit, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            and audit.get("operation") == "submissions.forward"
            and audit.get("account_id") == account
            and audit.get("channel_id") in {"imessage", "bluebubbles"}
            and audit.get("result_event_ids") == [source_id]
            and audit.get("call_kind") in {"bridge", "slash_command"}
        ):
            continue
        if receipt.get("schema_version") == 1:
            actor = audit.get("actor_id")
            owner_matches = (
                receipt.get("actor_identity") == member
                and audit.get("member_id") == member
                and audit.get("correlation_id") == correlation
            )
        elif receipt.get("schema_version") == 2:
            actor = audit.get("openclaw_actor_id")
            owner_matches = (
                receipt.get("actor_identity")
                == audit.get("actor_principal_id")
                == audit.get("represented_service_principal_id")
                == COS_SERVICE_PRINCIPAL_ID
                and audit.get("operator_principal_id") == COS_AUTOMATION_OPERATOR_ID
                and audit.get("context_scope") == "member_private"
                and audit.get("context_member_id")
                == audit.get("on_behalf_of_member_id")
                == audit.get("resource_owner_principal_id")
                == member
                and audit.get("audience") == [member]
                and audit.get("visibility") == [scope]
                and audit.get("approving_member_ids") == [member]
                and audit.get("approval_quorum") == "member_owner"
                and bool(audit.get("authority_grant_id"))
                and "read_context" in audit.get("permissions", [])
                and audit.get("request_correlation_id") == correlation
                and audit.get("causation_event_ids") == [source_id]
            )
        else:
            continue
        if not owner_matches or not isinstance(actor, str) or not actor:
            continue
        if audit["call_kind"] == "bridge":
            native = audit.get("message_id")
            if not isinstance(native, str) or not native:
                continue
            identity = [audit["channel_id"], account, actor, native]
            if (
                hashlib.sha256(
                    json.dumps(identity, separators=(",", ":")).encode()
                ).hexdigest()
                != digest
            ):
                continue
        return True
    return False


async def audit_historical_attribution(
    event_log: EventLog,
    *,
    repository_root: Any,
    provider_bindings: tuple[AttributionBinding, ...],
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
    bindings_by_account: dict[str, list[AttributionBinding]] = {}
    for binding in provider_bindings:
        bindings_by_account.setdefault(
            binding.provider_account_id.casefold(), []
        ).append(binding)
    bridge_history = await BridgeHistory.load(event_log, watermark or "")
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
        submission = (
            await event_log.get(str(event["event_id"]))
            if event_type == "triage.intent.submitted"
            else None
        )
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
        elif submission is not None and (
            adapter == "direct-intake"
            or str((submission.get("payload") or {}).get("intent_id") or "").startswith(
                "direct-intake-cue:"
            )
            or str(submission.get("idempotency_key") or "").startswith(
                "direct-intake-receipt:"
            )
        ):
            # A forwarded cue is an internal member source, not a provider
            # observation. Malformed linkage cannot fall back to account matching.
            if await _direct_intake_owner_matches(
                event_log, submission, provider_bindings, watermark or ""
            ):
                binding_state = "internal_forwarded_member_source_reconciled"
                workspace = "personal"
                attributed += 1
        elif submission is not None and (
            adapter == "openclaw-submission"
            or str(submission.get("idempotency_key") or "").startswith(
                "openclaw-forward:"
            )
            or str((submission.get("payload") or {}).get("intent_id") or "").startswith(
                "forward_"
            )
        ):
            # Account aliases never borrow an unrelated provider binding when
            # the authenticated submission receipt is absent or contradictory.
            if await _openclaw_submission_owner_matches(
                event_log, submission, watermark or ""
            ):
                binding_state = "internal_openclaw_member_source_reconciled"
                workspace = "personal"
                attributed += 1
        elif adapter in SOURCE_ADAPTERS:
            # Bridge accounts and Notes source kinds are not central provider
            # bindings: Calendar/Reminders are rejected from central inventory,
            # and Notes has no central adapter pack. The old generic match could
            # only borrow an unrelated account. Missing or contradictory evidence must
            # never borrow a matching owner/account from current configuration.
            source = await event_log.get_bridge_attribution_coordinates(
                str(event["event_id"])
            )
            if source is not None and await bridge_history.matches(source):
                binding_state = "member_bridge_source_reconciled"
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
