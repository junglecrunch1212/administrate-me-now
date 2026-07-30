-- 0003 durable idempotency keys.
--
-- At-least-once adapters, pipelines, and command clients attach a stable key
-- to every semantic write. The append-only log owns the uniqueness boundary;
-- a duplicate key resolves to the original immutable event instead of
-- creating a second fact.

ALTER TABLE events ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_tenant_idempotency
    ON events (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
