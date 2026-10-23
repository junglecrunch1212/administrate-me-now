"""Authorized facts for the CoS working environment, never disclosure grants.

Keep ownership and each source's authority attached when information crosses
an internal tool boundary. Only policy-permitted bounded plaintext from the
service-owned Gmail mailbox enters this surface; arbitrary payloads, HTML and
attachments stay out.
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
from adminme.service_identities import COS_PARTY_ID, ServiceRequestContext
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
LIST_FIELDS = frozenset(
    {
        "provider",
        "provider_account_id",
        "workspace",
        "list_external_id",
        "list_name",
        "is_default",
        "provider_revision",
        "changed_at",
    }
)
LIST_ITEM_FIELDS = frozenset(
    {
        "provider",
        "provider_account_id",
        "workspace",
        "list_external_id",
        "item_external_id",
        "list_name",
        "title",
        "priority",
        "completed",
        "completion_at",
        "start",
        "due",
        "parent_external_id",
        "provider_position",
        "provider_revision",
        "last_modified_at",
        "read_only_reason",
    }
)
DATE_FIELDS = frozenset(
    {
        "year",
        "month",
        "day",
        "hour",
        "minute",
        "second",
        "time_zone",
        "all_day",
        "floating",
    }
)
SELF_PLANNING_VERSIONS = {
    "calendar.event_added": frozenset({("google_calendar", 3)}),
    "calendar.event_updated": frozenset({("google_calendar", 3)}),
    "list.created": frozenset({("google_tasks", 2), ("apple_reminders", 2)}),
    "list.updated": frozenset({("google_tasks", 2), ("apple_reminders", 2)}),
    "list_item.added": frozenset({("google_tasks", 4), ("apple_reminders", 4)}),
    "list_item.updated": frozenset({("google_tasks", 4), ("apple_reminders", 4)}),
    "list_item.completed": frozenset({("google_tasks", 4), ("apple_reminders", 4)}),
    "list_item.uncompleted": frozenset({("google_tasks", 4), ("apple_reminders", 4)}),
}
SELF_PLANNING_METADATA = frozenset(
    {
        "provider",
        "provider_account_id",
        "workspace",
        "calendar_external_id",
        "external_event_id",
        "list_external_id",
        "item_external_id",
        "provider_revision",
        "start",
        "end",
        "start_time_zone",
        "end_time_zone",
        "all_day",
        "status",
        "availability",
        "completed",
        "completion_at",
        "due",
        "changed_at",
        "last_modified_at",
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
    "list.created": LIST_FIELDS,
    "list.updated": LIST_FIELDS,
    "list_item.added": LIST_ITEM_FIELDS,
    "list_item.updated": LIST_ITEM_FIELDS,
    "list_item.completed": LIST_ITEM_FIELDS,
    "list_item.uncompleted": LIST_ITEM_FIELDS,
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


READ_RANK = {"metadata_only": 0, "typed_facts": 1, "typed_facts_safe_snippet": 2}
SELF_MAIL_METADATA = frozenset(
    {
        "provider",
        "provider_account_id",
        "workspace",
        "native_message_id",
        "thread_id",
        "source_channel",
        "received_at",
        "sent_at",
        "message_state",
        "delivery_status",
    }
)


def _self_mail(event: dict[str, Any], session: ServiceSession) -> bool:
    """Ownership is the acquired envelope, never a member route or browser label."""
    payload = event["payload"]
    return (
        event["tenant_id"] == session.tenant_id
        and event["type"] in {"messaging.received", "messaging.sent"}
        and event["schema_version"] >= 6
        and event["owner_scope"] == session.private_scope
        and event["visibility_scope"] == session.private_scope
        and payload.get("service_principal_id") == session.principal.principal_id
        and payload.get("member_id") is None
        and payload.get("provider") == "gmail"
        and payload.get("workspace") == "service"
        and payload.get("provider_account_id") == event["source_account_id"]
    )


def _self_planning(event: dict[str, Any], session: ServiceSession) -> bool:
    p = event["payload"]
    return (
        (p.get("provider"), event["schema_version"])
        in SELF_PLANNING_VERSIONS.get(event["type"], frozenset())
        and (
            p.get("provider") != "apple_reminders"
            or event["source_adapter"] == "reminders-cos-apple-eventkit"
        )
        and event["tenant_id"] == session.tenant_id
        and event["owner_scope"] == session.private_scope
        and event["visibility_scope"] == session.private_scope
        and p.get("service_principal_id") == session.principal.principal_id
        and p.get("member_id") is None
        and p.get("workspace") == "service"
        and p.get("provider_account_id") == event["source_account_id"]
    )


def _source_authority(
    runtime: Any,
    session: ServiceSession,
    event: dict[str, Any],
    contexts: list[ServiceRequestContext],
) -> dict[str, Any] | None:
    context = _context_for(event, session, contexts)
    own_source = _self_mail(event, session) or _self_planning(event, session)
    if context is None and not own_source:
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
    if own_source:
        mode = effective["policy"]["context_mode"]
        if (
            mode not in READ_RANK
            or coordinate.owner_principal_id != session.principal.principal_id
        ):
            return None
        if _self_planning(event, session) and mode == "typed_facts_safe_snippet":
            # This slice exposes typed planning facts, never descriptions/notes.
            mode = "typed_facts"
        return {
            "source_event_id": event["event_id"],
            "authority_kind": "service_self",
            "authority_grant_id": None,
            "context_scope": "service_private",
            "context_member_id": None,
            "owner_party_id": COS_PARTY_ID,
            "source_owner_scope": event["owner_scope"],
            "source_visibility_scope": event["visibility_scope"],
            "source_sensitivity": event["sensitivity"],
            "source_adapter": event["source_adapter"],
            "source_account_id": event["source_account_id"],
            "source_policy_key": coordinate.key,
            "source_policy_owner_id": coordinate.owner_principal_id,
            "source_audience": [],
            "read_mode": mode,
            "restriction": "internal_processing_only",
        }
    assert context is not None
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
    payload = event["payload"]
    if kind.startswith("messaging.") and payload.get("service_principal_id"):
        # The existing native-message unique index selects the exact current
        # snapshot without scanning unrelated mailbox bodies or member rows.
        return (
            runtime.projections.connection("interactions")
            .execute(
                "SELECT 1 FROM service_interactions WHERE tenant_id=? "
                "AND service_principal_id=? AND provider=? AND provider_account_id=? "
                "AND native_message_id=? AND owner_scope=? AND visibility_scope=? "
                "AND last_event_id=?",
                (
                    event["tenant_id"],
                    payload["service_principal_id"],
                    payload.get("provider"),
                    payload.get("provider_account_id"),
                    payload.get("native_message_id"),
                    event["owner_scope"],
                    event["visibility_scope"],
                    event["event_id"],
                ),
            )
            .fetchone()
            is not None
        )
    if payload.get("service_principal_id") and kind in SELF_PLANNING_VERSIONS:
        coordinates = (
            event["tenant_id"],
            payload["service_principal_id"],
            payload["provider"],
            payload["provider_account_id"],
        )
        if kind.startswith("calendar."):
            return (
                runtime.projections.connection("calendars")
                .execute(
                    "SELECT 1 FROM service_calendar_current WHERE tenant_id=? AND service_principal_id=? "
                    "AND provider=? AND provider_account_id=? AND calendar_external_id=? "
                    "AND external_event_id=? AND last_event_id=? AND deleted=0",
                    (
                        *coordinates,
                        payload["calendar_external_id"],
                        payload["external_event_id"],
                        event["event_id"],
                    ),
                )
                .fetchone()
                is not None
            )
        if kind.startswith("list."):
            return (
                runtime.projections.connection("lists")
                .execute(
                    "SELECT 1 FROM service_lists_current WHERE tenant_id=? AND service_principal_id=? "
                    "AND provider=? AND provider_account_id=? AND list_external_id=? AND last_event_id=? AND deleted=0",
                    (*coordinates, payload["list_external_id"], event["event_id"]),
                )
                .fetchone()
                is not None
            )
        return (
            runtime.projections.connection("lists")
            .execute(
                "SELECT 1 FROM service_list_items_current i WHERE tenant_id=? AND service_principal_id=? "
                "AND provider=? AND provider_account_id=? AND list_external_id=? AND item_external_id=? "
                "AND last_event_id=? AND deleted=0 AND NOT EXISTS (SELECT 1 FROM service_lists_current l "
                "WHERE l.tenant_id=i.tenant_id AND l.service_principal_id=i.service_principal_id "
                "AND l.provider=i.provider AND l.provider_account_id=i.provider_account_id "
                "AND l.list_external_id=i.list_external_id "
                "AND (l.deleted=1 OR l.last_deleted_event_id>=i.last_event_id))",
                (
                    *coordinates,
                    payload["list_external_id"],
                    payload["item_external_id"],
                    event["event_id"],
                ),
            )
            .fetchone()
            is not None
        )
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
        elif key in {"start", "due"} and isinstance(value, dict):
            fact[key], shortened = _bounded_fact(value, DATE_FIELDS)
            truncated |= shortened
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
    if event["type"].startswith(("list.", "list_item.")) and not _self_planning(
        event, session
    ):
        return None
    if fields is None or (
        event["type"] == "task.created" and event["schema_version"] >= 4
    ):
        return None
    authority = _source_authority(runtime, session, event, contexts)
    if authority is None or not _current_source(runtime, event):
        return None
    own_mail = _self_mail(event, session)
    if own_mail and authority["read_mode"] == "metadata_only":
        fields = SELF_MAIL_METADATA
    elif _self_planning(event, session) and authority["read_mode"] == "metadata_only":
        fields = fields & SELF_PLANNING_METADATA
    fact, truncated = _bounded_fact(event["payload"], fields)
    body_included = False
    if own_mail and authority["read_mode"] == "typed_facts_safe_snippet":
        text = event["payload"].get("body_text")
        if isinstance(text, str) and text:
            # Plaintext only, control characters removed; no HTML conversion,
            # attachment fetch, or claim that the bounded excerpt is complete.
            text = "".join(c for c in text if c.isprintable() or c in "\n\t")
            fact["body_snippet"] = text[:2000]
            fact["body_snippet_truncated"] = len(text) > 2000
            truncated |= len(text) > 2000
            body_included = True
    return {
        "event_id": event["event_id"],
        "event_type": event["type"],
        "occurred_at": event["occurred_at"],
        "authority": authority,
        "fact": fact,
        "fact_truncated": truncated,
        "provider_body_included": body_included,
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
        or (
            _context_for(event, session, contexts) is None
            and not _self_mail(event, session)
            and not _self_planning(event, session)
        )
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
        if authority is not None and source.get("authority_kind") == "service_self":
            if (
                READ_RANK.get(authority["read_mode"], -1)
                < READ_RANK[source["read_mode"]]
            ):
                return False
            authority = {**authority, "read_mode": source["read_mode"]}
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


async def retained_sources(
    runtime: Any,
    session: ServiceSession,
    session_digest: str,
    explicit_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """A model cannot omit a consumed source or lower its recorded read mode."""
    sources = {source["source_event_id"]: source for source in explicit_sources}
    rows = (
        runtime.projections.connection("tasks")
        .execute(
            "SELECT authority_json FROM cos_source_read_receipts WHERE tenant_id=? "
            "AND service_principal_id=? AND session_digest=? LIMIT 21",
            (session.tenant_id, session.principal.principal_id, session_digest),
        )
        .fetchall()
    )
    for row in rows:
        source = json.loads(row["authority_json"])
        current = sources.get(source["source_event_id"])
        # Keep the consumed grant/audience even when an explicit reference now
        # resolves differently. Only the processing floor may become stricter.
        if current is not None and source.get("authority_kind") == "service_self":
            source["read_mode"] = max(
                source["read_mode"],
                current.get("read_mode", "typed_facts"),
                key=READ_RANK.__getitem__,
            )
        sources[source["source_event_id"]] = source
    if len(sources) > 20:
        raise HTTPException(
            409,
            "private note exceeds 20 consumed sources; start a fresh narrowly scoped run",
        )
    result = list(sources.values())
    if not await work_authorized(runtime, session, {"source_authorities": result}):
        raise HTTPException(404, "consumed source authority is no longer available")
    return result
