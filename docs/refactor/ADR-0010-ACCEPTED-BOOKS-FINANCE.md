# ADR-0010 — Accepted Books publications and bounded finance conversations

Status: implemented candidate; synthetic validation and activation gates apply.
Date: 2026-10-23

**SUPERSEDE** ADR-0007's XLSX planning authority for a Books-owned book. Books
owns accepted accounting/planning publications and its scenario math. The legacy
XLSX path is not reused for this namespace. No automatic migration or dual write
is authorized. Production activation is an encrypted, self-owned control bound
to one existing book/entity/audience-policy tuple. That owner's dashboard reads
only the selected accepted book. Other members and the shared legacy Finance
workbook retain their existing authority and reverse processing. No global
workbook disable or household-wide cutover follows from one owner's activation.
Legacy static Finance switches remain restricted to credential-free fixtures.

**PRESERVE** ADR-0001's dependency direction, one Python runtime, OpenClaw model
and scheduling boundary, domain approval, privileged owner access, observation,
and encrypted append-only history. No provider token reuse or provider feed is
introduced. Private publication ingress and finance model access default off;
production configuration does not enable these settings automatically.

**SUPERSEDE** the mixed dashboard for an owner with an accepted Books publication.
The Finance page leads with that publication, its dated projections and owner
choices. Legacy workbook forecasts and provider-derived plan cards are not
rendered alongside it, including when reopening an older Finance view URL.
This presentation change does not itself enable import or model processing.

**MODERNIZE** Finance's empty state: unknown is null, partial account observations
are qualified, forecasts require authored inputs, and unreleased statements are
unavailable. A catalog route is not evidence of a supported calculation model.

The canonical interchange is
`adminme/finance/contracts/household-books.accepted-publication.v1.schema.json`.
Its adjacent `.sha256` is the exact UTF-8 file digest. The synthetic publication
is `tests/fixtures/finance/accepted-publication.synthetic.json`. The application
owns this schema and tests. A Books exporter must adopt the same version/hash;
no source adapter may invent a parallel shape. No Sheet edits are part of this
change. Initial exports contain bounded accepted aggregate metrics and scenario
results, with exact revision references, separate source/acceptance clocks,
quality dimensions, report release states and opaque owner-only evidence IDs.
Raw transaction/receipt acquisition is outside this interface.

Canonical content hashing excludes only `content_sha256`, sorts object keys,
uses compact ASCII JSON and disallows nonfinite values. Schema hashing uses the
unmodified file bytes. A whole publication validates before a single private
`finance.publication_accepted` event. The prior-publication successor slot has an
event-log idempotency key; conflicting content or forks reject. The money
projection writes immutable history, indexed metrics and the current pointer in
one transaction. Partial validation never advances the pointer. A crash after
commit is resolved from the same receipt and replay; it is not permission to
read pending Books recovery revisions or clear a recovery hold.

Numbers preserve basis, currency/unit, period, owner/allocation, inclusion policy,
quality and evidence. Accounting actuals, cash movement, observed values, chosen
budget, cost references, forecasts and intended obligations cannot mix silently.
The four synthetic scenario results demonstrate unchanged export, not household
scenario validation. Missing openings/coverage block the associated reports.

Owner-only results use the private book namespace and authenticated actor.
Another adult, a joint session, child, coach, family audience or professional
gets no account existence/counts or source link. Joint processing and authorized
source proxies require additional reviewed authority support. Existing generic
read-context grants do not imply privileged model-processing consent. Source
instructions are data, never policy. The runtime may return a preparation route
without returning private evidence.

The small native facade has status/query/calculate/job operations. Full F01–F31
metadata stays server-side; persistent context is a bounded status pointer.
Calculators accept named typed models and exact arithmetic, not expressions or
SQL. Accepted scenario outputs are returned unchanged. Complex tax, legal,
benefit eligibility and unvalidated strategic variants have explicit model and
professional boundaries, with useful evidence/preparation workflows.

