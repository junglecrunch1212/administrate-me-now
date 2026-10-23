# Household finance capability acceptance ledger

Status: candidate implementation; hosted process validation and live activation pending.

This ledger contains synthetic engineering scope only. It is not evidence of
provider access, owner consent, professional qualification or an activated
finance service. The governing boundaries are ADR-0001 (OpenClaw ownership),
ADR-0007 (Books authority), the finance accepted-publication contract, and the
resource gates in `FINANCE-RESOURCE-GATES.md`.

**PRESERVE:** private member scope, event history, independent action approvals,
provider observation provenance, and accepted external scenario results.
**MODERNIZE:** missing-data states, a small native tool facade, per-question
readiness, and typed preparation/decision records. A numerical variant remains
unavailable until its specific evidence, semantics, independent oracle and
runtime gates pass. This is an explicit implementation gap, not a waiver of a
required privacy invariant or product function.

## Evidence and release gates

The registry in `adminme/finance/capabilities.py` routes F01–F31. Its
`implementation_state` describes routing and preparation. It does not establish
that every associated calculator exists. Every question separately resolves
accepted, current, complete and authorized evidence. Its readiness must agree
with the named calculator/report actually available on the native API path.

No capability is live-verified in this change. Candidate tests use synthetic
fixtures. Hosted test results and exact commit/check URLs belong in the PR;
local source inspection alone is not a passing process test. Books adoption of
the canonical schema/hash, private acceptance, explicit processing authority,
activation, rollback rehearsal and general CoS performance gates remain
separate release requirements. No new grants, provider reconnects, source
sharing, financial actions or professional contact follow from this ledger.

The following common test locations establish the intended validation paths:

- `tests/unit/test_finance_answers.py`: exact current-result selection and
  deterministic rendering; changed values, units, periods, models, missing
  caveats, extra narrative and stale references are rejected.
- `tests/acceptance/test_finance_disclosure.py`: durable private disclosure receipts,
  revocation after retrieval/audit, buffered streaming, pinned publication change
  and qualified report availability through production endpoints. New coverage
  remains candidate until its exact hosted revision passes.
- `tests/unit/test_finance_readiness.py`: pinned, owner-scoped metadata proofs,
  exact report basis/release/quality, incomplete clocks and no semantic inference
  from report names. Accepted metadata supplies qualified report pointers while
  complete/current question evidence remains independently required.
- `tests/acceptance/test_finance_choices.py`: private explicit owner goals and
  preferences through member APIs, duplicate/conflicting requests, denied-write
  redaction, projection rebuild and restart.
- `tests/acceptance/test_finance_browser.py`: hosted disposable Chromium goal,
  pause, preference and reload flow using synthetic identity and publications;
  it does not establish live member-device or mobile acceptance.
- `tests/unit/test_finance_workflows.py`: exact decision quorum, changed
  baseline/audience/policy, expiry/revocation, attribution, minimum output,
  professional preparation and compound dependencies.
- `tests/unit/test_finance_calculators.py`: each implemented model's independent
  arithmetic cases, semantic exclusions and dimensional limits.
- `tests/unit/test_finance_resources.py`: resource caps, cache isolation and
  reauthorization, heavy-work refusal until production admission is proven.
- The native finance facade acceptance tests: authenticated composition-root
  requests, missing evidence/permissions, accepted publications and safe errors.

Test existence is not test success. Each supported numerical model must list
its version, schema, rounding policy, oracle, performance class and exclusions.

## Capability-by-capability disposition

Every row supports a bounded explanation or an evidence/preparation request.
“Candidate model” means its implementation and native-path validation must be
checked before release. “Preparation” supplies useful work while explicitly
withholding an unsupported numerical or professional conclusion.

