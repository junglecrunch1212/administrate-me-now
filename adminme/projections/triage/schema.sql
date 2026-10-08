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
    source_id                    TEXT NOT NULL DEFAULT 'origin:unknown',
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
    review_state                 TEXT NOT NULL DEFAULT 'needs_details',
    decision_disposition         TEXT NOT NULL DEFAULT 'decision',
    duplicate_of_proposal_id     TEXT,
    existing_object_kind         TEXT,
    existing_object_id           TEXT,
    policy_disposition           TEXT NOT NULL DEFAULT 'none',
    admission_decision           TEXT NOT NULL DEFAULT 'admit',
    reconciliation_active        INTEGER NOT NULL DEFAULT 0,
    reconciliation_id            TEXT,
    reconciliation_event_id      TEXT,
    reconciliation_revision      INTEGER NOT NULL DEFAULT 0,
    reconciliation_reasons_json  TEXT NOT NULL DEFAULT '[]',
    reconciliation_evidence_json TEXT NOT NULL DEFAULT '[]',
    reconciliation_authored_text_digest TEXT,
    reconciliation_policy_json   TEXT,
    reconciliation_scope_json    TEXT,
    reconciliation_freshness     TEXT NOT NULL DEFAULT 'unknown',
    priority_rank                INTEGER NOT NULL DEFAULT 60,
    priority_basis_json          TEXT NOT NULL DEFAULT '["standard_actionable_candidate"]',
    human_lane                   TEXT NOT NULL DEFAULT 'low_confidence_review',
    management_state             TEXT NOT NULL DEFAULT 'open',
    management_action            TEXT,
    management_action_id         TEXT,
    management_revision          INTEGER NOT NULL DEFAULT 0,
    management_event_id          TEXT,
    management_reason_codes_json TEXT NOT NULL DEFAULT '[]',
    snoozed_until                TEXT,
    learned_policy_state         TEXT NOT NULL DEFAULT 'none',
    learned_rule_id              TEXT,
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
    confirmation_event_id        TEXT,
    confirmation_request_id      TEXT,
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
CREATE TABLE IF NOT EXISTS triage_management_actions (
    tenant_id             TEXT NOT NULL,
    action_id             TEXT NOT NULL,
    proposal_id           TEXT NOT NULL,
    member_id             TEXT NOT NULL,
    action                TEXT NOT NULL,
    action_revision       INTEGER NOT NULL,
    supersedes_action_id  TEXT,
    reason_codes_json     TEXT NOT NULL DEFAULT '[]',
    evidence_event_ids_json TEXT NOT NULL DEFAULT '[]',
    linked_object_kind    TEXT,
    linked_object_id      TEXT,
    duplicate_of_proposal_id TEXT,
    snoozed_until         TEXT,
    policy_proposal_id    TEXT,
    reversible            INTEGER NOT NULL CHECK (reversible=1),
    provider_effects      INTEGER NOT NULL CHECK (provider_effects=0),
    managed_at            TEXT NOT NULL,
    owner_scope           TEXT NOT NULL,
    visibility_scope      TEXT NOT NULL,
    sensitivity           TEXT NOT NULL,
    last_event_id         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, action_id),
    UNIQUE (tenant_id, proposal_id, action_revision)
);
CREATE INDEX IF NOT EXISTS idx_triage_management_member
    ON triage_management_actions(tenant_id, member_id, proposal_id, action_revision);

CREATE TABLE IF NOT EXISTS triage_learned_policy_rules (
    tenant_id             TEXT NOT NULL,
    rule_id               TEXT NOT NULL,
    policy_proposal_id    TEXT NOT NULL,
    member_id             TEXT NOT NULL,
    scope_kind            TEXT NOT NULL,
    scope_value_digest    TEXT NOT NULL,
    outcome_kind          TEXT NOT NULL,
    instruction           TEXT NOT NULL,
    activation_mode       TEXT NOT NULL CHECK (activation_mode IN ('approved','trial')),
    trial_proposal_ids_json TEXT NOT NULL DEFAULT '[]',
    status                TEXT NOT NULL CHECK (
        status IN ('active','trial','suspended','revoked')
    ),
    last_event_id         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, rule_id)
);
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

CREATE TABLE IF NOT EXISTS triage_attention_rules (
    tenant_id TEXT NOT NULL, rule_id TEXT NOT NULL, member_id TEXT NOT NULL,
    conditions_json TEXT NOT NULL, exceptions_json TEXT NOT NULL,
    action TEXT NOT NULL, origin TEXT NOT NULL, feedback_event_ids_json TEXT NOT NULL,
    preview_digest TEXT NOT NULL, mode TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','trial','shadow','expired','suspended','revoked')),
    starts_at TEXT NOT NULL, expires_at TEXT, max_observations INTEGER,
    observed_count INTEGER NOT NULL DEFAULT 0, created_event_id TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_attention_rules_member
    ON triage_attention_rules(tenant_id, member_id, status);
CREATE TABLE IF NOT EXISTS triage_attention_observations (
    tenant_id TEXT NOT NULL, rule_id TEXT NOT NULL, observation_key TEXT NOT NULL,
    source_event_id TEXT NOT NULL, proposal_id TEXT NOT NULL, observed_at TEXT NOT NULL,
    match_kind TEXT NOT NULL, event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, rule_id, observation_key)
);
CREATE TABLE IF NOT EXISTS triage_attention_feedback (
    tenant_id TEXT NOT NULL, feedback_id TEXT NOT NULL, member_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
    observation_key TEXT NOT NULL, kind TEXT NOT NULL, rule_json TEXT,
    recorded_at TEXT NOT NULL, last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, feedback_id)
);
CREATE TABLE IF NOT EXISTS triage_attention_rule_history (
    tenant_id TEXT NOT NULL, rule_id TEXT NOT NULL, event_id TEXT NOT NULL,
    decision TEXT NOT NULL, reason TEXT NOT NULL, occurred_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, rule_id, event_id)
);
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
-- Durable event-derived cutover barrier; resetting/rebuilding replays its epoch.
CREATE TABLE IF NOT EXISTS triage_communication_intake_policy (
    tenant_id TEXT PRIMARY KEY,
    policy_event_id TEXT NOT NULL
);
