# Migration map

CO-008 update (2026-08-08): source ingestion is **HARDENED IN THE REVIEWED
CANDIDATE** by additive manifest `source_assurance` rules,
`triage.intent.features_extracted@v2`, and `triage.item.superseded@v2`.
`noise_filtering` v8 and Notes indexing v3 explicitly opt into full replay.
Replay preserves every immutable source and member decision, appends a terminal
assessment for each candidate-bearing source, retries the new reviewed skill
invocation generation, and retires only untouched proposals whose sole
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

Live activation hardening update (2026-08-05): the OpenClaw activation,
launchd, and backup rows are **HARDEN COMPLETE** for authenticated managed-RPC
probing, the macOS RunAtLoad/kickstart race, and generated OpenClaw plugin-skill
links. Arbitrary state links remain fail-closed and excluded generated links are
reconstructed by the pinned runtime rather than restored as trusted state.
SQLite's disposable `-wal`/`-shm` cleanup race is likewise excluded only after
service quiescence; the database and every other durable file remain required.
Central launchd startup now follows the runtime-before-Gateway dependency and
proves runtime readiness before allowing OpenClaw plugin registration.

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
| Event runtime and projections | KEEP/HARDEN COMPLETE | One EventSink/log/bus root; exactly fourteen registered projections (thirteen domain truth plus Triage); replay, migration, scope, checkpoint, and encrypted reopen tests pass. |
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
| Projection protocol/runner | `adminme/projections/base.py`, `runner.py` | replay/registration mechanisms; default tuple only `lists` | HARDEN | Exact fourteen registry (thirteen domain plus Triage), shared bus ownership, per-capability replay/idempotency/status. |
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
| Gmail Cat-A | `packs/adapters/messaging/gmail/` | account-bound v3 inbound, v2 outbound, direction-preserving history sync (`SENT` never becomes inbound), provider labels, reply headers, read/archive mutations, dynamic OAuth scopes and tests | HARDEN COMPLETE IN CODE | Live account remains read-only until fresh modify/send OAuth consent; historical v3 `SENT` labels remain usable for direction-aware replay; Pub/Sub watch/fallback remains operational follow-up. |
| Cat-B calendar/reminder/task packs | `packs/adapters/{calendaring,reminders,tasks}/` | Google/Apple Calendar and Apple Reminders now cover supported native fields, calendars/lists, recurrences, alarms/geofences, completions, change feedback, durable cursors, and central/bridge command paths | HARDEN COMPLETE IN CODE | Retain external authority, observation/governance, idempotent writes, and require live OAuth/EventKit propagation evidence. |
| Plaid Cat-C | `packs/adapters/financial/plaid/` | detailed private account/transaction/removal events, terminal summary, sign normalization, secret references, durable per-member/item cursor, restart/supervisor tests | KEEP/HARDEN CODE LIVE; PROVIDER ACTIVATION PENDING | Compose read-only; advance cursor only after accepted details and receipt; retain provider identity/revision/removal; require separate live Link/consent evidence. |
| Home Assistant Cat-C/E | `packs/adapters/automation/home-assistant/` | multi-capability implementation/tests | HARDEN | Compose; final guard, observation and verified action outcome. |
| Pipeline protocol/loader/runner | `adminme/pipelines/` | reactive subscriptions/tests; stop delegates bus ownership | HARDEN | One bus lifecycle owner, replay/idempotency and poison policy; capability registry. |
| Pipeline supervisor | `adminme/daemons/pipeline_supervisor.py` | prose-only future stub | REWRITE | Delete stub after composition root owns runner lifecycle. |
| Ten pipeline packs | `packs/pipelines/` | typed packs and many unit/integration tests; `noise_filtering` v3 emits classified facts, bounded semantic features, exact-quote eligible private Triage proposals, and replay recovery; `identity_resolution` v2 uses per-source observation identity | KEEP/HARDEN COMPLETE FOR CO-001 + LIVE REPAIR CANDIDATE | Deterministic code owns eligibility, scope/sensitivity exclusion, exact-source grounding, candidate allowlist, idempotency, and outcome feedback; confidence never gates a review candidate and no model owns a decision. |
| Ten skill packs | `packs/skills/` | schemas/prompts/tests present; `classify_message_nature` 4.0.1 returns classification, bounded semantic features, and exact-quote typed candidates defensively | HARDEN COMPLETE FOR CO-001 + LIVE REPAIR CANDIDATE | Stay on pinned OpenClaw `llm-task`; report grounded language features only; never rank, route, authorize, schedule, send, complete, suppress, or mutate. |
| OpenClaw skill wrapper | `adminme/lib/skill_runner/` | exactly one pinned `llm-task` request uses a canonical member-isolated Gateway session, records actual returned provider/model provenance, and versions durable terminal identity by skill version | REWRITE/HARDEN COMPLETE | OpenClaw owns routing/fallback; schemas, sensitivity, durable audit, exact-quote proposal semantics, and replay identity remain AdministrateMe-owned. |
| Session/scope/privacy | `adminme/lib/session.py`, `scope.py` | strong unit tests for roles/view-as/redaction | HARDEN | Enforce at central middleware and every query; trusted proxy; privileged-access audit and member portal. |
| guardedWrite/rate limiter | `adminme/lib/governance.py` | strict three-layer order plus exact caller/action rate-budget precedence; reviewed xlsx bulk ingestion is isolated from conservative principal and agent defaults | HARDEN | Preserve independent approvals, content/privacy/persona and observation gates; audit all terminal outcomes; never widen an external seam through a local-daemon exception. |
| Observation | `adminme/lib/observation.py` | on-by-default and suppression code/tests | HARDEN | Persist start/trust window/review prerequisites; complete payload; Cat-B explicit rule; final seam only. |
| Product Core / Today | `adminme/products/core/main.py`, `adminme/today.py`, member Today surface | Daily Brief v2 composes timed schedule, day context, effective reminders, typed executable work, Triage decisions, and the same explained Comms attention predicate with exact source links | REWRITE/MODERNIZE COMPLETE | Read composition only; preserve every item kind and source; contextual/all-day facts never become the next timed event; no model ranker. |
| Product Comms | `adminme/products/comms/main.py`, `adminme/comms.py`, member Comms surface | `deterministic-comms-cockpit-v2`, Focus v2, safe entity normalization, exact account capability, attention explanations, local focus commands, Triage/source links, and governed provider command spine | REWRITE/MODERNIZE COMPLETE FOR PRODUCT CORE | Unread is visual only; Triage remains the only proposal queue; Comms is not generic chat; missing Outlook/BlueBubbles/Twilio provider packs stay disabled and explicit. |
| Product Reminders | `adminme/reminders.py`, Lists v3 projection, member Reminders/Today surfaces and command/cue APIs | Shared effective-reminder composer, deterministic activation lenses, exact capabilities, transparent read-only possible-copy groups, structured dialogs, typed context facts/cues, and Google Tasks full supported CRUD/move/complete mirror | MODERNIZE COMPLETE IN CODE | Provider remains authoritative; every copy remains independently inspectable; list items remain distinct from Tasks/Commitments/calendar events; live propagation remains acceptance evidence. |
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