| ID | Supported route and candidate depth | Required release evidence / current numerical limit |
|---|---|---|
| F01 | Accepted category/budget reports; candidate variance calculation | Accepted comparable periods, chosen targets and confirmed priorities; no inferred preferences |
| F02 | Selected item unit-price comparison; receipt/return checklist | Item quantities, taxes/discount allocation and receipt matching; acquisition/returns remain external contracts |
| F03 | Due-date, renewal and payment-status preparation | Confirmed obligations/service cadence and actual payment status; no cancellation or inferred unused-service claim |
| F04 | Accepted cash-leg report and allocation preparation | Matched reimbursement evidence and shared agreement; expectations are not debts or received cash |
| F05 | Bounded travel finance brief and option preparation | Dated quotes, refund terms and valid cash timing; integrated purchase model unavailable where not declared |
| F06 | Unit-cost/time comparison against chosen preferences | Explicit time/quality choices; no inferred value of an adult's time |
| F07 | Candidate dated cash ladder | Known liquid opening, mandatory flows and timing rules; unknown mandatory inputs block a safety verdict |
| F08 | Candidate reserve coverage | Chosen reserve policy and liquid cash; no home/retirement liquidity assumption or universal reserve target |
| F09 | Candidate fixed debt/refinance comparison | Complete supported rate/fee/term/payoff inputs; variable or unsupported terms require another validated model and lender review |
| F10 | Credit-report/utilization preparation | Authorized dated limits/report details; score prediction, bureau access and unsupported score models unavailable |
| F11 | Narrow transaction-verification checklist | Accepted revisions and matched evidence; anomaly detection is not fraud adjudication |
| F12 | Accepted source/revision/quality status and reconciliation preparation | Openings, statement totals and matching; complete roll-forward model/close gate required before reconciled claims |
| F13 | Accepted observed-register report | Ownership, valuation dates and complete coverage; formal balance sheet remains unavailable without close/release evidence |
| F14 | Home decision dependency checklist | Supported ownership/financing/exit/tax model and dated inputs; no equity-as-cash or professional conclusion |
| F15 | Candidate ownership/lease total-cost comparison | Actual ownership/lease facts, costs and residual assumptions; lease payments do not create an owned asset |
| F16 | Candidate pay-frequency bridge; unchanged accepted scenarios | Person-specific deductions and timing; gross conversion does not guarantee net take-home |
| F17 | Candidate leave-gap comparison and benefit checklist | Accepted employer leave/benefit terms and timing; no eligibility or diagnosis inference |
| F18 | Entity/household boundary and professional preparation | Only authorized household-facing exposures; separate company books and cross-border determinations excluded |
| F19 | Tax evidence and rule-version preparation | Numerical tax estimate unavailable until year/jurisdiction/model/official-example validation; no filing/payment |
| F20 | Filing-choice evidence and professional packet preparation | Ownership and person attribution required; MFS/other comparisons unavailable without independently validated model |
| F21 | Candidate allocation/concentration/weighted-fee summary | Complete permitted holdings, valuations, wrapper and classifications; no inferred holdings, unsupported returns or trades |
| F22 | Investment alternative dependency checklist | Named validated comparative model plus horizon, tax/fee/risk evidence; unsupported recommendation unavailable |
| F23 | Retirement/withdrawal dependency and professional preparation | Complete assets/benefits/tax rules and validated withdrawal/sequence model; heavy simulation unavailable until resource gates pass |
| F24 | Candidate bounded goal funding path | Explicit timing, costs, contributions and model assumptions; aid/tax/trust eligibility excluded |
| F25 | Coverage/claim checklist and professional packet preparation | Policy terms/limits/exclusions and validated shock interpretation; no binding, cancellation or claim assertion |
| F26 | Candidate support-capacity comparison and family-minimum brief | Support intent, chosen reserve policy and agreement; optional reserves are not legal debts |
| F27 | Document/beneficiary/incapacity preparation | Current document metadata and actual fiduciary authority; no credential vault, legal instruments or automatic access |
| F28 | Candidate private owner goal/preference recording and replay; typed alternatives and decision quorum contracts | Hosted API/browser acceptance required for owner choices; joint decisions still require production integration and verified joint authority |
| F29 | Affected-dependency and reversible-step preparation | Event-specific income/care/housing/coverage/legal facts; status/relationship never establishes eligibility or authority |
| F30 | Accepted scenario comparisons and candidate bounded goal paths | Integrated alternatives require compatible accepted baselines/assumptions; no single optimal answer claim |
| F31 | Typed expected/observed outcome and professional manifest contracts | Production record integration plus accepted comparable evidence; preparation does not authorize disclosure or causal savings claims |

## Typed record and cross-domain boundaries

`adminme/finance/workflows.py` provides schema-validated goal, preference,
proposal, agreement, outcome and packet contracts. The schemas registered in
`adminme/events/schemas/finance_workflows.py` are immutable event facts once
accepted by the existing governed event-write path. Registration alone does
not activate an ingestion route or a projection. Explicit private owner goals
and preferences now have candidate member forms, governed ingestion and a
replayable money projection. They require an accepted owner publication and
optimistic predecessor, preserve immutable history, and grant no provider
action. Hosted acceptance remains pending for this revision. Joint decisions,
outcomes and professional packets still lack production record integration.
Existing legacy goal events remain distinct.

