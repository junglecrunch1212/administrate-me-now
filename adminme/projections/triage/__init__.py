"""Triage projection — human adjudication state for derived candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.triage import handlers


class TriageProjection(Projection):
    name = "triage"
    version = 5
    subscribes_to = [
        "triage.item.proposed",
        "triage.item.enriched",
        "triage.item.confirmed",
        "triage.item.materialized",
        "triage.item.dismissed",
        "triage.item.superseded",
        "triage.intent.submitted",
        "triage.intent.features_extracted",
        "household.action.proposed",
        "identity.merge_suggested",
        "commitment.proposed",
        "commitment.edited",
        "commitment.confirmed",
        "commitment.dismissed",
        "external_sync.write_requested",
        "external_sync.write_completed",
        "external_sync.write_failed",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        info = list(conn.execute("PRAGMA table_info(triage_items)"))
        columns = {row[1]: row for row in info}
        if "resolution_reason" not in columns:
            conn.execute("ALTER TABLE triage_items ADD COLUMN resolution_reason TEXT")
            info = list(conn.execute("PRAGMA table_info(triage_items)"))
            columns = {row[1]: row for row in info}
        target_is_required = bool(columns.get("target_kind", [None] * 4)[3])
        if (
            "domain_approval_event_id" in columns
            and "parent_intent_id" in columns
            and not target_is_required
        ):
            return
        conn.executescript(
            """
            DROP TABLE IF EXISTS triage_items_v4;
            CREATE TABLE triage_items_v4 (
                proposal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                target_kind TEXT,
                owner_member_id TEXT NOT NULL,
                title TEXT NOT NULL,
                candidate_json TEXT NOT NULL DEFAULT '{}',
                route_json TEXT,
                source_event_id TEXT NOT NULL,
                parent_intent_id TEXT,
                candidate_key TEXT,
                extraction_event_id TEXT,
                eligibility_rule_ids_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL,
                reasons_json TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL CHECK (state IN (
                    'pending_interpretation','pending_review','approved',
                    'confirmed','dismissed','failed'
                )),
                proposed_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                resolution_reason TEXT,
                materialized_event_ids_json TEXT NOT NULL DEFAULT '[]',
                domain_approval_event_id TEXT,
                materialization_event_id TEXT,
                outcome_event_id TEXT,
                owner_scope TEXT NOT NULL,
                visibility_scope TEXT NOT NULL,
                sensitivity TEXT NOT NULL DEFAULT 'normal'
                    CHECK (sensitivity IN ('normal','sensitive','privileged')),
                last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, proposal_id)
            );
            INSERT INTO triage_items_v4 (
                proposal_id, tenant_id, source_kind, target_kind,
                owner_member_id, title, candidate_json, route_json,
                source_event_id, confidence, reasons_json, state, proposed_at,
                resolved_at, resolved_by, resolution_reason,
                materialized_event_ids_json, owner_scope, visibility_scope,
                sensitivity, last_event_id
            ) SELECT
                proposal_id, tenant_id, source_kind, target_kind,
                owner_member_id, title, candidate_json, route_json,
                source_event_id, confidence, reasons_json, state, proposed_at,
                resolved_at, resolved_by, resolution_reason,
                materialized_event_ids_json, owner_scope, visibility_scope,
                sensitivity, last_event_id
              FROM triage_items;
            DROP TABLE triage_items;
            ALTER TABLE triage_items_v4 RENAME TO triage_items;
            CREATE INDEX IF NOT EXISTS idx_triage_tenant_owner_state
                ON triage_items(tenant_id, owner_member_id, state, proposed_at);
            CREATE INDEX IF NOT EXISTS idx_triage_tenant_source
                ON triage_items(tenant_id, source_event_id);
            CREATE INDEX IF NOT EXISTS idx_triage_tenant_parent_intent
                ON triage_items(tenant_id, parent_intent_id);
            """
        )


__all__ = ["TriageProjection"]
