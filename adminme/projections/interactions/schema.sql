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
    thread_id          TEXT,
    raw_event_ids      TEXT NOT NULL,          -- JSON array
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL DEFAULT 'normal'
                       CHECK (sensitivity IN ('normal','sensitive','privileged')),
    response_urgency   TEXT,
    suggested_action   TEXT,
    auto_handled       INTEGER NOT NULL DEFAULT 0,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, interaction_id)
);

CREATE INDEX IF NOT EXISTS idx_interactions_tenant_occurred
    ON interactions(tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_interactions_tenant_thread
    ON interactions(tenant_id, thread_id);

CREATE TABLE IF NOT EXISTS interaction_participants (
    tenant_id          TEXT NOT NULL,
    interaction_id     TEXT NOT NULL,
    party_id           TEXT NOT NULL,
    role               TEXT NOT NULL,          -- 'from' | 'to' | 'cc' | 'bcc' | 'mentioned'
    PRIMARY KEY (tenant_id, interaction_id, party_id, role)
);

CREATE INDEX IF NOT EXISTS idx_iparts_tenant_party
    ON interaction_participants(tenant_id, party_id);

-- Artifact links stay empty in this projection for v1. Prompt 06 populates
-- via the artifacts projection; the table is present now to avoid a schema
-- migration when that wiring lands.
CREATE TABLE IF NOT EXISTS interaction_attachments (
    tenant_id          TEXT NOT NULL,
    interaction_id     TEXT NOT NULL,
    artifact_id        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, interaction_id, artifact_id)
);
