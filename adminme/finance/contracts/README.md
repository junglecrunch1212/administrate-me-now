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

## Candidate versioned planning-view extension

The coordinating review reports that Books Financial Projection v1.2 cannot be
represented losslessly by v1's scalar bank-cash projection. The existing v1
schema and SHA remain frozen. The next interchange version will preserve
Books-produced planning views; it will not reproduce their forecasting math.
The candidate `household-books.dated-projection.v1` contract is separate from
accepted-publication v1. Its schema, SHA file, validator and private file-adapter
seam are now present; hosted validation and exact native exporter mapping remain
pending. It is not adopted by Books or enabled for private ingestion.

The extension will be a separately versioned dated-projection export anchored
to the exact accepted accounting publication ID and content hash. Projection
verification cannot advance accounting acceptance or clear recovery holds.
An accounting-only v1 publication may continue to omit `cash_projection`.

The extension must retain these separate coordinates:

| Coordinate | Distinction to preserve |
|---|---|
| Measure | `BANK_CASH_SUBSET` versus `CASH_EQUIVALENT_FUNDING_NOT_BANK`; they are non-additive views |
| Income basis | `OBSERVED_BASE_ONLY` versus `PLAN_MODELED_NET_FUNDING_NOT_PAYROLL` |
| Opening basis | `verified_unrestricted` versus `observed_cash_hypothetical` |
| Result | Source-produced lower/upper ranges and their missing endpoints, not an invented scalar |
| Status | Explicitly conditional `INCOMPLETE` planning estimates remain distinguishable from released safety conclusions |

The candidate preserves immutable projection/event version provenance and
predecessor checks, and exposes bounded planning-view queries and rendered
qualifications. See `DATED-PROJECTION.md` for the precise transport contract.
Only declared source combinations will be indexed; no cross product or sum of
views is inferred. Conditional planning values must not be relabeled ready,
verified openings, payroll, bank cash, or realized transactions. Existing v1
cash safety and accepted/recovery authority gates remain unchanged.

Synthetic acceptance must prove both versions replay unchanged; reject measure,
income/opening-basis and range/status conflation; preserve conditional ranges
with their exact evidence and limits; and retain owner isolation, held-revision
exclusion, current-grant checks and bounded results. Private financial values,
Sheet identifiers and live data are excluded from fixtures and logs. Exporter
adoption requires review of the exact new schema/hash after that mapping and
those tests exist; this plan authorizes no private import or activation.

The detailed review additionally requires the exact source-selected Plan case,
engine/input digests, next-day start and local inclusive/exclusive date bounds.
Modeled net funding replaces observed base only in the funding view; bank paths
remain invariant. Annual gross already includes bonuses. Unknown provider
clocks, thresholds and encumbrances retain nulls with reasons. Estimate,
completeness and safety states remain independent: reserve breach, negative
bank cash and a particular checking-account shortage are different signals,
and a positive remainder is not available surplus.

The native export has 12 contexts, five horizons and 3,215 combined detail
events. A protected manifest and bounded pagination must preserve the full
coverage, with explicit measure/context and payment/account routes. Card
purchases cannot become bank debits; settlement cash/debt legs, neutral
internal transfers, provisions and eventual bills must retain the producer's
inclusion semantics without counting a flow twice in one measure. These counts
are integration requirements, not permission to copy private source rows.
Exact native field mappings and the 12 context tuples still need a reviewed
source mapping; this adapter must not invent their Cartesian product.


## Bounded app-binding discovery

**MODERNIZE:** the existing authenticated finance-status tool accepts
`include_bindings=true` as an exclusive question, optionally selecting `book_id`.
It preserves existing finance processing, model-member, private direct-audience,
current delegation, admission, disclosure-receipt and final revocation controls.
No grants or credentials are created. Access unavailable is not verified absence.

The already-open EventLog connection selects only exact tenant/owner accounting
and dated-publication metadata in one read transaction. No raw payload, metrics,
provider source or journal lines cross the worker. The receipt fixes the scoped
last event, checked history digest and completeness. Unsupported schemas,
broken predecessor/anchor chains, unavailable reads and bounded-out scans cannot
produce empty or populated success. Limits are 256 events, 32 MiB of source
payload examined, 100,000 SQLite VM steps and 100 ms of worker execution; exceeding
any limit returns incomplete history. A receipt describes retained app history
at that watermark, not current source coverage or Books acceptance. Discovery
is not an initializer and cannot prove absence outside the selected history.

This enabling change does not supply the narrower finance-only CoS permission,
live bindings, private transport/import operation or deployment authorization.

### Owner metadata discovery and separate CoS access

**MODERNIZE:** the direct authenticated-owner operation is
`POST /api/member/v1/finance/bindings`. It needs the existing trusted member
ingress and current principal identity in the owner's own view. It needs no CoS
delegation, provider enrollment, financial model or publication-processing flag.
The **Books connection metadata** card at
`/member/settings?section=cos-work` exposes the controls and receipt. Both owner
and CoS metadata access default off.

`GET/PUT /api/member/v1/settings/finance-metadata` reads or changes only that
owner's two flags. PUT requires an exact current revision, strict boolean flags
and idempotency key. It appends `settings.finance_metadata_changed@v1` before
reporting success; no deployment configuration, plaintext journal or credentials
are edited. Latest valid encrypted control survives restart, stale revisions
fail, failed persistence does not enable access, and retrying an old enable after
a newer disable returns current disabled state. Direct owner reads append
`finance.metadata_inspected@v1`, with truthful member attribution and no raw
financial values. The receipt records inspection, not proof of network delivery.

