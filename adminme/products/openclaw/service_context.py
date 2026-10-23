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

CALENDAR_FIELDS = frozenset(
    {
        "member_id",
        "provider",
        "provider_account_id",
        "calendar_external_id",
        "calendar_name",
        "external_event_id",
        "provider_revision",
        "status",
        "summary",
        "location",
        "start",
        "end",
        "start_time_zone",
        "end_time_zone",
        "all_day",
        "availability",
        "visibility",
    }
)
NOTE_FIELDS = frozenset(
    {
        "knowledge_id",
        "member_id",
        "title",
        "source_kind",
        "source_type",
        "source_external_id",
        "account_external_id",
        "folder_external_id",
        "captured_at",
        "provider_modified_at",
        "provider_revision",
        "locked",
    }
)
MESSAGE_FIELDS = frozenset(
    {
        "member_id",
        "provider",
        "provider_account_id",
        "workspace",
        "native_message_id",
        "thread_id",
        "source_channel",
        "from_identifier",
        "to_identifier",
        "subject",
        "received_at",
        "sent_at",
        "message_state",
        "delivery_status",
        "native_revision",
    }
)
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
    "calendar.event_added": CALENDAR_FIELDS,
    "calendar.event_updated": CALENDAR_FIELDS,
    "contact.record.observed": frozenset(
        {
            "contact_record_id",
            "member_id",
            "provider",
            "provider_account_id",
            "container_external_id",
            "external_id",
            "provider_revision",
            "kind",
            "display_name",
            "given_name",
            "family_name",
            "nickname",
            "organization_name",
            "job_title",
            "identifiers",
            "observed_at",
        }
    ),
    "member_knowledge.note_added": NOTE_FIELDS,
    "member_knowledge.note_updated": NOTE_FIELDS,
    "member_knowledge.note_corrected": NOTE_FIELDS,
    "messaging.received": MESSAGE_FIELDS,
    "messaging.sent": MESSAGE_FIELDS,
    "artifact.received": frozenset(
        {
            "source",
            "external_artifact_id",
            "mime_type",
            "size_bytes",
            "filename",
            "received_at",
        }
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


def _source_authority(
    runtime: Any,
    session: ServiceSession,
    event: dict[str, Any],
    contexts: list[ServiceRequestContext],
) -> dict[str, Any] | None:
    context = _context_for(event, session, contexts)
    if context is None:
        return None
    try:
        coordinate = source_coordinate_for_event(
            event, provider_bindings=runtime.settings.provider_bindings
        )
    except ValueError:
        return None
    effective = runtime.settings_control.effective_source_policy_v2(
        coordinate.owner_principal_id, coordinate
    )
    # This standing-context tool has no trusted explicit-demand receipt. Even
    # the more permissive snippet mode returns only the typed fields below.
    # Do not turn a model-supplied flag into permission to read provider bodies.
    if effective["policy"]["context_mode"] not in {
        "typed_facts",
        "typed_facts_safe_snippet",
    }:
        return None
    return {
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


def _current_source(runtime: Any, event: dict[str, Any]) -> bool:
    """Select current observations without treating projections as new facts.

    The returned payload still comes from the original encrypted event. A
    missing/lagging row is withheld; updates, deletions and retractions cannot
    resurrect an old source snapshot through an explicit event reference.
    """
    kind = event["type"]
    if kind.startswith("calendar."):
        if event["schema_version"] < 2:
            # v1 updates are patches rather than complete calendar snapshots.
            return False
        projection, table, predicate = "calendars", "calendar_events", "last_event_id=?"
    elif kind == "contact.record.observed":
        projection, table, predicate = (
            "parties",
            "contact_records",
            "last_event_id=? AND deleted_at IS NULL",
        )
    elif kind.startswith("member_knowledge."):
        projection, table, predicate = (
            "member_knowledge",
            "member_knowledge",
            "last_event_id=? AND locked=0",
        )
    elif kind.startswith("messaging."):
        # Classification and local read/unread changes also advance last_event_id.
        # The last accepted source revision remains in the native provenance list.
        projection, table, predicate = (
            "interactions",
            "interactions",
            "json_extract(raw_event_ids, '$[#-1]')=? AND message_state<>'retracted'",
        )
    else:
        return True
    return (
        runtime.projections.connection(projection)
        .execute(
            f"SELECT 1 FROM {table} WHERE tenant_id=? AND owner_scope=? "
            f"AND visibility_scope=? AND {predicate} LIMIT 1",
            (
                event["tenant_id"],
                event["owner_scope"],
                event["visibility_scope"],
                event["event_id"],
            ),
        )
        .fetchone()
        is not None
    )


def _bounded_fact(
    payload: dict[str, Any], fields: frozenset[str]
) -> tuple[dict[str, Any], bool]:
    fact: dict[str, Any] = {}
    truncated = False
    for key in sorted(fields & payload.keys()):
        value = payload[key]
        if isinstance(value, str):
            fact[key] = value[:2000]
            truncated |= len(value) > 2000
        elif value is None or isinstance(value, (bool, int, float)):
            fact[key] = value
        elif key == "identifiers" and isinstance(value, list):
            identifiers = []
            for identifier in value[:20]:
                selected, shortened = _bounded_fact(
                    identifier,
                    frozenset({"kind", "value", "label", "primary", "verified"}),
                )
                identifiers.append(selected)
                truncated |= shortened
            fact[key] = identifiers
            truncated |= len(value) > 20
    return fact, truncated


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
    authority = _source_authority(runtime, session, event, contexts)
    if authority is None or not _current_source(runtime, event):
        return None
    fact, truncated = _bounded_fact(event["payload"], fields)
    return {
        "event_id": event["event_id"],
        "event_type": event["type"],
        "occurred_at": event["occurred_at"],
        "authority": authority,
        "fact": fact,
        "fact_truncated": truncated,
        "provider_body_included": False,
        "outside_disclosure_authorized": False,
    }


async def validate_context_cursor(
    runtime: Any,
    session: ServiceSession,
    contexts: list[ServiceRequestContext],
    event_id: str,
) -> None:
    # A cursor identifies a scan position, not content authority. It may name a
    # since-deleted observation or a policy-filtered source in an allowed scope.
    event = await runtime.event_log.get(event_id)
    if (
        event is None
        or event["type"] not in FACT_FIELDS
        or _context_for(event, session, contexts) is None
    ):
        raise HTTPException(404, "authorized context cursor not found")


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


async def work_authorized(
    runtime: Any, session: ServiceSession, work: dict[str, Any]
) -> bool:
    """Revocation stops use of derived records without erasing retained history."""
    sources = work.get("source_authorities")
    if sources is None:
        sources = json.loads(work.get("source_authorities_json") or "[]")
    contexts = active_contexts(runtime, session) if sources else []
    for source in sources:
        event = await runtime.event_log.get(source["source_event_id"])
        # Historical notes retain their original evidence even if a source was
        # updated. Current grants and inherited policy still govern every reuse.
        # This also supports typed internal intents that are not discovery facts.
        authority = (
            _source_authority(runtime, session, event, contexts) if event else None
        )
        if authority != source:
            return False
    return True


async def work_view(
    runtime: Any, session: ServiceSession, work: dict[str, Any]
) -> dict[str, Any]:
    if not await work_authorized(runtime, session, work):
        raise HTTPException(404, "CoS work authority is no longer available")
    work = dict(work)
    work["source_authorities"] = json.loads(work.pop("source_authorities_json", "[]"))
    work["outside_disclosure_authorized"] = False
    return work
