-- vector_search projection schema.
--
-- Per ADMINISTRATEME_BUILD.md §3.10 and SYSTEM_INVARIANTS.md §6, §8, §13.8.
--
-- [§13.8] privileged content MUST NOT be embedded or stored in this index.
-- Handler filters at write time (belt); Session (prompt 08) enforces at
-- read time (braces).
-- [§8] AdministrateMe does not call embedding models directly. Vectors
-- in embedding.generated events are pre-computed by a separate embedding
-- daemon (future prompt) that calls OpenClaw's embedding endpoint.
--
-- vec0 is a sqlite-vec virtual table; the sqlite-vec extension must be
-- loaded at connection time (see VectorSearchProjection.on_connection_opened).

CREATE VIRTUAL TABLE IF NOT EXISTS vector_index USING vec0(
    embedding_id  TEXT PRIMARY KEY,
    embedding     float[1536],
    linked_kind   TEXT,
    linked_id     TEXT,
    sensitivity   TEXT,
    owner_scope   TEXT,
    tenant_id     TEXT
);

-- Sidecar for exact-match lookups + metadata. vec0 doesn't expose
-- per-row text columns for arbitrary query, so auxiliary data lives here.
CREATE TABLE IF NOT EXISTS embeddings_meta (
    tenant_id             TEXT NOT NULL,
    embedding_id          TEXT NOT NULL,
    linked_kind           TEXT NOT NULL,
    linked_id             TEXT NOT NULL,
    embedding_dimensions  INTEGER NOT NULL,
    model_name            TEXT NOT NULL,
    sensitivity           TEXT NOT NULL
                          CHECK (sensitivity IN ('normal','sensitive','privileged')),
    source_text_sha256    TEXT NOT NULL,
    created_at_ms         INTEGER NOT NULL,
    last_event_id         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, embedding_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_tenant_linked
    ON embeddings_meta(tenant_id, linked_kind, linked_id);
