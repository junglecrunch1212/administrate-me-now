# Finance member projection

**MODERNIZE** the accepted Books presentation into six URL-backed member views.
**PRESERVE** ADR-0010 accounting authority, the frozen publication schemas,
source calculation ownership, current reader policy, owner-only provenance,
append-only history, and separate analysis/refresh controls. This change does
not enable an advisor, acquire transactions, or publish source edits.

## First usable outcome

Finance is the `/member/finance` surface within the shared member Dashboard.
An authorized reader can select a published cash/funding context and horizon,
understand the source estimate and its missing inputs, compare matching
published estimates, and expand dated drivers and evidence. The first screen
separates spendable cash (not published by the current contract), inflows,
outflows, and the source minimum/date. A positive remainder never becomes
available surplus. The current snapshot remains visibly historical.

The six views are Overview; Cash flow & events; Plans & assumptions; Spending
& shopping; Assets, debts & goals; Sources & confidence. Their stable query
keys retain the existing `overview`, `cashflow`, `scenarios`, `spending`,
`networth`, and `health` values. Books readers never fall through to legacy
workbook forecasts. Browser history, reload, reset and explicit refresh preserve
or replace only presentation state, not source inputs or authorization.
The surface's `workbook_planning_inputs_authoritative` flag is false whenever
Books is selected, including while its accepted publication is missing. This
flag describes the legacy planning inputs; missing Books evidence never makes
those inputs authoritative for the selected Books view.

## Binding crosswalk for the shipped view

All rows below are scoped by the authenticated tenant, selected book, actual
data owner, and current `FinanceReadScope`. Another reader does not impersonate
the data owner. The API checks the actor and read scope again after the worker
read. Existing owner provenance audiences remain unchanged.

`P` means `GET /api/member/v1/finance/planning?book_id=…&projection_id=…`.
The exact publication/hash, projection/hash, context and horizon accompany each
result. Contexts pin measure, dated scenario, spending case, income basis,
opening basis, currency, timezone and source as-of date. Only declared tuples
may be selected; missing combinations do not become a Cartesian product.

| Member element | Accepted field | Transformation / meaning | Route | Validation / freshness |
|---|---|---|---|---|
| Published choices | `contexts[]` coordinates | Labels only; selections do not edit assumptions | P `planning_contexts` | At most 48 declared contexts; no invented tuple; retained projection head compared |
| Selected scenario / spending case | `context.scenario_id`, `plan_case_id` | Dated scenario and spending case remain separate | P `planning_projection` | Same immutable context and horizon |
| Source date | `context.as_of_date`, `timezone` | Local date, never converted to an acceptance or refresh date | P `planning_projection` | Historical source date remains visible |
| Spendable cash | No field in current contract | Explicitly **not established**; never opening minus a guessed reserve | No computation | Missing producer metric and usable allocation, not zero |
| Expected cash in / planned funding in | `summary.cash_in` | Format exact minor-unit lower/upper endpoints; funding is not payroll | P `planning_projection` | Independent endpoint null reasons; selected measure/income basis |
| Expected cash out / funds needed | `summary.cash_out` | Format source total; never sum event pages or add provisions again | P `planning_projection` | Independent endpoint null reasons; selected measure |
| Lowest projected bank cash / funds | `summary.minimum` | Preserve range or tagged conservative scalar | P `planning_projection` | Never manufacture range endpoints from a scalar |
| Minimum date | `summary.minimum_date` | Preserve exact date or its own unknown reason | P `planning_projection` | Inside the selected local horizon, no fallback date |
| Opening / encumbered / closing | `summary.opening`, `encumbered`, `closing` | Exact ranges in expanded amounts; opening is not spendable | P `planning_projection` | Unknown endpoints stay unknown; opening basis explicit |
| Near-term review | `estimate_state`, `negative_bank_cash`, `checking_shortage`, `reserve_breach` and corresponding dates | Fixed presentation priority; source alerts remain distinct | P `planning_projection` | No fresh safety conclusion or inferred surplus |
| Chosen thresholds | `reserve_threshold`, `checking_threshold` plus `unknowns` | Preserve known zero separately from unknown | P `planning_projection` | No universal or default reserve |
| Confidence | `estimate_state`, `completeness_state`, `safety_state` | Three independent source declarations | P `planning_projection` | Historical source verification is not current payment safety |
| 7/30/90/180/365 comparison | Five exact summaries for one context | Ending-range marks and exact text; no continuous interpolated daily path | P `planning_projection`, five bounded reads on request | Same publication and projection hashes; overlapping horizons never added |
| Timing-scenario comparison | Declared contexts matching measure/income/opening/currency/timezone/date | Compare source outputs; label each scenario's published spending case | P `planning_projection` | One unambiguous context per scenario; prefer selected case when multiple cases exist, otherwise omit ambiguity |
| Dated drivers | `events[]` original date bounds, kind, direction, included flag, amount | Vertical item list, 50 per page | P `planning_events` | Window overlap is not proof of occurrence; undated stays unplaced |
| Driver cash/debt effects | `bank_effect`, `debt_effect`, `account_route_id`, `recognition_id` | Expose source effects and route in disclosure; never sum again | P `planning_events` | Card purchase/settlement and provision/bill recognition rules remain producer/validator authority |
| Required inputs | `conditions`, gap groups and original issues | Expand explanation, required input, timing and evidence | P `planning_gaps`, `planning_issues` | Preserve required counts and membership; groups do not clear issues |
| Source references | Protected artifact references, declared hashes and source clocks | Display as references, not fetched source content | P `planning_evidence`, `planning_sources` | Existing current reader policy; no new sharing or source access |
| Historical / superseded state | Requested/current accounting and projection heads | Show newer-publication notice; explicit refresh selects latest accepted head | All planning reads | Never silently combine generations; late successes and failures cannot repaint newer selection |
| Private goals/preferences | Existing choice record, baseline publication, event/revision | Existing explicit owner intent controls | `/api/member/v1/finance/choices` | Existing CAS/idempotency and owner-only authority; does not recalculate Books |

