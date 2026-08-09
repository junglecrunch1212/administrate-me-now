-- Recurrences projection schema — RFC 5545 RRULE templates.
--
-- Per ADMINISTRATEME_BUILD.md §3.6 and SYSTEM_INVARIANTS.md §4.
--
-- [§4.5] recurrence firing does NOT auto-create tasks. Occurrence
--   materialization is a pipeline concern (prompt 10c reminder_dispatch).
-- [§4.6] recurrences never complete commitments — task completion logic
--   is the only path for commitment fulfillment.
--
-- No cross-DB FK enforcement (per §2.3); linked_kind + linked_id is a
-- polymorphic pointer into parties/assets/accounts/household.

CREATE TABLE IF NOT EXISTS recurrences (
    recurrence_id    TEXT NOT NULL,
    tenant_id        TEXT NOT NULL,
    linked_kind      TEXT NOT NULL
                     CHECK (linked_kind IN ('party','asset','account','household')),
    linked_id        TEXT NOT NULL,
    kind             TEXT NOT NULL,
    rrule            TEXT NOT NULL,
    next_occurrence  TEXT NOT NULL,
    lead_time_days   INTEGER NOT NULL DEFAULT 0,
    trackable        INTEGER NOT NULL DEFAULT 0,
    notes            TEXT,
    status           TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','paused','ended')),
    lifecycle_at     TEXT,
    lifecycle_reason TEXT,
    owner_scope      TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity      TEXT NOT NULL DEFAULT 'normal'
                     CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, recurrence_id)
);

CREATE TABLE IF NOT EXISTS recurrence_occurrences (
    tenant_id             TEXT NOT NULL,
    recurrence_id         TEXT NOT NULL,
    occurrence_date       TEXT NOT NULL,
    outcome               TEXT NOT NULL CHECK (outcome IN ('completed','skipped')),
    recorded_at           TEXT NOT NULL,
    recorded_by_member_id TEXT,
    last_event_id         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, recurrence_id, occurrence_date)
);

CREATE INDEX IF NOT EXISTS idx_recurrences_tenant_next
    ON recurrences(tenant_id, next_occurrence);
CREATE INDEX IF NOT EXISTS idx_recurrences_tenant_linked
    ON recurrences(tenant_id, linked_kind, linked_id);

-- Ordered routines live beside recurrence templates because they share
-- calendar recurrence semantics, but they remain a distinct aggregate with
-- their own definition and occurrence outcome events.
CREATE TABLE IF NOT EXISTS routines (
    routine_id         TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    member_id          TEXT NOT NULL,
    title              TEXT NOT NULL,
    timezone           TEXT NOT NULL,
    starts_on          TEXT NOT NULL,
    rrule              TEXT NOT NULL,
    window_start_local TEXT NOT NULL,
    window_end_local   TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active','paused','ended')),
    lifecycle_at       TEXT,
    lifecycle_reason   TEXT,
    owner_scope        TEXT NOT NULL,
    visibility_scope   TEXT NOT NULL,
    sensitivity        TEXT NOT NULL DEFAULT 'normal'
                       CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, routine_id)
);

CREATE TABLE IF NOT EXISTS routine_steps (
    tenant_id      TEXT NOT NULL,
    routine_id     TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    position       INTEGER NOT NULL CHECK (position >= 0),
    title          TEXT NOT NULL,
    micro_step     TEXT,
    linked_task_id TEXT,
    last_event_id  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, routine_id, step_id),
    UNIQUE (tenant_id, routine_id, position),
    FOREIGN KEY (tenant_id, routine_id)
        REFERENCES routines(tenant_id, routine_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS routine_occurrences (
    tenant_id       TEXT NOT NULL,
    routine_id      TEXT NOT NULL,
    occurrence_date TEXT NOT NULL,
    status          TEXT NOT NULL
                    CHECK (status IN ('in_progress','completed','completed_with_skips')),
    completed_steps INTEGER NOT NULL DEFAULT 0,
    skipped_steps   INTEGER NOT NULL DEFAULT 0,
    last_event_id   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, routine_id, occurrence_date),
    FOREIGN KEY (tenant_id, routine_id)
        REFERENCES routines(tenant_id, routine_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS routine_step_outcomes (
    tenant_id            TEXT NOT NULL,
    routine_id           TEXT NOT NULL,
    occurrence_date      TEXT NOT NULL,
    step_id              TEXT NOT NULL,
    outcome              TEXT NOT NULL CHECK (outcome IN ('completed','skipped')),
    recorded_at          TEXT NOT NULL,
    recorded_by_member_id TEXT NOT NULL,
    last_event_id        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, routine_id, occurrence_date, step_id),
    FOREIGN KEY (tenant_id, routine_id, step_id)
        REFERENCES routine_steps(tenant_id, routine_id, step_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_routines_tenant_member
    ON routines(tenant_id, member_id);
CREATE INDEX IF NOT EXISTS idx_routine_outcomes_occurrence
    ON routine_step_outcomes(tenant_id, routine_id, occurrence_date);
CREATE INDEX IF NOT EXISTS idx_recurrence_outcomes_occurrence
    ON recurrence_occurrences(tenant_id, recurrence_id, occurrence_date);
