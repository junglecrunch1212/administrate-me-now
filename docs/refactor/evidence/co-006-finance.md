# CO-006 Finance evidence

Date: 2026-08-07

Status: **PASS — candidate implementation and aggregate validation complete**

## Reviewed behavior

- Existing `money` projection upgraded additively; the runtime inventory stays
  at thirteen domain projections plus derived Triage.
- `deterministic-finance-cockpit-v1` composes cash flow, budgets, scenarios,
  goals, accounts, workbook state, data health, and one explained focus item
  from authorized rows and a caller-supplied time.
- The canonical Finance workbook contains twelve sheets with protected
  provider/derived fields and editable, typed planning fields.
- Workbook validation rejects missing structure, invalid units, negative
  budgets/goals, out-of-range scenario inputs, invalid status/booleans, and
  zero or multiple active baselines before planning truth changes.
- Private provider and planning rows remain absent from the shared household
  workbook and unauthorized member/API views.
- Plaid produces detailed account/transaction/removal events and advances a
  durable per-item cursor only after accepted emissions.

## Private source workbook review

The supplied workbook was analyzed and repaired only in the ignored local
`outputs/` area. No household values were copied into repository code,
documentation, fixtures, screenshots, or assertions.

The resulting workbook has sixteen rendered and visually inspected sheets.
Its formula scan found no spreadsheet error values, and its Checks sheet reports
all model controls passing. Corrected controls include transaction range
coverage, liability/net-worth reconciliation, visible home/mortgage inputs,
full scenario horizon, and explicit model/import contracts.

## Executable evidence

The production-composition acceptance proof exercises the entire Finance loop:

1. private provider observations and shared planning facts enter the encrypted
   append-only event log;
2. the deterministic money projection produces the member cockpit while
   excluding the private account and cash total from another adult's view;
3. the production forward daemon writes the canonical workbook;
4. a valid budget edit returns through the runtime-owned reverse daemon as a
   typed event;
5. an invalid negative budget is rejected without changing accepted truth or
   consuming the accepted sidecar; a corrected retry is then accepted; and
6. replay produces the same projection while a child session is denied.

The Chrome-backed Gate 7 harness passed **136/136** checks. Finance-specific
coverage exercised Overview, Spending, Scenarios, Goals, Data health, view URL
restoration, signed transactions, and deterministic scenario changes. Manual
in-app-browser review also covered the six Finance views at desktop and mobile
widths; the mobile document had no horizontal overflow, routes persisted in
the URL, the page kept one heading level-one, and the browser reported no
console, page, or request failures.

The uninterrupted release-candidate command
`PATH=/opt/homebrew/opt/node@24/bin:$PATH bash scripts/acceptance/release-candidate.sh`
passed on Python 3.14.6 and Node 24.19.0 with:

- **32/32** strict acceptance tests;
- **1,580/1,580** root, code, and pack tests;
- **60/60** standalone bridge tests;
- Ruff, mypy, Pyright (**0 errors, 0 warnings**), shell, architecture-negative,
  package-contract, and dependency-audit gates;
- clean install, tenant-isolation, encrypted backup/restore, build/package,
  and performance-budget rehearsals; and
- the pinned OpenClaw 2026.7.2 contract plus **14/14** plugin tests.

The Python suite reports one upstream Starlette/FastAPI test-client deprecation
warning; it is not a functional failure or a dependency vulnerability. Live
Plaid consent and the family runtime were intentionally untouched.
