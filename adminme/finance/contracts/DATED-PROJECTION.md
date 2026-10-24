# Candidate dated projection transport

`household-books.dated-projection.v1.schema.json` and its `.sha256` file define a
new projection interchange. Accepted-publication v1 and its hash are unchanged.
This contract preserves producer outputs; it neither computes a forecast nor
establishes correctness of the Books engine. Native field mapping, source
adoption and private parity remain separate review dependencies. This supports
a declared subset of Books v1.2 outputs; it is not lossless v1.2 support. Daily
balances, card-debt paths and per-account shortfalls are explicitly unsupported
in the contract and visible on every consumer result. Synthetic
fixtures are not native parity evidence. The producer handoff distinguishes 18
frozen oracle groups (two openings × three scenarios × bank/funding-observed/
funding-modeled families), 12 actually verified native tuples (three scenarios ×
two measures × two income controls, observed opening only), and 24 proposed
query aliases. Bank-income aliases reuse one source output. Verified-opening
null/unavailable oracle outputs are not labeled native-tested. The mapper's
reported 1,611 local checks, including 780 field comparisons across 60 observed
native horizons, do not establish a conforming payload, private runtime parity
or adoption.

The later Books-side frozen local candidate reports canonical size 6,020,614
bytes (below 8 MiB), all 24 aliases/120 horizons, 3,307 body variants, 38,382
ordered references and 141 original issues including 49 required. Its independent
local checker reports 470,138 assertions with no source-parity failure. This is
reported local producer evidence, not an application import or private runtime
test. Canonical conformance still rejects six unbound accounting/application
identity fields and their audience references; none were invented. Two original
horizon-crossing windows account for 18 source-group/24 alias occurrences. The
semantic overlap repair is pinned at
`1323abd5738bf6d1f7d58994e8b40a1b49b94217`: all 20 synthetic inline/shared
boundary cases and browser acceptance passed. Two replay-render fixture failures
(`answer_ref` missing from direct-query results) were corrected at `e5483133`,
whose 871 Python checks and browser flow passed. That revision's light and
shared-planning latency gates still failed, independently of source-window
conformance. The exact validator file and schema hashes remain unchanged.
The producer separately reports the overlap delta verified against that exact
implementation and validator SHA-256
`7a2c74fa3e4dac7e05e6f07ccbde3f2f362638740032e788897214f14374c0dd`:
all 24 original crossing-window occurrences and all nonauthority conformance
checks passed, alongside 12 synthetic interval cases, without mutating the
6,020,614-byte candidate or its content hash. Full validation still correctly
rejects the unbound identities. This clears only the local producer overlap
gate, not current-head hosted CI, resource qualification or runtime acceptance.
No private payload was transferred to this checkout or hosted CI. Source
adoption, binding authority and lossless unsupported collections remain open.

The owner-only file-adapter seam requires finance publication processing to be
enabled and the exact current accepted accounting publication ID/content hash.
Declared accounting revisions must be a subset of that publication's accepted
set. A projection cannot advance accounting, discard held revisions or clear a
recovery hold. Each projection has its own immutable ID, content digest, exact
predecessor, source verification reference, engine/version and input digest.
Conflicting retries reject; identical retries recover the recorded result.
There is no public ingestion endpoint, provider acquisition or automatic grant.

## Typed contexts and outputs

Each declared context binds measure, scenario, income basis, opening basis,
Plan case, currency, IANA timezone and as-of date. The manifest describes only
explicit source contexts; the adapter does not generate a Cartesian product.
`chosen` must match the captured `selected_plan_case_id`. It never defaults to a
particular spending case. A selected `verified_unrestricted` context with an
unknown opening stays selectable with `UNAVAILABLE` estimates and explicit
reasons; it is neither omitted nor relabeled as hypothetical. Bank contexts differing only in income basis must
have identical source paths. Funding contexts retain the producer's replacement
income output without adding it to the observed base.

All five horizons begin the next local calendar day. `start_inclusive` and
`end_exclusive` preserve the exact local-date interval across DST. All amounts
are signed integer minor units already rounded by the producer. Transport does
not round a range into a scalar, multiply a partial month, add a bonus to annual
gross, or convert a noncash expectation into a receipt. A range can have one or
both endpoints missing, each with its reason. Known zero and unknown are distinct. A source conservative minimum uses
`{kind: CONSERVATIVE_SOURCE_SCALAR, value_minor, unknown_reason}` instead of a
lower/upper range. Consumers label that scalar explicitly. No range endpoints
are manufactured from it. An absent source zero-crossing date stays null with
its own reason; the adapter never substitutes a first-negative date or computes
another crossing.

