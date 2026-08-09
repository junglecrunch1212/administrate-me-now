# Release acceptance contract

CO-008 upgrade-controller amendment (2026-08-08): AC-21 and AC-34 require
every same-baseline or runtime-migration update to execute the reviewed
candidate's upgrade policy from a commit-versioned controller, never the old
active release's policy. Before any live mutation, the launcher must prove a
clean unchanged Git commit, preserve the explicit runtime-migration gate,
attest the loaded controller file and commit, and make the manager independently
reject absent or mismatched provenance. Candidate policy still must create an
encrypted backup, retain the previous immutable release, pass final full
Doctor and synthetic proof, or restore pointer, launch definitions, and exact
OpenClaw selection.
The runtime-migration gate compares compatibility range, preferred version,
and formula for both Python and Node; matching only the range is insufficient.
Live evidence: PR #300 passed both hosted gates, merged as `dc99208`, and the
attested controller completed encrypted backup, activation, terminal source
equality, final full Doctor, authenticated synthetic proof, and rollback
retention. Authenticated Triage rendered candidate rows and all eleven capture
hints while observation prevented member decisions and provider effects.

CO-008 source-throughput amendment (2026-08-08): AC-34 requires privacy and
member-route refusal before any language call. A language-bound replay may opt
into bounded concurrency only when the consumer is idempotent and the durable
bus advances through the contiguous successful source prefix; a failure cannot
skip its source even when later callbacks completed. Delivery concurrency must
be explicit, bounded, and visible in subscriber diagnostics. The updater may
tolerate a slow readiness document only while the separate content-free
liveness endpoint remains true; it may not reinterpret liveness as source
settlement or waive the final terminal predicate.

CO-008 deployment-repair amendment (2026-08-08): AC-34 additionally requires
readiness to remain bounded while one source-assurance refresh is in flight;
refresh failures must be visible without exposing source content. Doctor must
fail activation until the proof is fresh, `accounted == obligations`, and
pending, missing, and failed are all zero. Reviewed language recovery must
reuse a prior successful exact fingerprint, retry an older causal failure no
more than once, and reuse its own terminal retry failure on redelivery. A
changed version or idempotency key without executable causal-failure handling
does not satisfy AC-34.
AC-34 also requires the rollback-aware updater to distinguish an advancing
reviewed full replay from a structural failure. It may poll only the
content-free readiness settlement contract for a bounded 30-minute window; it
must not rerun heavyweight Doctor continuously, persist a candidate cursor
across rollback, waive terminal equality, or skip the final full Doctor and
synthetic proof.

CO-008 amendment (2026-08-08): AC-34 makes source disposition a release gate.
Every current or future adapter source must declare its terminal contract; the
production runtime accounts for each fact without exposing content; and
Doctor, Settings, and Triage distinguish eligible, explicitly empty,
quarantined, failed, pending, and missing. Queue emptiness and a passed
subscriber checkpoint are not evidence of complete processing.

Live language-seam amendment (2026-08-08): AC-12, AC-14, AC-21, and AC-33
require the Keychain-resolved Gateway credential to reach the explicit skill
runtime without ambient secret lookup. Bootstrap and upgrade must enable the
exact pinned bundled `llm-task`, add it without removing existing tool-policy
entries, validate the result through OpenClaw's own config writer, and prove
runtime tool registration in Doctor. A reviewed language-recovery replay may
append new exact-source candidate kind/key pairs but cannot rewrite or duplicate
prior fallback proposals, confirm an item, create canonical truth, or request a
provider write.

CO-007 amendment (2026-08-08): AC-09–AC-12, AC-14, AC-18, AC-23–AC-28,
and new AC-33 require exact-text intent durability, deterministic multi-kind
eligibility, natural-dictation clause boundaries, confirmation-before-effect,
signed independent approval, immutable terminal linkage, Gmail cursor/quarantine
recovery, checkpoint-derived freshness, and least-privileged pinned chat. A
real provider effect, live family account, or family-Mac deployment cannot be
claimed from the credential-free candidate evidence.

