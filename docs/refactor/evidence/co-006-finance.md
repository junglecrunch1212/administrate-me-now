# CO-006 Finance evidence

Date: 2026-08-07

Status: **PASS — live core activated; provider enrollment pending**

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
Plaid consent was intentionally untouched.

## Live family activation

PR 283 passed the cascade check and the complete credential-free macOS
rehearsal, then merged to `main`. The rollback-safe updater activated exact
commit `f8a96e4f90526598bb86fbdefcee6daa10cedd19`, retained the previous release,
created an encrypted backup, and passed full Doctor with 14/14 healthy
projection checkpoints, 5/5 managed OpenClaw jobs, loopback-only listeners,
and observation mode.

The live canonical workbook contains twelve sheets, 38 assumptions, 14
budgets, five scenarios, five goals, and 791 historical/manual rows. The money
projection retains every row: 750 participate in current Finance arithmetic;
41 transfer/excluded rows remain available to provenance and replay without
distorting cash flow. The source workbook, live workbook, event log, and
projection therefore agree on the intentional inclusion boundary.

The original attempt proved a production-only defect: the generic 60/minute
budget refused 491 otherwise-valid rows after the reverse sidecar had consumed
the reviewed save. Exact caller/action policy now grants bounded bulk headroom
only to `daemon:xlsx_reverse`, validation caps Raw Data at 5,000 populated rows,
and the principal/agent/external defaults remain unchanged. Rebaseline plus
retry produced no additional denial and converged the full import.

The regenerated workbook passed the exact-sheet and row-count contract, formula
error scan, and visual inspection of Dashboard, Cash Flow Plan, Budget vs
Actual, Scenarios, Balance Sheet, and Checks. Live Tailscale browser exercise
passed Overview, Cash flow, Spending, Scenarios, Net worth, and Data health;
each route retained one visible Finance H1, one selected view, and its expected
deterministic section.

Plaid remains explicitly disabled/not configured and the cockpit therefore has
zero provider accounts. Live Link/consent and propagation evidence are still
deferred; no bank write capability was added.
