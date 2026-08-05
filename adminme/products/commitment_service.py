"""Authorized, optimistic commitment command helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from fastapi import HTTPException

from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.lib.session import Session
from adminme.projections.commitments.queries import get_commitment


async def prior_command(
    runtime: Any,
    session: Session,
    *,
    event_type: str,
    commitment_id: str,
    request_id: str,
    request_fingerprint: str,
) -> tuple[str, dict[str, Any]] | None:
    key = f"command:{event_type}:{commitment_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is None:
        return None
    if prior["raw_ref"] != f"request-sha256:{request_fingerprint}":
        raise HTTPException(409, "idempotency key conflicts with prior command body")
    return prior["event_id"], owned_commitment(runtime, session, commitment_id)


def command_fingerprint(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def owned_commitment(
    runtime: Any, session: Session, commitment_id: str
) -> dict[str, Any]:
    if runtime.projections is None:
        raise HTTPException(503, "commitments projection unavailable")
    row = get_commitment(
        runtime.projections.connection("commitments"),
        session,
        commitment_id=commitment_id,
    )
    if row is None or row["owed_by_party"] != session.auth_member_id:
        raise HTTPException(404, "commitment not found")
    return row


async def emit_command(
    runtime: Any,
    session: Session,
    *,
    commitment_id: str,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    request_fingerprint: str,
    expected_last_event_id: str,
) -> tuple[str, dict[str, Any], bool]:
    locks: dict[str, asyncio.Lock] = runtime.commitment_command_locks
    lock = locks.setdefault(f"{session.tenant_id}:{commitment_id}", asyncio.Lock())
    async with lock:
        return await _emit_command_locked(
            runtime,
            session,
            commitment_id=commitment_id,
            event_type=event_type,
            payload=payload,
            request_id=request_id,
            request_fingerprint=request_fingerprint,
            expected_last_event_id=expected_last_event_id,
        )


async def _emit_command_locked(
    runtime: Any,
    session: Session,
    *,
    commitment_id: str,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    request_fingerprint: str,
    expected_last_event_id: str,
) -> tuple[str, dict[str, Any], bool]:
    """Serialize compare-and-append within the single-process runtime."""
    if runtime.event_log is None or runtime.events is None:
        raise HTTPException(503, "event writer unavailable")
    key = f"command:{event_type}:{commitment_id}:{request_id}"
    prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    if prior is not None:
        if prior["raw_ref"] != f"request-sha256:{request_fingerprint}":
            raise HTTPException(
                409, "idempotency key conflicts with prior command body"
            )
        row = owned_commitment(runtime, session, commitment_id)
        return prior["event_id"], row, True
    row = owned_commitment(runtime, session, commitment_id)
    if row["last_event_id"] != expected_last_event_id:
        raise HTTPException(
            409, {"code": "stale_write", "current": row["last_event_id"]}
        )
    if runtime.guarded_write is None:
        raise HTTPException(503, "write authorization unavailable")
    decision = await runtime.guarded_write.check(session, event_type, payload)
    if not decision.pass_:
        raise HTTPException(
            403,
            {
                "code": "write_denied",
                "layer": decision.layer_failed,
                "reason": decision.reason,
            },
        )
    scope = row["owner_scope"]
    envelope = EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="product-api",
        source_account_id="commitments",
        owner_scope=scope,
        visibility_scope=row["visibility_scope"],
        sensitivity=row["sensitivity"],
        actor_identity=session.auth_member_id,
        idempotency_key=key,
        raw_ref=f"request-sha256:{request_fingerprint}",
        payload=payload,
    )
    try:
        event_id = await runtime.events.write(
            envelope,
            correlation_id=session.correlation_id,
            causation_id=row["last_event_id"],
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            409, "idempotency key conflicts with prior command body"
        ) from exc
    await runtime.wait_for_projection("commitments", event_id)
    updated = owned_commitment(runtime, session, commitment_id)
    return event_id, updated, False


__all__ = ["command_fingerprint", "emit_command", "owned_commitment", "prior_command"]
