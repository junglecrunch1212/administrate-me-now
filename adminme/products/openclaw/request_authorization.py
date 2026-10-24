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

from adminme.request_actions_control import request_actions_allowed
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.lib.session import Session
from adminme.lib.skill_runner import (
    SkillContext,
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillOutputInvalid,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
)
from adminme.products.openclaw import main as seam

RULE = "current-human-request.apple-reminder-create.v1"
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


async def ground_action(
    runtime: Any,
    session: Session,
    source: dict[str, Any],
    *,
    action_id: str,
    request_fingerprint: str,
    action: dict[str, Any],
) -> dict[str, Any]:
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
            p["request_fingerprint"] != request_fingerprint
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
        OpenClawResponseMalformed,
        OpenClawTimeout,
        OpenClawUnreachable,
        SkillOutputInvalid,
        SkillScopeInsufficient,
        SkillSensitivityRefused,
    ) as exc:
        raise HTTPException(
            409,
            {
                "code": "request_authorization_unavailable",
                "reason": "The exact request could not be verified; no action was confirmed or executed.",
            },
        ) from exc
    if (
        result.output.get("match") != "matched"
        or not evidence_matches(text, result.output.get("evidence"))
        or not result.recorded_event_id
    ):
        raise HTTPException(
            409,
            {
                "code": "request_clarification_required",
                "reason": "The proposed action does not have clear grounded request evidence.",
            },
        )
    now = datetime.now(timezone.utc)
    payload = {
        "member_id": session.auth_member_id,
        "source_event_id": source["event_id"],
        "action_id": action_id,
        "request_fingerprint": request_fingerprint,
        "action_fingerprint": fingerprint(action),
        "rule_id": RULE,
        "source_fingerprint": fingerprint(text),
        "skill_event_id": result.recorded_event_id,
        "action": action,
        "evidence": result.output["evidence"],
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
    # _append's normal idempotency namespace keeps this event append-only.
    return await runtime.event_log.get_by_idempotency(
        session.tenant_id,
        f"openclaw:{session.auth_member_id}:{GROUNDING_TYPE}:{domain['payload']['action_id']}",
    )


def ticket(
    runtime: Any, session: Session, domain: dict[str, Any], grounding: dict[str, Any]
) -> dict[str, Any]:
    p = grounding["payload"]
    body = {
        "contract": CONTRACT,
        "member_id": session.auth_member_id,
        "domain_approval_event_id": domain["event_id"],
        "grounding_event_id": grounding["event_id"],
        "source_event_id": p["source_event_id"],
        "action_fingerprint": p["action_fingerprint"],
        "rule_id": RULE,
        "issued_at": p["issued_at"],
        "expires_at": p["expires_at"],
        "evidence": p["evidence"],
        "action": p["action"],
    }
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
    _, source = await current_private_instruction(
        request,
        provenance,
        p["source_event_id"],
        required_permission="prepare_external_actions",
    )
    now = datetime.now(timezone.utc)
    if (
        grounding.get("type") != GROUNDING_TYPE
        or grounding.get("schema_version") != 1
        or grounding.get("tenant_id") != session.tenant_id
        or grounding.get("owner_scope") != grounding.get("visibility_scope")
        or grounding.get("owner_scope") != f"private:{session.auth_member_id}"
        or grounding.get("actor_identity") != COS_SERVICE_PRINCIPAL_ID
        or p["member_id"] != session.auth_member_id
        or p["rule_id"] != RULE
        or not datetime.fromisoformat(p["issued_at"])
        <= now
        < datetime.fromisoformat(p["expires_at"])
        or domain.get("source_adapter") != "openclaw-apple-reminder"
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
        "rule_id": RULE,
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
