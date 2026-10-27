"""CoS-private internal work through a distinct authenticated service route.

The service owns this history. Member conversations, provider actions and
scheduler registration are separate authority boundaries, never implicit here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.events.log import IdempotencyConflict
from adminme.lib.principal import PrincipalIdentity, ServiceSession
from adminme.openclaw_topology import (
    member_agent_id,
    service_agent_id,
    service_session_valid,
)
from adminme.cos_self_replies import self_reply_session
from adminme.products.openclaw import main as seam
from adminme.products.openclaw.service_context import (
    READ_RANK,
    ContextSourceKind,
    active_contexts,
    context_source_matches,
    context_source_types,
    fact_for,
    retained_sources,
    source_facts,
    source_authority,
    validate_context_cursor,
    work_view,
)
from adminme.products.openclaw.service_mail_context import (
    MemberGmailThreadSelector,
    ResolvedMailThread,
    revalidate_member_gmail_thread,
    resolve_member_gmail_thread,
)
from adminme.projections.tasks.queries import get_task, service_work_for_principal
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    ServiceRequestContext,
    service_private_scope,
)

router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["cos-private-work"])


class ServiceOnlyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provenance: seam.Provenance


class SourceFactsRequest(ServiceOnlyRequest):
    source_event_ids: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(
        default_factory=list, max_length=20
    )

    @field_validator("source_event_ids")
    @classmethod
    def unique_sources(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("source event references must be unique")
        return value


class CreatePrivateWorkRequest(SourceFactsRequest):
    request_id: str = Field(min_length=8, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    record_kind: Literal["work", "note", "scratchpad"] = "work"
    service_work_type: Literal[
        "operational_task",
        "waiting_for",
        "follow_up",
        "clarification",
        "exception",
        "preparation",
        "held_action",
        "provider_failure",
        "reconciliation",
        "operational_calendar",
        "reminder_like",
    ] = "operational_task"
    due: AwareDatetime | None = None
    follow_up_at: AwareDatetime | None = None


class TransitionPrivateWorkRequest(ServiceOnlyRequest):
    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["start", "resume", "hold", "follow_up", "complete", "cancel"]
    reason_code: Literal[
        "clarification",
        "provider_failure",
        "reconciliation",
        "scheduled_hold",
        "completed",
        "cancelled",
        "other",
    ]
    follow_up_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_wait(self) -> TransitionPrivateWorkRequest:
        if (self.action in {"hold", "follow_up"}) != (self.follow_up_at is not None):
            raise ValueError("only hold/follow-up require a follow-up time")
        return self


class ReadPrivateWorkRequest(ServiceOnlyRequest):
    work_offset: int = Field(default=0, ge=0, le=1_000_000)
    task_id: str | None = Field(default=None, min_length=1, max_length=240)
    after_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    limit: int = Field(default=50, ge=1, le=100)


class ReadServiceContextRequest(SourceFactsRequest):
    source_kind: ContextSourceKind | None = None
    mail_thread: MemberGmailThreadSelector | None = None
    after_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def select_discovery_or_exact_sources(self) -> ReadServiceContextRequest:
        selected = sum(
            (
                self.source_kind is not None,
                bool(self.source_event_ids),
                self.mail_thread is not None,
            )
        )
        if selected > 1:
            raise ValueError(
                "choose source_kind discovery, exact source_event_ids, or one mail_thread"
            )
        return self


class MemberFollowUpRequest(ServiceOnlyRequest):
    """One current native instruction, never a caller-chosen audience or scope."""

    action: Literal["create", "status"]
    source_message_event_id: str = Field(min_length=1, max_length=240)
    instruction: str | None = Field(default=None, min_length=1, max_length=2000)
    follow_up_source_id: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def shape(self) -> MemberFollowUpRequest:
        if self.action == "create":
            if not self.instruction or self.follow_up_source_id is not None:
                raise ValueError("creation requires only an exact instruction excerpt")
        elif self.instruction is not None or not self.follow_up_source_id:
            raise ValueError("status requires only the original follow-up reference")
        return self


def _session(request: Request, provenance: seam.Provenance) -> ServiceSession:
    seam._authenticate(request)
    runtime = seam._runtime(request)
    secret = runtime.settings.openclaw_shared_secret
    if (
        provenance.call_kind != "tool"
        or provenance.actor_id != COS_SERVICE_PRINCIPAL_ID
        or provenance.channel_id != "administrateme-service"
        or provenance.agent_id != service_agent_id(COS_SERVICE_PRINCIPAL_ID)
        or provenance.account_id is not None
        or provenance.message_id is not None
        or provenance.thread_id is not None
        or not service_session_valid(
            COS_SERVICE_PRINCIPAL_ID, secret, provenance.session_key or ""
        )
    ):
        raise HTTPException(403, "exact service agent and private session required")
    return _internal_session(runtime, provenance.correlation_id)


def _internal_session(runtime: Any, correlation_id: str) -> ServiceSession:
    """Trusted server ownership after the caller's independent authorization."""
    principals = [
        principal
        for principal in runtime.settings.service_principals
        if principal.service_principal_id == COS_SERVICE_PRINCIPAL_ID
        and principal.kind == "chief_of_staff"
        and principal.party_id == COS_PARTY_ID
        and principal.private_scope == service_private_scope(COS_SERVICE_PRINCIPAL_ID)
    ]
    if len(principals) != 1:
        raise HTTPException(503, "exactly one configured CoS principal is required")
    scope = principals[0].private_scope
    context = ServiceRequestContext(
        tenant_id=runtime.settings.instance.tenant_id,
        actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
        operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
        represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        context_scope="service_private",
        resource_owner_principal_id=COS_SERVICE_PRINCIPAL_ID,
        owner_party_id=COS_PARTY_ID,
        responsible_party_id=COS_PARTY_ID,
        assignee_party_id=COS_PARTY_ID,
        visibility=(scope,),
        permissions=frozenset(
            {"read_context", "create_internal_work", "manage_internal_work"}
        ),
    )
    return ServiceSession(
        tenant_id=runtime.settings.instance.tenant_id,
        principal=PrincipalIdentity(
            "service_principal", COS_SERVICE_PRINCIPAL_ID, COS_PARTY_ID
        ),
        service_context=context,
        correlation_id=correlation_id,
    )


