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
    interpretation_json          TEXT,
    context_id                   TEXT NOT NULL DEFAULT '',
    context_kind                 TEXT NOT NULL DEFAULT 'isolated_source',
    group_label                  TEXT,
    title_fingerprint            TEXT NOT NULL DEFAULT '',
    object_match_fingerprint     TEXT NOT NULL DEFAULT '',
    queue_lane                   TEXT NOT NULL DEFAULT 'standard',
    evidence_state               TEXT NOT NULL DEFAULT 'source_link_only',
    ownership_state              TEXT NOT NULL DEFAULT 'unknown',
    lifecycle_state              TEXT NOT NULL DEFAULT 'unknown',
    decision_disposition         TEXT NOT NULL DEFAULT 'decision',
    duplicate_of_proposal_id     TEXT,
    existing_object_kind         TEXT,
    existing_object_id           TEXT,
    policy_disposition           TEXT NOT NULL DEFAULT 'none',
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
    dismissal_event_id           TEXT,
    dismissal_request_id         TEXT,
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
CREATE TABLE IF NOT EXISTS canonical_object_fingerprints (
    tenant_id          TEXT NOT NULL,
    owner_member_id    TEXT,
    target_kind        TEXT NOT NULL,
    object_id          TEXT NOT NULL,
    title_fingerprint  TEXT NOT NULL,
    state              TEXT NOT NULL DEFAULT 'active',
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, target_kind, object_id)
);
CREATE INDEX IF NOT EXISTS idx_canonical_fingerprint_lookup
    ON canonical_object_fingerprints(
        tenant_id, owner_member_id, target_kind, title_fingerprint, state
    );

CREATE TABLE IF NOT EXISTS triage_context_sessions (
    tenant_id          TEXT NOT NULL,
    owner_member_id    TEXT NOT NULL,
    thread_key         TEXT NOT NULL,
    sequence           INTEGER NOT NULL,
    context_id         TEXT NOT NULL,
    first_proposed_at  TEXT NOT NULL,
    last_proposed_at   TEXT NOT NULL,
    last_source_event_id TEXT NOT NULL,
    source_count       INTEGER NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, owner_member_id, thread_key, sequence),
    UNIQUE (tenant_id, context_id)
);
CREATE INDEX IF NOT EXISTS idx_triage_context_session_latest
    ON triage_context_sessions(tenant_id, owner_member_id, thread_key, sequence);
CREATE TABLE IF NOT EXISTS triage_context_session_sources (
    tenant_id          TEXT NOT NULL,
    owner_member_id    TEXT NOT NULL,
    thread_key         TEXT NOT NULL,
    sequence           INTEGER NOT NULL,
    source_event_id    TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, owner_member_id, thread_key, sequence, source_event_id
    )
);
-- parent_intent_id is additive for deployed projection databases. Its index is
-- created by TriageProjection.migrate_schema() after that column exists.