Heavy work remains unavailable until reserved general-CoS capacity, process
termination/memory enforcement, durable terminal receipts and mixed-load
acceptance are proven. A disabled job route is not an implemented worker.
Protected caches are optional; private data is not persisted in a generic cache
or vector corpus. Resource limits and remaining gates are recorded in
`FINANCE-RESOURCE-GATES.md` and the capability acceptance ledger.

Activation requires separate owner authorization, exporter schema adoption,
private accepted-data comparisons, model/policy/tool verification, shared XLSX
preservation, guarded update/backup/rollback, authenticated member acceptance and
measured general-CoS performance. This candidate authorizes none of those live
operations.

## Dated projection export

The optional `cash_projection` object preserves Books-produced current,
expected and chosen scenarios, each with 7/30/90/180/365-day horizons. Horizon
end dates are `as_of_date + days` in the declared timezone/calendar-date model.
The opening is a reconciled liquid-cash value, not net worth. Mandatory-flow
coverage is explicit; missing opening/coverage forbids numerical safety results.
Shortage/surplus dates are accepted engine signals, not recomputed here.

`cash_projection` and `cash_events` reports index these accepted values and
planned events unchanged. Events retain direction, kind, certainty, earliest/
latest dates and protected evidence references. They are forecasts, not actual
posted transactions. Event detail defaults to the current scenario and 90 days;
callers can explicitly select a supported scenario/horizon. Economic-cost
measures are not separately defined in v1; a later reviewed schema version must
specify them rather than relabel cash or accounting numbers. The dated synthetic
fixture is `accepted-publication.dated.synthetic.json`. Schema adoption remains
a coordinating review gate, independent from this producer-neutral reader.


## Owner activation and approved package import

**MODERNIZE** activation: the authenticated principal saves a fixed existing
book binding and separate Books-dashboard / CoS-analysis permissions at
`/api/member/v1/settings/finance`. Both default off. CoS additionally requires
the existing private-context read grant; the Finance control never creates or
widens generic delegations. Current owner/book authority is re-read after tool
retrieval and audit, before disclosure. A pending owner-control change waits only for an already-admitted atomic
append. Upload and validation do not hold the owner lock; fresh approval, role,
control and head checks precede the commit. Once revocation is committed,
subsequent disclosure is denied. An unavailable or malformed owner
control makes that owner's Finance dashboard
explicitly unavailable rather than substituting legacy totals, without preventing
other owners' Finance or unrelated household services from starting. A verified
empty or intentionally disabled control preserves the existing legacy view.
Durable controls survive restart. Source identity creation and provider enrollment are outside this seam.

The owner separately approves a `household-books.closed-import.v1` manifest with
exact component hashes, byte limits, source snapshot and independently reviewed
evidence hashes. Approval expires in 30 minutes and is invalid after any control
revision. The closed upload carries only those pinned canonical publications,
provenance index and blobs as base64 JSON. It cannot supply its own approval,
resolve paths/URLs, edit the Sheet or enable a refresh schedule. One global import
admission bounds body/decoder concurrency; decoding is off the HTTP loop and
admission remains held until cancelled native work has settled.

**MODERNIZE** import execution under the event/projection ownership contract:
stream the closed packet through 64 KiB frames to one short-lived calculation
child at a time. Full exact-byte, schema, lineage and independent-evidence
validation stays mandatory. The parent receives immutable encoded events with
process-local receipts bound to type, tenant, owner, book, source, idempotency key
and payload hash; its ordinary append API still validates payloads normally.
One authorized atomic append publishes the accepted event batch. Parent-owned
keys, handles, current authority, durable audit and event order never move to the
child. Preparation and a contiguous Money delivery group use sequential child
lifetimes; each group is restricted to one tenant/owner/book and at most 131
actual committed events, including provenance. The bus reports active draining
until the final child receipt and reap complete. This is no new service,
scheduler or provider capability. Authority is re-read on the parent-owned
log connection after admission/queue waits and immediately before COMMIT.
Final disclosure rechecks the current owner and policy after audit persistence.