async def _instruction_source(
    runtime: Any, member: Any, event_id: str
) -> dict[str, Any]:
    source = await runtime.event_log.get(event_id)
    scope = f"private:{member.auth_member_id}"
    if (
        source is None
        or source["tenant_id"] != member.tenant_id
        or (source["type"], source["schema_version"])
        not in {("triage.intent.submitted", 2), ("cos.conversation.instruction", 1)}
        or source["owner_scope"] != scope
        or source["visibility_scope"] != scope
        or source["actor_identity"] != member.auth_member_id
        or source["payload"].get("member_id") != member.auth_member_id
        or (
            source["type"] == "triage.intent.submitted"
            and (
                source["payload"].get("submission_kind") != "member_forwarded"
                or source["payload"].get("intake_channel")
                not in {"imessage", "apple_imessage"}
            )
        )
    ):
        raise HTTPException(404, "private member instruction not found")
    return source


async def current_private_instruction(
    request: Request,
    provenance: seam.Provenance,
    source_message_event_id: str,
    *,
    required_permission: Literal[
        "read_context", "prepare_external_actions"
    ] = "read_context",
) -> tuple[Any, dict[str, Any]]:
    """Authenticate one durably captured current native private instruction."""
    member = seam._session(request, provenance, required_permission=required_permission)
    runtime = seam._runtime(request)
    p = provenance
    if p.channel_id in {"administrateme-portal", "gmail"}:
        from adminme.conversation_instructions import current_conversation_instruction

        current = await current_conversation_instruction(
            runtime, member, p, source_message_event_id
        )
        return member, current
    if (
        member.auth_role != "principal"
        or p.call_kind != "bridge"
        or p.channel_id != "imessage"
        or not p.account_id
        or not p.message_id
        or p.thread_id is not None
        or p.agent_id != member_agent_id(member.auth_member_id)
        or p.session_key
        != self_reply_session(member.auth_member_id, p.account_id, p.actor_id)
    ):
        raise HTTPException(403, "a current private member instruction is required")
    identity = [p.channel_id, p.account_id, p.actor_id, p.message_id]
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode()
    ).hexdigest()
    current = await runtime.event_log.get_by_idempotency(
        member.tenant_id, f"openclaw-forward:{digest}"
    )
    if current is None or current["event_id"] != source_message_event_id:
        raise HTTPException(
            403, "instruction does not match the current native message"
        )
    current = await _instruction_source(runtime, member, current["event_id"])
    age = (
        datetime.now(timezone.utc)
        - datetime.fromisoformat(current["payload"]["submitted_at"])
    ).total_seconds()
    if not 0 <= age <= 3600:
        raise HTTPException(403, "current member instruction expired")
    return member, current


