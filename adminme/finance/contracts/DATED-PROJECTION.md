# Candidate dated projection transport

`household-books.dated-projection.v1.schema.json` and its `.sha256` file define a
new projection interchange. Accepted-publication v1 and its hash are unchanged.
This contract preserves producer outputs; it neither computes a forecast nor
establishes correctness of the Books engine. Native field mapping, source
adoption and private parity remain separate review dependencies. This supports
a declared subset of Books v1.2 outputs; it is not lossless v1.2 support. Daily
balances, card-debt paths and per-account shortfalls are explicitly unsupported
in the contract and visible on every consumer result. Synthetic
fixtures are not a representation of the actual twelve native context tuples.

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
both endpoints missing, each with its reason. Known zero and unknown are distinct.

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
the context timezone.

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

An export may contain up to 48 explicitly declared contexts and 10,000 detail
rows within 8 MiB. Each context has an exact row count and digest of its ordered
rows. Validation rejects omissions, duplicate event identities, undeclared
contexts and inconsistent manifests. The synthetic acceptance fixture contains
12 contexts and 3,215 total rows; it tests transport coverage only. A separate
synthetic 24-context fixture adds unavailable verified-opening choices. Neither
fixture establishes the actual native export mapping.

Each row retains its date range, account/payment route, measure via context,
direction, amount range, bank/debt effects, evidence and conditions. A card
purchase has no bank debit. A known settlement reduces bank cash and debt by
the same amount once. Internal household transfers have zero net bank/debt
effect. `recognition_id` identifies one economic recognition within a context:
only one row with that ID can be included. An annual provision and its eventual
bill cannot both be included for the same recognition. The exporter must map
its actual inclusion policy to these keys; the adapter cannot infer it.

The member surface selects one context and horizon and pages detail 50 rows at
a time. Native `finance-query` uses `planning_contexts`, `planning_projection`
or `planning_events`, returning at most one item per page with `next_offset`.
A typed measure/scenario/income/opening selector, optionally disambiguated by
case/currency/timezone/as-of, reaches an exact context without scanning the
index. Summary views (`balances`, `signals`, `provenance`) are fixed bounded
sections that each retain the applicable conditions and exact publication pins.
Status, selection and one summary fit the existing three sequential-call limit;
separate follow-up turns retrieve other sections. The limits are unchanged.

`planning_gaps`, `planning_evidence` and `planning_sources` resolve one scoped
reference per native page; `planning_engines` returns the three-role manifest.
Gap metadata provides an explanation, required input, timing and evidence IDs
within its owner, context and horizon. Evidence drilldown returns a protected
artifact reference and declared hash, with `source_content_state=not_included`.
It does not return source bytes or independently verify the referenced file.
Private URLs are not accepted in the explanation fields. References are indexed
by context so bounded reads do not parse the complete detail/evidence arrays.
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
