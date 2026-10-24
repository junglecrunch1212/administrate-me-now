"""Bounded owner-only closed-package ingress with independent evidence approval."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from adminme.events.envelope import EventEnvelope
from adminme.events.schemas.finance_import import (
    FinanceImportApprovedV1,
    FinanceImportManifest,
)
from adminme.finance.access_control import control_lock, policy_for_session
from adminme.finance.closed_import import decode_package
from adminme.finance.importer import import_publication
from adminme.finance.import_work import await_settled, run_cpu
from adminme.finance.planning_store import import_projection
from adminme.finance.publication import PublicationError
from adminme.lib.session import Session
from adminme.products.auth import session_from_request
from adminme.products.member.finance_access import require_owner, current_control
from adminme.products.member.main import (
    _guard_member_write,
    _prior_settings_receipt,
    _settings_request_fingerprint,
    _write_settings_receipt,
)

router = APIRouter(tags=["member-finance-import"])
SessionDep = Annotated[Session, Depends(session_from_request)]
APPROVAL_EVENT = "settings.finance_import_approved"
DECODE_SECONDS = 30


class ImportApprovalRequest(BaseModel):
    model_config = {"extra": "forbid", "hide_input_in_errors": True}
    request_id: str = Field(min_length=8, max_length=120)
    expected_policy_revision: int = Field(ge=1, strict=True)
    manifest: FinanceImportManifest


def _check_policy(policy: Any, manifest: FinanceImportManifest, revision: int) -> None:
    if (
        not policy.books_enabled
        or policy.revision != revision
        or policy.binding != manifest.binding
    ):
        raise HTTPException(409, "finance_import_policy_changed")


@router.post("/api/member/v1/finance/import-approvals")
async def approve_import(request: Request, session: SessionDep) -> dict[str, Any]:
    require_owner(request, session)
    # Bound the manifest before parsing; a giant approval body must not reach
    # FastAPI's eager JSON decoder or compete with ordinary household requests.
    raw = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 32768:
                    raise ValueError("approval body limit")
                raw.extend(chunk)
        body = ImportApprovalRequest.model_validate(json.loads(raw))
    except (ValueError, TypeError, RecursionError, TimeoutError):
        raise HTTPException(422, "finance_import_approval_invalid") from None
    finally:
        raw.clear()
    runtime = request.app.state.runtime
    fingerprint = _settings_request_fingerprint(
        APPROVAL_EVENT, body.model_dump(exclude={"request_id"})
    )
    async with control_lock(runtime, session):
        policy = await current_control(runtime, session)
        _check_policy(policy, body.manifest, body.expected_policy_revision)
        prior = await _prior_settings_receipt(
            runtime,
            session,
            event_type=APPROVAL_EVENT,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
        )
        if prior is not None:
            require_owner(request, session)
            return {
                "approval_event_id": prior["event_id"],
                "expires_at": prior["payload"]["expires_at"],
                "idempotent": True,
                "provider_effect_count": 0,
            }
        now = datetime.now(timezone.utc)
        payload = {
            "owner_id": session.auth_member_id,
            "changed_by_member_id": session.auth_member_id,
            "policy_revision": policy.revision,
            "policy_event_id": policy.event_id,
            "manifest": body.manifest.model_dump(),
            "approved_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "provider_effects": False,
        }
        await _guard_member_write(runtime, session, APPROVAL_EVENT, payload)
        require_owner(request, session)
        event_id = await _write_settings_receipt(
            runtime,
            session,
            event_type=APPROVAL_EVENT,
            payload=payload,
            request_id=body.request_id,
            request_fingerprint=fingerprint,
            owner_member_id=session.auth_member_id,
            sensitivity="privileged",
        )
        require_owner(request, session)
        return {
            "approval_event_id": event_id,
            "expires_at": payload["expires_at"],
            "idempotent": False,
            "provider_effect_count": 0,
        }


async def _approval(
    runtime: Any, session: Session, event_id: str
) -> FinanceImportApprovedV1:
    try:
        row = await runtime.event_log.read_finance_import_approval(
            session.tenant_id, session.auth_member_id, event_id
        )
        if row is None:
            raise ValueError("missing approval")
        value = FinanceImportApprovedV1.model_validate(row["payload"])
        if (
            row["actor_identity"] != session.auth_member_id
            or row["source_account_id"] != session.auth_member_id
            or row["source_adapter"] != "member-settings-control-plane"
            or row["visibility_scope"] != f"private:{session.auth_member_id}"
            or row["schema_version"] != 1
            or value.owner_id != session.auth_member_id
            or datetime.fromisoformat(value.expires_at) <= datetime.now(timezone.utc)
        ):
            raise ValueError("invalid approval")
        policy = policy_for_session(runtime, session)
        _check_policy(policy, value.manifest, value.policy_revision)
        if policy.event_id != value.policy_event_id:
            raise ValueError("changed policy event")
        return value
    except (ValueError, TypeError, KeyError, RuntimeError):
        raise HTTPException(403, "finance_import_approval_unavailable") from None


async def _audit(
    runtime: Any,
    session: Session,
    approval_id: str,
    revision: int,
    outcome: str,
    reason: str,
    accounting: dict | None,
    dated: dict | None,
) -> str:
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            tenant_id=session.tenant_id,
            type="finance.import_result",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="member-finance-import",
            source_account_id=session.auth_member_id,
            actor_identity=session.auth_member_id,
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="privileged",
            payload={
                "owner_id": session.auth_member_id,
                "approval_event_id": approval_id,
                "policy_revision": revision,
                "outcome": outcome,
                "reason": reason,
                "publication_event_id": accounting.get("event_id")
                if accounting
                else None,
                "projection_event_id": dated.get("event_id") if dated else None,
                "provider_effects": False,
            },
        ),
        correlation_id=session.correlation_id,
    )


async def _decode_settled(data: bytearray, manifest: FinanceImportManifest):
    """Keep admission and input ownership until the native decoder has stopped.

    Cancelling an asyncio waiter cannot stop a running Python thread. Shield its
    task and drain it, even under repeated cancellation, before releasing the
    request's import lock. A cancelled/timed-out decode can never enter ingress.
    """
    try:
        async with asyncio.timeout(DECODE_SECONDS):
            return await run_cpu(decode_package, data, manifest)
    finally:
        # run_cpu has settled on every exit, including cancellation/timeout.
        data.clear()


@router.post("/api/member/v1/finance/imports/{approval_id}")
async def import_approved_package(
    approval_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    require_owner(request, session)
    runtime = request.app.state.runtime
    lock = runtime.settings_command_locks.setdefault(
        "finance-closed-package-import", asyncio.Lock()
    )
    if lock.locked():
        raise HTTPException(409, "finance_import_busy")
    async with lock:
        await current_control(runtime, session)
        approval = await _approval(runtime, session, approval_id)
        limit = approval.manifest.encoded_body_limit()
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "finance_import_json_required")
        accounting = dated = None
        entered_ingress = False
        data = bytearray()
        try:
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > limit:
                        raise PublicationError("finance_import_body_limit")
                    data.extend(chunk)
            publication, projection, package = await _decode_settled(
                data, approval.manifest
            )
            require_owner(request, session)
            if (
                publication["tenant_id"] != session.tenant_id
                or publication["owner_id"] != session.auth_member_id
            ):
                raise PublicationError("finance_import_owner_mismatch")
            # A slow upload/decoder must not prevent the owner from revoking
            # access. Acquire the owner lock only for the bounded event writes,
            # then re-read approval and policy after the unlocked decoding phase.
            async with control_lock(runtime, session):
                await current_control(runtime, session)
                await _guard_member_write(
                    runtime,
                    session,
                    "finance.publication_accepted",
                    {"operation": "import_exact_owner_approved_books"},
                )
                require_owner(request, session)
                await _approval(runtime, session, approval_id)
                entered_ingress = True
                accounting = await import_publication(
                    runtime, session, publication, provenance_package=package
                )
                if projection is not None:
                    dated = await import_projection(runtime, session, projection)
                require_owner(request, session)
                audit_id = await _audit(
                    runtime,
                    session,
                    approval_id,
                    approval.policy_revision,
                    "accepted",
                    "exact_approved_package",
                    accounting,
                    dated,
                )
                return {
                    "state": "accepted",
                    "accounting": accounting,
                    "dated": dated,
                    "audit_event_id": audit_id,
                    "provider_effect_count": 0,
                }
        except (PublicationError, TimeoutError) as exc:
            reason = (
                str(exc)
                if isinstance(exc, PublicationError)
                else "finance_import_timeout"
            )
            outcome = "incomplete" if entered_ingress else "rejected"
            audit_id = await _audit(
                runtime,
                session,
                approval_id,
                approval.policy_revision,
                outcome,
                reason,
                accounting,
                dated,
            )
            # An ambiguous append or rejected dated output cannot undo accepted
            # accounting. Preserve the last good heads and return explicit scope.
            raise HTTPException(
                409 if entered_ingress else 422,
                {
                    "state": outcome,
                    "reason": reason,
                    "accounting": accounting,
                    "dated": dated,
                    "audit_event_id": audit_id,
                    "provider_effect_count": 0,
                },
            ) from None
        except HTTPException:
            await await_settled(
                _audit(
                    runtime,
                    session,
                    approval_id,
                    approval.policy_revision,
                    "incomplete" if entered_ingress else "rejected",
                    "finance_import_authorization_changed",
                    accounting,
                    dated,
                )
            )
            raise
        except asyncio.CancelledError:
            # Native work has drained before this handler. Retain a terminal
            # receipt even if the caller repeats cancellation during the audit.
            await await_settled(
                _audit(
                    runtime,
                    session,
                    approval_id,
                    approval.policy_revision,
                    "incomplete" if entered_ingress else "rejected",
                    "finance_import_request_cancelled",
                    accounting,
                    dated,
                )
            )
            raise
        finally:
            data.clear()
