---
schema_version: 1
current_phase: 8
phase_name: production_activation
gate: 8
gate_result: CO_017_DEPLOYED_LIVE_VERIFIED
verified_local_date: 2026-08-12
verified_github_date: 2026-08-12
branch: main
candidate_commit_resolution: d181fad24ef488a71ce5568cc40fb81309cb3817
pull_request_url: https://github.com/junglecrunch1212/administrate-me-now/pull/336
openclaw_version: 2026.7.2
openclaw_commit: ce67ffb70e83e5ebb56ea02e814e577153f84f61
openclaw_archive_sha256: 3fffb07090f63492bff89daac6ae0879e66e205c743911598bc79e3bb7e2b577
next_phase: observation_review_and_candidate_signal_quality_iteration
---

# Release handoff

## 2026-08-12 CO-018 local candidate

Status: amended again after independent review rejected exact head `93bc404`;
the terminal-member-decision race is repaired and fresh complete aggregate
validation passes. Independent review, merge, deployment, and any run against
a family Mac or live provider remain pending.

The candidate corrects a message-context false-association class without
changing source facts or member authority. Gmail adapter v1.3.0 records provider
message time as `occurred_at` and append time as `recorded_at`; additive
`messaging.sent@v4` retains the HTML MIME alternative needed to bound quoted
Sent history. Immutable pre-v4 Gmail Sent facts remain readable but their
bodies fail closed with an explicit provenance-unavailable assessment during
v21 replay; untouched system proposals previously rooted in those bodies are
superseded while member-enriched or decided proposals remain permanent.
Additive `triage.item.superseded@v3` carries the exact inspected proposal
revision, so a later-appended member enrichment wins deterministically even if
it races replay. Message context
v2 splits reused provider threads into 30-day/100-source episodes and requires
exact RFC continuity, exact attachment identity, or explicit member
confirmation for a cross-thread source. Repeated participants, addresses,
names, subjects, and filenames are corroboration only. Authored-text v2 is
shared by language input, deterministic fallback, exact grounding, and source
display, including fail-closed offset-zero and uncertain quoted/forwarded
history. Explicit HTML quote boundaries constrain multipart plain-text
alternatives, with disagreement failing closed. Gmail message-local attachment
IDs are explicitly non-admitting, and invalid/missing provider timestamps are
quarantined rather than rewritten as ingestion time.
The language payload reserves its bounded character budget for the latest
anchor while retaining the source under assessment. Replay reconciliation uses
the full bounded episode rather than the 12-message model window.

Triage wrong-context dismissal now supports an atomic rejection of the actual
admitting source edge and an evidence-span-scoped quoted-history correction;
other asks in the message remain intact. Its browser controls are emitted only
for server-declared exact correction capabilities, so a deterministic fallback
without uniquely applicable evidence cannot offer a dead-end action. Exact
admitting sources outside the display window are separately authorized and
hydrated before that capability is declared. Member-owned confirmation and
reinstatement endpoints reverse each decision. Context Fabric v2 rebuilds the
state from immutable events, and wrong-context feedback cannot aggregate into a
broad sender/account/workspace suppression suggestion. Noise filtering v21
opts into full replay and retires only untouched stale context interpretations;
an unchanged context-free same-source deterministic fallback that remains
eligible under the current high-signal rule keeps its existing pending item
instead of being superseded and hidden by the historical candidate identity.
When that deterministic core is attached to a stale contextual interpretation,
the old proposal is superseded and excluded from active-history dedupe while a
distinct replay-versioned context-free proposal keeps the valid ask pending.
Member decisions remain permanent. The new production-composition acceptance
tests complete detach, relink, reinstate, and rebuild and exercise actual Gmail
mapping through a v20-to-v21 full replay that supersedes the stale crossed-wire
proposal and an untouched false legacy-Sent proposal while retaining a newly
derived valid sibling and replacing stale context around a still-valid v20
deterministic fallback with exactly one context-free pending proposal;
a separate production process appends enrichment between
inspection and stale v3 supersession and keeps it pending, with no canonical or
provider effect. Aggregate gate evidence is recorded in the CO-018 evidence
file. The rejected exact-head review also identified a rollback-documentation
typo (`messaging.sent@v3` instead of the emitted `messaging.sent@v4`), which is
corrected in the change order. Fresh complete gates, independent exact-head
review, and the ordinary controlled-release path remain mandatory before any
Phase B action.

The subsequent independent review of exact head `c915c1c` found that the first
fallback repair covered only prior proposals without an interpretation object.
A v20 model result can overlap a deterministic fallback, retain its
`message-fallback-*` identity, and attach contextual interpretation metadata.
If that metadata became stale, v21 correctly superseded the old proposal but
then historical kind/key dedupe suppressed the still-valid deterministic
replacement. The amended implementation now carries the exact superseded
proposal event IDs across reconciliation, excludes only those events from
active-history dedupe, and derives replacement proposal IDs from the replay-
versioned idempotency identity. Unit and production Gmail replay proofs cover
both retention of an unchanged context-free prior and replacement of a
contextualized prior. The `c915c1c` receipt is rejected and grants no release
authority. The amended worktree passed the 186-test changed-path slice, the
complete aggregate release-candidate suite, a separate 1,700-test configured
root discovery, and the invariant scan. Final audit then closed the companion
append-order case: when a member revision causes revision-bound supersession to
no-op, the still-active same-source/kind/key predecessor now suppresses
projection of the later replacement rather than allowing duplicate review
work. The production race process proves both the member revision and the
absence of a projected replacement. The race-hardened worktree passed the
186-test changed-path slice, complete aggregate release-candidate suite, and
separate 1,700-test configured root discovery. Hosted checks and independent
review remain required for the final head.

Independent review of exact head `93bc404` then proved that dismissal and
confirmation could win the same append window yet still be followed by a new
pending replacement. The first projection guard considered only active states;
that preserved enrichment but treated terminal member history like successful
system retirement. The amended projection now admits a replay replacement only
when the predecessor bears the exact system `eligibility_rule_replaced`
disposition. The production process is parameterized across enrichment,
dismissal, and confirmation: each member action remains authoritative, the
stale revision-bound supersession cannot override it, and the later replacement
does not project. The `93bc404` receipt is rejected and grants no release
authority. The amended worktree passed the 188-test changed-path slice, 63
acceptance processes, 1,871 aggregate tests, 60 bridge tests, a separate
1,702-test configured root discovery, and every invariant/static/audit/
performance/build/isolation/backup/OpenClaw release gate. Hosted checks and
independent exact-head review remain mandatory.

## 2026-08-12 CO-017 deployed and live verified

Status: PR #336 is merged, deployed, and independently live verified in
observation mode. Exact reviewed head
`fd13157648ae3ad02b4190a95feb817ad99fee46` received a fifth independent
review with no findings, both hosted checks passed, and GitHub created merge
commit `d181fad24ef488a71ce5568cc40fb81309cb3817` with the authorized base and
head as its two parents.

The first post-merge preflight stopped before updater invocation because a
transient Google Calendar connection reset was still latched as unrelated
`backoff` health. No backup, recovery request, release copy, pointer switch, or
provider effect occurred. Separate owner authority permitted one
current-release restart only. An initial direct method call was rejected before
service mutation because the predecessor does not support the candidate's
new quiescence keyword; unchanged service state was reverified. The one
authorized restart then used the predecessor's actual production method,
returned Calendar to running observation-only health with no current error,
and left the exact four-source degradation as Doctor's sole failure. Fresh
exact-head G-001 authorization then resumed the release transaction.

The attested guarded updater selected immutable merge
`d181fad24ef488a71ce5568cc40fb81309cb3817` without a runtime or OpenClaw
migration. At stopped watermark `ev_01KZV0VRC80003`, it recorded 62,673/62,673
source obligations, zero pending/missing, the exact four retained failures,
eight healthy pipelines, fifteen healthy projections, zero unhealthy durable
consumers, and a live Gateway. It appended exactly four content-free recovery
requests. Each produced one newer linked v4 `no_candidate` assessment under
the pinned seam; all four prior failed assessment events remain immutable and
each replacement is now the latest assessment for its exact source.

Encrypted backup `administrateme-20260812T125603Z.ambackup` authenticated and
hash-verified in memory: its envelope and manifest agree, all 6,222 allowlisted
files match, all fifteen projections are represented, and no unsafe archive
member exists. Rollback remains available to predecessor
`2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe`; both immutable release trees and
their runtimes remain present, and the predecessor is compatibility-valid for
the unchanged OpenClaw/runtime baseline.

Final live evidence is healthy at 62,674/62,674 source obligations with zero
pending, missing, or failed dispositions and no degraded capability. Full
Doctor passes 21/21 checks, including fifteen projections, healthy durable
checkpoints, the pinned model seam, member portal/chat health, and a canonical
synthetic round trip with zero external side effects. Authenticated read-only
Triage acceptance through Tailscale rendered the private principal surface,
observation status, no-operator boundary, all ten member routes, and no
recovery identifiers, warnings, or errors. A nonexistent exact view target
returned an empty inventory with no fallback to the authenticated member's
nonempty inventory. Observation emitted no state-change event, no member
decision or provider action was performed, and the provider-called terminal
count remained zero through final watermark `ev_01KZV199ZX0000`.

## 2026-08-11 CO-017 candidate and review history

Status at the time: the recovery implementation was merged but not deployed;
a follow-up release-safety candidate was in progress. The deployment closure
above supersedes this historical candidate status.

The original source-assurance snapshot at current production commit
`2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe` accounted for 62,623 source
obligations with zero pending and zero missing, but four exact message sources
remained terminally failed with `language_seam_OpenClawUnreachable`.
Generation 19 exhausted its base and two transient invocation identities.
Ordinary replay then encountered the append-only log's correct idempotency
conflict because the generation-19 assessment key already names a different
failed payload.

CO-017 adds typed, content-free recovery request/refusal events and a linked v4
assessment. An authenticated adult operator may submit only exact supported,
private, same-member, non-privileged sources whose newest exact assessment has
an allowlisted timeout/unreachable reason, and only while observation mode is
active. Processing revalidates the request, uses a fresh request-bound
assessment and pinned `llm-task` invocation namespace, caps execution at two
concurrent requests and three total language attempts, and preserves the old
failure. Source assurance treats the newest valid replacement as current.
Delivery rechecks observation, tenant, and current-predecessor truth. Distinct
requests for one source are single-flight through terminal append; derived
events are ordered after their command on cold reopen; and the request-bound
base call may bypass the predecessor's old causal skill failure once without
repeating its own result. Base, recovery-1, and recovery-2 each find their exact
durable terminal across a mid-retry restart even when a sibling failed later.
Duplicate delivery and restart reuse durable
identities, refusal is terminal for its exact command, and a persistent failure
remains explicit.

PR #335 exact head `bedd3180ce57c4ba9f8d4c136b3f25411b40f17f`
received independent review and complete local/hosted gates. G-001 authorization
produced GitHub merge commit
`2a37aa08179a356fddfe0d4cd30ffd3e29f732a3`. Phase B then stopped before
backup or activation: ordinary live ingestion had advanced the fully accounted
snapshot to 62,632/62,632, and a prior transient Google Tasks connection reset
was still latched as current `backoff` health. No updater, recovery request,
release pointer, or provider write was invoked. A documented current-release
restart proved the provider healthy and returned Google Tasks to running,
observation-only state; accounting then advanced normally to 62,633/62,633.

The follow-up changes the checked-in exception to v2. It remains restricted to
the exact active production commit, the original 62,623-obligation floor, the
four declared source IDs and reasons, fresh accounting, observation mode, and
no unrelated degradation. Growth above the floor is accepted only when every
current obligation is accounted and pending/missing remain zero. The stopped
encrypted-log audit requires equality with the final live watermark, records
the exact activation watermark/current counts, verifies durable pipeline and
projection health, and carries a Gateway-live-at-quiescence proof in the
content-free receipt before appending the four requests. Any intervening event,
consumer failure, new failure, missing/pending obligation, count regression,
stale audit, source/reason change, or hidden degraded capability fails closed.
Google Tasks
now restores current health after a successful poll while retaining the
transient error in immutable audit history. Runtime status no longer relabels a
degraded readiness document as observation-only, and post-switch settlement
reads readiness for every allowed transient and aborts immediately on a hidden
structural degradation or unavailable readiness after runtime liveness.

The follow-up drift/status hardening passed the focused policy/status and
combined recovery/runtime/updater/Google Tasks process suites, 57 strict
acceptance gates, 1,827 root/package tests, 60 bridge tests, the complete
release candidate suite, and the separately required 1,660-test root run,
Ruff, mypy, and invariants. The disposable arm64 macOS rehearsal passes 79
tests with both plists valid. Node 24 Gate 7 passes 259/259 checks and 39
screenshots with zero console, page, or request failures. Fifth independent
review accepted exact head `fd13157648ae3ad02b4190a95feb817ad99fee46`, both
hosted checks passed, and the fresh G-001 authorizations described above closed
the release. The prior authorization was not reused.

PR #336's first independent review of head
`7ccb555e2cb57d3b2157931ba66a0d568b9712c0` rejected two P1 gaps: the stopped
audit reused stale unrelated capability state, and only two transient Doctor
classes queried readiness. The findings are addressed by the live/stopped
watermark binding, stopped durable-consumer and Gateway-quiescence receipts,
and all-transient readiness rule above. That head remains unapproved; complete
gates and a fresh independent review of the amended immutable head are
mandatory before requesting G-001 authorization.

The second independent review rejected amended head
`e601a4e14efc15f1127833cc5f4611f36aa02569` with one P1 rollback gap: when
initial quiescence partly stopped the service pair, an unguarded second cleanup
stop could fail and bypass selector/plugin restoration and the predecessor
restart. Candidate activation and explicit rollback now collect that cleanup
failure, continue through every restoration step, always attempt the required
predecessor restart, and report the original plus all secondary errors. A
process regression forces both stop attempts to fail after the runtime has
stopped and proves exactly one predecessor restart, healthy final service
status, and no candidate pointer. The focused updater/source-repair/macOS slice
passes 85 tests. The rejected head has no release authority; complete gates
and a third independent exact-head review remain mandatory.

The third independent review rejected head
`9cfd0da1b4cbdd14cb28940649552664c196de9d` with one P1 status gap.
`ServiceManager.start()` can return failed readiness without raising, while
both restoration branches checked only for exceptions. Candidate rollback and
explicit rollback restoration now require the returned predecessor/current
service status to be exactly healthy and retain failed/degraded status,
runtime, and Gateway state in the terminal error. The activation process test
now combines both stop failures with a non-raising failed restart, and a new
explicit-rollback regression proves the same rule after rollback Doctor
failure. The focused updater/source-repair/macOS slice passes 86 tests. The
third rejected head has no release authority; complete gates and a fourth
independent exact-head review remain mandatory.

The fourth independent review accepted head
`1175dc6e498d0a79226179095683f7fbe9b872bb` with no findings and confirmed all
prior P1s closed. Its hosted macOS job nevertheless exposed one deterministic-
test gap before the repository aggregate ran: the isolated OpenClaw-transient
unit test did not provide a readiness document, so its result depended on
whether the executing Mac happened to have an unrelated loopback runtime.
Production correctly failed closed when the GitHub runner had none. The test
now supplies explicit healthy readiness, preserving the production rule while
removing host-state dependence. That otherwise accepted head has no release
authority because its hosted gate failed; complete gates and a fifth
independent exact-head review remain mandatory.

## 2026-08-10 G-001 controlled-release governance bootstrap

The household owner approved G-001 to remove the deadlock between the
repository's deny-by-default merge rule and the requirement that Phase B begin
only after review and merge. This is a governance-only change: no application,
bootstrap, bridge, pack, plugin, workflow, configuration, dependency, lock,
test, fixture, runtime, credential, permission, provider, or household-data
behavior changes.

ADR-0009 preserves “do not merge” as the default and defines one explicit
capability: a current-task owner instruction must name one PR and exact
40-character head. A non-author human or separate non-implementing Codex task
must review that same head. Current `origin/main` must be contained, every
required final-head gate must pass, all findings must be resolved, and only a
GitHub merge commit may change `main`. Base drift requires a non-rewriting
merge into the candidate, complete rereview and retest, and a new exact-head
authorization.

Phase B remains post-merge only. It accepts the immutable merged SHA through
the guarded updater after a verified encrypted backup, retains observation,
rolls back on any new degradation, and requires settlement, full Doctor,
privacy/isolation, authenticated read-only browser acceptance, and zero
unintended provider effects. Runtime/dependency migration, Keychain or
permission change, provider enrollment/write activation, unrelated repair,
and installed-release editing remain outside the authority. One hosted-green
governing-docs-only follow-up may record release evidence.

This governance PR cannot authorize its own merge and does not authorize any
existing product PR. It becomes effective only when the owner manually merges
it to `main`. The exact product PR/head must then receive a fresh controlled-
release instruction in a newly created task that loads the merged rule.

## 2026-08-10 CO-013 bounded Gmail history activation

