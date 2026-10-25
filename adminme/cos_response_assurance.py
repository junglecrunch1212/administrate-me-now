"""Content-free accounting of the exact CoS response approval/effect chain."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
import re
import hashlib
from typing import Any

from adminme.cos_response_policy import POLICY_EVENT_TYPE, recipient_decision
from adminme.cos_responses import RESPONSE_CONTRACT, content_fingerprint
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

APPROVED = "cos.response.approved"
ATTEMPTED = "cos.response.attempted"
RESULT = "cos.response.result"
OUTBOUND_TYPES = {"external.sent", "observation.suppressed"}
CONTEXT_TYPES = {
    APPROVED,
    ATTEMPTED,
    RESULT,
    POLICY_EVENT_TYPE,
    "openclaw.approval.received",
    "cos.reply.openclaw_authorized",
    "cos.conversation.reply_authorized",
    "cos.conversation.instruction",
    "cos.action.request_grounded",
    "cos.reply.reminder_requested",
    "cos.reply.reminder_bound",
    "cos.reply.reminder_cancelled",
    "cos.reply.reminder_authorized",
    "cos.channels.activated",
    "cos.channels.deactivated",
    "triage.intent.submitted",
}
AUDIT_TYPES = CONTEXT_TYPES | OUTBOUND_TYPES


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def _private(event: dict[str, Any], approved: dict[str, Any]) -> bool:
    member = approved["payload"].get("member_id")
    return bool(
        member
        and event.get("tenant_id") == approved.get("tenant_id")
        and event.get("owner_scope") == f"private:{member}"
        and event.get("visibility_scope") == f"private:{member}"
        and event.get("actor_identity") in {member, COS_SERVICE_PRINCIPAL_ID}
    )


def _conversation_valid(
    approved: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> bool:
    p = approved.get("payload") or {}
    source = by_id.get(str(p.get("conversation_source_event_id")), {})
    payload = source.get("payload") or {}
    submitted, started = _time(payload.get("submitted_at")), _time(p.get("approved_at"))
    return bool(
        p.get("authorization_method") == "authenticated_conversation"
        and p.get("channel") == "email"
        and _private(source, approved)
        and approved.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
        and source.get("type") == "cos.conversation.instruction"
        and source.get("schema_version") == 1
        and source.get("source_adapter") == "cos-conversation"
        and source.get("actor_identity")
        == payload.get("member_id")
        == p.get("member_id")
        and payload.get("authentication_rule")
        == "enrolled_receiver_authenticated_sender"
        and payload.get("channel_id") == "gmail"
        and payload.get("account_id") == p.get("provider_account_id")
        and payload.get("thread_id") == p.get("thread_id")
        and payload.get("source_message_event_id")
        and p.get("in_reply_to")
        and approved.get("causation_id") == source.get("event_id")
        and submitted is not None
        and started is not None
        and 0 <= (started - submitted).total_seconds() <= 3600
        and str(source.get("event_id")) < str(approved.get("event_id"))
    )


def _standing_valid(
    approved: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    *,
    before: str | None = None,
) -> bool:
    from adminme.cos_channel_operation import CosChannelActivationV2
    from adminme.cos_self_replies import self_reply_binding

    payload = approved.get("payload") or {}
    scheduled = payload.get("authorization_method") == "requested_self_reminder"
    activation = by_id.get(str(payload.get("standing_authorization_event_id")), {})
    source = by_id.get(str(payload.get("source_message_event_id")), {})
    try:
        grant = CosChannelActivationV2.model_validate(activation["payload"])
        cutoff = before or str(approved["event_id"])
        changes = [
            event
            for event in by_id.values()
            if event.get("tenant_id") == approved.get("tenant_id")
            and event.get("type")
            in {"cos.channels.activated", "cos.channels.deactivated"}
            and str(event["event_id"]) < cutoff
        ]
        latest = max(changes, key=lambda event: str(event["event_id"]), default={})
        submitted = _time(source["payload"]["submitted_at"])
        approved_at = _time(payload["approved_at"])
        enabled = _time(grant.enabled_at)
        return bool(
            payload.get("authorization_method")
            in {"standing_self_reply", "requested_self_reminder"}
            and payload.get("channel") == "imessage"
            and payload.get("subject") == ""
            and approved.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
            and approved.get("causation_id")
            == (
                payload.get("reminder_event_id")
                if scheduled
                else activation.get("event_id")
            )
            and activation.get("type") == "cos.channels.activated"
            and activation.get("actor_identity") == grant.operator_principal_id
            and activation.get("tenant_id") == approved.get("tenant_id")
            and latest.get("event_id") == activation.get("event_id")
            and payload.get("self_reply_binding")
            == self_reply_binding(
                payload["member_id"],
                payload["provider_account_id"],
                payload["recipient"],
            )
            and payload["self_reply_binding"] in grant.self_reply_bindings
            and source.get("type") == "triage.intent.submitted"
            and _private(source, approved)
            and source.get("actor_identity") == payload.get("member_id")
            and source["payload"].get("member_id") == payload.get("member_id")
            and source["payload"].get("submission_kind") == "member_forwarded"
            and submitted is not None
            and approved_at is not None
            and enabled is not None
            and submitted <= approved_at
            and enabled <= (approved_at if scheduled else submitted)
            and (
                _reminder_valid(approved, by_id, before=cutoff)
                if scheduled
                else (approved_at - submitted).total_seconds() <= 3600
            )
            and str(source["event_id"]) < str(approved["event_id"])
            and str(activation["event_id"])
            < str(approved["event_id"] if scheduled else source["event_id"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _reminder_valid(
    approved: dict[str, Any], by_id: dict[str, dict[str, Any]], *, before: str
) -> bool:
    payload = approved["payload"]
    reminder = by_id.get(str(payload.get("reminder_event_id")), {})
    bound = by_id.get(str(payload.get("reminder_binding_event_id")), {})
    source = by_id.get(str(payload.get("source_message_event_id")), {})
    saved = reminder.get("payload") or {}
    origin = by_id.get(str(saved.get("activation_event_id")), {})
    from adminme.cos_channel_operation import CosChannelActivationV2

    try:
        origin_grant = CosChannelActivationV2.model_validate(origin.get("payload"))
    except (TypeError, ValueError):
        return False
    origin_enabled = _time(origin_grant.enabled_at)
    submitted = _time(source.get("payload", {}).get("submitted_at"))
    requested = _time(saved.get("requested_at"))
    due = _time(saved.get("due_at"))
    approved_at = _time(payload.get("approved_at"))
    return bool(
        reminder.get("type") == "cos.reply.reminder_requested"
        and _private(reminder, approved)
        and reminder.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
        and reminder.get("causation_id") == source.get("event_id")
        and saved.get("member_id") == payload.get("member_id")
        and saved.get("source_message_event_id")
        == payload.get("source_message_event_id")
        and origin.get("type") == "cos.channels.activated"
        and origin.get("tenant_id") == approved.get("tenant_id")
        and origin.get("actor_identity") == origin_grant.operator_principal_id
        and saved.get("self_reply_binding") in origin_grant.self_reply_bindings
        and origin_enabled is not None
        and submitted is not None
        and origin_enabled <= submitted
        and str(origin.get("event_id")) < str(source.get("event_id"))
        and saved.get("self_reply_binding") == payload.get("self_reply_binding")
        and saved.get("body") == payload.get("body")
        and saved.get("provenance", {}).get("account_id")
        == payload.get("provider_account_id")
        and saved.get("provenance", {}).get("actor_id") == payload.get("recipient")
        and bound.get("type") == "cos.reply.reminder_bound"
        and _private(bound, approved)
        and bound.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
        and bound.get("causation_id") == reminder.get("event_id")
        and bound.get("payload", {}).get("member_id") == payload.get("member_id")
        and bound.get("payload", {}).get("reminder_event_id")
        == reminder.get("event_id")
        and bound.get("payload", {}).get("job_id")
        and submitted is not None
        and requested is not None
        and due is not None
        and approved_at is not None
        and 0 <= (requested - submitted).total_seconds() <= 3600
        and 60 <= (due - requested).total_seconds() <= 366 * 86400
        and 0 <= (approved_at - due).total_seconds() <= 600
        and str(source.get("event_id"))
        < str(reminder.get("event_id"))
        < str(bound.get("event_id"))
        < str(approved["event_id"])
        and not any(
            event.get("type") == "cos.reply.reminder_cancelled"
            and event.get("tenant_id") == approved.get("tenant_id")
            and event.get("payload", {}).get("reminder_event_id")
            == reminder.get("event_id")
            and str(event["event_id"]) < before
            for event in by_id.values()
        )
    )


def _approval_valid(approved: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    from adminme.requested_email_assurance import requested_email_approval_valid

    payload = approved.get("payload") or {}
    policy = by_id.get(str(payload.get("policy_event_id")), {})
    policy_payload = policy.get("payload") or {}
    approved_at = _time(payload.get("approved_at"))
    expires_at = _time(payload.get("expires_at"))
    try:
        return bool(
            approved.get("type") == APPROVED
            and payload.get("contract") == RESPONSE_CONTRACT
            and (
                requested_email_approval_valid(approved, by_id)
                if payload.get("authorization_method") == "requested_email"
                else _conversation_valid(approved, by_id)
                if payload.get("authorization_method") == "authenticated_conversation"
                else _standing_valid(approved, by_id)
                if payload.get("authorization_method")
                in {"standing_self_reply", "requested_self_reminder"}
                else approved.get("actor_identity") == payload.get("member_id")
            )
            and _private(approved, approved)
            and policy.get("type") == POLICY_EVENT_TYPE
            and policy.get("tenant_id") == approved.get("tenant_id")
            and policy_payload.get("revision") == payload.get("policy_revision")
            and str(policy["event_id"]) < str(approved["event_id"])
            and approved_at is not None
            and expires_at is not None
            and approved_at < expires_at
            and (expires_at - approved_at).total_seconds() <= 600
            and payload["content_fingerprint"]
            == content_fingerprint(
                account_id=payload["provider_account_id"],
                recipient=payload["recipient"],
                subject=payload["subject"],
                body=payload["body"],
                channel=payload["channel"],
            )
            and recipient_decision(
                {**policy_payload, "event_id": policy["event_id"]},
                channel=payload["channel"],
                recipients=[payload["recipient"]],
                member_id=payload["member_id"],
            )["allowed"]
        )
    except (KeyError, TypeError, ValueError):
        return False


def _attempt_valid(
    attempt: dict[str, Any],
    approved: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    policies: list[dict[str, Any]],
) -> bool:
    payload = attempt.get("payload") or {}
    approval = approved.get("payload") or {}
    openclaw_id = payload.get("openclaw_approval_event_id")
    openclaw = by_id.get(str(openclaw_id), {})
    receipt = openclaw.get("payload") or {}
    attempted = _time(payload.get("attempted_at"))
    start, end = _time(approval.get("approved_at")), _time(approval.get("expires_at"))
    issued, consumed, expires = (
        _time(receipt.get(name)) for name in ("issued_at", "consumed_at", "expires_at")
    )
    preceding = [
        event
        for event in policies
        if event.get("tenant_id") == approved.get("tenant_id")
        and str(event["event_id"]) < str(attempt["event_id"])
    ]
    latest = max(preceding, key=lambda event: str(event["event_id"]), default={})
    if approval.get("authorization_method") == "authenticated_conversation":
        host = receipt.get("host_decision") or {}
        source = (
            by_id.get(str(approval.get("conversation_source_event_id")), {}).get(
                "payload"
            )
            or {}
        )
        try:
            host_timely = (
                isinstance(host["issued_at_ms"], int)
                and isinstance(host["expires_at_ms"], int)
                and attempted is not None
                and host["issued_at_ms"]
                <= attempted.timestamp() * 1000
                < host["expires_at_ms"]
                and 0 < host["expires_at_ms"] - host["issued_at_ms"] <= 120_000
            )
        except (KeyError, TypeError):
            host_timely = False
        return bool(
            _conversation_valid(approved, by_id)
            and _private(attempt, approved)
            and _private(openclaw, approved)
            and payload.get("contract") == RESPONSE_CONTRACT
            and payload.get("member_id")
            == approval.get("member_id")
            == receipt.get("member_id")
            and payload.get("content_fingerprint")
            == approval.get("content_fingerprint")
            == receipt.get("content_fingerprint")
            and payload.get("policy_event_id")
            == approval.get("policy_event_id")
            == latest.get("event_id")
            and payload.get("automatic_retry_allowed") is False
            and attempt.get("causation_id") == openclaw_id
            and openclaw.get("type") == "cos.conversation.reply_authorized"
            and openclaw.get("causation_id") == approved.get("event_id")
            and receipt.get("approval_event_id") == approved.get("event_id")
            and receipt.get("source_event_id")
            == approval.get("conversation_source_event_id")
            == host.get("source_event_id")
            and host.get("contract") == "authenticated-conversation-reply-v1"
            and host.get("channel") == "gmail"
            and host.get("rule_id") == "same_conversation_reply_v1"
            and host.get("body_sha256")
            == hashlib.sha256(approval.get("body", "").encode()).hexdigest()
            and all(
                host.get(key) == source.get(key)
                for key in (
                    "account_id",
                    "actor_id",
                    "session_key",
                    "agent_id",
                    "message_id",
                    "thread_id",
                )
            )
            and receipt.get("automatic_retry_allowed") is False
            and host_timely
            and attempted is not None
            and start is not None
            and end is not None
            and start <= attempted < end
            and str(approved["event_id"])
            < str(openclaw["event_id"])
            < str(attempt["event_id"])
        )
    if approval.get("authorization_method") in {
        "standing_self_reply",
        "requested_self_reminder",
    }:
        return bool(
            _standing_valid(approved, by_id, before=str(attempt["event_id"]))
            and _private(attempt, approved)
            and _private(openclaw, approved)
            and payload.get("contract") == RESPONSE_CONTRACT
            and payload.get("member_id") == approval.get("member_id")
            and payload.get("content_fingerprint")
            == approval.get("content_fingerprint")
            and payload.get("policy_event_id")
            == approval.get("policy_event_id")
            == latest.get("event_id")
            and payload.get("automatic_retry_allowed") is False
            and attempt.get("causation_id") == openclaw_id
            and openclaw.get("type")
            == (
                "cos.reply.reminder_authorized"
                if approval.get("authorization_method") == "requested_self_reminder"
                else "cos.reply.openclaw_authorized"
            )
            and openclaw.get("causation_id") == approved.get("event_id")
            and receipt.get("approval_event_id") == approved.get("event_id")
            and all(
                receipt.get(key) == approval.get(key)
                for key in (
                    "member_id",
                    "content_fingerprint",
                    "standing_authorization_event_id",
                    "source_message_event_id",
                    "self_reply_binding",
                    "authorization_method",
                    "reminder_event_id",
                    "reminder_binding_event_id",
                )
            )
            and receipt.get("automatic_retry_allowed") is False
            and _time(receipt.get("authorized_at")) == start
            and attempted is not None
            and start is not None
            and end is not None
            and start <= attempted < end
            and str(approved["event_id"])
            < str(openclaw["event_id"])
            < str(attempt["event_id"])
        )
    from adminme.requested_email_assurance import requested_email_host_valid

    fingerprint = receipt.get("request_fingerprint")
    return bool(
        _private(attempt, approved)
        and payload.get("contract") == RESPONSE_CONTRACT
        and payload.get("member_id") == approval.get("member_id")
        and payload.get("content_fingerprint") == approval.get("content_fingerprint")
        and payload.get("policy_event_id") == approval.get("policy_event_id")
        and latest.get("event_id") == approval.get("policy_event_id")
        and payload.get("automatic_retry_allowed") is False
        and attempt.get("causation_id") == openclaw_id
        and openclaw.get("type") == "openclaw.approval.received"
        and _private(openclaw, approved)
        and openclaw.get("causation_id") == approved.get("event_id")
        and receipt.get("member_id") == approval.get("member_id")
        and receipt.get("tool_name") == "administrateme_cos_response"
        and receipt.get("action") == "cos.response.send"
        and receipt.get("single_use") is True
        and (
            requested_email_host_valid(openclaw, approved, by_id)
            if approval.get("authorization_method") == "requested_email"
            else receipt.get("approval_method") == "allow_once"
        )
        and isinstance(fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        and receipt.get("resource_fingerprint") == fingerprint
        and openclaw.get("raw_ref") == f"request-sha256:{fingerprint}"
        and all(
            value is not None
            for value in (attempted, start, end, issued, consumed, expires)
        )
        and start <= attempted < end  # type: ignore[operator]
        # The shared OpenClaw receipt records consumed_at to whole seconds;
        # its issuance and the exact response timestamps retain milliseconds.
        and issued - timedelta(seconds=1) <= consumed <= attempted  # type: ignore[operator]
        and issued <= attempted < expires  # type: ignore[operator]
        and str(approved["event_id"])
        < str(openclaw["event_id"])
        < str(attempt["event_id"])
    )


def _terminal_valid(terminal: dict[str, Any], approved: dict[str, Any]) -> bool:
    payload = terminal.get("payload") or {}
    approval = approved.get("payload") or {}
    status = payload.get("status")
    effect = payload.get("provider_effect_count")
    return bool(
        _private(terminal, approved)
        and payload.get("contract") == RESPONSE_CONTRACT
        and payload.get("member_id") == approval.get("member_id")
        and payload.get("content_fingerprint") == approval.get("content_fingerprint")
        and terminal.get("causation_id") == approved.get("event_id")
        and payload.get("automatic_retry_allowed") is False
        and (
            (status == "unknown" and effect is None)
            or (status == "sent" and type(effect) is int and effect == 1)
            or (
                status in {"denied", "suppressed"}
                and type(effect) is int
                and effect == 0
            )
        )
        and _time(payload.get("completed_at")) is not None
        and str(approved["event_id"]) < str(terminal["event_id"])
    )


def _outbound_valid(
    event: dict[str, Any], attempt: dict[str, Any], approved: dict[str, Any]
) -> bool:
    payload = event.get("payload") or {}
    approval = approved.get("payload") or {}
    sent = event.get("type") == "external.sent"
    expected_provider = "gmail" if approval.get("channel") == "email" else "imessage"
    return bool(
        _private(event, approved)
        and payload.get("initiating_command_id") == approved.get("event_id")
        and payload.get("domain_approval_event_id") == approved.get("event_id")
        and payload.get("initiating_event_id") == attempt.get("event_id")
        and event.get("causation_id") == attempt.get("event_id")
        and payload.get("openclaw_approval_event_id")
        == attempt["payload"].get("openclaw_approval_event_id")
        and payload.get("content_fingerprint") == approval.get("content_fingerprint")
        and payload.get("provider_account_id") == approval.get("provider_account_id")
        and payload.get("operation") == "cos.response.send"
        and payload.get("provider") == expected_provider
        and payload.get("target_channel") == expected_provider
        and payload.get("target_identifier") == approval.get("recipient")
        and payload.get("provider_called") is sent
        and str(attempt["event_id"]) < str(event["event_id"])
    )


def cos_provider_effect_counts(
    events: list[dict[str, Any]],
    *,
    include: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, int]:
    """Count requests/results, retaining full context for bounded windows.

    A draft approval is not a send request. A refused execution before contact
    intent is a zero-effect request with one denial receipt. Every attempted
    dispatch needs a terminal receipt and independent consumed approval. Real
    outbound facts are counted even when a request or terminal is missing.
    """
    selected = include or (lambda _event: True)
    counts: defaultdict[str, int] = defaultdict(int)
    by_id = {str(event["event_id"]): event for event in events}
    policies = [event for event in events if event["type"] == POLICY_EVENT_TYPE]
    attempts: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    terminals: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    outbound: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    attempt_ids = {event["event_id"] for event in events if event["type"] == ATTEMPTED}
    for event in events:
        if not selected(event):
            continue
        payload = event.get("payload") or {}
        if event["type"] in {ATTEMPTED, RESULT}:
            approval_id = str(payload.get("approval_event_id") or "")
            (attempts if event["type"] == ATTEMPTED else terminals)[approval_id].append(
                event
            )
        elif event["type"] in OUTBOUND_TYPES and (
            payload.get("operation") == "cos.response.send"
            or payload.get("action") == "cos.response.send"
            or payload.get("attempted_action") == "cos.response.send"
            or payload.get("initiating_event_id") in attempt_ids
            or by_id.get(str(payload.get("domain_approval_event_id")), {}).get("type")
            == APPROVED
        ):
            outbound[str(payload.get("domain_approval_event_id") or "")].append(event)

    for approval_id in attempts.keys() | terminals.keys() | outbound.keys():
        requests, results, effects = (
            attempts[approval_id],
            terminals[approval_id],
            outbound[approval_id],
        )
        approved = by_id.get(approval_id, {})
        valid_approval = _approval_valid(approved, by_id)
        denied_without_attempt = (
            not requests
            and len(results) == 1
            and results[0]["payload"].get("status") == "denied"
            and valid_approval
            and _terminal_valid(results[0], approved)
        )
        counts["write_requests"] += len(requests) + int(denied_without_attempt)
        counts["terminal_results"] += len(results)
        counts["duplicate_request"] += max(0, len(requests) - 1)
        counts["duplicate_terminal"] += max(0, len(results) - 1) + max(
            0, len(effects) - 1
        )
        if requests and not results:
            counts["missing_terminal"] += 1
        if results and not requests and not denied_without_attempt:
            counts["orphan_terminal"] += len(results)
        for attempt in requests:
            if not approval_id or not valid_approval:
                counts["malformed_request"] += 1
            if not valid_approval or not _attempt_valid(
                attempt, approved, by_id, policies
            ):
                counts["approval_correlation_missing"] += 1
        for result in results:
            payload = result.get("payload") or {}
            status, effect = payload.get("status"), payload.get("provider_effect_count")
            if type(effect) is int and effect == 1:
                counts["provider_called_true"] += 1
                counts["suppressed_false_or_unknown"] += 1
            elif (
                type(effect) is int
                and effect == 0
                and status in {"denied", "suppressed"}
            ):
                counts["provider_called_false"] += 1
                counts["suppressed_true"] += int(status == "suppressed")
            else:
                counts["provider_called_unknown"] += 1
                counts["suppressed_false_or_unknown"] += 1
            valid_terminal = valid_approval and _terminal_valid(result, approved)
            if status in {"sent", "suppressed"}:
                expected_type = (
                    "external.sent" if status == "sent" else "observation.suppressed"
                )
                valid_terminal = bool(
                    valid_terminal
                    and len(requests) == 1
                    and len(effects) == 1
                    and effects[0]["type"] == expected_type
                    and _outbound_valid(effects[0], requests[0], approved)
                    and str(effects[0]["event_id"]) < str(result["event_id"])
                )
            elif status == "denied" and (requests or effects):
                valid_terminal = bool(
                    valid_terminal
                    and approved["payload"].get("channel") == "imessage"
                    and len(requests) == 1
                    and not effects
                    and payload.get("reason") == "native_dispatch_not_claimed"
                    and _attempt_valid(requests[0], approved, by_id, policies)
                )
            if not valid_terminal:
                counts["approval_correlation_missing"] += 1
        for index, effect in enumerate(effects):
            sent = effect["type"] == "external.sent"
            matching_result = any(
                result["payload"].get("status") == ("sent" if sent else "suppressed")
                and type(result["payload"].get("provider_effect_count")) is int
                and result["payload"]["provider_effect_count"] == int(sent)
                for result in results
            )
            if index > 0 or not matching_result:
                counts["provider_called_true" if sent else "provider_called_false"] += 1
                counts[
                    "suppressed_false_or_unknown" if sent else "suppressed_true"
                ] += 1
            if not results:
                counts["orphan_terminal"] += 1
            if not (
                valid_approval
                and len(requests) == 1
                and _outbound_valid(effect, requests[0], approved)
            ):
                counts["approval_correlation_missing"] += 1
    return dict(counts)
