-- money projection schema — the transaction ledger.
--
-- Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2, §12.
--
-- Cross-DB FK references (from_party/to_party → parties, linked_artifact
-- → artifacts, linked_account → accounts, linked_interaction →
-- interactions) are documentation only; SQLite cannot enforce FKs across
-- separate projection DBs. Integrity preserved by upstream pipelines per
-- [§2.3]; the integration rebuild test logs orphans informationally.
--
-- Convention for CHECK constraints per phase 07a CF-6: CHECK on closed
-- enum columns (kind, sensitivity). Open columns (category, notes) get
-- no CHECK.
--
-- Soft-delete via ``deleted_at``: ``money_flow.manually_deleted`` does
-- not remove the row — rebuild correctness depends on it. Plaid-sourced
-- rows are never deleted through this projection; the Plaid adapter
-- handles reversal/correction on its own event stream.

CREATE TABLE IF NOT EXISTS money_flows (
    flow_id             TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,
    from_party          TEXT,                    -- doc-only → parties(party_id)
    to_party            TEXT,                    -- doc-only → parties(party_id)
    amount_minor        INTEGER NOT NULL,
    currency            TEXT NOT NULL,           -- ISO 4217
    occurred_at         TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN (
                            'paid','received','owed','reimbursable')),
    category            TEXT,
    linked_artifact     TEXT,                    -- doc-only → artifacts
    linked_account      TEXT,                    -- doc-only → accounts
    linked_interaction  TEXT,                    -- doc-only → interactions
    notes               TEXT,
    source_adapter      TEXT NOT NULL,           -- 'plaid' | 'receipts_ocr' | 'manual' | ...
    is_manual           INTEGER NOT NULL DEFAULT 0,
    added_by_party_id   TEXT,
    deleted_at          TEXT,
    owner_scope         TEXT NOT NULL,
    visibility_scope    TEXT NOT NULL,
    sensitivity         TEXT NOT NULL DEFAULT 'normal'
                        CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, flow_id)
);

CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_occurred
    ON money_flows(tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_category
    ON money_flows(tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_account
    ON money_flows(tenant_id, linked_account);
CREATE INDEX IF NOT EXISTS idx_money_flows_tenant_manual
    ON money_flows(tenant_id, is_manual);
