# CO-006 — Finance workbook, cash-flow safety, and scenario planning

Status: **APPROVED AND IMPLEMENTED IN CANDIDATE; LIVE ACTIVATION NOT AUTHORIZED**

Date: 2026-08-07

## Purpose

Turn the household financial planner into a safe, auditable planning source
and build the Finance projection from it. The product must reduce the executive
function cost of answering “Are we okay?”, “When does cash become unsafe?”,
“What can we change?”, and “What happens if we pursue this goal?” without a
model making a financial decision.

## Product outcome

Finance is one calm cockpit with six URL-backed views:

- **Overview** leads with cash, projected month-end, baseline runway, net
  worth, data health, and one deterministic next review.
- **Cash flow** shows monthly inflow/outflow, plan basis, reserve floor, and
  each scenario's forecast points.
- **Spending** compares category budget to month-to-date actuals and sorts the
  most constrained categories first.
- **Scenarios** compares named household-authored futures with explicit input
  deltas, horizon, reserve breach, and cash-out month.
- **Net worth** shows authorized accounts, balance type, source timestamp, and
  assets less liabilities without exposing credentials.
- **Data health** separates provider freshness, workbook validation, account
  link health, pending/uncategorized counts, safely excluded non-USD rows, and
  actionable setup truth.

The cockpit uses fixed deterministic priority: invalid workbook, unsupported
currency, no financial source, unhealthy account, stale source, forecast
cash-out, reserve breach, over-budget category, uncategorized transactions,
then a calm current state.
Color reinforces state but never carries meaning alone. Currency amounts are
plain data, not moralized or scored.

## Data and workbook scope

ADR-0007 governs authority. The candidate extends the existing money
projection; it does not add a fifteenth projection or a second runtime.

Typed events cover provider observations/removals, account observations,
household classifications/manual updates, assumptions, budgets, scenarios,
goals, and workbook publication/validation. Projection migrations are
additive. Removed or excluded provider rows remain historical facts but do not
enter current totals.

`adminme-finance.xlsx` schema v2 contains twelve sheets. Editable planning
cells round-trip through validation and typed events. Dashboard, Cash Flow
Plan, Balance Sheet, Accounts, Checks, Metadata, identifiers, provider fields,
and derived values are protected. Manual Raw Data rows may be added and edited;
provider rows permit only household classification, memo/notes, and inclusion
overrides. Deleting a provider row never deletes provider truth.

The supplied planner was repaired as a private local artifact: transaction
formulas now include actual populated rows, liabilities and net worth reconcile,
home and mortgage projections reference visible inputs, scenario horizons are
not clipped, model/check/import contracts are explicit, and input cells are
visually identified. The artifact remains ignored by Git because it contains
household financial data.

## Plaid adapter plan and implementation

The reviewed Plaid Cat-C adapter remains inbound-only. Each sync emits:

- one private privileged account observation per account;
- one private privileged transaction observation per added or modified item;
- one explicit removal tombstone per removed transaction;
- one private summary receipt after detailed events succeed.

Amounts are normalized once at the adapter boundary into received/paid plus an
absolute minor-unit value. Provider IDs, item/account identity, merchant,
categories, pending status, currency, source timestamps, and provenance remain
available for reconciliation. Secrets resolve only through approved secret
references. The provider cursor is durable per member/item and advances only
after all events are accepted, so restart repeats safely instead of skipping
facts.

Real Link enrollment, consent, account selection, webhook operations, and live
propagation remain an explicit post-merge activation boundary. Tests use
credential-free fixtures and do not claim a live connection.

## Determinism, privacy, and write boundary

- All totals, runway, reserve, budget, scenario, goal, and focus results are
  deterministic functions of authorized projection rows plus explicit `as_of`.
- No model routes money, chooses a scenario, creates a goal, or recommends a
  transaction. Natural-language assistance stays behind pinned OpenClaw and
  can only return non-authorizing language proposals.
- Financial provider observations are private/privileged by default. Shared
  workbooks apply the same row filter as APIs and exclude private member rows.
- Provider identifiers are protected. Credentials and full account numbers
  never enter events, workbooks, APIs, logs, screenshots, or fixtures.
- No bank-side write capability is introduced. A future financial write must
  pass the full separate external-write contract and cannot inherit authority
  from this read adapter.

## Acceptance

AC-32 demonstrates the full production composition: detailed observations and
planning events project into one scoped Finance cockpit, a validated workbook
edit round-trips, an invalid workbook fails closed, replay is equivalent,
private member data stays private, and the system still registers exactly
fourteen projections. Supporting unit/integration tests cover workbook shape
and protection, reverse events, validation, amount semantics, provider removal,
restart-safe cursor behavior, and member rendering.

The local workbook artifact is additionally inspected sheet-by-sheet and
scanned for formula errors. Browser verification uses the isolated Gate 7
runtime; this change order does not mutate the live family release.

## Classification

- **PRESERVE:** event log, provider authority, member isolation, workbook
  round-trip, provenance, deterministic architecture, OpenClaw boundary, and
  observation.
- **MODERNIZE:** money v2, Finance cockpit, planning workbook, validation,
  detailed Plaid mirror, and durable provider cursor.
- **SUPERSEDE:** thin/stale Finance projection, summary-only Plaid receipt,
  unvalidated planning edits, and separate spreadsheet/dashboard calculations.
- **DEFER:** live Plaid consent/Link and provider evidence, optional financial
  providers, and explicit cross-member account sharing grants only.
