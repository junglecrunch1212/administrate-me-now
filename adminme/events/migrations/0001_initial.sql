-- 0001 initial event log schema.
-- Per ADMINISTRATEME_BUILD.md §"L2: THE EVENT LOG" and the prompt-03 spec.
-- Schema is the minimal MVP used by prompt 03; typed-payload columns land in
-- prompt 04 (see ADMINISTRATEME_BUILD.md §L2 full schema).

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    event_at_ms     INTEGER NOT NULL,
    tenant_id       TEXT NOT NULL,
    owner_scope     TEXT NOT NULL,
    type            TEXT NOT NULL,
    version         INTEGER NOT NULL,
    correlation_id  TEXT,
    source          TEXT,
    payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_owner_scope_time
    ON events (owner_scope, event_at_ms);

CREATE INDEX IF NOT EXISTS idx_events_type_time
    ON events (type, event_at_ms);

CREATE INDEX IF NOT EXISTS idx_events_correlation
    ON events (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Append-only enforcement via triggers. Per SYSTEM_INVARIANTS.md §1 invariant 2,
-- the log rejects UPDATE/DELETE against `events` at the storage layer.
CREATE TRIGGER IF NOT EXISTS trg_events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only');
END;
