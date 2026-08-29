# Migration map

Phase B live-closure amendment (2026-08-26): protected merge
`7ed10ac599e08dc1e616f6b9c3f0ab3b11dfb9b7` is active after an ordinary
same-runtime governed upgrade. Startup appended four exact refusals for the
previously non-terminal recovery controls; final recovery counts are 13
requested, 5 succeeded, 8 refused, 0 failed, and 0 pending. Full Doctor,
65,691/65,691 source settlement, all durable consumers, exact OpenClaw,
authenticated read-only browser scope, backup, rollback, observation, and zero
provider-effect proofs pass. This is **HARDEN COMPLETE LIVE** and introduces no
fact rewrite, checkpoint move, source replay, event/schema, projection,
canonical, credential, provider, Python, Node, or OpenClaw migration.

Phase B recovery-terminality amendment (2026-08-26): active merge
`cfde6b631edd034633e8dc63a0204d06de363ac3` completed v22 settlement with
65,685/65,685 source obligations accounted, zero source pending/missing/failed,
healthy durable consumers and adapters, observation active, and no provider-
called effect. A separate command-ledger audit nevertheless found four of the
new recovery requests without a linked assessment or refusal because v22's
non-anchor early assessment omitted recovery context. This repair
**PRESERVES/HARDENS** append-only control terminality: all early assessments
carry the exact recovery link, and startup processes only non-terminal request
controls through a fixed watermark and a 64-command ceiling. It changes no
checkpoint, rewrites no fact, performs no full source replay, and introduces no
event/schema, projection, canonical, credential, provider, Python, Node, or
OpenClaw migration.

Phase B rollback-schema amendment (2026-08-26): the second guarded attempt of
merge `af29e858` stopped before backup or release selection because the first
attempt's additive `adapter_cursors.retry_not_before_ms` column is not readable
by predecessor `d181fad`'s `SELECT *` dataclass construction. Gmail and Google
Calendar truthfully reported the exact same constructor failure; observation,
source accounting, event history, checkpoints, and provider state were
unchanged. This candidate **PRESERVES/HARDENS** rollback by moving retry timing
to the content-free companion `adapter_retry_state` table. Opening the candidate
store atomically copies any v21 timing values and drops only the incompatible
column, after which both the candidate and exact predecessor schemas can read
the database. The predecessor ignores the companion table. The guarded repair
policy recognizes only the two exact capability/instance/version/error tuples
left by this rejected rollback; it does not admit other adapter failures or
weaken post-switch full Doctor. No source, projection, canonical, credential,
checkpoint, provider, Python, Node, or OpenClaw migration is introduced.

Phase B replay-settlement amendment (2026-08-26): the guarded activation of
merge `ec3c776` was automatically rolled back because noise-filtering v21 did
not settle its full historical replay inside the unchanged one-hour release
window. The attempt **PRESERVED** observation mode, append-only source and
derived history, human decisions, provider-write denial, and exact predecessor
selection. Noise filtering advances from v21 to v22 so partial v21 events remain
immutable and cannot occupy the new generation's identities. V22
**MODERNIZES/HARDENS** replay by deterministically terminating non-anchor
sources as `no_candidate`/`thread-anchor-deferred-v1` without a language call;
the latest thread anchor retains the pinned `llm-task` invocation and all
candidate/reconciliation semantics. Existing classifications and semantic
features are retained rather than rewritten for a historical non-anchor. The
release repair exception now accepts only the exact four remaining
idempotency-race failures after predecessor backlog settlement, with zero
missing/pending sources and a 65,675-obligation floor. No source, projection,
canonical object, member decision, provider object, credential, runtime, or
OpenClaw pin is migrated.

Phase A runtime-repair amendment (2026-08-25): this candidate **PRESERVES**
immutable source facts, encrypted append-only history, deterministic replay,
member/tenant privacy, human decision authority, observation mode, guarded
provider writes, and the one-Python-runtime/one-pinned-Gateway topology. It
**MODERNIZES/HARDENS** additive adapter cursor state, lease fencing, durable
Calendar/Tasks progress, automatic half-open probes, bounded retry eligibility,
current-process OpenClaw health, explicit release eligibility, manifest-pinned
Node selection, incremental bounded-history indexes, fixed-point restart
quiescence, truthful Comms command settlement, and revision-bound Triage
pagination. It **SUPERSEDES** memory-only
provider watermarks, permanently open circuits, direct writes under missing
observation seams, ambient Node execution, indefinitely reusable healthy
reports, full-history rescans, false-success local command responses, and
snapshotless pagination. Existing cursor stores migrate retry eligibility into
a companion table while retaining their provider watermarks,
lease/error/circuit state, and dedupe ledger. Older event/schema facts remain
readable and are never rewritten. The main cursor table remains
predecessor-readable, so rollback can ignore the companion state and remains a
release-selection operation rather than a source or provider-data rewrite. No
required behavior is deferred. Phase B deployment is a separate
owner-authorized boundary.

Control-plane reboot amendment (2026-08-25): ADR-0013 retires G-001, the Outcome Harness, proposed G-002/ADR-0012, and the earlier executor instructions. Current delivery uses one protected GitHub pull request with ordinary CI and optional advisory review. Historical process descriptions below record past events only; they are not current instructions. V2 runtime, product, privacy, observation, backup, rollback, and provider-write boundaries are unchanged.

CO-018 mixed-backlog amendment (2026-08-15, historical): `origin/main` was
`cd08b50`, including PR #339's forward repair and PR #340. The active release
remained `d181fad`. Content-free read-only diagnosis
proved restored noise-filtering v20 is durably poisoned at
`ev_01KZP8DCKX0000` behind checkpoint `ev_01KZP8DCKK0000`; five repeatable
idempotency conflicts prevent checkpoint advance, leave five recovery commands
pending behind the poison, and cause later received and sent source obligations
to remain missing. This is a **PRESERVE/HARDEN LOCAL CANDIDATE**: the release
exception now declares independent received/sent ceilings whose sum must equal
the total ceiling, rejects undeclared source types and per-type overflow, and
uses the actual persisted v20 poison coordinates. It does not change replay,
recovery, source truth, runtime dependencies, observation, provider state, or
the installed release.

CO-018 release amendment (2026-08-13): PR #338 merged as `5b6c140`, but
guarded activation rejected it and restored `d181fad`. Candidate replay
appended four immutable `language_seam_IdempotencyConflict` assessments when
concurrent exact skill calls raced to record different results under one
idempotency identity. The forward repair makes the event log's first recorded
result canonical for every losing caller. Its first repair head declared only
those four exact failures for guarded linked recovery. During validation, restored generation
20 reached one exact retained collision, persisted its five-attempt poison
state, and paused later message assessment; one older allowlisted
`OpenClawUnreachable` assessment also remains current. The amended exception
therefore binds all five exact failure targets plus the exact v20
checkpoint/poison state and a bounded later-`messaging.received` backlog. The
20-row diagnostic display remains bounded while an additive content-free
all-record summary proves the complete backlog; the candidate can hydrate that
summary from the older predecessor at its exact watermark through a query-only
encrypted-log connection, and the stopped audit recomputes it independently.
This is a **HARDEN/FORWARD-REPAIR LOCAL CANDIDATE**; it performs no checkpoint
edit, source rewrite, event deletion, provider write, or runtime migration.

CO-018 update (2026-08-12): source-time and message-context correctness is a
**MODERNIZE/SUPERSEDE LOCAL CANDIDATE**. Gmail advances from v1.1.1 to v1.3.0 and emits
additive `messaging.received@v4` / `messaging.sent@v4`; prior versions remain
registered and readable. Noise filtering advances from v20 to v21 with an
explicit full replay, and the pinned classification skill advances from v6 to
v7. Replay appends new assessments/proposals and deterministic supersession
receipts only for untouched stale interpretations. Source events, member
feedback, decisions, canonical records, and provider facts are never rewritten.
An unchanged context-free deterministic fallback that v21 derives from the
same source under the same current high-signal rule retains its existing
pending proposal. The new assessment is appended, but replay does not
supersede the valid item or emit a duplicate merely because a v20 extraction
already owns the candidate identity.
If the prior proposal combines that deterministic identity with stale
contextual interpretation metadata, v21 supersedes the contextual proposal,
excludes that exact event from active-history dedupe, and appends the current
fallback under a deterministic replay-versioned proposal ID. The source and
old proposal remain immutable, while exactly one context-free replacement is
pending for review. The projection admits that replacement only when the same-
source/kind/key predecessor bears the exact successful system
`eligibility_rule_replaced` disposition; enrichment, dismissal, confirmation,
and later terminal states therefore prevent duplicate or reopened work.
The v21 replay stores exact relationship-edge provenance. Source-text
corrections are proposal/candidate/digest/span scoped; replay no longer blanks
unrelated authored asks. Invalid Gmail source timestamps quarantine instead of
creating false source chronology. Gmail message-local attachment IDs remain
non-admitting until a stable identity or content digest exists.
Multipart authored-text agreement and server-declared correction capabilities
are derived read/pipeline behavior only; they add no event, projection, or
provider migration. `messaging.sent@v4` additively retains `body_html` for
historical Sent observations; older v1-v3 facts remain readable and immutable,
but Gmail Sent bodies from those versions are not eligible as authored evidence
because the missing HTML alternative makes quote-boundary recovery impossible.
They receive a new content-free v21 assessment with reason
`legacy_gmail_sent_quote_boundary_unavailable`; any untouched system proposal
rooted in that now-unreliable body receives an append-only
`eligibility_rule_replaced` supersession, while member enrichment and decisions
remain permanent. Additive `triage.item.superseded@v3` binds current pipeline
supersessions to the exact inspected proposal revision; deterministic
projection compare-and-apply ignores a stale receipt when a newer member event
won append order. Prior v1/v2 supersession events remain readable with their
historical semantics. No source migration or provider refetch occurs. Model-window budget reservation, full-episode reconciliation
scope, and off-window relationship hydration are derived behavior and migrate
no stored facts.