Money's existing encrypted writer has one dedicated serialized thread for open,
reads, transactions, replay, snapshot, publication and close. Cross-projection
queries retain fences; Money callbacks cannot invert the general/authority-worker
wait order. Projection children receive actual committed event bytes. Their
bounded SQL channel permits only twenty fixed Money statements with exact
owner/book/tenant parameters; a parent-side authorizer denies transaction control,
other tables, functions, PRAGMAs, DDL and attachments. The parent commits or rolls
back the whole Money transaction and the bus advances only its successful prefix.
Replay uses the same full proof checks and actual archived bytes. Event pages are
byte-bounded without treating a short page as EOF.

**PRESERVE** Context Fabric's metadata-only graph contract from
`ADR-0008-DETERMINISTIC-CONTEXT-FABRIC.md`: for the three closed Finance event
types, delivery and replay request only envelope coordinates and lineage. SQL
omits these unused bodies before creating driver/Python payload values, while
preserving full bodies for Money and ordinary history reads. Node/edge values,
owner and visibility scope, source coordinates and successful-prefix checkpoints
remain identical to replaying decoded envelopes; the production import/rebuild
regression compares every graph column. Its effect on combined RSS and allocator
retention must be measured, not inferred from the absence of live Python objects.

Each child uses the same pinned Python with isolated import paths, a credential-free
environment, private stdio pipes, CORE=0 and a 60-second CPU limit. Parent watchdogs
bound preparation to 60 seconds and projection application to 30 seconds. Owned
termination has a three-second grace followed by a three-second kill/reap wait.
An uncertain reap retains exclusive admission and its active ownership record;
up to three delayed owned retries are bounded, and exhaustion remains quarantined.
Missing terminal or forced teardown is incomplete evidence. A child self-watch
exits if its parent disappears or its telemetry pipe closes. Python audit guards
deny writes, private file reads, sockets and descendants; only trusted code,
dependencies and self metrics are readable. Terminal success attests no guard
fault. These guards are **not an OS sandbox or hard RAM/disk quota**. No child
opens a database, plaintext staging file, provider or network connection.
The hosted native fixture allows only the exact isolated interpreter/script,
clean locale environment and parent-owned admission, with at most eight lifetimes
and one active child. Aggregate measurements include live and reaped child CPU,
RSS and conservative lifetime peak bounds. On Darwin the child RSS is explicitly
its lifetime peak, so that aggregate is an upper bound rather than instantaneous
child RSS. A Linux subreaper fixture checks abrupt owned-parent exit with blocked
input/output; it does not claim Darwin crash qualification. Terminal evidence requires
all launches accounted for and all children reaped. The changed Mini scope
(one added Money thread, one temporary child with its usage thread, parent
watchdog/possible bounded cleanup timers, and sequential child lifetimes) must
be reviewed before any native lab; the earlier scope is not reused.

Accounting and dated projection remain independent immutable events. An accepted
accounting import followed by a rejected dated projection returns an explicit
incomplete receipt. Conflicts never replace the last good head, and retrying exact
accepted bytes uses the existing idempotent receipts. Imports preserve owner
choices and chosen baselines. Terminal receipts contain identifiers and outcomes,
not financial values. Synthetic production-composition coverage lives in
`tests/acceptance/test_finance_activation.py`.