## Gate 2 migration delta

| Subsystem | Gate 2 result | Remaining boundary |
|---|---|---|
| Event writes and durable delivery | `EventSink`/`EventWriter` is the sole typed producer contract; it commits a single event or atomic batch before bus notification. Durable checkpoints advance only after success; retry and poison metadata survive restart. | Full forced-kill and outbound ambiguity matrices remain AC-09/20. |
| SQLCipher lifecycle | Log open/migration, locking, wrong-key failure, rollback, close, and encrypted reopen are owned by the runtime. | Backup/restore and legacy migration fixtures remain AC-15/20. |
| Runtime and projections | One `RuntimeApplication` owns lifecycle and registers exactly parties, interactions, artifacts, commitments, tasks, recurrences, calendars, places_assets_accounts, money, vector_search, xlsx_workbooks, member_knowledge, and lists. | Per-capability semantic/replay equivalence remains AC-07. |
| Producers | Pipeline packs, adapter packs/supervisor, governance, observation, skills, bridge ingestion, and xlsx paths use the injected event sink; synchronous capability probes are removed. | End-to-end product semantics remain Phase 3+. |
| Product topology | Core, Comms, Capture, Automation, Bridge, and System are namespaces inside one FastAPI application with truthful liveness/readiness. | Full UX and authorization behavior remains AC-08/18/19. |
| Portability | Xlsx locking supports Windows and POSIX; scaffold templates use portable filenames; bridge task shutdown is bounded. | Clean macOS installation/rehearsal remains AC-01/21. |