Live investigation after CO-012 found the missing-source cause: Gmail's first
poll intentionally snapshotted the current history cursor and imported zero
existing mail. The healthy downstream system therefore had no source facts
for a pre-launch multi-message obligation. CO-013 makes first-use acquisition
finite, durable, and explicit while retaining the provider as source and
Triage as a human gate.

Gmail adapter v1.1 snapshots the incremental cursor first. Optional private
historical seeds run before deterministic exact-participant/date expansion;
any remaining capacity is filled by up to 1,000 non-draft messages from the
prior 730 days by default. Global hard ceilings remain 3,650 days and 5,000
messages. Query text never enters an event, status, or cursor ID. A content-
free policy-digest cursor makes restart idempotent and status reports only
bounds, counts, completion, and truncation.

Focused adapter, contextual-reasoning, and production-supervisor tests pass,
including seed-first priority, exact participant/date expansion, Python 3.14
address parsing, context-before-seed chronological emission from newest-first
results, paging, hard caps, private-query non-disclosure, normal typed emission,
durable replacement-instance idempotency, and preservation of incremental
cursor ordering. The synchronous Gmail acquisition batch now executes outside
the central asyncio loop; a held-open provider call proves runtime health and
unrelated scheduling remain responsive. The full
candidate matrix passes: 1,764 root/pack tests, 60 isolated bridge tests, 47
strict acceptance tests, Ruff/format/mypy/Pyright, dependency/security/
architecture/invariant checks, performance budgets, builds/packages, clean
install, tenant isolation, encrypted backup/restore, the exact pinned native
OpenClaw contract, and all 17 plugin tests. PR #314 merged after its hosted
constitutional cascade and Apple-silicon rehearsal passed. Its first guarded
activation correctly rolled back when the synchronous history batch starved
the health surface; the older release is healthy and its incompatible private
settings were removed. At that checkpoint the narrow liveness repair still
required review, merge, and guarded redeployment. No candidate was confirmed
and no provider effect was authorized.

PR #315 subsequently passed the hosted constitutional cascade and 17m24s
Apple-silicon rehearsal, merged as `7d750cd8811dbed1b84fc104d3a66f2fa791b91e`,
and deployed through the attested updater with an encrypted backup and rollback
retained. Live acquisition raised source facts from 61,398 to 62,526 while the
runtime liveness surface remained responsive, and candidate-eligible facts rose
from 13 to at least 18. The expanded facts then exposed a cross-layer schema
defect: deterministic context assembly emitted
`participant_reference_overlap`, but the pinned skill input and typed Triage
context schemas rejected it. Forty-five retained failures share that content-
free cause; the model was never called for them. The follow-up advances
`classify_message_nature` to 6.0.1 and `noise_filtering` to 13.0.0, admits the
reason in both schemas, and performs reviewed full replay under the next
idempotency generation. PR #316 passed both hosted gates and merged as
`6cc627ffca4ef11bf9670e440d5eee82bfbdff11`. Guarded activation proved the
schema repair: replay advanced without new failures and began superseding the
retained failures, but the real 1,500-message language-bound backlog progressed
too slowly for the 30-minute controller ceiling. A first throughput follow-up
proved that sixteen-way delivery is unsafe for context-linked sources sharing a
durable idempotency identity: the bus correctly poisoned the subscriber on
conflict. The final repair restores the reviewed eight-way width, retains the
still-bounded one-hour settlement window, and makes the lightweight settlement
loop run full Doctor immediately when any non-source capability newly degrades.
That fail-fast path passed every local and hosted gate, merged as `a9a4045`,
and correctly rejected the next live attempt at the first retained generation-13
idempotency conflict. The generation-14 activation then advanced cleanly past
that retained identity and processed more than 1,080 message facts, raising
eligible accounting to at least 133, before a later pair of context-equivalent
sources exposed the underlying read-before-append race even at width eight.
The fail-fast gate again rejected and rolled back the release without deleting
evidence. Generation 15 then proved the atomic first-equivalent-writer repair:
it crossed the exact later collision with zero subscriber failures or poison.
That live pass also exposed a retry herd when several retained language
failures reached the pinned Gateway together; parallel immediate retries could
still leave failed terminal assessments. The generation-15 pass later found a
second conflict after its first three proposals for one assessment had already
appended. Encrypted metadata proved the fourth recorded candidate reused the
same `(target_kind, candidate_key)` as an earlier candidate but had a different
payload. That is an intra-interpretation duplicate, not a cross-source race.
Generation 17 fixed that boundary, but its guarded live replay exposed that the
queued recovery reused the base durable invocation identity. The skill wrapper
correctly replayed the current generation's recorded failure instead of issuing
an unbounded second call, so the source remained failed. Generation 18 gave the
recovery its own durable identity and live replay proved the first retained
failure recovered, but one later OpenClaw stream reached its real 60-second
terminal timeout. That pass also crossed both retained idempotency-collision
sources without a subscriber failure and increased the eligible-candidate count
to 166, proving the atomic and same-interpretation identity repairs. The final
candidate is therefore v19/generation 19.
The first generation-19 activation also exposed a provider list/get deletion
race: Gmail returned one discovered id as 404 after it had disappeared. The
record was already quarantined and the cursor advanced, but adapter v1.1.0
reported the whole source in backoff. Adapter v1.1.1 HARDENS that distinction:
record-level quarantine remains audited while a successful polling cycle stays
healthy; real transport, rate-limit, authentication, and API errors retain
their existing degraded/fatal paths.
PR #325 merged that v1.1.1 repair as
`b58e09dab80df4161f7314a541f5657b46677fbb`. Guarded activation of the exact
merge settled 62,553/62,553 source obligations with 225 candidate-bearing
facts and zero pending, missing, or failed. Generation 19 recovered the retained
failure, crossed both historical collision points, and live-proved its second
bounded recovery after a base timeout and unreachable first recovery. Full
Doctor is healthy; authenticated Triage shows 33 Ready and 67 Needs Details
records under observation. No member decision or provider write occurred.
Deliberately shared proposal and supersession
identities let the SQLCipher event log atomically select the first equivalent
writer, while any non-equivalent key reuse remains a hard conflict. Ordinary
delivery retains width eight. Transient OpenClaw timeout/unreachable recovery
serializes behind a runtime-local lock and uses at most two separate,
deterministic `:recovery-1` / `:recovery-2` invocation identities. A prior
success is reusable on replay, while exhausting both attempts remains terminal
and cannot trigger more provider work without a new reviewed change. Within one
recorded interpretation, fixed code collapses repeated typed
candidate identities to the first recorded occurrence before emitting the
terminal assessment or proposal; confidence never ranks that choice. A
persistent failure still emits an auditable failed disposition and fails
Doctor. The one-hour guard remains unchanged. No candidate has been confirmed
and no provider effect has been authorized. Final settlement, Doctor, and
authenticated Triage proof are complete; the exact content-free evidence is
retained in `docs/refactor/evidence/co-013-bounded-mailbox-history-acquisition.md`.

## 2026-08-10 CO-012 contextual-message candidate

The observed legal-message failure is now represented by
`CHANGE-ORDER-012-CONTEXTUAL-MESSAGE-REASONING.md` and AC-37. The candidate
replaces isolated-body candidate interpretation with a bounded private context
pack assembled only from immutable same-member/account/workspace facts. Exact
provider-thread, header-participant, authored participant-reference,
normalized-subject, and attachment-filename edges are code-owned and
disclosed. Quoted history is removed before the
pinned skill receives the pack. Content deduplication is bound to tenant,
member owner, visibility, sensitivity, provider account, workspace, and thread
identity so identical notices cannot collide across private member scopes.

The pinned `classify_message_nature` skill v6 may return several distinct
schema-bound interpretations with stable aliases, exact quotes, apparent
owner/status, compact and expanded review aids, next step, unresolved
questions, and related aliases. Fixed code rejects ungrounded, closed,
other-owned, related-only, out-of-scope, and disallowed output. Message
pipeline v12 evaluates candidates at the latest retained thread source,
prevents same-local-offset collapse, supersedes only untouched older
interpretations, and never reopens a member decision. Triage proposal v3 and
projection v6 retain the private review explanation separately from canonical
truth.

The complete local candidate matrix passes: 1,745 root/pack tests, 60 isolated
bridge tests, 47 strict acceptance tests, Ruff, format, mypy, Pyright,
dependency/invariant/architecture checks, performance budgets, builds,
packages, clean install, tenant isolation, encrypted backup/restore, the exact
pinned native OpenClaw contract, and all 17 plugin tests. That includes a
fictional six-message legal-authorization matter and a related signing notice
that yield four separate human-gated Task/Commitment decisions with exact
multi-source provenance and zero provider effects. The rendered console passes
223 desktop/narrow/accessibility checks with zero browser, page, or request
errors. Reviewed merge, guarded
observation deployment, replay settlement, Doctor, and authenticated live
desktop/narrow Triage review remain required before this section may claim
deployment.

## 2026-08-09 CO-011 deployed and live verified

`CHANGE-ORDER-011-CANDIDATE-IDENTIFICATION-AND-TRIAGE-CONTEXT.md` now governs
the member-facing candidate source policy and decision context. The candidate
adds an executable catalog for every stable member entry, OpenClaw Add for
review, current adapters, and reviewed future source families. Settings stores
only revisioned member/source mode, grounded-language boolean, and supported
kind subset; explicit member entry remains locked on and all source, privacy,
routing, confirmation, governance, observation, and provider-effect rules are
non-configurable.

Message, Notes, Finance, and reviewed context pipelines receive effective
policy through the production composition root. Quiet/manual/language-disabled
paths remain terminally assessed. Triage now composes authorized read-time
source identity, direction, sender/counterparty, subject, time, allowlisted
fields, literal evidence, extraction methods, and policy snapshot from the
immutable source event. It never presents a generated paraphrase as evidence.

Local evidence passes: 1,740 root/pack Python tests, 60 isolated bridge tests,
46 strict acceptance tests, Ruff, mypy, invariants, native OpenClaw contract
and 17 plugin tests. A disposable authenticated browser saved/reloaded/restored
Gmail policy, rendered exact message context in Triage, reflowed Settings and
Triage without horizontal overflow at 390×844, and reported no browser errors.

PR #311 passed the constitutional cascade and macOS credential-free rehearsal,
merged as `cd49c60d608c1acf3e0baf9aa5f000c982382a66`, and deployed through the
attested same-runtime updater after encrypted backup
`administrateme-20260809T204055Z.ambackup`. Rollback remains available and
observation remains active. The active release stayed on Python 3.14.6, Node
24.19.0, and pinned OpenClaw 2026.7.2; this was not a runtime migration.

The intentional replay converged with zero consecutive pipeline failures and
zero lag. Live pipeline versions are noise filtering 11.0.0, Notes indexing
5.0.0, and deterministic structured sources 2.0.0. Final Doctor is healthy:
14/14 projections, 8/8 pipelines, authenticated `llm-task`, 5/5 managed jobs,
and the canonical synthetic round trip pass with zero external effects. Source
assurance is current and settled at 61,335/61,335 obligations: 28 source facts
have candidates, 309 have explicit no-candidate dispositions, 39 are safely
quarantined, and pending, missing, and failed are all zero.

Authenticated live browser proof renders the 33-row current/future source
matrix and its eleven candidate types in Settings. Triage renders 100 visible
records (15 ready and 85 needing details), including reviewed Gmail billing and
payment actions. The selected live candidate exposes channel, direction,
adapter/account provenance, sender, subject, observed time, possible target
type, literal source evidence, and extraction-method badges beside the human
gate. The live browser console has no warnings or errors. No candidate was
confirmed or dismissed and no provider write was authorized.

## 2026-08-09 CO-010 deployed and live verified

PR #309 passed both hosted gates, merged as
`e908ee5d93944da41a5d0dfbd3dd2f16fad5ba4a`, and deployed through the
attested updater after encrypted backup
`administrateme-20260809T185921Z.ambackup`. The updater retained rollback and
observation mode. OpenClaw inspection proves the registered install source and
loaded runtime module both resolve inside that exact immutable release; all
Doctor plugin identity booleans are true.

A controlled stop/start proved automatic Gateway-before-runtime convergence.
The native plugin first reported the runtime unavailable, retried after one
second, and became ready without an operator POST. Settled idle sampling used
0.09 CPU-second over 30 seconds (approximately 0.3% of one core) with stable
RSS. A final full Doctor is healthy: 14/14 projections, 8/8 required pipelines,
5/5 managed OpenClaw jobs, authenticated `llm-task`, and the canonical
synthetic round trip all pass with zero external effects.

Source assurance is current and settled at 61,335/61,335 obligations: 31
source facts have candidates, 306 have an explicit no-candidate disposition,
39 are quarantined, and pending, missing, and failed are all zero. Authenticated
member-browser proof renders Triage with 77 items needing details and 13 ready
for confirmation and no console warnings or errors. The visible queue includes
payment/billing changes and requested actions, closing the original false-empty
failure. It also includes lower-value automated engineering notices; that is a
signal-ranking and member-review concern, not missing extraction. No candidate
was confirmed or dismissed and no provider write was authorized. Observation
soak and member review are the next gate.

## 2026-08-09 immutable OpenClaw plugin-selection candidate

PR #308 passed both hosted gates, merged as `43cd565`, and deployed through
the attested updater after encrypted backup
`administrateme-20260809T182639Z.ambackup`. Status and full Doctor are healthy:
the runtime is observation-only; all fourteen projections and eight required
pipelines are healthy; five OpenClaw jobs reconcile; and source assurance is
settled at 61,335/61,335 with 31 candidate-bearing facts and zero pending,
missing, or failed outcomes.

The requested reboot proof was not accepted. Direct OpenClaw inspection showed
that its linked AdministrateMe install and loaded module still came from old
release `d52050b`. The candidate plugin had been built in `43cd565`, but upgrade
selected only the OpenClaw executable and launch definitions. Name-only Doctor
discovery allowed the same-version old plugin to pass, so PR #308's health retry
was not actually running.

PR #309 stops services, removes only bounded stale AdministrateMe
registrations, links the candidate plugin through the reviewed OpenClaw CLI,
validates config, and requires install path plus runtime source to resolve in
the selected immutable release. Rollback symmetrically restores the previous
plugin link with its executable, pointer, and launch definitions. Focused unit
and production-composition tests pass. The complete candidate matrix also
passes: 46 strict acceptance tests, 1,725 root/pack tests, 60 isolated bridge
tests, 17 native plugin tests, lint/types/security/invariants, performance,
build/package, clean install, isolation, and encrypted backup/restore. Review,
merge, attested deployment, exact runtime-source proof, controlled full
restart, Doctor, idle CPU, and authenticated Triage browser proof remain
required.

## 2026-08-09 OpenClaw startup-health reconciliation candidate

PR #307 passed both hosted gates, merged as `cbc6cc9`, and deployed through
the attested updater after encrypted backup
`administrateme-20260809T174639Z.ambackup`. Doctor accepted the settled exact
61,333/61,333 source proof with 30 candidate-bearing facts and zero pending,
missing, or failed outcomes. Live process sampling confirmed that settled
assurance now idles instead of continuously scanning SQLCipher.

The deployment exposed a separate reboot-order defect: the Gateway must start
before Python reactive replay, but its native plugin sent AdministrateMe's
authenticated exact-pin health report only once. If Python was not yet
listening, both services could later be healthy while integration state
remained stale and the updater required a manual report.

PR #308 preserves the required order and makes native-plugin startup
nonblocking while reconciling health on capped 1/2/5/10/30-second backoff. It
stops after success, cancels on shutdown, retains exact version/commit refusal,
and logs no response content or secret. The complete candidate matrix passed:
46 strict acceptance tests, 1,721 root/pack tests, 60 isolated bridge tests,
all 17 native plugin tests, lint/type/security/invariant checks, performance,
build/package, clean install, tenant isolation, and encrypted backup/restore.
Reviewed merge, attested upgrade, controlled Gateway-before-runtime restart,
Doctor, and authenticated Triage browser proof remain required.

## 2026-08-09 CO-010 settled-idle assurance follow-up

PR #306 passed the local and hosted release gates, merged as `d248c37`, and
deployed through the attested updater after encrypted backup
`administrateme-20260809T171713Z.ambackup`. The updater returned
`status: upgraded`; Doctor accepted the current-revision 61,333/61,333 proof,
with 30 candidate-bearing facts and zero pending, missing, or failed outcomes.
Observation mode and the exact pinned OpenClaw runtime were preserved.

Live sampling found a remaining performance defect: SQLCipher still decrypted
the event table for all source facts, and the settled proof was recomputed
every 30 seconds despite an unchanged append-only head and unchanged
checkpoints. The follow-up candidate adds migration v4's covering metadata
index, splits terminal payload access into a disjoint query branch, and makes
settled exact revisions idle. Revision changes still refresh immediately;
pending work still rechecks every 30 seconds; refresh errors fail immediately;
and an unsettled proof fails stale after five minutes. Final review, merge,
attested deployment, full Doctor, an idle-CPU observation, and authenticated
Triage browser proof remain required.

That settled-idle correction is published for review as PR #307.

## 2026-08-09 CO-010 live activation and source-assurance correction

