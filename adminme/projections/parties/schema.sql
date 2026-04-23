-- Parties projection schema — CRM spine.
--
-- Per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3. Subscribed
-- event types: party.created, identifier.added, membership.added,
-- relationship.added. (party.merged arrives in prompt 10b.)
--
-- Rules:
-- - (tenant_id, party_id) is the unique identity — no cross-tenant identity
--   resolution (§3 invariant 2, §12 tenant isolation).
-- - identifiers.value_normalized is canonicalized upstream (E.164 phones,
--   lowercased emails) for exact-match merge.
-- - No hardcoded tenant data in this schema (§12 invariant 4).
-- - last_event_id columns carry the event_id that most recently wrote the
--   row — used during rebuild to short-circuit idempotent re-application
--   of the same event (and for debugging).

CREATE TABLE IF NOT EXISTS parties (
    party_id          TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN ('person','organization','household')),
    display_name      TEXT NOT NULL,
    sort_name         TEXT NOT NULL,
    nickname          TEXT,
    pronouns          TEXT,
    notes             TEXT,
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    created_at_ms     INTEGER NOT NULL,
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, party_id)
);

CREATE INDEX IF NOT EXISTS idx_parties_tenant_kind
    ON parties(tenant_id, kind);
CREATE INDEX IF NOT EXISTS idx_parties_tenant_sort
    ON parties(tenant_id, sort_name);

CREATE TABLE IF NOT EXISTS identifiers (
    identifier_id     TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    party_id          TEXT NOT NULL,
    kind              TEXT NOT NULL,
    value             TEXT NOT NULL,
    value_normalized  TEXT NOT NULL,
    verified          INTEGER NOT NULL DEFAULT 0,
    primary_for_kind  INTEGER NOT NULL DEFAULT 0,
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, identifier_id)
);

-- Exact-match merge (BUILD.md §3.1) — the unique key is
-- (tenant_id, kind, value_normalized). Cross-tenant identity collision is
-- allowed per §3 invariant 2.
CREATE UNIQUE INDEX IF NOT EXISTS idx_identifiers_tenant_kind_value
    ON identifiers(tenant_id, kind, value_normalized);
CREATE INDEX IF NOT EXISTS idx_identifiers_tenant_party
    ON identifiers(tenant_id, party_id);

CREATE TABLE IF NOT EXISTS memberships (
    membership_id     TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    party_id          TEXT NOT NULL,
    parent_party_id   TEXT NOT NULL,
    role              TEXT NOT NULL,
    started_at        TEXT,
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, membership_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_tenant_parent
    ON memberships(tenant_id, parent_party_id);
CREATE INDEX IF NOT EXISTS idx_memberships_tenant_party
    ON memberships(tenant_id, party_id);

CREATE TABLE IF NOT EXISTS relationships (
    relationship_id   TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    party_a           TEXT NOT NULL,
    party_b           TEXT NOT NULL,
    label             TEXT NOT NULL,
    direction         TEXT NOT NULL CHECK (direction IN ('a_to_b','b_to_a','mutual')),
    since             TEXT,
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, relationship_id)
);

CREATE INDEX IF NOT EXISTS idx_relationships_tenant_a
    ON relationships(tenant_id, party_a);
CREATE INDEX IF NOT EXISTS idx_relationships_tenant_b
    ON relationships(tenant_id, party_b);