All ranges are source model bounds, not statistical confidence intervals.
The current contract has no accepted net-cash-change metric. Display inflow and
outflow separately; do not recompute a source result from rounded labels. The
producer must define rounding when publishing income, costs and their net.

## Seven accounting reports: diagnosis and next binding

The initial accepted source declares all seven reports `unavailable` with
`report_mapping_not_accepted` and empty `source_coverage`. This is an explicit
producer declaration, not an acquisition failure or proof of no transactions.
Do not remove it, populate an unavailable report, or borrow values from the
independent dated projection. The member screen names this mapping gap.

| Report | Required basis | Useful source view / bounded region | Remaining producer work |
|---|---|---|---|
| `actuals` | `accounting_actual` | Household Dashboard monthly/category summaries; Spending Explorer covered-period totals; Spending Detail headers and selected evidence | Accepted category/period aggregation, reclassification/reversal policy and actual coverage; exclude pending and business pass-throughs |
| `cash` | `cash_movement` | Cash Activity; Cash Flow release status | Same-time openings, cash-leg inclusion, settlement/transfer elimination and reconciliation; formal Cash Flow stays unavailable until supported |
| `budget` | `chosen_budget` | Household Plan `B14:M32`, `B234:M299`, `B860:M879` | Named category dictionary, input/model version, gross nanny fronting vs expected support, provisions vs actual earmarks, missing obligations |
| `observed_net` | `observed_value` | Household Plan `B70:M80`; Current Accounts `A6:L24` | Account ownership, balance sign/type/currency/date, manual valuations and missing accounts; incomplete observed net is not reconciled net worth |
| `balance_sheet` | `accounting_actual` | Balance Sheet `A1:I18` and release controls | Reconciled opening coverage, book-cost basis, accepted release; observed market values cannot fill it |
| `scenarios` | `forecast` | Household Plan `B860:M885` and shared income controls | Fixed named spending-case outputs and deltas with common income basis; distinct from dated current/expected/chosen contexts |
| `quality` | Explicit permitted basis | Books Controls `A1:J18`, Monthly Close `A1:J10`, source clocks and statement coverage | Typed coverage/reconciliation/refresh declarations; do not encode counts or booleans as currency amounts |