Context Fabric advances from projection v1 to v2 with additive exact
relationship-decision and source-text-correction tables. Rebuild derives both
from immutable reject/confirm/correct/reinstate events. Existing projection
databases receive the `CREATE TABLE IF NOT EXISTS` additions through the normal
schema initialization before replay. `triage.item.proposed@v4` carries provider
source time and interpretation v2 while v1–v3 remain compatible. Rollback can
read the retained older facts and ignores new correction events, but it cannot
delete them and can temporarily restore the superseded weak context behavior.
No credential, provider enrollment, permission, Python/Node runtime, OpenClaw
pin, canonical model, or external object migrates.

CO-017 update (2026-08-12): terminal source-assessment recovery is
**HARDENED AND DEPLOYED**. Noise filtering advances from
generation/version 19 to 20. Existing source events, v3 assessments, candidates,
member decisions, canonical records, provider facts, and projection stores are
not migrated or rewritten. An accepted recovery appends a content-free request
and then a new linked v4 assessment under request-specific event and pinned
OpenClaw invocation identities. Source assurance deterministically chooses the
newest valid assessment for the exact source while retaining all predecessors.
Generation 20 consumes typed recovery requests directly; it does not use a
whole-log replay to escape an occupied prior-generation idempotency key.
Delivery rechecks observation and tenant identity, and distinct requests for
one failed source are single-flight through the terminal append. Request-bound
base execution may retry the predecessor's old causal failure once while its
own exact durable result remains replay-safe even after a later sibling attempt
fails. Fresh-process derived IDs are ordered
after the command rather than copied from the retained source timestamp.

The guarded updater contains one explicit, expiring baseline exception for the
known production commit, original 62,623-obligation floor, exact four source
IDs/reasons, and exact current full accounting at a fresh stopped-log
watermark. Only newer fully settled obligations may advance the snapshot;
regression, pending, missing, another failure, or stale evidence fails closed.
With the dedicated approval flag, the updater validates the baseline twice
while services are live, stops the runtime, proves the still-running Gateway is
live, then stops the Gateway. The stopped audit requires the final live event
watermark to equal the encrypted-log watermark and every durable pipeline and
projection row to remain non-degraded/non-poisoned. Only then does it record the
content-free exact counts/watermark/health receipt and append exact recovery
requests before candidate selection. A request
identity binds both the exact failed-assessment set and the stopped consumer
checkpoint, so restart reuses it while a v19 rollback that advances beyond it
causes the next guarded audit to append safely after that new position. Rollback
restores release/plugin/launch selection but retains those immutable events.
An exact command refusal is terminal and contributes a separate content-free
identity token, so a later guarded attempt appends a new request instead of
silently reusing the refusal.
Generation 19 ignores the new request type; a later reviewed generation-20
start safely delivers or reuses it. No checkpoint edit, database rewrite, event deletion,
credential migration, provider enrollment, permission change, external write,
or second runtime is introduced.

The first authorized activation stopped before backup when normal ingestion
advanced the original equality snapshot and Google Tasks retained a stale
`backoff` health state after a recovered connection reset. The follow-up
**HARDENS** the boundary rather than deleting or freezing new facts: Google
Tasks restores current running state on a successful poll, degraded readiness
is not relabelled as observation-only, and the updater reads readiness during
every settlement wait, rejecting hidden structural degradation or missing
readiness after runtime liveness.

PR #336 reviewed head `fd13157648ae3ad02b4190a95feb817ad99fee46`
deployed as merge `d181fad24ef488a71ce5568cc40fb81309cb3817` after a
second fail-closed preflight exposed an unrelated latched Google Calendar
transient. A separately authorized single predecessor restart restored that
adapter without configuration or provider writes; a fresh exact-head G-001
transaction then attested the merged controller, verified an encrypted backup,
bound final-live and stopped-log evidence at 62,673/62,673, proved eight
pipelines and fifteen projections healthy, and appended four exact recovery
requests. Four linked v4 replacements are now current while the older failures
remain immutable. Final live accounting is 62,674/62,674 with zero
pending/missing/failed; full Doctor, authenticated Triage isolation, rollback
compatibility, observation mode, and zero provider-called terminal effects all
pass. This is **PRESERVE** for append-only/provider/privacy authority and
**HARDEN COMPLETE LIVE** for the CO-017 recovery and release boundary.

CO-016 update (2026-08-11): Triage queue continuation is **HARDENED IN THE
REVIEWED CANDIDATE** with browser-only focus consumption, lens-relative banner
cleanup, a post-write refresh-failure safety latch, generation-invalidated
pagination with full decision-interval exclusion, action-specific settlement,
all-surface capture serialization, and presentation copy. No source event,
event schema, adapter, pipeline, provider object,
credential, runtime, or dependency migrates. Existing
`eligibility_rule_replaced` supersession events and feedback receipts remain
immutable; the member surface now labels their different authority correctly
without treating a shared reason code as proof of a migration.
Rollback restores the prior navigation/copy only and cannot remove decisions
or feedback. The latch is ephemeral browser state and does not migrate or
rewrite any durable record. The pagination generation is likewise ephemeral;
it discards only transport results made obsolete by a later member action.
The Triage-operation latch and causality token are ephemeral. Dirty confirmation
acquires them before FormData enrichment and retains them through decision and
authoritative refresh; save, Add-for-review intent, learning-policy, and
decision work is serialized, replacement renders and retained shared-shell
intent controls inherit the disabled state, and older callbacks cannot clear
feedback or a recorded-write refresh-failure latch. Invalidated pagination is
normalized before its transient state can be mistaken for semantic disabled
state. The additive enrichment response now returns deterministic computed
review state; changed readiness blocks confirmation, and a following decision
rejection refreshes the exact saved current revision independently of the
ordinary cursor. Ambiguous decision responses reconcile immutable exact-candidate
state and fail closed if terminal truth cannot be proven. Native dialog cancel
cannot discard an in-flight Add-for-review form on any surface; one stable
client request ID and a server-side per-intent lock make an ambiguous retry
idempotent. A generic definite 4xx is not retried; explicit server provenance
that intent and features are already recorded permits the one same-ID retry.
Existing feature events are rehydrated without a second language
interpretation, and every missing child is governance-preflighted before one
atomic proposal-family append. Unresolved ambiguity retains an in-dialog action
to the exact saved Needs interpretation item when known, or All candidates for
inspection before resubmission. Evidence-only and attach-existing candidates cannot be
approved through either API route, while exact member confirmation/dismissal
event/request receipts distinguish the requested action from other immutable
transitions. Projected acceptance of the exact confirmation is required before
materialization, so concurrent canonical truth wins. Triage projection v8 replays immutable
`member.provider_action.confirmed` facts and additively links the pending-action
receipt to the originating approved candidate. The projection runner reads the
stored version before stamping candidate metadata. Triage explicitly opts into
retained event-zero replay before live delivery, and v8 is stamped only after
that replay succeeds. Exact confirmation receipts are backfilled even when a
later historical outcome already advanced the row. An interruption therefore
retains v7 and retries on the next start; no other projection receives implicit replay behavior. Legacy rows
that predate retained events remain intact. This is a rebuildable read-model
upgrade only: no durable event, domain approval, provider command, outcome, or
provider state is migrated or rewritten. Non-continuation banners carry their
own presentation kind.

CO-013 update (2026-08-10): Gmail source acquisition is **HARDENED IN THE
REVIEWED CANDIDATE**. Existing incremental cursors, dedupe receipts, immutable
message facts, and member decisions are preserved. Adapter v1.1.1 snapshots the
current provider cursor before a bounded acquisition. Optional private
historical seeds run before deterministic exact-participant/date expansion and
recent-history fill, all inside one global cap. The additive metadata-only
completion cursor is keyed by a policy digest containing hashed—not plaintext—
seed queries. Existing installations acquire once because they have no such
marker; a reviewed policy change creates a new bounded obligation and restarts
reuse its marker. Rollback may ignore the marker and new source observations,
but must not delete either. No schema, credential, canonical object, provider
object, or runtime version migrates. The v13 operational replay remains
semantically unchanged at the reviewed eight-way context-safe width while the
guarded settlement ceiling moves from 30 minutes to one hour. A new non-source
capability degradation during that wait now triggers immediate full Doctor;
an individual Gmail record that disappears between provider discovery and get
is terminally quarantined while the successfully advanced adapter remains
healthy, rather than being mislabeled as provider backoff. This HARDENS health
truth and changes no source fact or provider object.
ordered-prefix checkpoints, terminal equality, and rollback remain. The final
consumer first advanced to v14/generation 14 because the rejected sixteen-way
live attempt durably occupied generation-13 identities. Live generation 14
then proved that read-before-append proposal deduplication could still race at
the reviewed width. Generation 15 proved the atomic identity repair by crossing
that exact collision, then exposed synchronized transient retries against the
pinned Gateway. Generation 15 later exposed a distinct same-assessment alias
collision: two non-equivalent candidates declared the same typed candidate
identity. Generation 17 fixed that collision but proved its serialized retry
still reused the base durable invocation identity, so the wrapper correctly
replayed the recorded failure without making a second call. Generation 18
proved the separate identity recovered one retained failure, then one later
OpenClaw stream reached its real terminal timeout. The final
v19/generation-19 consumer is **HARDENED AND LIVE VERIFIED** so
deliberately shared proposal/supersession identities accept only the first
equivalent event at the atomic event-log boundary; non-equivalent reuse still
fails. Ordinary width-eight delivery remains parallel, while only transient
pinned-seam recovery serializes locally and uses at most two separate
deterministic durable invocation identities. A success is reusable; exhausting
`:recovery-1` and `:recovery-2` remains terminal and cannot repeat without
another reviewed change. Fixed code also collapses repeated
`(target_kind, candidate_key)` identities within one recorded interpretation to
the first occurrence before the assessment/proposal boundary, without
confidence ranking. Persistent failure remains terminal. No retained fact,
terminal event, or member decision is deleted or rewritten.