@router.post("/service-follow-up")
async def member_follow_up(
    body: MemberFollowUpRequest, request: Request
) -> dict[str, Any]:
    """Accept one bounded instruction; disclose a receipt, never private work content."""
    member, current = await current_private_instruction(
        request, body.provenance, body.source_message_event_id
    )
    runtime = seam._runtime(request)
    p = body.provenance
    context = member.service_context
    # This exact instruction authorizes bounded internal organization. The CoS
    # owns her work under service-self authority, without a delegated-work grant.
    # Existing read_context still governs the source and every later reuse.
    if context is None:
        raise HTTPException(403, "standing source context is unavailable")
    source = (
        current
        if body.action == "create"
        else await _instruction_source(runtime, member, str(body.follow_up_source_id))
    )
    session = _internal_session(runtime, p.correlation_id)
    authority = source_authority(runtime, session, source, [context])
    if authority is None:
        raise HTTPException(
            403, "current source policy does not permit internal processing"
        )
    source_id = source["event_id"]
    request_id = f"member-follow-up:{source_id}"
    task_id = (
        "cos-member-follow-up:"
        + hashlib.sha256(f"{member.tenant_id}|{source_id}".encode()).hexdigest()[:32]
    )
    async with runtime.settings_command_locks.setdefault(
        f"cos-private-work:{member.tenant_id}:{task_id}", asyncio.Lock()
    ):
        previous = await runtime.event_log.get_by_idempotency(
            member.tenant_id, _key(session, "task.created", request_id)
        )
        duplicate = previous is not None
        if body.action == "create":
            instruction = body.instruction
            assert instruction is not None
            if (
                instruction != instruction.strip()
                or instruction not in source["payload"]["text"]
            ):
                raise HTTPException(
                    422, "retain an exact bounded excerpt of the current instruction"
                )
            fingerprint = seam._fingerprint(
                {
                    "member_id": member.auth_member_id,
                    "source_event_id": source_id,
                    "instruction": instruction,
                }
            )
            previous = await _prior(
                runtime, session, "task.created", request_id, fingerprint
            )
            if previous is None:
                event_id = await _append(
                    runtime,
                    session,
                    event_type="task.created",
                    version=4,
                    request_id=request_id,
                    fingerprint=fingerprint,
                    causation_id=source_id,
                    sensitivity=source["sensitivity"],
                    payload={
                        "task_id": task_id,
                        "title": "Follow up on a private member instruction",
                        "description": instruction,
                        "record_kind": "work",
                        "source_authorities": [authority],
                        "service_work_type": "follow_up",
                        "work_kind": "service_operational",
                        "owner_party_id": COS_PARTY_ID,
                        "responsible_party_id": COS_PARTY_ID,
                        "assignee_party_id": COS_PARTY_ID,
                        "service_party_id": COS_PARTY_ID,
                        "service_assignment_role": "internal",
                        "authority_kind": "service_self",
                        "authority_grant_id": None,
                        "source_invocation_id": p.correlation_id,
                        "created_by": COS_SERVICE_PRINCIPAL_ID,
                        "source_system": "member-requested-cos-follow-up",
                        "provider_effect_count": 0,
                    },
                )
                previous = await runtime.event_log.get(event_id)
        if previous is None:
            raise HTTPException(404, "requested CoS follow-up not found")
        # Check the immutable intake link, not later private text or source unions.
        if previous["payload"].get("source_authorities") != [authority]:
            raise HTTPException(
                404, "original follow-up authority is no longer available"
            )
        await runtime.wait_for_projection("tasks", previous["event_id"])
        row = get_task(
            runtime.projections.connection("tasks"), session, task_id=task_id
        )
        if row is None:
            raise HTTPException(
                503, "requested follow-up has not reached its read model"
            )
        await seam._record_tool_call(
            request,
            member,
            p,
            operation="tools.service-follow-up." + body.action,
            result_event_ids=[previous["event_id"]],
        )
        return {
            "follow_up_source_id": source_id,
            "task_id": task_id,
            "receipt_event_id": previous["event_id"],
            "last_event_id": row["last_event_id"],
            "status": row["status"],
            "duplicate": duplicate,
            "audience": [member.auth_member_id],
            "owner_party_id": COS_PARTY_ID,
            "private_body_withheld": True,
            "provider_effect_count": 0,
            "timer_registered": False,
        }


