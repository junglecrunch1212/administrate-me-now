-- Rebuildable typed temporal relationship index.  It contains identifiers,
-- coordinates, and reasons only; source bodies remain in their source facts.

CREATE TABLE IF NOT EXISTS context_nodes (
    tenant_id          TEXT NOT NULL,
    node_id            TEXT NOT NULL,
    node_kind          TEXT NOT NULL,
    owner_member_id    TEXT,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL CHECK (sensitivity IN ('normal','sensitive','privileged')),
    provider           TEXT,
    provider_account_id TEXT,
    workspace          TEXT,
    authority          TEXT NOT NULL CHECK (authority IN ('source_fact','member_confirmed','deterministic_derived','llm_proposed')),
    created_at         TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, node_id)
);

CREATE TABLE IF NOT EXISTS context_edges (
    tenant_id          TEXT NOT NULL,
    edge_id            TEXT NOT NULL,
    from_node_id       TEXT NOT NULL,
    to_node_id         TEXT NOT NULL,
    edge_type          TEXT NOT NULL,
    authority          TEXT NOT NULL CHECK (authority IN ('provider_exact','member_confirmed','deterministic_derived','llm_proposed')),
    reason_codes_json  TEXT NOT NULL DEFAULT '[]',
    source_event_ids_json TEXT NOT NULL DEFAULT '[]',
    algorithm_id       TEXT NOT NULL,
    schema_version     INTEGER NOT NULL,
    valid_from         TEXT NOT NULL,
    valid_to           TEXT,
    expires_at         TEXT,
    status             TEXT NOT NULL CHECK (status IN ('active','revoked','superseded')),
    revoked_by_event_id TEXT,
    owner_member_id    TEXT,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, edge_id)
);

CREATE TABLE IF NOT EXISTS contexts (
    tenant_id          TEXT NOT NULL,
    context_id         TEXT NOT NULL,
    context_level      TEXT NOT NULL CHECK (context_level IN ('provider_conversation','deterministic_matter','member_confirmed','isolated_source','semantic_suggestion')),
    label              TEXT,
    member_confirmed   INTEGER NOT NULL DEFAULT 0,
    merged_into_context_id TEXT,
    owner_member_id    TEXT,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL CHECK (sensitivity IN ('normal','sensitive','privileged')),
    created_at         TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, context_id)
);

CREATE TABLE IF NOT EXISTS triage_feedback (
    tenant_id          TEXT NOT NULL,
    feedback_id        TEXT NOT NULL,
    proposal_id        TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    reason             TEXT NOT NULL,
    context_id         TEXT,
    target_kind        TEXT,
    source_adapter     TEXT,
    provider_account_id TEXT,
    workspace          TEXT,
    sender_digest      TEXT,
    recorded_at        TEXT NOT NULL,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, feedback_id)
);

CREATE TABLE IF NOT EXISTS triage_policy_rules (
    tenant_id          TEXT NOT NULL,
    rule_id            TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    scope_kind         TEXT NOT NULL,
    scope_value_digest TEXT NOT NULL,
    disposition        TEXT NOT NULL,
    enforcement_mode   TEXT NOT NULL DEFAULT 'shadow' CHECK (enforcement_mode='shadow'),
    preview_affected_count INTEGER NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('active','revoked')),
    approved_at        TEXT NOT NULL,
    approved_by_member_id TEXT NOT NULL,
    expires_at         TEXT,
    revoked_at         TEXT,
    revoked_by_member_id TEXT,
    revoke_reason      TEXT,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_context_nodes_scope_kind
    ON context_nodes(tenant_id, owner_member_id, node_kind);
CREATE INDEX IF NOT EXISTS idx_context_edges_from
    ON context_edges(tenant_id, owner_member_id, from_node_id, status, edge_type);
CREATE INDEX IF NOT EXISTS idx_context_edges_to
    ON context_edges(tenant_id, owner_member_id, to_node_id, status, edge_type);
CREATE INDEX IF NOT EXISTS idx_contexts_member
    ON contexts(tenant_id, owner_member_id, context_level);
CREATE INDEX IF NOT EXISTS idx_feedback_member_reason
    ON triage_feedback(tenant_id, member_id, reason, recorded_at);
CREATE INDEX IF NOT EXISTS idx_policy_member_status
    ON triage_policy_rules(tenant_id, member_id, status, scope_kind);

CREATE TABLE IF NOT EXISTS context_matter_sessions (
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

CREATE TABLE IF NOT EXISTS context_relationship_decisions (
    tenant_id              TEXT NOT NULL,
    relationship_id        TEXT NOT NULL,
    source_event_id        TEXT NOT NULL,
    related_source_event_id TEXT NOT NULL,
    member_id              TEXT NOT NULL,
    provider               TEXT NOT NULL,
    provider_account_id    TEXT NOT NULL,
    workspace              TEXT NOT NULL,
    algorithm              TEXT NOT NULL,
    state                  TEXT NOT NULL CHECK (state IN ('rejected','confirmed')),
    reason                 TEXT NOT NULL,
    decided_at             TEXT NOT NULL,
    decided_by_member_id   TEXT NOT NULL,
    owner_scope            TEXT NOT NULL,
    visibility_scope       TEXT NOT NULL,
    sensitivity            TEXT NOT NULL,
    last_event_id          TEXT NOT NULL,
    PRIMARY KEY (tenant_id, relationship_id)
);

CREATE TABLE IF NOT EXISTS context_source_text_corrections (
    tenant_id              TEXT NOT NULL,
    correction_id          TEXT NOT NULL,
    source_event_id        TEXT NOT NULL,
    proposal_id            TEXT,
    candidate_key          TEXT,
    member_id              TEXT NOT NULL,
    algorithm              TEXT,
    authored_text_digest   TEXT,
    evidence_start         INTEGER,
    evidence_end           INTEGER,
    state                  TEXT NOT NULL CHECK (state IN ('corrected','reinstated')),
    reason                 TEXT NOT NULL,
    evidence_quote_digest  TEXT,
    decided_at             TEXT NOT NULL,
    decided_by_member_id   TEXT NOT NULL,
    owner_scope            TEXT NOT NULL,
    visibility_scope       TEXT NOT NULL,
    sensitivity            TEXT NOT NULL,
    last_event_id          TEXT NOT NULL,
    PRIMARY KEY (tenant_id, correction_id)
);

CREATE INDEX IF NOT EXISTS idx_context_relationship_decisions_member
    ON context_relationship_decisions(tenant_id, member_id, state);
CREATE INDEX IF NOT EXISTS idx_context_source_text_corrections_member
    ON context_source_text_corrections(tenant_id, member_id, state);

CREATE TABLE IF NOT EXISTS context_matter_session_sources (
    tenant_id          TEXT NOT NULL,
    owner_member_id    TEXT NOT NULL,
    thread_key         TEXT NOT NULL,
    sequence           INTEGER NOT NULL,
    source_event_id    TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, owner_member_id, thread_key, sequence, source_event_id
    )
);