CO-012 update (2026-08-10): contextual message reasoning is **HARDENED IN THE
REVIEWED CANDIDATE**. Existing message facts and candidate decisions are never
rewritten. `triage.item.proposed@v3` adds a private bounded interpretation;
Triage projection v6 adds nullable `interpretation_json` and replays old
events as `NULL`. Message pipeline v12 and pinned skill v6 request reviewed
replay: older thread sources receive terminal deferred assessments, the latest
source emits the conversation candidate set, and only untouched older
proposals may receive additive `eligibility_rule_replaced` supersession.
Enriched/confirmed/materialized/dismissed decisions win permanently. Rollback
may ignore v3 and the nullable column; reactivation replays immutable facts.
No credential, provider fact, canonical object, or provider object migrates.

CO-011 update (2026-08-09): candidate identification is **HARDENED IN THE
REVIEWED CANDIDATE** with an additive `candidate_policies` map in the
secret-free Settings journal. Schema v1 is accepted and migrates in memory;
the next Settings mutation persists schema v2 atomically. Unknown/corrupt
policy rows fail closed. `settings.candidate_policy_changed@v1` is additive and
private. Pipeline and skill version changes request reviewed replay where their
manifests already declare it, but no source fact, prior Triage proposal,
canonical object, provider object, projection row, or credential is rewritten.
Policy applies prospectively; historical reassessment stays explicit. Rollback
may ignore the additive event/map and needs no destructive data migration.

CO-010 update (2026-08-09): whole-life source and lifecycle coverage is
**HARDENED IN THE REVIEWED CANDIDATE**. Existing immutable source and domain
events are preserved. Message and Notes pipeline version changes request a
full durable replay, so retained facts are reassessed without becoming new
source observations. Tasks, Commitments, Recurrences, Routines, Places,
Assets, Accounts, and Finance Goals gain additive event schemas and additive
projection columns/tables only. Google Tasks persists content fingerprints
beside its durable cursor so an unchanged restart emits nothing. Finance
workbook goal status changes are modernized into typed pause/complete/reopen
events; Plaid facts and workbook planning authority remain separate. Older
code safely ignores the new event types, but rollback after new lifecycle
actions will not project those states; the immutable prior release therefore
remains available and forward reactivation must replay from event zero.

CO-009 update (2026-08-09): Reminders composition is **MODERNIZED** from a
Lists-only cockpit to a typed read-time union of scope-filtered Tasks and
provider items. No event or projection is merged. Existing Task and
Lists databases remain authoritative for their own record kinds; the browser
routes local actions to `task.*` and provider actions to the existing governed
external-write seam. `task.created@v1` gains optional property fields already
represented by the Tasks projection. Tasks projection v3 adds nullable
`available_from` so a deadline and defer/snooze activation are no longer the
same field; its migration is additive and old defer events fall back to their
historical due-date meaning at read time. Old events remain valid and replay
with their prior defaults. Triage candidate enrichment is **HARDENED** behind the
published `triage-enrichment-v3` allowlist. Rollback ignores no new event kind
and needs no destructive data conversion; older code safely ignores the new
optional payload/property while the additive projection column remains.
PRs #302 and #303 are **DEPLOYED**. Live exact-receipt navigation and a
controlled runtime restart verify the migrated Reminders composition and
restart-stable Calendar-source/outcome behavior without a runtime migration;
observation remains active and rollback remains available.

CO-008 upgrade-controller repair (2026-08-08): the ordinary updater's use of
the active release controller is **SUPERSEDED**. Every reviewed candidate now
self-hosts its upgrade policy in a commit-versioned controller. Same-baseline
controllers run on the active Python/Node boundary; runtime migrations retain
their explicit approval and side-by-side candidate runtime. Clean Git state,
unchanged HEAD, loaded-module path, and commit receipt are independently
attested before candidate policy can prepare or switch a release. Backup,
compatibility, full Doctor, synthetic proof, and automatic rollback are
unchanged. This repairs control-plane policy selection; it adds no event,
projection, authority, provider permission, or truth store.
PR #300 merged as `dc99208` and this path is **DEPLOYED**. The live receipt
attested the controller, retained rollback, and passed terminal source equality,
full Doctor, and authenticated synthetic execution in observation mode.

CO-008 source-throughput repair (2026-08-08): `noise_filtering` v9 is
**HARDENED** so privacy/member routing precedes NLP and its reviewed,
idempotent historical replay uses eight-way bounded delivery. The event bus
still provides at-least-once semantics and persists only the contiguous
successful prefix; it does not acknowledge around a failure. The
`classify_message_nature` 4.0.4 timeout changes from eight to 30 seconds to
match the pinned OpenClaw completion envelope without changing model routing or
authority. The 39 legacy household-shared Gmail facts remain immutable and are
explicitly quarantined; 316 private-member facts remain eligible for v9
assessment. Readiness-probe tolerance is **HARDENED** only while cheap runtime
liveness is true. No projection, truth store, provider permission, or
observation-mode policy changes.

CO-008 deployment repair (2026-08-08): runtime source accounting is
**HARDENED** from per-readiness synchronous invalidation to a mandatory initial
audit plus a 30-second, single-flight background refresh. This changes no event
schema or projection and adds no truth store; the cache is disposable runtime
health state. Failure-retry identity is **HARDENED** so the stable private
message fingerprint continues to reuse success, while an explicit reviewed
flag may bypass only an older causal failure once. The new failure terminal is
fingerprint-qualified and therefore idempotent. Bootstrap Doctor adds a named
fresh/terminal source check; it does not inspect content. The updater's bounded
wait widens only for named replay/health transients and preserves immediate
rejection of structural failures and automatic rollback.
After live evidence showed that two minutes cannot complete the v7-to-v8
historical cursor reset, post-switch settlement is **HARDENED** to a 30-minute
content-free readiness poll followed by one full Doctor. Candidate cursor state
is not migrated into the rollback release; immutable output/audit events remain
in the log, while the operational consumer cursor continues to follow the
active version.

CO-008 update (2026-08-08): source ingestion is **HARDENED IN THE REVIEWED
CANDIDATE** by additive manifest `source_assurance` rules,
`triage.intent.features_extracted@v2`, and `triage.item.superseded@v2`.
`noise_filtering` v8 and Notes indexing v3 explicitly opt into full replay.
Replay preserves every immutable source and member decision, appends a terminal
assessment for each candidate-bearing source, reuses a prior successful exact
fingerprint while retrying an older terminal failure once, and retires only untouched proposals whose sole
semantic admission was `explicit_action_verb_v1`. Runtime accounting adds no
projection and persists no second truth store. Rollback ignores additive event
versions but cannot remove their audit history. Adapter scaffolds and install
validation now fail closed when a future source lacks a disposition.

Explicit reactive replay contract (2026-08-08): the durable bus is **HARDENED**
with an opt-in manifest declaration for full replay on a reviewed consumer
version change. Healthy consumers otherwise retain their checkpoint; poisoned
consumers retain the existing last-good recovery. `noise_filtering` v7 opts in,
uses a privacy-scoped exact-message fingerprint to reuse one recorded language
result across duplicate source observations, and appends only kind/key pairs
absent from baseline/v4/v5/v6 proposal history.

Gateway-first startup repair (2026-08-08): central service ordering is
**HARDENED** after the guarded Phase-B candidate proved that starting the Python
runtime first lets reactive language replay race the unavailable Gateway. The
exact pinned OpenClaw health endpoint must be live before the runtime starts.
`noise_filtering` v6 and `classify_message_nature` 4.0.2 form a new append-only
replay boundary; they retain failed-start audit facts and deduplicate proposals
across baseline/v4/v5 history.

OpenClaw plugin-health reconciliation (2026-08-09): Gateway-first startup is
**PRESERVED/HARDENED**. The native plugin's formerly one-shot authenticated
health report is **MODERNIZED** into a nonblocking bounded-backoff service
reconciliation. Exact version/commit refusal and unavailable state remain
fail-closed; success ends retries and shutdown cancels them. This does not
change event schemas, consumer versions, source facts, projections, runtime
baselines, provider authority, or rollback compatibility.

Immutable plugin-selection reconciliation (2026-08-09): native-plugin
registration is **HARDENED** from plugin-name discovery to exact immutable
release identity. Upgrade and rollback now link the corresponding reviewed
plugin while services are stopped and validate its install/runtime source.
Stale same-version paths are **SUPERSEDED** as acceptable health evidence. No
event or consumer migration is required; prior immutable releases remain
retained for rollback.

