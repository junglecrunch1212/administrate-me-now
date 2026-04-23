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
    owner_scope      TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity      TEXT NOT NULL DEFAULT 'normal'
                     CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, recurrence_id)
);

CREATE INDEX IF NOT EXISTS idx_recurrences_tenant_next
    ON recurrences(tenant_id, next_occurrence);
CREATE INDEX IF NOT EXISTS idx_recurrences_tenant_linked
    ON recurrences(tenant_id, linked_kind, linked_id);
