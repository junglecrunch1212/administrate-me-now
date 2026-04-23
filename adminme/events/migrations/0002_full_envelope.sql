-- 0002 full envelope migration.
-- Per ADMINISTRATEME_BUILD.md §"L2: THE EVENT LOG" (full 15-column shape) and
-- DECISIONS.md §D16 (additive migration from prompt 03's MVP).
--
-- The MVP shipped by 0001 has 9 columns: event_id, event_at_ms, tenant_id,
-- owner_scope, type, version, correlation_id, source, payload. This migration
-- adds the BUILD.md §L2 additions: schema_version, occurred_at, recorded_at,
-- source_adapter, source_account_id, visibility_scope, sensitivity,
-- causation_id, raw_ref, actor_identity. Every NOT NULL addition carries a
-- DEFAULT so existing rows (if any) back-fill cleanly.
--
-- The legacy `version` column is left in place as an alias for schema_version;
-- no new code reads it. A future migration may drop it once the codebase is
-- quiet on that name.
--
-- The append-only triggers are dropped for the duration of the backfill UPDATE
-- and re-created afterward. The drop/recreate is the supported pattern —
-- ALTER TABLE ADD COLUMN does not fire BEFORE UPDATE triggers, but the
-- explicit UPDATE does, and we need to set derived columns on pre-existing
-- rows. The final state is identical to 0001's trigger set.

DROP TRIGGER IF EXISTS trg_events_no_update;
DROP TRIGGER IF EXISTS trg_events_no_delete;

ALTER TABLE events ADD COLUMN schema_version    INTEGER NOT NULL DEFAULT 1;
ALTER TABLE events ADD COLUMN occurred_at       TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN recorded_at       TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN source_adapter    TEXT NOT NULL DEFAULT 'unknown:legacy';
ALTER TABLE events ADD COLUMN source_account_id TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE events ADD COLUMN visibility_scope  TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN sensitivity       TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE events ADD COLUMN causation_id      TEXT;
ALTER TABLE events ADD COLUMN raw_ref           TEXT;
ALTER TABLE events ADD COLUMN actor_identity    TEXT;

-- Backfill: any rows that pre-date 0002 use `version` as `schema_version`,
-- synthesize occurred_at / recorded_at from event_at_ms, and set
-- visibility_scope equal to owner_scope.
UPDATE events
SET schema_version = version
WHERE schema_version = 1 AND version != 1;

UPDATE events
SET occurred_at      = strftime('%Y-%m-%dT%H:%M:%fZ', event_at_ms / 1000.0, 'unixepoch'),
    recorded_at      = strftime('%Y-%m-%dT%H:%M:%fZ', event_at_ms / 1000.0, 'unixepoch'),
    visibility_scope = owner_scope
WHERE occurred_at = '';

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

-- New index per SYSTEM_INVARIANTS.md §1 invariant 5 (implicit index list from
-- BUILD.md §L2) — matches the shape of idx_events_correlation from 0001.
CREATE INDEX IF NOT EXISTS idx_events_causation
    ON events (causation_id)
    WHERE causation_id IS NOT NULL;