Pinned language execution repair (2026-08-08): OpenClaw `2026.7.2` commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61` **SUPERSEDES** only the declared
defective same-version snapshot. The updater installs and atomically selects
the exact reviewed source, with exact-source rollback. Doctor full proof now
executes a bounded synthetic JSON call, not merely registration inspection.
Canonical member-isolated Gateway sessions and version-qualified skill terminal
events are **HARDENED**. `noise_filtering` v5 first replayed immutable message facts,
versions changed semantic facts, and appends only proposal kind/key pairs absent
across the baseline and v4 extraction generations.

Live Triage NLP runtime update (2026-08-08): the OpenClaw skill seam is
**HARDENED IN THE FOLLOW-UP CANDIDATE**. The Keychain-resolved Gateway token is
an explicit secret-safe skill-runtime dependency. The pinned bundled optional
`llm-task` plugin and additive tool-policy entry are reconciled by bootstrap and
upgrade through OpenClaw's validated config writer; Doctor now proves runtime
registration. `noise_filtering` v4 is a replay boundary that preserves v3
fallback facts and proposals while appending only newly grounded NLP kinds
under a new idempotency generation.

CO-007 update (2026-08-08): trustworthy capture and Triage are **MODERNIZED
COMPLETE IN THE PHASE-A CANDIDATE**. New exact-text intent and feature events
are additive. `triage.item.proposed@v1` and legacy external-write events remain
readable; new candidate lineage, confirmation chronology, independent approval,
and terminal audit use versioned schemas. Triage projection v5 rebuilds from the
immutable log, adding pending interpretation and approval/outcome linkage.
For deployed Triage v2 databases, the `parent_intent_id` index is created only
by the projection migration hook after the v5 table rebuild adds that column;
`schema.sql` must never reference an additive column before the hook runs. A
production-composition fixture preserves a legacy candidate while proving the
new lineage/approval columns and dependent index.
Gmail adopts the shared durable cursor/lease store with per-message dedupe and
explicit quarantine before page-watermark advancement. Rollback cannot delete
new events and must ignore unknown additive types safely. No migration collapses
domain kinds, turns a projection into truth, advances a provider cursor before
durability, or reclassifies a historical record through a model.

Runtime-Keychain update (2026-08-07): ADR-0006 **PRESERVES** every original
interpreter-bound item and **MODERNIZES** an authorized Python-boundary cutover
with candidate-bound copies plus an atomic, non-secret logical-reference alias
map. Values remain in memory and an anonymous pipe; no password, value file,
provider SDK, second secret store, or allow-all ACL is introduced. The older
release ignores the map and retains exact rollback access. Copy, receipt, alias,
and candidate-probe failures remain pre-mutation failures. The retry path reads
the active runtime tag from either the current exact preferred-version receipt
or the older reviewed `python@major.minor` formula and rejects any looser shape.
Because Homebrew Python is ad-hoc signed, candidate verification now proves an
independent fresh process through Apple's signed `security` client rather than
trusting the creating Python process's read-back. Aliased runtime reads/writes
and adapter credentials use that same anonymous-pipe seam; originals remain on
the old keyring path for rollback. Because Apple's prompt reader truncates long
input, runtime copies now use bounded Base64 fragments plus a versioned,
checksummed root manifest committed last. This **HARDENS** the existing
Keychain transport without creating another credential store.
The pre-cutover candidate probe enumerates only configured logical references;
the generated alias map participates in resolution but is not itself scanned as
credential configuration.
Central service and Doctor PATH are built from the candidate receipt and put
the reviewed Node formula ahead of global Homebrew binaries, preventing an
installed Node 22 from masquerading as the Node 24 candidate runtime.

CO-006 update (2026-08-07): Finance core is **MODERNIZED LIVE; PLAID ACTIVATION PENDING**
under ADR-0007. The existing money projection now separates private provider
observations/accounts, household classifications/manual rows, editable
planning assumptions/budgets/scenarios/goals, and workbook publication state.
One deterministic Finance contract drives a six-view member cockpit and the
twelve-sheet `adminme-finance.xlsx`. Plaid is detailed, read-only, removal-aware,
and durable-cursor-backed. The workbook owns planning intent; Plaid owns
provider facts; the encrypted event log owns history; the projection owns the
current read model. No fifteenth projection, provider SDK outside a pack,
second forecast engine, or bank write seam is added. Live Plaid Link/consent
and provider evidence remain explicit post-merge work.

CO-005 update (2026-08-07): production composition is **HARDENED** with
reviewed fail-closed authority/governance policy, governed Settings changes,
provider-source freshness, conservative legacy reminder copy presentation,
and truthful unresolved-endpoint status. The generic external mutation route
is **DELETE COMPLETE**; provider writes exist only behind typed product
commands, and Notes remains Cat-D ingest/query-only. ADR-0006/D30 **MODERNIZE**
the candidate baseline to Python 3.14/Node 24 with side-by-side tools and an
explicit pre-switch migration flag. The healthy live 3.11/22 release is not
changed by candidate preparation; stable wrappers resolve its own version
manifest before choosing a Node formula. Finance, Scoreboard, and privileged-read
audit depth are recorded as required incomplete work, not optional-provider
deferrals. See `CHANGE-ORDER-005-CONSTITUTIONAL-AUDIT.md`.

Settings update (2026-08-07): CO-004 **MODERNIZES COMPLETE IN CODE** the
generic capability/workbook/chat cards into one deterministic member control
plane. The member shell now names the authenticated identity and Tailscale
boundary. Connection setup, live runtime, permission, and safety state are
separate. A secret-free revisioned journal holds bounded preferences, pause
state, and bridge requests; typed events audit accepted changes. Profile
activation uses the existing validated extension lifecycle and adapter
pause/resume reconciles the existing supervisor. CO-005 additionally routes
accepted Settings mutations through production GuardedWrite policy before
state changes. No secret editor, second
runtime, provider SDK, OpenClaw operator console, model router, or fifteenth
projection was added.

People semantics update (2026-08-06): CO-003 **MODERNIZES LIVE IN OBSERVATION**
the historical CRM projection and generic member cards into deterministic
relationship memory. Parties v2 separates unresolved Endpoints, exact
provider Contact Records, qualified Parties, scoped Identifier Links,
Relationships, member profiles, facts, grants, and authorized FTS. Identity
resolution retains ambiguous evidence and fails closed; only curated contacts,
enrollment, explicit entry, or human-confirmed Triage candidates qualify a
Party. Reviewed enrollment now crosses the same event boundary through
idempotent Party/Membership/Relationship receipts, including non-member
contacts without portal authority. Four contact-source packs expose truthful capability/revision contracts;
live source enrollment remains per member and does not block the People
function or AC-30 fixture proof.

Today semantics update (2026-08-06): Today is **MODERNIZED COMPLETE IN CODE**
from a raw What Now plus “Open lists” surface into one deterministic Daily
Brief shared by the member portal and pinned OpenClaw dashboard. It composes
authorized projections at read time, in the configured household timezone,
and adds explicit Day Brief/Carousel/What Now controls. What Now uses a fixed
in-progress/next-30-minute calendar guard before the existing work ranker. Typed interaction
contracts preserve task, commitment, list-item, recurrence, ordered Routine,
calendar,
message, and clarifying-question boundaries. `Later` is view-only; actual
completion/outcome operations remain idempotent event commands. This adds no
second summary store and no model decision path.

D27 closes the ordered-Routine gap inside the existing `recurrences`
projection. Routine definitions carry their own schedule/time window and
ordered steps; dated step complete/skip outcomes advance only the current
Routine occurrence and never imply a linked-Task completion. Projection count
remains fourteen.

Personal Google OAuth update (2026-08-06): the Gmail, Google Calendar, and
Google Tasks activation rows are **KEEP/HARDEN COMPLETE IN CODE** for personal
accounts. Existing `op://` service-account deployments remain supported;
`keychain://` installed-user credentials are now resolved without raw secrets
in config, arguments, or environment variables. Gmail accepts OAuth while
remaining read-only, and Calendar/Tasks retain governed observation seams.

Personal Google read-only amendment (2026-08-28): the installed-user helper
and account configuration are **HARDEN COMPLETE IN CODE** for one member-scoped
grant containing exactly Gmail read-only, Calendar read-only, and Tasks
read-only. Broader or incomplete returned grants fail before Keychain storage.
The helper also resolves the consented Gmail profile and fails if it is not the
locally reviewed account; new documents bind that account and a one-way member
digest so the three adapters reject later owner reassignment.
Calendar and Tasks retain their bidirectional class ceilings for separately
authorized accounts, while a `read_only: true` instance uses only its provider
read scope and publishes no runtime write capability. Production composition
proves all three Settings connections remain observation-only and secret-free.
Real consent, configuration activation, deployment, and provider access remain
separate live gates.

Live activation hardening update (2026-08-05): the OpenClaw activation,
launchd, and backup rows are **HARDEN COMPLETE** for authenticated managed-RPC
probing, the macOS RunAtLoad/kickstart race, generated OpenClaw plugin-skill
links, agent-local `codex-home/{tmp,.tmp}` launcher cache, and
`codex-home/logs_*.sqlite{,-wal,-shm}` launcher telemetry. Durable sessions and
workspace state remain included. Arbitrary state links remain fail-closed and
excluded generated state is reconstructed by its owning runtime rather than
restored as trusted state.
SQLite's disposable `-wal`/`-shm` cleanup race is likewise excluded only after
service quiescence; the database and every other durable file remain required.
Central launchd startup now follows the Gateway-before-runtime dependency and
proves Gateway liveness before starting reactive replay. Because native plugin
registration occurs during that earlier Gateway start, its authenticated
AdministrateMe health report reconciles on bounded backoff until runtime
readiness rather than requiring a manual restart.

