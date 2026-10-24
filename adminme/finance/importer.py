"""Disabled-by-default accepted-publication ingress through the durable event bus.

No provider acquisition, recovery resumption, credential lookup or external write.
The preceding publication identifies the single successor slot in the event log;
idempotency enforces compare-and-swap even across an ambiguous timeout/restart.
"""

from __future__ import annotations
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from adminme.events.envelope import EventEnvelope
from adminme.finance.publication import (
    PublicationError,
    canonical_bytes,
    validate_publication,
    validate_transition,
)
from adminme.lib.session import Session
from adminme.finance.publication_provenance import (
    ClosedPackage,
    freeze_package,
    namespace_root,
    verify_package,
)
from adminme.finance.provenance_store import archive_package, read_archived_proof


def _key(p: dict[str, Any]) -> str:
    chain = [p[k] for k in ("tenant_id", "owner_id", "book_id", "prior_publication_id")]
    return "finance-publication:" + hashlib.sha256(canonical_bytes(chain)).hexdigest()


async def _settled(runtime: Any) -> None:
    for _ in range(100):
        state = await runtime.bus.subscriber_status("projection:money")
        if state["lag_count"] == 0:
            return
        await asyncio.sleep(0.02)
    raise PublicationError("publication_projection_pending")


def require_import_owner(runtime: Any, session: Session) -> None:
    from adminme.finance.access_control import books_enabled

    if not books_enabled(runtime, session):
        raise PublicationError("publication_ingress_disabled")
    if (
        session.auth_role != "principal"
        or session.is_view_as
        or session.service_context is not None
        or runtime.settings.instance.tenant_id != session.tenant_id
        or runtime.settings.member_roles.get(session.auth_member_id) != "principal"
    ):
        raise PublicationError("publication_owner_authority_required")


async def import_publication(
    runtime: Any,
    session: Session,
    value: dict[str, Any],
    *,
    provenance_package: ClosedPackage | None = None,
) -> dict[str, Any]:
    """Explicit owner/file seam; no network resolver or public write endpoint."""
    require_import_owner(runtime, session)
    if provenance_package is not None:
        provenance_package = freeze_package(provenance_package)
    lock = getattr(runtime, "_finance_publication_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        runtime._finance_publication_lock = lock
    if lock.locked():
        raise PublicationError("publication_ingress_busy")
    async with lock:
        # Only one bounded validation may enter the shared executor at a time.
        p = await asyncio.to_thread(validate_publication, value)
        if (
            p["tenant_id"] != session.tenant_id
            or p["owner_id"] != session.auth_member_id
        ):
            raise PublicationError("publication_owner_authority_required")
        if datetime.fromisoformat(p["accepted_at"]) > datetime.now(
            timezone.utc
        ) + timedelta(minutes=5):
            raise PublicationError("publication_future_clock")
        from adminme.finance.access_control import require_publication_binding

        require_publication_binding(runtime, session, p)
        event_key = _key(p)
        existing = await runtime.event_log.get_by_idempotency(
            session.tenant_id, event_key
        )
        if existing:
            old = existing["payload"]["publication"]
            if (
                old["publication_id"] != p["publication_id"]
                or old["content_sha256"] != p["content_sha256"]
            ):
                raise PublicationError("publication_identity_or_prior_conflict")
            await _settled(runtime)
            if namespace_root(p):
                await read_archived_proof(runtime, p)
            require_import_owner(runtime, session)
            return {
                "state": "accepted",
                "event_id": existing["event_id"],
                "publication_id": p["publication_id"],
                "idempotent": True,
                "provider_effect_count": 0,
            }
        await _settled(runtime)
        conn = runtime.projections.connection("money")
        coordinates = (session.tenant_id, session.auth_member_id, p["book_id"])
        old = conn.execute(
            "SELECT content_sha256 FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
            (*coordinates, p["publication_id"]),
        ).fetchone()
        if old:
            raise PublicationError("publication_identity_or_prior_conflict")
        head = conn.execute(
            "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
            coordinates,
        ).fetchone()
        if (head[0] if head else None) != p["prior_publication_id"]:
            raise PublicationError("publication_prior_conflict")
        previous = conn.execute(
            "SELECT publication_json FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
            (*coordinates, p["prior_publication_id"]),
        ).fetchone()
        prior_publication = json.loads(previous[0]) if previous else None
        validate_transition(prior_publication, p)
        if namespace_root(p):
            if provenance_package is None:
                raise PublicationError("provenance_closed_package_required")
            prior_proof = (
                await read_archived_proof(runtime, prior_publication)
                if prior_publication
                else None
            )
            await asyncio.to_thread(verify_package, p, provenance_package, prior_proof)
            require_import_owner(runtime, session)
            await archive_package(runtime, session, p, provenance_package)
            await _settled(runtime)
        elif provenance_package is not None:
            raise PublicationError("provenance_unexpected_package")
        require_import_owner(runtime, session)
        now = datetime.now(timezone.utc)
        scope = f"private:{session.auth_member_id}"
        envelope = EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=session.tenant_id,
            type="finance.publication_accepted",
            schema_version=1,
            occurred_at=now.isoformat(),
            source_adapter="accepted_books",
            source_account_id=p["book_id"],
            owner_scope=scope,
            visibility_scope=scope,
            sensitivity="privileged",
            actor_identity=session.event_actor_identity,
            idempotency_key=event_key,
            payload={"publication": p},
        )
        event_id = await runtime.events.write(envelope)
        await _settled(runtime)
        require_import_owner(runtime, session)
        return {
            "state": "accepted",
            "event_id": event_id,
            "publication_id": p["publication_id"],
            "idempotent": False,
            "provider_effect_count": 0,
        }
