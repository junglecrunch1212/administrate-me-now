# Release acceptance contract

Gate 8 replaces the earlier aspirational command list with this executable
contract. The previous list named fourteen test or script files that never
existed; a dead command is not evidence. Every row below now names a retained
test, script, build, or CI job. The aggregate interface is
`scripts/acceptance/release-candidate.sh` on the supported macOS runner.

Primary-surface amendment (2026-08-05): AC-05, AC-10, AC-11, AC-14, and AC-21
also cover the bidirectional Google Calendar, Apple Calendar, Apple Reminders,
and Apple Notes release. Deterministic tests exercise complete supported
provider schemas, create/update/delete and completion feedback, recurrence and
alarms, reminder lists and geofences, Notes folders and mutations, durable
cursors, bridge command idempotency, runtime adapter configuration, and
observation/governance. Real OAuth, EventKit, Notes Automation consent, and
iCloud propagation remain explicit live activation evidence.

## Rules

A row passes only from a clean checkout using production composition and
credential-free deterministic boundaries. There may be no unexpected skip or
xfail. Fakes may stand in for provider credentials and household data, but not
for the event log, bus, projections, authorization, HTTP composition, backup,
restore, OpenClaw protocol, or browser rendering boundary named by a row.

The release environment is macOS 14 on Apple silicon, Python 3.11 from the
lockfile, Node 22.23.1 (the preferred bootstrap pin), OpenClaw API
and Gateway `2026.7.2` at commit
`944cf08f94c334cff2b0fdf7e01187a9efa42715`, installed from the exact
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
| AC-09 | `tests/unit/test_event_bus.py`, pipeline runner tests, communication slice, and forced-exit test in `test_release_candidate_hardening.py` | Append-before-publish, checkpoints, redelivery, poison isolation, idempotency, retry bounds, restart, and committed-event recovery pass. |
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
| AC-21 | `scripts/acceptance/openclaw-source-install.sh` and `scripts/acceptance/macos-bootstrap-rehearsal.sh` in the `macOS bootstrap contract` PR job plus the live checklist | Credential-free Apple-silicon exact-source build, Homebrew 6 metadata compatibility, isolated Poetry tooling, reviewed-local plugin trust and runtime discovery, central/member launchd/plist contracts, cron/bridge process contracts, start/stop/restart, backup/restore, and Doctor pass. Live Gateway RPC, OAuth, EventKit/Notes grants, iCloud propagation, and household providers remain post-merge human activation. |
| AC-22 | `scripts/acceptance/architecture-negative-checks.sh` | No direct model SDK, manual pipeline/adapter bus publish, hardcoded tenant path/identity, direct projection write, executable/raw-log temporary view, or registered projection count other than fourteen. |
| AC-23 | Gate 7 acceptance, `tests/unit/test_member_chat_gate7.py`, and OpenClaw checks | Adult chat is cancellable and bounded; sessions/transcripts/results cannot cross; ordinary chat emits no domain event; explicit proposals are private and guarded; temporary lenses are data-only, private, review-promoted, expirable, and non-contaminating. |
| AC-24 | `tests/acceptance/test_triage_vertical_slice.py`, commitment acceptance, OpenClaw checks, and member browser checks | Derived candidates appear only in Triage; raw facts bypass it; no canonical record or provider write exists before confirmation; enrichment/routing is scoped; confirm/dismiss are idempotent and audited; legacy Inbox aliases are non-navigational compatibility only. |
| AC-25 | Gate 7 Comms process/browser slice, interaction replay tests, Cat-A adapter/supervisor suites, and OpenClaw contract checks | Member/account/workspace isolation, deterministic explained focus/noise rules, provider capability negotiation, local snooze/quiet replay, view-as/child denial, Triage linkage, independently approved governed reply/read/archive commands, observation suppression, and terminal provider outcomes pass without a model or second mailbox truth store. |
| AC-26 | Reminders composition/schema/projection/API process tests, Apple EventKit and Google Tasks adapter/supervisor suites, Gate 7 browser harness, and pinned OpenClaw contract checks | Member/account/list isolation, deterministic explained activation lenses, provider capability ceilings, typed expiring context cues, view-as/child denial, independently approved governed writes, observation suppression, revision conflicts, bidirectional provider transitions, and migration/replay pass without a model, provider-fantasy fields, or collapsed task/calendar truth. |
| AC-27 | `tests/unit/test_calendar_reasoning.py`, calendar projection and Google Calendar discovery suites, Gate 7 calendar-onboarding process proof, Triage vertical slice, and production browser simulation | Every authorized calendar is discovered with exact capabilities; provider event identity is account/calendar scoped; duplicate copies retain every source route but render once; timezone-local anchored Day/Week/Month/Agenda/Find-time ranges, spatial grids, all-day lanes, household guardrails, source filters, hard/soft/context/ignore, tentative/caregiving, buffers, conflict/pressure, Today, and reviewed Triage destinations all derive from typed policy without a model; display filters cannot change capacity and unreviewed/read-only routes cannot receive writes. |
| AC-28 | `test_ac28_five_surface_executive_loop_is_typed_linked_and_human_gated` in `tests/acceptance/test_gate7_household_experience.py`; `tests/unit/test_{surface_contracts,today_brief,comms,reminders_cockpit,what_now_gate6,calendar_reasoning}.py`; Interaction migration/replay and noise-filtering pipeline suites; Gate 7 browser harness | One member-scoped source fact can acquire schema-bound semantic features, yield only human-gated typed candidates, expose the same explained attention and exact source links across Today/Comms/Triage/Reminders/Calendar, materialize only after confirmation, and recalculate read models under observation without a provider mutation. Unread alone never creates Focus; all-day/context never becomes the next timed event; provider items and domain kinds remain distinct; route capability, freshness, privacy, deterministic decision ownership, replay, and terminal evidence remain explicit. |

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
