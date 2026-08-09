-- Cover the content-free source-assurance metadata scan without visiting the
-- provider-payload table rows. Terminal result payloads are queried through a
-- separate UNION branch and retain their ordinary table lookup.
CREATE INDEX IF NOT EXISTS idx_events_type_id_time
    ON events (type, event_id, occurred_at, recorded_at);