Gate 8 replaces the earlier aspirational command list with this executable
contract. The previous list named fourteen test or script files that never
existed; a dead command is not evidence. Every row below now names a retained
test, script, build, or CI job. The aggregate interface is
`scripts/acceptance/release-candidate.sh` on the supported macOS runner.

Runtime-Keychain amendment (2026-08-07): AC-21 additionally proves that a
Python-boundary migration copies configured logical secrets only through an
anonymous pipe, verifies them under the candidate interpreter, atomically
publishes an instance-scoped alias map, preserves old items for rollback, and
fails before release mutation on copy, receipt, map, or bounded-probe failure.
Focused AC-21 unit evidence covers current and legacy reviewed runtime-receipt
shapes plus ambiguous-receipt rejection.
AC-21 also requires separate-process create/read verification through the
Apple-signed Keychain client, proof that values never enter command arguments,
alias-aware production-store and adapter reads, and preservation of the
unaliased rollback keyring path. Long values must survive the Apple prompt
reader through bounded, versioned fragments and a manifest-last, checksummed
envelope; tampering, truncation, missing parts, and partial publication fail
closed without exposing a value. Candidate preflight excludes the generated
alias map from configured-reference discovery and resolves every logical
reference through the production alias-aware store.

CO-006 amendment (2026-08-07): AC-32 proves Finance through the production
composition without adding a projection or provider write seam. The Finance
round trip includes a 61-row-plus-enrichment workbook import beyond the
ordinary interactive budget, with the higher budget scoped only to the local
xlsx reverse daemon. The Finance
workbook is the reviewed planning authority; Plaid owns private provider facts;
the encrypted event log owns provenance/history; and the money projection owns
the deterministic current read model. Real Plaid consent and provider
propagation remain post-merge activation evidence.

CO-005 amendment (2026-08-07): release candidates run on Python
`>=3.14,<3.15` and Node `>=24.15,<25` under ADR-0006. A live 3.11/22 release
is not migrated by test setup. Candidate preparation is side-by-side and a
version-changing activation requires the explicit runtime-migration gate after
clean bootstrap, replay, backup/restore, browser, Doctor, and rollback proof.
The candidate Node formula must precede global Homebrew paths in both central
LaunchAgents and the candidate-interpreter Doctor environment.

Primary-surface amendment (2026-08-05): AC-05, AC-10, AC-11, AC-14, and AC-21
also cover the bidirectional Google Calendar, Apple Calendar, Apple Reminders,
and Apple Notes release. Deterministic tests exercise complete supported
provider schemas, create/update/delete and completion feedback, recurrence and
alarms, reminder lists and geofences, Notes folders and ingestion, durable
cursors, bridge command idempotency, runtime adapter configuration, and
observation/governance. Real OAuth, EventKit, Notes Automation consent, and
iCloud propagation remain explicit live activation evidence.

## Rules

A row passes only from a clean checkout using production composition and
credential-free deterministic boundaries. There may be no unexpected skip or
xfail. Fakes may stand in for provider credentials and household data, but not
for the event log, bus, projections, authorization, HTTP composition, backup,
restore, OpenClaw protocol, or browser rendering boundary named by a row.

