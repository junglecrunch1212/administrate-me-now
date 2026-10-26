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
from adminme.finance.access_control import (
    FinanceAccessError,
    control_lock,
    policy_for_session,
)
from adminme.finance.closed_import import decode_package
from adminme.finance.importer import _settled
from adminme.finance.import_work import await_settled, run_cpu
from adminme.finance.worker import prepare_import
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
APPROVED_BATCH_SETTLE_SECONDS = 40


async def _settled_approved_batch(runtime: Any, deadline: float) -> None:
    """Allow the existing 30s Money worker and bounded cleanup to finish.

    This single monotonic deadline applies only after the approved batch commits.
    Pre-ingress and legacy ingress retain their existing ten-second wait.
    """
    timeout = asyncio.timeout_at(deadline)
    try:
        async with timeout:
            while True:
                state = await runtime.bus.subscriber_status("projection:money")
                if state.get("degraded"):
                    raise PublicationError("publication_projection_unavailable")
                if asyncio.get_running_loop().time() >= deadline:
                    raise PublicationError("publication_projection_pending")
                if state["lag_count"] == 0 and not state.get("draining", False):
                    return
                await asyncio.sleep(0.02)
    except TimeoutError:
        if not timeout.expired():
            raise
        raise PublicationError("publication_projection_pending") from None


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


def _final_disclosure(request, session, approval):
    require_owner(request, session)
    try:
        _check_policy(
            policy_for_session(request.app.state.runtime, session),
            approval.manifest,
            approval.policy_revision,
        )
    except FinanceAccessError:
        raise HTTPException(503, "finance_control_unavailable") from None


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
        return _validate_approval(runtime, session, row)
    except (ValueError, TypeError, KeyError, RuntimeError):
        raise HTTPException(403, "finance_import_approval_unavailable") from None


def _validate_approval(runtime, session, row) -> FinanceImportApprovedV1:
    try:
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
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "finance_import_json_required")
        accounting = dated = None
        entered_ingress = False
        prepared = None
        publication_lock = getattr(runtime, "_finance_publication_lock", None)
        if publication_lock is None:
            publication_lock = runtime._finance_publication_lock = asyncio.Lock()
        if publication_lock.locked():
            raise HTTPException(409, "finance_import_busy")
        try:
            async with publication_lock:
                await _settled(runtime)
                # Full package/proof/schema validation and immutable payload
                # encoding happen without the owner's revocation lock.
                prepared = await prepare_import(
                    request, runtime, session, approval.manifest
                )
                require_owner(request, session)
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

                    def heads(conn):
                        key = (
                            session.tenant_id,
                            session.auth_member_id,
                            approval.manifest.binding.book_id,
                        )
                        result = []
                        for table, column in (
                            ("finance_publication_heads", "publication_id"),
                            ("finance_dated_projection_heads", "projection_id"),
                        ):
                            row = conn.execute(
                                f"SELECT {column} FROM {table} WHERE tenant_id=? AND owner_id=? AND book_id=?",
                                key,
                            ).fetchone()
                            result.append(row[0] if row else None)
                        return result

                    if (
                        await runtime.projections.read("money", heads)
                        != prepared.metadata["expected_heads"]
                    ):
                        raise PublicationError("finance_import_head_changed")
                    # One transaction is the linearization point. Cancellation
                    # settles it and records exact accepted ids before audit.
                    entered_ingress = True

                    async def authorize_commit():
                        await _approval(runtime, session, approval_id)
                        require_owner(request, session)

                    def commit_guard():
                        row = runtime.event_log._read_finance_import_approval(
                            session.tenant_id, session.auth_member_id, approval_id
                        )
                        _validate_approval(runtime, session, row)
                        require_owner(request, session)

                    async def commit():
                        nonlocal accounting, dated
                        assert prepared is not None
                        ids = await runtime.events._write_finance_batch(
                            session.tenant_id,
                            session.auth_member_id,
                            approval.manifest.binding.book_id,
                            prepared.events,
                            before_commit=authorize_commit,
                            commit_guard=commit_guard,
                            correlation_id=session.correlation_id,
                        )
                        for event, event_id in zip(prepared.events, ids, strict=True):
                            if event.type == "finance.publication_accepted":
                                accounting = {
                                    "state": "accepted",
                                    "event_id": event_id,
                                    "publication_id": prepared.metadata[
                                        "publication_id"
                                    ],
                                    "idempotent": prepared.metadata[
                                        "accounting_existing"
                                    ],
                                    "provider_effect_count": 0,
                                }
                            elif event.type == "finance.projection_verified":
                                dated = {
                                    "state": "verified",
                                    "event_id": event_id,
                                    "projection_id": prepared.metadata["projection_id"],
                                    "idempotent": prepared.metadata["dated_existing"],
                                    "provider_effect_count": 0,
                                }

                    await await_settled(commit())
                prepared.events.clear()
                await _settled_approved_batch(
                    runtime,
                    asyncio.get_running_loop().time() + APPROVED_BATCH_SETTLE_SECONDS,
                )
                require_owner(request, session)
                if prepared.metadata.get("dated_error"):
                    raise PublicationError(prepared.metadata["dated_error"])
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
                _final_disclosure(request, session, approval)
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
            _final_disclosure(request, session, approval)
            # An ambiguous append or rejected dated output cannot undo accepted
            # accounting. Preserve the last good heads and return explicit scope.
            raise HTTPException(
                409
                if entered_ingress
                or reason
                in {
                    "publication_identity_or_prior_conflict",
                    "publication_prior_conflict",
                }
                else 422,
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
            if prepared is not None:
                prepared.events.clear()
