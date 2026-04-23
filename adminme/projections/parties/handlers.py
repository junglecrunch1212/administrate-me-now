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
        "identifier.added": on_identifier_added,
        "membership.added": on_membership_added,
        "relationship.added": on_relationship_added,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        # Defensive — subscribes_to should prevent this, but if the runner
        # ever delivers an unexpected type, ignore rather than raise.
        return
    handler(envelope, conn)


def on_party_created(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO parties (
            party_id, tenant_id, kind, display_name, sort_name,
            nickname, pronouns, notes, attributes_json,
            owner_scope, visibility_scope, sensitivity,
            created_at_ms, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            envelope["event_at_ms"],
            envelope["event_id"],
        ),
    )


def on_identifier_added(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    tenant_id = envelope["tenant_id"]

    # Merge-candidate guard: if (tenant_id, kind, value_normalized) is already
    # owned by a different party, log at INFO and skip insertion. Per
    # DECISIONS.md §D4 the CRM spine does not auto-merge — prompt 10b's
    # identity_resolution pipeline emits merge suggestions for human review.
    existing = conn.execute(
        "SELECT identifier_id, party_id FROM identifiers "
        "WHERE tenant_id = ? AND kind = ? AND value_normalized = ?",
        (tenant_id, p["kind"], p["value_normalized"]),
    ).fetchone()
    if existing is not None and existing["party_id"] != p["party_id"]:
        _log.info(
            "potential merge candidate for %s/%s: identifier already owned by %s, "
            "new event claims %s (prompt 10b identity_resolution will adjudicate)",
            p["kind"],
            p["value_normalized"],
            existing["party_id"],
            p["party_id"],
        )
        return

    conn.execute(
        """
        INSERT INTO identifiers (
            identifier_id, tenant_id, party_id, kind, value,
            value_normalized, verified, primary_for_kind, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, identifier_id) DO UPDATE SET
            party_id         = excluded.party_id,
            kind             = excluded.kind,
            value            = excluded.value,
            value_normalized = excluded.value_normalized,
            verified         = excluded.verified,
            primary_for_kind = excluded.primary_for_kind,
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
        ),
    )


def on_membership_added(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO memberships (
            membership_id, tenant_id, party_id, parent_party_id,
            role, started_at, attributes_json, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, membership_id) DO UPDATE SET
            party_id        = excluded.party_id,
            parent_party_id = excluded.parent_party_id,
            role            = excluded.role,
            started_at      = excluded.started_at,
            attributes_json = excluded.attributes_json,
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
        ),
    )


def on_relationship_added(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO relationships (
            relationship_id, tenant_id, party_a, party_b,
            label, direction, since, attributes_json, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, relationship_id) DO UPDATE SET
            party_a         = excluded.party_a,
            party_b         = excluded.party_b,
            label           = excluded.label,
            direction       = excluded.direction,
            since           = excluded.since,
            attributes_json = excluded.attributes_json,
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
        ),
    )
