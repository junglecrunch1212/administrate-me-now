-- Tasks projection schema — household work.
--
-- Per ADMINISTRATEME_BUILD.md §3.5 and SYSTEM_INVARIANTS.md §4, §13.
--
-- Tasks are NOT commitments. Commitments are obligations between parties
-- (§4.1). Tasks are household work (mow lawn, renew license). Triage keeps
-- inferred candidates separate from already-official work; the projections
-- remain distinct (§4.3, §13.1, D26).
--
-- Cross-DB FK references (assignee_party → parties(party_id),
-- recurring_id → recurrences(recurrence_id)) are documentation only;
-- SQLite cannot enforce FKs across separate projection DBs. Integrity is
-- preserved by upstream pipelines per [§2.3].
--
-- what-now traverses goal_ref/dependency data during deterministic ranking.

CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT NOT NULL,
    tenant_id         TEXT NOT NULL,
    title             TEXT NOT NULL,
    status            TEXT NOT NULL,          -- inbox | next | in_progress | waiting_on | deferred | done | cancelled | dismissed
    assignee_party    TEXT,                   -- REFERENCES parties(party_id); NULL = household-shared
    work_kind         TEXT NOT NULL DEFAULT 'household_work'
                      CHECK (work_kind IN ('member_obligation','household_work','service_operational')),
    owner_member_id   TEXT,
    owner_party_id    TEXT,
    responsible_party_id TEXT,
    beneficiary_party_id TEXT,
    service_assignment_role TEXT,
    service_party_id TEXT,
    authority_grant_id TEXT,
    on_behalf_of_member_id TEXT,
    delegated_by_member_id TEXT,
    service_work_type TEXT,
    coordination_id TEXT,
    source_event_id TEXT,
    source_invocation_id TEXT,
    scheduled_start_at TEXT,
    scheduled_end_at TEXT,
    follow_up_at TEXT,
    waiting_on_party_id TEXT,
    waiting_on_member_id TEXT,
    provider_representation_owner_kind TEXT NOT NULL DEFAULT 'none'
                      CHECK (provider_representation_owner_kind IN ('none','member','service')),
    provider_representation_owner_principal_id TEXT,
    provider_id TEXT,
    provider_account_reference TEXT,
    provider_workspace_reference TEXT,
    provider_external_reference_digest TEXT,
    domain            TEXT NOT NULL,
    energy            TEXT,                   -- low | medium | high
    effort            TEXT,                   -- tiny | small | medium | large
    item_type         TEXT NOT NULL,          -- task | purchase | appointment | research | decision | chore | maintenance
    due_date          TEXT,
    available_from    TEXT,                   -- deterministic activation/snooze boundary; distinct from the deadline
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
    completed_by_party_id TEXT,
    lifecycle_actor_principal_id TEXT,
    lifecycle_actor_party_id TEXT,
    lifecycle_authority_grant_id TEXT,
    lifecycle_action  TEXT,
    lifecycle_at      TEXT,
    lifecycle_reason  TEXT,
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
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_owner_party
    ON tasks(tenant_id, owner_party_id, work_kind);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_due
    ON tasks(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_goal
    ON tasks(tenant_id, goal_ref);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_domain
    ON tasks(tenant_id, domain);

CREATE INDEX IF NOT EXISTS idx_tasks_tenant_service_work
    ON tasks(tenant_id, service_party_id, beneficiary_party_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant_coordination
    ON tasks(tenant_id, coordination_id);

-- Typed coordination is kept in the Tasks read model because it coordinates
-- work without becoming a Task, Commitment, provider object, or transcript.
CREATE TABLE IF NOT EXISTS coordination_records (
    tenant_id TEXT NOT NULL,
    coordination_id TEXT NOT NULL,
    coordination_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    last_action TEXT NOT NULL,
    originator_member_id TEXT NOT NULL,
    obligation_owner_kind TEXT NOT NULL,
    obligation_owner_id TEXT,
    responsible_party_id TEXT NOT NULL,
    beneficiary_party_id TEXT,
    linked_task_id TEXT,
    linked_commitment_id TEXT,
    cos_assignment_task_id TEXT,
    handoff_proposed_by_member_id TEXT,
    proposed_destination_member_id TEXT,
    handoff_expires_at TEXT,
    accepted_destination_member_id TEXT,
    waiting_on_member_id TEXT,
    waiting_since TEXT,
    follow_up_at TEXT,
    deadline_at TEXT,
    start_at TEXT,
    end_at TEXT,
    resource_id TEXT,
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, coordination_id)
);

CREATE INDEX IF NOT EXISTS idx_coordination_member_state
    ON coordination_records(tenant_id, originator_member_id, status);
CREATE INDEX IF NOT EXISTS idx_coordination_resource_time
    ON coordination_records(tenant_id, resource_id, start_at, end_at);

CREATE TABLE IF NOT EXISTS coordination_participants (
    tenant_id TEXT NOT NULL,
    coordination_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, coordination_id, member_id)
);

CREATE TABLE IF NOT EXISTS coordination_fields (
    tenant_id TEXT NOT NULL,
    coordination_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_value TEXT NOT NULL,
    audience_member_ids_json TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, coordination_id, field_name)
);

CREATE TABLE IF NOT EXISTS coordination_conversations (
    tenant_id TEXT NOT NULL,
    coordination_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    conversation_reference TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, coordination_id, member_id)
);

CREATE TABLE IF NOT EXISTS coordination_originator_receipts (
    tenant_id TEXT NOT NULL,
    coordination_id TEXT NOT NULL,
    originator_member_id TEXT NOT NULL,
    receipt_status TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, coordination_id, originator_member_id)
);
