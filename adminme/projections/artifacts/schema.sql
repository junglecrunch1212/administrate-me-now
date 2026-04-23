-- Artifacts projection schema — documents, images, structured records.
--
-- Per ADMINISTRATEME_BUILD.md §3.3 and SYSTEM_INVARIANTS.md §3 invariant 6.
--
-- artifact_links is polymorphic (artifact ⇄ party/asset/account/place/
-- interaction). Prompt 05 leaves it empty; prompt 06 populates as part of
-- domain-projection wiring.

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id        TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    mime_type          TEXT NOT NULL,
    byte_size          INTEGER NOT NULL,
    sha256             TEXT NOT NULL,
    source_adapter     TEXT NOT NULL,
    storage_ref        TEXT NOT NULL,
    title              TEXT,
    extracted_text     TEXT,
    extracted_structured_json TEXT,
    extracted_structured_kind TEXT,
    captured_at        TEXT NOT NULL,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL DEFAULT 'normal'
                       CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, artifact_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_tenant_sha256
    ON artifacts(tenant_id, sha256);
CREATE INDEX IF NOT EXISTS idx_artifacts_tenant_captured
    ON artifacts(tenant_id, captured_at);

CREATE TABLE IF NOT EXISTS artifact_links (
    tenant_id          TEXT NOT NULL,
    artifact_id        TEXT NOT NULL,
    linked_kind        TEXT NOT NULL,
    linked_id          TEXT NOT NULL,
    link_role          TEXT,
    PRIMARY KEY (tenant_id, artifact_id, linked_kind, linked_id, link_role)
);