PR #305 passed the full local and Apple-silicon release gates, merged as
`9591e01`, and deployed through the attested candidate-owned updater after
encrypted backup `administrateme-20260809T162955Z.ambackup`. The live replay
accounted for 61,333 of 61,333 source obligations with zero pending, missing,
or failed outcomes. It classified 30 source facts as candidate-bearing, 305 as
explicit no-candidate, and 39 as quarantined. The central runtime and pinned
OpenClaw Gateway are running from reviewed immutable releases in observation
mode; no provider write was authorized.

Independent full Doctor verification found one intermittent operational
failure after that successful settlement. Source assurance decrypted and
materialized every full provider payload on each refresh; the 61,000-event
scan took longer than its 30-second interval, and the runtime treated refresh
activity itself as stale. The correction candidate keeps the mandatory initial
audit and unchanged terminal-count predicate, but closes each later audit at a
fixed event-ID watermark, reads metadata for ordinary source facts, parses only
required terminal payloads, and separates `refresh_in_progress` from bounded
freshness. Refresh exception degrades immediately; a successful proof older
than five minutes degrades as stale. Doctor also requires the audited revision
to equal the current event/checkpoint head, so cached evidence cannot certify
new facts. Review, merge, attested upgrade, full Doctor, and authenticated
Triage browser proof remain required before this handoff can advance to
observation soak.

That correction is published for review as PR #306.

## 2026-08-09 CO-010 lifecycle-completeness candidate

The principal authorized a whole-life event audit after the live mailbox had
retained source messages but surfaced no useful Triage candidates. The clean
candidate branch adds an executable catalog (15 life patterns, 11 candidate
channels, 121 source/target decisions, 23 object lifecycles, and all registered
event schemas) plus a generated evidence artifact and process gates. It does
not alter the current family runtime before review and merge.

The repair broadens message, Note, member-intent, and pinned OpenClaw proposal
vocabulary to all eleven Triage kinds while keeping exact evidence and human
confirmation mandatory. Retained message/Note facts are replayed on reviewed
pipeline-version activation; no mailbox fact is rewritten. Plaid recurrence
recognition and reviewed Home Assistant rule snapshots are deterministic and
never sent to a model. Comms, Notes, Calendar, and Reminders can explicitly
reassess exact visible source evidence.

Typed Task, Commitment, Recurrence, Routine, Place/Asset/Account, and Finance
Goal lifecycle commands now retain terminal outcomes and surface them in
Reminders, Today, Finance, Settings, and Scoreboard as appropriate. Google
Tasks restart polling persists fingerprints with its cursor. The Finance
workbook emits the same Goal lifecycle events as the portal instead of
overwriting status. Optional providers remain truthful and unconfigured.

Phase A release evidence is complete on the required Python 3.14 / Node 24
candidate path: 46 strict acceptance tests, 1,716 root/pack tests, 60 bridge
tests, static analysis, audits, builds, clean install, tenant isolation,
encrypted backup/restore, OpenClaw contract/build/tests, and shell checks all
passed. The rendered Gate 7 harness passed 223 assertions with zero recorded
failures. Phase B remains pending: final diff review, merge, attested immutable
upgrade, Doctor, and observation-mode source replay must pass before the
handoff front matter may claim CO-010 deployed.

## 2026-08-09 CO-009 cross-surface Task lifecycle (deployed and live verified)

PR #302 merged as `3ab4b7839b0ad867eed326b34c576fd1ffc150cb` and the
attested upgrade controller deployed it with a compatible-runtime receipt,
encrypted backup, immutable rollback release, and observation mode preserved.
Live member-scoped API and browser proof showed both confirmed email-derived
Tasks in the canonical Reminders union under their original Task IDs. The
confirmation and projection path was correct; the remaining member-visible
defect was receipt navigation, which selected the exact item but left Reminders
on the incompatible default Now lens for Anytime work.

The follow-up makes receipt navigation select the exact Task and its
deterministic activation lens while clearing only incompatible presentation
filters. The same live deployment exposed a separate Google Calendar restart
defect: unchanged calendar-source facts were re-appended, and initial sync put
already-past history into in-memory elapsed tracking, causing large redundant
outcome batches after each runtime restart. The candidate uses the shared
durable adapter dedupe store for unchanged source observations, excludes
already-elapsed history during initial discovery, and gives legitimate future
elapsed/cancelled transitions stable idempotency. It does not persist a partial
Google sync token or change provider authority. Focused adapter and lifecycle
suites pass 46 tests, and rendered Gate 7 passes 222 assertions with 36
screenshots and zero console, page, request, accessibility-name, overflow, or
result failures.

PR #303 passed both required GitHub gates and merged as
`6d2d6e582a3e76060901ec8f2ccd9bad37b3d72a`. The attested candidate
controller deployed that exact commit without a Python, Node, or OpenClaw
migration. The receipt records a compatible lockfile, pinned OpenClaw
`2026.7.2` at `ce67ffb7`, an encrypted backup at
`administrateme-20260809T133553Z.ambackup`, and an immutable rollback release.
Both launchd services run from the new release; observation remains active.

Live Triage Recent now opens the exact confirmed Task in Reminders, adds the
deterministic `view=anytime` URL state, presses the Anytime lens, and renders
the canonical Task detail. Both confirmed email-derived Tasks remain visible
as AdministrateMe-owned household work. Readiness settled at 61,175/61,175
source obligations with zero pending, missing, or failed. A controlled
runtime-only restart then proved the targeted adapter invariant: Calendar
source observations remained exactly 388 and Calendar outcomes remained
exactly 43,510. The final audit settled at 61,225/61,225 with zero pending,
missing, or failed and no Calendar counter growth.

The controlled restart separately exposed an older Google Tasks behavior: five
list and 45 item observations were re-appended once, then fully reconciled.
Provider/list/item identity and the two canonical Tasks remained correct. This
is recorded as the next adapter idempotency hardening item; it is not evidence
against the completed CO-009 canonical Task lifecycle or Calendar restart fix.

The member-reported failure was reproduced: two confirmed Triage Task
candidates had canonical `task.created` events and Tasks rows, but the
Reminders surface queried only provider Lists. The result was technically
materialized and operationally invisible. CO-009 treats that as a failed
product lifecycle.

The current candidate adds `deterministic-reminders-cockpit-v2`, a typed
read-time union that labels canonical AdministrateMe Tasks separately from
provider items and retains independent IDs, provenance, lifecycle events,
revisions, and write paths. Reminders adds local Task create/edit/complete/
defer/block controls plus Waiting and Completed lenses. Task deadlines and
available-from/defer activation are now separate; elapsed activations re-enter
the deterministic Today/What Now/Scoreboard read without rewriting event
truth. Triage now publishes `triage-enrichment-v3`; browser controls include date/time pickers, bounded
recurrence, energy, effort, work type, domain, first tiny step, notes,
authorized Party selection, and capability-filtered provider fields. The API
derives a closed target allowlist from the same contract. Task confirmation and
direct Task creation return an exact Reminders destination receipt; refreshed
Recent decisions retain the same destination link. Google Tasks renders a
date-only control and no Apple-only start/priority/alarm/recurrence/geofence
properties.

The browser saves visible ordinary-field edits before confirmation and requires
a refreshed final review after structural target/member/provider/account/list
changes. Triage's member directory is now derived from configured identities
and only enriched with Party labels; it cannot silently substitute the first
visible Party when the authenticated member has no Party row. The selector
fails closed when no configured member matches. Local confirmation waits for
the exact destination projection before returning success. Member-facing Task
status says **Open**, not the internal lifecycle token `inbox`.

Targeted proof passes with 46 tests: `tests/unit/test_projection_tasks.py`,
`tests/unit/test_reminders_cockpit.py`,
`tests/acceptance/test_triage_vertical_slice.py`, and the Gate 7 household
experience slice. The Task process proof verifies the same ID in Reminders,
Today, and Scoreboard and verifies no Calendar event is synthesized. The Gate 7
rendered harness now seeds and inspects a canonical Task, the Task enrichment
form, and the exact Google Tasks capability ceiling. All ten adult surfaces
pass desktop plus 390-pixel rendered use with named controls, no overflow,
landmarks, keyboard focus, contrast, and zero console/page/request errors.

Final local candidate evidence on 2026-08-09:

- strict acceptance: 39 passed under `--runxfail`;
- complete Python/pack suite: 1,694 passed; bridge distribution: 60 passed;
- Ruff and format clean across 508 files; mypy clean across 228 sources;
  Pyright reports zero errors and zero warnings;
- root and bridge dependency audits plus the production OpenClaw npm audit
  report no known vulnerability;
- performance passed every budget: 849.05 ms cold start, 16.45 ms append/
  projection p95, 14.39 ms temporary-view p95, 17.43 ms Today p95, 13.46 ms
  Task rebuild, 2.98 ms chat-fixture p95, 14.16 MiB peak traced memory, and
  2,381,176 encrypted bytes after 100 Task events;
- clean install, architecture negatives, tenant isolation, encrypted backup/
  restore, both Python package builds, shell validation, and diff hygiene
  passed;
- pinned OpenClaw contract, TypeScript check, 15 tests, build, package dry run,
  and production audit passed;
- rendered Gate 7 passed 222 assertions and retained 36 screenshots with zero
  failed result, console/page/request error, unnamed control, or horizontal
  overflow.

The aggregate suite was run with Python 3.14.6 and the side-by-side Node
24.19.0 candidate path. Its first attempt correctly stopped when the shell
selected live Node 22; the passing rerun explicitly selected Node 24 without
changing the live runtime, as required by ADR-0006. PRs #302 and #303, guarded
deployment, exact receipt navigation, both live canonical Tasks, full source
settlement, and the controlled restart proof complete CO-009.

## 2026-08-08 CO-008 candidate-controller repair and live deployment

PR #299 passed Constitution sync and the Apple-silicon rehearsal and merged as
`d5dd805`. Its guarded activation supplied the first direct live proof that the
Gmail/Triage repair works: the v9 runtime reported 49 eligible-candidate
assessments, seven explicit no-candidate outcomes, 39 privacy quarantines, zero
failed assessments, and an advancing ordered replay prefix. At the sampled
instant 56,634 of 56,919 content-free source obligations were accounted, with
25 in flight and 260 not yet reached. Observation mode remained active; no
candidate was confirmed and no provider write occurred.

The updater still rolled the candidate back and restored `c484f0a` healthy.
The failure was not the candidate's 30-minute settlement contract: the command
entered through the active release's `update.sh`, whose ordinary branch invoked
the active release's old `bootstrap.upgrade` and its old two-minute deadline.
Candidate changes to upgrade policy could therefore never govern the update
that introduced them.

PR #300 made candidate-policy provenance executable. Every update now
requires a clean reviewed Git checkout, creates a commit-versioned controller,
synchronizes dependencies outside the active application venv, rechecks HEAD
and cleanliness, attests the loaded module path, and passes the exact commit to
an independent manager check. Same-baseline updates run candidate policy on the
active Python/Node boundary; only the explicit, separately rehearsed migration
path may use candidate runtimes. Compatibility, encrypted backup, final full
Doctor and synthetic proof, immutable prior release, exact OpenClaw selection,
and automatic rollback remain mandatory. The exact local release matrix passed
with 38 strict acceptance tests, 1,687 root/pack tests, 60 bridge tests, and 15
pinned OpenClaw plugin tests, plus Ruff/format, mypy, Pyright, invariants,
performance, dependency audits, root/bridge packages, clean install, tenant
isolation, encrypted backup/restore, plugin build/package/audit, Bash syntax,
and diff hygiene under Python 3.14 and Node 24. Constitution sync passed in five
seconds and the Apple-silicon credential-free rehearsal passed in 10 minutes
16 seconds. PR #300 merged as `dc99208`.

The merged updater then built an attested `dc99208` controller on the existing
3.14/24 runtime, prepared the immutable release, created encrypted backup
`administrateme-20260809T044355Z.ambackup`, atomically selected both services,
and completed the reviewed settlement window, full Doctor, authenticated
OpenClaw synthetic proof, and rollback-retention check. Its receipt reported
`upgrade_controller_attested: true`, no runtime migration, no OpenClaw
migration, and `status: upgraded`.

During activation, content-free accounting reached 58,344 of 58,344 source
obligations with zero pending, missing, or failed; v9 reached zero lag with no
failure or poison. The settled classification was 23 source facts with eligible
candidates, 293 explicitly assessed with none, and 39 privacy quarantines.
New adapter facts may subsequently appear as `pending` only inside the existing
five-minute grace window; overdue work still becomes missing and degrades
health. Both LaunchAgents and the active pointer resolve to `dc99208`, and the
runtime is ready in observation mode.

Authenticated rendered Triage proof shows Jimmy's private member identity,
source accounting, 53 visible human-gated rows (20 Ready and 33 Needs details),
and the complete eleven-kind Add-for-review dialog. The dialog states that an
unclear thought remains Needs interpretation rather than being discarded. No
row was confirmed, enriched, dismissed, materialized, or sent. Historical
unreviewed marketing/CI noise remains visibly auditable for member feedback;
it was not silently deleted or promoted to truth. CO-008 is deployed. The next
step is the observation soak and member decisions, not another source-ingest
repair.

## 2026-08-08 CO-008 source-throughput repair

PR #298 passed Constitution sync and the Apple-silicon rehearsal, merged as
`5c85b05`, and entered guarded activation after encrypted backup
`administrateme-20260809T033417Z.ambackup`. Doctor again rejected incomplete
source settlement and the updater restored `c484f0a`; the active runtime,
Gateway, launch definitions, and loopback health are live. Observation mode
remained active and no member decision or provider write was performed.

Retained content-free evidence exposed two distinct executable defects rather
than a longer replay window. Of 355 immutable Gmail facts, 316 are correctly
private-member scoped and 39 legacy facts are household-shared. Only 18 v8
assessments were retained: 16 explicit scope quarantines and two explicit
empty-text outcomes. The next shared-scope source still entered the language
seam because scope refusal occurred after NLP. The bus also delivered this
language-bound consumer serially, while the service probe treated one slow
readiness document as proof that replay was not advancing.

The reviewed repair moves every scope/member refusal ahead of the model,
introduces opt-in bounded delivery concurrency with ordered-prefix durable
checkpointing, and raises the classification skill's bounded timeout to match
the observed pinned-Gateway completion envelope. `noise_filtering` v9 uses
eight-way delivery only for its idempotent source assessor. A failed callback
never advances past the failed source; later completed callbacks are safely
redelivered. The updater tolerates a slow readiness document only while the
cheap runtime liveness endpoint remains true and retains the same settlement
deadline, terminal equality, full Doctor, synthetic proof, and rollback.
Focused failure-path tests pass. The exact release matrix passed with 37 strict
acceptance tests, 1,681 root/pack tests, 60 bridge tests, and 15 pinned OpenClaw
plugin tests, plus lint, types, invariants, performance, dependencies, builds,
clean install, tenant isolation, backup/restore, package/audit, shell, and diff
gates under Python 3.14 and Node 24. PR #299 passed both hosted gates and merged
as `d5dd805`; its activation outcome and remaining controller repair are
recorded above.

## 2026-08-08 CO-008 replay-settlement repair

PR #297 passed Constitution sync and the 14-minute Apple-silicon rehearsal,
merged as `4c81ac4`, and entered guarded activation after encrypted backup
`administrateme-20260809T030105Z.ambackup`. The candidate runtime and Gateway
started, but the two-minute transient window ended with
`source_assurance_terminal_dispositions` incomplete. Automatic rollback
restored `c484f0a`; its runtime, Gateway, launch definitions, and loopback
health are live.

The retained content-free accounting evidence is 54,125 obligations, 53,839
accounted, 286 missing historical message assessments, zero pending, and zero
failed. The v8 consumer must traverse the historical encrypted stream before
it can append every terminal message assessment. Rollback correctly restores
the active v7 operational cursor, so a later v8 activation restarts rather than
secretly preserving candidate cursor state.

The repair centralizes Doctor's content-free terminal predicate and changes
post-switch behavior from repeated heavyweight Doctor execution to one initial
Doctor, a two-second lightweight readiness poll bounded at 30 minutes, and one
final full Doctor plus synthetic proof after settlement. Structural failures
still return immediately; a missed deadline still rolls back. Focused
bootstrap/runtime acceptance is green. The exact release matrix passes with 37
strict acceptance tests, 1,668 root/pack tests, 60 bridge tests, and 15 pinned
OpenClaw plugin tests, plus lint, types, invariants, performance, dependencies,
builds, clean install, tenant isolation, backup/restore, package/audit, shell,
and diff gates under Python 3.14 and Node 24. Review, merge, and guarded
activation remain pending.

## 2026-08-08 CO-008 guarded activation rollback and repair

PR #296 passed GitHub Constitution sync and Apple-silicon rehearsal and merged
as `1d2b64a`. Guarded activation created encrypted backup
`administrateme-20260809T021351Z.ambackup`, built the immutable release, and
started both candidate services. Post-switch Doctor then rejected
`launchd_and_process_state`; the updater automatically restored the prior
`c484f0a` pointer and launch agents. The restored runtime and exact OpenClaw
health endpoint are live. The append-only replay/audit facts created during the
attempt were retained; no source fact or member decision was removed.