Mac bootstrap compatibility update (2026-08-05): the build/package and
bootstrap rows are **HARDEN COMPLETE** for Homebrew 6 metadata, external pinned
Poetry tooling, installer-owned first-run scaffolding, explicit reviewed-local
plugin trust, and pinned OpenClaw discovery registration. The production
virtualenv remains synchronized, but no longer contains the Poetry executable
performing that destructive sync.

Primary-surface update (2026-08-05, corrected by CO-005): central Google
Calendar and per-member Apple Calendar and Reminders are **KEEP/HARDEN COMPLETE
IN CODE**; Apple Notes is **KEEP/HARDEN COMPLETE FOR INGEST ONLY**.
The release preserves provider-native schemas at the transport boundary,
normalizes typed facts into the event log, reconciles external authority, and
routes supported Calendar/Reminder writes and terminal results through the
central/bridge command paths. Notes exposes no provider command path.
Production bootstrap now actually supplies `config/adapters.json` to
the runtime supervisor. Real OAuth, macOS privacy consent, and device
propagation remain live activation gates rather than simulated readiness.
Central and bridge emits now require durable acknowledgement before cursor
advancement; transport failure is retried instead of treated as success.

Triage semantics update (2026-08-06): the former Inbox is **MODERNIZED
COMPLETE** into a proposal-only human gate. A fourteenth, derived `triage`
projection aggregates current commitment/OpenClaw proposals and the generic
typed candidate lifecycle without changing the original thirteen domain-truth
capabilities. Open tasks remain on Today/Scoreboard. Provider observations
remain facts; extracted calendar/list/reminder candidates require explicit
member/account/container routing and governed confirmation before a write
request. Deprecated Inbox HTTP/OpenClaw aliases remain for pinned clients.

Comms semantics update (2026-08-06): Comms is **MODERNIZED COMPLETE FOR THE
PRODUCT CORE** as the ninth adult surface. It is a deterministic, scoped
read-time composition over interactions rather than a copied mailbox or an
OpenClaw transcript. Member, provider account, and personal/work workspace
remain independently authorized. Local snooze/quiet state is event sourced;
provider reply/read/archive is capability-negotiated and travels through the
external-write governance and outcome path. Gmail is implemented; uninstalled
Microsoft Graph, BlueBubbles, and Twilio edges remain visibly disabled rather
than simulated. All extracted domain candidates still route through Triage.

Comms live-migration correction (2026-08-06): the interactions projection is
**HARDEN COMPLETE** for an existing pre-Comms database. Indexes that depend on
the additive member/account columns are created by the projection migration
hook after those columns exist, never by the pre-migration bootstrap script.
The retained regression executes both phases against a legacy table and proves
all native-message, member/occurred, and account/thread indexes are present.

Five-surface loop update (2026-08-06): CO-001 **MODERNIZES COMPLETE IN CODE**
the read compositions and browser workflows for Today, Triage, Comms,
Reminders, and Calendar. Shared surface-link, attention, provider-action, and
freshness DTOs remain disposable and non-authorizing. Daily Brief v2,
Comms Focus v2, effective Reminders, structured Triage, and Calendar candidate
preview supersede raw/remixed source rows, unread-as-priority, browser prompts,
and context-as-next-event. Interaction v4 is additive and replayable. The
existing skill/pipeline counts remain ten: `classify_message_nature` v4 reports
bounded semantic features and exact-quote candidates through pinned OpenClaw,
while `noise_filtering` v3 owns fixed private-scope/field/source eligibility and
Triage proposal events. Consumer-version changes recover a poisoned subscriber
from its last good checkpoint; the same version remains fail-closed.

Gate 7 update (2026-08-04): the browser-experience deferral is closed. The
deleted Node scaffold stays deleted; FastAPI serves a small static member
portal and authorized JSON/streaming APIs. The exact-pinned OpenClaw plugin
gains the six remaining domain dashboard bindings and signed member-session
provenance, not a second Control UI. A schema-versioned JSON configuration
seam adds disposable saved views without adding a domain aggregate,
projection, scheduler, workflow, or copied record store.

| Gate 7 subsystem | Final class | Executable result |
|---|---|---|
| Principal experience | KEEP/HARDEN COMPLETE IN CO-002 CANDIDATE | Exact OpenClaw Control UI exposes Today, Triage, Comms, Reminders, Calendar, People, Notes, Finance, Scoreboard, Settings, health, governed review/lifecycle bindings, and the same deterministic Daily Brief/Comms/Reminders/Notes contracts as the portal; Knowledge remains a deprecated binding only. |
| Member/child experience | REWRITE COMPLETE | Python-served portal has adult ten-surface, child two-surface, and shared-screen one-surface server allowlists plus responsive/accessibility evidence; Today supplies a Day Brief, explicit Carousel, and deterministic What Now, Comms supplies member-isolated deterministic message focus, and Reminders supplies deterministic provider-backed activation lenses. |
| Member CoS chat | COMPATIBILITY ADAPTER COMPLETE | Minimal pinned protocol-v4 Gateway relay; OpenClaw owns transcript/memory/providers/routing/retry/accounting; signed per-member tool provenance is independently authorized. |
| Temporary quests | NEW DATA-ONLY CONFIG SEAM COMPLETE | Private by default, allowlisted projection queries/actions, reviewed audience/child safety, typed transition audit, atomic persistence, expiry/archive; no household-fact mutation. |
| Browser runtime | KEEP DELETED NODE / ADD STATIC PYTHON SURFACE | No Node app server, service worker, local transcript/cache, provider SDK, model picker, admin console, plugin manager, or terminal. |
| Live deployment | KEEP/HARDEN ACTIVE IN OBSERVATION | Central Tailscale/Gateway/provider reads are live; member bridges and optional provider grants remain explicit per-member activation work. The 3.14/24 cutover requires a new rehearsal. |

Gate 6 update (2026-08-03): the remaining retained domain code is converged on
the Gate 2–5 runtime. All six products, thirteen domain projections, and the
derived Triage projection are composed;
member knowledge and scoped vector search are distinct; workbooks, profiles,
personas, packs, bridge sources, durable cursors, notification policy,
capability registry, contextual what-now, and activity feed have executable
paths. Rows below retain baseline history; this Gate 6 delta is current truth.

| Gate 6 subsystem | Final class | Executable result |
|---|---|---|
| Event runtime and projections | KEEP/HARDEN COMPLETE | One EventSink/log/bus root; exactly fifteen registered projections (thirteen domain truth plus Triage and Context Fabric); replay, migration, scope, checkpoint, and encrypted reopen tests pass. |
| Domain APIs | KEEP/REWRITE COMPLETE | Core, Comms, Capture, Automation, Bridge, and System are real namespaces; activity/capability status derives from runtime wiring and authorization. |
| Notes/authorized recall | KEEP/HARDEN/MODERNIZE COMPLETE IN CO-002 CANDIDATE | Private provider-neutral `member_knowledge`; deterministic owner/grant-filtered FTS; scope-filtered revocable `vector_search`; privileged indexing/model processing forbidden; exact-evidence semantics create Triage proposals only. |
| Workbooks | KEEP/HARDEN LIVE FOR CO-006 FINANCE CORE | `adminme-ops.xlsx` retains its contract; Finance schema v2 adds validated assumptions, budgets, scenarios, goals, checks, protected provider fields, privacy-filtered rows, typed reverse events, and one shared deterministic calculation source. |
| Adapters/bridge | KEEP/HARDEN COMPLETE | Cat-A–E contracts, deterministic fixtures, durable cursor state, real Cat-D filesystem watchers, central ingest, and fail-closed mirror configuration. |
| Pipelines/OpenClaw | KEEP/CONVERGE COMPLETE | Six reactive packs stay in-process; five proactive programs are scheduled by OpenClaw; notification delivery belongs to OpenClaw and is confirmed only by a typed result. |
| Profiles/personas/packs | KEEP/REWRITE COMPLETE | Five profiles and four personas are data/versioned extensions with stage/test/activate/rollback; all six pack kinds remain. |
| Duplicate platform code | DELETE COMPLETE | Empty console package, prose daemons/helpers, local scheduler dependency/config, fake send receipts, and unused platform dependencies removed. |
| Optional providers and browser UX | DEFER TRUTHFULLY | Missing provider config is disabled/blocked; live providers and full browser household surfaces require later configured/live evidence. |

Operational audit update (2026-08-04): the encrypted ten-section bootstrap now
also installs the exact SHA-256-pinned OpenClaw GitHub source snapshot, owns and
transactionally reconciles all five standing-order jobs in an explicit IANA
timezone, and provides a short-lived member-bound bridge export/accept/finalize/
revoke path with per-member launchd and real central ingest proof. Live provider
grants and connector-pack activation remain post-merge evidence boundaries.

Classifications describe the Phase 2+ treatment, not Gate 1 edits. **KEEP** retains semantics/code with compatibility coverage; **HARDEN** retains the core but closes a proven seam; **REWRITE** replaces scaffolding or incompatible implementation behind tests; **DEFER** is allowed only for an optional provider; **DELETE** occurs only after its replacement passes.

