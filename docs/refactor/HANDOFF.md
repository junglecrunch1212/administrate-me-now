---
schema_version: 1
current_phase: 8
phase_name: production_activation
gate: 8
gate_result: CO007_PHASE_A_CANDIDATE_PASS_LIVE_NOT_DEPLOYED
verified_local_date: 2026-08-08
verified_github_date: 2026-08-07
branch: codex/administrateme-v2-runtime
candidate_commit_resolution: pending_reviewed_commit
pull_request_url: null
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
openclaw_archive_sha256: a0f1800a64ef495062da6c65e0e9a2b9a6ca0e51c3bc6197918db4eddc7292b8
next_phase: independent_review_then_phase_b_activation
---

# Release handoff

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
