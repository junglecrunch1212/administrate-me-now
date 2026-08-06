---
schema_version: 1
current_phase: 8
phase_name: production_activation
gate: 8
gate_result: CO001_LIVE_OBSERVATION_ACTIVE_MAIN_MERGE_PENDING
verified_local_date: 2026-08-06
verified_github_date: 2026-08-06
branch: codex/administrateme-v2-runtime
candidate_commit_resolution: eeec1a4bd8ef6955636871ff7cc75a41a571fc73
pull_request_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
openclaw_archive_sha256: a0f1800a64ef495062da6c65e0e9a2b9a6ca0e51c3bc6197918db4eddc7292b8
next_phase: co001_main_review_then_remaining_member_surfaces
---

# Release handoff

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
perform a real provider mutation, or change People, Knowledge, Finance,
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