| Subsystem | First-party paths | Evidence at baseline | Class | Migration requirement |
|---|---|---|---|---|
| Event envelope/log/migrations | `adminme/events/envelope.py`, `log.py`, `migrations/` | SQLCipher append/validation and tests exist | HARDEN | Put behind one append-and-publish port; preserve append-only/schema/encryption; add crash/migration/backup gates. |
| Event schema registry/upcasters | `adminme/events/registry.py`, `schemas/` | Broad typed schema suite and upcaster tests | KEEP | Inventory versions, prohibit destructive shape changes, add migration fixtures and lifecycle outcomes. |
| Durable in-process bus | `adminme/events/bus.py` | durable offsets/retry/degrade exist; checkpoint test fails in baseline | HARDEN | Fix ordering/race in Phase 2; poison isolation, restart, drain; event-log append must notify only after commit. |
| Central composition root | absent | no process instantiates log+bus+all runners/APIs | REWRITE | Create one lifecycle owner with truthful readiness and ordered shutdown. |
| Projection protocol/runner | `adminme/projections/base.py`, `runner.py` | replay/registration mechanisms; default tuple only `lists` | HARDEN | Exact fifteen registry (thirteen domain plus Triage and Context Fabric), shared bus ownership, per-capability replay/idempotency/status. |
| Parties projection | `adminme/projections/parties/` | v2 schema/handlers/queries, `adminme/people.py`, identity pipeline, contact packs, AC-30 | KEEP/HARDEN LIVE IN CO-003 OBSERVATION | Registered and additive/replayable; separates endpoints/provider records/qualified Parties; scopes identifiers, relationships, profiles, facts, grants, and FTS; ambiguous/hidden resolution fails closed. |
| Interactions projection | `adminme/projections/interactions/` | v4 account/workspace/body/provider-state plus additive semantic-feature/provenance columns, repeated-source lookup, scoped thread queries, command results, migration/replay tests | HARDEN COMPLETE | Preserve every message observation in `raw_event_ids`; attach schema-bound features only to the exact source interaction; fold repeated native observations only within tenant/member/provider/account; preserve privacy. Comms grouping and Focus remain deterministic read-time composition. |
| Artifacts projection | `adminme/projections/artifacts/` | schema/handlers/queries/tests | KEEP | Register; scope/provenance/correction. |
| Commitments projection | `adminme/projections/commitments/` | schema/handlers/queries/tests | KEEP | Register; full proposal-review-completion feedback. |
| Tasks projection | `adminme/projections/tasks/` | schema/handlers/queries/tests | KEEP | Register; keep task semantics and what-now links. |
| Recurrences projection | `adminme/projections/recurrences/` | schema/handlers/queries/tests | KEEP | Register; never conflate with cron. |
| Calendars projection | `adminme/projections/calendars/` | schema/handlers/queries/tests | KEEP | Register; privacy redaction/link semantics. |
| Places/assets/accounts projection | `adminme/projections/places_assets_accounts/` | schema/handlers/queries/tests | KEEP | Register; preserve distinct entity kinds within capability. |
| Money projection | `adminme/projections/money/` | v2 schema/handlers/queries, Finance cockpit, replay/privacy tests, AC-32 | KEEP/HARDEN/MODERNIZE LIVE FOR CO-006 CORE | Preserve privileged Plaid provenance while adding accounts, removals, classifications/manual corrections, assumptions, budgets, scenarios, goals, workbook state, and deterministic cash-flow/net-worth/scenario composition. |
| Vector search projection | `adminme/projections/vector_search/` | schema/handlers/queries and privileged-exclusion tests | HARDEN | Register; scope at query/index; prove no privileged/cross-owner embeddings. |
| XLSX projection/reverse daemon | `adminme/projections/xlsx_workbooks/`, `adminme/daemons/xlsx_sync/`, `xlsx_forward.py`, `xlsx_reverse.py` | Finance schema v2, structural/type/semantic validation, planning reverse events, owner-only workbook/sidecar files, protected facts, sidecar/lock/conflict/rebuild tests | HARDEN LIVE FOR CO-006 FINANCE CORE | Preserve macOS lock semantics; reject malformed planning saves before domain mutation; filter every sheet by workbook audience; regenerate derived sheets from the money projection. |
| Member knowledge projection | absent; bridge schemas refer to future 11d | required capability missing | REWRITE | Implement owner-scoped schema/handlers/queries and vector handoff from typed Cat-D events. |
| Lists projection | `adminme/projections/lists/` | implemented and default-registered | KEEP | Compose with other 12; external-authority reconciliation and task promotion links. |
| Triage projection | `adminme/projections/triage/` | new D26 deterministic proposal read model | MODERNIZE COMPLETE | Aggregate proposal families, enforce scope, rebuild from the log, never become domain source-of-truth. |
| Adapter protocol/legacy base | `adminme/adapters/base.py` | protocol and supervisor tests | HARDEN | One async emission port; durable status/watermarks/circuit; remove sync/async ambiguity. |
| Five adapter ABCs | `adminme/lib/adapters/` | Cat-A–E contracts and validation tests | KEEP | Preserve categories/verbs/floors/deployment; unify final outbound guard. |
| Adapter pack registry/validator/CLI | `adminme/lib/pack_registry/`, `adminme/cli/commands/adapters*` | useful validation; Windows-invalid `<NAME>` tracked templates | HARDEN | Portable template names/placeholders, all six pack lifecycle stages, atomic rollback/install events. |
| Central adapter supervisor | `adminme/daemons/adapter_supervisor.py` | restart/status primitive; error emit uses ambiguous call shape; not composed | HARDEN | Construct real packs/contexts, retain tasks, durable state, orderly drain, composition status. |
| Bridge package/runtime | `bridge/adminme_bridge/`, `adminme/products/bridge/`, `bootstrap/bridge_enrollment.py` | Central ingest and owner-scoped knowledge are retained; reviewed bootstrap adds encrypted export, identity/release/expiry validation, per-member launchd, real enrollment proof, finalization/revocation, and durable idempotent commands for Apple Calendar, Reminders, and Notes | HARDEN COMPLETE FOR ENROLLMENT AND PRIMARY APP COMMANDS | Live opt-in pack configuration, Apple grants, and iCloud propagation remain separate evidence. |
| Gmail Cat-A | `packs/adapters/messaging/gmail/` | account-bound v3 inbound, v2 outbound, direction-preserving incremental history (`SENT` never becomes inbound), private-seed-first bounded/durable `messages.list` acquisition with exact-participant/date expansion and recent fill, content-free policy-digest receipt, off-event-loop synchronous provider acquisition, provider labels, reply headers, read/archive mutations, dynamic OAuth scopes and tests | HARDEN DEPLOYED; CONTEXT-VOCABULARY REPAIR IN REVIEW | Live acquisition and liveness pass; final source settlement, Doctor, and contextual Triage proof remain. Live account remains read-only until fresh modify/send OAuth consent; Pub/Sub remains an optional latency follow-up, not a completeness substitute. |
| Cat-B calendar/reminder/task packs | `packs/adapters/{calendaring,reminders,tasks}/` | Google/Apple Calendar and Apple Reminders now cover supported native fields, calendars/lists, recurrences, alarms/geofences, completions, change feedback, durable cursors, restart-stable Google calendar-source observations, future-only elapsed transitions, and central/bridge command paths | HARDEN COMPLETE IN CODE | Retain external authority, observation/governance, idempotent writes, and require live OAuth/EventKit propagation evidence. Historical provider events remain facts; initial discovery must not reinterpret already-past history as a new outcome. |
| Plaid Cat-C | `packs/adapters/financial/plaid/` | detailed private account/transaction/removal events, terminal summary, sign normalization, secret references, durable per-member/item cursor, restart/supervisor tests | KEEP/HARDEN CODE LIVE; PROVIDER ACTIVATION PENDING | Compose read-only; advance cursor only after accepted details and receipt; retain provider identity/revision/removal; require separate live Link/consent evidence. |
| Home Assistant Cat-C/E | `packs/adapters/automation/home-assistant/` | multi-capability implementation/tests | HARDEN | Compose; final guard, observation and verified action outcome. |
| Pipeline protocol/loader/runner | `adminme/pipelines/` | reactive subscriptions/tests; stop delegates bus ownership | HARDEN | One bus lifecycle owner, replay/idempotency and poison policy; capability registry. |
| Pipeline supervisor | `adminme/daemons/pipeline_supervisor.py` | prose-only future stub | REWRITE | Delete stub after composition root owns runner lifecycle. |
| Ten pipeline packs | `packs/pipelines/` | typed packs and many unit/integration tests; `noise_filtering` v20 emits classified facts, bounded semantic features, exact-quote eligible private Triage proposals, same-interpretation typed-identity collapse, atomic first-equivalent-writer cross-source deduplication, and exact request-bound terminal-assessment recovery with at most two concurrent requests and three attempts each; `identity_resolution` v2 uses per-source observation identity | KEEP/HARDEN COMPLETE; CO-017 RECOVERY DEPLOYED AND LIVE-VERIFIED | Deterministic code owns eligibility, scope/sensitivity exclusion, exact-source grounding, candidate allowlist, idempotency, context-reason vocabulary, recovery-reason validation, and outcome feedback; confidence never gates or ranks a review candidate and no model owns a decision. |
| Ten skill packs | `packs/skills/` | schemas/prompts/tests present; `classify_message_nature` 6.0.1 returns classification, bounded semantic features, and exact-quote typed candidates defensively while admitting every fixed-code context reason | HARDEN COMPLETE; CO-013 VOCABULARY REPAIR IN REVIEW | Stay on pinned OpenClaw `llm-task`; report grounded language features only; never rank, route, authorize, schedule, send, complete, suppress, or mutate. |
| OpenClaw skill wrapper | `adminme/lib/skill_runner/` | exactly one pinned `llm-task` request uses a canonical member-isolated Gateway session, records actual returned provider/model provenance, and versions durable terminal identity by skill version | REWRITE/HARDEN COMPLETE | OpenClaw owns routing/fallback; schemas, sensitivity, durable audit, exact-quote proposal semantics, and replay identity remain AdministrateMe-owned. |
| Session/scope/privacy | `adminme/lib/session.py`, `scope.py` | strong unit tests for roles/view-as/redaction | HARDEN | Enforce at central middleware and every query; trusted proxy; privileged-access audit and member portal. |
| guardedWrite/rate limiter | `adminme/lib/governance.py` | strict three-layer order plus exact caller/action rate-budget precedence; reviewed xlsx bulk ingestion is isolated from conservative principal and agent defaults | HARDEN | Preserve independent approvals, content/privacy/persona and observation gates; audit all terminal outcomes; never widen an external seam through a local-daemon exception. |
| Observation | `adminme/lib/observation.py` | on-by-default and suppression code/tests | HARDEN | Persist start/trust window/review prerequisites; complete payload; Cat-B explicit rule; final seam only. |
| Product Core / Today | `adminme/products/core/main.py`, `adminme/today.py`, member Today surface | Daily Brief v2 composes timed schedule, day context, effective reminders, typed executable work, Triage decisions, and the same explained Comms attention predicate with exact source links | REWRITE/MODERNIZE COMPLETE | Read composition only; preserve every item kind and source; contextual/all-day facts never become the next timed event; no model ranker. |
| Product Comms | `adminme/products/comms/main.py`, `adminme/comms.py`, member Comms surface | `deterministic-comms-cockpit-v2`, Focus v2, safe entity normalization, exact account capability, attention explanations, local focus commands, Triage/source links, and governed provider command spine | REWRITE/MODERNIZE COMPLETE FOR PRODUCT CORE | Unread is visual only; Triage remains the only proposal queue; Comms is not generic chat; missing Outlook/BlueBubbles/Twilio provider packs stay disabled and explicit. |
| Product Reminders | `adminme/reminders.py`, Lists v3 projection, member Reminders/Today surfaces and command/cue APIs | Shared effective-reminder composer, deterministic activation lenses, exact receipt-to-item/lens routing, exact capabilities, transparent read-only possible-copy groups, structured dialogs, typed context facts/cues, and Google Tasks full supported CRUD/move/complete mirror | MODERNIZE COMPLETE IN CODE | Provider remains authoritative; every copy remains independently inspectable; list items remain distinct from Tasks/Commitments/calendar events; deep links change presentation only; live propagation remains acceptance evidence. |
| Product Calendar | `adminme/calendar.py`, Calendars v3 projection, member Calendar/onboarding/Triage/Today surfaces, Google Calendar discovery | Existing deterministic household reasoning plus Triage candidate preview through the same effective-event/conflict/buffer/guardrail code and reviewed write destinations; exact deep links and URL selection remain presentation-only | MODERNIZE COMPLETE IN CODE; LIVE READ/POLICY PROOF COMPLETE | SUPERSEDE naïve every-event-is-busy, single-primary-calendar, context-as-next-event, and agenda-only UI; preserve provider authority, privacy, typed event separation, governance, and post-observation write evidence. |
| Product People | `adminme/people.py`, Parties v2, reviewed household-enrollment receipt, member People surface, identity pipeline, four contact packs | `deterministic-people-cockpit-v1`, idempotent enrolled Party/Membership/Relationship events, qualified directory, authorized audience-specific search, source/lens rail, master/detail context, explicit cues/settings, reversible lifecycle, Triage promotion, and pinned OpenClaw read binding | REWRITE/MODERNIZE LIVE IN CO-003 OBSERVATION | SUPERSEDE sender-as-person, bootstrap-file projection reads, generic CRM cards, tenant-wide identifier assumptions, implicit provider sharing, model closeness/ranking, and guilt cues; live contact enrollment/write remains per member and observation-gated. |
| Product Capture compatibility | `adminme/products/capture/main.py` | stale quick-capture docstring-only stub | REWRITE/COMPATIBILITY | Notes owns the member recall experience; Capture retains legacy domain read/search APIs where required; quick capture/central voice claims stay deleted and Knowledge routes are deprecated aliases. |
| Product Automation / Finance | `adminme/products/automation/main.py`, `adminme/projections/money/queries.py`, member Finance surface | `deterministic-finance-cockpit-v1` provides scoped cash-flow, budgets, scenarios, goals, net worth, data health, and fixed-priority review from one projection | REWRITE/MODERNIZE LIVE FOR CO-006 CORE | Keep Finance read-only and deterministic; planning writes arrive through validated workbook events; future provider actions require an independent change order and full external-write contract. |
| Bridge product | `adminme/products/bridge/` | only implemented FastAPI app; separate `:3337` | HARDEN | Move router into central root; preserve endpoint compatibility/auth; no separate central process. |
| Console runtime | `console/server.js` | comment-only stub; npm test is placeholder echo | DELETE after replacement | Build real family frontend, then remove Node application server; Node remains build-only if used. |
| Console/browser application | `adminme/products/member/assets/` served by the Python runtime | Ten-surface role-authorized portal; CO-006 adds six URL-backed Finance lenses, calm cash/runway hierarchy, deterministic focus and data-health states, and responsive budget/scenario/account views | REWRITE/MODERNIZE LIVE THROUGH CO-006 | Preserve server authorization, text-only safe DOM construction, WCAG/reduced-motion/reflow gates, cache coherence, and isolated browser evidence; never restore a Node application runtime or native prompt workflow. |
| Reference HTML | `ADMINISTRATEME_CONSOLE_REFERENCE.html` | rich mock with stale flows and unsafe mock rendering; Gate 1 rendered evidence retained | KEEP as design evidence | Never ship as app; preserve valid vocabulary and retained captures, supersede retired flows. |
| OpenClaw plugin placeholders | `adminme/openclaw_plugins/` | Deleted at Gate 4 after import scan and native contract/process tests; history remains in Git | DELETE COMPLETE | Replaced by one exact-pinned native package at `openclaw/administrateme/`. |
| Standing-order prose/cron | `bootstrap/openclaw/programs/`, `cron.yaml`, `openclaw/administrateme/automations.json`, `bootstrap/openclaw_cron.py` | Five proactive jobs cross-checked, ownership-scoped, timezone-explicit, idempotently reconciled, post-verified, Doctor-visible, and rollback-tested; reward dispatch stays reactive | HARDEN COMPLETE | Retain disposable-Mac RPC proof and never add a Python scheduler. |
| Bootstrap sections | `bootstrap/wizard.py`, `bootstrap/state.py`, `bootstrap/sections/` | Gate 5 ten-section AES-GCM journal; per-section versions, input digests, events, resume/rollback and skip follow-ups | REWRITE COMPLETE FOR INSTALLER | Preserve the encrypted schema and add only versioned migrations. Live grants remain evidence, not state assumptions. |
| Pack install order | `bootstrap/pack_install_order.yaml` | partial, older checkpoint says 1/10 skills | REWRITE | Capability/dependency-driven six-kind plan, exact artifact hashes, atomic install/rollback. |
| Profiles/personas | `openclaw/administrateme/persona/` plus broader built-ins still absent | Gate 4 adds the active instance persona source and deterministic SOUL/workspace compiler; the constitutional five profiles/four persona packs remain later AC-17 work | REWRITE PARTIAL | Member additions remain per-member; persona stays per-instance; neither changes authority/model routing. |
| CLI | `adminme/cli/` | adapter commands and future-command claims | HARDEN | Runtime/Doctor/replay/projection/backup/pack/bootstrap commands that call real composition services. |
| Instance configuration | `adminme/lib/instance_config.py`, `config/channels.yaml` | path resolver and one config exist | HARDEN | Versioned schema, secret references, port/instance isolation, no hardcoded household/path. |
| Health/Doctor | `bootstrap/doctor.py` plus runtime status | Gate 5 exact pin/architecture/loopback/encryption/migration/projection/workbook/adapter/bridge/launchd/backup/observation checks, required reactive subscriber poison detection, and canonical proof | REWRITE/HARDEN COMPLETE FOR INSTALLER | Required pipeline absence/failure/poison is unhealthy; JSON export stays secret-scanned. |
| Backup/restore | `bootstrap/backup_restore.py` | Gate 5 quiesced authenticated archive, manifest hashes, safe extraction, replay-before-replace, automatic rollback | REWRITE COMPLETE FOR AC-15 PROCESS | Prove the same contract on the disposable live Mac and retain off-host target evidence. |
| Build/package system | root/bridge Poetry, native plugin npm, exact OpenClaw source/pnpm | Root and bridge locks plus plugin lock are retained; unpublished OpenClaw 2026.7.2 is fetched only by exact GitHub commit and archive SHA-256, built with its declared pnpm | HARDEN COMPLETE FOR BOOTSTRAP | A future distribution change requires compatibility review and the full live Mac gate. |
| CI | `.github/workflows/macos-bootstrap.yml` plus existing workflows | Gate 5 adds an untrusted-PR-safe credential-free macOS bootstrap/plugin/shell/lint/type/recovery job | REWRITE PARTIAL | Broader repository matrix remains; no private credential enters the workflow. |
| Tests | `tests/`, `bridge/tests/`, pack tests | 1,170 collected before errors; broad isolated coverage | KEEP/HARDEN | Preserve meaningful tests, fix portability/races, add process gates, forbid false placeholders. |
| Historical prompts/build logs | `prompts/`, `docs/build_log.md`, handoffs/memos | useful provenance but stale future instructions | KEEP as history | Exclude from governing instruction chain; link from current contracts only when relevant. |
| Vendored provider references | `docs/reference/` | large snapshots create lint noise and API staleness | DEFER refresh per optional integration | Exclude from runtime/lint or validate separately; never use as floating current contract. |

