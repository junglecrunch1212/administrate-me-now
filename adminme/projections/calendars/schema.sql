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
    external_uid      TEXT NOT NULL,
    owner_party       TEXT,                   -- REFERENCES parties(party_id)
    summary           TEXT,
    description       TEXT,
    location          TEXT,
    start_at          TEXT NOT NULL,
    end_at            TEXT NOT NULL,
    all_day           INTEGER NOT NULL DEFAULT 0,
    attendees_json    TEXT NOT NULL DEFAULT '[]',
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