The native `administrateme_finance_bindings` tool calls
`POST /api/openclaw/v1/tools/finance-bindings`. Its separate CoS flag does not
grant authority: the existing loopback credential, exact member/private host turn
and current `read_context` delegation are still required. It retains the existing
service-aware tool audit. Financial queries, imports, advice and model enablement
remain separate. Metadata does not create a financial-answer session; ordinary
private CoS transcript retention still applies.

Both paths share the owner-filtered fixed-watermark reader described above,
permit one in-flight metadata read, and cap results at 8 KiB before the constant
provider-effect field. Disabled, busy, unavailable and incomplete states never
assert empty history. Reads and control transitions share a per-owner lock;
after the durable audit, a fresh control read and synchronous identity/role
check precede return with no intervening await. CoS additionally re-resolves its
current delegation and exact audience. Revocation cannot be passed by cached
permission. No new identity or database connection is introduced.

Tenant/owner come from current authentication. One explicitly supplied existing
household Party ID can be checked through the scoped Parties query; only kind
eligibility, ID and source event are returned, never names or contact data.
`not_visible_or_missing` is not proof of global absence. No ID is enumerated,
generated or selected as the Books binding. There is no local Books-registration
or publisher-policy registry: reviewed publisher records remain authority for
book ID and policy ID/version. Retained accepted history can report previous
bindings. Verified-empty history proves only scoped app history at its watermark,
not absence of publisher registration or authority to initialize a publication.

### Delivery dependencies and recovery

The dated PARTIAL snapshot is independent of daily Sheets refresh, formal
statements and direct-provider freshness. Keep daily automation off. Preserve
as-of/source clocks, unknowns, holds, partial coverage and non-additive views;
never imply daily-current data or reliable affordability/payment safety.

1. Engineering qualifies owner controls, native metadata, restart/audit,
   isolation and final denial through hosted production composition and synthetic
   browser checks. Current PR scope is `system-wide`, not a separately qualified
   metadata component. The existing protected release selector requires exact
   release-scope evidence plus Linux `system-consistency`, macOS
   `system-rehearsal` and `bridge-installed-runtime`; the protected
   `deterministic-consistency` result and current updater evidence remain binding.
   Development-draft guards and the full-finance 10% latency gates stay unchanged.
   A genuinely separate metadata release would first require a bounded component
   contract and positive/negative selector tests; no relabeling substitutes for it.
   Recommendation: retain this PR's system-wide artifact path and, if qualified
   and approved, initially enable only the direct-owner metadata control. Keep
   publication processing, model membership and CoS metadata off. This is a
   qualified system release with narrowly selected behavior, not a metadata-only
   release or clearance of failed full-finance resource gates. A separate
   component would cut the owner router/control/audit schemas, bounded history
   reader, Settings UI and their tests away from the CoS tools, financial query
   facade, import/provenance and model pipeline; freeze remaining dependencies
   and prove unrelated/ownership/storage changes still select system-wide. That
   alternative needs implementation and qualification, not a new permission form.
2. After protected release and explicit deployment approval, the owner can enable
   **only their own metadata access** in the card, inspect the fixed-watermark
   receipt and disable it again. No CoS grant is needed for this prerequisite.
   Bound acceptance: correct owner identity/history receipt, hidden/absent entity
   distinction, disabled and wrong-audience denials, retained audit and restart.
   Live member rendering must use the authorized member-device procedure.
3. Existing authorities then supply the household entity, publisher book/policy
   ID/version and history disposition. The producer builds the canonical
   accounting envelope, closed package and exact dated-projection anchor. An
   independent reviewer/owner must inspect and pin the exact evidence set.
   `ClosedPackage.approved_evidence` enforces exact-set equality in candidate
   code; it is not a live approval UI. The package's index or summary-report hash
   cannot self-approve its evidence.
4. A private handoff requires exact package, destination ownership/audience,
   retention and transfer approval. OpenAI Library exact-version upload and
   materialization is a candidate reported by the coordinating task, not a
   verified transfer here. The source cloud path is not local to this host.
   The receiver must check every manifest byte count and SHA256 against the
   independently approved source manifest; no signed URL, public link or Git
   upload belongs in the handoff. No private file has been acquired by this work.
5. Financial advisory use still needs the narrow finance-only permission and
   supported publication/model activation operation. Current `read_context` is
   broader private-context reading, not a finance-only permission. Import and
   activation require action-time approval of exact bindings, digest set,
   destination and owner scope; no provider writes or other member data follow.
6. Before claiming usable projection/CoS, prove exact accepted and dated read-back,
   expected stale/partial qualifications, owner isolation and revocation on the
   activated immutable artifact. The daily engine is not a prerequisite.

The concrete metadata recovery operation is PUT to the same Settings endpoint
with both flags false, the current revision and a fresh request ID (or uncheck
both boxes and save in the card). Read back disabled state and a denied metadata
read; the append-only control, inspection and publication events remain intact.
This supported transition needs no new CoS grant or adapter-config migration.
Do not revoke unrelated standing permissions. After the new event types exist,
prefer feature disable on a schema-aware runtime. Binary rollback requires a
predecessor proven compatible with retained state; the existing guarded command
is, with actual reviewed paths substituted at activation:

```sh
python -m bootstrap.cli --instance-root /absolute/reviewed/instance rollback \
  --recovery-repository /absolute/clean/protected-main-checkout
```

Do not run it to test a candidate. Preserve the healthiest compatible runtime
and retained history. A failed history read, privacy denial failure or new
workflow regression triggers recovery and isolated diagnosis; rollback cannot
erase disclosure. No live setting, grant, transfer, import or deployment is
performed by this code/test work.
