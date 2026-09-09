-- The metadata reader includes causation_id for exact terminal correlation.
-- Cover that complete selection without visiting encrypted payload rows.
-- Retain idx_events_type_id_time for archived readers that force its name.
CREATE INDEX IF NOT EXISTS idx_events_type_id_time_causation
    ON events (type, event_id, occurred_at, recorded_at, causation_id);