Investigation proved that `/api/system/ready` synchronously rescanned the live
encrypted log whenever replay appended an event or advanced a checkpoint. That
unbounded audit exceeded the service probe's five-second timeout despite a live
runtime. It also proved that the v8 invocation-key change did not bypass the
skill wrapper's separately cached causal terminal failures, and that bootstrap
Doctor lacked the named source-disposition check claimed by acceptance prose.

The repair candidate makes later source audits stale-while-revalidate with one
background task, visible freshness/error state, and cancellation before event
log shutdown. The mandatory initial audit still fails startup closed. It adds
an explicit one-generation prior-failure retry that preserves successful
exact-fingerprint reuse and prevents repeat calls after the reviewed retry
fails. Doctor now requires fresh, content-free accounting with zero pending,
missing, or failed obligations; this named check is a bounded post-switch
transient while replay settles. The exact release matrix passes with 37 strict
acceptance tests, 1,666 root/pack tests, 60 bridge tests, and 15 pinned OpenClaw
plugin tests, plus lint, types, invariants, performance, dependency, build,
clean-install, isolation, backup/restore, package, shell, and diff gates under
Python 3.14 and Node 24. PR #297 subsequently merged; its activation outcome is
recorded above.

## 2026-08-08 CO-008 source-assurance candidate

The systemic repair requested after the live Gmail/Triage incident is
implemented in Phase A. Every declared adapter and bridge source family now
has a validated disposition contract. Candidate-bearing message and Notes
pipelines append a terminal v2 assessment for eligible, explicitly empty,
quarantined, and failed outcomes. Runtime source assurance compares source
facts with assessments, named projection checkpoints, or terminal action
results and degrades Triage/member health when processed work is absent. The
adult shared-shell Add-for-review picker now exposes all eleven supported
proposal kinds.

Message replay generation v8 replaces the broad generic action-verb fallback
with a bounded high-signal policy plus exact-quote schema-bound language
interpretation. It appends supersession only for untouched legacy proposals
whose semantic admission depended on the retired rule; any member enrichment,
decision, materialization, or dismissal is preserved. Notes indexing v3
removes confidence as authority, enforces privacy and exact-source grounding,
and uses source-derived timestamps so replay and retry remain idempotent.

Credential-free candidate evidence is 37 strict process acceptance tests,
1,661 root/pack tests, 60 bridge tests, and 15 pinned OpenClaw plugin tests.
Ruff lint/format, mypy, Pyright, invariant and architecture-negative checks,
performance budgets, dependency audits, root/bridge build and lock checks,
clean install, tenant isolation, backup/restore, shell validation, plugin
contract/build/package, and production npm audit pass under Python 3.14 and
Node 24. A transient npm-registry timeout on the first aggregate run was
retested successfully and did not change code; the exact final aggregate
rerun passed. Independent review, merge, encrypted live backup, immutable
activation, v8/v3 replay, zero-lag Doctor, source-account reconciliation, and
portal verification remain mandatory before this candidate may be called
deployed.

## 2026-08-08 Triage v7 live activation

PR #294 passed the constitutional cascade and clean macOS rehearsal, merged as
`c484f0a`, and was activated with encrypted rollback backup
`administrateme-20260809T002636Z.ambackup`. The active release receipt, launchd
definitions, loopback services, exact OpenClaw source, authenticated
`llm-task` execution, fourteen projections, six required pipeline subscribers,
cron reconciliation, synthetic round trip, and observation mode all pass full
Doctor.

The live v7 consumer reset its healthy terminal cursor as declared, began with
342 historical messages pending, and reached zero lag with zero consecutive
failures and no poison event. Across 354 immutable message facts, the 4.0.3
language seam recorded 83 successful calls and four fail-closed unreachable
terminals. Exact duplicates reused successful results. Replay added 26 new
grounded proposals from 19 source facts: nine tasks, twelve reminders, three
calendar events, and two party identifiers. The resulting member Triage
projection contains 86 pending records across 50 source facts: 65 tasks,
13 reminders, three calendar events, two recurrences, two party identifiers,
and one relationship fact. The refreshed private portal reports 19 needing
details and 67 ready, with no console errors. No proposal was confirmed,
dismissed, materialized, dispatched, or written to a provider.

The live UI audit also found a distinct signal-quality defect that this replay
repair does not conceal: 56 pre-v7 fallback proposals were admitted by the
broad `explicit_action_verb_v1` rule, including message boilerplate and
marketing calls to action. They remain pending because an activation must not
silently impersonate the member's human gate. The next reviewed change must
supersede that broad inbound eligibility rule, deterministically quarantine or
rank legacy low-specificity proposals, and preserve the 26 grounded v7 results.

## 2026-08-08 explicit pipeline replay contract and Triage v7

PR #293 merged and deployed successfully as `ec36783`; the exact OpenClaw
successor, authenticated Doctor probe, Gateway-first startup, runtime, and
observation mode are healthy. The post-deployment aggregate audit then proved
that `noise_filtering` v6 reported zero lag without re-reading healthy historical
messages. The durable bus previously used a version change only to recover a
poisoned subscriber from its last good checkpoint. A healthy subscriber merely
stored the new version and retained its terminal checkpoint, so documentation
calling v5/v6 a full replay boundary was stronger than executable behavior.

This repair **HARDENS** the durable bus with an explicit opt-in full-replay
contract. Only a reviewed pipeline manifest may request `replay.on_version_change:
full`; all other consumers retain the existing checkpoint behavior. The bus
then resets only that operational cursor and health counters while preserving
the encrypted append-only source log and every prior output/audit fact.
`noise_filtering` 7.0.0 and `classify_message_nature` 4.0.3 are the first live
consumer of the contract. A privacy-scoped SHA-256 identity reuses one recorded
model result across byte-equivalent duplicate message observations, while
causation remains attached to a real source event. Candidate generation v7
deduplicates against baseline/v4/v5/v6 proposal kind/key history. The result is
still proposal-only under observation and the Triage human gate.

## 2026-08-08 Gateway-first startup and Triage v6 replay repair

PR #292 merged as `c96dbf8`, but two guarded activation attempts correctly
rolled back before acceptance. The first candidate-controller invocation omitted
the instance-local executable directory and Doctor could not resolve the exact
OpenClaw selector. The corrected invocation reached the new source, but the
existing service manager launched the AdministrateMe runtime before OpenClaw.
The runtime immediately began the reviewed Triage replay while its only language
seam was unavailable, durably auditing transport failures and advancing through
defensive deterministic results. The post-switch process/readiness check rejected
the release; both attempts restored release `b23c6b1`, exact OpenClaw source
`944cf08f`, healthy observation mode, and produced encrypted backups.

This follow-up **HARDENS** the central composition without changing model
authority. Service activation now starts the exact pinned OpenClaw Gateway,
proves its credential-free loopback `/health` response within a bounded window,
and only then starts the AdministrateMe runtime and its reactive replay. Runtime
liveness and full readiness retain separate bounded checks. The reviewed replay
boundary advances to `classify_message_nature` 4.0.2 and `noise_filtering` 6.0.0,
so the immutable message facts can be interpreted after the failed-start audit
facts while cross-generation candidate deduplication still appends only absent
target-kind/candidate-key pairs. Observation mode and human confirmation remain
mandatory; this repair performs no provider write.

## 2026-08-08 pinned llm-task execution and Triage v5 repair

The authenticated Phase-B probe reached the enabled, allowed, runtime-registered
`llm-task` tool but failed before provider execution. The former exact
OpenClaw `2026.7.2` snapshot launched the bundled tool through a retired
file-backed embedded-session path without an agent identity; its own SQLite
session runtime rejected every call. Registration evidence alone therefore
produced a false healthy Doctor result.

