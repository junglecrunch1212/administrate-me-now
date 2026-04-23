-- places_assets_accounts projection schema — three linked entity families.
--
-- Per ADMINISTRATEME_BUILD.md §3.8 and SYSTEM_INVARIANTS.md §2, §12.
--
-- Cross-DB FK references (linked_place → places; organization/party_id →
-- parties; linked_asset → assets) are documentation only; SQLite cannot
-- enforce FKs across separate projection DBs. Integrity preserved by
-- upstream pipelines per [§2.3].
--
-- Convention for CHECK constraints per phase 07a CF-6: CHECK is applied
-- on closed-enum columns (places.kind, assets.kind, accounts.kind,
-- accounts.status, sensitivity). Open columns (display_name, notes)
-- receive no CHECK.
--
-- [§12] login_vault_ref MUST be a vault pointer, never a raw credential.
-- The CHECK constraint catches accidental writes. Real defense is the
-- adapter that emits account.added.
--
-- place_associations and asset_owners ship here with their schemas so
-- prompt 07b's xlsx does not have to migrate later, but they are not
-- populated by this prompt's handlers — dedicated event types will land
-- in a future prompt.

CREATE TABLE IF NOT EXISTS places (
    place_id          TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN (
                          'home','second_home','office','school','medical',
                          'gym','church','cemetery','storage','other')),
    address_json      TEXT NOT NULL,
    geo_lat           REAL,
    geo_lon           REAL,
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, place_id)
);

CREATE INDEX IF NOT EXISTS idx_places_tenant_kind
    ON places(tenant_id, kind);

CREATE TABLE IF NOT EXISTS place_associations (
    tenant_id         TEXT NOT NULL,
    place_id          TEXT NOT NULL,                -- doc-only → places(place_id)
    party_id          TEXT NOT NULL,                -- doc-only → parties(party_id)
    role              TEXT NOT NULL,                -- 'resident','owner','tenant','visitor','other'
    since             TEXT,
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, place_id, party_id, role)
);

CREATE INDEX IF NOT EXISTS idx_place_associations_tenant_party
    ON place_associations(tenant_id, party_id);

CREATE TABLE IF NOT EXISTS assets (
    asset_id          TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN (
                          'vehicle','appliance','instrument','boat',
                          'firearm','pet','other')),
    linked_place      TEXT,                          -- doc-only → places(place_id)
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_assets_tenant_kind
    ON assets(tenant_id, kind);
CREATE INDEX IF NOT EXISTS idx_assets_tenant_place
    ON assets(tenant_id, linked_place);

CREATE TABLE IF NOT EXISTS asset_owners (
    tenant_id         TEXT NOT NULL,
    asset_id          TEXT NOT NULL,                -- doc-only → assets(asset_id)
    party_id          TEXT NOT NULL,                -- doc-only → parties(party_id)
    share_pct         REAL,
    since             TEXT,
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, asset_id, party_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_owners_tenant_party
    ON asset_owners(tenant_id, party_id);

CREATE TABLE IF NOT EXISTS accounts (
    account_id        TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    organization      TEXT NOT NULL,                 -- doc-only → parties(party_id)
    kind              TEXT NOT NULL CHECK (kind IN (
                          'utility','subscription','insurance','license',
                          'bank','credit_card','loan','brokerage','other')),
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','dormant','cancelled','pending')),
    billing_rrule     TEXT,
    next_renewal      TEXT,
    login_vault_ref   TEXT
                      CHECK (login_vault_ref IS NULL
                             OR login_vault_ref LIKE 'op://%'
                             OR login_vault_ref LIKE '1password://%'
                             OR login_vault_ref LIKE 'vault://%'),
    linked_asset      TEXT,                          -- doc-only → assets(asset_id)
    linked_place      TEXT,                          -- doc-only → places(place_id)
    attributes_json   TEXT NOT NULL DEFAULT '{}',
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_accounts_tenant_kind
    ON accounts(tenant_id, kind);
CREATE INDEX IF NOT EXISTS idx_accounts_tenant_status
    ON accounts(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_accounts_tenant_renewal
    ON accounts(tenant_id, next_renewal);
CREATE INDEX IF NOT EXISTS idx_accounts_tenant_org
    ON accounts(tenant_id, organization);
