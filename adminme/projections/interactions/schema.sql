-- Interactions projection schema — deduplicated touchpoints.
--
-- Per ADMINISTRATEME_BUILD.md §3.2 and SYSTEM_INVARIANTS.md §3 invariant 5.
-- Subscribed event types (prompt 05): messaging.received, messaging.sent,
-- telephony.sms_received. Further channel_family values arrive with later
-- adapter prompts.
--
-- raw_event_ids is a JSON array; for v1 each row aggregates exactly one
-- raw event. Prompt 10b's noise_filtering pipeline may merge related
-- interactions into one row later; the column shape is ready for that.

CREATE TABLE IF NOT EXISTS interactions (
    interaction_id     TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    direction          TEXT NOT NULL CHECK (direction IN ('inbound','outbound','mutual')),
    channel_family     TEXT NOT NULL,
    channel_specific   TEXT NOT NULL,
    occurred_at        TEXT NOT NULL,
    subject            TEXT,
    summary            TEXT,
    body_ref           TEXT,
    body_text          TEXT,
    body_preview       TEXT,
    thread_id          TEXT,
    native_message_id  TEXT,
    native_chat_id     TEXT,
    native_revision    TEXT,
    native_revision_ms INTEGER NOT NULL DEFAULT 0,
    reply_to_native_message_id TEXT,
    transport_class    TEXT,
    is_group           INTEGER NOT NULL DEFAULT 0,
    message_state      TEXT NOT NULL DEFAULT 'active',
    bridge_enrollment_id TEXT,
    device_identity_sha256 TEXT,
    connector_id       TEXT,
    protected_source_id TEXT,
    transport_version  TEXT,
    transport_commit   TEXT,
    correlation_id     TEXT,
    member_id          TEXT,
    provider           TEXT,
    provider_account_id TEXT,
    workspace          TEXT NOT NULL DEFAULT 'personal'
                       CHECK (workspace IN ('personal','work')),
    unread             INTEGER NOT NULL DEFAULT 0,
    archived           INTEGER NOT NULL DEFAULT 0,
    starred            INTEGER NOT NULL DEFAULT 0,
    important          INTEGER NOT NULL DEFAULT 0,
    snoozed_until      TEXT,
    quieted            INTEGER NOT NULL DEFAULT 0,
    rfc_message_id     TEXT,
    reply_to_identifier TEXT,
    provider_labels    TEXT NOT NULL DEFAULT '[]',
    attachment_metadata TEXT NOT NULL DEFAULT '[]',
    assessment_outcome  TEXT,
    assessment_reason_codes TEXT NOT NULL DEFAULT '[]',
    command_state      TEXT,
    last_command_id    TEXT,
    delivery_status    TEXT,
    raw_event_ids      TEXT NOT NULL,          -- JSON array
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL DEFAULT 'normal'
                       CHECK (sensitivity IN ('normal','sensitive','privileged')),
    response_urgency   TEXT,
    suggested_action   TEXT,
    auto_handled       INTEGER NOT NULL DEFAULT 0,
    classification     TEXT,
    classification_confidence REAL,
    semantic_features_json TEXT NOT NULL DEFAULT '{}',
    semantic_confidence REAL,
    semantic_skill_name TEXT,
    semantic_skill_version TEXT,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, interaction_id)
);

CREATE INDEX IF NOT EXISTS idx_interactions_tenant_occurred
    ON interactions(tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_interactions_tenant_thread
    ON interactions(tenant_id, thread_id);
-- Indexes that depend on additive columns are created by migrate_schema()
-- after an existing projection has received those columns. Keeping them in
-- this bootstrap script would make SQLite resolve the new column names before
-- the migration hook runs and block an otherwise safe in-place upgrade.
CREATE TABLE IF NOT EXISTS interaction_participants (
    tenant_id          TEXT NOT NULL,
    interaction_id     TEXT NOT NULL,
    party_id           TEXT NOT NULL,
    role               TEXT NOT NULL,          -- 'from' | 'to' | 'cc' | 'bcc' | 'mentioned'
    PRIMARY KEY (tenant_id, interaction_id, party_id, role)
);

-- Provider endpoints are not Parties. They remain here until a separate
-- identity-resolution event proves an exact Party relationship.
CREATE TABLE IF NOT EXISTS interaction_source_participants (
    tenant_id          TEXT NOT NULL,
    interaction_id     TEXT NOT NULL,
    participant_id     TEXT NOT NULL,
    role               TEXT NOT NULL,
    is_self            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, interaction_id, participant_id, role)
);

CREATE INDEX IF NOT EXISTS idx_isparts_tenant_interaction
    ON interaction_source_participants(tenant_id, interaction_id);

CREATE INDEX IF NOT EXISTS idx_iparts_tenant_party
    ON interaction_participants(tenant_id, party_id);

CREATE TABLE IF NOT EXISTS service_interactions (
    tenant_id            TEXT NOT NULL,
    interaction_id       TEXT NOT NULL,
    service_principal_id TEXT NOT NULL,
    provider             TEXT NOT NULL,
    provider_account_id  TEXT NOT NULL,
    channel_id           TEXT NOT NULL,
    native_message_id    TEXT,
    subject              TEXT,
    body_text            TEXT,
    occurred_at          TEXT NOT NULL,
    trusted_route_key    TEXT,
    routing_scope        TEXT NOT NULL DEFAULT 'unresolved'
                         CHECK (routing_scope IN (
                             'member_private','household_shared',
                             'external_contact','unresolved'
                         )),
    routing_target_id    TEXT,
    owner_scope          TEXT NOT NULL,
    visibility_scope     TEXT NOT NULL,
    sensitivity          TEXT NOT NULL,
    last_event_id        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, interaction_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_service_interactions_native
    ON service_interactions(
        tenant_id, service_principal_id, provider,
        provider_account_id, native_message_id
    ) WHERE native_message_id IS NOT NULL;

-- Artifact links stay empty in this projection for v1. Prompt 06 populates
-- via the artifacts projection; the table is present now to avoid a schema
-- migration when that wiring lands.
CREATE TABLE IF NOT EXISTS interaction_attachments (
    tenant_id          TEXT NOT NULL,
    interaction_id     TEXT NOT NULL,
    artifact_id        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, interaction_id, artifact_id)
);