The separate hosted Linux import diagnostic uses valid synthetic 98-blob proof
of at least 18 MiB plus a dated payload of at least 6,020,614 bytes, preserving
the synthetic shared-inventory geometry (3,307 variants, 3,215 source IDs and
38,382 context references) with valid long identifiers. It records
provenance, envelope, dated and encoded request sizes independently; measures
upload, decoding, validation, archival, current/peak/retained runtime RSS, aggregate parent-plus-child RSS and CPU,
core request cadence and the owner-control critical section/revocation wait;
and checks exact terminal receipts, unchanged independent grants and every child
exit/cleanup. The resource probe measures revocation after the atomic commit;
separate functional tests cover revocation during validation and cancellation.
Both sampled aggregate RSS and a conservative sum-of-peaks bound are retained.
Its 90-second CPU, cooperative 512 MiB RSS and 180-second wall watchdog are
hosted diagnostic containment, not a replacement acceptance policy. One short
import with a single concurrent core request does not qualify sustained query
load, the household Mini, maximum permitted sizes, real-data conversion or
private transport. The existing native qualification scope remains separate.

An explicit `import_allocations` dispatch runs only a separate allocation
profile with the same synthetic package and runtime caps. It traces one Python
allocation frame from immediately before upload, retains raw core outcomes,
compares live allocations after GC and bounded subscriber settlement, and reports
remaining lag/degradation. Its instrumentation overhead cannot establish latency
or resource acceptance. Python-tracked bytes do not attribute SQLCipher native
allocations or allocator free pages; unexplained RSS is reported as such. After
recording the original retention observations, this diagnostic additionally
requests `PRAGMA shrink_memory` on the owned event-log and Money connection
threads, recording each connection's unchanged cache settings and before/after
RSS. These post-measurement probes never replace acceptance measurements, close
connections, or change persistent state or production behavior. The same opt-in
profile records glibc arena used/free bytes and one final test-only allocator
trim, preserving all original retention results. Receipt verification, connection
queueing, SQL insert/commit and authority-check spans distinguish waiting from
execution. Trimmed RSS and instrumented latency are never qualification evidence.

Event-log subscriber lag counts and latest IDs use one parent-owned read-only
SQLCipher connection, one owner thread and a 256 KiB metadata page-cache target.
Each query reads committed WAL state and closes its cursor; there is no cached
lag or grant. Its SQL authorizer permits only event IDs/types and counts, denying
payloads and mutations. The original writer/payload connection still owns all
approval reads, ordered atomic appends and both native authority checks. Closing
rejects new metadata reads and drains accepted native work on its owner thread,
including cancellation. Read-only backup inspection keeps its original single
connection. This removes demonstrated count-query waits behind Finance commits;
it does not claim to repair allocator retention or qualify the native workload.

Dated structural validation retains at most 32 successful receipts containing
only the pinned-schema and complete canonical-payload hashes. Every reuse hashes
all supplied bytes and returns a newly detached value; changed bytes or schema
require all validation again. No payload or authority is retained in this cache.
This avoids repeating the same expensive pure validation during event admission
and projection application. Source-proof verification, accounting anchors,
private scope, current control and final disclosure checks remain independent.

Closed-proof verification similarly reuses only exact deterministic inputs:
actual bytes of every stored blob and the index, complete publication bytes,
independent evidence pins, predecessor lineage and the compiled schema/contract.
Every reuse rereads and hashes the full closure; missing/corrupt stored proof or
changed approvals/predecessors miss the receipt and must validate normally.
Validation rejects bytes that change between fingerprinting and verification.
At most eight serialized lineage summaries, 2 MiB each and 4 MiB total, remain
in process memory; returned values are detached. This cache retains no raw rows,
grants, current policy or permission decision. Current anchors, owner authority,
transition/replay checks and final disclosure remain independently enforced.

The decoder releases its owned upload bytearray after parsing, and releases
encoded components as their exact hashes and canonical bytes are checked. Dated
validation snapshots containers before hashing while sharing immutable scalars,
avoiding another full set of decoded strings. These changes reduce transient
copies; retained RSS and core latency still require measured qualification.

Recovery for these new control/import events uses the schema-aware runtime:
disable both Books and CoS analysis with the current control revision and verify
denied disclosure. Keep accepted history, shared workbooks and existing generic
grants intact. An older binary lacking these event schemas is not an automatic
rollback target. Qualified activation must name its concrete supported updater
recovery command and preserve compatible encrypted state.