Gate 1 itself changed no production file. Gate 2 has now executed the runtime-spine subset of this map.

## CO-010 live source-assurance scale delta

| Subsystem | Before live correction | Reviewed correction | Classification |
|---|---|---|---|
| Event Log diagnostic reads | Source assurance used the full-envelope reader, decrypting and parsing provider payloads that its content-free counts did not use; a conditional payload column still forced SQLCipher table-row visits after PR #306. | Migration v4 adds a covering type/event/time index. `read_metadata_since` closes at an event-ID watermark, reads ordinary source facts from that covering index, and uses a disjoint table-payload branch only for named terminal result types. Ordinary provider content never enters the audit object. | MODERNIZE |
| Assurance freshness | Any running refresh was labeled stale; the first correction still age-rescanned a settled immutable revision every 30 seconds. | Refresh activity is independent. An exact current revision with zero pending obligations remains settled without a wall-clock rescan. Revision changes refresh immediately; pending obligations recheck every 30 seconds; refresh exception fails immediately and an unsettled proof fails stale after five minutes. | SUPERSEDE |
| Doctor settlement | Required healthy, exact terminal counts, zero pending/missing/failed, and a fresh proof. | Preserved and tightened: the explicit bounded-watermark proof must also match the current event/checkpoint revision; its age and refresh activity are separately visible. | PRESERVE/HARDEN |

