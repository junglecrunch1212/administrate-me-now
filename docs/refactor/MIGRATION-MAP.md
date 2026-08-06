# Migration map

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

Primary-surface update (2026-08-05): central Google Calendar and per-member
Apple Calendar, Reminders, and Notes are now **KEEP/HARDEN COMPLETE IN CODE**.
The release preserves provider-native schemas at the transport boundary,
normalizes typed facts into the event log, reconciles external authority, and
routes governed writes and terminal results through the central/bridge command
paths. Production bootstrap now actually supplies `config/adapters.json` to
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

Gate 7 update (2026-08-04): the browser-experience deferral is closed. The
deleted Node scaffold stays deleted; FastAPI serves a small static member
portal and authorized JSON/streaming APIs. The exact-pinned OpenClaw plugin
gains the six remaining domain dashboard bindings and signed member-session
provenance, not a second Control UI. A schema-versioned JSON configuration
seam adds disposable saved views without adding a domain aggregate,
projection, scheduler, workflow, or copied record store.

| Gate 7 subsystem | Final class | Executable result |
|---|---|---|
| Principal experience | KEEP/HARDEN COMPLETE | Exact OpenClaw Control UI exposes Today, Triage, Comms, Reminders, Calendar, People, Knowledge, Finance, Scoreboard, Settings, health, governed review/lifecycle bindings, and the same deterministic Daily Brief/Comms/Reminders contracts as the portal. |
| Member/child experience | REWRITE COMPLETE | Python-served portal has adult ten-surface, child two-surface, and shared-screen one-surface server allowlists plus responsive/accessibility evidence; Today supplies a Day Brief, explicit Carousel, and deterministic What Now, Comms supplies member-isolated deterministic message focus, and Reminders supplies deterministic provider-backed activation lenses. |
| Member CoS chat | COMPATIBILITY ADAPTER COMPLETE | Minimal pinned protocol-v4 Gateway relay; OpenClaw owns transcript/memory/providers/routing/retry/accounting; signed per-member tool provenance is independently authorized. |
| Temporary quests | NEW DATA-ONLY CONFIG SEAM COMPLETE | Private by default, allowlisted projection queries/actions, reviewed audience/child safety, typed transition audit, atomic persistence, expiry/archive; no household-fact mutation. |
| Browser runtime | KEEP DELETED NODE / ADD STATIC PYTHON SURFACE | No Node app server, service worker, local transcript/cache, provider SDK, model picker, admin console, plugin manager, or terminal. |
| Live deployment | DEFER TRUTHFULLY | Supported-Mac real Gateway/providers/Tailscale/Apple grants remain Phase 8 evidence. |

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
| Knowledge/search | KEEP/HARDEN COMPLETE | Private explicit-source `member_knowledge`; scope-filtered domain `vector_search`; privileged embedding forbidden. |
| Workbooks | KEEP/HARDEN COMPLETE | `adminme-ops.xlsx` and `adminme-finance.xlsx` retain forward/reverse/sidecar/lock/conflict/Plaid/rebuild behavior. |
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
| Parties projection | `adminme/projections/parties/` | schema/handlers/queries/tests | KEEP | Register and prove live/replay/privacy in composition. |
| Interactions projection | `adminme/projections/interactions/` | account/workspace/body/provider-state schema, handlers, scoped thread queries, command results, replay tests | HARDEN COMPLETE | Preserve every message observation in `raw_event_ids`; fold repeated native-message observations only within the same tenant/member/provider/account namespace, retain latest provider state, and preserve privacy. Deterministic Comms grouping remains read-time composition. |
| Artifacts projection | `adminme/projections/artifacts/` | schema/handlers/queries/tests | KEEP | Register; scope/provenance/correction. |
| Commitments projection | `adminme/projections/commitments/` | schema/handlers/queries/tests | KEEP | Register; full proposal-review-completion feedback. |
| Tasks projection | `adminme/projections/tasks/` | schema/handlers/queries/tests | KEEP | Register; keep task semantics and what-now links. |
| Recurrences projection | `adminme/projections/recurrences/` | schema/handlers/queries/tests | KEEP | Register; never conflate with cron. |
| Calendars projection | `adminme/projections/calendars/` | schema/handlers/queries/tests | KEEP | Register; privacy redaction/link semantics. |
| Places/assets/accounts projection | `adminme/projections/places_assets_accounts/` | schema/handlers/queries/tests | KEEP | Register; preserve distinct entity kinds within capability. |
| Money projection | `adminme/projections/money/` | schema/handlers/queries/tests | KEEP | Register; privileged scope and Plaid provenance. |
| Vector search projection | `adminme/projections/vector_search/` | schema/handlers/queries and privileged-exclusion tests | HARDEN | Register; scope at query/index; prove no privileged/cross-owner embeddings. |
| XLSX projection/reverse daemon | `adminme/projections/xlsx_workbooks/`, `adminme/daemons/xlsx_sync/`, `xlsx_forward.py`, `xlsx_reverse.py` | substantial logic; direct POSIX `fcntl`; Windows collection/runtime failures | HARDEN | Keep macOS lock semantics; compose lifecycle; complete both workbooks, conflict and replay gates. |
| Member knowledge projection | absent; bridge schemas refer to future 11d | required capability missing | REWRITE | Implement owner-scoped schema/handlers/queries and vector handoff from typed Cat-D events. |
| Lists projection | `adminme/projections/lists/` | implemented and default-registered | KEEP | Compose with other 12; external-authority reconciliation and task promotion links. |
| Triage projection | `adminme/projections/triage/` | new D26 deterministic proposal read model | MODERNIZE COMPLETE | Aggregate proposal families, enforce scope, rebuild from the log, never become domain source-of-truth. |
| Adapter protocol/legacy base | `adminme/adapters/base.py` | protocol and supervisor tests | HARDEN | One async emission port; durable status/watermarks/circuit; remove sync/async ambiguity. |
| Five adapter ABCs | `adminme/lib/adapters/` | Cat-A–E contracts and validation tests | KEEP | Preserve categories/verbs/floors/deployment; unify final outbound guard. |
| Adapter pack registry/validator/CLI | `adminme/lib/pack_registry/`, `adminme/cli/commands/adapters*` | useful validation; Windows-invalid `<NAME>` tracked templates | HARDEN | Portable template names/placeholders, all six pack lifecycle stages, atomic rollback/install events. |
| Central adapter supervisor | `adminme/daemons/adapter_supervisor.py` | restart/status primitive; error emit uses ambiguous call shape; not composed | HARDEN | Construct real packs/contexts, retain tasks, durable state, orderly drain, composition status. |
| Bridge package/runtime | `bridge/adminme_bridge/`, `adminme/products/bridge/`, `bootstrap/bridge_enrollment.py` | Central ingest and owner-scoped knowledge are retained; reviewed bootstrap adds encrypted export, identity/release/expiry validation, per-member launchd, real enrollment proof, finalization/revocation, and durable idempotent commands for Apple Calendar, Reminders, and Notes | HARDEN COMPLETE FOR ENROLLMENT AND PRIMARY APP COMMANDS | Live opt-in pack configuration, Apple grants, and iCloud propagation remain separate evidence. |
| Gmail Cat-A | `packs/adapters/messaging/gmail/` | account-bound v3 inbound, v2 outbound, history sync, provider labels, reply headers, read/archive mutations, dynamic OAuth scopes and tests | HARDEN COMPLETE IN CODE | Live account remains read-only until fresh modify/send OAuth consent; Pub/Sub watch/fallback remains operational follow-up. |
| Cat-B calendar/reminder/task packs | `packs/adapters/{calendaring,reminders,tasks}/` | Google/Apple Calendar and Apple Reminders now cover supported native fields, calendars/lists, recurrences, alarms/geofences, completions, change feedback, durable cursors, and central/bridge command paths | HARDEN COMPLETE IN CODE | Retain external authority, observation/governance, idempotent writes, and require live OAuth/EventKit propagation evidence. |
| Plaid Cat-C | `packs/adapters/financial/plaid/` | inbound code/tests; vendored docs lint noise | KEEP | Compose sandbox-first; redact/privileged scope; authority and workbook proof. |
| Home Assistant Cat-C/E | `packs/adapters/automation/home-assistant/` | multi-capability implementation/tests | HARDEN | Compose; final guard, observation and verified action outcome. |
| Pipeline protocol/loader/runner | `adminme/pipelines/` | reactive subscriptions/tests; stop delegates bus ownership | HARDEN | One bus lifecycle owner, replay/idempotency and poison policy; capability registry. |
| Pipeline supervisor | `adminme/daemons/pipeline_supervisor.py` | prose-only future stub | REWRITE | Delete stub after composition root owns runner lifecycle. |
| Ten pipeline packs | `packs/pipelines/` | typed packs and many unit/integration tests | KEEP | Register production set; close outcome feedback; remove hidden scheduler ownership. |
| Ten skill packs | `packs/skills/` | schemas/prompts/tests present | HARDEN | Pin manifest/schema versions; tenant scan; current OpenClaw invocation contract. |
| OpenClaw skill wrapper | `adminme/lib/skill_runner/` | Gate 4 makes exactly one pinned `llm-task` request and records actual returned provider/model provenance; no local provider iteration | REWRITE COMPLETE | OpenClaw owns routing/fallback; schemas, sensitivity, durable audit, and proposal semantics remain AdministrateMe-owned. |
| Session/scope/privacy | `adminme/lib/session.py`, `scope.py` | strong unit tests for roles/view-as/redaction | HARDEN | Enforce at central middleware and every query; trusted proxy; privileged-access audit and member portal. |
| guardedWrite/rate limiter | `adminme/lib/governance.py` | correct three-layer local order; no full approval/outbound composition | HARDEN | Compose independent approvals, content/privacy/persona and observation gates; all terminal outcomes. |
| Observation | `adminme/lib/observation.py` | on-by-default and suppression code/tests | HARDEN | Persist start/trust window/review prerequisites; complete payload; Cat-B explicit rule; final seam only. |
| Product Core | `adminme/products/core/main.py` | docstring-only stub | REWRITE | Router namespace and real handlers for CoS/what-now/lifecycle. |
| Product Comms | `adminme/products/comms/main.py`, `adminme/comms.py`, member Comms surface | Ninth adult surface, deterministic focus/thread contract, account capability negotiation, local focus commands and governed provider command spine | REWRITE COMPLETE FOR PRODUCT CORE | Triage remains the only proposal queue; Comms is not generic chat; missing Outlook/BlueBubbles/Twilio provider packs stay disabled and explicit. |
| Product Reminders | `adminme/reminders.py`, Lists v3 projection, member Reminders surface and command/cue APIs | Tenth adult surface, deterministic activation lenses, exact account/list capability negotiation, governed provider command spine, typed context facts/cues, and Google Tasks full supported CRUD/move/complete mirror | MODERNIZE COMPLETE IN CODE | Provider remains authoritative; list items remain distinct from Tasks/Commitments/calendar events; EventKit/Google limitations stay explicit; live propagation remains acceptance evidence. |
| Product Calendar | `adminme/calendar.py`, Calendars v3 projection, member Calendar/onboarding/Triage/Today surfaces, Google Calendar discovery | Deterministic per-member hard/soft/context/ignore semantics, route-scoped identity, duplicate grouping, tentative/caregiving load, buffers, household guardrails, conflict/pressure, find-time, exact provider capabilities, reviewed destinations, canonical-primary alias retirement, and restart-stable provider-revision idempotency | MODERNIZE COMPLETE IN CODE; LIVE READ/POLICY PROOF COMPLETE | SUPERSEDE naïve “every event is busy” and single-primary-calendar assumptions; preserve provider authority, privacy redaction, typed event separation, governance, and require post-observation provider-write propagation evidence. |
| Product Capture | `adminme/products/capture/main.py` | stale quick-capture docstring-only stub | REWRITE | Knowledge/CRM/Lists/read/search namespace; delete quick capture/voice/triage claims. |
| Product Automation | `adminme/products/automation/main.py` | docstring-only stub | REWRITE | Finance/signal/action namespace with scoped reads and governed writes. |
| Bridge product | `adminme/products/bridge/` | only implemented FastAPI app; separate `:3337` | HARDEN | Move router into central root; preserve endpoint compatibility/auth; no separate central process. |
| Console runtime | `console/server.js` | comment-only stub; npm test is placeholder echo | DELETE after replacement | Build real family frontend, then remove Node application server; Node remains build-only if used. |
| Console/browser application | `console/` assets to be created | reference mock only | REWRITE | Implement UX contract, server authorization, sanitization, accessibility, browser tests; serve from Python runtime. |
| Reference HTML | `ADMINISTRATEME_CONSOLE_REFERENCE.html` | rich mock with stale flows and unsafe mock rendering; Gate 1 rendered evidence retained | KEEP as design evidence | Never ship as app; preserve valid vocabulary and retained captures, supersede retired flows. |
| OpenClaw plugin placeholders | `adminme/openclaw_plugins/` | Deleted at Gate 4 after import scan and native contract/process tests; history remains in Git | DELETE COMPLETE | Replaced by one exact-pinned native package at `openclaw/administrateme/`. |
| Standing-order prose/cron | `bootstrap/openclaw/programs/`, `cron.yaml`, `openclaw/administrateme/automations.json`, `bootstrap/openclaw_cron.py` | Five proactive jobs cross-checked, ownership-scoped, timezone-explicit, idempotently reconciled, post-verified, Doctor-visible, and rollback-tested; reward dispatch stays reactive | HARDEN COMPLETE | Retain disposable-Mac RPC proof and never add a Python scheduler. |
| Bootstrap sections | `bootstrap/wizard.py`, `bootstrap/state.py`, `bootstrap/sections/` | Gate 5 ten-section AES-GCM journal; per-section versions, input digests, events, resume/rollback and skip follow-ups | REWRITE COMPLETE FOR INSTALLER | Preserve the encrypted schema and add only versioned migrations. Live grants remain evidence, not state assumptions. |
| Pack install order | `bootstrap/pack_install_order.yaml` | partial, older checkpoint says 1/10 skills | REWRITE | Capability/dependency-driven six-kind plan, exact artifact hashes, atomic install/rollback. |
| Profiles/personas | `openclaw/administrateme/persona/` plus broader built-ins still absent | Gate 4 adds the active instance persona source and deterministic SOUL/workspace compiler; the constitutional five profiles/four persona packs remain later AC-17 work | REWRITE PARTIAL | Member additions remain per-member; persona stays per-instance; neither changes authority/model routing. |
| CLI | `adminme/cli/` | adapter commands and future-command claims | HARDEN | Runtime/Doctor/replay/projection/backup/pack/bootstrap commands that call real composition services. |
| Instance configuration | `adminme/lib/instance_config.py`, `config/channels.yaml` | path resolver and one config exist | HARDEN | Versioned schema, secret references, port/instance isolation, no hardcoded household/path. |
| Health/Doctor | `bootstrap/doctor.py` plus runtime status | Gate 5 exact pin/architecture/loopback/encryption/migration/projection/workbook/adapter/bridge/launchd/backup/observation checks and canonical proof | REWRITE COMPLETE FOR INSTALLER | AC-14 expands provider freshness/circuit detail; JSON export stays secret-scanned. |
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
