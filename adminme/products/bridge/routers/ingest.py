"""
Bridge ingest endpoints — /api/bridge/{ingest,ingest/batch,health}.

Per memo 04 §2.2 (F-3: Tailscale-User-Login binds ``owner_scope``
non-overridably) + [§6.19] (bridge sovereignty) + [§7.3, D17 corollary 1]
(bridges emit canonical events; the ingest router persists). [D7] payload
schemas validated by ``EventLog.append`` — malformed payloads surface as
422.

The router lifts ``EventLog`` + ``MemberIdResolver`` off
``request.app.state`` so the production startup and test wiring share
one code path.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

from adminme.events.envelope import EventEnvelope
from adminme.events.log import AppendValidationError
from adminme.events.writer import EventSink
from adminme.products.bridge.auth import (
    MemberIdResolver,
    resolve_member_id,
    resolve_tailscale_identity,
)

router = APIRouter(prefix="/api/bridge", tags=["bridge"])


class BatchIngestRequest(BaseModel):
    model_config = {"extra": "forbid"}
    envelopes: list[dict[str, Any]] = Field(min_length=1)


def _get_event_sink(request: Request) -> EventSink:
    events = getattr(request.app.state, "event_sink", None)
    if events is None:
        raise HTTPException(503, detail="event_sink_unavailable")
    return events


def _get_resolver(request: Request) -> MemberIdResolver:
    resolver = getattr(request.app.state, "member_id_resolver", None)
    if resolver is None:
        raise HTTPException(503, detail="member_id_resolver_unavailable")
    return resolver


def _stamp_owner_scope(
    payload: dict[str, Any],
    resolved_member_id: str,
    expected_tenant_id: str | None = None,
) -> EventEnvelope:
    """Validate envelope shape + enforce F-3 owner_scope binding.

    Per memo 04 §2.2: a claimed ``owner_scope`` that disagrees with the
    Tailscale-resolved identity is a HARD 403 refusal — never silently
    overwritten."""
    try:
        envelope_in = EventEnvelope.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "envelope_validation_failed", "errors": exc.errors()},
        ) from exc

    expected_scope = f"private:{resolved_member_id}"
    if expected_tenant_id is not None and envelope_in.tenant_id != expected_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tenant_claim_mismatch",
                "claimed": envelope_in.tenant_id,
                "expected": expected_tenant_id,
            },
        )
    if envelope_in.owner_scope != expected_scope:
        # F-3: hard 403, NOT silent overwrite. Memo 04 §7(d).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "owner_scope_claim_mismatch",
                "claimed": envelope_in.owner_scope,
                "expected": expected_scope,
            },
        )
    return envelope_in


def _expected_tenant_id(request: Request) -> str | None:
    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    instance = getattr(settings, "instance", None)
    value = getattr(instance, "tenant_id", None)
    return str(value) if value else None


def _enforce_member_knowledge_contract(
    request: Request,
    envelope: EventEnvelope,
    member_id: str,
) -> None:
    if not envelope.type.startswith("member_knowledge.note_"):
        return
    expected_scope = f"private:{member_id}"
    source_kind = envelope.payload.get("source_kind")
    expected_adapters = {
        "apple_notes": "knowledge-apple-notes",
        "apple_voice_memos": "knowledge-apple-voice-memos",
        "obsidian": "knowledge-obsidian",
        "fixture": "fixture-claim",
    }
    violations: list[str] = []
    if envelope.visibility_scope != expected_scope:
        violations.append("visibility_scope")
    if envelope.payload.get("member_id") != member_id:
        violations.append("payload.member_id")
    expected_adapter = (
        expected_adapters.get(source_kind) if isinstance(source_kind, str) else None
    )
    if expected_adapter != envelope.source_adapter:
        violations.append("source_adapter")
    if (
        source_kind in {"apple_voice_memos", "obsidian"}
        and envelope.sensitivity == "normal"
    ):
        violations.append("sensitivity_floor")
    runtime = getattr(request.app.state, "runtime", None)
    roles = getattr(getattr(runtime, "settings", None), "member_roles", {})
    if source_kind == "obsidian" and roles.get(member_id) == "child":
        violations.append("adult_only")
    if violations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "member_knowledge_contract_denied",
                "violations": violations,
            },
        )


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_single(request: Request, payload: dict[str, Any]) -> dict[str, str]:
    tailscale_identity = resolve_tailscale_identity(request)
    member_id = resolve_member_id(tailscale_identity, _get_resolver(request))
    envelope = _stamp_owner_scope(payload, member_id, _expected_tenant_id(request))
    _enforce_member_knowledge_contract(request, envelope, member_id)
    try:
        event_id = await _get_event_sink(request).write(envelope)
    except AppendValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "append_validation_failed", "message": str(exc)},
        ) from exc
    request.app.state.ingest_count += 1
    return {"event_id": event_id, "owner_scope": envelope.owner_scope}


@router.post("/ingest/batch", status_code=status.HTTP_202_ACCEPTED)
async def ingest_batch(request: Request, body: BatchIngestRequest) -> dict[str, Any]:
    tailscale_identity = resolve_tailscale_identity(request)
    member_id = resolve_member_id(tailscale_identity, _get_resolver(request))

    stamped: list[EventEnvelope] = []
    for offset, raw in enumerate(body.envelopes):
        try:
            stamped.append(
                _stamp_owner_scope(raw, member_id, _expected_tenant_id(request))
            )
            _enforce_member_knowledge_contract(request, stamped[-1], member_id)
        except HTTPException as exc:
            # Per F-3 + §7 c2: whole-batch reject with offset of first bad envelope.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "batch_envelope_rejected",
                    "offset": offset,
                    "reason": exc.detail,
                },
            ) from exc

    try:
        event_ids = await _get_event_sink(request).write_batch(stamped)
    except AppendValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "append_validation_failed", "message": str(exc)},
        ) from exc
    request.app.state.ingest_count += len(event_ids)
    return {"event_ids": event_ids, "count": len(event_ids)}


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "bridge_endpoint": "/api/bridge/ingest",
        "ingest_count": int(getattr(request.app.state, "ingest_count", 0)),
    }


__all__ = ["router", "BatchIngestRequest"]
