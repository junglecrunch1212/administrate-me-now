-- Calendars projection schema — external calendar events + availability.
--
-- Per ADMINISTRATEME_BUILD.md §3.7 and SYSTEM_INVARIANTS.md §5.
--
-- [§5.1] calendar event rows are provider observations. Principals separately
--   record deterministic per-member calendar rules and schedule policy.
-- [§5.2] provider writes use the governed external-command seam; projection
--   rows are never mutated as a substitute for provider evidence.
-- [§5.3] tasks.due_date does NOT create calendar events. These
--   projections are merged only at read time in surface layers.
--
-- Provider event ids are unique only inside an account/calendar route. The
-- route-scoped unique index allows deliberate duplicate copies to coexist so
-- the read model can group them without deleting provenance.

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

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_events_tenant_route_uid
    ON calendar_events(
        tenant_id,
        calendar_source,
        ifnull(provider_account_id, ''),
        ifnull(calendar_external_id, ''),
        external_uid
    );
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

CREATE TABLE IF NOT EXISTS calendar_sources (
    tenant_id            TEXT NOT NULL,
    member_id            TEXT NOT NULL,
    provider              TEXT NOT NULL,
    provider_account_id   TEXT NOT NULL,
    calendar_external_id  TEXT NOT NULL,
    calendar_name         TEXT NOT NULL,
    access_role           TEXT NOT NULL,
    is_primary            INTEGER NOT NULL DEFAULT 0,
    selected              INTEGER NOT NULL DEFAULT 0,
    hidden                INTEGER NOT NULL DEFAULT 0,
    timezone              TEXT,
    color_id              TEXT,
    default_reminders_json TEXT NOT NULL DEFAULT '[]',
    read_details          INTEGER NOT NULL DEFAULT 0,
    read_free_busy        INTEGER NOT NULL DEFAULT 0,
    write_events          INTEGER NOT NULL DEFAULT 0,
    observed_at           TEXT NOT NULL,
    owner_scope           TEXT NOT NULL,
    visibility_scope      TEXT NOT NULL,
    sensitivity           TEXT NOT NULL,
    last_event_id         TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, provider, provider_account_id, calendar_external_id
    )
);

CREATE INDEX IF NOT EXISTS idx_calendar_sources_tenant_member
    ON calendar_sources(tenant_id, member_id, calendar_name);

CREATE TABLE IF NOT EXISTS calendar_member_rules (
    tenant_id            TEXT NOT NULL,
    rule_id              TEXT NOT NULL,
    subject_member_id    TEXT NOT NULL,
    source_member_id     TEXT NOT NULL,
    provider             TEXT NOT NULL,
    provider_account_id  TEXT NOT NULL,
    calendar_external_id TEXT NOT NULL,
    purpose              TEXT NOT NULL,
    effect               TEXT NOT NULL,
    tentative_effect     TEXT NOT NULL,
    caregiving_load      TEXT NOT NULL,
    caregiving_summary_values_json TEXT NOT NULL DEFAULT '[]',
    buffer_kind          TEXT NOT NULL DEFAULT 'auto',
    write_target         INTEGER NOT NULL DEFAULT 0,
    destination_kind     TEXT,
    enabled              INTEGER NOT NULL DEFAULT 1,
    configured_at        TEXT NOT NULL,
    owner_scope          TEXT NOT NULL,
    visibility_scope     TEXT NOT NULL,
    sensitivity          TEXT NOT NULL,
    last_event_id        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, rule_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_rules_tenant_subject_route
    ON calendar_member_rules(
        tenant_id, subject_member_id, provider, provider_account_id,
        calendar_external_id
    );

CREATE TABLE IF NOT EXISTS calendar_schedule_policies (
    tenant_id               TEXT NOT NULL,
    member_id               TEXT NOT NULL,
    timezone                TEXT NOT NULL,
    windows_json            TEXT NOT NULL,
    buffer_minutes_json     TEXT NOT NULL,
    accepted_chores_required INTEGER NOT NULL DEFAULT 1,
    one_confirm_write       INTEGER NOT NULL DEFAULT 1,
    configured_at           TEXT NOT NULL,
    owner_scope             TEXT NOT NULL,
    visibility_scope        TEXT NOT NULL,
    sensitivity             TEXT NOT NULL,
    last_event_id           TEXT NOT NULL,
    PRIMARY KEY (tenant_id, member_id)
);