`estimate_state`, `completeness_state` and `safety_state` are independent.
`INCOMPLETE` may carry useful conditional estimates with explicit conditions.
`SOURCE_VERIFIED` safety requires the bank measure, a verified unrestricted
opening, complete mandatory-flow coverage and complete, evidenced estimates
with known thresholds/signals. It remains a historical source assertion; all
consumer cards are qualified and current safety stays unverified. Funding never
carries a negative-bank-cash or checking-shortage assertion. Reserve breach,
negative bank cash and checking shortage are separately typed. No available-
surplus field is inferred from positive closing values. Horizon cash-in/out
ranges and minimum, first negative, reserve-breach and zero-crossing dates retain
their source values or independent null reasons. Known dates must lie within
the selected horizon, and context as-of dates cannot exceed verification in
the context timezone. These summary dates differ from original detail-event
windows. A detail window's inclusive earliest/latest bounds need only overlap
the largest context horizon: `earliest < end_exclusive` and
`latest >= start_inclusive`, with `earliest <= latest`. The same overlap rule
selects each shorter horizon's page. Preserve both original bounds even when
they extend before the start or beyond the end; never clip or drop an overlapping
window. Overlap and the producer's `included` flag do not establish occurrence
inside the selected horizon, and consumers never recompute producer totals from
these pages. Wholly disjoint or reversed windows reject. Undated items remain
unplaced. This corrects the semantic validator and selection behavior without a
schema shape/hash change; the current pin remains
`951a59286ca22396f2084484490aa1d54b47c0ee10c0bc727580134a31c4b925`.

Provider-as-of, retrieval and observation clocks each carry an independent
value or null reason. An unknown provider clock is not filled with a retrieval
time. Each source separately retains its balance-as-of date, timezone, four
clocks, declared content hash and refresh state. A three-role manifest identifies
the dated, funding and income engines and their input hashes. The dated engine
matches the top-level engine coordinates. `refresh_state` remains visible,
including `OFF`. Unknown encumbrances
and thresholds stay unknown. Accounting and projection head changes invalidate
previous native answer receipts independently of current grant revocation.

## Exact source clock meanings

| Contract field | Meaning and producer mapping |
|---|---|
| `sources[].provider_sync_at` | The exact Books `provider_sync_at` timestamp: the provider's reported last synchronization. Preserve its timestamp/offset when supplied; otherwise null with its own reason. |
| `sources[].provider_as_of` | A separately reported timestamp describing the data's effective time at the provider. It does not mean last synchronization. Without an explicit producer data-as-of field, it stays unknown. |
| `sources[].balance_as_of` | The source's accounting balance date in its declared timezone. A date does not imply a midnight synchronization timestamp. |
| `sources[].retrieved_at` | When the source material was fetched. Retrieval does not establish provider freshness. |
| `sources[].observed_at` | When that source observation was captured. It is independent of retrieval and synchronization. |

There is no clock alias or fallback. Root `source_clocks` retain the producer's
aggregate data-as-of/retrieval/observation fields only; they do not summarize or
replace the per-source synchronization clocks. Known source timestamps cannot
postdate projection verification. Unknown synchronization remains visible in
`planning_sources` and the member source-provenance drilldown even when another
clock is known. This mapping establishes transport meaning; exact native field
and context parity still needs producer evidence before adoption.

## Complete details with bounded consumption

An export remains capped at 48 declared contexts, 10,000 unique detail bodies
and 8 MiB. Inline `detail_events` remain supported up to 10,000 total rows.
Alternatively, `shared_event_inventory` holds each body once, and every
`detail_manifest` lists ordered `record_ids`. Inline and shared layouts cannot
be mixed. Shared record IDs are storage references distinct from original
`event_id` values: source variants may retain the same event ID in different
contexts with distinct record IDs, while byte-identical bodies may be shared.
A context cannot include the same source event identity twice.

`shared_inventory_sha256` hashes the canonical ordered inventory array. Each
context's `row_count` and `rows_sha256` bind its entire ordered materialized
row array, adding its `context_id` and excluding the inventory-only `record_id`.
Canonical arrays use sorted object keys, compact JSON separators, UTF-8 and no
NaN/Infinity. The projection content digest binds the inventory and references.
Unknown/duplicate references, unreferenced inventory entries, conflicting IDs,
wrong counts/digests and undeclared contexts reject before head advancement.
No value, inclusion policy or date is inferred while materializing a reference.

The producer-fit handoff reports 3,215 original native detail items, 35,988
occurrences across the 18 oracle groups, and 38,382 when bank aliases expand.
Those are different populations. The reported dated-only count of 35,646 still
exceeds the former inline capacity. The updated synthetic shared fixture tests
3,307 body variants across 3,215 original IDs, 18 source groups with 35,988
occurrences, 24 aliases with 38,382 ordered references, and a largest context of
2,818 events. This is an export-fit test, not proof that synthetic bodies or
memberships equal the native export. Exact field,
variant, alias and ordering parity remains the producer's separate review gate.

Validation expands one bounded context at a time. Storage keeps bodies and
owner/context-scoped references separately. Query metadata excludes both the
inventory and full reference lists, and each detail query joins only its scoped
page. Sharing does not put repeated event payloads into CoS context or raise
the existing per-call, per-turn, row-page or 8 MiB limits. Large-export runtime
and recovery performance still need their own evidence before activation.

