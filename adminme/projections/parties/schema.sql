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
    qualification     TEXT NOT NULL DEFAULT 'qualified'
                      CHECK (qualification IN ('qualified','unconfirmed_endpoint','archived')),
    source_kind       TEXT NOT NULL DEFAULT 'domain',
    updated_at_ms     INTEGER NOT NULL DEFAULT 0,
    archived_at       TEXT,
    merged_into_party_id TEXT,
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
    label             TEXT,
    source_kind       TEXT NOT NULL DEFAULT 'domain',
    source_record_id  TEXT,
    owner_scope       TEXT NOT NULL DEFAULT 'shared:household',
    visibility_scope  TEXT NOT NULL DEFAULT 'shared:household',
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    revoked_at        TEXT,
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, identifier_id)
);

-- Exact-match merge (BUILD.md §3.1) — the unique key is
-- (tenant_id, kind, value_normalized). Cross-tenant identity collision is
-- allowed per §3 invariant 2.
CREATE INDEX IF NOT EXISTS idx_identifiers_tenant_kind_value
    ON identifiers(tenant_id, kind, value_normalized);
CREATE INDEX IF NOT EXISTS idx_identifiers_tenant_party
    ON identifiers(tenant_id, party_id);
CREATE INDEX IF NOT EXISTS idx_identifiers_scope
    ON identifiers(tenant_id, owner_scope, revoked_at);

CREATE TABLE IF NOT EXISTS memberships (
    membership_id     TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    party_id          TEXT NOT NULL,
    parent_party_id   TEXT NOT NULL,
    role              TEXT NOT NULL,
    started_at        TEXT,
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    owner_scope       TEXT NOT NULL DEFAULT 'shared:household',
    visibility_scope  TEXT NOT NULL DEFAULT 'shared:household',
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    ended_at          TEXT,
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
    owner_scope       TEXT NOT NULL DEFAULT 'shared:household',
    visibility_scope  TEXT NOT NULL DEFAULT 'shared:household',
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    ended_at          TEXT,
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, relationship_id)
);

CREATE INDEX IF NOT EXISTS idx_relationships_tenant_a
    ON relationships(tenant_id, party_a);
CREATE INDEX IF NOT EXISTS idx_relationships_tenant_b
    ON relationships(tenant_id, party_b);

CREATE TABLE IF NOT EXISTS people_endpoints (
    endpoint_id        TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    kind               TEXT NOT NULL,
    value              TEXT NOT NULL,
    value_normalized   TEXT NOT NULL,
    classification     TEXT NOT NULL
                       CHECK (classification IN ('unresolved','person','organization','service','automated')),
    provider           TEXT,
    provider_account_id TEXT,
    source_interaction_id TEXT,
    observed_at        TEXT NOT NULL,
    evidence_json      TEXT NOT NULL DEFAULT '[]',
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL
                       CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, endpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_people_endpoints_lookup
    ON people_endpoints(tenant_id, member_id, kind, value_normalized);

CREATE TABLE IF NOT EXISTS contact_records (
    contact_record_id  TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    party_id           TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    provider           TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    container_external_id TEXT NOT NULL,
    external_id        TEXT NOT NULL,
    provider_revision  TEXT,
    kind               TEXT NOT NULL,
    display_name       TEXT NOT NULL,
    sort_name          TEXT NOT NULL,
    given_name         TEXT,
    family_name        TEXT,
    nickname           TEXT,
    organization_name  TEXT,
    job_title          TEXT,
    fields_json        TEXT NOT NULL DEFAULT '{}',
    groups_json        TEXT NOT NULL DEFAULT '[]',
    source_url         TEXT,
    observed_at        TEXT NOT NULL,
    capability_warnings_json TEXT NOT NULL DEFAULT '[]',
    deleted_at         TEXT,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL
                       CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, contact_record_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_contact_records_provider
    ON contact_records(
        tenant_id, member_id, provider, provider_account_id,
        container_external_id, external_id
    );
CREATE INDEX IF NOT EXISTS idx_contact_records_party
    ON contact_records(tenant_id, party_id, deleted_at);

CREATE TABLE IF NOT EXISTS contact_groups (
    group_record_id    TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    provider           TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    container_external_id TEXT NOT NULL,
    external_id        TEXT NOT NULL,
    name               TEXT NOT NULL,
    provider_revision  TEXT,
    observed_at        TEXT NOT NULL,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, group_record_id)
);

CREATE TABLE IF NOT EXISTS member_relationship_profiles (
    profile_id         TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    party_id           TEXT NOT NULL,
    circle             TEXT,
    desired_cadence_days INTEGER,
    cues_enabled       INTEGER NOT NULL DEFAULT 0,
    favorite           INTEGER NOT NULL DEFAULT 0,
    suppressed         INTEGER NOT NULL DEFAULT 0,
    suppression_reason TEXT,
    tags_json          TEXT NOT NULL DEFAULT '[]',
    changed_at         TEXT NOT NULL,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, profile_id),
    UNIQUE (tenant_id, member_id, party_id)
);

CREATE TABLE IF NOT EXISTS relationship_facts (
    fact_id            TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    party_id           TEXT NOT NULL,
    kind               TEXT NOT NULL,
    label              TEXT,
    value              TEXT NOT NULL,
    effective_date     TEXT,
    source_event_id    TEXT NOT NULL,
    evidence_quote     TEXT,
    confirmed_at       TEXT NOT NULL,
    confirmed_by_member_id TEXT NOT NULL,
    revoked_at         TEXT,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, fact_id)
);

CREATE INDEX IF NOT EXISTS idx_relationship_facts_party
    ON relationship_facts(tenant_id, member_id, party_id, revoked_at);

CREATE TABLE IF NOT EXISTS party_access_grants (
    grant_id           TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    party_id           TEXT NOT NULL,
    owner_member_id    TEXT NOT NULL,
    grantee_member_id  TEXT NOT NULL,
    field_groups_json  TEXT NOT NULL,
    granted_at         TEXT NOT NULL,
    expires_at         TEXT,
    revoked_at         TEXT,
    revoked_by_member_id TEXT,
    owner_scope        TEXT NOT NULL,
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, grant_id)
);

CREATE INDEX IF NOT EXISTS idx_party_access_grantee
    ON party_access_grants(tenant_id, grantee_member_id, party_id, revoked_at);

CREATE VIRTUAL TABLE IF NOT EXISTS people_fts USING fts5(
    tenant_id UNINDEXED,
    party_id UNINDEXED,
    audience_member_id UNINDEXED,
    display_name,
    sort_name,
    nickname,
    organization,
    relationship_terms
);
