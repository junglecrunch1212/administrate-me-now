# Accepted Books publication v1 handoff

The JSON Schema in this directory is the canonical interchange. Its `.sha256`
file hashes the exact UTF-8 file bytes. A publisher sets `schema_sha256` to that
value. `content_sha256` hashes compact sorted ASCII JSON of the complete
publication, excluding only `content_sha256`; numbers must be finite integers
in minor units. The implementation validates the whole snapshot before emitting
one privileged event. The synthetic examples live in `tests/fixtures/finance/`.

This is a candidate handoff, not confirmation of Books adoption or live access.
A coordinating review must acknowledge the exact schema and hash before an
exporter adopts it. Only explicit accepted revisions may enter; paused recovery
holds cannot be cleared by a publication. A successor cannot accept or discard
previously excluded held revisions. Same successor slot and same hash is
idempotent; forks/conflicting identities reject and retain the last good head.

## Field semantics

- `tenant_id`, `book_id`, `entity_id`, `owner_id`: canonical IDs. Only a household
  entity and one private owner are supported in v1. Cross-entity and joint
  privileged processing need a reviewed authority extension, not relabeling.
- `audience_policy_id` and `audience_policy_version`: publisher's reviewed policy
  reference. They grant no app/model permission. Runtime owner scope and current
  service authority are checked independently on reads and delivery.
- `accepted_journal_revision_ids`, `excluded_pending_revision_ids`: exact accepted
  and held sets; no mutable sheet tail is implied. Revisions stay immutable.
- `published_at`, `accepted_at`, per-source `observed_at`/`accepted_at`, and
  `valuation_date`: separate clocks. Source observation precedes source
  acceptance, which precedes publication acceptance. Ingestion rejects future
  clocks beyond five minutes of tolerated clock skew.
- Reports carry their own release, basis, period, model/version and five quality
  dimensions. `ready` requires complete dimensions; the consuming API still
  qualifies historical snapshots until current freshness is independently
  verified. An unavailable report carries no metrics. Unknown metrics are null,
  not zero. Evidence references are opaque internal IDs, never Drive URLs.
- `accounting_actual` and `cash_movement` are different report bases. Principal,
  settlements, transfers and expected reimbursements must be classified by the
  accepted producer's inclusion policy. `cost_reference` is a chosen analytical
  reference; it is **not** an alias for economic actuals. A standalone economic
  cost report/model is outside v1 and needs explicit reviewed semantics.
- `planning_input_version`, `planning_model_id`, `planning_model_version` pin the
  accepted external engine. Existing scenario values cross unchanged; this
  interface does not reproduce their spreadsheet arithmetic.

## Optional dated cash projection

`cash_projection` contains the accepted Books engine's outputs. It is not a
second engine. It requires `current`, `expected`, and `chosen` scenario IDs and
all five horizons: 7, 30, 90, 180, 365 days. Every end date equals `as_of_date`
plus the horizon's calendar days in the declared IANA timezone. Scenario event
rows retain earliest/latest dates, amount, direction, kind and certainty.
They represent planned cash events, never automatically realized transactions.
Known event amounts require protected evidence references. The reconciled
opening date cannot be later than acceptance in the declared timezone. Summary
and event result identifiers use separate namespaces to prevent collisions.

`opening_liquid_minor`, `opening_reconciled`, `mandatory_flow_coverage`, and
`chosen_floor_minor` are explicit inputs/declarations. Without a reconciled
known opening and complete mandatory-flow coverage, closing/minimum balances
and shortage/surplus signals must remain null/unavailable. The publisher's
model/version owns shortage/surplus definitions and chosen assumptions; no
universal reserve target is imposed. The API does not compute these fields.
It indexes them into `cash_projection` summary and `cash_events` detail reports.
Both reports retain the opening, coverage declaration and chosen threshold so
consumers can see the assumptions behind shortage/surplus signals.

Current freshness and completeness are different. Historical dated projections
can be useful qualified snapshots while requiring a fresh source review before
an affordability or payment-safety conclusion. Optional unknown inflows must
not silently become guaranteed cash. A zero amount is valid only when it is an
accepted measured/model output with evidence, never a missing-value default.

The dated example is entirely synthetic. It demonstrates a shortage before a
later expected inflow despite a higher ending balance, and a distinct chosen
spending scenario. It establishes transport/semantic tests, not the correctness
of the Books engine or private household plan.
