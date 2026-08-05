-- Calendars projection schema — external calendar events + availability.
--
-- Per ADMINISTRATEME_BUILD.md §3.7 and SYSTEM_INVARIANTS.md §5.
--
-- [§5.1] calendars projection is populated by external adapters;
--   AdministrateMe does not write back to external providers.
-- [§5.2] modifications made inside AdministrateMe (if any) never round-
--   trip to external calendars.
-- [§5.3] tasks.due_date does NOT create calendar events. These
--   projections are merged only at read time in surface layers.
--
-- UNIQUE(calendar_source, external_uid) is load-bearing: it lets
-- adapters replay polling cycles idempotently (prompt 11).

CREATE TABLE IF NOT EXISTS calendar_events (
    calendar_event_id TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    calendar_source   TEXT NOT NULL,
    provider_account_id TEXT,
    member_id         TEXT,
    calendar_external_id TEXT,
    calendar_name     TEXT,
    external_uid      TEXT NOT NULL,
    ical_uid          TEXT,
    provider_revision TEXT,
    owner_party       TEXT,                   -- REFERENCES parties(party_id)
    summary           TEXT,
    description       TEXT,
    location          TEXT,
    structured_location_json TEXT,
    start_at          TEXT NOT NULL,
    end_at            TEXT NOT NULL,
    all_day           INTEGER NOT NULL DEFAULT 0,
    start_time_zone   TEXT,
    end_time_zone     TEXT,
    status            TEXT NOT NULL DEFAULT 'confirmed',
    event_type        TEXT NOT NULL DEFAULT 'default',
    availability      TEXT NOT NULL DEFAULT 'busy',
    provider_visibility TEXT NOT NULL DEFAULT 'default',
    organizer_json    TEXT,
    attendees_json    TEXT NOT NULL DEFAULT '[]',
    alarms_json       TEXT NOT NULL DEFAULT '[]',
    use_default_alarms INTEGER NOT NULL DEFAULT 0,
    recurrence_json   TEXT NOT NULL DEFAULT '{}',
    sequence          INTEGER NOT NULL DEFAULT 0,
    conference_json   TEXT,
    attachments_json  TEXT NOT NULL DEFAULT '[]',
    color_id          TEXT,
    source_url        TEXT,
    html_link         TEXT,
    linked_item_kind  TEXT,
    linked_item_id    TEXT,
    completion_policy TEXT NOT NULL DEFAULT 'manual',
    provider_created_at TEXT,
    provider_updated_at TEXT,
    privacy           TEXT NOT NULL DEFAULT 'open'
                      CHECK (privacy IN ('open','privileged','redacted')),
    title_redacted    TEXT,
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, calendar_event_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_events_tenant_source_uid
    ON calendar_events(tenant_id, calendar_source, external_uid);
CREATE INDEX IF NOT EXISTS idx_calendar_events_tenant_start
    ON calendar_events(tenant_id, start_at);
CREATE INDEX IF NOT EXISTS idx_calendar_events_tenant_owner
    ON calendar_events(tenant_id, owner_party);
CREATE INDEX IF NOT EXISTS idx_calendar_events_tenant_member
    ON calendar_events(tenant_id, member_id, start_at);

CREATE TABLE IF NOT EXISTS availability_blocks (
    availability_id  TEXT NOT NULL,
    tenant_id        TEXT NOT NULL,
    party_id         TEXT NOT NULL,           -- REFERENCES parties(party_id)
    start_at         TEXT NOT NULL,
    end_at           TEXT NOT NULL,
    source_adapter   TEXT NOT NULL,
    last_event_id    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, availability_id)
);

CREATE INDEX IF NOT EXISTS idx_avail_tenant_party_start
    ON availability_blocks(tenant_id, party_id, start_at);

CREATE TABLE IF NOT EXISTS calendar_event_outcomes (
    tenant_id        TEXT NOT NULL,
    member_id        TEXT NOT NULL,
    calendar_source  TEXT NOT NULL,
    external_uid     TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    attendance_status TEXT,
    evidence         TEXT NOT NULL,
    confidence       REAL NOT NULL,
    recorded_at      TEXT NOT NULL,
    linked_item_kind TEXT,
    linked_item_id   TEXT,
    owner_scope      TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity      TEXT NOT NULL,
    last_event_id    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, member_id, calendar_source, external_uid)
);
