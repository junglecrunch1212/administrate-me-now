-- Commitments projection schema — the obligation tracker.
--
-- Per ADMINISTRATEME_BUILD.md §3.4 and SYSTEM_INVARIANTS.md §4.
--
-- Cross-DB FK references below (owed_by_party, owed_to_party,
-- confirmed_by → parties(party_id); source_interaction_id →
-- interactions(interaction_id)) are documentation only. SQLite cannot
-- enforce FKs across separate projection DBs (each projection owns its
-- own SQLite file per prompt 05's pattern). Integrity is preserved by
-- upstream pipelines per [§2.3]; the integration rebuild test logs
-- orphans as informational.
--
-- [§4.1] commitments are obligations between parties; they are distinct
--   from tasks (household work — §4.3, §13.1).
-- [§4.6] task completion does not auto-complete commitments. A bridging
--   pipeline may emit explicit commitment.completed events later.

CREATE TABLE IF NOT EXISTS commitments (
    commitment_id         TEXT NOT NULL,
    tenant_id             TEXT NOT NULL,
    owed_by_party         TEXT NOT NULL,          -- REFERENCES parties(party_id)
    owed_to_party         TEXT NOT NULL,          -- REFERENCES parties(party_id)
    kind                  TEXT NOT NULL,
    description           TEXT NOT NULL,
    due_at                TEXT,
    status                TEXT NOT NULL,          -- pending | snoozed | done | cancelled | delegated
    confidence            REAL,
    source_interaction_id TEXT,                   -- REFERENCES interactions(interaction_id)
    source_skill          TEXT,
    proposed_at           TEXT,
    confirmed_at          TEXT,
    confirmed_by          TEXT,                   -- REFERENCES parties(party_id)
    completed_at          TEXT,
    owner_scope           TEXT NOT NULL,
    visibility_scope      TEXT NOT NULL,
    sensitivity           TEXT NOT NULL DEFAULT 'normal'
                          CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id         TEXT NOT NULL,
    PRIMARY KEY (tenant_id, commitment_id)
);

CREATE INDEX IF NOT EXISTS idx_commitments_tenant_status
    ON commitments(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_commitments_tenant_owed_by
    ON commitments(tenant_id, owed_by_party);
CREATE INDEX IF NOT EXISTS idx_commitments_tenant_owed_to
    ON commitments(tenant_id, owed_to_party);
CREATE INDEX IF NOT EXISTS idx_commitments_tenant_source
    ON commitments(tenant_id, source_interaction_id);
