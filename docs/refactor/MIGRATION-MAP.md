# Migration map

Classifications describe the Phase 2+ treatment, not Gate 1 edits. **KEEP** retains semantics/code with compatibility coverage; **HARDEN** retains the core but closes a proven seam; **REWRITE** replaces scaffolding or incompatible implementation behind tests; **DEFER** is allowed only for an optional provider; **DELETE** occurs only after its replacement passes.

| Subsystem | First-party paths | Evidence at baseline | Class | Migration requirement |
|---|---|---|---|---|
| Event envelope/log/migrations | `adminme/events/envelope.py`, `log.py`, `migrations/` | SQLCipher append/validation and tests exist | HARDEN | Put behind one append-and-publish port; preserve append-only/schema/encryption; add crash/migration/backup gates. |
| Event schema registry/upcasters | `adminme/events/registry.py`, `schemas/` | Broad typed schema suite and upcaster tests | KEEP | Inventory versions, prohibit destructive shape changes, add migration fixtures and lifecycle outcomes. |
| Durable in-process bus | `adminme/events/bus.py` | durable offsets/retry/degrade exist; checkpoint test fails in baseline | HARDEN | Fix ordering/race in Phase 2; poison isolation, restart, drain; event-log append must notify only after commit. |
| Central composition root | absent | no process instantiates log+bus+all runners/APIs | REWRITE | Create one lifecycle owner with truthful readiness and ordered shutdown. |
| Projection protocol/runner | `adminme/projections/base.py`, `runner.py` | replay/registration mechanisms; default tuple only `lists` | HARDEN | Exact 13 registry, shared bus ownership, per-capability replay/idempotency/status. |
| Parties projection | `adminme/projections/parties/` | schema/handlers/queries/tests | KEEP | Register and prove live/replay/privacy in composition. |
| Interactions projection | `adminme/projections/interactions/` | schema/handlers/queries/tests | KEEP | Register; canonical communication slice/dedupe. |
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
| Adapter protocol/legacy base | `adminme/adapters/base.py` | protocol and supervisor tests | HARDEN | One async emission port; durable status/watermarks/circuit; remove sync/async ambiguity. |
| Five adapter ABCs | `adminme/lib/adapters/` | Cat-A–E contracts and validation tests | KEEP | Preserve categories/verbs/floors/deployment; unify final outbound guard. |
| Adapter pack registry/validator/CLI | `adminme/lib/pack_registry/`, `adminme/cli/commands/adapters*` | useful validation; Windows-invalid `<NAME>` tracked templates | HARDEN | Portable template names/placeholders, all six pack lifecycle stages, atomic rollback/install events. |
| Central adapter supervisor | `adminme/daemons/adapter_supervisor.py` | restart/status primitive; error emit uses ambiguous call shape; not composed | HARDEN | Construct real packs/contexts, retain tasks, durable state, orderly drain, composition status. |
| Bridge package/runtime | `bridge/adminme_bridge/`, `adminme/products/bridge/` | 55 bridge tests pass; central endpoint works; some integration emits drop | HARDEN | Instantiate discovered packs, secure enrollment/auth, owner stamping, retained tasks, central composition namespace. |
| Gmail Cat-A | `packs/adapters/messaging/gmail/` | implementation/tests present | HARDEN | Canonical communication slice; assistant identity modes and OpenClaw delivery outcome. |
| Cat-B calendar/reminder/task packs | `packs/adapters/{calendaring,reminders,tasks}/` | many tests; bridge dual-deployment failures | HARDEN | Durable bridge emit, external reconciliation, explicit observation policy, idempotent writes. |
| Plaid Cat-C | `packs/adapters/financial/plaid/` | inbound code/tests; vendored docs lint noise | KEEP | Compose sandbox-first; redact/privileged scope; authority and workbook proof. |
| Home Assistant Cat-C/E | `packs/adapters/automation/home-assistant/` | multi-capability implementation/tests | HARDEN | Compose; final guard, observation and verified action outcome. |
| Pipeline protocol/loader/runner | `adminme/pipelines/` | reactive subscriptions/tests; stop delegates bus ownership | HARDEN | One bus lifecycle owner, replay/idempotency and poison policy; capability registry. |
| Pipeline supervisor | `adminme/daemons/pipeline_supervisor.py` | prose-only future stub | REWRITE | Delete stub after composition root owns runner lifecycle. |
| Ten pipeline packs | `packs/pipelines/` | typed packs and many unit/integration tests | KEEP | Register production set; close outcome feedback; remove hidden scheduler ownership. |
| Ten skill packs | `packs/skills/` | schemas/prompts/tests present | HARDEN | Pin manifest/schema versions; tenant scan; current OpenClaw invocation contract. |
| OpenClaw skill wrapper | `adminme/lib/skill_runner/` | schema/provenance code/tests; unpinned host and local provider iteration | REWRITE | OpenClaw owns routing/fallback; exact API negotiation; actual provider/model provenance; no direct fallback. |
| Session/scope/privacy | `adminme/lib/session.py`, `scope.py` | strong unit tests for roles/view-as/redaction | HARDEN | Enforce at central middleware and every query; trusted proxy; privileged-access audit and member portal. |
| guardedWrite/rate limiter | `adminme/lib/governance.py` | correct three-layer local order; no full approval/outbound composition | HARDEN | Compose independent approvals, content/privacy/persona and observation gates; all terminal outcomes. |
| Observation | `adminme/lib/observation.py` | on-by-default and suppression code/tests | HARDEN | Persist start/trust window/review prerequisites; complete payload; Cat-B explicit rule; final seam only. |
| Product Core | `adminme/products/core/main.py` | docstring-only stub | REWRITE | Router namespace and real handlers for CoS/what-now/lifecycle. |
| Product Comms | `adminme/products/comms/main.py` | docstring-only stub | REWRITE | Router namespace for operational inbox/proposals/reviews/delivery; no generic chat. |
| Product Capture | `adminme/products/capture/main.py` | stale quick-capture docstring-only stub | REWRITE | Knowledge/CRM/Lists/read/search namespace; delete quick capture/voice/triage claims. |
| Product Automation | `adminme/products/automation/main.py` | docstring-only stub | REWRITE | Finance/signal/action namespace with scoped reads and governed writes. |
| Bridge product | `adminme/products/bridge/` | only implemented FastAPI app; separate `:3337` | HARDEN | Move router into central root; preserve endpoint compatibility/auth; no separate central process. |
| Console runtime | `console/server.js` | comment-only stub; npm test is placeholder echo | DELETE after replacement | Build real family frontend, then remove Node application server; Node remains build-only if used. |
| Console/browser application | `console/` assets to be created | reference mock only | REWRITE | Implement UX contract, server authorization, sanitization, accessibility, browser tests; serve from Python runtime. |
| Reference HTML | `ADMINISTRATEME_CONSOLE_REFERENCE.html` | rich mock with stale flows and unsafe mock rendering; Gate 1 rendered evidence retained | KEEP as design evidence | Never ship as app; preserve valid vocabulary and retained captures, supersede retired flows. |
| OpenClaw plugin placeholders | `adminme/openclaw_plugins/` | README claims Python; empty packages | DELETE after replacement | Replace with one native TypeScript plugin package and exact compatibility pin. |
| Standing-order prose/cron | `bootstrap/openclaw/programs/`, `cron.yaml` | six programs; no installer/reconciler | HARDEN | Version all required orders, install idempotently, detect drift/remove obsolete jobs, record events. |
| Bootstrap sections | `bootstrap/sections/` | README/`__init__` only | REWRITE | Ten-section executable state machine with resume/rollback/encrypted answers/report. |
| Pack install order | `bootstrap/pack_install_order.yaml` | partial, older checkpoint says 1/10 skills | REWRITE | Capability/dependency-driven six-kind plan, exact artifact hashes, atomic install/rollback. |
| Profiles/personas | absent | five/four required built-ins missing | REWRITE | Tenant-neutral packs/compiler/tests; no authority/model policy; exact OpenClaw SOUL path from pin. |
| CLI | `adminme/cli/` | adapter commands and future-command claims | HARDEN | Runtime/Doctor/replay/projection/backup/pack/bootstrap commands that call real composition services. |
| Instance configuration | `adminme/lib/instance_config.py`, `config/channels.yaml` | path resolver and one config exist | HARDEN | Versioned schema, secret references, port/instance isolation, no hardcoded household/path. |
| Health/Doctor | scattered status methods; no composed Doctor | incomplete | REWRITE | Capability registry, freshness/circuits/pin/backup/privacy diagnostics, machine JSON and human remediation. |
| Backup/restore | no complete first-party implementation | required, absent | REWRITE | Encrypted, consistent, verified restore and replay on fresh path. |
| Build/package system | root/bridge Poetry, console npm; no Makefile | Python deps use broad constraints; Poetry absent locally; Node pin clear | HARDEN | Reproducible locked install, supported scripts, package checks, exact OpenClaw/frontend pins. |
| CI | `.github/workflows/` | executor orchestration/constitutional sync/smoke, no ordinary PR quality pipeline | REWRITE | Normal untrusted-PR-safe lint/type/test/acceptance matrix; no secrets; macOS release rehearsal job. |
| Tests | `tests/`, `bridge/tests/`, pack tests | 1,170 collected before errors; broad isolated coverage | KEEP/HARDEN | Preserve meaningful tests, fix portability/races, add process gates, forbid false placeholders. |
| Historical prompts/build logs | `prompts/`, `docs/build_log.md`, handoffs/memos | useful provenance but stale future instructions | KEEP as history | Exclude from governing instruction chain; link from current contracts only when relevant. |
| Vendored provider references | `docs/reference/` | large snapshots create lint noise and API staleness | DEFER refresh per optional integration | Exclude from runtime/lint or validate separately; never use as floating current contract. |

No production file is changed by this Gate 1 classification.
