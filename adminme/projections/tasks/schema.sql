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

-- G07 delegated execution remains a set of related objects.  The action,
-- standing envelope, approval, provider command/result, later observation,
-- and reconciliation are intentionally not collapsed into the Task row or
-- into one generic status blob.
CREATE TABLE IF NOT EXISTS cos_execution_envelopes (
    tenant_id TEXT NOT NULL,
    envelope_id TEXT NOT NULL,
    authority_grant_id TEXT NOT NULL,
    granted_by_member_id TEXT NOT NULL,
    represented_service_principal_id TEXT NOT NULL,
    action_class TEXT NOT NULL,
    canonical_object_kind TEXT NOT NULL,
    canonical_object_id TEXT NOT NULL,
    occurrence_id TEXT,
    series_id TEXT,
    recurrence_strategy TEXT NOT NULL,
    desired_representation_id TEXT NOT NULL,
    provider_family TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    provider_account_owner_principal_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    audience_ids_json TEXT NOT NULL,
    representation_kind TEXT NOT NULL,
    target_external_id TEXT,
    expected_provider_revision TEXT,
    desired_payload_digest TEXT NOT NULL,
    provider_execution_scope TEXT NOT NULL,
    openclaw_permission_included INTEGER NOT NULL,
    provider_effect_count INTEGER NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by_member_id TEXT,
    revocation_reason_code TEXT,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, envelope_id)
);

CREATE INDEX IF NOT EXISTS idx_cos_execution_envelope_match
    ON cos_execution_envelopes(
        tenant_id, authority_grant_id, action_class, canonical_object_id,
        destination_id, revoked_at, expires_at
    );

CREATE TABLE IF NOT EXISTS cos_delegated_actions (
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    action_revision INTEGER NOT NULL,
    state TEXT NOT NULL,
    canonical_object_kind TEXT NOT NULL,
    canonical_object_id TEXT NOT NULL,
    occurrence_id TEXT,
    series_id TEXT,
    recurrence_strategy TEXT NOT NULL,
    coordination_id TEXT,
    desired_representation_id TEXT NOT NULL,
    actor_principal_id TEXT NOT NULL,
    actor_party_id TEXT NOT NULL,
    operator_principal_id TEXT NOT NULL,
    operator_kind TEXT NOT NULL,
    represented_service_principal_id TEXT NOT NULL,
    active_context_scope TEXT NOT NULL,
    active_context_member_id TEXT,
    on_behalf_of_member_id TEXT,
    authority_grant_id TEXT NOT NULL,
    authority_grant_event_id TEXT NOT NULL,
    resource_owner_principal_id TEXT NOT NULL,
    provider_account_owner_principal_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_family TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    production_adapter TEXT,
    adapter_architecture TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    audience_ids_json TEXT NOT NULL,
    action_class TEXT NOT NULL,
    operation TEXT NOT NULL,
    representation_kind TEXT NOT NULL,
    expected_provider_revision TEXT,
    target_external_id TEXT,
    desired_payload_json TEXT NOT NULL,
    desired_state_json TEXT NOT NULL,
    desired_payload_digest TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    command_id TEXT NOT NULL,
    command_idempotency_key TEXT NOT NULL,
    fact_event_ids_json TEXT NOT NULL,
    facts_observed_at TEXT NOT NULL,
    facts_valid_until TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    privacy_guard_version TEXT NOT NULL,
    content_guard_digest TEXT NOT NULL,
    risk_class TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    domain_approval_event_id TEXT,
    openclaw_approval_event_id TEXT,
    execution_envelope_id TEXT,
    dispatch_authorization_event_id TEXT,
    command_event_id TEXT,
    terminal_result_event_id TEXT,
    observation_event_id TEXT,
    reconciliation_event_id TEXT,
    owner_scope TEXT NOT NULL,
    visibility_scope TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, action_id)
);

CREATE INDEX IF NOT EXISTS idx_cos_delegated_action_context
    ON cos_delegated_actions(
        tenant_id, active_context_member_id, represented_service_principal_id,
        state, prepared_at
    );

CREATE TABLE IF NOT EXISTS cos_delegated_action_approvals (
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    approval_event_id TEXT NOT NULL,
    approval_kind TEXT NOT NULL,
    approved_by_member_id TEXT NOT NULL,
    execution_envelope_id TEXT,
    action_fingerprint TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, approval_event_id)
);

CREATE TABLE IF NOT EXISTS cos_delegated_action_commands (
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    command_event_id TEXT NOT NULL,
    domain_approval_event_id TEXT NOT NULL,
    openclaw_approval_event_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, command_id)
);

CREATE TABLE IF NOT EXISTS cos_delegated_action_results (
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    result_event_id TEXT NOT NULL,
    result_type TEXT NOT NULL,
    provider_called INTEGER NOT NULL,
    suppressed INTEGER NOT NULL,
    external_id TEXT,
    provider_revision TEXT,
    result_json TEXT NOT NULL,
    error_kind TEXT,
    retryable INTEGER NOT NULL,
    terminal_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, result_event_id)
);

CREATE TABLE IF NOT EXISTS cos_delegated_action_observations (
    tenant_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    result_event_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_family TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    provider_revision TEXT NOT NULL,
    observed_state_json TEXT NOT NULL,
    observation_fingerprint TEXT NOT NULL,
    direct_provider_edit_detected INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, observation_id)
);

CREATE TABLE IF NOT EXISTS cos_delegated_action_reconciliations (
    tenant_id TEXT NOT NULL,
    reconciliation_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    observation_event_id TEXT NOT NULL,
    result_event_id TEXT NOT NULL,
    desired_state_digest TEXT NOT NULL,
    observed_state_digest TEXT NOT NULL,
    outcome TEXT NOT NULL,
    canonical_outcome TEXT NOT NULL,
    canonical_outcome_event_id TEXT,
    reason_code TEXT NOT NULL,
    reconciled_at TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, reconciliation_id)
);

CREATE TABLE IF NOT EXISTS cos_delegated_action_state_history (
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    state TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, action_id, source_event_id, state)
);