def _key(session: ServiceSession, event_type: str, request_id: str) -> str:
    digest = hashlib.sha256(request_id.encode()).hexdigest()
    return f"openclaw-service:{session.principal.principal_id}:{event_type}:{digest}"


async def _prior(
    runtime: Any,
    session: ServiceSession,
    event_type: str,
    request_id: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    event = await runtime.event_log.get_by_idempotency(
        session.tenant_id, _key(session, event_type, request_id)
    )
    if event is not None and event["raw_ref"] != f"request-sha256:{fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior request")
    return event


async def _append(
    runtime: Any,
    session: ServiceSession,
    *,
    event_type: str,
    version: int,
    payload: dict[str, Any],
    request_id: str,
    fingerprint: str,
    causation_id: str | None = None,
    sensitivity: Sensitivity = "normal",
) -> str:
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=version,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="openclaw-plugin",
        source_account_id=session.principal.principal_id,
        owner_scope=session.private_scope,
        visibility_scope=session.private_scope,
        sensitivity=sensitivity,
        actor_identity=session.event_actor_identity,
        idempotency_key=_key(session, event_type, request_id),
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )
    try:
        return await runtime.events.write(
            envelope, correlation_id=session.correlation_id, causation_id=causation_id
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior request"
        ) from exc


def _read_session_digest(provenance: seam.Provenance) -> str:
    return hashlib.sha256(
        ("cos-source-read:" + (provenance.session_key or "")).encode()
    ).hexdigest()


async def _audit(
    runtime: Any,
    session: ServiceSession,
    provenance: seam.Provenance,
    operation: str,
    result_event_ids: list[str],
    source_authorities: list[dict[str, Any]] | None = None,
) -> None:
    payload = {
        "operation": operation,
        **session.service_context.audit_payload(),
        "permissions": sorted(session.service_context.permissions),
        "openclaw_actor_id": provenance.actor_id,
        "agent_id": provenance.agent_id,
        # A signed session coordinate is a credential, not audit content.
        "session_key": None,
        "session_id": None,
        "channel_id": provenance.channel_id,
        "account_id": None,
        "message_id": None,
        "thread_id": None,
        "request_correlation_id": provenance.correlation_id,
        "causation_event_ids": result_event_ids[-1:],
        "result_event_ids": result_event_ids,
        "observation_event_ids": [],
        "reconciliation_event_ids": [],
        "call_kind": "tool",
        "openclaw_version": provenance.openclaw_version,
        "openclaw_commit": provenance.openclaw_commit,
    }
    if source_authorities is not None:
        payload.update(
            {
                "source_read_session_digest": _read_session_digest(provenance),
                "source_read_authorities": source_authorities,
            }
        )
    fingerprint = seam._fingerprint(payload)
    request_id = f"{operation}:{provenance.correlation_id}"
    prior = await _prior(
        runtime, session, "openclaw.tool_call.recorded", request_id, fingerprint
    )
    if prior:
        if source_authorities is not None:
            await runtime.wait_for_projection("tasks", prior["event_id"])
        return
    audit_id = await _append(
        runtime,
        session,
        event_type="openclaw.tool_call.recorded",
        version=3 if source_authorities is not None else 2,
        payload=payload,
        request_id=request_id,
        fingerprint=fingerprint,
        causation_id=result_event_ids[-1] if result_event_ids else None,
    )
    if source_authorities is not None:
        await runtime.wait_for_projection("tasks", audit_id)


async def _work(runtime: Any, session: ServiceSession, task_id: str) -> dict[str, Any]:
    row = get_task(runtime.projections.connection("tasks"), session, task_id=task_id)
    if (
        row is None
        or row.get("service_party_id") != COS_PARTY_ID
        or row.get("owner_party_id") != COS_PARTY_ID
        or row.get("service_assignment_role") != "internal"
        or row.get("authority_grant_id") is not None
        or row.get("owner_member_id") is not None
        or row.get("on_behalf_of_member_id") is not None
        or row.get("beneficiary_party_id") is not None
    ):
        raise HTTPException(404, "CoS-private work not found")
    return await work_view(runtime, session, row)


