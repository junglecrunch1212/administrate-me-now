CREATE TABLE IF NOT EXISTS member_knowledge (
    tenant_id          TEXT NOT NULL,
    knowledge_id       TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL
                       CHECK (sensitivity IN ('normal','sensitive','privileged')),
    title              TEXT,
    body               TEXT NOT NULL,
    body_format        TEXT NOT NULL DEFAULT 'plain',
    source_kind        TEXT NOT NULL,
    source_external_id TEXT NOT NULL,
    account_external_id TEXT,
    account_name       TEXT,
    folder_external_id TEXT,
    folder_path        TEXT,
    content_sha256     TEXT NOT NULL,
    captured_at        TEXT NOT NULL,
    provider_created_at TEXT,
    provider_modified_at TEXT,
    provider_revision  TEXT,
    locked             INTEGER NOT NULL DEFAULT 0,
    shared             INTEGER NOT NULL DEFAULT 0,
    pinned             INTEGER,
    tags_json          TEXT,
    attachments_json   TEXT NOT NULL DEFAULT '[]',
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    capability_warnings_json TEXT NOT NULL DEFAULT '[]',
    supersedes_event_id TEXT,
    source_adapter     TEXT NOT NULL,
    source_account_id  TEXT NOT NULL,
    correlation_id     TEXT,
    causation_id       TEXT,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, knowledge_id)
);

CREATE INDEX IF NOT EXISTS idx_member_knowledge_owner
    ON member_knowledge(tenant_id, member_id, sensitivity, captured_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_member_knowledge_source
    ON member_knowledge(tenant_id, member_id, source_kind, source_external_id);

CREATE TABLE IF NOT EXISTS member_knowledge_folders (
    tenant_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    account_external_id TEXT,
    folder_external_id TEXT NOT NULL,
    folder_path TEXT NOT NULL,
    parent_folder_external_id TEXT,
    changed_at TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, member_id, source_kind, folder_external_id)
);
