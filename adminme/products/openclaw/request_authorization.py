"""Grounded current human requests, independently checked by both policy engines.

This is evidence for a bounded action, never an account grant or queue release.
A legacy confirmation has no grounding receipt and cannot gain one on replay.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from typing import Any

from fastapi import HTTPException, Request

from adminme.events.log import EventLogError
from adminme.request_actions_control import request_actions_allowed
from adminme.reminder_choices import CHOICE_RULE
from adminme.reminder_modes import MODE_RULE
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.lib.session import Session
from adminme.lib.skill_runner import (
    SkillContext,
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
)
from adminme.products.openclaw import main as seam

RULE = "current-human-request.apple-reminder-create.v1"
COS_RULE = "current-human-request.cos-reminder-create.v1"
EMAIL_RULE = "current-human-request.cos-email-to-requester.v1"
REQUEST_RULES = frozenset({RULE, COS_RULE, EMAIL_RULE})
CONTRACT = "authenticated-request-v1"
GROUNDING_TYPE = "cos.action.request_grounded"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def evidence_matches(text: str, evidence: Any) -> bool:
    return bool(
        isinstance(evidence, list)
        and 1 <= len(evidence) <= 8
        and all(
            isinstance(span, dict)
            and set(span) == {"start", "end", "text"}
            and type(span["start"]) is int
            and type(span["end"]) is int
            and 0 <= span["start"] < span["end"] <= len(text)
            and text[span["start"] : span["end"]] == span["text"]
            for span in evidence
        )
    )


def canonical_evidence(text: str, quotes: Any) -> list[dict[str, Any]] | None:
    """Resolve quote-only model output to canonical retained code-point spans.

    No fuzzy matching, invented offsets, duplicate coverage or arbitrary choice
    between repeated occurrences. Existing signed span receipts stay unchanged.
    """
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 8:
        return None
    spans: list[dict[str, Any]] = []
    for row in quotes:
        if not isinstance(row, dict) or set(row) != {"text"}:
            return None
        quote = row["text"]
        if not isinstance(quote, str) or not quote.strip():
            return None
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) != -1:
            return None
        end = start + len(quote)
        if any(start < span["end"] and span["start"] < end for span in spans):
            return None
        spans.append({"start": start, "end": end, "text": quote})
    return sorted(spans, key=lambda span: span["start"])


async def ground_action(
    runtime: Any,
    session: Session,
    source: dict[str, Any],
    *,
    action_id: str,
    request_fingerprint: str,
    action: dict[str, Any],
    rule_id: str = RULE,
) -> dict[str, Any]:
    email = rule_id == EMAIL_RULE
    if (
        rule_id not in REQUEST_RULES
        or action.get("provider") != ("gmail" if email else "apple_reminders")
        or action.get("operation") != ("send_email" if email else "create_reminder")
        or action.get("resource_owner")
        != (
            COS_SERVICE_PRINCIPAL_ID
            if rule_id in {COS_RULE, EMAIL_RULE}
            else "requester"
        )
        or (
            email
            and (
                action.get("operating_principal") != COS_SERVICE_PRINCIPAL_ID
                or action.get("recipient_member_id") != session.auth_member_id
            )
        )
    ):
        raise HTTPException(403, "Unsupported requested action or resource owner")
    if runtime.observation is None or await runtime.observation.is_active():
        raise HTTPException(409, "Observation mode suppresses requested actions")
    if not request_actions_allowed(
        runtime, issued_at=source["payload"]["submitted_at"]
    ):
        raise HTTPException(
            409, "Authenticated-request actions are revoked for recovery"
        )
    key = f"openclaw:{session.auth_member_id}:{GROUNDING_TYPE}:{action_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is not None:
        p = prior["payload"]
        if (
            p["rule_id"] != rule_id
            or p["request_fingerprint"] != request_fingerprint
            or p["action_fingerprint"] != fingerprint(action)
            or p["source_event_id"] != source["event_id"]
        ):
            raise HTTPException(409, "request identity belongs to different intent")
        if datetime.fromisoformat(p["expires_at"]) <= datetime.now(timezone.utc):
            raise HTTPException(
                409,
                "Original request authority expired; a new explicit instruction is required",
            )
        return prior
    text = source["payload"]["text"]
    try:
        # A failed invocation has no successful result. Its old source causation
        # must not let a new message spend that same action identity again.
        # This exact delimited member/action/skill prefix uses the existing
        # tenant/idempotency index, reads at most 32 receipts, and fails closed
        # on overflow. A pack update does not turn an old failure into authority.
        failed = await runtime.event_log.get_by_idempotency_prefix(
            session.tenant_id, f"skill:failed:{key}:skill:ground_requested_action:"
        )
        if failed:
            raise SkillInputInvalid("request identity already has failed grounding")
        result = await runtime.run_skill_fn(
            "ground_requested_action",
            {
                "instruction": text,
                "submitted_at": source["payload"]["submitted_at"],
                "timezone": runtime.settings.instance.timezone,
                "action": action,
            },
            SkillContext(
                session=session,
                triggering_event_id=source["event_id"],
                invocation_idempotency_key=key,
                require_clean_result=True,
                owner_scope=f"private:{session.auth_member_id}",
                visibility_scope=f"private:{session.auth_member_id}",
                sensitivity=source["sensitivity"],
            ),
            input_sensitivities={"instruction": source["sensitivity"]},
        )
    except (
        EventLogError,
        ValueError,
        OpenClawResponseMalformed,
        OpenClawTimeout,
        OpenClawUnreachable,
        SkillInputInvalid,
        SkillOutputInvalid,
        SkillScopeInsufficient,
        SkillSensitivityRefused,
    ) as exc:
        # An existing invocation cannot be rebound to new message text or input.
        # Preserve its receipt/failure and expose a closed failure, never HTTP 500.
        raise HTTPException(
            409,
            {
                "code": "request_authorization_unavailable",
                "reason": "The exact request could not be verified; no action was confirmed or executed.",
            },
        ) from exc
    if result.output.get("match") in {"not_requested", "ambiguous"}:
        raise HTTPException(
            409,
            {
                "code": "request_clarification_required",
                "reason": "The proposed action does not have clear grounded request evidence.",
            },
        )
    evidence = canonical_evidence(text, result.output.get("evidence"))
    if (
        result.output.get("match") != "matched"
        or evidence is None
        or not result.recorded_event_id
    ):
        # An extraction/protocol failure does not establish unclear human intent,
        # another account choice, or a provider permission problem.
        raise HTTPException(
            409,
            {
                "code": "request_authorization_unavailable",
                "reason": "The request evidence could not be verified; no action was confirmed or executed.",
            },
        )
    now = datetime.now(timezone.utc)
    payload = {
        "member_id": session.auth_member_id,
        "source_event_id": source["event_id"],
        "action_id": action_id,
        "request_fingerprint": request_fingerprint,
        "action_fingerprint": fingerprint(action),
        "rule_id": rule_id,
        "source_fingerprint": fingerprint(text),
        "skill_event_id": result.recorded_event_id,
        "action": action,
        "evidence": evidence,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }
    # The same exact request reuses its original semantic evaluation and expiry.
    event_id, _ = await seam._append(
        runtime,
        session,
        event_type=GROUNDING_TYPE,
        source_account_id=session.auth_member_id,
        owner_scope=f"private:{session.auth_member_id}",
        visibility_scope=f"private:{session.auth_member_id}",
        sensitivity=source["sensitivity"],
        payload=payload,
        request_id=action_id,
        causation_id=source["event_id"],
    )
    event = await runtime.event_log.get(event_id)
    assert event is not None
    return event


async def grounding_for_domain(
    runtime: Any, session: Session, domain: dict[str, Any]
) -> dict[str, Any] | None:
    # Original one-message receipts keep their namespace; continuation has its
    # own immutable schema and may never silently replace one of those receipts.
    primary = await runtime.event_log.get_by_idempotency(
        session.tenant_id,
        f"openclaw:{session.auth_member_id}:{GROUNDING_TYPE}:{domain['payload']['action_id']}",
    )
    choice = await runtime.event_log.get_by_idempotency(
        session.tenant_id,
        f"openclaw:{session.auth_member_id}:cos.reminder.choice_grounded:{domain['payload']['action_id']}",
    )
    mode = await runtime.event_log.get_by_idempotency(
        session.tenant_id,
        f"openclaw:{session.auth_member_id}:cos.reminder.mode_grounded:{domain['payload']['action_id']}",
    )
    matches = [event for event in (primary, choice, mode) if event is not None]
    if len(matches) > 1:
        raise HTTPException(403, "Action has contradictory request authorities")
    return matches[0] if matches else None


def ticket(
    runtime: Any, session: Session, domain: dict[str, Any], grounding: dict[str, Any]
) -> dict[str, Any]:
    p = grounding["payload"]
    if p["rule_id"] not in REQUEST_RULES | {CHOICE_RULE, MODE_RULE}:
        raise HTTPException(403, "Unsupported request authorization rule")
    body = {
        "contract": CONTRACT,
        "member_id": session.auth_member_id,
        "domain_approval_event_id": domain["event_id"],
        "grounding_event_id": grounding["event_id"],
        "source_event_id": p["source_event_id"],
        "action_fingerprint": p["action_fingerprint"],
        "rule_id": p["rule_id"],
        "issued_at": p["issued_at"],
        "expires_at": p["expires_at"],
        "evidence": p["evidence"],
        "action": p["action"],
    }
    if p["rule_id"] in {CHOICE_RULE, MODE_RULE}:
        if (
            grounding.get("type")
            != (
                "cos.reminder.mode_grounded"
                if p["rule_id"] == MODE_RULE
                else "cos.reminder.choice_grounded"
            )
            or grounding.get("schema_version") != 1
        ):
            raise HTTPException(403, "Reminder choice grounding type is mismatched")
        body["continuation"] = p["continuation"]
    secret = runtime.settings.openclaw_shared_secret
    if not secret:
        raise HTTPException(503, "request authorization credential is unavailable")
    return {
        **body,
        "signature": hmac.new(
            secret.encode(), (CONTRACT + "|" + canonical(body)).encode(), hashlib.sha256
        ).hexdigest(),
    }


async def validate_request_authorization(
    request: Request,
    session: Session,
    provenance: seam.Provenance,
    domain: dict[str, Any],
    grounding_id: str,
) -> dict[str, str]:
    from adminme.products.openclaw.service_work import current_private_instruction

    runtime = seam._runtime(request)
    grounding = await grounding_for_domain(runtime, session, domain)
    if grounding is None or grounding["event_id"] != grounding_id:
        raise HTTPException(403, "original action has no current request authorization")
    p = grounding["payload"]
    if grounding.get("type") in {
        "cos.reminder.choice_grounded",
        "cos.reminder.mode_grounded",
    }:
        from adminme.products.openclaw.reminder_choice_authorization import (
            validate_choice_authorization,
        )

        return await validate_choice_authorization(
            request, session, provenance, domain, grounding
        )
    _, source = await current_private_instruction(
        request,
        provenance,
        p["source_event_id"],
        required_permission="prepare_external_actions",
    )
    now = datetime.now(timezone.utc)
    rule_id = p.get("rule_id")
    own_reminder = rule_id == COS_RULE
    if own_reminder:
        from adminme.cos_reminders_commands import CoSReminderIntent, SOURCE_ADAPTER

        try:
            intent = CoSReminderIntent.model_validate(
                domain["payload"]["command_payload"]
            )
            intent.require_current(runtime.settings.adapter_config)
            if (
                intent.action() != p["action"]
                or intent.source_event_id != source["event_id"]
                or intent.authorization_expires_at
                != datetime.fromisoformat(p["expires_at"])
                or domain["payload"].get("provider_account_id")
                != intent.selection.provider_account_id
                or domain["payload"].get("target_external_id") is not None
                or domain["payload"].get("expected_provider_revision") is not None
            ):
                raise ValueError("CoS native action differs from grounded intent")
        except (KeyError, ValueError, PermissionError) as exc:
            raise HTTPException(
                403, "CoS reminder authority changed or mismatched"
            ) from exc
        expected_adapter = SOURCE_ADAPTER
    else:
        expected_adapter = "openclaw-apple-reminder"
    if (
        grounding.get("type") != GROUNDING_TYPE
        or grounding.get("schema_version") != 1
        or grounding.get("tenant_id") != session.tenant_id
        or grounding.get("owner_scope") != grounding.get("visibility_scope")
        or grounding.get("owner_scope") != f"private:{session.auth_member_id}"
        or grounding.get("actor_identity") != COS_SERVICE_PRINCIPAL_ID
        or p["member_id"] != session.auth_member_id
        or rule_id not in {RULE, COS_RULE}
        or p["action"].get("resource_owner")
        != (COS_SERVICE_PRINCIPAL_ID if own_reminder else "requester")
        or not datetime.fromisoformat(p["issued_at"])
        <= now
        < datetime.fromisoformat(p["expires_at"])
        or domain.get("source_adapter") != expected_adapter
        or domain.get("actor_identity") != session.auth_member_id
        or domain.get("source_account_id") != session.auth_member_id
        or domain.get("visibility_scope") != f"private:{session.auth_member_id}"
        or domain.get("causation_id") != source["event_id"]
        or domain["payload"].get("action_id") != p["action_id"]
        or domain["payload"].get("request_fingerprint") != p["request_fingerprint"]
        or domain["payload"].get("operation") != "create_reminder"
        or domain["payload"].get("provider") != "apple_reminders"
        or p["action_fingerprint"] != fingerprint(p["action"])
        or p["source_fingerprint"] != fingerprint(source["payload"]["text"])
        or not evidence_matches(source["payload"]["text"], p["evidence"])
    ):
        raise HTTPException(403, "request authorization is stale or mismatched")
    if not request_actions_allowed(runtime, issued_at=p["issued_at"]):
        raise HTTPException(409, "This request authority was revoked for recovery")
    if await runtime.observation.is_active():
        raise HTTPException(409, "Observation mode suppresses requested actions")
    return {
        "request_authorization_event_id": grounding_id,
        "source_event_id": source["event_id"],
        "domain_approval_event_id": domain["event_id"],
        "action_fingerprint": p["action_fingerprint"],
        "rule_id": str(rule_id),
    }


async def require_current_bridge_request(runtime: Any, command: dict[str, Any]) -> None:
    """New policy receipts expire/revoke before native claim; legacy stays unchanged."""
    approval_id = command.get("payload", {}).get("openclaw_approval_event_id")
    approval = await runtime.event_log.get(str(approval_id)) if approval_id else None
    if (
        approval is None
        or approval.get("payload", {}).get("approval_method") != "authenticated_request"
    ):
        return
    from adminme.projections.parties.queries import (
        resolve_service_delegation,
        ServiceDelegationResolutionError,
    )
    from adminme.service_identities import (
        COS_AUTOMATION_OPERATOR_ID,
    )

    p = approval["payload"]
    grounded = await runtime.event_log.get(p["request_authorization_event_id"])
    if (
        grounded is None
        or not request_actions_allowed(
            runtime, issued_at=grounded["payload"]["issued_at"]
        )
        or datetime.fromisoformat(grounded["payload"]["expires_at"])
        <= datetime.now(timezone.utc)
    ):
        raise HTTPException(409, "Requested action authority expired or was revoked")
    try:
        context = resolve_service_delegation(
            runtime.projections.connection("parties"),
            tenant_id=command["tenant_id"],
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
            context_scope="member_private",
            context_member_id=p["member_id"],
        )
        if "prepare_external_actions" not in context.permissions:
            raise ValueError("request preparation authority was revoked")
    except (ServiceDelegationResolutionError, ValueError) as exc:
        raise HTTPException(
            409, "Member action authority is no longer current"
        ) from exc

    if grounded.get("type") in {
        "cos.reminder.choice_grounded",
        "cos.reminder.mode_grounded",
    }:
        from adminme.products.openclaw.reminder_choice_authorization import (
            require_current_choice_sources,
        )

        await require_current_choice_sources(runtime, grounded, context)
