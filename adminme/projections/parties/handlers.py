"""
Parties projection handlers — CRM spine.

Per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3. Each handler
is idempotent: re-applying the same event must produce the same row state
(§2 invariant 4). The projection DB columns themselves, keyed by
``(tenant_id, <entity_id>)``, carry enough state to make that safe via
``INSERT ... ON CONFLICT DO UPDATE``.

Subscribed event types in this projection:
- ``party.created``       → parties row
- ``identifier.added``    → identifiers row (with merge-candidate guard)
- ``membership.added``    → memberships row
- ``relationship.added``  → relationships row

``party.merged`` is registered as a schema in ``adminme/events/schemas/crm.py``
for prompt 10b but is NOT handled here — merge logic lands in prompt 10b.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    """Dispatch an envelope to the right handler. Called by
    ``PartiesProjection.apply()`` inside the runner's transaction."""
    event_type = envelope["type"]
    dispatch = {
        "party.created": on_party_created,
        "party.updated": on_party_updated,
        "party.qualification_set": on_party_qualification_set,
        "party.archived": on_party_archived,
        "party.restored": on_party_restored,
        "party.merged": on_party_merged,
        "party.split": on_party_split,
        "identifier.added": on_identifier_added,
        "identifier.revoked": on_identifier_revoked,
        "membership.added": on_membership_added,
        "relationship.added": on_relationship_added,
        "relationship.updated": on_relationship_updated,
        "relationship.ended": on_relationship_ended,
        "contact.record.observed": on_contact_record_observed,
        "contact.record.deleted": on_contact_record_deleted,
        "contact.group.observed": on_contact_group_observed,
        "endpoint.observed": on_endpoint_observed,
        "member_relationship.profile_set": on_member_relationship_profile_set,
        "relationship_fact.confirmed": on_relationship_fact_confirmed,
        "relationship_fact.revoked": on_relationship_fact_revoked,
        "party.access_granted": on_party_access_granted,
        "party.access_revoked": on_party_access_revoked,
        "service.delegation.requested": on_service_delegation_requested,
        "service.delegation.approved": on_service_delegation_approved,
        "service.delegation.revoked": on_service_delegation_revoked,
        "operating_agreement.constitution_registered": on_constitution_registered,
        "operating_agreement.proposed": on_operating_agreement_proposed,
        "operating_agreement.approved": on_operating_agreement_approved,
        "operating_agreement.rejected": on_operating_agreement_rejected,
        "operating_agreement.suspended": on_operating_agreement_suspended,
        "operating_agreement.revoked": on_operating_agreement_revoked,
        "operating_agreement.exception_consumed": on_operating_agreement_exception_consumed,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        # Defensive — subscribes_to should prevent this, but if the runner
        # ever delivers an unexpected type, ignore rather than raise.
        return
    handler(envelope, conn)


def on_party_created(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    qualification = (
        "unconfirmed_endpoint"
        if envelope.get("source_adapter") == "identity_resolution"
        else "qualified"
    )
    conn.execute(
        """
        INSERT INTO parties (
            party_id, tenant_id, kind, display_name, sort_name,
            nickname, pronouns, notes, attributes_json,
            owner_scope, visibility_scope, sensitivity,
            qualification, source_kind, updated_at_ms,
            created_at_ms, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, party_id) DO UPDATE SET
            kind             = excluded.kind,
            display_name     = excluded.display_name,
            sort_name        = excluded.sort_name,
            nickname         = excluded.nickname,
            pronouns         = excluded.pronouns,
            notes            = excluded.notes,
            attributes_json  = excluded.attributes_json,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            qualification    = CASE
                WHEN parties.qualification = 'archived' THEN parties.qualification
                ELSE excluded.qualification
            END,
            source_kind      = excluded.source_kind,
            updated_at_ms    = excluded.updated_at_ms,
            last_event_id    = excluded.last_event_id
        """,
        (
            p["party_id"],
            envelope["tenant_id"],
            p["kind"],
            p["display_name"],
            p["sort_name"],
            p.get("nickname"),
            p.get("pronouns"),
            p.get("notes"),
            json.dumps(p.get("attributes") or {}, separators=(",", ":")),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            qualification,
            str(envelope.get("source_adapter") or "domain"),
            envelope["event_at_ms"],
            envelope["event_at_ms"],
            envelope["event_id"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], p["party_id"])


def on_identifier_added(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    tenant_id = envelope["tenant_id"]

    conn.execute(
        """
        INSERT INTO identifiers (
            identifier_id, tenant_id, party_id, kind, value,
            value_normalized, verified, primary_for_kind, last_event_id
            , label, source_kind, source_record_id,
            owner_scope, visibility_scope, sensitivity, revoked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(tenant_id, identifier_id) DO UPDATE SET
            party_id         = excluded.party_id,
            kind             = excluded.kind,
            value            = excluded.value,
            value_normalized = excluded.value_normalized,
            verified         = excluded.verified,
            primary_for_kind = excluded.primary_for_kind,
            label            = excluded.label,
            source_kind      = excluded.source_kind,
            source_record_id = excluded.source_record_id,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            revoked_at       = NULL,
            last_event_id    = excluded.last_event_id
        """,
        (
            p["identifier_id"],
            tenant_id,
            p["party_id"],
            p["kind"],
            p["value"],
            p["value_normalized"],
            1 if p.get("verified") else 0,
            1 if p.get("primary_for_kind") else 0,
            envelope["event_id"],
            p.get("label"),
            str(envelope.get("source_adapter") or "domain"),
            p.get("source_record_id"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
        ),
    )


def on_membership_added(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO memberships (
            membership_id, tenant_id, party_id, parent_party_id,
            role, started_at, attributes_json, last_event_id
            , owner_scope, visibility_scope, sensitivity, ended_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(tenant_id, membership_id) DO UPDATE SET
            party_id        = excluded.party_id,
            parent_party_id = excluded.parent_party_id,
            role            = excluded.role,
            started_at      = excluded.started_at,
            attributes_json = excluded.attributes_json,
            owner_scope     = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity     = excluded.sensitivity,
            ended_at        = NULL,
            last_event_id   = excluded.last_event_id
        """,
        (
            p["membership_id"],
            envelope["tenant_id"],
            p["party_id"],
            p["parent_party_id"],
            p["role"],
            p.get("started_at"),
            json.dumps(p.get("attributes") or {}, separators=(",", ":")),
            envelope["event_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
        ),
    )


def on_relationship_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO relationships (
            relationship_id, tenant_id, party_a, party_b,
            label, direction, since, attributes_json, last_event_id
            , owner_scope, visibility_scope, sensitivity, ended_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(tenant_id, relationship_id) DO UPDATE SET
            party_a         = excluded.party_a,
            party_b         = excluded.party_b,
            label           = excluded.label,
            direction       = excluded.direction,
            since           = excluded.since,
            attributes_json = excluded.attributes_json,
            owner_scope     = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity     = excluded.sensitivity,
            ended_at        = NULL,
            last_event_id   = excluded.last_event_id
        """,
        (
            p["relationship_id"],
            envelope["tenant_id"],
            p["party_a"],
            p["party_b"],
            p["label"],
            p["direction"],
            p.get("since"),
            json.dumps(p.get("attributes") or {}, separators=(",", ":")),
            envelope["event_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], p["party_a"])
    _refresh_fts(conn, envelope["tenant_id"], p["party_b"])


def _stable_id(prefix: str, *parts: str) -> str:
    value = "\x1f".join(parts)
    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:22]}"


def _refresh_fts(conn: sqlcipher3.Connection, tenant_id: str, party_id: str) -> None:
    conn.execute(
        "DELETE FROM people_fts WHERE tenant_id=? AND party_id=?",
        (tenant_id, party_id),
    )
    party = conn.execute(
        "SELECT * FROM parties WHERE tenant_id=? AND party_id=?",
        (tenant_id, party_id),
    ).fetchone()
    if party is None or party["qualification"] != "qualified":
        return
    profiles = conn.execute(
        "SELECT member_id, circle, tags_json FROM member_relationship_profiles "
        "WHERE tenant_id=? AND party_id=? AND suppressed=0",
        (tenant_id, party_id),
    ).fetchall()
    relationships = conn.execute(
        "SELECT label, owner_scope, sensitivity FROM relationships WHERE tenant_id=? "
        "AND (party_a=? OR party_b=?) AND ended_at IS NULL",
        (tenant_id, party_id, party_id),
    ).fetchall()
    facts = conn.execute(
        "SELECT member_id, kind, label, value FROM relationship_facts "
        "WHERE tenant_id=? AND party_id=? AND revoked_at IS NULL "
        "AND sensitivity!='privileged'",
        (tenant_id, party_id),
    ).fetchall()
    attrs = json.loads(party["attributes_json"] or "{}")
    audiences: dict[str, dict[str, list[str]]] = {}

    def audience(member_id: str) -> dict[str, list[str]]:
        return audiences.setdefault(member_id, {"organizations": [], "terms": []})

    owner_scope = str(party["owner_scope"])
    base_audience = (
        owner_scope.removeprefix("private:")
        if owner_scope.startswith("private:")
        else ""
    )
    audience(base_audience)["organizations"].append(
        str(attrs.get("organization_name") or "")
    )
    for profile in profiles:
        terms = audience(str(profile["member_id"]))["terms"]
        terms.append(str(profile["circle"] or ""))
        try:
            terms.extend(
                str(value) for value in json.loads(profile["tags_json"] or "[]")
            )
        except json.JSONDecodeError:
            pass
    for relationship in relationships:
        if relationship["sensitivity"] == "privileged":
            continue
        relationship_scope = str(relationship["owner_scope"])
        relationship_audience = (
            relationship_scope.removeprefix("private:")
            if relationship_scope.startswith("private:")
            else ""
        )
        audience(relationship_audience)["terms"].append(str(relationship["label"]))
    for fact in facts:
        fact_audience = audience(str(fact["member_id"]))
        if fact["kind"] in {"employer", "role"}:
            fact_audience["organizations"].append(str(fact["value"]))
        fact_audience["terms"].extend(
            [str(fact["kind"]), str(fact["label"] or ""), str(fact["value"])]
        )
    # A grant makes the Party's core identity searchable by the grantee. Its
    # private fact/identifier terms are checked against the active grant at
    # query time so expiry and revocation cannot leave a searchable sidecar.
    grants = conn.execute(
        "SELECT grantee_member_id FROM party_access_grants WHERE tenant_id=? "
        "AND party_id=? AND revoked_at IS NULL",
        (tenant_id, party_id),
    ).fetchall()
    for grant in grants:
        audience(str(grant["grantee_member_id"]))
    for member_id, values in audiences.items():
        conn.execute(
            "INSERT INTO people_fts "
            "(tenant_id, party_id, audience_member_id, display_name, sort_name, "
            "nickname, organization, relationship_terms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tenant_id,
                party_id,
                member_id,
                party["display_name"],
                party["sort_name"],
                party["nickname"] or "",
                " ".join(values["organizations"]),
                " ".join(values["terms"]),
            ),
        )


def on_party_updated(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    updates = payload["field_updates"]
    allowed = {
        "display_name",
        "sort_name",
        "nickname",
        "pronouns",
        "notes",
        "kind",
    }
    assignments: list[str] = []
    values: list[Any] = []
    for key in sorted(set(updates) & allowed):
        assignments.append(f"{key}=?")
        values.append(updates[key])
    if not assignments:
        return
    assignments.extend(["updated_at_ms=?", "last_event_id=?"])
    values.extend(
        [
            envelope["event_at_ms"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["party_id"],
        ]
    )
    conn.execute(
        f"UPDATE parties SET {', '.join(assignments)} WHERE tenant_id=? AND party_id=?",  # noqa: S608
        values,
    )
    _refresh_fts(conn, envelope["tenant_id"], payload["party_id"])


def on_party_qualification_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    conn.execute(
        "UPDATE parties SET qualification=?, archived_at=CASE WHEN ?='archived' "
        "THEN ? ELSE NULL END, updated_at_ms=?, last_event_id=? "
        "WHERE tenant_id=? AND party_id=?",
        (
            payload["qualification"],
            payload["qualification"],
            payload["changed_at"],
            envelope["event_at_ms"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["party_id"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], payload["party_id"])


def on_party_archived(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    conn.execute(
        "UPDATE parties SET qualification='archived', archived_at=?, updated_at_ms=?, "
        "last_event_id=? WHERE tenant_id=? AND party_id=?",
        (
            payload["archived_at"],
            envelope["event_at_ms"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["party_id"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], payload["party_id"])


def on_party_restored(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    conn.execute(
        "UPDATE parties SET qualification='qualified', archived_at=NULL, "
        "updated_at_ms=?, last_event_id=? WHERE tenant_id=? AND party_id=?",
        (
            envelope["event_at_ms"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["party_id"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], payload["party_id"])


def on_party_merged(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    survivor = payload["surviving_party_id"]
    merged = payload["merged_party_id"]
    conn.execute(
        "UPDATE parties SET qualification='archived', merged_into_party_id=?, "
        "archived_at=?, updated_at_ms=?, last_event_id=? "
        "WHERE tenant_id=? AND party_id=?",
        (
            survivor,
            payload["merged_at"],
            envelope["event_at_ms"],
            envelope["event_id"],
            envelope["tenant_id"],
            merged,
        ),
    )
    for table in ("contact_records", "identifiers", "relationship_facts"):
        conn.execute(
            f"UPDATE {table} SET party_id=? WHERE tenant_id=? AND party_id=?",  # noqa: S608
            (survivor, envelope["tenant_id"], merged),
        )
    profiles = conn.execute(
        "SELECT profile_id, member_id FROM member_relationship_profiles "
        "WHERE tenant_id=? AND party_id=?",
        (envelope["tenant_id"], merged),
    ).fetchall()
    for profile in profiles:
        collision = conn.execute(
            "SELECT 1 FROM member_relationship_profiles WHERE tenant_id=? "
            "AND member_id=? AND party_id=?",
            (envelope["tenant_id"], profile["member_id"], survivor),
        ).fetchone()
        if collision is None:
            conn.execute(
                "UPDATE member_relationship_profiles SET party_id=? "
                "WHERE tenant_id=? AND profile_id=?",
                (survivor, envelope["tenant_id"], profile["profile_id"]),
            )
        else:
            conn.execute(
                "DELETE FROM member_relationship_profiles WHERE tenant_id=? "
                "AND profile_id=?",
                (envelope["tenant_id"], profile["profile_id"]),
            )
    conn.execute(
        "UPDATE relationships SET party_a=CASE WHEN party_a=? THEN ? ELSE party_a END, "
        "party_b=CASE WHEN party_b=? THEN ? ELSE party_b END "
        "WHERE tenant_id=? AND (party_a=? OR party_b=?)",
        (merged, survivor, merged, survivor, envelope["tenant_id"], merged, merged),
    )
    _refresh_fts(conn, envelope["tenant_id"], merged)
    _refresh_fts(conn, envelope["tenant_id"], survivor)


def on_party_split(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    restored = payload["restored_party_id"]
    conn.execute(
        "UPDATE parties SET qualification='qualified', merged_into_party_id=NULL, "
        "archived_at=NULL, updated_at_ms=?, last_event_id=? "
        "WHERE tenant_id=? AND party_id=?",
        (
            envelope["event_at_ms"],
            envelope["event_id"],
            envelope["tenant_id"],
            restored,
        ),
    )
    allocation = {
        "identifiers": ("identifier_id", payload.get("identifier_ids") or []),
        "contact_records": (
            "contact_record_id",
            payload.get("contact_record_ids") or [],
        ),
        "relationship_facts": (
            "fact_id",
            payload.get("relationship_fact_ids") or [],
        ),
        "member_relationship_profiles": (
            "profile_id",
            payload.get("profile_ids") or [],
        ),
    }
    for table, (id_column, ids) in allocation.items():
        for value in ids:
            conn.execute(
                f"UPDATE {table} SET party_id=? WHERE tenant_id=? AND {id_column}=?",  # noqa: S608
                (restored, envelope["tenant_id"], value),
            )
    for relationship_id in payload.get("relationship_party_a_ids") or []:
        conn.execute(
            "UPDATE relationships SET party_a=? WHERE tenant_id=? AND relationship_id=?",
            (restored, envelope["tenant_id"], relationship_id),
        )
    for relationship_id in payload.get("relationship_party_b_ids") or []:
        conn.execute(
            "UPDATE relationships SET party_b=? WHERE tenant_id=? AND relationship_id=?",
            (restored, envelope["tenant_id"], relationship_id),
        )
    _refresh_fts(conn, envelope["tenant_id"], payload["merged_party_id"])
    _refresh_fts(conn, envelope["tenant_id"], restored)


def on_identifier_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    conn.execute(
        "UPDATE identifiers SET revoked_at=?, last_event_id=? "
        "WHERE tenant_id=? AND identifier_id=?",
        (
            payload["revoked_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["identifier_id"],
        ),
    )


def on_endpoint_observed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    if envelope["owner_scope"] != f"private:{payload['member_id']}":
        return
    conn.execute(
        """
        INSERT INTO people_endpoints (
            endpoint_id, tenant_id, member_id, kind, value, value_normalized,
            classification, provider, provider_account_id, source_interaction_id,
            observed_at, evidence_json, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, endpoint_id) DO UPDATE SET
            value=excluded.value, value_normalized=excluded.value_normalized,
            classification=excluded.classification, provider=excluded.provider,
            provider_account_id=excluded.provider_account_id,
            source_interaction_id=COALESCE(excluded.source_interaction_id, people_endpoints.source_interaction_id),
            observed_at=excluded.observed_at, evidence_json=excluded.evidence_json,
            owner_scope=excluded.owner_scope, visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            payload["endpoint_id"],
            envelope["tenant_id"],
            payload["member_id"],
            payload["kind"],
            payload["value"],
            payload["value_normalized"],
            payload["classification"],
            payload.get("provider"),
            payload.get("provider_account_id"),
            payload.get("source_interaction_id"),
            payload["observed_at"],
            json.dumps(payload.get("evidence") or []),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def on_contact_record_observed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope["owner_scope"] != f"private:{payload['member_id']}":
        return
    party_id = _stable_id(
        "party_contact",
        envelope["tenant_id"],
        payload["member_id"],
        payload["provider"],
        payload["provider_account_id"],
        payload["container_external_id"],
        payload["external_id"],
    )
    sort_name = payload.get("sort_name") or payload["display_name"].casefold()
    attributes = {
        "given_name": payload.get("given_name"),
        "family_name": payload.get("family_name"),
        "organization_name": payload.get("organization_name"),
        "job_title": payload.get("job_title"),
    }
    conn.execute(
        """
        INSERT INTO parties (
            party_id, tenant_id, kind, display_name, sort_name, nickname,
            pronouns, notes, attributes_json, owner_scope, visibility_scope,
            sensitivity, qualification, source_kind, updated_at_ms,
            created_at_ms, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, 'qualified',
                  'contact_record', ?, ?, ?)
        ON CONFLICT(tenant_id, party_id) DO UPDATE SET
            kind=excluded.kind, display_name=excluded.display_name,
            sort_name=excluded.sort_name, nickname=excluded.nickname,
            attributes_json=excluded.attributes_json,
            owner_scope=excluded.owner_scope, visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity,
            qualification=CASE WHEN parties.qualification='archived' THEN 'archived' ELSE 'qualified' END,
            updated_at_ms=excluded.updated_at_ms, last_event_id=excluded.last_event_id
        """,
        (
            party_id,
            envelope["tenant_id"],
            payload["kind"],
            payload["display_name"],
            sort_name,
            payload.get("nickname"),
            json.dumps(attributes, separators=(",", ":")),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_at_ms"],
            envelope["event_at_ms"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        """
        INSERT INTO contact_records (
            contact_record_id, tenant_id, party_id, member_id, provider,
            provider_account_id, container_external_id, external_id,
            provider_revision, kind, display_name, sort_name, given_name,
            family_name, nickname, organization_name, job_title, fields_json,
            groups_json, source_url, observed_at, capability_warnings_json,
            deleted_at, owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  NULL, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, contact_record_id) DO UPDATE SET
            party_id=excluded.party_id, provider_revision=excluded.provider_revision,
            kind=excluded.kind, display_name=excluded.display_name,
            sort_name=excluded.sort_name, given_name=excluded.given_name,
            family_name=excluded.family_name, nickname=excluded.nickname,
            organization_name=excluded.organization_name, job_title=excluded.job_title,
            fields_json=excluded.fields_json, groups_json=excluded.groups_json,
            source_url=excluded.source_url, observed_at=excluded.observed_at,
            capability_warnings_json=excluded.capability_warnings_json,
            deleted_at=NULL, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            payload["contact_record_id"],
            envelope["tenant_id"],
            party_id,
            payload["member_id"],
            payload["provider"],
            payload["provider_account_id"],
            payload["container_external_id"],
            payload["external_id"],
            payload.get("provider_revision"),
            payload["kind"],
            payload["display_name"],
            sort_name,
            payload.get("given_name"),
            payload.get("family_name"),
            payload.get("nickname"),
            payload.get("organization_name"),
            payload.get("job_title"),
            json.dumps(payload.get("fields") or {}, separators=(",", ":")),
            json.dumps(payload.get("group_external_ids") or [], separators=(",", ":")),
            payload.get("source_url"),
            payload["observed_at"],
            json.dumps(payload.get("capability_warnings") or [], separators=(",", ":")),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    for index, identifier in enumerate(payload.get("identifiers") or []):
        identifier_id = _stable_id(
            "ident_contact",
            payload["contact_record_id"],
            identifier["kind"],
            identifier["value_normalized"],
            str(index),
        )
        conn.execute(
            """
            INSERT INTO identifiers (
                identifier_id, tenant_id, party_id, kind, value, value_normalized,
                verified, primary_for_kind, last_event_id, label, source_kind,
                source_record_id, owner_scope, visibility_scope, sensitivity, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'contact_record', ?, ?, ?, ?, NULL)
            ON CONFLICT(tenant_id, identifier_id) DO UPDATE SET
                party_id=excluded.party_id, value=excluded.value,
                value_normalized=excluded.value_normalized, verified=excluded.verified,
                primary_for_kind=excluded.primary_for_kind, label=excluded.label,
                owner_scope=excluded.owner_scope, visibility_scope=excluded.visibility_scope,
                sensitivity=excluded.sensitivity, revoked_at=NULL,
                last_event_id=excluded.last_event_id
            """,
            (
                identifier_id,
                envelope["tenant_id"],
                party_id,
                identifier["kind"],
                identifier["value"],
                identifier["value_normalized"],
                1 if identifier.get("verified") else 0,
                1 if identifier.get("primary") else 0,
                envelope["event_id"],
                identifier.get("label"),
                payload["contact_record_id"],
                envelope["owner_scope"],
                envelope["visibility_scope"],
                envelope["sensitivity"],
            ),
        )
    _refresh_fts(conn, envelope["tenant_id"], party_id)


def on_contact_record_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = conn.execute(
        "SELECT party_id FROM contact_records WHERE tenant_id=? AND contact_record_id=?",
        (envelope["tenant_id"], payload["contact_record_id"]),
    ).fetchone()
    conn.execute(
        "UPDATE contact_records SET deleted_at=?, provider_revision=?, last_event_id=? "
        "WHERE tenant_id=? AND contact_record_id=? AND member_id=?",
        (
            payload["deleted_at"],
            payload.get("provider_revision"),
            envelope["event_id"],
            envelope["tenant_id"],
            payload["contact_record_id"],
            payload["member_id"],
        ),
    )
    conn.execute(
        "UPDATE identifiers SET revoked_at=?, last_event_id=? WHERE tenant_id=? "
        "AND source_record_id=?",
        (
            payload["deleted_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["contact_record_id"],
        ),
    )
    if row is not None:
        _refresh_fts(conn, envelope["tenant_id"], str(row["party_id"]))


def on_contact_group_observed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    conn.execute(
        """
        INSERT INTO contact_groups (
            group_record_id, tenant_id, member_id, provider, provider_account_id,
            container_external_id, external_id, name, provider_revision,
            observed_at, owner_scope, visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, group_record_id) DO UPDATE SET
            name=excluded.name, provider_revision=excluded.provider_revision,
            observed_at=excluded.observed_at, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            payload["group_record_id"],
            envelope["tenant_id"],
            payload["member_id"],
            payload["provider"],
            payload["provider_account_id"],
            payload["container_external_id"],
            payload["external_id"],
            payload["name"],
            payload.get("provider_revision"),
            payload["observed_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def on_member_relationship_profile_set(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope["owner_scope"] != f"private:{payload['member_id']}":
        return
    conn.execute(
        """
        INSERT INTO member_relationship_profiles (
            profile_id, tenant_id, member_id, party_id, circle,
            desired_cadence_days, cues_enabled, favorite, suppressed,
            suppression_reason, tags_json, changed_at, owner_scope,
            visibility_scope, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, member_id, party_id) DO UPDATE SET
            profile_id=excluded.profile_id, circle=excluded.circle,
            desired_cadence_days=excluded.desired_cadence_days,
            cues_enabled=excluded.cues_enabled, favorite=excluded.favorite,
            suppressed=excluded.suppressed,
            suppression_reason=excluded.suppression_reason,
            tags_json=excluded.tags_json, changed_at=excluded.changed_at,
            owner_scope=excluded.owner_scope, visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            payload["profile_id"],
            envelope["tenant_id"],
            payload["member_id"],
            payload["party_id"],
            payload.get("circle"),
            payload.get("desired_cadence_days"),
            1 if payload.get("cues_enabled") else 0,
            1 if payload.get("favorite") else 0,
            1 if payload.get("suppressed") else 0,
            payload.get("suppression_reason"),
            json.dumps(payload.get("tags") or [], separators=(",", ":")),
            payload["changed_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], payload["party_id"])


def on_relationship_fact_confirmed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope["owner_scope"] != f"private:{payload['member_id']}":
        return
    conn.execute(
        """
        INSERT INTO relationship_facts (
            fact_id, tenant_id, member_id, party_id, kind, label, value,
            effective_date, source_event_id, evidence_quote, confirmed_at,
            confirmed_by_member_id, revoked_at, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, fact_id) DO UPDATE SET
            kind=excluded.kind, label=excluded.label, value=excluded.value,
            effective_date=excluded.effective_date,
            source_event_id=excluded.source_event_id,
            evidence_quote=excluded.evidence_quote,
            confirmed_at=excluded.confirmed_at,
            confirmed_by_member_id=excluded.confirmed_by_member_id,
            revoked_at=NULL, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            payload["fact_id"],
            envelope["tenant_id"],
            payload["member_id"],
            payload["party_id"],
            payload["kind"],
            payload.get("label"),
            payload["value"],
            payload.get("effective_date"),
            payload["source_event_id"],
            payload.get("evidence_quote"),
            payload["confirmed_at"],
            payload["confirmed_by_member_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], payload["party_id"])


def on_relationship_fact_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = conn.execute(
        "SELECT party_id FROM relationship_facts WHERE tenant_id=? AND fact_id=?",
        (envelope["tenant_id"], payload["fact_id"]),
    ).fetchone()
    conn.execute(
        "UPDATE relationship_facts SET revoked_at=?, last_event_id=? "
        "WHERE tenant_id=? AND fact_id=?",
        (
            payload["revoked_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["fact_id"],
        ),
    )
    if row is not None:
        _refresh_fts(conn, envelope["tenant_id"], str(row["party_id"]))


def on_relationship_updated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    allowed = {"label", "direction", "since", "attributes_json"}
    updates = payload["field_updates"]
    assignments: list[str] = []
    values: list[Any] = []
    for key in sorted(set(updates) & allowed):
        assignments.append(f"{key}=?")
        values.append(
            json.dumps(updates[key], separators=(",", ":"))
            if key == "attributes_json" and not isinstance(updates[key], str)
            else updates[key]
        )
    if not assignments:
        return
    assignments.append("last_event_id=?")
    values.extend(
        [envelope["event_id"], envelope["tenant_id"], payload["relationship_id"]]
    )
    conn.execute(
        f"UPDATE relationships SET {', '.join(assignments)} WHERE tenant_id=? AND relationship_id=?",  # noqa: S608
        values,
    )


def on_relationship_ended(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = conn.execute(
        "SELECT party_a, party_b FROM relationships WHERE tenant_id=? AND relationship_id=?",
        (envelope["tenant_id"], payload["relationship_id"]),
    ).fetchone()
    conn.execute(
        "UPDATE relationships SET ended_at=?, last_event_id=? "
        "WHERE tenant_id=? AND relationship_id=?",
        (
            payload["ended_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["relationship_id"],
        ),
    )
    if row is not None:
        _refresh_fts(conn, envelope["tenant_id"], str(row["party_a"]))
        _refresh_fts(conn, envelope["tenant_id"], str(row["party_b"]))


def on_party_access_granted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope["owner_scope"] != f"private:{payload['owner_member_id']}":
        return
    conn.execute(
        """
        INSERT INTO party_access_grants (
            grant_id, tenant_id, party_id, owner_member_id, grantee_member_id,
            field_groups_json, granted_at, expires_at, revoked_at,
            revoked_by_member_id, owner_scope, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
        ON CONFLICT(tenant_id, grant_id) DO UPDATE SET
            field_groups_json=excluded.field_groups_json,
            granted_at=excluded.granted_at, expires_at=excluded.expires_at,
            revoked_at=NULL, revoked_by_member_id=NULL,
            last_event_id=excluded.last_event_id
        """,
        (
            payload["grant_id"],
            envelope["tenant_id"],
            payload["party_id"],
            payload["owner_member_id"],
            payload["grantee_member_id"],
            json.dumps(payload["field_groups"], separators=(",", ":")),
            payload["granted_at"],
            payload.get("expires_at"),
            envelope["owner_scope"],
            envelope["event_id"],
        ),
    )
    _refresh_fts(conn, envelope["tenant_id"], payload["party_id"])


def on_party_access_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = conn.execute(
        "SELECT party_id FROM party_access_grants WHERE tenant_id=? AND grant_id=?",
        (envelope["tenant_id"], payload["grant_id"]),
    ).fetchone()
    conn.execute(
        "UPDATE party_access_grants SET revoked_at=?, revoked_by_member_id=?, "
        "last_event_id=? WHERE tenant_id=? AND grant_id=?",
        (
            payload["revoked_at"],
            payload["revoked_by_member_id"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["grant_id"],
        ),
    )
    if row is not None:
        _refresh_fts(conn, envelope["tenant_id"], str(row["party_id"]))


def on_service_delegation_requested(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope.get("actor_identity") != payload["requested_by_member_id"]:
        return
    expected_owner_scope = (
        f"private:{payload['context_member_id']}"
        if payload["context_scope"] != "household_shared"
        else "shared:household"
    )
    if (
        envelope["owner_scope"] != expected_owner_scope
        or envelope["visibility_scope"] != expected_owner_scope
    ):
        return
    conn.execute(
        """
        INSERT INTO service_delegation_grants (
            grant_id, tenant_id, service_principal_id, service_party_id,
            context_scope, context_member_id, resource_owner_principal_id,
            owner_party_id, requested_by_member_id, required_approvals_json,
            approval_quorum, permissions_json, audience_json, visibility_json,
            requested_at, expires_at, revoked_at, revoked_by_member_id,
            revocation_reason, owner_scope, visibility_scope, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                  NULL, NULL, ?, ?, ?)
        ON CONFLICT(tenant_id, grant_id) DO NOTHING
        """,
        (
            payload["grant_id"],
            envelope["tenant_id"],
            payload["service_principal_id"],
            payload["service_party_id"],
            payload["context_scope"],
            payload.get("context_member_id"),
            payload.get("resource_owner_principal_id"),
            payload.get("owner_party_id"),
            payload["requested_by_member_id"],
            json.dumps(payload["required_approving_member_ids"], separators=(",", ":")),
            payload["approval_quorum"],
            json.dumps(payload["permissions"], separators=(",", ":")),
            json.dumps(payload.get("audience") or [], separators=(",", ":")),
            json.dumps(payload.get("visibility") or [], separators=(",", ":")),
            payload["requested_at"],
            payload.get("expires_at"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["event_id"],
        ),
    )


def on_service_delegation_approved(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope.get("actor_identity") != payload["approved_by_member_id"]:
        return
    grant = conn.execute(
        "SELECT required_approvals_json, revoked_at, owner_scope, visibility_scope "
        "FROM service_delegation_grants "
        "WHERE tenant_id=? AND grant_id=?",
        (envelope["tenant_id"], payload["grant_id"]),
    ).fetchone()
    if (
        grant is None
        or grant["revoked_at"] is not None
        or envelope["owner_scope"] != grant["owner_scope"]
        or envelope["visibility_scope"] != grant["visibility_scope"]
    ):
        return
    if payload["approved_by_member_id"] not in set(
        json.loads(str(grant["required_approvals_json"]))
    ):
        return
    conn.execute(
        """
        INSERT INTO service_delegation_approvals (
            tenant_id, grant_id, approved_by_member_id, approved_at, last_event_id
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, grant_id, approved_by_member_id) DO NOTHING
        """,
        (
            envelope["tenant_id"],
            payload["grant_id"],
            payload["approved_by_member_id"],
            payload["approved_at"],
            envelope["event_id"],
        ),
    )


def on_service_delegation_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope.get("actor_identity") != payload["revoked_by_member_id"]:
        return
    grant = conn.execute(
        "SELECT requested_by_member_id, required_approvals_json, "
        "owner_scope, visibility_scope "
        "FROM service_delegation_grants WHERE tenant_id=? AND grant_id=?",
        (envelope["tenant_id"], payload["grant_id"]),
    ).fetchone()
    if grant is None:
        return
    if (
        envelope["owner_scope"] != grant["owner_scope"]
        or envelope["visibility_scope"] != grant["visibility_scope"]
    ):
        return
    revoker = payload["revoked_by_member_id"]
    authorized = {
        str(grant["requested_by_member_id"]),
        *json.loads(str(grant["required_approvals_json"])),
    }
    if revoker not in authorized:
        return
    conn.execute(
        """UPDATE service_delegation_grants
              SET revoked_at=?, revoked_by_member_id=?, revocation_reason=?,
                  last_event_id=?
            WHERE tenant_id=? AND grant_id=? AND revoked_at IS NULL""",
        (
            payload["revoked_at"],
            revoker,
            payload.get("reason"),
            envelope["event_id"],
            envelope["tenant_id"],
            payload["grant_id"],
        ),
    )


def _json_value(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def on_constitution_registered(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope.get("source_adapter") != "constitutional-operating-agreement":
        raise ValueError("constitutional agreement is release-registered only")
    conn.execute(
        """
        INSERT INTO operating_agreements (
            tenant_id, agreement_id, revision, layer, authority_scope,
            owner_member_id, state, supersedes_revision, proposed_by_member_id,
            proposed_at, registered_at, effective_from, expires_at,
            one_time_use_limit, consumed_count, required_approvals_json,
            approval_quorum, principal_set_digest, proposal_event_id, rule_json,
            provenance_event_ids_json, provenance_digests_json, release_commit,
            definition_digest, approved_at, rejected_at, suspended_at, revoked_at,
            lifecycle_actor_member_id, lifecycle_reason_code, owner_scope,
            visibility_scope, sensitivity, last_event_id
        ) VALUES (?,?,?,'constitutional','household_shared',NULL,'active',NULL,
                  NULL,NULL,?,NULL,NULL,NULL,0,'[]','constitutional',NULL,NULL,?,
                  '[]','[]',?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?)
        ON CONFLICT(tenant_id, agreement_id, revision) DO UPDATE SET
            rule_json=excluded.rule_json, release_commit=excluded.release_commit,
            definition_digest=excluded.definition_digest,
            registered_at=excluded.registered_at, owner_scope=excluded.owner_scope,
            visibility_scope=excluded.visibility_scope,
            sensitivity=excluded.sensitivity, last_event_id=excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            payload["agreement_id"],
            payload["revision"],
            payload["registered_at"],
            _json_value(payload["rule"]),
            payload["release_commit"],
            payload["definition_digest"],
            payload["registered_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        """UPDATE operating_agreements SET state='superseded',
                  lifecycle_reason_code='superseded'
            WHERE tenant_id=? AND agreement_id=?
              AND revision < (
                  SELECT MAX(revision) FROM operating_agreements
                   WHERE tenant_id=? AND agreement_id=? AND layer='constitutional'
              )
              AND layer='constitutional' AND state='active'""",
        (
            envelope["tenant_id"],
            payload["agreement_id"],
            envelope["tenant_id"],
            payload["agreement_id"],
        ),
    )


def on_operating_agreement_proposed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope.get("actor_identity") != payload["proposed_by_member_id"]:
        raise ValueError("agreement proposer must match the event actor")
    values: dict[str, Any] = {
        "tenant_id": envelope["tenant_id"],
        "agreement_id": payload["agreement_id"],
        "revision": payload["revision"],
        "layer": payload["layer"],
        "authority_scope": payload["authority_scope"],
        "owner_member_id": payload.get("owner_member_id"),
        "state": "proposed",
        "supersedes_revision": payload.get("supersedes_revision"),
        "proposed_by_member_id": payload["proposed_by_member_id"],
        "proposed_at": payload["proposed_at"],
        "registered_at": None,
        "effective_from": payload.get("effective_from"),
        "expires_at": payload.get("expires_at"),
        "one_time_use_limit": payload.get("one_time_use_limit"),
        "consumed_count": 0,
        "required_approvals_json": _json_value(
            payload["required_approving_member_ids"]
        ),
        "approval_quorum": payload["approval_quorum"],
        "principal_set_digest": payload["principal_set_digest"],
        "proposal_event_id": envelope["event_id"],
        "rule_json": _json_value(payload["rule"]),
        "provenance_event_ids_json": _json_value(
            payload.get("provenance_event_ids") or []
        ),
        "provenance_digests_json": _json_value(payload.get("provenance_digests") or []),
        "release_commit": None,
        "definition_digest": None,
        "approved_at": None,
        "rejected_at": None,
        "suspended_at": None,
        "revoked_at": None,
        "lifecycle_actor_member_id": None,
        "lifecycle_reason_code": None,
        "owner_scope": envelope["owner_scope"],
        "visibility_scope": envelope["visibility_scope"],
        "sensitivity": envelope["sensitivity"],
        "last_event_id": envelope["event_id"],
    }
    columns = tuple(values)
    conn.execute(
        f"INSERT INTO operating_agreements ({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)}) "
        "ON CONFLICT(tenant_id, agreement_id, revision) DO NOTHING",
        tuple(values[column] for column in columns),
    )


def _agreement_for_lifecycle(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> Any:
    payload = envelope["payload"]
    row = conn.execute(
        "SELECT * FROM operating_agreements "
        "WHERE tenant_id=? AND agreement_id=? AND revision=?",
        (envelope["tenant_id"], payload["agreement_id"], payload["revision"]),
    ).fetchone()
    if row is None:
        raise ValueError("operating agreement revision does not exist")
    if (
        row["authority_scope"] != payload["authority_scope"]
        or row["owner_member_id"] != payload.get("owner_member_id")
        or row["owner_scope"] != envelope["owner_scope"]
        or row["visibility_scope"] != envelope["visibility_scope"]
    ):
        raise ValueError("operating agreement lifecycle scope changed")
    return row


def on_operating_agreement_approved(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if envelope.get("actor_identity") != payload["approved_by_member_id"]:
        raise ValueError("agreement approver must match the event actor")
    row = _agreement_for_lifecycle(envelope, conn)
    required = set(json.loads(str(row["required_approvals_json"])))
    if (
        row["state"] not in {"proposed", "active"}
        or payload["approved_by_member_id"] not in required
        or row["proposal_event_id"] != payload["proposal_event_id"]
        or row["principal_set_digest"] != payload["principal_set_digest"]
    ):
        raise ValueError("agreement approval does not match the proposal")
    conn.execute(
        """
        INSERT INTO operating_agreement_approvals (
            tenant_id, agreement_id, revision, approved_by_member_id,
            approved_at, proposal_event_id, principal_set_digest, last_event_id
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id, agreement_id, revision, approved_by_member_id)
        DO NOTHING
        """,
        (
            envelope["tenant_id"],
            payload["agreement_id"],
            payload["revision"],
            payload["approved_by_member_id"],
            payload["approved_at"],
            payload["proposal_event_id"],
            payload["principal_set_digest"],
            envelope["event_id"],
        ),
    )
    approvals = {
        str(item[0])
        for item in conn.execute(
            "SELECT approved_by_member_id FROM operating_agreement_approvals "
            "WHERE tenant_id=? AND agreement_id=? AND revision=?",
            (envelope["tenant_id"], payload["agreement_id"], payload["revision"]),
        ).fetchall()
    }
    if approvals == required:
        conn.execute(
            """UPDATE operating_agreements SET state='active', approved_at=?,
                      lifecycle_actor_member_id=?, last_event_id=?
                WHERE tenant_id=? AND agreement_id=? AND revision=?""",
            (
                payload["approved_at"],
                payload["approved_by_member_id"],
                envelope["event_id"],
                envelope["tenant_id"],
                payload["agreement_id"],
                payload["revision"],
            ),
        )
        if row["supersedes_revision"] is not None:
            conn.execute(
                """UPDATE operating_agreements SET state='superseded',
                          lifecycle_reason_code='superseded'
                    WHERE tenant_id=? AND agreement_id=? AND revision=?
                      AND state IN ('active','suspended')""",
                (
                    envelope["tenant_id"],
                    payload["agreement_id"],
                    row["supersedes_revision"],
                ),
            )


def on_operating_agreement_rejected(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = _agreement_for_lifecycle(envelope, conn)
    required = set(json.loads(str(row["required_approvals_json"])))
    if (
        envelope.get("actor_identity") != payload["rejected_by_member_id"]
        or payload["rejected_by_member_id"] not in required
    ):
        raise ValueError("agreement rejection is not authorized")
    conn.execute(
        """UPDATE operating_agreements SET state='rejected', rejected_at=?,
                  lifecycle_actor_member_id=?, lifecycle_reason_code=?, last_event_id=?
            WHERE tenant_id=? AND agreement_id=? AND revision=?
              AND state='proposed'""",
        (
            payload["rejected_at"],
            payload["rejected_by_member_id"],
            payload["reason_code"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["agreement_id"],
            payload["revision"],
        ),
    )


def on_operating_agreement_suspended(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = _agreement_for_lifecycle(envelope, conn)
    required = set(json.loads(str(row["required_approvals_json"])))
    if (
        envelope.get("actor_identity") != payload["suspended_by_member_id"]
        or payload["suspended_by_member_id"] not in required
    ):
        raise ValueError("agreement suspension is not authorized")
    conn.execute(
        """UPDATE operating_agreements SET state='suspended', suspended_at=?,
                  lifecycle_actor_member_id=?, lifecycle_reason_code=?, last_event_id=?
            WHERE tenant_id=? AND agreement_id=? AND revision=? AND state='active'""",
        (
            payload["suspended_at"],
            payload["suspended_by_member_id"],
            payload["reason_code"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["agreement_id"],
            payload["revision"],
        ),
    )


def on_operating_agreement_revoked(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = _agreement_for_lifecycle(envelope, conn)
    required = set(json.loads(str(row["required_approvals_json"])))
    if (
        envelope.get("actor_identity") != payload["revoked_by_member_id"]
        or payload["revoked_by_member_id"] not in required
    ):
        raise ValueError("agreement revocation is not authorized")
    conn.execute(
        """UPDATE operating_agreements SET state='revoked', revoked_at=?,
                  lifecycle_actor_member_id=?, lifecycle_reason_code=?, last_event_id=?
            WHERE tenant_id=? AND agreement_id=? AND revision=?
              AND state NOT IN ('revoked','superseded')""",
        (
            payload["revoked_at"],
            payload["revoked_by_member_id"],
            payload["reason_code"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["agreement_id"],
            payload["revision"],
        ),
    )


def on_operating_agreement_exception_consumed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    row = _agreement_for_lifecycle(envelope, conn)
    if row["layer"] != "exception" or row["one_time_use_limit"] != 1:
        raise ValueError("only a one-time exception can be consumed")
    if row["state"] != "active" or int(row["consumed_count"]) >= 1:
        raise ValueError("one-time exception is not applicable")
    conn.execute(
        """UPDATE operating_agreements SET state='consumed', consumed_count=1,
                  lifecycle_reason_code='consumed', last_event_id=?
            WHERE tenant_id=? AND agreement_id=? AND revision=?""",
        (
            envelope["event_id"],
            envelope["tenant_id"],
            payload["agreement_id"],
            payload["revision"],
        ),
    )