## Simple GitHub delivery migration delta

| Delivery stage | Retired process | Current result | Classification |
|---|---|---|---|
| Change flow | G-001 and the Outcome Harness bound work to exact phrases, separate reviewer tasks, generated artifacts, staged labels, and repeated proof invalidation. | One branch from current `main`, one pull request, required GitHub CI, optional bounded advisory review, and ordinary owner merge language. Failures are fixed on the same branch. | SUPERSEDE |
| Merge | The implementation task had no ordinary completion path. | Protected GitHub merge commits are the only path to `main`; direct push, force-push, history rewrite, and protection bypass remain forbidden. | MODERNIZE |
| Deployment | Process evidence and live safety were coupled into one transaction. | Deployment is separately requested after merge and retains the immutable revision, observation, encrypted backup, rollback, Doctor/settlement, privacy/isolation, browser, and zero-provider-effect gates. | PRESERVE/HARDEN |
| Product coverage | Harness artifacts wrapped genuine navigation and browser assertions. | The useful checks remain as ordinary unit, production-composition, and Gate 7 browser tests. | PRESERVE |
## Gate 2 migration delta

| Subsystem | Gate 2 result | Remaining boundary |
|---|---|---|
| Event writes and durable delivery | `EventSink`/`EventWriter` is the sole typed producer contract; it commits a single event or atomic batch before bus notification. Durable checkpoints advance only after success; retry and poison metadata survive restart. | Full forced-kill and outbound ambiguity matrices remain AC-09/20. |
| SQLCipher lifecycle | Log open/migration, locking, wrong-key failure, rollback, close, and encrypted reopen are owned by the runtime. | Backup/restore and legacy migration fixtures remain AC-15/20. |
| Runtime and projections | One `RuntimeApplication` owns lifecycle and registers exactly parties, interactions, artifacts, commitments, tasks, recurrences, calendars, places_assets_accounts, money, vector_search, xlsx_workbooks, member_knowledge, and lists. | Per-capability semantic/replay equivalence remains AC-07. |
| Producers | Pipeline packs, adapter packs/supervisor, governance, observation, skills, bridge ingestion, and xlsx paths use the injected event sink; synchronous capability probes are removed. | End-to-end product semantics remain Phase 3+. |
| Product topology | Core, Comms, Capture, Automation, Bridge, and System are namespaces inside one FastAPI application with truthful liveness/readiness. | Full UX and authorization behavior remains AC-08/18/19. |
| Portability | Xlsx locking supports Windows and POSIX; scaffold templates use portable filenames; bridge task shutdown is bounded. | Clean macOS installation/rehearsal remains AC-01/21. |

## CO-010 exact plugin-selection live delta

| Subsystem | Before PR #309 | Deployed result | Classification |
|---|---|---|---|
| OpenClaw local plugin selection | Same-version discovery could continue loading a reviewed but stale immutable release after an otherwise successful upgrade. | The updater reconciles the bounded AdministrateMe registration while stopped; Doctor requires the registered path and loaded module to resolve in the selected release. Live release `e908ee5d93944da41a5d0dfbd3dd2f16fad5ba4a` satisfies both. | HARDEN |
| Startup health | The new retry implementation could exist only in the inactive candidate tree, leaving successful startup dependent on an operator health request. | A controlled Gateway-before-runtime restart used the active plugin's bounded automatic retry and reached healthy Doctor without a manual POST. | MODERNIZE |
| Source-to-Triage proof | Source retention and subscriber checkpoints previously allowed a false-empty member queue. | The final CO-013 proof accounts for 62,553/62,553 obligations with 225 candidate-bearing facts and zero pending, missing, or failed outcomes; authenticated Triage renders 67 Needs Details and 33 Ready current records under observation. | SUPERSEDE |

## CO-014 context and queue migration delta

| Subsystem | Before | Candidate result | Classification |
|---|---|---|---|
| Context | Bounded message context existed only inside extraction/proposal metadata; provider thread could become the de facto member matter. | Fifteenth rebuildable `context_fabric` projection separates provider conversation, deterministic 30-day/100-source matter, member-confirmed context, and non-authoritative semantic suggestion. | MODERNIZE |
| Candidate reachability | `pending_for_member` and the browser silently stopped at the newest 100 rows. | Candidate Inventory exposes exact totals and stable cursor pages; production acceptance reaches every row exactly once. | SUPERSEDE |
| Member workload | One source with many instructions occupied many peer rows in a flat master list. | One matter card nests independent child candidates; exact duplicates/existing-object matches remain visible evidence without becoming extra decisions. | MODERNIZE |
| Dismissal memory | Free-text dismissal ended the proposal but could not improve future review. | Typed feedback is immutable; repeated scoped outcomes create a member-visible rule suggestion; previewed approval is reversible and shadow-only. | MODERNIZE |
| Runtime registry | Thirteen domain projections plus Triage. | Thirteen domain projections plus Triage and Context Fabric: exact count fifteen in runner, Doctor, backup/restore, Settings, invariants, and release gates. | SUPERSEDE exact-fourteen count; PRESERVE topology |

## CO-015 decision comprehension and link migration delta

| Subsystem | Before | Deployed result | Classification |
|---|---|---|---|
| Today decisions | Candidate title, target-kind label, generic micro-step, and generic Review action required the member to reconstruct context. | `member-decision-context-v1` names the direct question, situation, reason, source, uncertainty, fixed consequences, next step, and member authority with progressive disclosure. | MODERNIZE |
| Today → Triage | Candidate id appeared in the URL, but the server loaded only its ordinary cursor and the browser could silently show another matter. | Candidate plus matter URL state drives an independently authorized focus lookup with pending/resolved/not-found state and exact DOM focus. | SUPERSEDE generic/fallback navigation |
| Resolved and stale links | Ordinary Recent limit and stale client focus could hide a decision or force a member back to Recent. | Requested resolved rows remain reachable outside the ordinary limit; deliberate lens changes consume stale focus and never reopen a decision. | HARDEN |
| Comms/Notes candidates | Comms linked a candidate count to Triage top; Notes carried only a candidate id. | Scoped candidate inventory publishes individual candidate/matter links; Notes carries candidate, matter, view, and evidence identity. | MODERNIZE |
| Scoreboard next steps | Canonical Tasks appeared without a path back to the managing surface. | Active Tasks carry an exact Reminders item link; terminal evidence remains distinct. | MODERNIZE |
| Surface contract | Typed paths covered only six stable routes. | `surface-link-v1` covers all ten adult routes and remains non-authorizing. | HARDEN |
| Runtime/domain topology | Fifteen projections and existing sources, models, write gates. | Unchanged: no projection, source store, graph, provider SDK, model router, write path, or external effect added. | PRESERVE |
| Release activation | CO-015 behavior existed only in isolated candidate evidence. | PR #331 merge `2fede3f` became active after encrypted backup and guarded same-runtime upgrade; Doctor was healthy, authenticated exact-focus and phone review passed, observation remained active, and the provider-called count remained zero. | PRESERVE guarded deployment and observation boundaries |

## Staged control-plane retirement delta

| Subsystem | Retired state | Current result | Classification |
|---|---|---|---|
| Process authority | Claude executor instructions, G-001, the Outcome Harness, and proposed G-002/ADR-0012 created overlapping task loops. | Active executor instructions, repository workflow skills, harness schemas/code/tests, generated state, and exact-head authority are removed. Git history retains provenance without making it current authority. | SUPERSEDE |
| Member navigation seam | The focused navigation module landed in the mixed Outcome Harness merge. | `navigation.py`, compatibility imports, and behavior coverage remain as ordinary product code and tests. Routes, labels, authorization, modes, observation, events, and provider behavior are unchanged. | PRESERVE behavior; MODERNIZE tests |
| CI | Harness consistency and artifact validation occupied the required status context. | The same protected `deterministic-consistency` context now runs ordinary tests, lint, types, invariants, dependency-lock validation, OpenClaw checks, and Git diff hygiene. | MODERNIZE |
| Runtime and providers | Delivery tooling could be confused with live authority. | The reboot changes no runtime state, household data, credential, provider, observation setting, or deployment. Live changes remain separately requested and guarded. | PRESERVE |
