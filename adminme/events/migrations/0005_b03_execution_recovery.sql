-- B03 durable crash-recovery lookups.  No row is created and no provider
-- permission is enabled by this migration; it only indexes immutable events.
CREATE INDEX IF NOT EXISTS idx_events_b03_command_recovery
    ON events(tenant_id, type, idempotency_key, event_id)
    WHERE type IN (
        'cos.google_tasks_execution.contact_attempt_committed',
        'cos.google_tasks_execution.provider_result_received',
        'cos.google_tasks_execution.terminal',
        'cos.google_tasks_execution.observed',
        'cos.google_tasks_execution.reconciled'
    );
