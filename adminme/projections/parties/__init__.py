"""
Parties projection — CRM spine.

Per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.parties import handlers


class PartiesProjection(Projection):
    name = "parties"
    version = 3
    subscribes_to = [
        "party.created",
        "party.updated",
        "party.qualification_set",
        "party.archived",
        "party.restored",
        "party.merged",
        "party.split",
        "identifier.added",
        "identifier.revoked",
        "membership.added",
        "relationship.added",
        "relationship.updated",
        "relationship.ended",
        "contact.record.observed",
        "contact.record.deleted",
        "contact.group.observed",
        "endpoint.observed",
        "member_relationship.profile_set",
        "relationship_fact.confirmed",
        "relationship_fact.revoked",
        "party.access_granted",
        "party.access_revoked",
        "service.delegation.requested",
        "service.delegation.approved",
        "service.delegation.revoked",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        table_additions: dict[str, dict[str, str]] = {
            "parties": {
                "qualification": "TEXT NOT NULL DEFAULT 'qualified'",
                "source_kind": "TEXT NOT NULL DEFAULT 'domain'",
                "updated_at_ms": "INTEGER NOT NULL DEFAULT 0",
                "archived_at": "TEXT",
                "merged_into_party_id": "TEXT",
            },
            "identifiers": {
                "label": "TEXT",
                "source_kind": "TEXT NOT NULL DEFAULT 'domain'",
                "source_record_id": "TEXT",
                "owner_scope": "TEXT NOT NULL DEFAULT 'shared:household'",
                "visibility_scope": "TEXT NOT NULL DEFAULT 'shared:household'",
                "sensitivity": "TEXT NOT NULL DEFAULT 'normal'",
                "revoked_at": "TEXT",
            },
            "memberships": {
                "owner_scope": "TEXT NOT NULL DEFAULT 'shared:household'",
                "visibility_scope": "TEXT NOT NULL DEFAULT 'shared:household'",
                "sensitivity": "TEXT NOT NULL DEFAULT 'normal'",
                "ended_at": "TEXT",
            },
            "relationships": {
                "owner_scope": "TEXT NOT NULL DEFAULT 'shared:household'",
                "visibility_scope": "TEXT NOT NULL DEFAULT 'shared:household'",
                "sensitivity": "TEXT NOT NULL DEFAULT 'normal'",
                "ended_at": "TEXT",
            },
        }
        for table, additions in table_additions.items():
            columns = {
                str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

        conn.execute("DROP INDEX IF EXISTS idx_identifiers_tenant_kind_value")
        conn.execute(
            "CREATE INDEX idx_identifiers_tenant_kind_value "
            "ON identifiers(tenant_id, kind, value_normalized)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_identifiers_scope "
            "ON identifiers(tenant_id, owner_scope, revoked_at)"
        )

        fts_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(people_fts)")
        }
        if "audience_member_id" not in fts_columns:
            conn.execute("DROP TABLE IF EXISTS people_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE people_fts USING fts5("
                "tenant_id UNINDEXED, party_id UNINDEXED, "
                "audience_member_id UNINDEXED, display_name, sort_name, "
                "nickname, organization, relationship_terms)"
            )

        # Existing sender-derived rows have no historical source column in the
        # projection DB. Email-shaped, non-member records fail closed into the
        # review population until a replay or explicit promotion supplies
        # stronger evidence. No event or Party row is deleted.
        conn.execute(
            """
            UPDATE parties
               SET qualification='unconfirmed_endpoint',
                   source_kind='legacy_identity'
             WHERE qualification='qualified'
               AND (display_name LIKE '%@%' OR sort_name LIKE '%@%')
               AND NOT EXISTS (
                   SELECT 1 FROM memberships m
                    WHERE m.tenant_id=parties.tenant_id
                      AND m.party_id=parties.party_id
                      AND m.ended_at IS NULL
               )
            """
        )
        for row in conn.execute("SELECT tenant_id, party_id FROM parties").fetchall():
            handlers._refresh_fts(conn, str(row[0]), str(row[1]))


__all__ = ["PartiesProjection"]
