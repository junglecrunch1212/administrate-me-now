"""Independent domain policy for one original request plus one fresh list answer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from pydantic import ValidationError

from adminme.apple_service_permissions import permission_options, service_permission
from adminme.events.envelope import Sensitivity
from adminme.events.schemas.reminder_choices import (
    ReminderChoiceGroundedV1,
    ReminderChoiceOfferedV1,
    ReminderListChoice,
)
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
from adminme.products.openclaw.reminder_choice_work import (
    draft_fields,
    receipt_key,
    source_and_work_authority,
    validate_original,
    instruction_identity,
    selected_task_answer,
    selection_snapshot,
)
from adminme.reminder_choices import (
    CHOICE_RULE,
    digest,
    execution_expiry,
    selected_choice,
    selected_task,
)
from adminme.request_actions_control import request_actions_allowed
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

RULE = CHOICE_RULE
GROUNDING_TYPE = "cos.reminder.choice_grounded"


def exact_action(p: ReminderChoiceOfferedV1, list_id: str) -> dict[str, Any]:
    return {
        "provider": "apple_reminders",
        "operation": "create_reminder",
        "resource_owner": "requester",
        **draft_fields(title=p.title, due_at=p.due_at, notes=p.notes),
        "list_id": list_id,
        "selected_by_policy": False,
    }


async def current_choice(
    runtime: Any,
    member: Session,
    offered: dict[str, Any],
    answer: dict[str, Any],
    action: dict[str, Any],
) -> None:
    p = ReminderChoiceOfferedV1.model_validate(offered["payload"])
    await validate_original(runtime, member, offered)
    retained_answer, _, _ = await source_and_work_authority(
        runtime, member, answer["event_id"]
    )
    if answer != retained_answer:
        raise HTTPException(
            403, "Fresh answer must come from its retained authenticated source"
        )
    if runtime.observation is None or await runtime.observation.is_active():
        raise HTTPException(
            409, "Observation mode suppresses reminder choice authorization"
        )
    if not request_actions_allowed(
        runtime, issued_at=answer["payload"]["submitted_at"]
    ):
        raise HTTPException(
            409, "Authenticated-request actions are revoked for recovery"
        )
    permission = service_permission(runtime, member.auth_member_id, "apple_reminders")
    if permission is None or "create_reminder" not in permission["operations"]:
        raise HTTPException(
            409, "Current Apple Reminders writing permission is unavailable"
        )
    options = permission_options(runtime, member.auth_member_id, "apple_reminders")
    _, authored_answer = await selected_task_answer(runtime, member, offered, answer)
    selected = selected_choice(
        p,
        authored_answer,
        provider_account_id=permission["provider_account_id"],
        current_choices=[
            ReminderListChoice(list_id=row["container_id"], label=row["label"])
            for row in options["scopes"]
            if row["container_id"] in permission["allowed_container_ids"]
        ],
        now=datetime.now(timezone.utc),
    )
    if action != exact_action(p, selected.list_id):
        raise HTTPException(
            403, "A list answer cannot change the original reminder draft"
        )


async def ground_reminder_choice(
    runtime: Any,
    member: Session,
    original: dict[str, Any],
    answer: dict[str, Any],
    *,
    offered: dict[str, Any],
    action: dict[str, Any],
    request_fingerprint: str,
) -> dict[str, Any]:
    from adminme.products.openclaw.request_authorization import canonical_evidence

    p = ReminderChoiceOfferedV1.model_validate(offered["payload"])
    retained_original = await validate_original(runtime, member, offered)
    if original != retained_original:
        raise HTTPException(
            403, "Original reminder source must come from retained history"
        )
    await current_choice(runtime, member, offered, answer, action)
    if (
        original["event_id"] != p.original_source_event_id
        or answer["event_id"] == original["event_id"]
        or request_fingerprint
        != digest({"offered_event_id": offered["event_id"], "action": action})
    ):
        raise HTTPException(
            403, "Continuation sources or immutable draft are mismatched"
        )
    key = receipt_key(member.auth_member_id, GROUNDING_TYPE, p.action_id)
    prior = await runtime.event_log.get_by_idempotency(member.tenant_id, key)
    if prior is not None:
        if (
            prior["payload"]["source_event_id"] != answer["event_id"]
            or prior["payload"]["request_fingerprint"] != request_fingerprint
            or prior["payload"]["action_fingerprint"] != digest(action)
            or not await historical_choice_matches(runtime.event_log, prior)
        ):
            raise HTTPException(
                409, "This reminder already has a different answer authorization"
            )
        if datetime.fromisoformat(prior["payload"]["expires_at"]) <= datetime.now(
            timezone.utc
        ):
            raise HTTPException(
                409, "Original answer authorization expired; it cannot be renewed"
            )
        return prior
    original_text, answer_text = original["payload"]["text"], answer["payload"]["text"]
    sensitivity = cast(
        Sensitivity,
        max(
            [original["sensitivity"], answer["sensitivity"]],
            key=["normal", "sensitive", "privileged"].index,
        ),
    )
    # The durable action ID is fixed. Each fresh answer has its own model
    # invocation, so an earlier unsuccessful invocation can never be re-bound.
    invocation = "reminder-choice-answer:" + digest(
        [offered["event_id"], answer["event_id"], digest(action)]
    )
    try:
        result = await runtime.run_skill_fn(
            "ground_reminder_choice",
            {
                "original_instruction": original_text,
                "original_submitted_at": original["payload"]["submitted_at"],
                "answer": answer_text,
                "answer_submitted_at": answer["payload"]["submitted_at"],
                "timezone": runtime.settings.instance.timezone,
                "action": action,
                "offered_choices": offered["payload"]["choices"],
            },
            SkillContext(
                session=member,
                triggering_event_id=answer["event_id"],
                invocation_idempotency_key=invocation,
                require_clean_result=True,
                owner_scope=f"private:{member.auth_member_id}",
                visibility_scope=f"private:{member.auth_member_id}",
                sensitivity=sensitivity,
            ),
            input_sensitivities={
                "original_instruction": original["sensitivity"],
                "answer": answer["sensitivity"],
            },
        )
    except (
        OpenClawResponseMalformed,
        OpenClawTimeout,
        OpenClawUnreachable,
        SkillInputInvalid,
        SkillOutputInvalid,
        SkillScopeInsufficient,
        SkillSensitivityRefused,
    ) as exc:
        raise HTTPException(
            409,
            {
                "code": "request_authorization_unavailable",
                "reason": "The original request and answer could not be verified; no action was confirmed.",
            },
        ) from exc
    original_evidence = canonical_evidence(
        original_text, result.output.get("original_quotes")
    )
    answer_evidence = canonical_evidence(
        answer_text, result.output.get("answer_quotes")
    )
    if result.output.get("match") in {"not_requested", "ambiguous"}:
        raise HTTPException(
            409,
            {
                "code": "request_clarification_required",
                "reason": "The original request and fresh answer do not clearly request this unchanged reminder.",
            },
        )
    if (
        result.output.get("match") != "matched"
        or not original_evidence
        or not answer_evidence
        or not result.recorded_event_id
    ):
        raise HTTPException(
            409,
            {
                "code": "request_authorization_unavailable",
                "reason": "The retained request evidence could not be verified; no action was confirmed.",
            },
        )
    await current_choice(runtime, member, offered, answer, action)
    snapshot, _ = await selected_task_answer(runtime, member, offered, answer)
    now = datetime.now(timezone.utc)
    payload = ReminderChoiceGroundedV1.model_validate(
        {
            "member_id": member.auth_member_id,
            "source_event_id": answer["event_id"],
            "action_id": p.action_id,
            "request_fingerprint": request_fingerprint,
            "action_fingerprint": digest(action),
            "rule_id": RULE,
            "source_fingerprint": digest(answer_text),
            "skill_event_id": result.recorded_event_id,
            "action": action,
            "evidence": answer_evidence,
            "issued_at": now,
            "expires_at": execution_expiry(p, issued_at=now),
            "continuation": {
                "offered_event_id": offered["event_id"],
                "offered": offered["payload"],
                "original_instruction": original_text,
                "original_evidence": original_evidence,
                "selection": snapshot.model_dump(mode="json"),
            },
        }
    )
    event_id, _ = await seam._append(
        runtime,
        member,
        event_type=GROUNDING_TYPE,
        source_account_id=member.auth_member_id,
        owner_scope=f"private:{member.auth_member_id}",
        visibility_scope=f"private:{member.auth_member_id}",
        sensitivity=sensitivity,
        payload=payload.model_dump(mode="json"),
        request_id=p.action_id,
        causation_id=answer["event_id"],
    )
    event = await runtime.event_log.get(event_id)
    assert event is not None
    return event


async def historical_choice_matches(event_log: Any, grounding: dict[str, Any]) -> bool:
    """Immutable attribution survives later policy revocation and pending expiry."""
    from adminme.products.openclaw.request_authorization import evidence_matches

    try:
        p = ReminderChoiceGroundedV1.model_validate(grounding["payload"])
        c = p.continuation
        offer = await event_log.get(c.offered_event_id)
        original = await event_log.get(c.offered.original_source_event_id)
        answer = await event_log.get(p.source_event_id)
        if offer is None or original is None or answer is None:
            return False
        tenant = grounding["tenant_id"]
        snapshot = await selection_snapshot(event_log, tenant, p.member_id, answer)
        target = selected_task(snapshot, answer["payload"]["text"])
        if (
            snapshot != c.selection
            or target is None
            or target[0].offered_event_id != c.offered_event_id
        ):
            return False
        selection = selected_choice(
            c.offered,
            target[1],
            provider_account_id=c.offered.provider_account_id,
            current_choices=c.offered.choices,
            now=p.issued_at,
        )
        return bool(
            grounding["type"] == GROUNDING_TYPE
            and grounding["schema_version"] == 1
            and grounding["actor_identity"] == COS_SERVICE_PRINCIPAL_ID
            and grounding["source_adapter"] == "openclaw-plugin"
            and grounding["source_account_id"] == p.member_id
            and grounding["owner_scope"]
            == grounding["visibility_scope"]
            == f"private:{p.member_id}"
            and grounding["causation_id"] == p.source_event_id
            and offer["tenant_id"] == tenant
            and offer["type"] == "cos.reminder.choice_offered"
            and offer["schema_version"] == 1
            and offer["actor_identity"] == COS_SERVICE_PRINCIPAL_ID
            and offer["source_adapter"] == "openclaw-plugin"
            and offer["source_account_id"] == p.member_id
            and offer["owner_scope"]
            == offer["visibility_scope"]
            == f"private:{p.member_id}"
            and offer["payload"] == c.offered.model_dump(mode="json")
            and offer["causation_id"] == original["event_id"]
            and original["event_id"]
            < offer["event_id"]
            < answer["event_id"]
            < grounding["event_id"]
            and instruction_identity(original, tenant, p.member_id)
            and instruction_identity(answer, tenant, p.member_id)
            and original["payload"]["text"] == c.original_instruction
            and datetime.fromisoformat(original["payload"]["submitted_at"])
            == c.offered.original_submitted_at
            and digest(c.original_instruction) == c.offered.original_source_fingerprint
            and digest(
                draft_fields(
                    title=c.offered.title,
                    due_at=c.offered.due_at,
                    notes=c.offered.notes,
                )
            )
            == c.offered.draft_fingerprint
            and p.action == exact_action(c.offered, selection.list_id)
            and p.action_fingerprint == digest(p.action)
            and p.request_fingerprint
            == digest({"offered_event_id": c.offered_event_id, "action": p.action})
            and p.source_fingerprint == digest(answer["payload"]["text"])
            and evidence_matches(c.original_instruction, c.original_evidence)
            and evidence_matches(answer["payload"]["text"], p.evidence)
        )
    except (KeyError, TypeError, ValueError, ValidationError, HTTPException):
        return False


async def historical_choice_domain_matches(
    event_log: Any, domain: dict[str, Any], grounding: dict[str, Any]
) -> bool:
    """Static exact command lineage, without reinterpreting later live policy."""
    from adminme.products.openclaw.apple_reminders import due_components

    if not await historical_choice_matches(event_log, grounding):
        return False
    try:
        p = ReminderChoiceGroundedV1.model_validate(grounding["payload"])
        pending = p.continuation.offered
        decision = domain["payload"]
        command = decision["command_payload"]
        return bool(
            domain["tenant_id"] == grounding["tenant_id"]
            and domain["type"] == "member.provider_action.confirmed"
            and domain["schema_version"] == 1
            and domain["owner_scope"]
            == domain["visibility_scope"]
            == f"private:{p.member_id}"
            and domain["actor_identity"] == domain["source_account_id"] == p.member_id
            and domain["source_adapter"] == "openclaw-apple-reminder"
            and domain["causation_id"] == p.source_event_id
            and grounding["event_id"] < domain["event_id"]
            and decision["member_id"] == p.member_id
            and decision["action_id"] == p.action_id
            and decision["request_fingerprint"] == p.request_fingerprint
            and decision["provider"] == "apple_reminders"
            and decision["operation"] == "create_reminder"
            and decision["provider_account_id"] == pending.provider_account_id
            and decision.get("target_external_id") == p.action["list_id"]
            and decision.get("expected_provider_revision") is None
            and command["title"] == pending.title
            and command.get("notes") == pending.notes
            and command["due"] == due_components(pending.due_at)
            and command["list_external_id"] == p.action["list_id"]
        )
    except (KeyError, TypeError, ValueError):
        return False


async def validate_choice_authorization(
    request: Any,
    member: Session,
    provenance: Any,
    domain: dict[str, Any],
    grounding: dict[str, Any],
) -> dict[str, str]:
    from adminme.products.openclaw.apple_reminders import due_components
    from adminme.products.openclaw.service_work import current_private_instruction

    runtime = seam._runtime(request)
    p = ReminderChoiceGroundedV1.model_validate(grounding["payload"])
    _, answer = await current_private_instruction(
        request,
        provenance,
        p.source_event_id,
        required_permission="prepare_external_actions",
    )
    if not await historical_choice_domain_matches(runtime.event_log, domain, grounding):
        raise HTTPException(403, "Original reminder choice authority is mismatched")
    offer = await runtime.event_log.get(p.continuation.offered_event_id)
    assert offer is not None
    await current_choice(runtime, member, offer, answer, p.action)
    now = datetime.now(timezone.utc)
    pending = p.continuation.offered
    command = domain.get("payload", {}).get("command_payload", {})
    if (
        p.member_id != member.auth_member_id
        or not p.issued_at <= now < p.expires_at
        or not request_actions_allowed(runtime, issued_at=p.issued_at.isoformat())
        or domain.get("tenant_id") != member.tenant_id
        or domain.get("owner_scope") != domain.get("visibility_scope")
        or domain.get("owner_scope") != f"private:{member.auth_member_id}"
        or domain.get("type") != "member.provider_action.confirmed"
        or domain.get("schema_version") != 1
        or domain.get("actor_identity") != member.auth_member_id
        or domain.get("source_account_id") != member.auth_member_id
        or domain.get("source_adapter") != "openclaw-apple-reminder"
        or domain.get("causation_id") != p.source_event_id
        or domain["payload"].get("action_id") != p.action_id
        or domain["payload"].get("request_fingerprint") != p.request_fingerprint
        or domain["payload"].get("provider_account_id") != pending.provider_account_id
        or domain["payload"].get("provider") != "apple_reminders"
        or domain["payload"].get("operation") != "create_reminder"
        or command.get("title") != pending.title
        or command.get("notes") != pending.notes
        or command.get("due") != due_components(pending.due_at)
        or command.get("list_external_id") != p.action["list_id"]
    ):
        raise HTTPException(403, "Reminder choice authority expired or changed")
    return {
        "request_authorization_event_id": grounding["event_id"],
        "source_event_id": p.source_event_id,
        "domain_approval_event_id": domain["event_id"],
        "action_fingerprint": p.action_fingerprint,
        "rule_id": RULE,
    }


async def require_current_choice_sources(
    runtime: Any, grounding: dict[str, Any], context: Any
) -> None:
    """Final dispatch rechecks both retained sources without inventing a member session."""
    from adminme.products.openclaw.service_context import source_authority
    from adminme.products.openclaw.service_work import _internal_session

    if "read_context" not in context.permissions or not await historical_choice_matches(
        runtime.event_log, grounding
    ):
        raise HTTPException(
            409,
            "Reminder choice history or current source permission cannot be verified",
        )
    p = ReminderChoiceGroundedV1.model_validate(grounding["payload"])
    if p.expires_at <= datetime.now(
        timezone.utc
    ) or p.continuation.offered.pending_expires_at <= datetime.now(timezone.utc):
        raise HTTPException(409, "Original reminder choice authority expired")
    internal = _internal_session(runtime, grounding["event_id"])
    for source_id in (
        p.continuation.offered.original_source_event_id,
        p.source_event_id,
    ):
        source = await runtime.event_log.get(source_id)
        if (
            source is None
            or source_authority(runtime, internal, source, [context]) is None
        ):
            raise HTTPException(
                409, "Reminder instruction source authority was revoked"
            )


async def require_choice_ticket_for_domain(
    runtime: Any,
    member: Session,
    domain: dict[str, Any],
    authorization: dict[str, str] | None,
) -> None:
    """A choice-created domain can never fall back to a later manual approval."""
    from adminme.products.openclaw.reminder_choice_work import offered_by_action

    if domain.get("source_adapter") != "openclaw-apple-reminder":
        return
    offered = await offered_by_action(runtime, member, domain["payload"]["action_id"])
    if offered is not None and (
        authorization is None or authorization.get("rule_id") != RULE
    ):
        raise HTTPException(
            403,
            "Reminder choices require their exact unexpired current-answer authorization",
        )
