"""Owner-confirmed Notes actions using the existing two-approval command spine."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from adminme.apple_notes_writes import assert_editable_note, note_body_html
from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.projections.member_knowledge.queries import get_visible


router = APIRouter(tags=["member-apple-notes"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class AppleNoteCommandRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=1, max_length=240)
    operation: Literal["create_note", "update_note", "append_note", "delete_note"]
    provider_account_id: str = Field(min_length=1, max_length=500)
    account_external_id: str = Field(min_length=1, max_length=1_000)
    folder_external_id: str = Field(min_length=1, max_length=1_000)
    knowledge_id: str | None = Field(default=None, min_length=1, max_length=240)
    expected_last_event_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )
    expected_provider_revision: str | None = Field(
        default=None, min_length=1, max_length=1_000
    )
    body: str | None = Field(default=None, max_length=100_000)
    title: str | None = Field(default=None, max_length=1_000)


def prepare_note_payload(
    body: AppleNoteCommandRequest,
    *,
    permission: dict[str, Any],
    note: dict[str, Any] | None,
    member_id: str,
) -> tuple[dict[str, Any], str | None]:
    if (
        permission.get("provider_account_id") != body.provider_account_id
        or permission.get("account_external_id") != body.account_external_id
        or body.folder_external_id not in permission.get("allowed_container_ids", [])
        or body.operation not in permission.get("operations", [])
    ):
        raise HTTPException(
            403, "This Notes account, folder, or action is not allowed in Settings."
        )
    target: str | None = None
    if body.operation == "create_note":
        if (
            body.knowledge_id
            or body.expected_last_event_id
            or body.expected_provider_revision
        ):
            raise HTTPException(
                422, "A new note cannot name an existing note or revision."
            )
    else:
        if (
            note is None
            or note.get("access_mode") != "owner"
            or note.get("member_id") != member_id
            or note.get("source_kind") != "apple_notes"
            or note.get("account_external_id") != body.account_external_id
            or note.get("folder_external_id") != body.folder_external_id
        ):
            raise HTTPException(
                404, "An owned note in the selected account and folder was not found."
            )
        if (
            not body.expected_last_event_id
            or body.expected_last_event_id != note.get("last_event_id")
            or not body.expected_provider_revision
            or body.expected_provider_revision != note.get("provider_revision")
        ):
            raise HTTPException(
                409, "The note changed; refresh it before approving a change."
            )
        try:
            assert_editable_note(note)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        target = str(note.get("source_external_id") or "")
        if not target:
            raise HTTPException(409, "The native note identity is unavailable.")
    if body.operation == "delete_note":
        if body.body is not None or body.title is not None:
            raise HTTPException(422, "Deleting a note cannot also change its text.")
        rendered = None
    else:
        if body.operation == "append_note" and body.title is not None:
            raise HTTPException(422, "Appending text cannot replace a note's title.")
        try:
            rendered = note_body_html(body=body.body or "", title=body.title)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {
        "member_id": member_id,
        "account_external_id": body.account_external_id,
        "folder_external_id": body.folder_external_id,
        "allowed_container_ids": list(permission["allowed_container_ids"]),
        "expected_provider_revision": body.expected_provider_revision,
        **({"body": rendered} if rendered is not None else {}),
        "content_privacy_guard": {
            "member_bound": True,
            "source_scope": f"private:{member_id}",
            "plain_text_only": True,
            "rich_document_replacement": False,
        },
    }, target


def _request_fingerprint(body: AppleNoteCommandRequest) -> str:
    return hashlib.sha256(
        json.dumps(
            body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


async def _prior_action(
    runtime: Any, session: Session, request_id: str
) -> dict[str, Any] | None:
    prior = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"provider-action:{session.auth_member_id}:{request_id}"
    )
    if prior is None:
        return None
    if (
        prior.get("type") != "member.provider_action.confirmed"
        or prior.get("tenant_id") != session.tenant_id
        or prior.get("owner_scope") != f"private:{session.auth_member_id}"
        or prior.get("payload", {}).get("member_id") != session.auth_member_id
        or prior.get("payload", {}).get("provider") != "apple_notes"
    ):
        raise HTTPException(409, "This request identifier belongs to another action.")
    return prior


@router.get("/api/member/v1/notes/commands/status")
async def note_command_status(
    request: Request,
    session: SessionDep,
    request_id: Annotated[str, Query(min_length=1, max_length=240)],
) -> dict[str, Any]:
    from adminme.products.member.main import _require_private_notes, _runtime

    _require_private_notes(session)
    runtime = _runtime(request)
    if runtime.event_log is None:
        raise HTTPException(503, "Notes action history is unavailable.")
    prior = await _prior_action(runtime, session, request_id)
    if prior is None:
        return {"state": "not_recorded", "request_id": request_id}
    approval = prior["payload"]
    result: dict[str, Any] = {
        "state": "awaiting_openclaw_approval",
        "request_id": request_id,
        "command_id": approval["action_id"],
        "domain_approval_event_id": prior["event_id"],
        "request_fingerprint": approval["request_fingerprint"],
        "request": (approval.get("command_payload") or {}).get("note_request"),
    }
    command = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"external-sync-approved:{prior['event_id']}"
    )
    if command is None:
        return result
    command_payload = command.get("payload") or {}
    if (
        command.get("owner_scope") != prior["owner_scope"]
        or command_payload.get("provider") != "apple_notes"
        or command_payload.get("member_id") != session.auth_member_id
        or command_payload.get("domain_approval_event_id") != prior["event_id"]
        or command_payload.get("request_fingerprint") != approval["request_fingerprint"]
    ):
        return {**result, "state": "unknown"}
    result.update(state="pending", command_event_id=command["event_id"])
    terminal = await runtime.event_log.get_by_idempotency(
        session.tenant_id, f"external-sync-result:{approval['action_id']}"
    )
    if terminal is None:
        return result
    payload = terminal.get("payload") or {}
    if (
        terminal.get("owner_scope") != prior["owner_scope"]
        or payload.get("member_id") != session.auth_member_id
        or payload.get("provider") != "apple_notes"
        or payload.get("command_event_id") != command["event_id"]
        or payload.get("domain_approval_event_id") != prior["event_id"]
        or payload.get("request_fingerprint") != approval["request_fingerprint"]
    ):
        return {**result, "state": "unknown"}
    native = payload.get("result") or {}
    state = "unknown"
    if terminal.get("type") == "external_sync.write_completed":
        if native.get("applied") is True and not payload.get("suppressed"):
            state = "completed"
        elif payload.get("suppressed"):
            state = "failed"
    elif terminal.get("type") == "external_sync.write_failed" and (
        payload.get("provider_called") is False
        or payload.get("error_kind")
        in {"validation", "permission", "conflict", "not_found", "unsupported"}
    ):
        state = "failed"
    return {
        **result,
        "state": state,
        "result_event_id": terminal["event_id"],
        "external_id": payload.get("external_id"),
        "automatic_retry": False,
    }


@router.post("/api/member/v1/notes/commands", status_code=202)
async def note_command(
    body: AppleNoteCommandRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    from adminme.apple_service_permissions import (
        bind_command_permission,
        service_permission,
    )
    from adminme.products.member.main import (
        _append_provider_action_confirmation,
        _require_private_notes,
        _runtime,
    )

    _require_private_notes(session)
    runtime = _runtime(request)
    if (
        runtime.projections is None
        or runtime.events is None
        or runtime.event_log is None
    ):
        raise HTTPException(503, "Notes state is unavailable.")
    fingerprint = _request_fingerprint(body)
    prior = await _prior_action(runtime, session, body.request_id)
    if prior is not None:
        if prior.get("raw_ref") != f"request-sha256:{fingerprint}":
            raise HTTPException(409, "Request id conflicts with a prior Notes action.")
        current = await note_command_status(request, session, body.request_id)
        return {
            **current,
            "accepted": True,
            "duplicate": True,
            "command_id": body.request_id,
            "event_id": prior["event_id"],
            "domain_approval_event_id": prior["event_id"],
            "observation_mode": runtime._observation_active,
        }
    permission = service_permission(runtime, session.auth_member_id, "apple_notes")
    if permission is None:
        raise HTTPException(409, "Enable Apple Notes changes in Settings first.")
    note = (
        get_visible(
            runtime.projections.connection("member_knowledge"),
            session,
            knowledge_id=body.knowledge_id,
        )
        if body.knowledge_id
        else None
    )
    provider_payload, target = prepare_note_payload(
        body, permission=permission, note=note, member_id=session.auth_member_id
    )
    provider_payload["note_request"] = body.model_dump(mode="json")
    command_payload = {
        "command_id": body.request_id,
        "member_id": session.auth_member_id,
        "provider": "apple_notes",
        "provider_account_id": body.provider_account_id,
        "operation": body.operation,
        "target_external_id": target,
        "expected_provider_revision": body.expected_provider_revision,
        "payload": provider_payload,
        "requested_at": EventEnvelope.now_utc_iso(),
    }
    try:
        bind_command_permission(runtime, session.auth_member_id, command_payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    decision = await runtime.guarded_write.check(
        session, "external_sync.write_requested", command_payload
    )
    if not decision.pass_:
        raise HTTPException(
            409 if decision.layer_failed == "governance" else 403,
            {"reason": decision.reason, "correlation_id": decision.correlation_id},
        )
    event_id, duplicate = await _append_provider_action_confirmation(
        runtime,
        session,
        command_payload=command_payload,
        request_id=body.request_id,
        request_fingerprint=fingerprint,
        source_adapter="member-apple-notes",
        sensitivity=cast(Sensitivity, (note or {}).get("sensitivity") or "normal"),
        causation_id=body.expected_last_event_id,
    )
    return {
        "accepted": True,
        "duplicate": duplicate,
        "command_id": body.request_id,
        "event_id": event_id,
        "domain_approval_event_id": event_id,
        "state": "awaiting_openclaw_approval",
        "observation_mode": runtime._observation_active,
    }
