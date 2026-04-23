"""
money projection handlers — transaction ledger state transitions.

Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2. Handlers are
idempotent: re-applying the same event produces the same row state (§2
invariant 4). Keyed by ``(tenant_id, flow_id)``.

Subscribed event types:
- ``money_flow.recorded``         → INSERT with is_manual=0
- ``money_flow.manually_added``   → INSERT with is_manual=1
- ``money_flow.manually_deleted`` → UPDATE deleted_at (soft delete)

Per [§2.3], handlers do not validate cross-projection references — if
``linked_account`` does not exist in ``accounts``, the row still lands.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "money_flow.recorded": apply_money_flow_recorded,
        "money_flow.manually_added": apply_money_flow_manually_added,
        "money_flow.manually_deleted": apply_money_flow_manually_deleted,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def apply_money_flow_recorded(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO money_flows (
            flow_id, tenant_id, from_party, to_party, amount_minor, currency,
            occurred_at, kind, category, linked_artifact, linked_account,
            linked_interaction, notes, source_adapter, is_manual,
            added_by_party_id, deleted_at, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL,
                  ?, ?, ?, ?)
        ON CONFLICT(tenant_id, flow_id) DO UPDATE SET
            from_party         = excluded.from_party,
            to_party           = excluded.to_party,
            amount_minor       = excluded.amount_minor,
            currency           = excluded.currency,
            occurred_at        = excluded.occurred_at,
            kind               = excluded.kind,
            category           = excluded.category,
            linked_artifact    = excluded.linked_artifact,
            linked_account     = excluded.linked_account,
            linked_interaction = excluded.linked_interaction,
            notes              = excluded.notes,
            source_adapter     = excluded.source_adapter,
            is_manual          = 0,
            added_by_party_id  = NULL,
            owner_scope        = excluded.owner_scope,
            visibility_scope   = excluded.visibility_scope,
            sensitivity        = excluded.sensitivity,
            last_event_id      = excluded.last_event_id
        """,
        (
            p["flow_id"],
            envelope["tenant_id"],
            p.get("from_party_id"),
            p.get("to_party_id"),
            p["amount_minor"],
            p["currency"],
            p["occurred_at"],
            p["kind"],
            p.get("category"),
            p.get("linked_artifact"),
            p.get("linked_account"),
            p.get("linked_interaction"),
            p.get("notes"),
            p["source_adapter"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_money_flow_manually_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO money_flows (
            flow_id, tenant_id, from_party, to_party, amount_minor, currency,
            occurred_at, kind, category, linked_artifact, linked_account,
            linked_interaction, notes, source_adapter, is_manual,
            added_by_party_id, deleted_at, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, 'manual',
                  1, ?, NULL, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, flow_id) DO UPDATE SET
            from_party         = excluded.from_party,
            to_party           = excluded.to_party,
            amount_minor       = excluded.amount_minor,
            currency           = excluded.currency,
            occurred_at        = excluded.occurred_at,
            kind               = excluded.kind,
            category           = excluded.category,
            notes              = excluded.notes,
            source_adapter     = 'manual',
            is_manual          = 1,
            added_by_party_id  = excluded.added_by_party_id,
            owner_scope        = excluded.owner_scope,
            visibility_scope   = excluded.visibility_scope,
            sensitivity        = excluded.sensitivity,
            last_event_id      = excluded.last_event_id
        """,
        (
            p["flow_id"],
            envelope["tenant_id"],
            p.get("from_party_id"),
            p.get("to_party_id"),
            p["amount_minor"],
            p["currency"],
            p["occurred_at"],
            p["kind"],
            p.get("category"),
            p.get("notes"),
            p["added_by_party_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_money_flow_manually_deleted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    # Soft-delete: row stays, deleted_at populated. Rebuild correctness
    # depends on the row persisting.
    p = envelope["payload"]
    cur = conn.execute(
        """
        UPDATE money_flows
           SET deleted_at    = ?,
               last_event_id = ?
         WHERE tenant_id = ? AND flow_id = ?
        """,
        (
            p["deleted_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            p["flow_id"],
        ),
    )
    if cur.rowcount == 0:
        # Deletion before addition — event ordering quirk. No-op; log.
        _log.info(
            "money_flow.manually_deleted for unknown flow_id=%s tenant=%s",
            p["flow_id"],
            envelope["tenant_id"],
        )