@router.post("/service-private-work", status_code=201)
async def create_work(
    body: CreatePrivateWorkRequest, request: Request
) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = seam._runtime(request)
    values = body.model_dump(mode="json", exclude={"provenance"})
    fingerprint = seam._fingerprint(values)
    task_id = (
        "cos-private-task:"
        + hashlib.sha256(
            f"{session.tenant_id}|{session.principal.principal_id}|{body.request_id}".encode()
        ).hexdigest()[:32]
    )
    async with runtime.settings_command_locks.setdefault(
        f"cos-private-work:{session.tenant_id}:{task_id}", asyncio.Lock()
    ):
        prior = await _prior(
            runtime, session, "task.created", body.request_id, fingerprint
        )
        if prior is not None:
            event_id = prior["event_id"]
        else:
            facts = await source_facts(runtime, session, body.source_event_ids)
            authorities = await retained_sources(
                runtime,
                session,
                _read_session_digest(body.provenance),
                [fact["authority"] for fact in facts],
            )
            sensitivity = cast(
                Sensitivity,
                max(
                    [
                        "normal",
                        *(source["source_sensitivity"] for source in authorities),
                    ],
                    key=("normal", "sensitive", "privileged").index,
                ),
            )
            payload = {
                "task_id": task_id,
                "title": body.title,
                "description": body.description,
                "record_kind": body.record_kind,
                "source_authorities": authorities,
                "due": values["due"],
                "follow_up_at": values["follow_up_at"],
                "service_work_type": body.service_work_type,
                "work_kind": "service_operational",
                "owner_member_id": None,
                "owner_party_id": COS_PARTY_ID,
                "responsible_party_id": COS_PARTY_ID,
                "assignee_party_id": COS_PARTY_ID,
                "beneficiary_party_id": None,
                "service_party_id": COS_PARTY_ID,
                "service_assignment_role": "internal",
                "authority_kind": "service_self",
                "authority_grant_id": None,
                "source_invocation_id": body.provenance.correlation_id,
                "created_by": COS_SERVICE_PRINCIPAL_ID,
                "source_system": "household-cos",
                "provider_effect_count": 0,
            }
            event_id = await _append(
                runtime,
                session,
                event_type="task.created",
                version=5
                if any(
                    source.get("authority_kind") == "service_self"
                    for source in authorities
                )
                else 4,
                payload=payload,
                request_id=body.request_id,
                fingerprint=fingerprint,
                sensitivity=sensitivity,
            )
        await runtime.wait_for_projection("tasks", event_id)
        await _audit(
            runtime, session, body.provenance, "tools.service-private-work", [event_id]
        )
        return {
            "task_id": task_id,
            "event_id": event_id,
            "duplicate": prior is not None,
            "work": await _work(runtime, session, task_id),
            "provider_effect_count": 0,
            "timer_registered": False,
        }


