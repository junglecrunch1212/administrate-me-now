# Deletion and deferral map

Gate 7 update: the browser household UX is no longer deferred. A small static
portal served by the existing Python runtime replaces the deleted Node
scaffold. It adds no frontend application server, service worker, persistent
browser cache, provider SDK, model picker, generic transcript/memory store,
OpenClaw administration surface, plugin manager, terminal, scheduler, or task
runner. The immutable reference HTML remains acceptance vocabulary only.

The missing non-admin chat capability is closed by a narrow OpenClaw protocol
relay; OpenClaw still owns every generic chat/runtime concern. The missing
quest capability is closed by atomic presentation configuration over existing
authorized reads; no Project object, fourteenth projection, domain cache,
workflow engine, executable template/code/SQL, or raw-log query was introduced.
Only live optional providers and the supported-Mac rehearsal remain deferred
to Phase 8.

CO-004 update: the static/truncated Settings capability dump and opaque member
initial are superseded. Provider adapters that do not exist remain deferred
and visibly unavailable; the UI does not fabricate Connect controls. Live
OAuth enrollment, scope expansion, and member-device OS consent remain secure
activation ceremonies, not fixture claims. Identity visibility, truthful
status axes, view-as/cross-adult denial, bounded fine tuning, adapter
pause/resume, scoped bridge requests, redaction, and AC-31 are not deferred.

Gate 6 update: deletion prerequisites are now met for the empty `console/`
Node package, `adminme/daemons/pipeline_supervisor.py`, duplicate xlsx daemon
facades, and dead correlation/identifier helpers. AdministrateMe-side
scheduler/platform dependencies and speculative no-op delivery claims are
removed. The constitutional household surfaces remain in product APIs,
workbooks, the native OpenClaw dashboard, and reference evidence.

Gate 6 formal deferrals are limited to live optional-provider activation and
the full browser household UX. Optional provider manifests and deterministic
fixture paths remain, but unconfigured providers are disabled/blocked and do
not make readiness claims. Full browser implementation is Phase 7 work; no
household domain/API/workbook behavior was deleted with the console scaffold.

Gate 5 update: the empty bootstrap-section scaffold is replaced by the encrypted ten-section state machine and stable operator scripts. Backup, restore, Doctor, user launchd, and upgrade compatibility validation are no longer absent. No live provider, console, or member-knowledge product behavior was deleted or falsely declared complete.

Gate 3 update: Core and Comms are no longer docstring-only service stubs. They are versioned router namespaces in the Gate 2 process and now own the first complete commitment review/lifecycle and deterministic what-now surface. Capture/Knowledge and Automation retain their deferred product work; no legacy production path was deleted in this gate.

Gate 1 deletes no production behavior. A future delete lands only in the same PR as a passing replacement/negative test and a crosswalk/handoff update.

## Delete after replacement

| Target | Why it must go | Preconditions |
|---|---|---|
| Python-plugin claim and four empty files in `adminme/openclaw_plugins/` | **DELETED at Gate 4.** Incompatible with the native current TypeScript plugin contract; no runtime import referenced them. | One TS plugin passes the pinned fixture and process portions of AC-12; history is recoverable from Git. |
| Comment-only `console/server.js` and empty Node package | **DELETED at Gate 6.** It started no server or household surface and conflicted with one-Python-process topology. | Six Python product namespaces, workbooks, OpenClaw dashboard, and reference evidence remain. |
| Four docstring-only product `main.py` service stubs | They advertise separate processes and stale routes without behavior. | Router namespaces exist with route compatibility tests; ownership remains. |
| `adminme/daemons/pipeline_supervisor.py` prose stub | **DELETED at Gate 6.** A separate supervisor duplicated composition ownership and contained no code. | Composition root owns reactive runner lifecycle; OpenClaw owns proactive schedules. |
| AdministrateMe-side provider-fallback loop in `adminme/lib/skill_runner/wrapper.py` | **DELETED at Gate 4.** OpenClaw owns model routing/fallback. | Wrapper posts once, supplies no provider/model override, validates output, and records actual returned provenance. |
| Quick-capture prefix, direct central voice-ingest, and Capture-owned triage route/UI claims | Superseded by member-owned tools, Cat-D bridge ingestion, Notes authorized recall, and the separate Triage gate. | Notes/member bridge/exact-evidence proposal slices pass AC-04/18/24/29. |
| Sender-as-person identity creation, tenant-wide one-identifier/one-party uniqueness, model closeness scores, and generic CRM cards | **SUPERSEDED by ADR-0004/CO-003.** These patterns turn transport evidence into identity truth, leak ambiguity across scopes, and produce unexplained relationship pressure. | Endpoints remain distinct; curated provider records or human decisions qualify Parties; scoped exact resolution fails closed; member intent and descriptive measures drive the People cockpit; AC-30 passes. |
| Product-scheduling references to APScheduler or AdministrateMe cron | Duplicate OpenClaw responsibility. | Gate 4 derives five OpenClaw automation declarations and a typed standing-order trigger; AdministrateMe does not schedule them. Internal debounce/retry timers remain explicitly exempt. |
| Legacy central `:3333–:3337` process topology | More failure domains and duplicated lifecycle without semantic value. | One root on `:3330`; compatibility aliases tested/removed deliberately; AC-19. |
| Placeholder console test that only echoes success | Falsely signals coverage. | Real unit/browser suites exist and AC-18 runs them. |
| Windows-illegal scaffold template filenames containing `<NAME>` | Prevent normal checkout and makes scaffolding tests non-portable. | Portable placeholder convention produces identical generated names; Linux/macOS/Windows tests pass. |

