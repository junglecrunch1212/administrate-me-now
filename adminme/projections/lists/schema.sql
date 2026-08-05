-- Lists projection schema — 13th projection per [D18].
--
-- Per ADMINISTRATEME_BUILD.md §L3 and SYSTEM_INVARIANTS.md §2.
--
-- Subscribes to ``list_item.added@v1`` only. The other three list_item
-- event types (updated/completed/deleted) have no first-party producers
-- on main yet; declaring subscriptions without producers would break
-- rebuild correctness per UT-27-style discipline. Follow-up sub-prompts
-- (11b-i-β / 11b-iv-β) ship the producers and a paired 11f-β extends
-- subscriptions then.
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