@router.post("/service-private-work/{task_id}/state")
async def transition_work(
    task_id: str,
    body: TransitionPrivateWorkRequest,
    request: Request,
) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = seam._runtime(request)
    fingerprint = seam._fingerprint(
        {"task_id": task_id, **body.model_dump(mode="json", exclude={"provenance"})}
    )
    async with runtime.settings_command_locks.setdefault(
        f"cos-private-work:{session.tenant_id}:{task_id}", asyncio.Lock()
    ):
        prior = await _prior(
            runtime, session, "cos.work.state_changed", body.request_id, fingerprint
        )
        if prior is not None:
            event_id = prior["event_id"]
        else:
            row = await _work(runtime, session, task_id)
            if row["last_event_id"] != body.expected_last_event_id:
                raise HTTPException(409, "CoS-private work changed")
            if row["status"] in {"done", "cancelled", "dismissed"}:
                raise HTTPException(409, "CoS-private work is already terminal")
            if body.follow_up_at and body.follow_up_at <= datetime.now(timezone.utc):
                raise HTTPException(422, "follow-up time must be in the future")
            status = {
                "start": "in_progress",
                "resume": "next",
                "hold": "deferred",
                "follow_up": "deferred",
                "complete": "done",
                "cancel": "cancelled",
            }[body.action]
            event_id = await _append(
                runtime,
                session,
                event_type="cos.work.state_changed",
                version=2,
                request_id=body.request_id,
                fingerprint=fingerprint,
                causation_id=body.expected_last_event_id,
                payload={
                    "task_id": task_id,
                    "action": body.action,
                    "resulting_status": status,
                    "changed_at": EventEnvelope.now_utc_iso(),
                    "actor_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                    "actor_service_party_id": COS_PARTY_ID,
                    "operator_principal_id": COS_AUTOMATION_OPERATOR_ID,
                    "authority_kind": "service_self",
                    "authority_grant_id": None,
                    "follow_up_at": body.follow_up_at.isoformat()
                    if body.follow_up_at
                    else None,
                    "reason_code": body.reason_code,
                    "expected_last_event_id": body.expected_last_event_id,
                    "provider_effect_count": 0,
                },
            )
        await runtime.wait_for_projection("tasks", event_id)
        await _audit(
            runtime,
            session,
            body.provenance,
            "tools.service-private-work.state",
            [event_id],
        )
        return {
            "task_id": task_id,
            "event_id": event_id,
            "duplicate": prior is not None,
            "work": await _work(runtime, session, task_id),
            "provider_effect_count": 0,
            "timer_registered": False,
        }


@router.post("/service-private-state")
async def read_work(body: ReadPrivateWorkRequest, request: Request) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = seam._runtime(request)
    if body.after_event_id:
        cursor = await runtime.event_log.get(body.after_event_id)
        if cursor is None or any(
            (
                cursor["tenant_id"] != session.tenant_id,
                cursor["owner_scope"] != session.private_scope,
                cursor["visibility_scope"] != session.private_scope,
            )
        ):
            raise HTTPException(404, "CoS-private history cursor not found")
    next_work_offset: int | None = None
    if body.task_id:
        work = [await _work(runtime, session, body.task_id)]
    else:
        work = []
        offset = body.work_offset
        while offset - body.work_offset < 1000 and len(work) < body.limit:
            rows = service_work_for_principal(
                runtime.projections.connection("tasks"),
                session,
                limit=100,
                offset=offset,
            )
            if not rows:
                break
            for row in rows:
                offset += 1
                try:
                    work.append(await work_view(runtime, session, row))
                except HTTPException:
                    continue
                if len(work) == body.limit:
                    next_work_offset = offset
                    break
            if len(rows) < 100:
                break
        if offset - body.work_offset >= 1000:
            next_work_offset = offset
    history: list[dict[str, Any]] = []
    next_cursor: str | None = None
    scanned = 0
    # The encrypted log is authoritative. A narrow owner/type scan is bounded
    # in returned rows, and never returns any member scope or session secrets.
    async for event in runtime.event_log.read_since(
        cursor=body.after_event_id,
        owner_scope=session.private_scope,
        types=["task.created", "cos.work.state_changed", "openclaw.tool_call.recorded"],
    ):
        visible = (
            event["tenant_id"] == session.tenant_id
            and event["visibility_scope"] == session.private_scope
        )
        if not visible:
            continue
        scanned += 1
        payload = event["payload"]
        relevant = not body.task_id or payload.get("task_id") == body.task_id
        event_task_id = payload.get("task_id")
        if event_task_id:
            try:
                await _work(runtime, session, event_task_id)
            except HTTPException:
                relevant = False
        if visible and body.task_id and event["type"] == "openclaw.tool_call.recorded":
            for result_id in payload.get("result_event_ids", []):
                result = await runtime.event_log.get(result_id)
                if (
                    result
                    and result["tenant_id"] == session.tenant_id
                    and result["owner_scope"] == session.private_scope
                    and result["visibility_scope"] == session.private_scope
                    and result["payload"].get("task_id") == body.task_id
                ):
                    relevant = True
        if (
            visible
            and relevant
            and payload.get("operation") != "tools.service-private-state"
        ):
            history.append(
                {
                    key: event.get(key)
                    for key in (
                        "event_id",
                        "type",
                        "schema_version",
                        "occurred_at",
                        "actor_identity",
                        "owner_scope",
                        "visibility_scope",
                        "correlation_id",
                        "causation_id",
                        "payload",
                    )
                }
            )
        if len(history) == body.limit or scanned == 1000:
            next_cursor = event["event_id"]
            break
    # Reading a retained note carries its full original source chain into this
    # run too. Re-saving its text cannot erase the original client restrictions.
    read_sources: dict[str, dict[str, Any]] = {}
    for item in [*work, *(entry["payload"] for entry in history)]:
        for source in item.get("source_authorities", []):
            prior_source = read_sources.get(source["source_event_id"])
            if prior_source is None or READ_RANK.get(
                source.get("read_mode", "typed_facts"), 1
            ) > READ_RANK.get(prior_source.get("read_mode", "typed_facts"), 1):
                read_sources[source["source_event_id"]] = source
    await _audit(
        runtime,
        session,
        body.provenance,
        "tools.service-private-state",
        [],
        list(read_sources.values()),
    )
    return {
        "principal_id": session.principal.principal_id,
        "party_id": session.principal.party_id,
        "owner_scope": session.private_scope,
        "work": work,
        "next_work_offset": next_work_offset,
        "history": history,
        "next_cursor": next_cursor,
        "authority_kind": "service_self",
        "provider_effect_count": 0,
        "timer_registered": False,
    }


