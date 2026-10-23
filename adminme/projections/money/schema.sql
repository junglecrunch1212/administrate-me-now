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
    memo                TEXT,
    member_id           TEXT,
    merchant_name       TEXT,
    merchant_category   TEXT,
    provider_category   TEXT,
    assigned_category   TEXT,
    provider_transaction_id TEXT,
    provider_item_id    TEXT,
    pending             INTEGER NOT NULL DEFAULT 0,
    included            INTEGER NOT NULL DEFAULT 1,
    provider_revision   TEXT,
    updated_at          TEXT,
    removed_at          TEXT,
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
CREATE TABLE IF NOT EXISTS financial_accounts (
    account_id               TEXT NOT NULL,
    tenant_id                TEXT NOT NULL,
    member_id                TEXT NOT NULL,
    provider                 TEXT NOT NULL,
    provider_item_id         TEXT NOT NULL,
    institution_id           TEXT NOT NULL,
    institution_name         TEXT NOT NULL,
    display_name             TEXT NOT NULL,
    account_type             TEXT NOT NULL CHECK (account_type IN (
                                 'depository','credit','loan','investment','other')),
    account_subtype          TEXT,
    last4                    TEXT,
    current_balance_minor    INTEGER,
    available_balance_minor  INTEGER,
    limit_minor              INTEGER,
    currency                 TEXT NOT NULL DEFAULT 'USD',
    as_of                    TEXT NOT NULL,
    link_healthy             INTEGER NOT NULL DEFAULT 1,
    owner_scope              TEXT NOT NULL,
    visibility_scope         TEXT NOT NULL,
    sensitivity              TEXT NOT NULL DEFAULT 'privileged'
                             CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id            TEXT NOT NULL,
    PRIMARY KEY (tenant_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_financial_accounts_tenant_member
    ON financial_accounts(tenant_id, member_id);

CREATE TABLE IF NOT EXISTS finance_assumptions (
    assumption_id       TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,
    category            TEXT NOT NULL,
    label               TEXT NOT NULL,
    value_decimal       TEXT NOT NULL,
    unit                TEXT NOT NULL CHECK (unit IN (
                            'USD','USD/month','USD/year','ratio','months','count')),
    notes               TEXT,
    as_of               TEXT NOT NULL,
    owner_scope         TEXT NOT NULL,
    visibility_scope    TEXT NOT NULL,
    sensitivity         TEXT NOT NULL DEFAULT 'privileged'
                        CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, assumption_id)
);

CREATE TABLE IF NOT EXISTS finance_budgets (
    budget_id           TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,
    category            TEXT NOT NULL,
    amount_minor        INTEGER NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'USD',
    cadence             TEXT NOT NULL DEFAULT 'monthly' CHECK (cadence = 'monthly'),
    active              INTEGER NOT NULL DEFAULT 1,
    notes               TEXT,
    as_of               TEXT NOT NULL,
    owner_scope         TEXT NOT NULL,
    visibility_scope    TEXT NOT NULL,
    sensitivity         TEXT NOT NULL DEFAULT 'privileged'
                        CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, budget_id)
);

CREATE TABLE IF NOT EXISTS finance_scenarios (
    scenario_id                  TEXT NOT NULL,
    tenant_id                    TEXT NOT NULL,
    name                         TEXT NOT NULL,
    is_baseline                  INTEGER NOT NULL DEFAULT 0,
    horizon_months               INTEGER NOT NULL,
    opening_cash_delta_minor     INTEGER NOT NULL DEFAULT 0,
    monthly_income_delta_minor   INTEGER NOT NULL DEFAULT 0,
    monthly_expense_delta_minor  INTEGER NOT NULL DEFAULT 0,
    one_time_month               INTEGER,
    one_time_amount_minor        INTEGER NOT NULL DEFAULT 0,
    notes                        TEXT,
    active                       INTEGER NOT NULL DEFAULT 1,
    owner_scope                  TEXT NOT NULL,
    visibility_scope             TEXT NOT NULL,
    sensitivity                  TEXT NOT NULL DEFAULT 'privileged'
                                 CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id                TEXT NOT NULL,
    PRIMARY KEY (tenant_id, scenario_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_finance_one_baseline
    ON finance_scenarios(tenant_id) WHERE is_baseline = 1 AND active = 1;

CREATE TABLE IF NOT EXISTS finance_goals (
    goal_id              TEXT NOT NULL,
    tenant_id            TEXT NOT NULL,
    name                 TEXT NOT NULL,
    target_minor         INTEGER NOT NULL,
    current_minor        INTEGER NOT NULL DEFAULT 0,
    target_date          TEXT,
    priority             INTEGER NOT NULL DEFAULT 100,
    status               TEXT NOT NULL CHECK (status IN ('active','paused','complete')),
    notes                TEXT,
    lifecycle_at         TEXT,
    lifecycle_actor      TEXT,
    lifecycle_reason     TEXT,
    owner_scope          TEXT NOT NULL,
    visibility_scope     TEXT NOT NULL,
    sensitivity          TEXT NOT NULL DEFAULT 'privileged'
                         CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, goal_id)
);

CREATE TABLE IF NOT EXISTS finance_workbook_state (
    tenant_id                TEXT NOT NULL,
    workbook_name            TEXT NOT NULL,
    workbook_sha256          TEXT NOT NULL,
    workbook_schema_version  INTEGER NOT NULL,
    published_at             TEXT NOT NULL,
    published_by_party_id    TEXT NOT NULL,
    validation_state         TEXT NOT NULL CHECK (validation_state IN ('valid','rejected')),
    validation_failures_json TEXT NOT NULL DEFAULT '[]',
    owner_scope              TEXT NOT NULL DEFAULT '',
    visibility_scope         TEXT NOT NULL DEFAULT '',
    sensitivity              TEXT NOT NULL DEFAULT 'privileged'
                             CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id            TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workbook_name)
);

-- Accepted Books is a separate private book namespace; never legacy shared XLSX.
CREATE TABLE IF NOT EXISTS finance_publications (
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    publication_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, owner_id, book_id, publication_id)
);
CREATE TABLE IF NOT EXISTS finance_publication_heads (
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, owner_id, book_id)
);
CREATE TABLE IF NOT EXISTS finance_publication_metrics (
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    metric_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, owner_id, book_id, publication_id, metric_id)
);
CREATE INDEX IF NOT EXISTS idx_finance_publication_report
ON finance_publication_metrics(tenant_id,owner_id,book_id,publication_id,report_id,ordinal);


-- Explicit planning intent; accepted Books reports remain immutable.
CREATE TABLE IF NOT EXISTS finance_choices (
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    choice_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    last_event_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, owner_id, book_id, kind, choice_id)
);
