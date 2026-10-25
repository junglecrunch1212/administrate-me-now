"""Retained provenance checks for current-instruction CoS email receipts.

These checks explain an already recorded effect. They cannot authorize a send;
execution still checks current grants, both policy engines and the final permit.
"""

from __future__ import annotations

from typing import Any

from adminme.cos_responses import content_fingerprint
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

RULE = "current-human-request.cos-email-to-requester.v1"


def requested_email_approval_valid(
    approved: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> bool:
    from adminme.cos_response_assurance import _private, _time
    from adminme.products.openclaw.request_authorization import (
        evidence_matches,
        fingerprint,
    )

    p = approved.get("payload") or {}
    source = by_id.get(str(p.get("request_source_event_id")), {})
    s = source.get("payload") or {}
    grounding = by_id.get(str(p.get("request_authorization_event_id")), {})
    g = grounding.get("payload") or {}
    action = p.get("request_action")
    submitted, issued, expires = (
        _time(value)
        for value in (s.get("submitted_at"), p.get("approved_at"), p.get("expires_at"))
    )
    if not isinstance(action, dict) or not isinstance(s.get("text"), str):
        return False
    source_kind = bool(
        (
            source.get("type") == "triage.intent.submitted"
            and source.get("schema_version") == 2
            and source.get("source_adapter") == "openclaw-submission"
            and s.get("submission_kind") == "member_forwarded"
            and s.get("intake_channel") in {"imessage", "apple_imessage"}
        )
        or (
            source.get("type") == "cos.conversation.instruction"
            and source.get("schema_version") == 1
            and source.get("source_adapter") == "cos-conversation"
            and (
                (
                    s.get("channel_id") == "gmail"
                    and s.get("authentication_rule")
                    == "enrolled_receiver_authenticated_sender"
                    and s.get("source_message_event_id")
                    and s.get("thread_id")
                )
                or (
                    s.get("channel_id") == "administrateme-portal"
                    and s.get("authentication_rule") == "authenticated_member_session"
                    and not s.get("thread_id")
                )
            )
        )
    )
    expected = {
        "provider": "gmail",
        "operation": "send_email",
        "resource_owner": COS_SERVICE_PRINCIPAL_ID,
        "operating_principal": COS_SERVICE_PRINCIPAL_ID,
        "recipient_member_id": p.get("member_id"),
        **{
            key: p.get(key)
            for key in (
                "service_channel_id",
                "provider_account_id",
                "recipient",
                "subject",
                "body",
                "content_fingerprint",
            )
        },
        **{
            key: action.get(key)
            for key in ("sender_selected_by_policy", "recipient_selected_by_policy")
        },
    }
    try:
        return bool(
            approved.get("schema_version") == 5
            and p.get("authorization_method") == "requested_email"
            and p.get("channel") == "email"
            and approved.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
            and _private(source, approved)
            and source_kind
            and source.get("actor_identity") == s.get("member_id") == p.get("member_id")
            and approved.get("causation_id") == source.get("event_id")
            and _private(grounding, approved)
            and grounding.get("type") == "cos.action.request_grounded"
            and grounding.get("schema_version") == 1
            and grounding.get("actor_identity") == COS_SERVICE_PRINCIPAL_ID
            and grounding.get("causation_id") == source.get("event_id")
            and g.get("member_id") == p.get("member_id")
            and g.get("source_event_id") == source.get("event_id")
            and g.get("source_fingerprint") == fingerprint(s["text"])
            and evidence_matches(s["text"], g.get("evidence"))
            and g.get("rule_id") == RULE
            and g.get("action_id") == p.get("action_id")
            and g.get("request_fingerprint") == p.get("request_fingerprint")
            and g.get("issued_at") == p.get("approved_at")
            and g.get("expires_at") == p.get("expires_at")
            and g.get("action_fingerprint") == fingerprint(action)
            and fingerprint(g.get("action")) == fingerprint(action)
            and fingerprint(expected) == fingerprint(action)
            and all(
                type(action.get(key)) is bool
                for key in ("sender_selected_by_policy", "recipient_selected_by_policy")
            )
            and p.get("content_fingerprint")
            == content_fingerprint(
                account_id=p["provider_account_id"],
                recipient=p["recipient"],
                subject=p["subject"],
                body=p["body"],
                channel="email",
            )
            and all(value is not None for value in (submitted, issued, expires))
            and 0 <= (issued - submitted).total_seconds() <= 3600  # type: ignore[operator]
            and 0 < (expires - issued).total_seconds() <= 600  # type: ignore[operator]
            and str(source.get("event_id"))
            < str(grounding.get("event_id"))
            < str(approved.get("event_id"))
        )
    except (KeyError, TypeError, ValueError):
        return False


def requested_email_host_valid(
    host: dict[str, Any],
    approved: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
) -> bool:
    """Additional machine-policy fields; ordinary host/attempt checks still apply."""
    p = approved.get("payload") or {}
    h = host.get("payload") or {}
    g = by_id.get(str(p.get("request_authorization_event_id")), {}).get("payload") or {}
    return bool(
        requested_email_approval_valid(approved, by_id)
        and host.get("schema_version") == 2
        and h.get("approval_method") == "authenticated_request"
        and str(h.get("receipt_id", "")).startswith("ocreq_")
        and h.get("rule_id") == RULE
        and h.get("domain_approval_event_id") == approved.get("event_id")
        and h.get("source_event_id") == p.get("request_source_event_id")
        and h.get("request_authorization_event_id")
        == p.get("request_authorization_event_id")
        and h.get("action_fingerprint") == g.get("action_fingerprint")
    )
