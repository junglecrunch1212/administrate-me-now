"""Triage projection — human adjudication state for derived candidates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.context_fabric import (
    evidence_state,
    exact_text_fingerprint,
    lifecycle_state,
    ownership_state,
    queue_lane,
    stable_digest,
)

from adminme.projections.base import Projection
from adminme.projections.triage import handlers
from adminme.triage_review import proposal_source_id, triage_review_state


class TriageProjection(Projection):
    name = "triage"
    version = 12
    replay_retained_events_on_version_change = True
    subscribes_to = [
        "triage.item.proposed",
        "triage.item.enriched",
        "triage.item.confirmed",
        "triage.item.materialized",
        "triage.item.dismissed",
        "triage.item.superseded",
        "triage.item.managed",
        "triage.source_policy_rule.decided",
        "triage.source_policy_rule.reviewed",
        "triage.attention_rule.saved",
        "triage.attention_rule.reviewed",
        "triage.attention_feedback.recorded",
        "triage.reconciliation.decided",
        "triage.intent.submitted",
        "triage.intent.features_extracted",
        "household.action.proposed",
        "identity.merge_suggested",
        "commitment.proposed",
        "commitment.edited",
        "commitment.confirmed",
        "commitment.dismissed",
        "member.provider_action.confirmed",
        "external_sync.write_requested",
        "external_sync.write_completed",
        "external_sync.write_failed",
        "task.created",
        "recurrence.added",
        "calendar.event_added",
        "list_item.added",
        "list_item.updated",
        "triage.feedback.recorded",
        "triage.policy_rule.approved",
        "triage.policy_rule.revoked",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)

    def migrate_schema(self, conn: Any) -> None:
        info = list(conn.execute("PRAGMA table_info(triage_items)"))
        columns = {row[1]: row for row in info}
        if "interpretation_json" not in columns:
            conn.execute("ALTER TABLE triage_items ADD COLUMN interpretation_json TEXT")
            info = list(conn.execute("PRAGMA table_info(triage_items)"))
            columns = {row[1]: row for row in info}
        if "resolution_reason" not in columns:
            conn.execute("ALTER TABLE triage_items ADD COLUMN resolution_reason TEXT")
            info = list(conn.execute("PRAGMA table_info(triage_items)"))
            columns = {row[1]: row for row in info}
        target_is_required = bool(columns.get("target_kind", [None] * 4)[3])
        current_shape = (
            "domain_approval_event_id" in columns
            and "parent_intent_id" in columns
            and not target_is_required
        )
        if not current_shape:
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
                interpretation_json TEXT,
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
                confirmation_event_id TEXT,
                confirmation_request_id TEXT,
                materialization_event_id TEXT,
                outcome_event_id TEXT,
                dismissal_event_id TEXT,
                dismissal_request_id TEXT,
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
                """
            )
        info = list(conn.execute("PRAGMA table_info(triage_items)"))
        columns = {row[1]: row for row in info}
        additions = {
            "context_id": "TEXT NOT NULL DEFAULT ''",
            "context_kind": "TEXT NOT NULL DEFAULT 'isolated_source'",
            "group_label": "TEXT",
            "title_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "object_match_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "queue_lane": "TEXT NOT NULL DEFAULT 'standard'",
            "evidence_state": "TEXT NOT NULL DEFAULT 'source_link_only'",
            "ownership_state": "TEXT NOT NULL DEFAULT 'unknown'",
            "lifecycle_state": "TEXT NOT NULL DEFAULT 'unknown'",
            "review_state": "TEXT NOT NULL DEFAULT 'needs_details'",
            "source_id": "TEXT NOT NULL DEFAULT 'origin:unknown'",
            "decision_disposition": "TEXT NOT NULL DEFAULT 'decision'",
            "duplicate_of_proposal_id": "TEXT",
            "existing_object_kind": "TEXT",
            "existing_object_id": "TEXT",
            "policy_disposition": "TEXT NOT NULL DEFAULT 'none'",
            "admission_decision": "TEXT NOT NULL DEFAULT 'admit'",
            "reconciliation_active": "INTEGER NOT NULL DEFAULT 0",
            "reconciliation_id": "TEXT",
            "reconciliation_event_id": "TEXT",
            "reconciliation_revision": "INTEGER NOT NULL DEFAULT 0",
            "reconciliation_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
            "reconciliation_evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            "reconciliation_authored_text_digest": "TEXT",
            "reconciliation_policy_json": "TEXT",
            "reconciliation_scope_json": "TEXT",
            "reconciliation_freshness": "TEXT NOT NULL DEFAULT 'unknown'",
            "reconciliation_prior_state": "TEXT",
            "priority_rank": "INTEGER NOT NULL DEFAULT 60",
            "priority_basis_json": (
                "TEXT NOT NULL DEFAULT '[\"standard_actionable_candidate\"]'"
            ),
            "human_lane": "TEXT NOT NULL DEFAULT 'low_confidence_review'",
            "management_state": "TEXT NOT NULL DEFAULT 'open'",
            "management_action": "TEXT",
            "management_action_id": "TEXT",
            "management_revision": "INTEGER NOT NULL DEFAULT 0",
            "management_event_id": "TEXT",
            "management_reason_codes_json": "TEXT NOT NULL DEFAULT '[]'",
            "snoozed_until": "TEXT",
            "learned_policy_state": "TEXT NOT NULL DEFAULT 'none'",
            "learned_rule_id": "TEXT",
            "proposal_event_id": "TEXT",
            "attention_expires_at": "TEXT",
            "attention_base_state": "TEXT NOT NULL DEFAULT 'none'",
            "attention_base_rule_id": "TEXT",
            "attention_base_lane": "TEXT",
            "attention_reason_json": "TEXT NOT NULL DEFAULT '[]'",
            "attention_rule_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "attention_shadow_json": "TEXT NOT NULL DEFAULT '[]'",
            "attention_expiry_schedule_json": "TEXT NOT NULL DEFAULT '[]'",
            "confirmation_event_id": "TEXT",
            "confirmation_request_id": "TEXT",
            "dismissal_event_id": "TEXT",
            "dismissal_request_id": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE triage_items ADD COLUMN {name} {sql_type}")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS triage_management_actions (
                tenant_id TEXT NOT NULL, action_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL, member_id TEXT NOT NULL,
                action TEXT NOT NULL, action_revision INTEGER NOT NULL,
                supersedes_action_id TEXT, reason_codes_json TEXT NOT NULL DEFAULT '[]',
                evidence_event_ids_json TEXT NOT NULL DEFAULT '[]',
                linked_object_kind TEXT, linked_object_id TEXT,
                duplicate_of_proposal_id TEXT, snoozed_until TEXT,
                policy_proposal_id TEXT,
                reversible INTEGER NOT NULL CHECK (reversible=1),
                provider_effects INTEGER NOT NULL CHECK (provider_effects=0),
                managed_at TEXT NOT NULL, owner_scope TEXT NOT NULL,
                visibility_scope TEXT NOT NULL, sensitivity TEXT NOT NULL,
                last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, action_id),
                UNIQUE (tenant_id, proposal_id, action_revision)
            );
            CREATE INDEX IF NOT EXISTS idx_triage_management_member
                ON triage_management_actions(
                    tenant_id, member_id, proposal_id, action_revision
                );
            CREATE TABLE IF NOT EXISTS triage_learned_policy_rules (
                tenant_id TEXT NOT NULL, rule_id TEXT NOT NULL,
                policy_proposal_id TEXT NOT NULL, member_id TEXT NOT NULL,
                scope_kind TEXT NOT NULL, scope_value_digest TEXT NOT NULL,
                outcome_kind TEXT NOT NULL, instruction TEXT NOT NULL,
                activation_mode TEXT NOT NULL CHECK (
                    activation_mode IN ('approved','trial')
                ),
                trial_proposal_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL CHECK (
                    status IN ('active','trial','suspended','revoked')
                ),
                last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, rule_id)
            );
            CREATE INDEX IF NOT EXISTS idx_triage_human_queue
                ON triage_items(
                    tenant_id, owner_member_id, management_state,
                    human_lane, proposed_at
                );
            CREATE TABLE IF NOT EXISTS triage_context_sessions (
                tenant_id TEXT NOT NULL, owner_member_id TEXT NOT NULL,
                thread_key TEXT NOT NULL, sequence INTEGER NOT NULL,
                context_id TEXT NOT NULL, first_proposed_at TEXT NOT NULL,
                last_proposed_at TEXT NOT NULL, last_source_event_id TEXT NOT NULL,
                source_count INTEGER NOT NULL, last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, owner_member_id, thread_key, sequence),
                UNIQUE (tenant_id, context_id)
            );
            CREATE TABLE IF NOT EXISTS triage_context_session_sources (
                tenant_id TEXT NOT NULL, owner_member_id TEXT NOT NULL,
                thread_key TEXT NOT NULL, sequence INTEGER NOT NULL,
                source_event_id TEXT NOT NULL,
                PRIMARY KEY (
                    tenant_id, owner_member_id, thread_key, sequence,
                    source_event_id
                )
            );
            """
        )
        rows = conn.execute(
            """SELECT tenant_id, proposal_id, owner_member_id, target_kind,
                      title, source_event_id, candidate_json, reasons_json,
                      interpretation_json, proposed_at, last_event_id,
                      object_match_fingerprint
                 FROM triage_items
                WHERE context_id='' OR title_fingerprint=''
                   OR object_match_fingerprint=''
                ORDER BY tenant_id, owner_member_id, proposed_at, proposal_id"""
        ).fetchall()
        sessions: dict[tuple[str, str, str], dict[str, Any]] = {}
        completed_sessions: list[tuple[tuple[str, str, str], dict[str, Any]]] = []
        for row in rows:
            interpretation = json.loads(row[8]) if row[8] else None
            payload = {
                "owner_member_id": row[2],
                "target_kind": row[3],
                "title": row[4],
                "source_event_id": row[5],
                "candidate": json.loads(row[6] or "{}"),
                "reasons": json.loads(row[7] or "[]"),
                "interpretation": interpretation,
            }
            object_match = exact_text_fingerprint(
                row[3],
                (
                    f"{payload['candidate'].get('kind', '')}|"
                    f"{payload['candidate'].get('rrule', '')}"
                    if row[3] == "recurrence" and payload["candidate"].get("kind")
                    else row[4]
                ),
            )
            thread_key = (
                str(interpretation.get("thread_key") or "")
                if isinstance(interpretation, dict)
                else ""
            )
            context_kind = "isolated_source"
            if thread_key:
                key = (str(row[0]), str(row[2]), thread_key)
                proposed = datetime.fromisoformat(str(row[9]).replace("Z", "+00:00"))
                if proposed.tzinfo is None:
                    proposed = proposed.replace(tzinfo=timezone.utc)
                session = sessions.get(key)
                new_session = session is None
                if session is not None and str(row[5]) not in session["sources"]:
                    new_session = (
                        len(session["sources"]) >= 100
                        or abs((proposed - session["first"]).days) > 30
                    )
                if new_session:
                    if session is not None:
                        completed_sessions.append((key, dict(session)))
                    sequence = 0 if session is None else int(session["sequence"]) + 1
                    session = {
                        "sequence": sequence,
                        "first": proposed,
                        "first_text": str(row[9]),
                        "last_text": str(row[9]),
                        "last_source": str(row[5]),
                        "sources": {str(row[5])},
                        "last_event_id": str(row[10]),
                    }
                    sessions[key] = session
                assert session is not None
                if not new_session and str(row[5]) not in session["sources"]:
                    session["last_text"] = str(row[9])
                    session["last_source"] = str(row[5])
                    session["sources"].add(str(row[5]))
                    session["last_event_id"] = str(row[10])
                context_id = "ctx_matter_" + stable_digest(
                    row[0], row[2], thread_key, session["sequence"]
                )
                context_kind = "deterministic_matter"
            else:
                context_id = "ctx_source_" + stable_digest(row[0], row[2], row[5])
            conn.execute(
                """UPDATE triage_items SET
                       context_id=?, context_kind=?, group_label=?,
                       title_fingerprint=?, object_match_fingerprint=?,
                       queue_lane=?, evidence_state=?,
                       ownership_state=?, lifecycle_state=?
                     WHERE tenant_id=? AND proposal_id=?""",
                (
                    context_id,
                    context_kind,
                    interpretation.get("compact_summary")
                    if isinstance(interpretation, dict)
                    else row[4],
                    exact_text_fingerprint(row[3], row[4]),
                    object_match,
                    queue_lane(payload),
                    evidence_state(payload),
                    ownership_state(payload),
                    lifecycle_state(payload),
                    row[0],
                    row[1],
                ),
            )
        completed_sessions.extend(
            (key, dict(session)) for key, session in sessions.items()
        )
        for (tenant_id, owner_id, thread_key), session in completed_sessions:
            context_id = "ctx_matter_" + stable_digest(
                tenant_id, owner_id, thread_key, session["sequence"]
            )
            conn.execute(
                """INSERT OR REPLACE INTO triage_context_sessions (
                       tenant_id, owner_member_id, thread_key, sequence,
                       context_id, first_proposed_at, last_proposed_at,
                       last_source_event_id, source_count, last_event_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    owner_id,
                    thread_key,
                    session["sequence"],
                    context_id,
                    session["first_text"],
                    session["last_text"],
                    session["last_source"],
                    len(session["sources"]),
                    session["last_event_id"],
                ),
            )
            conn.executemany(
                """INSERT OR IGNORE INTO triage_context_session_sources (
                       tenant_id, owner_member_id, thread_key, sequence,
                       source_event_id
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        tenant_id,
                        owner_id,
                        thread_key,
                        session["sequence"],
                        source_event_id,
                    )
                    for source_event_id in sorted(session["sources"])
                ],
            )

        # Recompute every presentation-only classifier during a version
        # migration. Retained proposals therefore receive the corrected
        # nullable-date lane and the same source/readiness facets as new rows.
        presentation_rows = conn.execute(
            """SELECT proposal_id, source_kind, target_kind, title,
                      candidate_json, route_json, eligibility_rule_ids_json,
                      reasons_json, interpretation_json, state, tenant_id
                 FROM triage_items"""
        ).fetchall()
        for row in presentation_rows:
            candidate = json.loads(row[4] or "{}")
            route = json.loads(row[5]) if row[5] else None
            eligibility = json.loads(row[6] or "[]")
            payload = {
                "target_kind": row[2],
                "title": row[3],
                "candidate": candidate,
                "reasons": json.loads(row[7] or "[]"),
                "interpretation": json.loads(row[8]) if row[8] else None,
            }
            review = triage_review_state(
                {"title": row[3], "target_kind": row[2], "state": row[9]},
                candidate=candidate,
                route=route,
            )
            conn.execute(
                """UPDATE triage_items
                      SET queue_lane=?, source_id=?, review_state=?
                    WHERE tenant_id=? AND proposal_id=?""",
                (
                    queue_lane(payload),
                    proposal_source_id(
                        source_kind=str(row[1]),
                        candidate=candidate,
                        eligibility_rule_ids=eligibility,
                    ),
                    review["state"],
                    row[10],
                    row[0],
                ),
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_triage_tenant_parent_intent "
            "ON triage_items(tenant_id, parent_intent_id)"
        )
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_triage_member_context_state
                ON triage_items(tenant_id, owner_member_id, context_id, state, proposed_at);
            CREATE INDEX IF NOT EXISTS idx_triage_member_inventory
                ON triage_items(tenant_id, owner_member_id, state, proposed_at, proposal_id);
            CREATE INDEX IF NOT EXISTS idx_triage_exact_match
                ON triage_items(tenant_id, owner_member_id, target_kind, title_fingerprint, state);
            CREATE INDEX IF NOT EXISTS idx_triage_inventory_facets
                ON triage_items(tenant_id, owner_member_id, state, source_id, target_kind);
            CREATE TABLE IF NOT EXISTS canonical_object_fingerprints (
                tenant_id TEXT NOT NULL, owner_member_id TEXT, target_kind TEXT NOT NULL,
                object_id TEXT NOT NULL, title_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active', owner_scope TEXT NOT NULL,
                visibility_scope TEXT NOT NULL, sensitivity TEXT NOT NULL,
                last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, target_kind, object_id)
            );
            CREATE INDEX IF NOT EXISTS idx_canonical_fingerprint_lookup
                ON canonical_object_fingerprints(
                    tenant_id, owner_member_id, target_kind, title_fingerprint, state
                );
            CREATE TABLE IF NOT EXISTS triage_context_sessions (
                tenant_id TEXT NOT NULL, owner_member_id TEXT NOT NULL,
                thread_key TEXT NOT NULL, sequence INTEGER NOT NULL,
                context_id TEXT NOT NULL, first_proposed_at TEXT NOT NULL,
                last_proposed_at TEXT NOT NULL, last_source_event_id TEXT NOT NULL,
                source_count INTEGER NOT NULL, last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, owner_member_id, thread_key, sequence),
                UNIQUE (tenant_id, context_id)
            );
            CREATE TABLE IF NOT EXISTS triage_context_session_sources (
                tenant_id TEXT NOT NULL, owner_member_id TEXT NOT NULL,
                thread_key TEXT NOT NULL, sequence INTEGER NOT NULL,
                source_event_id TEXT NOT NULL,
                PRIMARY KEY (
                    tenant_id, owner_member_id, thread_key, sequence,
                    source_event_id
                )
            );
            CREATE INDEX IF NOT EXISTS idx_triage_context_session_latest
                ON triage_context_sessions(
                    tenant_id, owner_member_id, thread_key, sequence
                );
            """
        )


__all__ = ["TriageProjection"]