The release environment is macOS 14 on Apple silicon, Python 3.14.6 from the
lockfile, Node 24.19.0 (the preferred bootstrap pin), OpenClaw API
and Gateway `2026.7.2` at commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`, installed from the exact
SHA-256-pinned GitHub source archive because that version is not on npm,
loopback-only services, encrypted
temporary instances, and no real household account. Live provider grants are a
post-merge activation boundary and cannot be represented as a fixture pass.

## Executable gates

| ID | Executable release requirement | Pass condition |
|---|---|---|
| AC-00 | `git diff --check`, clean fresh-worktree status after validation | No tracked build residue, secret, or unrelated file. |
| AC-01 | `scripts/acceptance/clean-install.sh`, `tests/acceptance/test_macos_bootstrap_operations.py`, and bootstrap unit suites | Ten-section encrypted fixture install, exact source receipt, idempotent cron, selected-member bridge export/accept/finalize/revoke, idempotent rerun, Doctor, redaction, and safe operations pass. |
| AC-02 | `tests/acceptance/test_runtime_spine.py` and `tests/integration/test_runtime_application.py` | One Python composition owns encrypted log, durable bus, exactly fourteen registered projections (thirteen domain truth plus Triage), pipelines, six product routers, bridge ingest, readiness, unwind, and shutdown. |
| AC-03 | `tests/acceptance/test_communication_commitment_vertical_slice.py` | Communication-to-outcome slice, guarded approval, replay, rebuild, stale/duplicate denial, and contextual next action pass. |
| AC-04 | `tests/acceptance/test_member_bridge_knowledge_vertical_slice.py` | Enrolled member knowledge remains owner-stamped, searchable only where allowed, correctable, replayable, and source-spoof resistant. |
| AC-05 | Full suite projection/schema tests, including calendar, task, commitment, list/reminder/context cue, recurrence/routine, interaction, the existing-table Comms and Lists migration regressions, and `tests/unit/test_primary_surface_v2.py` | Domain types remain separate while linked calendar attendance/change, reminder completion feedback, contextual reminder presentation, and ordered Routine step outcomes update only the correct projections; a cue never changes provider truth and a Routine step never implies linked-Task completion; existing databases receive additive columns before dependent indexes. |
| AC-06 | `tests/unit/test_what_now_gate6.py`, `tests/unit/test_today_brief.py`, `tests/unit/test_projection_recurrences.py`, plus the communication and Gate 7 acceptance slices | Deterministic time/context/energy/availability/blocker/freshness inputs produce explained next actions and immediate feedback; Today composes the member's local Day Brief and ordered Routine occurrence without a model or second truth store. |
| AC-07 | `tests/unit/test_event_log.py`, `tests/integration/test_projection_rebuild.py`, and runtime inventory tests | SQLCipher, wrong-key rejection, append-only log, exact fourteen-name registry, replay, and scope filtering pass. |
| AC-08 | `tests/integration/test_security_end_to_end.py`, `tests/unit/test_scope.py`, and Gate 7 abuse tests | `authMember`/`viewMember`, owner, role, visibility, sensitivity, child, shared-screen, direct-route, and cross-owner rules fail closed. |
| AC-09 | `tests/unit/test_event_bus.py`, pipeline runner tests, communication slice, and forced-exit test in `test_release_candidate_hardening.py` | Append-before-publish, checkpoints, redelivery, poison isolation, idempotency, retry bounds, restart, committed-event recovery, and explicitly opted-in full replay on a reviewed consumer version change pass; ordinary version changes retain their checkpoint. |
| AC-10 | Adapter base, validator, supervisor, cursor, dual-deployment, primary-surface, and Apple Notes bridge command suites under `tests/unit/adapters`, `tests/unit/lib/adapters`, `tests/integration/test_adapter_*`, and `bridge/tests/` | Cat-A/B/C/D/E contracts, complete supported provider fields, bidirectional verbs, capabilities, allowlists, sensitivity, cursors, circuits, identities, command idempotency, and runtime adapter configuration pass. |
| AC-11 | `tests/integration/test_security_end_to_end.py`, `tests/unit/test_observation.py`, and `tests/unit/test_notifications_gate6.py` | Governance order, independent approval, observation suppression, result ingestion, retries, and terminal audit pass. |
| AC-12 | OpenClaw `check`, `contract`, `test`, `build`, and `pack --dry-run`; `tests/acceptance/test_openclaw_native_seam.py`; `tests/unit/bootstrap/test_openclaw_cron_reconciliation.py` | Exact native contract, scoped tools, model provenance, actor denial, exact-once outcomes, outage/restart, owned cron reconciliation/rollback, and compatibility refusal pass. Live install/inspect/deep-RPC remains an activation step. |
| AC-13 | All `test_xlsx_*` unit/integration suites | Both named workbooks, protected/derived rejection, reverse events, sidecars, locks, conflicts, retries, and rebuild pass. |
| AC-14 | Runtime, Gate 6 capability, bootstrap Doctor, adapter supervisor, and OpenClaw outage tests | Disabled, stale, degraded, and blocked states remain distinct; reads survive safe degradations; writes fail closed; diagnostics are actionable and redacted. |
| AC-15 | `scripts/acceptance/backup-restore.sh` and bootstrap operations tests | Encrypted backup, authenticated staging restore, wrong-key/tamper refusal, rollback copy, and post-restore Doctor pass. |
| AC-16 | `scripts/acceptance/tenant-isolation.sh` | Two separately keyed instances accept identical IDs without crossing log, projection, member knowledge, HTTP, or OpenClaw session boundaries. |
| AC-17 | Extension, pack-loader, manifest-validator, skill, profile, and persona suites | Six pack kinds, dependencies, install/rollback behavior, five profiles, and four personas compile without changing authority or model routing. |
| AC-18 | `tests/acceptance/test_gate7_household_experience.py` and console harness `test:gate7` | Ten adult surfaces, three modes, Today Day Brief/Carousel/What Now controls, deterministic Comms focus/account/thread controls, deterministic Reminders activation/account/list controls, ordered Routine complete/skip progression, other typed actions, responsive/reduced-motion captures, chat/degraded states, and private/promoted/archived quest states pass without browser errors. |
| AC-19 | Runtime loopback validation, bridge authorization/enrollment tests, Gate 7 direct-route abuse tests, and listener/config scan in the release report | Gateway stays on pinned loopback, portal and bridge authorization maps are independently revocable, member self identity and owner scope are bound, spoofed/cross-owner requests fail, and legacy listeners are absent. |
| AC-20 | `tests/acceptance/test_release_candidate_hardening.py`, runtime shutdown tests, and backup/restore tests | A real v1 encrypted file migrates through v3, old facts survive, wrong keys and mutation fail, repeated shutdown is safe, startup unwinds, and a committed append survives forced exit. |
| AC-21 | `scripts/acceptance/openclaw-source-install.sh` and `scripts/acceptance/macos-bootstrap-rehearsal.sh` in the `macOS bootstrap contract` PR job, bootstrap controller-provenance tests, plus the live checklist | Credential-free Apple-silicon exact-source build, Homebrew 6 metadata compatibility, isolated Poetry tooling, attested candidate-owned update policy on the active runtime, reviewed-local plugin trust and runtime discovery, central/member launchd/plist contracts, Gateway-health-before-runtime replay startup, cron/bridge process contracts, start/stop/restart, backup/restore, rollback, and Doctor pass. Live Gateway RPC, OAuth, EventKit/Notes grants, iCloud propagation, and household providers remain post-merge human activation. |
| AC-22 | `scripts/acceptance/architecture-negative-checks.sh` | No direct model SDK, manual pipeline/adapter bus publish, hardcoded tenant path/identity, direct projection write, executable/raw-log temporary view, or registered projection count other than fourteen. |
| AC-23 | Gate 7 acceptance, `tests/unit/test_member_chat_gate7.py`, and OpenClaw checks | Adult chat is cancellable and bounded; sessions/transcripts/results cannot cross; ordinary chat emits no domain event; explicit proposals are private and guarded; temporary lenses are data-only, private, review-promoted, expirable, and non-contaminating. |
| AC-24 | `tests/acceptance/test_triage_vertical_slice.py`, commitment acceptance, OpenClaw checks, and member browser checks | Derived candidates appear only in Triage; raw facts bypass it; no canonical record or provider write exists before confirmation; enrichment/routing is scoped; confirm/dismiss are idempotent and audited; legacy Inbox aliases are non-navigational compatibility only. |
| AC-25 | Gate 7 Comms process/browser slice, interaction replay tests, Cat-A adapter/supervisor suites, and OpenClaw contract checks | Member/account/workspace isolation, deterministic explained focus/noise rules, provider capability negotiation, local snooze/quiet replay, view-as/child denial, Triage linkage, independently approved governed reply/read/archive commands, observation suppression, and terminal provider outcomes pass without a model or second mailbox truth store. |
| AC-26 | Reminders composition/schema/projection/API process tests, Apple EventKit and Google Tasks adapter/supervisor suites, Gate 7 browser harness, and pinned OpenClaw contract checks | Member/account/list isolation, deterministic explained activation lenses, provider capability ceilings, typed expiring context cues, view-as/child denial, independently approved governed writes, observation suppression, revision conflicts, bidirectional provider transitions, and migration/replay pass without a model, provider-fantasy fields, or collapsed task/calendar truth. |
| AC-27 | `tests/unit/test_calendar_reasoning.py`, calendar projection and Google Calendar discovery suites, Gate 7 calendar-onboarding process proof, Triage vertical slice, and production browser simulation | Every authorized calendar is discovered with exact capabilities; provider event identity is account/calendar scoped; duplicate copies retain every source route but render once; timezone-local anchored Day/Week/Month/Agenda/Find-time ranges, spatial grids, all-day lanes, household guardrails, source filters, hard/soft/context/ignore, tentative/caregiving, buffers, conflict/pressure, Today, and reviewed Triage destinations all derive from typed policy without a model; display filters cannot change capacity and unreviewed/read-only routes cannot receive writes. |
| AC-28 | `test_ac28_five_surface_executive_loop_is_typed_linked_and_human_gated` in `tests/acceptance/test_gate7_household_experience.py`; `tests/unit/test_{surface_contracts,today_brief,comms,reminders_cockpit,what_now_gate6,calendar_reasoning}.py`; Interaction migration/replay and noise-filtering pipeline suites; Gate 7 browser harness | One member-scoped source fact can acquire schema-bound semantic features, yield only human-gated typed candidates, expose the same explained attention and exact source links across Today/Comms/Triage/Reminders/Calendar, materialize only after confirmation, and recalculate read models under observation without a provider mutation. Unread alone never creates Focus; all-day/context never becomes the next timed event; provider items and domain kinds remain distinct; route capability, freshness, privacy, deterministic decision ownership, replay, and terminal evidence remain explicit. |
| AC-29 | CO-002 Notes process tests in `tests/acceptance/test_member_bridge_knowledge_vertical_slice.py` and `tests/acceptance/test_gate7_household_experience.py`; member-knowledge/vector/Triage projection and `extract_note_semantics` skill suites; Gate 7 browser harness; pinned OpenClaw checks | Provider-neutral note observations reach the private Notes surface with authorized FTS, exact provenance, safe rendering, provider limits, explicit per-note adult grant/revoke, view-as/child/device denial, and replay. Privileged/deleted content is removed from derived indexes; changed source versions supersede pending candidates. A bounded pinned OpenClaw extraction can produce only deterministic exact-evidence Triage proposals and can never choose scope/route or materialize/write a domain/provider record. Canonical Notes routes and deprecated Knowledge aliases remain coherent. |
| AC-30 | `tests/acceptance/test_people_vertical_slice.py`; runtime/parties/identity-resolution/contact-adapter unit and integration suites; pack validators; Gate 7/live People browser review; pinned OpenClaw checks | The production composition records reviewed household enrollment as idempotent Party/Membership/Relationship events; preserves exact provider contact identity and revision; qualifies eligible curated records while unknown endpoints remain outside People; resolves only one authorized qualified identifier and fails ambiguous/hidden matches closed; gates Party/relationship/fact promotion through Triage; enforces private source/profile/fact boundaries and child/view-as/device denial; replays lifecycle and authorized search deterministically; reports truthful Apple/Google/Microsoft/CardDAV capability ceilings; and serves one safe, responsive, deterministic People contract to the portal and pinned OpenClaw binding without a model-owned ranking or second truth store. |
| AC-31 | `test_ac31_settings_is_a_truthful_member_control_plane` in `tests/acceptance/test_gate7_household_experience.py`; adapter-supervisor/runtime integration tests; Gate 7/live Settings browser review | The production composition displays authenticated/read/action member identity and trusted ingress; denies child and view-as mutations and cross-adult bridge enrollment; exposes setup/runtime/permission/safety without config-as-health; leaks no credential reference; activates only validated data profiles; propagates bounded deterministic preferences; records scoped bridge requests; atomically reconciles pause/resume; rejects stale revisions; emits typed private receipts; remains responsive and keyboard operable; and retains one Python runtime, pinned OpenClaw boundary, and exactly fourteen projections. |
| AC-32 | `test_ac32_finance_workbook_to_cockpit_replay_and_member_isolation` in `tests/acceptance/test_gate7_household_experience.py`; money, financial-schema, XLSX forward/reverse, Plaid pack/supervisor/cursor suites; isolated Gate 7 Finance browser review | Detailed provider/account facts and reviewed workbook assumptions, budgets, scenarios, goals, classifications, and manual rows append as typed scoped events; a 61-row plus enrichment import exceeds the ordinary interactive limit while remaining admitted only for the reviewed xlsx daemon caller; one deterministic Finance contract drives workbook and portal; invalid workbooks fail closed; Plaid authority and removal semantics remain intact; durable cursor restart cannot skip accepted facts; replay is equivalent; private member rows never enter another member view or the shared workbook; and the runtime retains exactly fourteen projections with no financial provider write seam. |
| AC-33 | `tests/unit/test_{triage_intent,event_bus,pipeline_noise_filtering,skill_wrapper}.py`; Gmail adapter and `tests/integration/test_{adapter_supervisor_gmail,pipeline_10b_i_integration,runtime_application}.py`; bootstrap required-tool/Doctor/service-order suites; `tests/acceptance/test_{triage_vertical_slice,macos_bootstrap_operations}.py`; Triage, communication, OpenClaw, Gate 7 acceptance slices; pinned plugin checks; disposable in-app browser review | Authenticated member/source text is appended before interpretation; fixed code yields zero or more exact-span or exact-quote typed proposals without a confidence threshold; NLP can identify due actions, decisions/replies, bills, recurrences, and grounded outbound member promises but cannot choose scope, route, authority, or truth; Gmail `SENT` is outbound and an inbound sender promise is never assigned to the member; production skill dependencies explicitly receive the Keychain-resolved Gateway token and instance paths; the exact pinned bundled `llm-task` is enabled, additively allowed, validated, runtime-registered, and live before reactive replay starts; recorded results are reused for a triggering fact or an exact private-scope duplicate-message fingerprint; a reviewed opt-in consumer upgrade replays immutable source facts and appends only new candidate kind/key pairs; repeated sender observations remain idempotent; Doctor fails on a missing/degraded/poisoned required subscriber or unavailable required language tool; Triage distinguishes interpretation/details/readiness/recent outcomes; confirmation precedes local materialization or a pending provider action; only a signed exact allow-once receipt creates external-write v2; no provider is changed in credential-free observation evidence. |
| AC-34 | `tests/acceptance/test_source_assurance_vertical_slice.py`; `tests/unit/test_{source_assurance,triage_intent,pipeline_noise_filtering,pipeline_member_knowledge_indexing}.py`; adapter manifest/CLI/scaffold validator and bootstrap controller-provenance suites; runtime/Gate 6/Triage acceptance; full Doctor and live replay checklist | Every declared adapter/member source has a valid category-specific terminal disposition. Production composition proves no-candidate, quarantine, all eleven member hints, and named projection accounting; legacy failed extraction never appears empty; overdue or checkpoint-skipped work is missing; failed assessment degrades health; Doctor, Settings, and Triage agree on secret-free counts; arbitrary messages use only high-signal fixed fallback plus exact-quote schema-bound NLP; confidence never authorizes; privileged/unroutable content is refused before the model; replay retries reviewed prior failures, deduplicates exact sources, and supersedes only untouched proposals admitted solely by the retired generic action-verb rule; the reviewed settlement policy—not a stale active controller—governs guarded activation. |

## Aggregate release command

On the supported macOS checkout:

```bash
bash scripts/acceptance/release-candidate.sh
```

The script runs strict acceptance, the full Python and bridge suites, Ruff,
format, mypy, production Pyright, package/lock builds, architecture and tenant
checks, clean install, backup/restore, OpenClaw contract/test/build/pack/audit,
shellcheck, Bash syntax, and diff hygiene. Rendered browser evidence is retained
separately because the pinned deterministic harness uses the installed browser;
its exact command is `npm --prefix tools/console-reference-harness run
test:gate7`.

## Evidence standard

The final report records commit, dirty state, UTC time, OS/architecture,
runtime and pinned OpenClaw versions, command, exit status, duration/counts,
unexpected skips/xfails, credential-free fixture boundary, coverage,
performance, retained artifact hashes, and redacted failures. Human visual and
live-provider review supplement but never replace authorization or behavior
tests.