This repair **PRESERVES** one exact-pinned OpenClaw model router, member privacy,
typed exact-source proposals, append-only history, the Triage human gate, and
observation mode. It **SUPERSEDES** only the defective same-version OpenClaw
snapshot with exact official commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`, reviewed because its upstream
`llm-task` uses isolated, tool-free completion instead of the incompatible
session path. The archive SHA-256, package version, package manager, plugin API,
Node engine, bundled tool tests, and complete source build are verified. The
guarded updater accepts only the declared predecessor-to-successor transition,
installs the reviewed source before cutover, and restores the previous exact
source as part of rollback.

AdministrateMe now sends a canonical `agent:main:adminme:...` Gateway session
that remains distinct per authenticated member. Skill result/failure identities
include the skill version, and terminal failures carry correlation plus
causation, allowing an explicitly versioned repair to retry without rewriting
prior audit facts. `classify_message_nature` 4.0.1 and `noise_filtering` 5.0.0
form the reviewed replay boundary. Classification and semantic facts select the
first byte-identical or unused baseline/v4/v5 generation; candidate replay
deduplicates across all earlier extraction generations and appends only absent
target-kind/candidate-key pairs under v5.

Doctor's full proof now makes one bounded, authenticated, household-free JSON
execution through the real Gateway, configured model route, bundled tool, and
schema validator. A registered tool that returns 4xx/5xx, malformed JSON, or a
schema mismatch is unhealthy and blocks release cutover. No credential or model
response body enters diagnostic evidence.

## 2026-08-08 live Triage extraction incident and runtime follow-up

The live Gmail adapter had durably accepted 82 private message facts, but
`pipeline:pipeline:noise_filtering` failed before its first delivery because
the skill runner tried to rediscover `ADMINME_INSTANCE_DIR` from process state.
After five attempts the bus correctly poisoned the subscriber, retained a null
checkpoint, and accumulated 347 relevant events. The failure then survived
service and release restarts by design. Doctor incorrectly reported healthy
because it inspected fourteen projection consumers but no required reactive
pipeline consumers. The identity subscriber was independently poisoned by an
endpoint-wide idempotency key whose payload changed on the second observation.

This repair **PRESERVES** source facts, member privacy, typed domain
distinctions, observation, the Triage human gate, and the pinned OpenClaw
`llm-task` seam. It **HARDENS** the production composition with an explicit
skill-runtime dependency bundle, recorded-result reuse on retry, replay-stable
derived payloads, per-source endpoint observation keys, consumer-version
checkpoints, and automatic replay from the last good checkpoint only after a
reviewed consumer version changes. Doctor now fails when any required reactive
subscriber is missing, degraded, failed, or poisoned.

It also **MODERNIZES** message interpretation without transferring decision
authority to a model. `classify_message_nature` v4 may report tasks, calendar
events, reminders, recurrences, lists, list items, and party identifiers, but
fixed code rejects every candidate without an exact contiguous subject/body
quote, filters fields through the target-kind allowlist, enforces the private
member scope, deduplicates overlapping deterministic evidence, and emits only
a review proposal. Confidence is presentation evidence, never an eligibility
threshold. Bills, explicit decisions/replies, due actions, repeating charges,
and exact first-person promises in outbound mail can therefore reach Triage
while remaining unofficial until member confirmation. Gmail now preserves the
provider `SENT` direction as `messaging.sent`; historical v3 facts retain the
same label for direction-aware replay, and inbound promises are never assigned
to the member. `noise_filtering` v3 and `identity_resolution` v2 were the first
reviewed replay boundaries. PR #289 merged and deployed as `fc258ec`; replay
completed at zero lag with 51 exact-source, human-gated proposals from 31
messages and no confirmation, canonical creation, or external-write request.

The live replay also proved that the deterministic fallback was masking a
second composition defect: 105 `classify_message_nature` calls returned 401
because the explicit skill runtime did not receive the Keychain-backed Gateway
token. Once supplied in an isolated synthetic probe, the authenticated Gateway
truthfully returned `llm-task` unavailable. The pinned build ships that tool as
a bundled optional plugin which must be explicitly enabled and added to
`tools.alsoAllow`; the prior Doctor checked only AdministrateMe plugin discovery.

This follow-up injects the secret with `repr=False`, reconciles only the pinned
bundled `llm-task` configuration through OpenClaw's own validated writer, and
requires Doctor evidence for enabled, allowed, runtime-registered tool state.
`noise_filtering` v4 is the reviewed language-recovery boundary. It appends
changed classification/semantic facts under a new idempotency generation and
adds only candidate kind/key pairs absent from the earlier deterministic
fallback, so it neither rewrites append-only evidence nor duplicates the 51
pending proposals.
Credential-free release evidence is recorded in
`evidence/triage-extraction-repair.md`: 36 strict acceptance tests, 1,632 root
and pack tests, 60 bridge tests, 15 pinned plugin tests, both type checkers,
lint across 225 production sources, dependency audits, performance, architecture negatives, clean install,
tenant isolation, backup/restore, build, and package all pass under Python
3.14.6 and the reviewed Node 24.19.0 path.

## 2026-08-08 CO-007 trustworthy capture, Triage, and SSOT candidate

CO-007 is approved and implemented on the candidate branch in Phase A only. It
**PRESERVES** the encrypted append-only event log, distinct domain models,
deterministic projections, Triage human gate, provider authority, observation,
member privacy, and the one pinned OpenClaw routing seam. It **MODERNIZES** the
shared member shell with exact-text Add for review, deterministic multi-intent
extraction, explicit interpretation/details/ready/recent lanes, source and
pipeline freshness, durable Gmail accounting, chronological confirmation and
materialization receipts, signed independent OpenClaw approval, and linked
terminal provider outcomes. It **SUPERSEDES** browser approval booleans,
direct generic capture, confidence-based disappearance, whole-batch in-memory
Gmail cursoring, config-as-health, and the admin-only chat parameter that made
least-privileged `chat.send` fail.

The pinned OpenClaw source at commit `944cf08` proves that
`suppressCommandInterpretation` is an `operator.admin`-only chat field. The
member relay now sends only `sessionKey`, `message`, `deliver=false`, and an
idempotency key under operator read/write scope. Provider effects require a
content-bound, signed, 120-second, single-use allow-once receipt. The same
binding may resume an interrupted idempotent command, but a changed tool,
content, member, receipt id, signature, or expired receipt fails before an
adapter.

Credential-free browser verification captured natural dictation as four
separate calendar, reminder, recurrence, and commitment proposals, placed them
in the correct Triage lanes, and confirmed a local recurrence only after the
human gate. No household provider was written, no live Gmail mutation was
performed, observation remained active, and no family-Mac deployment occurred.
Exact candidate evidence and remaining activation boundaries are in
`evidence/co-007-capture-triage-ssot.md`.

Candidate validation passed 1,616/1,616 root/pack Python tests, 60/60 bridge
tests, and 34/34 strict acceptance tests, Ruff over 224 production source files, mypy,
the invariant and architecture-negative scripts, and the exact-pinned Node
24.19 plugin contract/build/dry-run package with 15/15 tests. Desktop and
390×844 in-app browser exercises produced four
natural-dictation proposals, showed the expected Triage lanes and immutable
local outcome, had zero browser-console errors, and had no horizontal overflow.
The production npm dependency audit is clean. The full dev-tree audit retains
one transitive `nanoid@3.3.16` advisory under Vitest/Vite/PostCSS; it is not
runtime reachable and is not silently rewritten as part of CO-007.

### Phase-B legacy projection repair

PR #287 was independently reviewed and merged on 2026-08-08. Its first guarded
family-Mac activation correctly failed closed and restored the prior release:
the deployed Triage v2 database did not yet have `parent_intent_id`, while the
candidate `schema.sql` tried to create that column's index before
`TriageProjection.migrate_schema()` could rebuild the table. No provider write
was attempted and the encrypted event log remained authoritative.

The deployment repair **HARDENS** the additive migration without changing
Triage semantics: the dependent index is now created only by the migration
hook after the column exists. A production-composition acceptance test starts
the real runtime over the exact pre-PR #287 encrypted table shape, proves the
legacy candidate survives, and verifies the new lineage/approval columns and
index. The guarded updater, backup, full Doctor, and observation-mode checks
remain mandatory for the retry.

## 2026-08-07 CO-006 Finance workbook and cash-flow activation

CO-006 is implemented in the live family runtime under ADR-0007. It **PRESERVES** the
encrypted append-only log, private provider authority, member isolation,
deterministic projections, workbook round-trip, observation, and exact
OpenClaw boundary. It **MODERNIZES** the existing money projection into one
cash-flow/scenario engine shared by the Finance portal and Finance workbook. It
**SUPERSEDES** the thin three-sheet workbook, summary-only Plaid receipts,
hidden spreadsheet constants, brittle zero-result formulas, and static Finance
card.

The canonical workbook now has twelve sheets with protected provider/derived
facts and editable assumptions, budgets, scenarios, goals, classifications,
and manual transactions. Validation fails closed before planning changes.
Plaid emits detailed private account/transaction/removal observations and a
terminal receipt, and its durable member/item cursor advances only after all
events are accepted. The portal exposes Overview, Cash flow, Spending,
Scenarios, Net worth, and Data health from
`deterministic-finance-cockpit-v1`; fixed code, not a model, chooses the one
next review.

The supplied private household planner was repaired and visually/formula
validated as an ignored local output. No household financial values entered
tracked code, docs, fixtures, or tests. Real Plaid Link/consent, explicit
cross-member account sharing, and provider propagation remain post-merge live
activation evidence. No bank write seam was added.

Final candidate validation passed the complete release command under Python
3.14.6 and Node 24.19.0: 32/32 strict acceptance tests, 1,580/1,580 root,
code, and pack tests, 60/60 bridge tests, the 14/14 pinned OpenClaw plugin
tests, both Python type checkers, lint/format/shell checks, dependency audits,
performance budgets, clean install, tenant isolation, encrypted backup/restore,
architecture negatives, and package builds. The Chrome-backed Gate 7 harness
passed 136/136 checks, including all six Finance views, URL restoration, and
deterministic scenario changes; direct desktop/mobile browser simulation found
no overflow, console, page, or request failures.

The reviewed runtime migration and CO-006 core are live at merge commit
`33c5622d4953b01263914ae77a7de0cdac9aa65e`. The rollback-safe updater created
an encrypted pre-switch backup, retained the previous release, and passed full
post-switch Doctor: exact Python 3.14.6, Node 24.19.0, OpenClaw 2026.7.2,
14/14 healthy projection checkpoints, 5/5 managed OpenClaw jobs, loopback-only
services, and observation mode.

The private reviewed planner is now represented in the canonical twelve-sheet
Finance workbook and encrypted event/projection state without committing any
household value. Live evidence contains 38 assumptions, 14 budgets, five
scenarios, five goals, and 791 historical/manual rows. Of those rows, 750 are
included in Finance arithmetic and 41 transfer/excluded rows remain preserved
for provenance. The earlier generic write budget refused 491 rows; PR 283
isolated a bounded higher budget to `daemon:xlsx_reverse` only, and the clean
retry added no further denial. The regenerated workbook passed its structural,
formula-error, and visual checks, and all six URL-backed Finance views passed
the live Tailscale browser exercise.

The final privacy audit found that atomic workbook and sidecar writers inherited
broader mode bits than the constitutional privacy contract allows. The live
files were immediately restricted, and PR 285 hardened the boundary rather than
relying on a parent directory: workbooks and sidecars are created as
`0600`, workbook-specific sidecar directories are `0700`, and the temporary
file has its private mode before any content is written. A forced live Finance
regeneration under the active release retained those modes for the workbook and
all twelve sidecars while preserving the same validated row counts and cockpit.

Plaid remains disabled and unconfigured, so the live cockpit intentionally has
no provider accounts. Live Link/consent, per-account sharing, and provider
propagation evidence remain the next explicit activation boundary. Observation
mode remains active and no bank write seam exists.

## 2026-08-07 runtime-Keychain reconciliation

The household principal subsequently authorized the reviewed runtime migration.
The first live attempt failed closed before backup, service stop, or pointer
mutation because Python 3.14 could not cross the login-Keychain application
partition. The login Keychain remained unlocked and all configured items were
present; resetting it would have stranded encryption and OAuth material.

ADR-0006 now **PRESERVES** the original interpreter-bound items for rollback and
**MODERNIZES** Python-boundary secret continuity with candidate-bound copies and
an atomic, non-secret logical-reference alias map. Values travel only in memory
and through an anonymous parent/child pipe. The new runtime validates alias
scope and cycles, and migration still fails before active-service mutation if
any source, copy, receipt, map, or bounded candidate probe is invalid. This is
an operational repair, not a new credential store or a weakening of Keychain
policy.

The first reviewed alias-copy attempt also exposed a launcher-path defect before
service mutation: resolving the controller venv's `python` symlink selected the
Homebrew base interpreter without the controller dependencies. The coordinator
now normalizes the absolute launcher path without dereferencing it, and the
regression test requires the venv path to reach the child process.

After the Keychain bridge passed, the first candidate cutover exposed a second
fail-closed live-only boundary: the cold schema/projection start completed just
after the fixed 90-second liveness deadline, so the upgrade restored the old
release. Service launch now permits a bounded five-minute cold-start window,
still probes only `/api/system/live`, and still requires full post-switch Doctor
before retaining the candidate. Unit evidence covers both retry success and
deadline failure.

The next retry exposed rollback idempotency before backup or service mutation:
the old release ignored the retained candidate alias map as designed, but the
coordinator initially followed it and asked Python 3.11 to read Python 3.14
items. Retry source selection now compares the alias runtime tag with the
active release's reviewed version receipt. A mismatch uses the untouched
logical originals; a matching active runtime continues through its effective
aliases. The regression test preserves the stale map while proving the
rolled-back interpreter reads the original service name.

The reviewed retry then encountered one more pre-mutation compatibility edge
on the actual installed release: its D10-era `versions.json` records
`python@3.11` in `python.formula` but predates `python.preferred`. Runtime-tag
selection now accepts either the current exact preferred version or that
strict legacy Homebrew formula shape, while rejecting ambiguous receipts.
Focused evidence covers both formats and the installed receipt resolves to
`py311`. The failed attempt did not create a backup, stop a service, replace a
pointer, publish a new alias map, or expose a secret.

The next guarded cutover proved that an in-process read-back was not sufficient
macOS ACL evidence. The candidate child created and read its five copies, but a
fresh launchd Python process blocked in `SecItemCopyMatching`; the five-minute
liveness guard restored the old release. A process sample and ACL-only Keychain
inspection showed that Homebrew Python is ad-hoc signed and that the creating
process's default access did not carry into the daemon lifecycle.

ADR-0006 now **PRESERVES** Python-keyring access only for untouched rollback
originals and **MODERNIZES** runtime-bound aliases through Apple's signed
`/usr/bin/security` client. Values enter `security` only through anonymous
standard-input pipes and return only through captured standard output; no value
enters argv, environment, files, or logs. Candidate creation and verification
use separate fresh Apple-signed processes, the production store uses the same
path for aliased get/put/delete, and adapter credential resolution follows the
validated instance alias map. A disposable cross-process proof passed under
the actual candidate Python and the exact probe item was deleted afterward.

The first signed-client retry then stopped before copy or service mutation
because the 3.11 coordinator imported the new helper at module load and resolved
`adminme` from the old installed environment. Helper imports are now delayed to
the candidate child and to the matching-new-runtime alias branch. The rolled
back 3.11 coordinator therefore remains self-contained, while the processes
that use the helper run only in an environment that contains it.

The following retry exposed a final Apple-client representation boundary before
service mutation: prompt-mode `security add-generic-password -w` silently
retained only the first 128 characters of the 624-character OAuth record. Four
shorter records verified, which isolated the failure from Keychain access and
ACLs. Runtime aliases now use a versioned, checksummed envelope whose Base64
fragments and root manifest each remain below the prompt limit. A random
generation is written fragment-first and the manifest is committed last; reads
reassemble, decode, and verify before returning a value, while failed writes
remove only their uncommitted generation. Exact disposable 624-character
create/read/delete evidence passed through fresh Apple-signed processes. Values
still enter neither argv, environment, files, logs, nor diagnostics, and the
five original rollback items remain untouched. The exact coordinator-child
proof also caught Python resolving `bootstrap/secrets.py` in place of the
standard-library module; generation now uses `os.urandom` directly, and a
script-path-shadow regression test preserves child importability.

After all five envelopes verified and the alias map published, the candidate
preflight exposed a generated-config classification defect before backup or
service mutation. `configured_keychain_references()` scanned the alias map as
if it were operator configuration, so Python 3.14 was asked to open both five
logical references through the production alias seam and five physical names
through the rollback-only keyring seam. The probe now excludes the generated
alias map and validates only configured logical references; alias resolution
then proves the candidate physical envelopes through the same production store
used after cutover. Regression evidence retains a physical alias in the map
while requiring it never to become an independent probe target.

The next guarded cutover reached Python 3.14 liveness and the full post-switch
Doctor, proving that credential continuity, schema startup, and rollback-safe
service launch were working. Doctor rejected `runtime_and_package_versions`
because its PATH and both generated LaunchAgents placed the global Homebrew bin
before the reviewed Node formula. The host therefore reported Node 22.23.2/npm
10.9.8 even though Node 24.19.0/npm 11.17.0 were installed side by side. The
updater restored the old release automatically. Service and Doctor PATH now
share one release-manifest-derived builder and place the reviewed Node formula
before the global Homebrew bin; ordering regressions cover both LaunchAgents and
the candidate-interpreter Doctor process.

## 2026-08-07 CO-005 constitutional audit candidate

CO-005 is implemented in the candidate under ADR-0006/D30 and
`CHANGE-ORDER-005-CONSTITUTIONAL-AUDIT.md`. It **PRESERVES** the event-sourced
topology, exact OpenClaw pin, SQLCipher boundary, member isolation, provider
authority, Triage human gate, observation, backup, and rollback. It
**MODERNIZES** production governance, cross-surface freshness/copy/identity
truth, tenant scanning, and the future runtime baseline. It **SUPERSEDES**
default-allow unknown governance, the generic provider mutation endpoint,
Notes provider writes, event-start freshness, unresolved-endpoint-as-review,
and D10 as the future architecture.

The audit first proved the active immutable release healthy and then passed
the unchanged candidate baseline release suite. Direct production inspection
found that the runtime composed `GuardedWrite` with empty policy even though
fixture tests proved a stronger configuration. The candidate now loads
reviewed tenant-neutral policy (or an instance override), denies unknown
actions, preserves strict allowlist -> governance -> rate-limit order, and
routes Settings changes through it. The unaudited generic external-sync route
is removed; Notes is rejected by the external-write schema and remains
ingest/query/Triage-only.

Calendar/Today freshness now uses provider/source update watermarks rather
than future event starts. Reminders conservatively mark a missing-account
legacy row as a possible copy only when one concrete provider account is
possible, never merges/deletes source rows, and isolates ambiguous accounts.
People reports unresolved senders without linking raw endpoint observations to
an empty Triage queue. Household-specific built-in/test fixture tokens were
removed and the tenant canary now includes bridge, packs, and tests.

The candidate targets Python 3.14.6, Node 24.19.0, Homebrew 6, Poetry 2.4.1,
and npm 11. Node 24 was installed keg-only beside the linked Node 22; Python
3.14 was already present. No global link, active-release pointer, LaunchAgent,
service, observation state, provider connection, or production venv changed.
The candidate upgrade path builds a new release-local environment and refuses
a Python/Node baseline change before service replacement unless the separate
runtime-migration flag is explicitly supplied after the complete rehearsal.

Final candidate validation passed 31 acceptance tests, 1,558 root/code/pack
tests, 60 standalone bridge tests, all Python and TypeScript static/security/
package gates, clean install, tenant isolation, encrypted backup/restore,
architecture negatives, performance budgets, and shell validation. The Gate 7
Chrome-backed production-surface harness also passed. The immutable historical
console-reference digest is now line-ending portable; its Edge-only interaction
run remains environment-skipped because Edge is not installed on this Mac.

A final stable-wrapper audit caught and fixed one cutover-safety defect: the
candidate wrapper briefly selected its Node 24 path before resolving the older
active release. Wrappers now resolve `current-release.json` first and load the
Node formula from that release's own manifest. Post-fix live Doctor is healthy
on Python 3.11.15 / Node 22.23.2 with fourteen healthy projections, five
reconciled jobs, exact plugin discovery, and observation still active.

The audit does not relabel unfinished required product scope as optional:
Finance, Scoreboard/reward UX, and privileged-read audit depth require focused
follow-up. No member bridge is active and Notes remains unconfigured on the
live instance. Optional Outlook/BlueBubbles/Twilio and other provider edges
remain truthful disabled capabilities.

## 2026-08-07 CO-004 Settings control plane live observation release

The principal approved CO-004 on 2026-08-07. ADR-0005 and
`CHANGE-ORDER-004-SETTINGS-IDENTITY-AND-CONNECTION-CONTROL.md` now govern
Settings. The change **PRESERVES** Tailscale trusted ingress,
`authMember`/`viewMember`, provider authority, secret references, the encrypted
append-only audit log, observation, exactly fourteen projections, one central
Python runtime, the pinned OpenClaw boundary, and separate per-member bridges.
It **MODERNIZES** Settings into a deterministic member control plane. It
**SUPERSEDES** the opaque initial avatar, configuration-as-health, overloaded
status, truncated capability dump, static workbook/chat rows, model-decided
preferences, broad operator controls, and unreviewed provider buttons.

The shell now identifies the authenticated member, role, trusted Tailscale
ingress, read member, and action member. View-as is conspicuous and read-only.
Seven URL-backed areas cover Overview, My account, CoS behavior, Connections,
Bridges & devices, Household & privacy, and Safety & system. Adult Settings
offers only adult support profiles. Bounded member preferences feed the
existing deterministic member-experience composer; they cannot change
authority, privacy, or model routing.

Each connection keeps setup, runtime, reviewed capability, and safety as
separate evidence. A stored credential reference is reported only as stored;
connected requires a live adapter handshake. The supervisor now returns a
secret-free per-instance inventory and atomically reconciles a completely
constructed candidate set for pause/resume. The secret-free revisioned
`settings-control.json` journal stores only operational desired state. All
journal mutations share one lock, reject stale revisions, and emit typed
private receipts. If a receipt fails, profile, preference, bridge-request, or
adapter desired/runtime state rolls back so an unaudited terminal change
cannot remain active.

Add Bridge records a scoped, resumable request only. Adult private-device
enrollment remains self-owned; a principal may request a child bridge but not
another adult's bridge. Target consent, Tailscale identity, OS permissions,
reviewed capability acceptance, owner-scoped ingest proof, and bootstrap
material revocation remain the existing secure enrollment ceremony. OAuth and
OS consent for accounts not already enrolled, and optional adapter families
without reviewed implementations, remain truthful provider-specific activation
work rather than simulated Connect controls.

AC-31 passed through the production composition root. The aggregate release
candidate passed 31/31 strict acceptance tests, 1,553/1,553 Python
code-and-pack tests, 60/60 standalone bridge tests, Ruff lint/format, mypy,
Pyright, dependency audits, performance budgets, Python and bridge builds,
architecture negatives, clean install, tenant isolation, authenticated
backup/restore, shell validation, exact OpenClaw 2026.7.2 contract validation,
and 14/14 pinned plugin tests. The retained Gate 7 browser run passed 129/129
checks across desktop, tablet, and phone, including Settings identity/action
separation, seven areas, URL restoration, connection-truth explanation,
adult-only profiles, keyboard/accessibility checks, and phone reflow without
horizontal overflow.

Live observation activation completed on immutable revision
`b9f340a82d2430171fca38a8fc0f529577b35813`. The final guarded switch created
encrypted backup `administrateme-20260807T124248Z.ambackup`; the prior release
remains pointer-addressable for rollback. Stable-wrapper status and the full
post-switch Doctor are healthy: the Python runtime remains observation-only,
OpenClaw runs the exact reviewed 2026.7.2 source and discovered plugin contract,
fourteen of fourteen projection checkpoints are healthy, five of five managed
jobs reconcile, four secure references resolve, and the canonical synthetic
round trip passes with zero external side effects.

The live portal was refreshed against the final revision and verified without
mutating any connection. It shows the authenticated principal, zero settings
items needing intervention, three live central connections with independent
setup/runtime/credential/safety axes, and external writes suppressed by
observation. The read-only mail connection now explicitly says it has no
reviewed provider write capability; the calendar and task connections show
their reviewed write ceilings without claiming writes execute. Every Settings
area opened successfully, the selected area survived its URL, and the current
production viewport had no horizontal overflow.

## 2026-08-06 CO-003 People and relationship memory live observation release

The principal approved CO-003 on 2026-08-06. ADR-0004 and
`CHANGE-ORDER-003-PEOPLE-AND-RELATIONSHIP-MEMORY.md` now govern People. The
change **PRESERVES** the encrypted append-only log, provider authority, typed
domain separation, member isolation, Triage human gating, external-write
governance, observation, replay, and the pinned OpenClaw seam. It
**MODERNIZES** People into deterministic relationship memory. It
**SUPERSEDES** sender-as-person creation, tenant-wide exact-identifier
assumptions, implicit provider-share authorization, generic CRM cards, model
closeness/ranking, moralized overdue cues, and per-contact/account agent
proliferation.

Parties v2 separates unresolved Endpoints, exact provider Contact Records,
qualified Parties, scoped Identifier Links, Memberships, Relationships,
member-specific profiles, typed facts, and explicit grants. Ambiguous exact
matches are retained as evidence and fail closed. The identity-resolution
pipeline observes unknown inbound/outbound endpoints instead of minting
people. Only household enrollment, explicit entry, an eligible curated contact
record, or a human-confirmed Triage candidate can qualify a Party.

The member portal and pinned `administrateme.dashboard.people` binding consume
`deterministic-people-cockpit-v1`. The portal adds authorized search,
All/Household/Favorites/Organizations/Suppressed lenses, truthful contact
sources, explicit relationship cues, a qualified Party master/detail view,
authorized contact/fact/activity context, and member-owned relationship
settings. Children, devices, ambient identities, cross-adult private reads,
and view-as mutations fail closed. Search indexing is audience-specific;
granted fact/identifier searches re-check the active grant at query time so
expiry or revocation cannot leave a searchable side channel.

The reviewed household enrollment now crosses the event boundary before
runtime readiness. It records the household Party, member Parties,
Memberships, explicit Relationships, and optional reviewed external contacts
idempotently; it does not grant an external contact membership, helper status,
portal identity, provider data, or hidden identifiers. A restart/replay process
test proves no duplicate enrollment events and the People surface labels the
authenticated member as “You.” Contact freshness no longer borrows an
unrelated message watermark when no contact provider is configured.

Four reviewed contact-source packs establish provider-neutral, loss-aware
contracts for Apple Contacts, Google People, Microsoft Graph Contacts, and
CardDAV. They preserve exact member/provider/account/container/native id/
revision, capabilities, tombstones, groups, cursor, and revision conflict.
No source is presented as configured until its member-specific client and
permission are actually enrolled. All contact provider writes remain disabled
by observation and require the existing guarded external-sync path.

AC-30 passes through the production composition root. The complete release
candidate command passed 30/30 strict acceptance tests, 1,552/1,552 Python
code-and-pack tests, 60/60 standalone bridge tests, Ruff format/lint across 489
files, mypy across 216 source files, Pyright, dependency audits, performance
budgets, both package builds, architecture negatives, clean install, tenant
isolation, authenticated backup/restore, shell validation, exact OpenClaw
2026.7.2 contract validation, and 14/14 pinned plugin tests. Provider clients
and live provider mutations are deliberately outside this credential-free
candidate claim.

Live observation activation completed on immutable revision
`40d85aab141ac74032579cf8953ecc1c5f53f311`. The guarded updater created
encrypted backup `administrateme-20260807T031528Z.ambackup` before switching.
Status and Doctor are healthy: the Python runtime is observation-only,
OpenClaw is running on its exact reviewed contract, fourteen of fourteen
projections are healthy, five of five managed cron jobs reconcile, the
canonical synthetic round trip passes, and there are no live provider claims.
The live People route shows the five reviewed scoped identities with
member-relative relationship labels, honest no-source/no-interaction states,
working URL-backed search and Household lens/detail selection, the canonical
ten-surface navigation, and no horizontal overflow at the production desktop
viewport. AC-30 covers the remaining responsive contract viewports. Contact
source enrollment and any provider mutation remain separate per-member,
per-account, observation-governed evidence boundaries.

The first guarded switch of implementation commit `5f5f58d` was refused when
the live pre-v2 identifiers table exposed a schema-ordering defect: the
bootstrap schema attempted to create the new scope index before the additive
scope columns existed. The updater automatically restored the prior immutable
release and Doctor returned healthy. The correction moves the index to
`PartiesProjection.migrate_schema()` and adds a regression that executes the
real schema-then-migration ordering against a legacy identifiers table. The
complete release candidate command then passed again with the count above.

## 2026-08-06 CO-002 Notes and authorized family recall

The principal approved CO-002 on 2026-08-06. ADR-0003 and
`CHANGE-ORDER-002-NOTES-AND-FAMILY-RECALL.md` now govern the former Knowledge
surface. The adult navigation and pinned OpenClaw binding use **Notes**;
Knowledge remains a deprecated compatibility alias only. This change
**PRESERVES** encrypted event-log authority, Cat-D source ownership, private
scope, domain-type separation, human gating, and the pinned OpenClaw
`llm-task` seam. It **MODERNIZES** provider-neutral note normalization,
authorized recall/search, source capability truth, and explicit adult sharing.
It **SUPERSEDES** a Notion/Obsidian second authority, provider-share inference,
autonomous whole-corpus mining, and direct note-to-domain/provider writes.

The release candidate adds additive member-note v3 fields, explicit
grant/revoke events and projection rows, deterministic FTS, derived-vector
revocation, and a bounded `extract_note_semantics` skill. Exact-evidence,
allowlisted, confidence-qualified results become private Triage proposals only.
Source correction, deletion, or privileged reclassification supersedes pending
candidates and removes ineligible derived search state. Recurrence remains
separate from tasks, calendar events, reminders, lists, list items, and
OpenClaw scheduler jobs.

The built-in skill inventory is now eleven: CO-002 adds one Notes-only
extraction pack without removing or weakening any of the prior ten. Model
routing, fallback, credentials, and generic conversation state remain wholly
OpenClaw-owned.

The member Notes workspace supplies source rail, authorized search, All,
Recent, Shared, Meetings, and Needs Triage lenses, master/detail reading,
provenance, capability warnings, explicit per-note adult grant/revoke, safe
text rendering, and exact Triage/source-evidence links. Principal view-as,
children, devices, ambient sessions, generic OpenClaw memory, and operator
scope do not grant note access. AC-29 is the governing process gate. The exact
release-candidate suite passed 29/29 acceptance tests, 1,544/1,544 Python
code-and-pack tests, 60/60 standalone bridge tests, and 14/14 pinned OpenClaw
plugin tests. Ruff format/lint (480 files), mypy (213 source files), pyright,
architecture negatives, invariant/identity/path scans, dependency audits,
Python/bridge/OpenClaw builds, clean install, tenant isolation, authenticated
backup/restore, and performance budgets passed. The rendered Gate 7 portal run
passed 119/119 checks across desktop, tablet, and phone with zero failed checks,
including search restoration from a URL reload.
The reviewed candidate was then switched into observation and verified as
recorded below.

Live activation completed on immutable commit
`7e7cff75cbc0d04fedd79cb8e5ef85743ebe5553`. The authenticated pre-switch
backup is `administrateme-20260807T000806Z.ambackup`; the prior release remains
pointer-addressable for rollback. Stable-wrapper status and the full candidate
Doctor report healthy loopback services, observation-only runtime state, 14/14
projection stores and checkpoints, 5/5 managed OpenClaw jobs, exact reviewed
OpenClaw `2026.7.2` plugin discovery, and a passing canonical synthetic round
trip with zero external side effects.

The in-app browser verified the canonical `/member/notes` route and the
`/member/knowledge` 308 compatibility redirect against the running release.
The adult rail contains exactly ten destinations with Notes active after
People; observation, source freshness, and a truthful Not Configured empty
state remain visible. A read-only search restored exactly from its URL after a
reload and cleared back to the canonical route. The surface had no document
overflow at 1440x1000, the active 966 px window, or 390x844; its source/search
controls and chat drawer remained available on phone; and the browser reported
no warnings or errors. Personal knowledge truthfully remains `pending-bridge`;
this deployment does not claim a live Notes provider or enable any external
write.

## 2026-08-06 CO-001 five-surface executive loop

The principal approved CO-001 on 2026-08-06. The implementation
**PRESERVES** event-log authority, provider truth, member isolation, typed
domain separation, dual approval/governance, observation, and the pinned
OpenClaw `llm-task` seam. It **MODERNIZES** the Today, Triage, Comms,
Reminders, and Calendar read compositions and browser workflow. It
**SUPERSEDES** unread-as-Focus, all-day/context-as-next-event, raw reminder
recomposition in Today, free-text/native-prompt routing, and a permanent chat
rail on dense workspaces. No required product function is deferred.

`adminme/surface_contracts.py` supplies disposable non-authorizing deep-link,
attention, provider-action, and freshness DTOs. Daily Brief v2 separates timed
schedule and day context, consumes the shared effective-reminder composer, and
uses the same Focus v2 predicate/inputs as Comms. Comms v2 treats unread as
presentation only and safely normalizes provider text. Triage exposes
structured authorized destinations, deterministic readiness, recent decisions,
exact source evidence, and Calendar candidate previews through the existing
household reasoning contract. Strict reminder possible-copy groups reduce
duplicate cognitive load without merging, completing, deleting, or hiding any
provider row.

Interactions v4 additively stores schema-bound semantic features and provenance
from `messaging.semantic_features_extracted`. The existing ten-skill inventory
is unchanged: `classify_message_nature` v3 reports bounded features/candidates
through pinned OpenClaw, and `noise_filtering` v2 applies fixed confidence,
exact-private-member scope, sensitivity, target, and field allowlists before emitting private,
human-gated Triage proposals. The model does not rank, route, authorize,
schedule, send, complete, suppress, or mutate.

AC-28 is the process-level proof in
`tests/acceptance/test_gate7_household_experience.py`, supported by the pure
surface/Today/Comms/Reminders/What Now/Calendar suites, Interaction migration
and replay, pipeline defensive cases, server authorization, and Gate 7 browser
checks. Final exact release counts and live immutable deployment evidence are
recorded below after the release gates complete.

Release-candidate evidence on the supported Mac is
`bash scripts/acceptance/release-candidate.sh`: **PASS**. Its retained phases
reported 27/27 acceptance tests with `--runxfail`, 1,537/1,537 Python
code-and-pack tests, 60/60 standalone bridge tests, and 14/14 pinned OpenClaw
plugin tests. Ruff lint/format, mypy (213 files), pyright, architecture
negatives, invariant/identity/path scans, dependency audits, Python and bridge
builds, OpenClaw TypeScript build/contract/pack, npm audit, shellcheck, and
`git diff --check` passed. The credential-free performance smoke stayed inside
every budget; clean install, tenant isolation, and authenticated backup/restore
rehearsals passed. There were no unexpected skips or xfails and no known
third-party vulnerabilities. Non-failing environment notices were the retained
Starlette/httpx deprecation, Poetry metadata deprecations, pip-audit exclusions
for the two unpublished local packages, cache-deserialization warnings, and an
available newer pyright release.

Observation remains active. This change does not activate optional providers,
perform a real provider mutation, or change People, the then-Knowledge surface
(now Notes under CO-002), Finance,
Scoreboard, or Settings. Rollback is the existing authenticated immutable
release-pointer rollback after service quiescence; the event migration is
additive, so prior events are not rewritten or deleted.

Live observation activation completed on immutable revision
`eeec1a4bd8ef6955636871ff7cc75a41a571fc73`. The final authenticated switch
backup is `administrateme-20260806T224346Z.ambackup`; the prior immutable
revision remains pointer-addressable for rollback. Independent stable-wrapper
status and full Doctor reported healthy loopback services, the exact reviewed
OpenClaw `2026.7.2` source/commit, 14/14 projection checkpoints, 5/5 managed
jobs, and a passing canonical synthetic round trip with zero external side
effects. Runtime state remains `observation_only`.

The live switch also strengthened deployment correctness. The first CO-001
activation showed that post-switch Doctor could inspect new packs through the
retiring release interpreter. `UpgradeManager` now invokes Doctor through the
candidate release's `.venv` and supplies the exact instance, OpenClaw, and
operator PATH environment. An intermediate candidate with incomplete runtime
environment failed closed and rolled back automatically; the corrected
candidate and both responsive follow-up revisions passed full post-switch
Doctor before adoption.

The in-app browser then exercised all five routes without provider writes.
At both the previously failing 911 px width and a 390 px phone width, Today,
Triage, Comms, Reminders, and Calendar showed exact active navigation,
persistent observation/freshness state, a collapsed CoS drawer, no degraded
banner, no document-level overflow, and zero browser warnings/errors. The live
audit caught and repaired Calendar's intermediate-width board/header overflow
and Reminders' five-control Quick Add overflow; process acceptance was rerun
after each repair. Comms also demonstrated that unread is presentation-only:
the live Focus lens was empty while the All lens retained the scoped source
threads, and selecting one thread produced an exact URL-backed reader without
sending or confirming anything.

## 2026-08-06 multi-calendar interaction reconciliation

The Calendar frontend **MODERNIZES** its agenda-list-only presentation while
preserving `deterministic-household-calendar-v1` as the sole reasoning
contract. The production surface API now accepts an explicit local date and a
bounded view enum, then composes a one-day Day, Monday-based seven-day Week,
six-week Month, or fourteen-day Agenda/Find-time range through the same scoped
Calendars projection and approved policy. Duplicate groups expose all source
routes so a member can visually filter copies without losing provenance.
The comparative product evidence and deterministic translation are recorded in
`docs/refactor/CALENDAR-UX-RESEARCH.md`.

The browser now leads with anchored date navigation and a spatial Day/Week
grid, separate all-day lane, Month and Agenda views, source visibility controls,
capacity-versus-calendar color lenses, household guardrail backgrounds,
current-time position, duration-aware open-window review, and an event detail
pane for effect, source, buffer, duplicate, and evidence facts. Calendar
visibility and color are presentation-only and never remove facts from capacity,
conflict, Today, or Triage computation. Recurring guardrails and buffers are
summarized before the reviewed rule editor, which is progressively disclosed
to reduce accidental policy changes. `tests/unit/test_calendar_reasoning.py`
and the Gate 7 process slice pin source-route retention and exact anchored
month/day boundaries. The credential-free rendered Gate 7 run passes 107/107
checks with ten Calendar-specific interactions, zero console/page/request
errors, and deterministic Week/Month captures under
`docs/refactor/evidence/gate-7/visual/`. Live provider-data simulation follows
reviewed deployment.

## 2026-08-06 live Calendar activation evidence

The central Mac is running the reviewed Calendar reconciliation in observation
mode. Live Google discovery resolved seven included calendars and four
explicitly quarantined calendars; the reviewed per-member rules and household
schedule policy are event-sourced, Calendar onboarding reports complete, and
Today, Find time, Rules, and Triage were browser-simulated against the running
release. Doctor reports all fourteen projections healthy, exact OpenClaw
plugin discovery, five-of-five managed schedules, and both loopback services
healthy. External provider writes remain suppressed during observation.

The production run also exposed two restart-only defects that static fixtures
did not: Google's legacy `primary` alias could survive beside its canonical
calendar id, and a fresh adapter process re-appended unchanged provider
snapshots. Both are **HARDENED** without weakening append-only authority. The
Calendars projection retires only the derived alias directory/rule state after
the canonical primary is observed, while provider event snapshots now use a
stable member/account/calendar/event/revision idempotency identity. Event facts
are never deleted, real provider revisions still append, and restart regression
coverage pins the behavior through the shipped adapter loader.

## 2026-08-06 deterministic household Calendar reconciliation

The Calendar product now **MODERNIZES** the earlier single-primary-calendar
and naïve every-observation-is-busy behavior. Google calendar-list discovery
records provider account, calendar, access role, and exact read/write
capability. Calendar event identity is scoped to provider/account/calendar so
legitimate duplicate copies coexist in the encrypted log and projection;
`iCalUID` plus occurrence time groups them only in the member read model.

Principal-approved `calendar.rule.configured` and
`calendar.schedule_policy.configured` events supply the entire decision input
for hard blocks, soft load, context, quarantine, tentative/caregiving effects,
buffers, protected household windows, conflict/pressure detection, and open
windows. `deterministic-household-calendar-v1` contains no model seam. Calendar
now provides Day, Week, Find time, and Rules views; Today consumes the same
effective events, while Triage offers only reviewed writable destinations and
retains the one-confirmation plus independent-governance write contract.

Code/process evidence is `tests/unit/test_calendar_reasoning.py`, the Calendars
v3 projection tests, Google multi-calendar discovery/restart tests, and the
Gate 7 onboarding/Calendar/Today/Triage process slice. Live account discovery,
policy seeding, and browser simulation are complete. Provider-write
propagation remains AC-27 production evidence after observation is explicitly
approved.

## 2026-08-06 live native-message reconciliation repair

The Reminders activation restart exposed a pre-existing Comms poison event:
Gmail legitimately re-observed one native message after provider state changed,
but the interactions handler derived a new row id from the new event while a
tenant/channel-only unique index rejected the native id. The durable bus
correctly stopped after five attempts and the upgrade manager correctly rolled
back rather than hiding a degraded checkpoint.

The repair **HARDENS** D28 while preserving append-only event authority. Repeat
observations now resolve to the first interaction only inside the exact tenant,
member, provider, and provider-account namespace; every contributing event id
is retained and the latest provider observation updates the projection. The
former uniqueness constraint is replaced by an account-scoped lookup index so
normal at-least-once replay can reach deterministic reconciliation code.
Regression coverage proves both repeat folding and cross-account isolation.

## 2026-08-06 deterministic Reminders cockpit

Reminders is now the tenth stable adult surface after Comms. The portal and
pinned OpenClaw dashboard consume `deterministic-reminders-cockpit-v1`, a
read-time composition over the existing Lists projection. It exposes explained
Now/Scheduled/Places/Anytime/Completed lenses, member/account/list filters,
quick add, provider-native item actions, exact capability/limitation remedies,
and source provenance without a model ranking or a new truth store.

List snapshots advance additively to v3 for lossless Google opaque position,
source links, provider metadata, and assignment write restrictions. The Lists
projection version advances to 3 and also stores typed, member-scoped,
expiring context facts plus explicit reminder cue rules; the production
projection count remains fourteen. Google Tasks advances from create-only and
single-list/open-item polling to supported list/task CRUD, completion,
uncompletion, move, clear-completed, list discovery, completed/deleted/hidden/
assigned observation, and transition events. Google remains date-only with no
native priority/alarm/recurrence/geofence fields. Apple EventKit retains its
existing richer date/alarm/recurrence/geofence support and its explicit
Reminders.app-only feature ceiling.

All portal mutations are self-only, exact-account capability checked,
field-allowlisted, optimistic, independently OpenClaw-approved,
governance/rate-limit/content-privacy guarded, observation-aware, idempotent,
and reconciled only by provider terminal evidence. This is a **MODERNIZE**
change under D29/C099; it does not change Task, Commitment, calendar,
recurrence, scheduler, or projection-count semantics. Live provider propagation
remains separate acceptance evidence.

## 2026-08-06 Comms live projection migration repair

The first production activation of D28 exposed an ordering defect that fresh
databases could not reproduce: `schema.sql` attempted to create Comms indexes
on `member_id` and `provider_account_id` before the additive interaction
migration created those columns on an existing v2 projection. This hotfix
**HARDENS** live upgrade behavior while **PRESERVING** every D28 semantic and
the append-only event authority. Additive-column indexes now remain owned by
`InteractionsProjection.migrate_schema()` and are created only after their
columns exist. The regression test executes the current bootstrap schema and
migration hook against a pre-Comms interaction table, matching the production
failure boundary instead of testing the hook in isolation.

## 2026-08-06 deterministic Comms cockpit

This amendment **MODERNIZES** unified communications under D28 while
**PRESERVING** event authority, member/privacy scope, Triage human gating,
provider-native truth, and the single OpenClaw model seam. Comms is the ninth
adult surface after Triage. It composes authorized interactions at read time
into combined, per-account, and personal/workspace lenses; the union is never
stored as a shared mailbox. Children, devices, cross-member requests, and
view-as mutations fail closed.

The Focus, Waiting, Snoozed, Quieted, and All lenses use fixed, explained
rules over direction, unread/important state, typed classification, urgency,
recency, and explicit member controls. No model ranks, sends, archives, or
materializes household truth. The pinned `llm-task` seam may produce auditable
NLP classifications and extraction proposals, but calendar/task/reminder/list
and CRM candidates still require Triage confirmation.

`messaging.received@v3` and `messaging.sent@v2` retain member, provider,
account, workspace, native thread/message identifiers, participants, labels,
state, and provenance in the interactions projection. Snooze/quiet state is
local and event sourced. Reply/read/archive operations are exact-account,
capability-negotiated `external_sync` commands that require independent
approval, the normal external-write guard, observation handling, optimistic
concurrency, and completed/failed reconciliation. Gmail implements these
contracts; Microsoft Graph, a reviewed BlueBubbles bridge, and Twilio
SMS/WhatsApp are truthful disabled catalog edges until installed and granted.

Human-to-human provider messages bypass OpenClaw model sessions. Member-to-CoS
external DMs use one isolated agent per member with account-aware DM scope; a
separate work agent is justified only by a real policy/tool/model/retention
boundary, not by an inbox filter. The portal and pinned OpenClaw dashboard read
the same `deterministic-comms-cockpit-v1` contract.

Final local verification on 2026-08-06: the root Python/bridge suite passed
with 1,340 tests and the combined `tests` + `packs` release slice passed with
1,486 tests; strict acceptance passed with 24 tests under
`--runxfail`; Ruff, format, mypy across 189 source files, invariant checks,
architecture-negative checks, JavaScript syntax, and diff hygiene passed. The
pinned OpenClaw contract, TypeScript check, 14 tests, and build passed. The
production browser harness passed 92 assertions across 21 screenshots at
desktop/tablet/phone widths with zero console, page, or request failures.
Research, exact capability ceilings, process proof, and live boundaries are
retained in
`docs/refactor/evidence/gate-7/comms-cockpit-2026-08-06.md`.

## 2026-08-06 Today Daily Brief and execution correction

This amendment **MODERNIZES** Today into the member's deterministic CoS Day
Sheet while **PRESERVING** the now/hour/daypart/day/week ranker, distinct
domain types, and event-sourced authority. The default Day Brief now composes
the configured local date, calendar, executable tasks and commitments,
provider list items, individual recurrences, ordered Routine occurrences,
actionable messages, and pending
clarifying decisions from authorized projections. The former raw “Open lists”
card and internal carousel/profile/member label are removed.

Day Brief, Carousel, and What Now are explicit member controls over the same
brief. What Now opens an in-progress or next-30-minute timed event first, then
falls back to the deterministic work ranking. Carousel
offers only actions valid for that item's type; `Later` changes only the card
position. Task/commitment lifecycle remains canonical, Apple Reminders uses
the existing governed external-write request, trackable recurrence completion
emits `recurrence.completed`, and calendar attendance/missed state emits an
idempotent, member-confirmed `calendar.event_outcome_recorded`. Calendar time
alone is not attendance proof and list items/recurrences are not coerced into
tasks. The pinned OpenClaw Today binding consumes this same brief rather than a
parallel model-owned summary.

D27 completes the previously reported Routine gap without coercing related
objects. `routine.defined` records an owner-scoped RFC 5545 schedule, IANA
timezone, local time window, and contiguous ordered steps. Today shows the
current step with `Step done` and `Skip step`; either action emits one
idempotent `routine.step_outcome_recorded` fact for the dated occurrence.
Optimistic concurrency enforces step order. A linked Task is provenance only
and remains unchanged. These tables live inside the existing rebuildable
`recurrences` projection, so the registry remains exactly fourteen
projections.

Executable evidence is in `tests/unit/test_today_brief.py`,
`tests/unit/test_what_now_gate6.py`,
`tests/unit/test_projection_calendars.py`, and the process-level Today slice in
`tests/acceptance/test_gate7_household_experience.py`. Rendered evidence and
machine-readable browser assertions are retained under
`docs/refactor/evidence/gate-7/visual/`.

Final local verification on 2026-08-06: the complete Python/bridge suite
passed with 1,335 tests; strict acceptance passed with 23 tests under
`--runxfail`; Ruff, mypy, invariants, and diff hygiene passed; the pinned
OpenClaw contract and 14 plugin tests passed; and the Gate 7 production-browser
harness passed all 81 assertions with no failed result or leaked fixture
process.

The former known boundary is now closed by D27 and its projection/process
tests. Routine definition authoring belongs in a later Settings workflow; the
Today execution contract itself is complete for definitions already present
in household truth.

## 2026-08-06 Triage surface and adjudication correction

This amendment **MODERNIZES** the former Inbox into the canonical Triage
surface under D26. Triage is a private, deterministic projection containing
only unresolved extracted/derived candidates; already-official tasks stay on
Today and Scoreboard. Existing commitment proposals and OpenClaw household
action proposals are aggregated during migration, while new generic
`triage.item.*` events cover task, commitment, calendar-event, reminder, list,
list-item, and CRM identifier-link targets.

Member confirmation passes through a closed typed materializer. Local targets
emit canonical events only after confirmation. External targets require an
explicit household member, provider account, and destination container before
a governed `external_sync.write_requested` event can be emitted; the provider
observation remains the fact that updates calendar/list projections. The Apple
Reminders adapter no longer consumes unconfirmed `commitment.proposed` events.
Legacy Inbox HTTP/OpenClaw bindings remain deprecated aliases but are absent
from new navigation. `tests/acceptance/test_triage_vertical_slice.py` proves
the production composition from proposal through human confirmation to one
official task.

Local release evidence on 2026-08-06: the complete root suite passed with
1,324 tests; strict acceptance passed with 22 tests under `--runxfail`; Ruff
and mypy passed across 189 production/bootstrap source files; both invariant scripts
passed; and the exact pinned OpenClaw contract, TypeScript check, 14 plugin
tests, and build passed. The Triage process proof also covers idempotent
enrichment/confirmation/dismissal, private-scope isolation, CRM visibility,
Apple Calendar routing, Google Tasks routing, replay, and the rule that an
external write command cannot fabricate a provider observation.

## 2026-08-06 personal Google OAuth activation repair

The live Mac activation proved that the Google packs' service-account-only
credential resolver could not enroll a personal Gmail account. This amendment
adds a shared secret-reference resolver for existing 1Password references and
instance-bound macOS Keychain references, installed-user OAuth factories for
Gmail and Google Tasks (matching Calendar), and a local consent helper that
stores only refresh credentials in Keychain. Gmail remains read-only; Calendar
and Tasks remain governed by observation mode and the external-write seams.
Pack-level regression coverage passes with 29 tests and Ruff is clean.

## 2026-08-06 live release-switch hardening

The first guarded Triage activation attempt rolled back automatically when
launchd retained the previous immutable release definition during a reload
race. The repair waits for the old job to disappear, then verifies the loaded
program, working directory, and repository-root environment against the new
plist before health can pass. Stable wrappers also change into the validated
active release before importing bootstrap code, and LaunchAgents now provide
the exact state directory through `ADMINME_INSTANCE_DIR`, preventing live
model-skill pipelines from degrading on an unresolved instance path. The
post-switch gate tolerates only bounded projection/Gateway/plugin/cron/listener
startup transients before requiring the complete Doctor and synthetic round
trip; structural and security failures still roll back immediately. These
changes **MODERNIZE** service switching while **PRESERVING** rollback, release
immutability, secret-free argument lists, and fail-closed readiness.

Live visual validation also proved that stable asset URLs allowed a signed-in
browser to retain the pre-Triage JavaScript across an immutable release switch.
Member HTML now injects one content-derived version into both CSS and JavaScript
URLs and serves the shell/assets with `Cache-Control: no-store`, so browser code
and server APIs cannot straddle releases.

## 2026-08-05 live activation hardening

The production Mac rehearsal additionally proved and repaired three operational
edges: the managed Gateway RPC probe now receives its Keychain-backed token
without persisting it, launchd startup tolerates the documented RunAtLoad versus
kickstart race only when the job is verifiably loaded, and encrypted backup
omits only OpenClaw's generated `plugin-skills` links while continuing to reject
all other links. Quiesced backup also tolerates SQLite removing only disposable
`-wal`/`-shm` sidecars between enumeration and hashing; durable files remain
fail-closed. Launchd now proves the central runtime ready before starting the
Gateway so the local plugin can register against its backend. The complete root
suite passes with 1,314 tests.

## 2026-08-05 Mac bootstrap compatibility repair

The live Apple-silicon rehearsal exposed five release-blocking compatibility
defects and this reviewed amendment fixes them. Bootstrap now requires the
current Homebrew 6 metadata consumer, keeps pinned Poetry in an instance-local
tooling environment outside the synchronized application virtualenv, permits
only the installer-owned `app`, `bin`, and `state` scaffold at first wizard
entry, and explicitly acknowledges the reviewed local OpenClaw plugin path.
The plugin now registers its eleven declared dashboard methods during the
pinned runtime's read-only discovery mode and its nine tools during tool
discovery, without starting hooks or services outside full activation.
Every OpenClaw CLI, cron, activation, and launchd path is also bound to the
instance-local OpenClaw state/config directory so lab and production cannot
select each other's plugin paths. Bootstrap explicitly writes and validates
`gateway.mode=local` before launchd starts the pinned Gateway.
Optional unconfigured member chat remains explicitly blocked in capability
health but no longer blocks the deterministic runtime's global readiness.
Resumed installs reload existing user LaunchAgents before kickstart; the later
2026-08-06 hardening above adds the required unload wait and exact loaded-job
verification.

This **MODERNIZES** installer/package compatibility while **PRESERVING** the
reviewed-source boundary, fail-closed first-run behavior, one pinned OpenClaw
runtime, OpenClaw cron ownership, and AdministrateMe domain authority. Evidence
is recorded in `docs/refactor/evidence/mac-bootstrap-compatibility-2026-08-05.md`.

## 2026-08-05 release disposition

The operator explicitly authorized final review, amendment, green-check merge,
and installation preparation. The reviewed release adds the bidirectional
primary surfaces: central Google Calendar plus per-member Apple Calendar,
Reminders, and Notes. It also fixes production composition so
`config/adapters.json` reaches the central adapter supervisor after service
restart. Calendar and reminder sync also now requires a durable central event
ID before local cursor/state advancement, so an ingest outage retries instead
of silently losing the provider change. The exact merged `main` SHA and final
GitHub check result are supplied with the operator handoff; the Mac must refuse
any other revision.

The initial CoS Mac Mini installation remains credential-free and starts in
observation mode. Provider credentials and Apple grants are deliberately not
bootstrap inputs. The central service is installed first; each member's Apple
apps require a separate per-member bridge running under a macOS login signed
into that member's iCloud account. Google-backed calendars remain owned by the
central Google adapter and must not be duplicated through EventKit.

## Outcome

The repository is the reviewed production-activation candidate. The
two bootstrap gaps that reopened Gate 8 are implemented: AdministrateMe now
reconciles its five pinned OpenClaw schedules, and the central/member bridge
enrollment lifecycle has supported export, acceptance, proof, finalization,
status, disable, and revocation commands. The audit also found that OpenClaw
`2026.7.2` is absent from npm; bootstrap now builds only the exact reviewed
GitHub commit from a pinned archive checksum and records source provenance.

Installation is authorized only at the exact merged SHA stated in the final
operator handoff and only after its GitHub checks are green. The Mac install is
itself the live Apple-silicon rehearsal: stop on any failed release boundary,
Doctor, backup/restore, Tailscale identity, service, or isolation check. No
household data, provider credential, family identity, or family Mac was touched
while preparing this release.

## What is ready

- The central installer refuses root, Rosetta, unsupported macOS, FileVault
  off, low disk, a dirty checkout, a non-matching reviewed SHA, and production
  `--dry-run` before household mutation.
- Release extraction is atomic. Interrupted Python installs have commit
  receipts and safely retry instead of trusting a partial directory.
- OpenClaw source, package-manager lock, version, executable, and receipt are
  pinned and Doctor-checked. There is no npm, beta, or branch fallback.
- The wizard records an explicit IANA household timezone, resumes only its
  encrypted original answers, writes truthful seed follow-ups, requires the
  observation briefing, and distinguishes expected bridge follow-up from a
  fault.
- Cron reconciliation cross-checks both declarations, owns only
  `administrateme-*`, rejects duplicates, adds/edits/removes recorded stale
  jobs, leaves foreign jobs untouched, verifies the resulting state, and
  attempts rollback before reporting failure.
- Member bridge transfers are AES-GCM encrypted, expire after 30 minutes, bind
  member/role/private scope/Tailscale identity/release, keep the one-time code
  separate, and rotate Keychain material transactionally.
- Member installation verifies Apple silicon, FileVault, disk, Tailscale self
  identity, a clean exact-SHA checkout, child/pack restrictions, and an
  immutable atomic release. It rolls back a failed install and consumes the
  transfer file only after the owner-scoped enrollment proof succeeds.
- Central finalization requires the encrypted event-log proof. Revocation is
  independent of portal identity and leaves the central runtime fail closed
  if a safe restart cannot be proven.
- Stable central and member helpers resolve their archived release rather than
  depending on the Terminal's current folder. Tailscale login comparison is
  case-insensitive while duplicate household identities remain forbidden.
- The parallel operator guide includes GitHub acquisition, account/FileVault,
  Homebrew, Tailscale CLI and Serve, lab rehearsal, all wizard answers,
  OpenClaw activation, portal checks, bridge enrollment, backup/restore,
  reboot, observation, and symptom-based troubleshooting.

## Local release evidence

The 2026-08-05 primary-surface amendment passed the current release matrix on
the Windows review host before push:

- strict acceptance: 21 passed with `--runxfail`;
- root and pack suite: 1,455 passed; bridge distribution: 60 passed;
- focused primary-surface/bootstrap regression: 25 passed; three explicit
  central/bridge ingest-failure tests also passed;
- Ruff clean and 455 files formatted; mypy clean across 199 production source
  files; production Pyright: zero errors and zero warnings;
- root and bridge dependency audits found no known vulnerabilities; both lock
  checks and wheel/sdist builds passed;
- architecture-negative, credential-free clean-install, two-instance tenant
  isolation, and encrypted backup/restore process rehearsals passed;
- pinned OpenClaw contract, TypeScript, 12 tests, build, package dry run, and
  production dependency audit passed;
- performance smoke passed all budgets: 11.46-second cold start, 128.26 ms
  append/projection p95, 352.81 ms temporary-view p95, 26.53 ms member-chat
  fixture p95, 5.77 MiB peak traced memory, and 2.38 MB encrypted storage after
  100 task events;
- `git diff --check` passed. Unix `shellcheck` remains intentionally delegated
  to the final Apple-silicon GitHub job because it is not installed on the
  Windows review host.

The final PR-head Apple-silicon check is a merge prerequisite and is reported
with the exact merged SHA in the operator handoff.

All commands below passed on 2026-08-04 from the complete candidate tree:

- acceptance contract: 21 passed with `--runxfail`;
- root and pack collection: 1,446 passed, zero failures;
- bridge distribution: 60 passed; Ruff and mypy clean; wheel/sdist built;
- OpenClaw plugin: exact contract, TypeScript check, 12 tests, build, package
  dry run, and production audit passed;
- rendered portal: 78 checks and 18 screenshots, with zero console, page, or
  request failures;
- Ruff and format: 450 files clean; mypy: 195 source files clean; Pyright: zero
  errors and zero warnings;
- root and bridge lock checks/builds passed; both installed dependency audits
  report no known vulnerabilities (the two local unpublished project names
  are correctly skipped by PyPI lookup);
- architecture-negative, clean-install, two-instance tenant isolation, and
  encrypted backup/mutate/restore rehearsals passed;
- performance smoke passed every budget: 4.8-second cold start, 95.91 ms
  append/projection p95, 96.95 ms temporary-view p95, 18.34 ms member-chat
  fixture p95, 4.98 MiB peak traced memory, and 2.38 MB encrypted storage after
  100 task events;
- every new shell entrypoint passes Bash syntax, and `git diff --check` passes.

The only recurring test warning is Starlette's upstream TestClient/httpx
deprecation. Poetry also reports metadata-migration warnings. Neither affects
the locked runtime or the passing builds.

GitHub run 30964802418 also passed on the maintained Apple-silicon `macos-15`
runner. It built and verified the exact OpenClaw source, exercised the native
plugin contract, performed the credential-free bootstrap/Doctor/recovery
rehearsal, checked the static and secret-safe tree, and ran the whole release
candidate suite. The later receipt-path patch passed in that same run under
macOS's canonical `/private/var` temporary path.

## Remaining live activation gates

The following must be completed from the operator read-along on Apple hardware:

1. an Apple-silicon Mac rehearsal of real launchd startup, reboot,
   Tailscale Serve identity headers, OpenClaw Gateway RPC, cron list state, and
   one bridge enrollment/revocation before enabling provider writes;
2. real model/channel sign-in, minimal Apple/provider grants, Safari with
   VoiceOver, encrypted off-machine restore, and the five-to-seven-day
   observation soak;
3. member-by-member OAuth/EventKit/Notes consent and propagation checks, with
   calendar attendance/change and reminder completion verified as feedback.

Treat any failed item as a deployment stop, not as permission to weaken a pin,
disable identity checks, hand-edit encrypted state, copy a secret into a file,
or use a family Mac as the experiment.

## Operator path

The only first-install guide is
`docs/operations/MAC-MINI-SETUP-READ-ALONG.md`. The concise pointer is
`docs/operations/FIRST-MAC-BOOTSTRAP.md`; command detail is also mirrored in
`docs/refactor/MAC-MINI-OPERATIONS.md`. `KNOWN-LIMITATIONS.md` remains the
authoritative statement of live-only and deferred behavior.

Repository merge is authorized and completed only with green final-head checks.
No tool has remotely installed or activated a household Mac or provider.

## CO-014 live handoff — deterministic context and Triage Zero

CO-014 is reviewed, merged, and active in observation mode. Its complete
credential-free, migration, Doctor, settlement, and authenticated live-browser
gates pass.

- The default registry is now exactly fifteen: thirteen domain projections,
  Triage, and the rebuildable non-authoritative `context_fabric` projection.
- Context Fabric stores typed scoped IDs/edges and reason codes, not source
  bodies. Authoritative traversal excludes `llm_proposed`, is private-member
  exact, and is capped at depth four/500 nodes.
- Triage v7 performs additive migration, deterministic 30-day/100-source matter
  segmentation, exact duplicate retention, high-precision existing-object and
  recurrence evidence linking, full inventory counts, stable cursor paging,
  and matter-first child review.
- Dismissal can atomically append typed feedback. Repeated patterns become
  suggestions; previewed rules are member-approved, reversible, and shadow-
  only. No CO-014 path creates provider effects or automatic truth.
- `tests/acceptance/test_co014_context_fabric_stress.py` proves production HTTP
  pagination, two-member isolation, feedback/rule reversal, and exact
  Triage/Context Fabric replay. The fixed-seed 730-day low/base/high suite
  models 116,800/310,980/613,200 normalized signals and keeps 100% high-
  priority admission, zero missing pages/cross-scope links/provider effects,
  zero base backlog at the end, and reviewed storage/query bounds. The retired
  flat model ends with 3,571/13,184/28,722 rows of backlog respectively.
- Final-head release evidence on 2026-08-10 is green: 49 strict acceptance
  gates, 1,789 root/code/pack tests, 60 bridge tests, Ruff/format/mypy/Pyright,
  dependency audits, architecture invariants, performance budgets, clean
  install, tenant isolation, backup/restore, and the pinned OpenClaw contract,
  17 plugin tests, build, package, and audit. Final Gate 7 produced 225 passing
  assertions and 36 screenshots across desktop/narrow states with no console,
  page, or request failures.
- PRs #327, #328, and #329 merged the implementation and two defects found by
  authenticated live review. The final active release is `0e0f13e`, selected
  through the attested same-runtime updater after encrypted rollback backup
  `administrateme-20260811T013510Z.ambackup`.
- Independent final status and Doctor are healthy in observation mode: 15/15
  projection stores/checkpoints, 8/8 pipelines, 62,573/62,573 source
  obligations accounted for with zero pending/missing/failed, exact OpenClaw
  and `llm-task` execution, and zero synthetic external side effects.
- Authenticated Triage shows the complete 474-candidate/213-matter inventory,
  advances from 65/474 to 124/474 through two stable pages and 60 matter rows,
  preserves independently reviewable nested children, and has no horizontal
  overflow: the live document is 375 px inside a 390 px phone viewport.

## CO-015 release handoff — decision comprehension and surface integrity

CO-015 is reviewed, merged, deployed, and authenticated-live-verified under
G-001. PR #331 head `ca1a29033e48d5d6d32a0cd77de3fc0c1266778f`
received a separate exact-head review receipt after the earlier blocked head
was repaired. Both hosted checks were green, the PR was clean and mergeable,
and zero review threads remained. The guarded merge produced immutable
`main` release `2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe`.

- Today publishes `member-decision-context-v1`: direct question, what happened,
  why present, authorized source label, uncertainty, fixed
  Confirm/Correct/Dismiss consequences, smallest next step, candidate/matter
  identity, and explicit member decision authority.
- Bounded OpenClaw language remains a labelled review aid only. Fixed code owns
  fallback wording, choice set, consequences, routes, lookup, and every write
  boundary; no projection or model router was added.
- Triage accepts `triage_item`, resolves the complete scoped matter outside the
  ordinary cursor, retains an exact resolved row outside the Recent limit, and
  returns pending/resolved/not-found state without cross-member leakage or
  silent substitution.
- Browser focus uses exact candidate identity. A deliberate lens change clears
  stale focus; Gate 7 caught and verified this escape after confirmation.
- Comms now reports actual candidates and matters with individual exact links,
  Notes links candidate + matter + view + evidence, Scoreboard active Tasks
  return to exact Reminders items, and `surface-link-v1` covers all ten adult
  routes.
- The full release candidate suite passes: 49 strict acceptance gates, 1,792
  root/code/pack tests, 60 bridge tests, Ruff/format/mypy/Pyright, dependency
  audits, performance budgets, package builds, architecture checks, clean
  install, tenant isolation, backup/restore, and the pinned OpenClaw contract,
  17 plugin tests, build, package, and audit.
- Final exact-head Gate 7 passes 229 assertions and 36 screenshots across five
  viewport families with zero console, page, or request failures. The production
  composition proves off-page pending focus, immutable resolved focus, and
  cross-member `not_found`.
- The attested same-runtime updater created encrypted rollback backup
  `administrateme-20260811T102316Z.ambackup`, required no runtime or OpenClaw
  migration, retained predecessor `0e0f13e`, and activated the merge SHA with
  rollback available.
- Independent post-switch status and full Doctor are healthy in observation
  mode: 15/15 projection stores/checkpoints, 8/8 required pipelines,
  62,580/62,580 source obligations accounted for, zero pending/missing/failed,
  exact OpenClaw plus authenticated `llm-task`, and zero synthetic external
  side effects.
- Authenticated read-only live review proves decision context in both Day Brief
  and Carousel, candidate + matter + `all_candidates` routing, exact rendered
  and focused Triage identity, explicit no-substitution not-found behavior,
  and 375 px document width inside a 390 px phone viewport. The browser
  recorded no warnings or errors.
- Observation remained active throughout. The provider-called terminal count
  was zero before activation and remained zero after deployment, Doctor, and
  browser acceptance; no decision or provider action was performed.

## CO-016 candidate handoff — Triage queue continuity

Status: implementation candidate; not merged or deployed.

Authenticated live reproduction found that one Acorns spam dismissal did not
create a global sender rule and did not remove the unresolved inventory. The
browser retained the now-resolved exact candidate, reloaded it as `resolved`,
and automatically switched the surface to Recent. Historical
`eligibility_rule_replaced` receipts then dominated the visible read-only lane,
making one dismissal appear to have replaced every candidate.

That reason code is not exclusive to migrations: it also records ordinary
replacement when a newer conversation anchor supersedes an untouched
proposal. The presentation therefore must not infer migration provenance from
the reason alone.

The candidate consumes the resolved selection before reload, clears candidate
and matter URL state, preserves the active working lens, and selects the next
visible matter after both confirm and Dismiss & teach. Exact external links to
resolved candidates still open their immutable Recent receipt. When no matter
remains in the refreshed lens, feedback says the member is caught up rather
than promising another decision. A deliberate lens change clears that
lens-relative feedback before rendering its new context, while reselecting the
active lens preserves it. If the decision write succeeds but refresh fails, a
safety latch preserves the reload warning and disables stale Triage controls
through every lens click until successful reload. Recent identifies itself as
read-only audit history, and eligibility-rule supersession copy states only
that context or eligibility changed rather than claiming a migration or
learned sender rule. That presentation-only copy correction changes no event
schema, canonical or provider truth, or external-write authority. This
candidate separately changes the Triage projection schema and v7-to-v8 replay,
plus the projection runner's explicitly opted-in version replay/stamping path,
as recorded below. Dependency/runtime versions, adapters, pipelines, and other
projection replay policies remain unchanged. A request generation also
invalidates any pre-decision load-more
request before the decision begins (and before a deliberate lens change), so
its eventual success or failure cannot replace the newer authoritative queue,
restore a resolved candidate, alter decision feedback, or re-enable stale
controls. A serialized Triage-operation latch is acquired before dirty-form
enrichment and held through decision and authoritative refresh. It disables
every Triage control—including the shared-shell Add-for-review button and
retained intent form—survives replacement renders, and makes lens, matter,
intent, enrichment, learning-policy, decision, and pagination handlers reject
programmatic work, and native Escape cannot discard a pending Add-for-review
form. Invalidated pagination is normalized before disabled-state
capture, so a no-render failure cannot strand its Loading state. Typed
non-continuation feedback cannot be erased by a later lens change. Older
callbacks cannot replace feedback or clear the recorded-write refresh-failure
latch. The enrichment endpoint returns computed review state, changed readiness
stops before confirmation, and a rejected decision authoritatively refreshes
the exact saved current revision outside ordinary pagination for immediate
retry. Ambiguous transport/5xx and post-confirmation 4xx decision outcomes
reconcile that exact candidate's event/request-specific immutable receipt, reuse the
original request ID for one bounded continuation where needed, or keep every
action locked until reload; HTTP class and generic resolved state are not
settlement proof. A non-decision disposition change keeps exact focus in All
candidates. Shared Add for review now owns an independent operation token on
every member surface, preserves one client request ID across one ambiguous retry
under a per-intent server lock, and blocks Escape/Close/Cancel/repeat submit and
stale callbacks. A generic definite 4xx is not retried; the server explicitly
marks the post-capture case after intent and features are durable. That retry
uses the existing feature event instead of invoking the language seam again,
governance-preflights every missing child, and appends the complete proposal
family atomically. A still-ambiguous dialog keeps an enabled action beside the
exact member text that opens the exact saved Needs interpretation item when
the server supplies capture coordinates; an unknown transport outcome opens
All candidates so a member checks Triage before resubmitting. Evidence-only and attach-existing
candidates are non-approvable in the browser, Triage route, and underlying
commitment route. Confirmation and dismissal reconciliation require their
exact member-action event/request receipts rather than generic resolved state.
Materialization begins only after the Triage projection accepts that exact
confirmation, so concurrently arriving canonical truth wins without a
duplicate. Triage projection v8 additively links the immutable pending
OpenClaw/provider-action receipt without changing external-write authority;
Triage alone opts into reviewed version-change replay. The runner detects v7
metadata, migrates in place, replays retained events from immutable event zero,
and stamps v8 only after success. Exact confirmation receipts are backfilled
even when a later historical outcome advanced the row. Interrupted replay
retains v7 for retry and legacy rows remain intact.
The generations rotate before interaction reopens.

Gate 7 now performs a real task confirmation, a typed spam dismissal, next-
matter continuation, final-Ready-item caught-up feedback, URL cleanup,
lens-change banner cleanup, active-lens preservation, a forced post-write
refresh failure with surface/shared-intent lock and reload recovery, direct
Recent selection, a pagination request held from before confirmation and
observed through browser-side consumption after authoritative refresh, an
invalidated pagination request followed by a no-render failure, typed error
feedback preserved across a lens change, a dirty confirmation locked before
enrichment and through decision, a readiness-changing enrichment that cannot
advance to confirmation, a saved-enrichment/failed-decision/authoritative-
refresh/current-revision retry with exact off-page identity, a committed
decision followed by a lost-response exact-request receipt reconciliation and
refusal to accept another request's terminal receipt, a real
post-confirmation guard refusal resumed with one request identity, an exact
evidence-only transition with no substituted matter, an all-surface
Escape-resistant post-capture Add-for-review retry with one intent identity,
one recorded interpretation, atomic two-candidate recovery, a definite 4xx
with no retry, and exact saved-item recovery from both Today and Triage;
evidence-only confirmation
denial through both routes; exact confirmation/dismissal receipt proof; a
canonical-truth approval race with no duplicate materialization; explicit
Triage-only v7-to-v8 historical pending-action replay with interrupted-replay
retry and legacy-row preservation,
and provenance-bounded lifecycle-receipt copy assertion in production
composition. The repaired candidate passes 259 assertions and 39 screenshots
with zero console/page/request failures. It also passes 53 strict acceptance
gates, 1,799 root/code/pack tests, 60 bridge tests, all
static/dependency/performance/architecture/package gates, clean install,
tenant isolation, backup/restore, the pinned OpenClaw contract, its 17 plugin
tests, exact OpenClaw source install, and the 69-test arm64 macOS rehearsal.
Final candidate head, hosted checks, independent review, and any authorized
release evidence remain pending.

## 2026-08-13 Outcome Harness local candidate

The local candidate adds a thin exact-bound Outcome Harness, five bounded
repository skills, deterministic current-state consistency CI, and a focused
member-navigation module behind compatibility imports. The pilot intentionally
changes no visible route, label, order, click, payload, authorization, mode,
observation, event, projection, pipeline, provider, or release behavior.

An independent final review found that generic receipt filenames had overwritten
the earlier pilot's exact-bound non-browser receipts. The legacy Claude-era
executor/recovery workflows, settings, MCP configuration, hooks, agent
instructions, and setup verdict were therefore restored. A first separate
pre-retirement run was also correctly rejected: it did not bind those eighteen
legacy paths, permitted incomplete review-scope declarations, omitted untracked
mode/file-type identity and four outcome-state classifications, and described a
pre-extraction baseline it had not retained.

The next run, `20260813t180234z-18dafbf8-5xod__dw`, bound all eighteen paths and
passed its functional and browser checks, but independent review correctly
rejected it because the OpenClaw install/check/test breadth receipts ran under
Node 22.23.2/npm 10.9.8. A separate Node 24 browser receipt was not proof for
those commands. That rejected run grants no retirement authority.

The repaired harness binds untracked modes, file types, and symlink targets;
requires all eight review scopes and all eight outcome states exactly once; runs
the production navigation case from an immutable archive of `HEAD` as the
pre-extraction baseline; and requires every prerequisite removal path in its
task binding. OpenClaw breadth and browser receipts each require exact supported
Python 3.14, Node >=24.15,<25, and npm >=11.6,<12 toolchain evidence in their
hashed logs.

Pre-retirement run `20260813t182422z-739bfefa-22y57puv`, bound to diff
`d54819ab905da1d4e2e7071ce3496846008b4f84e83af45a1415f29188009376`,
retained all eighteen legacy paths byte/mode exact to `HEAD` and passed static,
focused, immutable-baseline, current production-composition, full breadth, and
260-assertion/39-screenshot browser proof. Its independent read-only review
returned `APPROVE` with no findings or unresolved evidence; canonical review
SHA-256 is
`98e1ee1f182fe058d723fa7d904947e3c5f56ed9bd76febb88574f0060cb5fe5`.
Only after preserving that exact `NOT_RELEASED` run did the local retirement
patch remove the six Claude executor/constitution/smoke workflows, Claude
settings/MCP registration, three hooks, six agent instructions, and obsolete
setup verdict. Intended replacements and Git provenance remain mapped in
`LEGACY-EXECUTOR-HISTORY.md`.

This is not released. Final breadth/release-tier receipts, final exact-diff
review, and owner handoff are required for the post-retirement candidate. No
commit, push, PR, merge, deployment, Phase B, provider effect, observation
change, household-state access, or G-001 authority has occurred.

Post-retirement run `20260813t184707z-a1b88771-msfn5j52` is rejected and must
not be reused. Its static, focused, immutable-baseline, production-composition,
breadth, and all substantive release checks passed, including 63 release
acceptance tests, 1,932 root tests, and 60 bridge tests. The aggregate release
command exited 1 because Ruff reported that `scripts/harness/checks.py` and
`tests/unit/harness/test_core.py` required formatting. Ruff then mechanically
formatted exactly those two files. Because that changed the binding, all final
receipts and review must come from a new post-format run.
