-- Tasks projection schema — household work.
--
-- Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13.
--
-- Tasks are NOT commitments. Commitments are obligations between parties
-- (§4.1). Tasks are household work (mow lawn, renew license). The inbox
-- surface merges them at read time in a later prompt; the projections
-- stay separate (§4.3, §13.1).
--
-- Cross-DB FK references (assignee_party → parties(party_id),
-- recurring_id → recurrences(recurrence_id)) are documentation only;
-- SQLite cannot enforce FKs across separate projection DBs. Integrity is
-- preserved by upstream pipelines per [§2.3].
--
-- # TODO(prompt-10c): whatnow pipeline traverses goal_ref DAG for ranking

CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    title             TEXT NOT NULL,
    status            TEXT NOT NULL,          -- inbox | next | in_progress | waiting_on | deferred | done | dismissed
    assignee_party    TEXT,                   -- REFERENCES parties(party_id); NULL = household-shared
    domain            TEXT NOT NULL,
    energy            TEXT,                   -- low | medium | high
    effort            TEXT,                   -- tiny | small | medium | large
    item_type         TEXT NOT NULL,          -- task | purchase | appointment | research | decision | chore | maintenance
    due_date          TEXT,
    micro_script      TEXT,
    linked_item_id    TEXT,
    linked_item_kind  TEXT,
    recurring_id      TEXT,                   -- REFERENCES recurrences(recurrence_id)
    depends_on_json   TEXT NOT NULL DEFAULT '[]',
    goal_ref          TEXT,                   -- parent task_id (self-ref; not enforced cross-row)
    life_event        TEXT,
    auto_research     INTEGER NOT NULL DEFAULT 0,
    waiting_on        TEXT,
    waiting_since     TEXT,
    created_at        TEXT NOT NULL,
    created_by        TEXT,
    completed_at      TEXT,
    completed_by      TEXT,
    source_system     TEXT,
    notes             TEXT,
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_tenant_status
    ON tasks(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_assignee
    ON tasks(tenant_id, assignee_party);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_due
    ON tasks(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_goal
    ON tasks(tenant_id, goal_ref);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_domain
    ON tasks(tenant_id, domain);