## Preserve, do not delete

- `member_knowledge` and `vector_search`: domain read models, not generic OpenClaw memory.
- Recurrences: domain meaning, not scheduler duplication.
- Product route/ownership contracts when services become namespaces.
- All thirteen domain projection capabilities plus the Triage adjudication projection, six product functions, five adapter categories, six pack kinds, five profiles, and four personas.
- Event schemas, governance, normalization, workbook semantics, and meaningful tests unless a cited migration proves replacement compatibility.
- Historical prompts/memos as provenance; they lose authority but are not erased.

## Optional deferrals

| Optional item | Deferral | Non-negotiable substitute/boundary |
|---|---|---|
| CalDAV provider | DEFER to community/v2 provider work. | Apple Calendar and Google Calendar capabilities remain; calendar projection cannot defer. |
| Google Drive document source | DEFER. | Apple Notes/Voice/optional Obsidian member Notes path, provider-neutral schemas, and artifacts remain; the Notes function cannot defer. |
| Telegram and Discord channel enablement | DEFER per deployment if not selected. | The scoped channel-bridge seam and at least one credential-free channel contract remain. |
| Twilio/SMS fallback, ElevenLabs, Deepgram, Privacy.com, Lob | DEFER when not configured. | Relevant action/signal interfaces remain explicit, disabled states honest, no required product function disappears. |
| Home Assistant, optional search provider, optional secondary LLM provider | DEFER per household selection. | Cat-E contract, OpenClaw-only model seam, and sensor/action product capability remain tested with fakes. |
| Obsidian member source | DEFER per member; prohibited for kid bridges by default. | Cat-D bridge, provider-neutral Notes contract, and `member_knowledge` capability remain. |
| Evernote, Otter, Fathom, Zoom, and Notion live adapters | DEFER until each provider contract, privacy review, credential-free fixture, and process proof lands. | CO-002 schema/UX remains loss-aware and truthfully not configured; Apple Notes and existing Cat-D sources continue to prove the required Notes function. |
| Microsoft Graph Contacts and CardDAV live enrollment; Apple Contacts and Google People enrollment for any member not yet consented | DEFER per member/source until the exact account, container, OS/OAuth grant, read reconciliation, and privacy review pass in observation. | CO-003 includes provider-neutral schemas, truthful capability matrices, loss-aware adapter packs, fixture proof, private defaults, and the complete People product; no configured/healthy claim or provider write is inferred. |
| Physical legacy-port aliases | DEFER only while a documented migration client needs them. | Product route namespaces and one-process topology remain. |

DEFER is prohibited for privacy/authorization/governance, observation, bootstrap safety, backup/restore, workbook round trip, clean install, the core OpenClaw plugin/four seams, or any required capability listed above.

## Gate 2 disposition

Gate 2 replaced the four product service stubs with router namespaces, replaced Windows-illegal scaffold filenames with portable placeholders, removed synchronous/duck-typed producer emission paths, and stopped treating the Bridge namespace as a separate central process. Historical placeholder files are retained only where later deletion prerequisites are not yet proven. Optional providers stay truthful: an unconfigured adapter is disabled and contributes no false readiness claim.