These regions are a binding specification, not authorization to import the
mutable Sheet directly. The exact private workbook ID, source capture, accepted
hashes and canonical exporter revision belong in the owner-only task evidence.
Source summaries were inspected read-only; no all-transaction recapture is
required. Producer revision/location must be current, not a deleted transfer
copy or a historical exporter pointer.

The frozen v1 accounting schema allows arbitrary metric IDs but has no semantic
dictionary, labels, category/account dimensions or metric-level missing reason.
Before semantic cards are enabled, agree a versioned metric dictionary or typed
successor export that supplies those meanings. The current member surface is
bounded to ten metrics per report/twenty total/32 KiB and has no dedicated
accounting pagination route. A later bounded member route must reuse
`query_publication` and the same final authority checks; CoS analysis need not be
enabled to read accepted member reports.

## Remaining member outcomes

These are unfinished product work, not completion claims for this UI slice:

1. Income breakdown: gross assumption, modeled taxes, unknown payroll
   deductions, modeled net, observed pay and historical bonuses. Publish each
   distinctly; total gross already includes bonuses.
2. Spending-case comparison: Current lifestyle, its separately conditional
   no-interest case, Sustainable and Essentials. Retain chosen allowances and
   unknown commitments; a stretch target is not measured essential need.
3. Shopping/subscriptions: source-card payer vs shopper, split shipments,
   unmatched credits/refunds, ongoing replenishment vs completed setup, service
   identity/cadence and realistic conditional opportunities. Do not claim
   cancellation or savings from missing charges.
4. Assets/debts/equity: dated observations, mortgage principal vs interest,
   contractual debt vs optional support reserve, excluded stale/disconnected
   accounts and missing manual liabilities. No assumed current balances.
5. Editable assumptions: supported Books input operation, explicit source/model
   revision, retained author intent and recalculated accepted output. Current
   private preferences are not a source-edit or scenario-calculation API.
6. Daily balance paths, per-account shortfalls and card-debt paths: the source
   has richer outputs, but these collections are explicitly unsupported by the
   current dated contract. Extend the producer/consumer boundary before graphing
   them. Itemized windows are available now; they are not daily balances.

## Publication and correction boundary

Retain immutable accepted accounting publication ID/content hash and its exact
accepted revisions. A dated successor pins that accounting publication/hash,
the three engine/input digests, source capture, selected spending case,
explicit contexts and independent clocks. Changed source classification,
pending-to-posted state or valuation creates a validated successor; it does not
rewrite accepted history. Failed validation leaves the prior head intact.

Preserve book/owner/account scope and the producer's recognition/inclusion
policy. Purchases and settlements are different legs. Mortgage principal is a
cash outflow/equity transfer, while interest is expense. A provision and its
eventual bill cannot both count under one recognition. Parent support is not a
receipt; full fronting remains visible until matched cash evidence exists.
Cancelled commitments remain in history and stop prospectively only with
source evidence. Unknown amounts and clocks never become zeros or current dates.

Member presentation pins a snapshot until explicit refresh, shows supersession,
and discards late responses after selection changes. If accounting advances
between the page and latest projection-index reads, the UI withholds estimates
until a refresh returns matching current accounting heads. An older projection
remains readable and labels any difference from the page summary and saved choices.
Changing one selector cannot silently change currency, timezone or source date.
Daily source refresh,
advisor activation, new persistent grants and external sharing remain separate.

## Validation and recovery

The existing hosted production-composition browser test covers exact minor-unit
formatting, declared contexts, all horizon comparisons, unknown usable cash,
independent safety flags, grouped issues/evidence, overlapping/undated events,
repeated/failed/interrupted reads, URL/history/reset, keyboard use and narrow
viewport reflow. Fixtures contain no household data. Passing hosted checks do
not establish actual member-device acceptance or native source parity.

Use current protected release selection and required checks. After supported
activation, inspect the actual intended member browser and compare the same
accepted heads/selected values. A narrow viewport is not physical mobile
acceptance; one member's session is not another's. Recover with the prior
compatible schema-aware runtime or supported Books disable operation while
retaining accounting and dated history. No pre-Finance binary, history reset,
automatic import replay, grant expansion or provider effect is part of recovery.