@router.post("/service-context")
async def read_context(
    body: ReadServiceContextRequest, request: Request
) -> dict[str, Any]:
    session = _session(request, body.provenance)
    runtime = seam._runtime(request)
    contexts = active_contexts(runtime, session)
    facts: list[dict[str, Any]] = []
    next_cursor: str | None = None
    scan_truncated = False
    resolved: ResolvedMailThread | None = None
    if body.mail_thread is not None:
        resolved = await resolve_member_gmail_thread(
            runtime,
            session,
            body.mail_thread,
            after_event_id=body.after_event_id,
            limit=body.limit,
        )
        facts = resolved.facts
        next_cursor = resolved.next_cursor
        scan_truncated = resolved.scan_truncated
        contexts = [resolved.context]
    else:
        facts = await source_facts(runtime, session, body.source_event_ids)
    if body.mail_thread is None and body.after_event_id:
        await validate_context_cursor(runtime, session, contexts, body.after_event_id)
    if body.mail_thread is None and not body.source_event_ids:
        scopes = sorted(
            {session.private_scope}
            | {scope for context in contexts for scope in context.allowed_scopes}
        )
        scan_boundaries: list[str] = []
        for scope in scopes:
            scanned = 0
            matched = 0
            async for event in runtime.event_log.read_since(
                cursor=body.after_event_id,
                types=context_source_types(body.source_kind),
                owner_scope=scope,
            ):
                scanned += 1
                fact = (
                    fact_for(runtime, session, event, contexts)
                    if context_source_matches(event, body.source_kind)
                    else None
                )
                if fact:
                    facts.append(fact)
                    matched += 1
                if matched > body.limit:
                    break
                if scanned == 1000:
                    scan_truncated = True
                    scan_boundaries.append(event["event_id"])
                    break
        # Do not skip a scope that hit its scan budget before finding current
        # facts. The next page can advance past filtered historical revisions.
        boundary = min(scan_boundaries) if scan_boundaries else None
        if boundary is not None:
            facts = [fact for fact in facts if fact["event_id"] <= boundary]
        facts.sort(key=lambda fact: fact["event_id"])
        if len(facts) > body.limit:
            facts = facts[: body.limit]
            next_cursor = facts[-1]["event_id"]
        elif boundary is not None:
            next_cursor = boundary
    await _audit(
        runtime,
        session,
        body.provenance,
        "tools.service-context",
        [fact["event_id"] for fact in facts],
        [fact["authority"] for fact in facts],
    )
    if resolved is not None:
        revalidate_member_gmail_thread(runtime, session, resolved)
    return {
        "principal_id": session.principal.principal_id,
        **({"source_kind": body.source_kind} if body.source_kind is not None else {}),
        **(
            {"mail_thread": body.mail_thread.model_dump(mode="json")}
            if body.mail_thread
            else {}
        ),
        "facts": facts,
        "next_cursor": next_cursor,
        "standing_contexts": [context.audit_payload() for context in contexts],
        "scan_truncated": scan_truncated,
        "provider_effect_count": 0,
        "outside_disclosure_authorized": False,
    }
