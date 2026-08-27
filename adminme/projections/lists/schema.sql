-- Lists projection schema — provider mirrors and contextual reminder cues.
--
-- Per ADMINISTRATEME_BUILD.md §L3 and SYSTEM_INVARIANTS.md §2.
--
-- Subscribes to canonical list/list-item transitions plus scoped context
-- facts and member-authored cue rules. Provider facts remain authoritative;
-- cues affect only deterministic presentation/notification eligibility.
--
-- Composite PK ``(tenant_id, list_external_id, item_external_id)``
-- guards against ``item_external_id`` collisions across source
-- namespaces (Apple Reminders ``EKReminder.calendarItemIdentifier`` +
-- Google Tasks ``task["id"]`` are distinct but their byte format may
-- overlap). ``list_external_id`` distinguishes by source-list.
--
-- Priority: EventKit ordinal scale [BUILD.md APPLE REMINDERS BIDIRECTIONAL]:
--   0 = none, 1 = high, 5 = medium, 9 = low.

CREATE TABLE IF NOT EXISTS list_items (
    tenant_id         TEXT NOT NULL,
    list_external_id  TEXT NOT NULL,
    item_external_id  TEXT NOT NULL,
    member_id         TEXT NOT NULL,
    provider          TEXT,
    provider_account_id TEXT,
    list_name         TEXT NOT NULL,
    list_color        TEXT,
    title             TEXT NOT NULL,
    notes             TEXT,
    url               TEXT,
    due_at            TEXT,
    priority          INTEGER NOT NULL CHECK (priority IN (0, 1, 5, 9)),
    completed         INTEGER NOT NULL DEFAULT 0,
    completion_at     TEXT,
    start_json        TEXT,
    due_json          TEXT,
    alarms_json       TEXT NOT NULL DEFAULT '[]',
    recurrence_json   TEXT NOT NULL DEFAULT '[]',
    flagged           INTEGER,
    tags_json         TEXT,
    parent_external_id TEXT,
    sort_order        INTEGER,
    provider_position TEXT,
    source_links_json TEXT NOT NULL DEFAULT '[]',
    provider_data_json TEXT NOT NULL DEFAULT '{}',
    read_only_reason  TEXT,
    linked_item_kind  TEXT,
    linked_item_id    TEXT,
    completion_policy TEXT NOT NULL DEFAULT 'manual',
    provider_revision TEXT,
    last_modified_at  TEXT NOT NULL,
    sync_cycle_id     TEXT NOT NULL,
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, list_external_id, item_external_id)
);

CREATE INDEX IF NOT EXISTS idx_list_items_tenant_member
    ON list_items(tenant_id, member_id);
CREATE INDEX IF NOT EXISTS idx_list_items_tenant_list
    ON list_items(tenant_id, list_external_id);
CREATE INDEX IF NOT EXISTS idx_list_items_tenant_completed
    ON list_items(tenant_id, completed);
CREATE INDEX IF NOT EXISTS idx_list_items_tenant_due
    ON list_items(tenant_id, due_at);

-- One native iCloud-shared item may be observed independently by several
-- member bridges.  The canonical ``list_items`` row remains collapsed by the
-- provider external ids, while this table retains every exact member/account
-- observation so projection updates never erase provenance.
CREATE TABLE IF NOT EXISTS list_item_observations (
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    list_external_id TEXT NOT NULL,
    item_external_id TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, provider, provider_account_id, member_id,
        list_external_id, item_external_id
    )
);

CREATE INDEX IF NOT EXISTS idx_list_item_observations_canonical
    ON list_item_observations(tenant_id, provider, list_external_id, item_external_id);

CREATE TABLE IF NOT EXISTS lists (
    tenant_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    list_external_id TEXT NOT NULL,
    list_name TEXT NOT NULL,
    color TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    provider_revision TEXT,
    changed_at TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, provider, provider_account_id, list_external_id)
);

CREATE INDEX IF NOT EXISTS idx_lists_tenant_member
    ON lists(tenant_id, member_id, provider);

CREATE TABLE IF NOT EXISTS list_observations (
    tenant_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    list_external_id TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, provider, provider_account_id, member_id, list_external_id
    )
);

CREATE INDEX IF NOT EXISTS idx_list_observations_canonical
    ON list_observations(tenant_id, provider, list_external_id);

CREATE TABLE IF NOT EXISTS reminder_cues (
    tenant_id TEXT NOT NULL,
    cue_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    list_external_id TEXT NOT NULL,
    item_external_id TEXT NOT NULL,
    signal_key TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    operator TEXT NOT NULL,
    expected_state TEXT,
    expected_number REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    configured_at TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, cue_id)
);

CREATE INDEX IF NOT EXISTS idx_reminder_cues_member_item
    ON reminder_cues(tenant_id, member_id, list_external_id, item_external_id);

CREATE TABLE IF NOT EXISTS context_signals (
    tenant_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    signal_key TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    numeric_value REAL,
    unit TEXT,
    latitude REAL,
    longitude REAL,
    accuracy_meters REAL,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, member_id, signal_key)
);

CREATE INDEX IF NOT EXISTS idx_context_signals_member_kind
    ON context_signals(tenant_id, member_id, kind, observed_at);
