"""Reauthorize finance-derived conversation disclosure at its final boundary.

Durable private session markers survive runtime restart and protect historical
finance context. Short-lived run receipts pin the authority and calculation /
publication dependencies delivered to the model. They contain no financial
raw source links or bearer session keys. Bounded typed results remain encrypted
and owner-private so final answers can be rendered without model arithmetic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.lib.session import Session
from adminme.finance.access_control import (
    FinanceAccessError,
    analysis_enabled,
    read_scope,
    refresh_read_scope,
)
from adminme.service_identities import (
    COS_SERVICE_PRINCIPAL_ID,
)

DISCLOSURE_TTL_SECONDS = 300


class FinanceDisclosureError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _session_digest(session: Session, session_key: str) -> str:
    if not session_key or len(session_key) > 1000:
        raise FinanceDisclosureError("finance_disclosure_session_required")
    return _digest([session.tenant_id, session.auth_member_id, session_key])


def _check_current_owner(runtime: Any, session: Session) -> None:
    if (
        session.auth_role != "principal"
        or session.is_view_as
        or runtime.settings.member_roles.get(session.auth_member_id) != "principal"
        or runtime.settings.instance.tenant_id != session.tenant_id
        or not analysis_enabled(runtime, session)
    ):
        raise FinanceDisclosureError("finance_disclosure_authority_changed")


def _disclosure_access(runtime: Any, session: Session):
    try:
        return read_scope(runtime, session, advisor=True).policy
    except FinanceAccessError:
        raise FinanceDisclosureError("finance_disclosure_authority_changed") from None


def validate_current_authority(
    runtime: Any,
    session: Session,
    expected_digest: str | None = None,
) -> str:
    """The book grant is separate from unrelated general CoS delegation."""
    _check_current_owner(runtime, session)
    try:
        scope = read_scope(runtime, session, advisor=True)
    except FinanceAccessError:
        raise FinanceDisclosureError("finance_disclosure_authority_changed") from None
    digest = _digest(
        {
            "tenant_id": session.tenant_id,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            **scope.authority_coordinates(),
        }
    )
    if expected_digest is not None and digest != expected_digest:
        raise FinanceDisclosureError("finance_disclosure_authority_changed")
    return digest


def _marker_key(session_hash: str) -> str:
    return f"finance-disclosure-session:{session_hash}"


def _run_key(session_hash: str, run_hash: str, slot: int) -> str:
    return f"finance-disclosure-run:{session_hash}:{run_hash}:{slot}"


def session_marked_in_memory(runtime: Any, session: Session, session_key: str) -> bool:
    """Observe a marker created during a stream without per-token database work."""
    return bool(getattr(runtime, "_finance_disclosure_overflow", False)) or (
        _session_digest(session, session_key)
        in getattr(runtime, "_finance_disclosure_sessions", set())
    )


def _remember_marker(runtime: Any, session_hash: str) -> None:
    if not hasattr(runtime, "_finance_disclosure_sessions"):
        runtime._finance_disclosure_sessions = set()
    if len(runtime._finance_disclosure_sessions) >= 1024:
        runtime._finance_disclosure_overflow = True
    else:
        runtime._finance_disclosure_sessions.add(session_hash)


async def _read(runtime: Any, session: Session, key: str) -> dict[str, Any] | None:
    row = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
    return _checked_payload(row, session) if row else None


def _checked_payload(row: dict[str, Any], session: Session) -> dict[str, Any]:
    if (
        row["type"] != "finance.disclosure_recorded"
        or row["owner_scope"] != f"private:{session.auth_member_id}"
        or row["visibility_scope"] != f"private:{session.auth_member_id}"
        or row["payload"]["member_id"] != session.auth_member_id
    ):
        raise FinanceDisclosureError("finance_disclosure_provenance_invalid")
    return dict(row["payload"])


async def _append(
    runtime: Any, session: Session, key: str, payload: dict[str, Any]
) -> None:
    await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            tenant_id=session.tenant_id,
            type="finance.disclosure_recorded",
            schema_version=1,
            occurred_at=payload["issued_at"],
            source_adapter="finance-disclosure",
            source_account_id="finance-disclosure",
            owner_scope=f"private:{session.auth_member_id}",
            visibility_scope=f"private:{session.auth_member_id}",
            sensitivity="privileged",
            actor_identity=COS_SERVICE_PRINCIPAL_ID,
            idempotency_key=key,
            payload=payload,
        )
    )


async def record_disclosure(
    runtime: Any,
    session: Session,
    provenance: Any,
    result: dict[str, Any],
    *,
    slot: int,
) -> None:
    """Record before a private tool result leaves the domain boundary."""
    session_hash = _session_digest(session, provenance.session_key or "")
    message_id = provenance.message_id or ""
    if not message_id.startswith("finance-run:") or not 1 <= slot <= 6:
        raise FinanceDisclosureError("finance_disclosure_run_required")
    run_id = message_id.removeprefix("finance-run:")
    if not run_id or len(run_id) > 200:
        raise FinanceDisclosureError("finance_disclosure_run_required")
    run_hash = _digest(run_id)
    _check_current_owner(runtime, session)
    authority_hash = validate_current_authority(runtime, session)
    async with runtime.settings_command_locks.setdefault(
        f"finance-disclosure:{session_hash}", asyncio.Lock()
    ):
        key = _run_key(session_hash, run_hash, slot)
        rows = await runtime.event_log.get_by_idempotency_keys(
            session.tenant_id, [_marker_key(session_hash), key]
        )
        existing = {
            row["idempotency_key"]: _checked_payload(row, session) for row in rows
        }
        marker = existing.get(_marker_key(session_hash))
        now = datetime.now(timezone.utc)
        access = _disclosure_access(runtime, session)
        common = {
            "book_binding_sha256": _digest(access.binding.model_dump())
            if access.binding
            else None,
            "member_id": session.auth_member_id,
            "session_sha256": session_hash,
            "authority_sha256": authority_hash,
            "issued_at": now.isoformat(),
        }
        if marker is None:
            await _append(
                runtime,
                session,
                _marker_key(session_hash),
                {"kind": "session", **common},
            )
        _remember_marker(runtime, session_hash)
        from adminme.finance.answers import answer_reference

        result_hash = answer_reference(result)
        prior = existing.get(key)
        if prior is not None:
            if prior["result_sha256"] != result_hash:
                raise FinanceDisclosureError("finance_disclosure_result_conflict")
        else:
            publication = result.get("publication") or result
            retained = {
                "kind": "run",
                **common,
                "run_sha256": run_hash,
                "slot": slot,
                "result_sha256": result_hash,
                "result_json": json.dumps(
                    result,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                "book_id": publication.get("book_id"),
                "publication_id": publication.get("publication_id"),
                "head_publication_id": publication.get("current_head_publication_id"),
                "calculator_id": result.get("calculator_id"),
                "model_version": result.get("model_version"),
                "expires_at": (
                    now + timedelta(seconds=DISCLOSURE_TTL_SECONDS)
                ).isoformat(),
            }
            await _append(runtime, session, key, retained)
        if prior is not None:
            retained = prior
        retained_key = (
            f"finance-disclosure-result:{session_hash}:{authority_hash}:{result_hash}"
        )
        if await _read(runtime, session, retained_key) is None:
            await _append(runtime, session, retained_key, retained)
        # Persistence yields to the event loop. Finish any ordinary request
        # before the final synchronous authorization query; nothing awaits
        # between that query and the caller's final transport/privacy checks.
        if not await runtime.finance_admission.yield_to_foreground():
            from adminme.finance.budgets import FinanceBudgetError

            raise FinanceBudgetError("finance_core_capacity_busy")
        try:
            await refresh_read_scope(
                runtime, read_scope(runtime, session, advisor=True), advisor=True
            )
        except FinanceAccessError:
            raise FinanceDisclosureError(
                "finance_disclosure_authority_changed"
            ) from None
        validate_current_authority(runtime, session, authority_hash)


async def session_has_finance(runtime: Any, session: Session, session_key: str) -> bool:
    session_hash = _session_digest(session, session_key)
    present = await _read(runtime, session, _marker_key(session_hash)) is not None
    if present:
        _remember_marker(runtime, session_hash)
    return present


async def check_disclosure(
    runtime: Any,
    session: Session,
    session_key: str,
    *,
    run_id: str | None = None,
    require_run: bool = False,
    answer_text: str | None = None,
) -> str | None:
    """Called for history, final portal content, and each native send/claim.

    The durable marker identifies a finance-bearing conversation. Current
    authority gates access; each run receipt independently binds its exact grant.
    A new book-control revision can retrieve fresh results but cannot validate
    old receipts. The analysis switch does not erase prior conversations.
    Already delivered conversation is retained; this checks new domain retrieval
    and referenced delivery, not universal erasure of past discussion.
    """
    session_hash = _session_digest(session, session_key)
    from adminme.finance.answers import FinanceAnswerError, selected_references

    try:
        refs = selected_references(answer_text) if answer_text is not None else []
    except FinanceAnswerError as exc:
        raise FinanceDisclosureError(str(exc)) from None
    receipts: list[dict[str, Any]] = []
    if run_id is not None:
        if not run_id or len(run_id) > 200:
            raise FinanceDisclosureError("finance_disclosure_run_required")
        run_hash = _digest(run_id)
        keys = [_run_key(session_hash, run_hash, slot) for slot in range(1, 7)]
        rows = await runtime.event_log.get_by_idempotency_keys(session.tenant_id, keys)
        receipts = [_checked_payload(row, session) for row in rows]
    # An earlier Finance question is not a permanent ordinary-conversation gate.
    # Already delivered conversation remains in OpenClaw. Reference reuse requires the
    # current exact book/audience grant, but never another source acquisition.
    if not receipts and not refs:
        return answer_text
    expected = validate_current_authority(runtime, session)
    now = datetime.now(timezone.utc)
    for receipt in receipts:
        if (
            receipt["authority_sha256"] != expected
            or datetime.fromisoformat(receipt["expires_at"]) <= now
        ):
            raise FinanceDisclosureError("finance_disclosure_receipt_expired")
    known = {receipt["result_sha256"] for receipt in receipts}
    for ref in refs:
        if ref in known:
            continue
        retained_receipt = await _read(
            runtime,
            session,
            f"finance-disclosure-result:{session_hash}:{expected}:{ref}",
        )
        if retained_receipt is None or retained_receipt["authority_sha256"] != expected:
            raise FinanceDisclosureError(
                "finance_disclosure_retained_result_unavailable"
            )
        receipts.append(retained_receipt)
    try:
        scope = read_scope(runtime, session, advisor=True)
        await refresh_read_scope(runtime, scope, advisor=True)
        access = scope.policy
    except FinanceAccessError:
        raise FinanceDisclosureError("finance_disclosure_authority_changed") from None
    marker = await _read(runtime, session, _marker_key(session_hash))
    if marker is None:
        raise FinanceDisclosureError("finance_disclosure_provenance_invalid")
    binding_hash = _digest(access.binding.model_dump()) if access.binding else None
    if any(receipt.get("book_binding_sha256") != binding_hash for receipt in receipts):
        raise FinanceDisclosureError("finance_disclosure_book_changed")
    # No awaited work may follow dependency checks before final authorization.
    for receipt in receipts:
        if receipt.get("book_id") and receipt.get("publication_id"):
            head = (
                runtime.projections.connection("money")
                .execute(
                    "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
                    (session.tenant_id, scope.data_owner_id, receipt["book_id"]),
                )
                .fetchone()
            )
            if head is None or head[0] != receipt.get("head_publication_id"):
                raise FinanceDisclosureError("finance_disclosure_publication_changed")
        result = json.loads(receipt["result_json"])
        if str(result.get("result_kind", "")).startswith("planning_"):
            from adminme.finance.planning_store import projection_head

            if (
                projection_head(
                    runtime.projections.connection("money"),
                    scope,
                    result["publication"]["book_id"],
                )
                != result["projection"]["head_id"]
            ):
                raise FinanceDisclosureError("finance_disclosure_projection_changed")
        if result.get("result_kind") == "owner_choices":
            from adminme.finance.choices import choice_head

            current_head = choice_head(
                runtime.projections.connection("money"),
                session,
                result["publication"]["book_id"],
            )
            if current_head != result.get("choice_head_event_id"):
                raise FinanceDisclosureError("finance_disclosure_owner_choice_changed")
        if receipt.get("calculator_id"):
            from adminme.finance.calculators import calculator_metadata

            model = calculator_metadata().get(receipt["calculator_id"])
            if model is None or model["version"] != receipt.get("model_version"):
                raise FinanceDisclosureError("finance_disclosure_model_changed")
    rendered = answer_text
    if answer_text is not None:
        from adminme.finance.answers import FinanceAnswerError, verify_answer

        try:
            rendered = verify_answer(answer_text, receipts)
        except FinanceAnswerError as exc:
            raise FinanceDisclosureError(str(exc)) from None
    # The awaited reads above are not themselves a permission guarantee.
    validate_current_authority(runtime, session, expected)
    return rendered