Each row retains its date range, account/payment route, measure via context,
direction, amount range, bank/debt effects, evidence and conditions. A card
purchase has no bank debit. A known settlement reduces bank cash and debt by
the same amount once. Internal household transfers have zero net bank/debt
effect. `recognition_id` identifies one economic recognition within a context:
only one row with that ID can be included. An annual provision and its eventual
bill cannot both be included for the same recognition. The exporter must map
its actual inclusion policy to these keys; the adapter cannot infer it.
Undated items have both date endpoints null and a nonempty
`date_unknown_reason`. They are not moved to today, an as-of date or a horizon
boundary. Detail pages retain them as explicitly unplaced items alongside the
selected dated rows; the UI and native renderer make that distinction. Source
horizon totals remain untouched. Partial invented date intervals are rejected.

The member surface selects one context and horizon and pages detail 50 rows at
a time. Native `finance-query` uses `planning_contexts`, `planning_projection`
or `planning_events`, returning at most one item per page with `next_offset`.
A typed measure/scenario/income/opening selector, optionally disambiguated by
case/currency/timezone/as-of, reaches an exact context without scanning the
index. Summary views (`balances`, `signals`, `dates`, `provenance`, `clocks`) are fixed bounded
sections that each retain the applicable conditions and exact publication pins.
Status, selection and one summary fit the existing three sequential-call limit;
separate follow-up turns retrieve other sections. The limits are unchanged.

`planning_gaps`, `planning_issues`, `planning_evidence` and `planning_sources`
resolve one scoped record per native page; `planning_engines` returns the
three-role manifest. `gap_group_id` selects original issues within one group.
Member reference pages contain at most 50 records with continuation controls.

The 128 gap-group and 16 horizon-condition-reference limits remain unchanged.
Optional `gap_issues` supports up to 256 original source issues in the same
8 MiB payload, enough for the reported 141 issue IDs without presenting them
all in one model response. Every issue retains its original ID, required flag,
owner audience, explanation/input/timing/evidence and exact per-context horizon
memberships. `issue_manifest` pins the ordered issue digest, total count and
required count. A grouping is explicit (`issue_group: true`) and reversible;
all children are individually retrievable. Groups cannot broaden or omit child
membership, hide required issues from a horizon's conditions, or declare
complete/safe evidence while a required issue remains. The synthetic case
preserves 141 IDs, 49 required globally and exactly 46 required per horizon.
The producer must confirm original flags/memberships against its source before
export; transport cannot independently determine whether supplied source facts
are true.

Group reads show the total/required issue counts for only the selected context
and horizon. A group pointer is not a cleared condition. Evidence drilldown
returns a protected artifact reference and declared hash, with
`source_content_state=not_included`; it neither returns source bytes nor
independently verifies the referenced file. Private URLs are not accepted in
explanation fields. References and issue memberships are indexed by context so
bounded reads do not parse all detail or issue arrays.
The generic existing reports retain their previous pagination limits. No
projection cache is enabled. If one fully qualified result exceeds the existing
native result budget, it fails closed rather than dropping conditions. The
accepted accounting hash, projection hash, selected context/horizon and exact
current authority are bound by the encrypted result/answer receipt. Projection
hashes transitively bind the source input digest and the complete manifest.

## Validation and recovery

Hosted synthetic tests cover range/null semantics, selected-case changes, bank
income-basis invariance, next-day/DST boundaries, route conservation, full
pagination, rejection before head advancement, owner isolation, replay/restart,
actor changes, projection replacement after retrieval and member/native card
parity. The hosted browser also selects funding separately from bank cash and
inspects conditional status, source refresh and detail availability. Test code
is not passing evidence until its exact run completes.

Disable finance publication processing to stop both accounting and projection
reads/ingress. Immutable accepted accounting and previous projection revisions
remain in encrypted history. This new projection schema needs a schema-aware
runtime for replay; a later deployment must verify compatible recovery before
activation. No private import, live activation or rollback claim follows from
these synthetic checks.

Native summary sections keep source date observations and root source clocks in
their own fixed `dates` and `clocks` views. `balances` carries all amount and
minimum values; `signals` carries thresholds, booleans and their unknown reasons;
`provenance` carries engine input and verification pins. Every section retains
all horizon conditions, evidence IDs, exact context and publication bindings.
This avoids dropping source fields or enlarging the 2,000-byte response budget
when original issues are grouped. The member card still retrieves the complete
summary. Full-budget matrix coverage includes both ranges and grouped scalar
minima across all declared synthetic contexts and horizons.

Storage `record_id` identifies a body variant, while `event_id` keeps the
original source identity. The coordinator reports structural review passed at `63691cc7` and schema
`951a59286ca22396f2084484490aa1d54b47c0ee10c0bc727580134a31c4b925`.
Books-side canonical payload construction/parity work is separate; it grants
no private transfer, import, adoption, merge or activation here.
