"""Authenticated conversation -> native CoS worker -> verified private scratchpad.

OpenClaw owns execution. Existing encrypted work and source-read/audit events
retain the request, origin and proof; no second queue, scheduler or transcript.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from adminme.openclaw_topology import service_agent_id, service_session_key
from adminme.products.openclaw import main as seam
from adminme.products.openclaw import service_work as work
from adminme.products.openclaw.service_context import work_authorized
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

router = APIRouter(
    prefix="/api/openclaw/v1/tools/workstation-work", tags=["cos-workstation-work"]
)
SEED = "tools.workstation-work.seed"
READ_BACK = "tools.workstation-work.read-back"
TOOLS = ("administrateme_service_private_work", "administrateme_service_private_state")


class MemberWorkRequest(work.MemberFollowUpRequest):
    # Host-generated run identity; never exposed as a model parameter.
    run_nonce: str = Field(pattern=r"^[0-9a-f]{32}$")


class MemberResultRequest(work.ServiceOnlyRequest):
    source_message_event_id: str = Field(min_length=1, max_length=240)
    follow_up_source_id: str = Field(min_length=1, max_length=240)
    run_nonce: str = Field(pattern=r"^[0-9a-f]{32}$")


class ReadBackRequest(work.ServiceOnlyRequest):
    task_id: str = Field(min_length=1, max_length=240)
    last_event_id: str = Field(min_length=1, max_length=240)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _service(runtime: Any, p: seam.Provenance, nonce: str) -> seam.Provenance:
    return seam.Provenance(
        actor_id=COS_SERVICE_PRINCIPAL_ID,
        agent_id=service_agent_id(COS_SERVICE_PRINCIPAL_ID),
        session_key=service_session_key(
            COS_SERVICE_PRINCIPAL_ID, runtime.settings.openclaw_shared_secret, nonce
        ),
        channel_id="administrateme-service",
        call_kind="tool",
        correlation_id=nonce,
        openclaw_version=p.openclaw_version,
        openclaw_commit=p.openclaw_commit,
    )


def _note_coordinate(tenant_id: str, source_id: str) -> tuple[str, str]:
    request_id = "workstation-note:" + source_id
    digest = hashlib.sha256(
        f"{tenant_id}|{COS_SERVICE_PRINCIPAL_ID}|{request_id}".encode()
    ).hexdigest()[:32]
    return request_id, "cos-private-task:" + digest


def _content_hash(note: dict[str, Any]) -> str:
    # Fixed ordered tuple is identical in the native plugin; no model digest.
    return hashlib.sha256(
        json.dumps(
            [note["task_id"], note["last_event_id"], note["title"], note.get("notes")],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _receipt(
    runtime: Any, session: Any, p: seam.Provenance, operation: str
) -> dict[str, Any] | None:
    return await runtime.event_log.get_by_idempotency(
        session.tenant_id,
        work._key(
            session,
            "openclaw.tool_call.recorded",
            f"{operation}:{work._read_session_digest(p)}",
        ),
    )


async def _binding(
    runtime: Any, session: Any, p: seam.Provenance
) -> dict[str, Any] | None:
    seed = await _receipt(runtime, session, p, SEED)
    if seed is None:
        return None
    payload = seed["payload"]
    ids = payload.get("result_event_ids", [])
    if len(ids) != 1:
        raise HTTPException(403, "workstation origin receipt is unavailable")
    anchor = await runtime.event_log.get(ids[0])
    if (
        not anchor
        or anchor["type"] != "task.created"
        or anchor["owner_scope"] != session.private_scope
        or anchor["tenant_id"] != session.tenant_id
        or anchor["payload"].get("source_system") != "member-requested-cos-follow-up"
    ):
        raise HTTPException(403, "workstation origin receipt is invalid")
    sources = anchor["payload"]["source_authorities"]
    if len(sources) != 1 or not await work_authorized(
        runtime, session, {"source_authorities": sources}
    ):
        raise HTTPException(403, "workstation source authority is no longer available")
    source_id = sources[0]["source_event_id"]
    request_id, task_id = _note_coordinate(session.tenant_id, source_id)
    original = await runtime.event_log.get_by_idempotency(
        session.tenant_id, work._key(session, "task.created", request_id)
    )
    return {
        "source_id": source_id,
        "source_authorities": sources,
        "anchor": anchor,
        "request_id": request_id,
        "task_id": task_id,
        "existing": original,
    }


@router.post("/admit")
async def admit(body: MemberWorkRequest, request: Request) -> dict[str, Any]:
    # Existing intake independently authenticates current human input and the
    # original owner on status. Its work remains CoS-owned service-self work.
    receipt = await work.member_follow_up(
        work.MemberFollowUpRequest.model_validate(
            body.model_dump(exclude={"run_nonce"})
        ),
        request,
    )
    runtime = seam._runtime(request)
    p = _service(runtime, body.provenance, body.run_nonce)
    session = work._internal_session(runtime, p.correlation_id)
    anchor = await runtime.event_log.get(receipt["receipt_event_id"])
    assert anchor is not None
    sources = anchor["payload"]["source_authorities"]
    if len(sources) != 1:
        raise HTTPException(403, "one authenticated instruction is required")
    # Seed the existing durable read ledger BEFORE native execution. The model
    # cannot remove the instruction's source restrictions from its new note.
    p = p.model_copy(update={"correlation_id": work._read_session_digest(p)})
    await work._audit(runtime, session, p, SEED, [anchor["event_id"]], sources)
    binding = await _binding(runtime, session, p)
    assert binding is not None
    if body.action == "status" and binding["existing"] is None:
        return {
            "status": "pending_or_unknown",
            "follow_up_source_id": binding["source_id"],
            "work_id": receipt["task_id"],
            "provider_effect_count": 0,
        }
    original_instruction = await runtime.event_log.get(binding["source_id"])
    assert original_instruction is not None
    return {
        "status": "ready",
        "mode": "read" if binding["existing"] else "create",
        "worker_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "worker_request_id": body.run_nonce,
        "follow_up_source_id": binding["source_id"],
        "work_id": receipt["task_id"],
        "artifact_request_id": binding["request_id"],
        "artifact_task_id": binding["task_id"],
        "instruction": original_instruction["payload"]["text"],
        "tools": list(TOOLS),
        "provider_effect_count": 0,
    }


@router.post("/policy")
async def policy(body: work.ServiceOnlyRequest, request: Request) -> dict[str, Any]:
    session = work._session(request, body.provenance)
    runtime = seam._runtime(request)
    binding = await _binding(runtime, session, body.provenance)
    if binding is None:
        return {"bound": False}
    return {
        "bound": True,
        "mode": "read" if binding["existing"] else "create",
        "artifact_request_id": binding["request_id"],
        "artifact_task_id": binding["task_id"],
        "tools": list(TOOLS),
    }


async def _verified_note(
    runtime: Any, session: Any, binding: dict[str, Any]
) -> dict[str, Any]:
    if binding["existing"] is None:
        raise HTTPException(409, "scratchpad has not been created")
    original = binding["existing"]
    if original["payload"].get("record_kind") not in {"note", "scratchpad"}:
        raise HTTPException(409, "worker did not create the requested scratchpad")
    note = await work._work(runtime, session, binding["task_id"])
    # The first slice can return a draft derived solely from this member's
    # instruction. Other source chains remain private, even with read access.
    if note["source_authorities"] != binding["source_authorities"]:
        raise HTTPException(403, "scratchpad contains other restricted sources")
    return note


@router.post("/read-back")
async def read_back(body: ReadBackRequest, request: Request) -> dict[str, Any]:
    session = work._session(request, body.provenance)
    runtime = seam._runtime(request)
    binding = await _binding(runtime, session, body.provenance)
    if binding is None or body.task_id != binding["task_id"]:
        raise HTTPException(403, "scratchpad is outside this native worker run")
    note = await _verified_note(runtime, session, binding)
    if (
        note["last_event_id"] != body.last_event_id
        or _content_hash(note) != body.content_sha256
    ):
        raise HTTPException(409, "native read-back differs from retained scratchpad")
    p = body.provenance.model_copy(
        update={"correlation_id": work._read_session_digest(body.provenance)}
    )
    await work._audit(runtime, session, p, READ_BACK, [note["last_event_id"]])
    return {
        "verified": True,
        "task_id": note["task_id"],
        "last_event_id": note["last_event_id"],
        "provider_effect_count": 0,
    }


@router.post("/result")
async def result(body: MemberResultRequest, request: Request) -> dict[str, Any]:
    # Recheck the current member and origin at disclosure, independently of the
    # worker's authority. No worker transcript or other private notes escape.
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
    p = _service(runtime, body.provenance, body.run_nonce)
    session = work._internal_session(runtime, p.correlation_id)
    binding = await _binding(runtime, session, p)
    if binding is None or binding["source_id"] != body.follow_up_source_id:
        raise HTTPException(403, "worker run does not match the authenticated request")
    proof = await _receipt(runtime, session, p, READ_BACK)
    if proof is None:
        return {
            "status": "pending_or_unknown",
            "follow_up_source_id": body.follow_up_source_id,
            "work_id": receipt["task_id"],
            "provider_effect_count": 0,
        }
    note = await _verified_note(runtime, session, binding)
    if proof["payload"]["result_event_ids"] != [note["last_event_id"]]:
        raise HTTPException(409, "scratchpad changed after native read-back")
    member = seam._session(request, body.provenance, required_permission="read_context")
    await seam._record_tool_call(
        request,
        member,
        body.provenance,
        operation="tools.workstation-work.result",
        result_event_ids=[proof["event_id"]],
    )
    return {
        "status": "verified",
        "follow_up_source_id": body.follow_up_source_id,
        "work_id": receipt["task_id"],
        "artifact_task_id": note["task_id"],
        "title": note["title"],
        "content": note.get("notes"),
        "read_back_event_id": proof["event_id"],
        "last_event_id": note["last_event_id"],
        "worker_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "resource_owner_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "audience": receipt["audience"],
        "return_to": "current_authenticated_conversation",
        "provider_effect_count": 0,
        "timer_registered": False,
    }