A decision agreement binds the exact proposal digest, baseline, audience,
policy, chosen alternative, adult identity, receipt and expiry. The resolver
requires a verifier backed by current domain authority. Tests may use a
synthetic verifier; production must never substitute a constant success. All
applicable adults must agree on the same alternative. Their decision agreement
still grants no external financial action.

Outcome records distinguish implementation state, comparable accepted metric
observations and attribution. Even reviewed decompositions do not themselves
assert causal savings. Professional packets hold opaque evidence references,
exact intended audience, content hash, unresolved questions and expiry; they
remain prepared and undisclosed.

Calendar briefs contain an allowlisted review label, due date and protected
reference. Travel and shopping receive only chosen constraints and selected
references. Family support receives arrangement terms and intent, without
salary/assets or private source links. These shapes minimize an already
authorized disclosure; creating a shape does not authorize its audience.

## Compound owner walkthrough

These are synthetic acceptance scenarios, not live household findings.

1. **Career, leave and move:** request the affected audience, pin the accepted
   baseline, then resolve income, benefits, cash timing, housing, tax and
   retirement. Unknown leave or timing terms produce a targeted request. The
   output is a dependency checklist and reversible options until an integrated
   model supports a numerical conclusion.
2. **Support and reserves:** combine support intent, reserve policy and each
   applicable adult's explicit choices. Optional reserve/gift intent never
   becomes enforceable debt; the family-facing brief excludes household
   financial context.
3. **Balance drop and purchase:** source/acceptance health precedes anomaly
   review, cash timing and purchase comparison. Broken coverage blocks a
   confident affordability verdict.
4. **Retirement and incapacity:** resolve withdrawal-model limits, policy
   evidence and actual document authority. Simulation output never becomes a
   trade instruction or a relative's access grant.

Daily, monthly and annual reviews are opt-in OpenClaw standing orders. No
workflow in this change registers a cron task, invokes an LLM per transaction,
contacts a professional or creates an always-on specialist agent.

## Remaining owners and next actions

- **Implementation project:** close unimplemented models and durable workflow
  integrations; pass independent model, production composition, privacy,
  replay, cancellation and mixed-load gates. Keep planned routes honest.
- **Books integration coordinator:** review and adopt this project's exact
  schema/hash; publish an explicitly accepted snapshot without pending tails.
- **Data owners:** authorize only the evidence/audience needed for a selected
  question, including both relevant owners for joint privileged processing.
- **Qualified professional/model reviewer:** validate specialized tax,
  withdrawal, policy, entity, foreign and legal variants before those numerical
  or consequential claims become supported.
- **Deployment owner:** separately request guarded activation after code gates,
  private acceptance and backup/rollback prerequisites pass.

## Grounded answer boundary

Finance tool results carry an opaque `answer_ref`. The model's final finance
reply contains only `{"finance_answer":{"result_refs":["answer_ref"]}}` with up
to three current references. The domain renderer supplies values, units, basis,
period, model/publication, assumptions, omissions and next steps from encrypted
owner-private receipts. Arbitrary financial prose and model-supplied numbers
cannot replace these facts. Portal delivery and native self-reply/claim recheck
current authority and pinned publication/model dependencies. Native transport
binds the exact rendered content before any provider effect.

Pinned OpenClaw `tool_result_persist` and `before_message_write` hooks remove
financial tool values, hypothetical tool arguments and unverified assistant
prose from the general transcript. Only bounded result references remain. The
SDK shapes and persistence transform were inspected at commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`; hosted integration and installed-hook
verification remain separate evidence. Structured finance results are retained
only in encrypted private domain history, never a shared vector or global memory.
A marked conversation needs a current finance result before another answer.
Revocation rejects the previous receipts; a replacement grant permits fresh
retrieval in the same conversation, without restoring the old receipt authority.
A reference is not a timeless permission or a substitute for fresh data.

The supported card is intentionally deterministic. Free-form personalized
recommendations, durable operational workflow integration and authorized app
source drilldown remain unfinished work; a grounded calculation card does not
establish those broader capabilities.
