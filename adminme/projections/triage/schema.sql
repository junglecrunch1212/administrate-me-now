-- Triage is the member-owned adjudication queue for derived candidates.
-- Raw source observations never enter this table merely because they arrived.

CREATE TABLE IF NOT EXISTS triage_items (
    proposal_id                  TEXT NOT NULL,
    tenant_id                    TEXT NOT NULL,
    source_kind                  TEXT NOT NULL,
    target_kind                  TEXT,
    owner_member_id              TEXT NOT NULL,
    title                        TEXT NOT NULL,
    candidate_json               TEXT NOT NULL DEFAULT '{}',
    route_json                   TEXT,
    source_event_id              TEXT NOT NULL,
    parent_intent_id             TEXT,
    candidate_key                TEXT,
    extraction_event_id          TEXT,
    eligibility_rule_ids_json    TEXT NOT NULL DEFAULT '[]',
    confidence                   REAL,
    reasons_json                 TEXT NOT NULL DEFAULT '[]',
    state                        TEXT NOT NULL
                                 CHECK (state IN (
                                   'pending_interpretation','pending_review','approved',
                                   'confirmed','dismissed','failed'
                                 )),
    proposed_at                  TEXT NOT NULL,
    resolved_at                  TEXT,
    resolved_by                  TEXT,
    resolution_reason            TEXT,
    materialized_event_ids_json  TEXT NOT NULL DEFAULT '[]',
    domain_approval_event_id     TEXT,
    materialization_event_id     TEXT,
    outcome_event_id             TEXT,
    owner_scope                  TEXT NOT NULL,
    visibility_scope             TEXT NOT NULL,
    sensitivity                  TEXT NOT NULL DEFAULT 'normal'
                                 CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id                TEXT NOT NULL,
    PRIMARY KEY (tenant_id, proposal_id)
);

CREATE INDEX IF NOT EXISTS idx_triage_tenant_owner_state
    ON triage_items(tenant_id, owner_member_id, state, proposed_at);
CREATE INDEX IF NOT EXISTS idx_triage_tenant_source
    ON triage_items(tenant_id, source_event_id);
CREATE INDEX IF NOT EXISTS idx_triage_tenant_parent_intent
    ON triage_items(tenant_id, parent_intent_id);
