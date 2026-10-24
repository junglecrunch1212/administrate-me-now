"""Authenticated work -> native browser observation -> same-origin result.

The native browser executes the read. Only its installed result middleware calls
capture, before exposing content to the model; no model-facing capture tool exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.products.openclaw import main as seam
from adminme.products.openclaw import service_work as work
from adminme.products.openclaw import workstation_work as handoff
from adminme.products.openclaw.service_context import work_authorized
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.workstation_inventory import AccountIdentity
from adminme.workstation_sources import (
    authorized_resource,
    identity_matches,
    observation_authority,
)

router = APIRouter(
    prefix="/api/openclaw/v1/tools/workstation-browser", tags=["workstation-browser"]
)
REQUESTED = "cos.workstation.read_requested"
OBSERVED = "cos.workstation.source_observed"


class BrowserReadRequest(handoff.MemberWorkRequest):
    resource_id: str = Field(min_length=1, max_length=300)


class BrowserCaptureRequest(work.ServiceOnlyRequest):
    route_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_target_id: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    actual_identity: AccountIdentity
    excerpt: str = Field(min_length=1, max_length=2000)
    truncated: bool


def _resource(
    runtime: Any, resource_id: str, *, for_read: bool = True
) -> dict[str, Any]:
    try:
        return authorized_resource(runtime, resource_id, for_read=for_read)
    except (OSError, ValueError, KeyError):
        raise HTTPException(
            403, "current browser identity or source authority is unavailable"
        ) from None


async def _event(
    runtime: Any, session: Any, p: seam.Provenance, kind: str
) -> dict[str, Any] | None:
    return await runtime.event_log.get_by_idempotency(
        session.tenant_id, work._key(session, kind, work._read_session_digest(p))
    )


async def _bound(
    runtime: Any, session: Any, p: seam.Provenance, *, for_read: bool
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None:
    admitted = await _event(runtime, session, p, REQUESTED)
    if admitted is None:
        return None
    payload = admitted["payload"]
    anchor = await runtime.event_log.get(payload["work_event_id"])
    if (
        not anchor
        or anchor["type"] != "task.created"
        or anchor["tenant_id"] != session.tenant_id
        or anchor["owner_scope"] != session.private_scope
        or anchor["payload"].get("source_system") != "member-requested-cos-follow-up"
    ):
        raise HTTPException(403, "browser work origin is unavailable")
    sources = anchor["payload"]["source_authorities"]
    if (
        len(sources) != 1
        or sources[0]["source_event_id"] != payload["instruction_source_event_id"]
        or not await work_authorized(runtime, session, {"source_authorities": sources})
    ):
        raise HTTPException(403, "browser instruction authority is unavailable")
    resource = _resource(runtime, payload["resource_id"], for_read=for_read)
    if (
        resource["authority_sha256"] != payload["authority_sha256"]
        or payload["requester_member_id"] not in resource["audience_member_ids"]
        or (for_read and resource["route_sha256"] != payload["route_sha256"])
    ):
        raise HTTPException(403, "browser source authority changed")
    if for_read and await runtime.observation.is_active():
        raise HTTPException(409, "native browser work is paused in observation mode")
    return admitted, resource, sources


@router.post("/admit")
async def admit(body: BrowserReadRequest, request: Request) -> dict[str, Any]:
    if body.action != "create":
        raise HTTPException(
            422, "use a fresh authenticated instruction for a current browser read"
        )
    member, current = await work.current_private_instruction(
        request, body.provenance, body.source_message_event_id
    )
    runtime = seam._runtime(request)
    resource = _resource(runtime, body.resource_id)
    if member.auth_member_id not in resource["audience_member_ids"]:
        raise HTTPException(403, "browser source is not authorized for this requester")
    if await runtime.observation.is_active():
        raise HTTPException(409, "native browser work is paused in observation mode")
    receipt = await work.member_follow_up(
        work.MemberFollowUpRequest.model_validate(
            body.model_dump(exclude={"run_nonce", "resource_id"})
        ),
        request,
    )
    p = handoff._service(runtime, body.provenance, body.run_nonce)
    session = work._internal_session(runtime, p.correlation_id)
    payload = {
        "operation": "native_browser_read",
        "request_id": body.run_nonce,
        "worker_session_digest": work._read_session_digest(p),
        "instruction_source_event_id": current["event_id"],
        "work_event_id": receipt["receipt_event_id"],
        "requester_member_id": member.auth_member_id,
        "resource_id": resource["resource_id"],
        "inventory_binding_id": resource["inventory_binding_id"],
        "inventory_binding_revision": resource["binding_revision"],
        "authority_sha256": resource["authority_sha256"],
        "route_sha256": resource["route_sha256"],
        "audience_member_ids": [member.auth_member_id],
        "requested_at": EventEnvelope.now_utc_iso(),
        "operating_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "resource_owner_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "provider_effect_count": 0,
    }
    prior = await _event(runtime, session, p, REQUESTED)
    if prior:
        stable = {key: value for key, value in payload.items() if key != "requested_at"}
        if stable != {
            key: value
            for key, value in prior["payload"].items()
            if key != "requested_at"
        }:
            raise HTTPException(
                409, "native browser run already has a different origin"
            )
    else:
        await work._append(
            runtime,
            session,
            event_type=REQUESTED,
            version=1,
            payload=payload,
            request_id=work._read_session_digest(p),
            fingerprint=seam._fingerprint(payload),
            causation_id=current["event_id"],
            sensitivity=cast(Sensitivity, resource["sensitivity"]),
        )
    bound = await _bound(runtime, session, p, for_read=True)
    assert bound is not None
    audit = p.model_copy(update={"correlation_id": work._read_session_digest(p)})
    await work._audit(
        runtime,
        session,
        audit,
        "tools.workstation-browser.seed",
        [bound[0]["event_id"]],
        bound[2],
    )
    return {
        "status": "ready",
        "worker_request_id": body.run_nonce,
        "follow_up_source_id": current["event_id"],
        "instruction": current["payload"]["text"],
        "resource_id": resource["resource_id"],
        "provider_effect_count": 0,
    }


@router.post("/policy")
async def policy(body: work.ServiceOnlyRequest, request: Request) -> dict[str, Any]:
    session = work._session(request, body.provenance)
    runtime = seam._runtime(request)
    bound = await _bound(runtime, session, body.provenance, for_read=True)
    if bound is None:
        return {"bound": False}
    observed = await _event(runtime, session, body.provenance, OBSERVED)
    return {
        "bound": True,
        "mode": "complete" if observed else "read",
        "resource": bound[1],
    }


@router.post("/capture")
async def capture(body: BrowserCaptureRequest, request: Request) -> dict[str, Any]:
    session = work._session(request, body.provenance)
    runtime = seam._runtime(request)
    bound = await _bound(runtime, session, body.provenance, for_read=True)
    if bound is None:
        raise HTTPException(403, "native browser run has no authenticated request")
    admitted, resource, sources = bound
    identity = body.actual_identity.model_dump(exclude_none=True)
    if (
        body.route_sha256 != resource["route_sha256"]
        or body.native_target_id != resource["native_target_id"]
        or body.url != resource["url"]
        or not identity_matches(resource, identity)
        or not body.excerpt.strip()
        or len(body.excerpt.encode()) > 2000
    ):
        raise HTTPException(
            403, "native browser read does not match its authorized source"
        )
    payload = {
        "operation": "native_browser_read",
        "request_event_id": admitted["event_id"],
        "instruction_source_event_id": admitted["payload"][
            "instruction_source_event_id"
        ],
        "worker_session_digest": work._read_session_digest(body.provenance),
        "resource_id": resource["resource_id"],
        "inventory_binding_id": resource["inventory_binding_id"],
        "inventory_binding_revision": resource["binding_revision"],
        "authority_sha256": resource["authority_sha256"],
        "route_sha256": resource["route_sha256"],
        "native_target_id": body.native_target_id,
        "url": body.url,
        "device": resource["device"],
        "os_user": resource["os_user"],
        "browser_profile": resource["browser_profile"],
        "actual_identity": body.actual_identity.model_dump(),
        "excerpt": body.excerpt,
        "truncated": body.truncated,
        "content_sha256": hashlib.sha256(body.excerpt.encode()).hexdigest(),
        "restrictions": resource["restrictions"],
        "audience_member_ids": admitted["payload"]["audience_member_ids"],
        "observed_at": EventEnvelope.now_utc_iso(),
        "operating_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "resource_owner_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "provider_effect_count": 0,
    }
    async with runtime.settings_command_locks.setdefault(
        "native-browser-capture:" + payload["worker_session_digest"],
        asyncio.Lock(),
    ):
        observed = await _event(runtime, session, body.provenance, OBSERVED)
        if observed is None:
            event = EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=session.tenant_id,
                type=OBSERVED,
                schema_version=1,
                occurred_at=payload["observed_at"],
                source_adapter="openclaw-browser",
                source_account_id=resource["expected_identity"]["account"],
                owner_scope=session.private_scope,
                visibility_scope=session.private_scope,
                sensitivity=cast(Sensitivity, resource["sensitivity"]),
                actor_identity=session.event_actor_identity,
                idempotency_key=work._key(
                    session, OBSERVED, payload["worker_session_digest"]
                ),
                payload=payload,
            )
            event_id = await runtime.events.write(
                event,
                correlation_id=body.provenance.correlation_id,
                causation_id=admitted["event_id"],
            )
            observed = await runtime.event_log.get(event_id)
        elif {
            key: value
            for key, value in observed["payload"].items()
            if key != "observed_at"
        } != {key: value for key, value in payload.items() if key != "observed_at"}:
            raise HTTPException(
                409, "browser run already captured a different observation"
            )
    assert observed is not None
    authority = observation_authority(runtime, session, observed)
    if authority is None:
        raise HTTPException(403, "observed browser source is no longer authorized")
    audit = body.provenance.model_copy(
        update={"correlation_id": work._read_session_digest(body.provenance)}
    )
    await work._audit(
        runtime,
        session,
        audit,
        "tools.workstation-browser.capture",
        [observed["event_id"]],
        [*sources, authority],
    )
    return {
        "status": "verified",
        "observation_event_id": observed["event_id"],
        "excerpt": body.excerpt,
        "truncated": body.truncated,
        "restriction": "internal_processing_only",
        "provider_effect_count": 0,
    }


@router.post("/result")
async def result(body: handoff.MemberResultRequest, request: Request) -> dict[str, Any]:
    receipt = await work.member_follow_up(
        work.MemberFollowUpRequest(
            provenance=body.provenance,
            source_message_event_id=body.source_message_event_id,
            action="status",
            follow_up_source_id=body.follow_up_source_id,
        ),
        request,
    )
    runtime = seam._runtime(request)
    p = handoff._service(runtime, body.provenance, body.run_nonce)
    session = work._internal_session(runtime, p.correlation_id)
    bound = await _bound(runtime, session, p, for_read=False)
    if (
        bound is None
        or bound[0]["payload"]["instruction_source_event_id"]
        != body.follow_up_source_id
    ):
        raise HTTPException(403, "browser run does not match the original request")
    observed = await _event(runtime, session, p, OBSERVED)
    if observed is None:
        return {
            "status": "pending_or_unknown",
            "follow_up_source_id": body.follow_up_source_id,
            "provider_effect_count": 0,
        }
    authority = observation_authority(runtime, session, observed)
    member = seam._session(request, body.provenance, required_permission="read_context")
    if (
        authority is None
        or member.auth_member_id not in bound[1]["audience_member_ids"]
        or observed["payload"]["audience_member_ids"] != [member.auth_member_id]
        or receipt["audience"] != [member.auth_member_id]
    ):
        raise HTTPException(403, "browser source is not authorized for this audience")
    await seam._record_tool_call(
        request,
        member,
        body.provenance,
        operation="tools.workstation-browser.result",
        result_event_ids=[observed["event_id"]],
    )
    return {
        "status": "verified",
        "follow_up_source_id": body.follow_up_source_id,
        "work_id": receipt["task_id"],
        "observation_event_id": observed["event_id"],
        "excerpt": observed["payload"]["excerpt"],
        "truncated": observed["payload"]["truncated"],
        "observed_at": observed["payload"]["observed_at"],
        "resource_id": observed["payload"]["resource_id"],
        "audience": receipt["audience"],
        "return_to": "current_authenticated_conversation",
        "worker_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "provider_effect_count": 0,
    }
