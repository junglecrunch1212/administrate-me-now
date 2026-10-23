"""Standing-grant facts for the CoS working environment, never disclosure grants.

Keep ownership and each source's authority attached when information crosses
an internal tool boundary. Provider bodies, credentials and arbitrary event
payloads do not enter this bounded canonical-fact surface.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from adminme.lib.principal import ServiceSession
from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    resolve_service_delegation,
)
from adminme.service_identities import ServiceRequestContext
from adminme.source_policy import source_coordinate_for_event

FACT_FIELDS = {
    "task.created": frozenset(
        {
            "task_id",
            "title",
            "description",
            "due",
            "due_date",
            "available_from",
            "domain",
            "item_type",
            "owner_member_id",
            "owner_party_id",
            "source_system",
        }
    ),
    "commitment.confirmed": frozenset(
        {"commitment_id", "confirmed_by_member_id", "confirmed_at", "note"}
    ),
}


def active_contexts(
    runtime: Any, session: ServiceSession
) -> list[ServiceRequestContext]:
    """Resolve current standing grants without synthesizing a member session."""
    conn = runtime.projections.connection("parties")
    coordinates = conn.execute(
        "SELECT DISTINCT context_scope, context_member_id, owner_party_id "
        "FROM service_delegation_grants WHERE tenant_id=? AND service_principal_id=? "
        "AND revoked_at IS NULL ORDER BY context_scope, context_member_id, owner_party_id",
        (session.tenant_id, session.principal.principal_id),
    ).fetchall()
    contexts = []
    for coordinate in coordinates:
        try:
            context = resolve_service_delegation(
                conn,
                tenant_id=session.tenant_id,
                service_principal_id=session.principal.principal_id,
                operator_principal_id=session.operator_principal_id,
                context_scope=coordinate["context_scope"],
                context_member_id=coordinate["context_member_id"],
                owner_party_id=coordinate["owner_party_id"],
            )
        except ServiceDelegationResolutionError:
            continue
        if "read_context" in context.permissions:
            contexts.append(context)
    return contexts


def _context_for(
    event: dict[str, Any],
    session: ServiceSession,
    contexts: list[ServiceRequestContext],
) -> ServiceRequestContext | None:
    if event["tenant_id"] != session.tenant_id:
        return None
    matches = [
        context
        for context in contexts
        if event["owner_scope"] in context.allowed_scopes
        and event["visibility_scope"] in context.allowed_scopes
    ]
    return matches[0] if len(matches) == 1 else None


def _policy_allows(runtime: Any, owner_id: str, policy_key: str) -> bool:
    state = runtime.settings_control.read()
    record = (state.get("source_policies_v2") or {}).get(owner_id, {}).get(policy_key)
    return not record or (record.get("policy") or {}).get("context_mode") not in {
        "none",
        "metadata_only",
    }


def fact_for(
    runtime: Any,
    session: ServiceSession,
    event: dict[str, Any],
    contexts: list[ServiceRequestContext],
) -> dict[str, Any] | None:
    fields = FACT_FIELDS.get(event["type"])
    # Service-derived records must retain their complete source chain through
    # private-state reads; they cannot be reintroduced as unqualified originals.
    if fields is None or (
        event["type"] == "task.created" and event["schema_version"] >= 4
    ):
        return None
    context = _context_for(event, session, contexts)
    coordinate = source_coordinate_for_event(
        event, provider_bindings=runtime.settings.provider_bindings
    )
    if context is None or not _policy_allows(
        runtime, coordinate.owner_principal_id, coordinate.key
    ):
        return None
    authority = {
        "source_event_id": event["event_id"],
        "authority_grant_id": context.authority_grant_id,
        "context_scope": context.context_scope,
        "context_member_id": context.context_member_id,
        "owner_party_id": context.owner_party_id,
        "source_owner_scope": event["owner_scope"],
        "source_visibility_scope": event["visibility_scope"],
        "source_sensitivity": event["sensitivity"],
        "source_adapter": event["source_adapter"],
        "source_account_id": event["source_account_id"],
        "source_policy_key": coordinate.key,
        "source_policy_owner_id": coordinate.owner_principal_id,
        "source_audience": list(context.audience),
        "restriction": "internal_processing_only",
    }
    return {
        "event_id": event["event_id"],
        "event_type": event["type"],
        "occurred_at": event["occurred_at"],
        "authority": authority,
        "fact": {
            key: value for key, value in event["payload"].items() if key in fields
        },
        "outside_disclosure_authorized": False,
    }


async def source_facts(
    runtime: Any, session: ServiceSession, event_ids: list[str]
) -> list[dict[str, Any]]:
    contexts = active_contexts(runtime, session)
    facts = []
    for event_id in event_ids:
        event = await runtime.event_log.get(event_id)
        fact = fact_for(runtime, session, event, contexts) if event else None
        if fact is None:
            raise HTTPException(404, "authorized canonical source fact not found")
        facts.append(fact)
    return facts


def work_authorized(
    runtime: Any, session: ServiceSession, work: dict[str, Any]
) -> bool:
    """Revocation stops use of derived records without erasing retained history."""
    sources = work.get("source_authorities")
    if sources is None:
        sources = json.loads(work.get("source_authorities_json") or "[]")
    contexts = active_contexts(runtime, session) if sources else []
    for source in sources:
        matches = [
            context
            for context in contexts
            if context.authority_grant_id == source["authority_grant_id"]
            and source["source_owner_scope"] in context.allowed_scopes
            and source["source_visibility_scope"] in context.allowed_scopes
            and tuple(source["source_audience"]) == context.audience
        ]
        if len(matches) != 1 or not _policy_allows(
            runtime, source["source_policy_owner_id"], source["source_policy_key"]
        ):
            return False
    return True


def work_view(
    runtime: Any, session: ServiceSession, work: dict[str, Any]
) -> dict[str, Any]:
    if not work_authorized(runtime, session, work):
        raise HTTPException(404, "CoS work authority is no longer available")
    work = dict(work)
    work["source_authorities"] = json.loads(work.pop("source_authorities_json", "[]"))
    work["outside_disclosure_authorized"] = False
    return work
