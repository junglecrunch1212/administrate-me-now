# ADR-0007 — Finance workbook authority and deterministic planning

Status: **ACCEPTED; IMPLEMENTED IN CANDIDATE, LIVE PROVIDER ACTIVATION NOT AUTHORIZED**

Date: 2026-08-07

Decision owners: household principals and AdministrateMe product/engineering

Governing change order: `CHANGE-ORDER-006-FINANCE-WORKBOOK-AND-CASHFLOW.md`

## Context

The household finance workbook is intentionally the human-editable planning
surface. The earlier implementation could not uphold that promise: it exposed
only a thin transaction export, treated Plaid summary receipts as if they were
the financial facts, and projected a static Finance card with no cash-flow or
scenario contract. The supplied planner also contained useful household
structure but mixed manual inputs, imported observations, hidden constants,
and derived formulas without a machine-readable authority boundary.

Calling one file the source of truth is ambiguous in an event-sourced system.
Bank facts, planning intent, history, and current presentation have different
authors and different correction rules. Collapsing them would either let a
spreadsheet overwrite provider facts or make the event log subordinate to a
mutable file.

## Decision

Finance uses four explicit, non-competing authorities:

1. **Plaid or another reviewed provider owns provider observations.** Account
   balances, transaction identity, dates, amounts, pending/removal state, and
   provider categories are read-only facts at the workbook boundary.
2. **`adminme-finance.xlsx` owns reviewed household planning intent.** Its
   Assumptions, Budget vs Actual, Scenarios, Goals, and allowed manual or
   classification cells are the canonical editable planning interface.
3. **The encrypted append-only event log owns causal history and provenance.**
   A valid workbook edit emits a typed event; no projection or workbook cell
   rewrites history. Provider and household authorship remain distinguishable.
4. **The money projection owns current deterministic read state.** Both the
   regenerated workbook and `deterministic-finance-cockpit-v1` consume that
   state. Neither is a second transactional database.

The forward loop is:

`provider observation -> typed event -> scoped money projection -> protected workbook cells and Finance views`.

The reviewed planning loop is:

`workbook edit -> structural/type/semantic validation -> typed planning event -> scoped money projection -> regenerated workbook and Finance views`.

The workbook contract is schema version 2. It contains twelve canonical
sheets: Dashboard, Cash Flow Plan, Assumptions, Budget vs Actual, Scenarios,
Goals, Balance Sheet, Raw Data, Accounts, Checks, Metadata, and Instructions.
Stable row identifiers are backend-authored. Blue planning cells are editable;
provider and derived cells are protected. A save is rejected if required
structure or values are invalid, if scenario bounds are unsafe, or if there is
not exactly one active baseline. Rejection is visible as workbook state and
does not mutate domain planning truth.

The Finance cockpit answers two household questions without model judgment:

- cash-flow safety: current cash, month-to-date and projected monthly flow,
  reserve floor, burn, runway, budgets, and a fixed-priority next review;
- future planning: bounded monthly scenarios, one-time changes, reserve breach
  month, cash-out month, goals, and net worth.

All calculations are integer-minor-unit or bounded decimal calculations over
typed rows. The caller supplies `as_of`; fixed ordering and explanations make
the result replayable. An LLM may extract or restate language through the
pinned OpenClaw seam, but it may not classify a transaction authoritatively,
set an assumption, choose a scenario, rank a goal, approve a provider write,
or originate a financial action.

The v1 cockpit and workbook use USD as their one planning currency. Non-USD
provider or budget rows remain preserved in the event log and appear as a
data-health exception, but they are excluded from totals until a future
reviewed FX-rate fact and conversion policy exist. The system never adds
different currencies as if their minor units were equivalent.

Provider enrollment remains private per member and account. A generated
household workbook includes only rows the workbook audience may read; private
member facts and planning rows do not become shared merely because the file is
household-operated. Plaid credentials remain secret references. Plaid sync is
read-only, advances a durable per-item cursor only after every detailed event
and summary receipt is accepted, and emits explicit removals instead of
silently deleting history.

There is no Plaid writeback. Workbook bidirectionality ends at AdministrateMe
planning and classification events. Future payment, transfer, lending, or
account-management capability would require its own provider contract,
allowlist, governance, independent approval, content/privacy guard,
observation behavior, terminal audit, and change order.

## Consequences

- A principal can use Excel directly without granting a spreadsheet authority
  over bank-owned facts.
- Dashboard and workbook use the same deterministic projection and cannot
  drift into separate forecast engines.
- Provider corrections/removals are replayable and never disguised as manual
  deletion.
- Scenario calculations are useful planning estimates, not financial advice,
  lending recommendations, or autonomous decisions.
- The existing count of thirteen domain projections plus derived Triage is
  preserved; Finance deepens the existing `money` capability.
- The attached private source workbook may be transformed into a local ignored
  artifact, but household values are never committed to platform code, docs,
  fixtures, or tests.

## Classification

- **PRESERVE:** encrypted event authority, provenance, provider authority,
  privacy/scope, replay, typed domain separation, observation, and no bank
  writes.
- **MODERNIZE:** the Finance workbook, detailed Plaid ingestion, cash-flow and
  scenario projection, Finance portal, validation, and durable cursor proof.
- **SUPERSEDE:** the three-sheet transaction export, summary-only Plaid event,
  hidden workbook constants, brittle zero-result formulas, and static Finance
  card.
- **DEFER:** only real Plaid Link/OAuth consent, per-account sharing grants,
  provider propagation evidence, and optional future providers. Finance core
  and workbook round-trip are not deferred.
