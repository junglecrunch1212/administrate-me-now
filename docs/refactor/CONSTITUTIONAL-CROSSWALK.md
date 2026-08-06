# Constitutional crosswalk

Personal Google OAuth amendment (2026-08-06): C035/C046/C050 are
**MODERNIZED** so the three central Google adapters can resolve independently
authorized installed-user credentials from the instance-bound macOS Keychain.
This **PRESERVES** secret-free configuration, least-privilege Gmail read scope,
event-log truth, per-member ownership, observation-mode write suppression, and
the allowlist/governance/privacy ordering for eventual provider writes.

Live activation amendment (2026-08-05): C046/C080/C082 are **HARDENED** by an
authenticated probe of the AdministrateMe-owned Gateway, truthful launchd race
handling, and backup treatment of generated OpenClaw discovery links as cache.
This preserves Keychain secrecy, fail-closed service health, safe extraction,
and event/projection state as the recoverable source of truth.

Mac bootstrap compatibility amendment (2026-08-05): C046/C050/C053 preserve
their semantics while the installer moves to Homebrew 6 metadata, isolates
Poetry from its synchronized target environment, recognizes only its own
pre-wizard scaffold, explicitly trusts the exact reviewed local plugin, and
registers dashboard ownership during pinned OpenClaw discovery. This
**MODERNIZES** package/runtime integration and **PRESERVES** source review,
Doctor truthfulness, OpenClaw scheduler ownership, and fail-closed bootstrap.

Primary-surface release amendment (2026-08-05): C035 and C037 now have
executable bidirectional implementations for central Google Calendar and
per-member Apple Calendar, Reminders, and Notes. Provider-native fields stay at
adapter boundaries; canonical events preserve provenance and owner scope;
calendar attendance/change and reminder completion feed typed projections and
outcomes. Central runtime adapter configuration is passed into the production
supervisor, and failed central/bridge ingest prevents local sync advancement.
This **MODERNIZES** the transport and projection coverage while
**PRESERVING** external authority, event-log truth, observation, governance,
and separate calendar/task/list/recurrence models. Real provider consent and
propagation remain AC-21 live evidence, not a credential-free code claim.

Gate 8 release audit (2026-08-04): all 96 rows (C001–C096) were reread
against the complete `main...codex/administrateme-v2-runtime` diff. Every row
maps to an executable AC-00–AC-23 gate in `ACCEPTANCE-CONTRACT.md`; the final
command, coverage, security, clean-room, performance, and live-boundary
evidence is recorded in `FINAL-VALIDATION-REPORT.md`. The only `DEFER`
disposition remains C085 and names optional providers, never a core function.

Bootstrap remediation update (2026-08-04): C046/C053/C061/C079/C080/C086 now
include exact-source OpenClaw provenance, owned cron reconciliation, separate
bridge authorization state, short-lived encrypted transfer, per-member
LaunchAgent acceptance, owner-scoped ingest proof, finalization/revocation, and
the non-programmer read-along. Credential-free tests do not replace the
independent merge or disposable-Mac live gate.

Gate 6 implementation evidence (2026-08-03): the baseline columns below are
historical observations. Current executable evidence preserves C001–C090 while
converging implementation ownership: six product namespaces; thirteen
projections; Cat-A–E; two bidirectional workbooks; eleven domain pipeline packs
split into six reactive runtime subscriptions and five OpenClaw standing
orders; ten skills; five profiles; four personas; scoped member knowledge and
domain search; typed notification/result feedback; contextual what-now; and a
scoped activity feed. Generic conversation memory, model routing, channel
delivery, and scheduling remain OpenClaw responsibilities. Evidence:
`docs/refactor/evidence/gate-6-domain-convergence.md`.

Gate 5 implementation evidence (2026-08-03): AC-03 and AC-12 remain intact. AC-01/15 now prove the encrypted ten-section bootstrap, fixed scripts, Doctor canonical loop, user launchd, secure references, backup/restore replay, and bridge enrollment restrictions. The authoritative new process test is `tests/acceptance/test_macos_bootstrap_operations.py`; AC-04 member-private knowledge is Phase 6.

No row may be removed merely because its current implementation is absent. A future semantic change updates the row, governing ADR, evidence, and executable gate in the same PR.

Disposition meanings:

- **PRESERVE:** semantics and public behavior remain.
- **MODERNIZE:** implementation/topology may change while compatibility is proven.
- **SUPERSEDE:** named stale behavior is replaced by the cited decision.
- **DEFER:** optional provider only; required capabilities cannot use this disposition.

Source abbreviations: **VISION** = WHAT-IS; **BUILD** = ADMINISTRATEME_BUILD; **PATTERNS** = CONSOLE_PATTERNS; **HTML** = CONSOLE_REFERENCE; **DIAGRAMS** = ADMINISTRATEME_DIAGRAMS; **FIELD** = FIELD_MANUAL; **EXAMPLES** = REFERENCE_EXAMPLES; **SEQ** = PROMPT_SEQUENCE; **MENTAL** = docs/00-mental-model; **CLAW** = openclaw-cheatsheet; **ARCH** = architecture-summary.

## Product, domain, and data

| ID | Material promise | Governing source/section and conflict | Baseline implementation/test evidence | Disposition | Release gate |
|---|---|---|---|---|---|
| C001 | Household Chief of Staff | VISION “What it is for”; BUILD opening thesis | Partial schemas/pipelines; no composed product | PRESERVE | AC-02, AC-06 |
| C002 | ADHD cognitive neuroprosthetic | VISION “speaks ADHD”; BUILD thesis/profile engine | Pipeline/profile prose only; no family runtime | PRESERVE | AC-06, AC-17 |
| C003 | Household/professional CRM | VISION thesis; BUILD L3 CRM; ARCH §4 | parties/interactions/artifacts/commitments code exists | PRESERVE | AC-03, AC-04 |
| C004 | Unified communications | VISION thesis; BUILD Comms | Gmail/pipeline assets; Comms API and plugin absent | PRESERVE | AC-03, AC-12 |
| C005 | Working memory/member knowledge | VISION thesis; BUILD D17; ARCH §§4/9 | `member_knowledge` absent; Cat-D bridge incomplete | PRESERVE | AC-04, AC-08 |
| C006 | Sensor/signal/action layer | VISION thesis; BUILD adapters/Automation | Plaid, calendars, lists, HA assets present; not composed | PRESERVE | AC-07, AC-10 |
| C007 | Closed observe-to-outcome-to-next-action loop | VISION mental model; FIELD daily use; DIAGRAMS flows | No process test closes loop | PRESERVE | AC-03, AC-06 |
| C008 | now/hour/daypart/day/week/longer horizons | FIELD workflows; BUILD Core/what-now; HTML Today | No what-now API/engine | PRESERVE | AC-06 |
| C009 | CRM spine: Parties, Identifiers, Memberships, Relationships, Interactions, Commitments, Artifacts | BUILD L3; ARCH §4 | Schemas/projections partly present; vertical slice incomplete | PRESERVE | AC-03, AC-04 |
| C010 | Event fact, calendar event, task, commitment, list item, recurrence, scheduler job remain distinct | BUILD projection semantics and D18; ARCH §4; DIAGRAMS | Separate schemas exist for several; no global semantic gate | PRESERVE | AC-05 |
| C011 | `parties` capability | BUILD §3.1; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-07 |
| C012 | `interactions` capability | BUILD §3.2; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-07 |
| C013 | `artifacts` capability | BUILD §3.3; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-07 |
| C014 | `commitments` capability | BUILD §3.4; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-03, AC-07 |
| C015 | `tasks` capability | BUILD §3.5; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-03, AC-07 |
| C016 | `recurrences` capability | BUILD §3.6; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-05, AC-07 |
| C017 | `calendars` capability | BUILD §3.7; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-05, AC-07 |
| C018 | `places_assets_accounts` capability | BUILD §3.8; ARCH §4 | combined code/schema present, not default-registered | PRESERVE | AC-07 |
| C019 | `money` capability | BUILD §3.9; ARCH §4 | code/schema present, not default-registered | PRESERVE | AC-07 |
| C020 | `vector_search` capability, non-privileged and scope-filtered | BUILD §3.10/privacy; ARCH §4 | code/tests present, not default-registered | PRESERVE | AC-07, AC-08 |
| C021 | `xlsx_workbooks` capability | BUILD §3.11/xlsx; ARCH §4 | substantial forward/reverse code; `fcntl` blocks Windows paths | PRESERVE | AC-13 |
| C022 | `member_knowledge` capability | BUILD D17/member bridge; ARCH §4 | missing | PRESERVE | AC-04, AC-07 |
| C023 | `lists` capability, list item not task | BUILD LISTS/D18; ARCH §4 | code/schema present; only default projection class | PRESERVE | AC-05, AC-07 |
| C024 | Thirteen, not twelve, projection capabilities | MENTAL says 12; BUILD/ARCH later add lists | Default tuple contains one; old test says ten | SUPERSEDE old counts; PRESERVE 13 | AC-07 |
| C025 | Deterministic rebuild from event zero | MENTAL core rule; BUILD L2/L3; DIAGRAMS replay | projection runner supports per-projection replay; complete registry absent | PRESERVE | AC-07, AC-09 |
| C026 | Typed lifecycle feedback: complete/defer/snooze/block/correct/approve/suppress/deliver | FIELD operation; BUILD schemas/pipelines; DIAGRAMS loops | subset of events/schemas exists | PRESERVE | AC-03, AC-05 |

## Runtime, delivery, adapters, and extensions

| ID | Material promise | Governing source/section and conflict | Baseline implementation/test evidence | Disposition | Release gate |
|---|---|---|---|---|---|
| C027 | Five dependency-ordered layers | MENTAL; VISION architecture; DIAGRAMS | directories exist; no composition root | PRESERVE | AC-02 |
| C028 | Local-first, one household hub, multi-member code | VISION; MENTAL; BUILD critical rules | InstanceConfig/scope primitives exist | PRESERVE | AC-01, AC-16 |
| C029 | SQLCipher append-only typed log with owner scope/provenance | BUILD L2; MENTAL; DIAGRAMS | `adminme/events/log.py` and trigger/schema tests | PRESERVE/HARDEN | AC-07, AC-08 |
| C030 | Commit append before publish; durable bus offsets | BUILD L2; MENTAL; DIAGRAMS | log and bus exist, but no append-notify composition | MODERNIZE | AC-02, AC-09 |
| C031 | At-least-once, idempotent consumers, poison handling | BUILD bus; PATTERNS degraded; DIAGRAMS | bus retries/degrades; checkpoint baseline test fails intermittently | PRESERVE/HARDEN | AC-09, AC-14 |
| C032 | Replay/restart/recovery and orderly shutdown | BUILD final checks; DIAGRAMS; FIELD recovery | isolated runner/bridge shutdown; central composition missing | MODERNIZE | AC-09, AC-15, AC-20 |
| C033 | Durable adapter watermarks and circuit state | BUILD adapters; selected Vellum pattern | some adapter status/cursors; no uniform durable circuit contract | MODERNIZE | AC-10, AC-14 |
| C034 | Cat-A communication | BUILD adapter framework D19; VISION taxonomy | Gmail exists; OpenClaw channel plugin missing | PRESERVE | AC-03, AC-10 |
| C035 | Cat-B external-state mirror and external authority | BUILD D18/D22; VISION taxonomy | Google/Apple Calendar and Apple Reminders expose bidirectional supported fields, lists, recurrence, alarms/geofences, completion/change feedback, durable reconciliation, and production supervisor wiring | PRESERVE/HARDEN COMPLETE IN CODE; LIVE PROVIDER EVIDENCE PENDING | AC-05, AC-10, AC-11, AC-21 |
| C036 | Cat-C inbound data | BUILD adapter framework | Plaid present | PRESERVE | AC-10 |
| C037 | Cat-D personal knowledge, bridge-private | BUILD D17/D19; ARCH §4 | owner-scoped member knowledge plus bidirectional Apple Notes folders/notes through per-member bridge commands and durable idempotent results | PRESERVE/HARDEN COMPLETE IN CODE; LIVE NOTES CONSENT PENDING | AC-04, AC-10, AC-21 |
| C038 | Cat-E outbound action | BUILD D19; adapter framework | Home Assistant present | PRESERVE | AC-10, AC-11 |
| C039 | Multi-capability adapters, explicit verbs, sensitivity floors, sharing models, central/bridge/dual runtime | BUILD adapter framework/amendments; EXAMPLES shapes | manifest/validator code and tests present; composition gaps | PRESERVE | AC-10 |
| C040 | Six pack kinds: adapter, pipeline, skill, projection, profile, persona | VISION pack table; BUILD pack system; EXAMPLES | adapter/pipeline/skill assets only | PRESERVE | AC-17 |
| C041 | Manifest validation, dependencies, staging, tests/build, atomic install, rollback, install events | BUILD pack registry; DIAGRAMS install; EXAMPLES | adapter validation and partial loaders; full lifecycle absent | MODERNIZE | AC-17 |
| C042 | Five built-in profile behaviors | BUILD “5 built-in profiles”; EXAMPLES §6 | no built-in profile directories/compiler | PRESERVE | AC-17 |
| C043 | Four built-in persona archetypes and compiled voice/theme boundaries | BUILD “4 built-in personas”; EXAMPLES §7 | no built-in persona directories/compiler | PRESERVE; sanitize exemplar | AC-17 |
| C044 | Progressive capability revelation, explicit empty/disabled/degraded states | Selected Vellum patterns; HTML/BUILD UI | mock demonstrates some states; runtime absent | MODERNIZE | AC-14, AC-18 |
| C045 | Evidence-rich activity; notification signal/decision/delivery separation | Selected Vellum patterns; BUILD events | partial event types/pipelines | MODERNIZE | AC-03, AC-18 |
| C046 | Capability registry and useful Doctor | BUILD pack/bootstrap/final checks; selected Vellum pattern | Gate 5 Doctor checks pins, encryption/migrations, exact projections/checkpoints, workbooks, adapters, bridge/launchd/backup/observation, and the canonical round trip with a safe JSON export | MODERNIZE COMPLETE FOR BOOTSTRAP | AC-01, AC-14 |
| C047 | Do not import Vellum runtime/memory/scheduler/workflow/gateway/builder | Refactor contract reconciliation | no Vellum runtime dependency found | PRESERVE prohibition | AC-22 |

## OpenClaw, LLM, privacy, and governance

| ID | Material promise | Governing source/section and conflict | Baseline implementation/test evidence | Disposition | Release gate |
|---|---|---|---|---|---|
| C048 | OpenClaw owns generic channels/conversations/sessions/memory/model routing/cron/orchestration/tools/nodes/admin | BUILD OpenClaw; CLAW; ARCH §5 | boundary documented; generic duplicates not implemented | PRESERVE | AC-12, AC-22 |
| C049 | AdministrateMe owns household truth, scopes, review, outcomes, workbooks | BUILD/MENTAL/ARCH | domain primitives partial | PRESERVE | AC-02–AC-18 |
| C050 | One current native TypeScript plugin | Current OpenClaw docs + v2 contract; baseline README says Python | Gate 4 exact-pinned package, manifest, build and fixture/process tests; Python placeholders deleted | SUPERSEDE COMPLETE (live Mac pending) | AC-12 |
| C051 | Typed skills seam | BUILD skill runner; CLAW Q1; EXAMPLES skill shape | OpenClaw-format skill plus one-call schema wrapper and durable actual provenance | PRESERVE/HARDEN COMPLETE | AC-12 |
| C052 | Slash-command seam | BUILD products/OpenClaw; CLAW | seven authenticated native commands dispatch to typed handlers | PRESERVE COMPLETE | AC-12 |
| C053 | Proactive standing-order seam | BUILD L4/bootstrap; CLAW Q3 | five cross-checked OpenClaw declarations plus owned, timezone-explicit, idempotent reconciliation, duplicate/drift refusal, rollback, and Doctor evidence; live Mac RPC pending | PRESERVE/HARDEN COMPLETE IN CODE | AC-12, AC-21 |
| C054 | Scoped channel/conversation bridge; generic history stays OpenClaw | BUILD D6/D17; ARCH | explicit consent/channel/event allowlist with full provenance; generic and privileged turns excluded | PRESERVE COMPLETE | AC-03, AC-12 |
| C055 | Every LLM call through `llm-task` with versioned schema/provenance/correlation/causation | BUILD L4; ARCH §5; CLAW; current official docs | exact-pinned one-call wrapper; no provider override/fallback; actual provenance recorded | MODERNIZE COMPLETE | AC-12 |
| C056 | Deterministic logic stays code; LLM state change is propose-confirm only | BUILD pipeline/governance; EXAMPLES | some pipelines follow proposal pattern | PRESERVE | AC-03, AC-11 |
| C057 | Identity-first separation and per-channel-peer scope | BUILD privacy/OpenClaw; PATTERNS session | session/scope code/tests exist; central edge missing | PRESERVE | AC-08 |
| C058 | `authMember`/`viewMember`, owner_scope, visibility/sensitivity | PATTERNS §2/§6; BUILD privacy; DIAGRAMS | session/scope code/tests exist | PRESERVE | AC-08 |
| C059 | Privileged exclusion from LLM/vector/cross-owner processing and audited access | BUILD privacy; PATTERNS privacy filter | wrapper/vector/scope tests partial; audit surface absent | PRESERVE | AC-08 |
| C060 | Child server-side denial and narrow member portal | PATTERNS §7; HTML child mode; BUILD UI | mock/client patterns only | PRESERVE | AC-08, AC-18 |
| C061 | Trusted Tailscale edge, not arbitrary header trust | PATTERNS §1 plus v2 security reconciliation | central and member workflows require private HTTPS `.ts.net` Serve origin; member self identity must match the encrypted package; central owner scope is resolved from separate enrollment state; spoof/cross-owner tests fail closed | MODERNIZE COMPLETE IN CODE; LIVE EDGE PENDING | AC-08, AC-19, AC-21 |
| C062 | guardedWrite order: allowlist -> governance -> sliding rate limit | PATTERNS §§3–4; BUILD authority/governance | Python implementation has these three | PRESERVE/HARDEN | AC-11 |
| C063 | Independent OpenClaw generic and AdministrateMe domain approvals | BUILD §8; PATTERNS approvals | optional tool exposure plus `allow-once` hook; AdministrateMe independently enforces role/scope/domain approval and exact-once retry | PRESERVE COMPLETE FOR GATE 4 | AC-11, AC-12 |
| C064 | assistant-send allowed; draft-for-principal review; auto-send-as-principal prohibited | BUILD critical rules/governance | no end-to-end delivery path | PRESERVE | AC-11 |
| C065 | Final content/privacy/persona guard | BUILD outbound/privacy/persona; FIELD | adapter observation wrappers exist; unified guard absent | PRESERVE | AC-11 |
| C066 | Every terminal write/approval/suppression/delivery outcome audited | PATTERNS guardedWrite; BUILD events | guardedWrite records denials/reviews, not full composed outcomes | PRESERVE/HARDEN | AC-11 |
| C067 | Observation starts on, stays 5–7 days, internal work continues, complete would-have payload recorded | BUILD observation plus v2 duration | observation helper exists; no bootstrap/trust-period gate | PRESERVE/HARDEN | AC-11 |
| C068 | Cat-B observation exception requires explicit reconciliation | Later BUILD amendment versus broad observation | baseline Cat-B wrappers suppress; policy ambiguous | SUPERSEDE ambiguity with fail-closed ADR-0000 rule | AC-05, AC-11 |

## Workbooks, UX, installation, and operations

| ID | Material promise | Governing source/section and conflict | Baseline implementation/test evidence | Disposition | Release gate |
|---|---|---|---|---|---|
| C069 | `adminme-ops.xlsx` and `adminme-finance.xlsx` are bidirectional, rebuildable projections | BUILD xlsx; DIAGRAMS; FIELD manual workflows | forward/reverse/sidecar code; platform-specific failures | PRESERVE/HARDEN | AC-13 |
| C070 | Protected/derived cells, reverse-diff events, sidecar, locks, conflict visibility, Plaid authority, replay equivalence | BUILD xlsx contract; EXAMPLES | substantial tests, some blocked/failing on Windows | PRESERVE/HARDEN | AC-13 |
| C071 | Today, Inbox, CRM, Capture/Knowledge, Finance, Calendar, Scoreboard, Settings | BUILD L5; HTML; ARCH §9 | HTML mock only; `console/server.js` stub | PRESERVE; rename semantics only | AC-18 |
| C072 | Carousel/compressed/child modes | BUILD profiles; HTML; ARCH §8 | HTML reference rendered and exercised by Gate 1 harness; production app absent | PRESERVE | AC-18 |
| C073 | Calendar privacy redaction | PATTERNS §6; HTML | Python scope tests; no real route/UI | PRESERVE | AC-08, AC-18 |
| C074 | Dual-path reward toast with dedupe | PATTERNS §8; HTML; BUILD | reward pipeline assets; no UI/SSE | PRESERVE | AC-18 |
| C075 | Observation/degraded banners and evidence/provenance views | PATTERNS §§9/11; HTML; selected Vellum | mock only | PRESERVE/MODERNIZE | AC-14, AC-18 |
| C076 | Responsive/accessibility behavior | BUILD L5; HTML visual contract | exact reference rendered at desktop/narrow viewports with keyboard tab activation; production app/tests absent | PRESERVE | AC-18 |
| C077 | Quick capture/voice ingest/triage retired; Knowledge read surfaces remain | ARCH §9/D17 overrides older BUILD/HTML | product stub still claims old flow | SUPERSEDE old mutations | AC-04, AC-18 |
| C078 | One central Python process plus pinned OpenClaw; product ownership/routes compatible | v2 contract reconciles MENTAL/BUILD multi-service ports | only bridge API implemented; no composition root | MODERNIZE | AC-02, AC-19 |
| C079 | Ten-section bridge-aware bootstrap | Later BUILD bootstrap §10 overrides nine-count introductions | Encrypted ten-section state machine plus selected-member package lifecycle, 30-minute transfer, member accept, event proof, finalize/revoke, and explicit non-Apple skip | SUPERSEDE nine-count; PRESERVE 10 COMPLETE | AC-01, AC-21 |
| C080 | Bootstrap resumable, idempotent, least-privilege, secret-safe, observation-first | BUILD bootstrap; DIAGRAMS state machine; FIELD install | Version/input-digest journal, original encrypted answers on resume, Keychain references, verified GitHub source receipt, central/member user LaunchAgents, transactional cron/bridge changes, observation briefing, interruption/resume and rollback tests | PRESERVE COMPLETE FOR INSTALLER | AC-01, AC-21 |
| C081 | Phase A disposable lab; Phase B only after reviewed merge | FIELD deployment; BUILD phases; v2 contract | process rule only | PRESERVE | AC-21 |
| C082 | Backup/restore, migration/upcasters, degraded operation | BUILD final checks; FIELD recovery; DIAGRAMS | Gate 5 implements authenticated backup, staging validation, rollback copy, 13-projection/workbook replay; broader legacy migration/degraded matrices remain | PRESERVE/HARDEN PARTIAL | AC-14, AC-15, AC-20 |
| C083 | Static tenant-identity and instance-path scans | BUILD security/final tree; EXAMPLES tenant warning | invariant script has some checks; named identity canary absent | PRESERVE | AC-16 |
| C084 | Two-instance isolation canary | BUILD multi-tenant rules; v2 contract | scoped unit tests, no two-live-instance process test | PRESERVE | AC-16 |
| C085 | Optional provider deferral only | BUILD integrations/options; SEQ roadmap | several providers unbuilt | DEFER only CalDAV/Drive/optional channels/providers | AC-22 |
| C086 | Clean macOS lab rehearsal before family install | FIELD phases; BUILD bootstrap; v2 contract | Gate 5 supplies macOS CI/rehearsal scripts and a live checklist; real grants and family activation remain unclaimed | PRESERVE IMPLEMENTED CONTRACT; LIVE EVIDENCE PENDING | AC-21 |
| C087 | Prompt sequence is historical ordering, not current truth | SEQ entire roadmap versus merged baseline and v2 ADRs | main stops around 11b-ii-β-β; later prompts absent | SUPERSEDE execution order; preserve dependency evidence | AC-02 |
| C088 | Reference examples preserve typed pack shape, tests, version/provenance, not stale APIs or tenant data | EXAMPLES preamble and §§1–7 | many pack shapes implemented; examples contain exemplar data | MODERNIZE/sanitize | AC-16, AC-17 |
| C089 | Field manual workflows remain operable through v2 UX | FIELD daily/weekly/admin/recovery workflows | no running console/product APIs | PRESERVE | AC-03, AC-06, AC-18, AC-20 |
| C090 | Diagrams explain relationships but never override prose | DIAGRAMS; source precedence rule | used as trace evidence only | PRESERVE rule | AC-22 |
| C091 | Authenticated non-admin adults can use the household CoS without operator access | Gate 7 experience contract; C048/C054 boundary | Signed per-member protocol-v4 relay, isolated OpenClaw history, no ordinary-chat domain event, exact explicit-tool audit | PRESERVE COMPLETE | AC-23 |
| C092 | Child chat is guardian-configurable with a narrow independent tool/data allowlist | Gate 7 item 7A; C060 | Disabled unless allowlisted; state/what-now/own-task lifecycle only; adult actions/review/correction/skills/bridge/notification/model/admin denied | PRESERVE COMPLETE | AC-08, AC-23 |
| C093 | Temporary quests are disposable data-only lenses over stable household truth | Gate 7 item 7B; anti-slop rule | Atomic schema v1 config, five named reads/actions, private owner binding, reviewed promotion, child compatibility, expiry/archive, typed audit | PRESERVE COMPLETE | AC-23 |
| C094 | Temporary surfaces add no Project aggregate, projection, scheduler, workflow, executable code/SQL/template, copied data, transcript, or raw-log access | Gate 7 item 7B; C024/C047/C048 | Negative validation plus exact 13-projection and unchanged task/list/calendar/finance counts | PRESERVE prohibition COMPLETE | AC-22, AC-23 |
| C095 | Eight surfaces and three modes use server authorization and retained visual vocabulary | Gate 7 experience contract; C071/C072/C076 | OpenClaw nine read bindings; Python adult/child/device allowlists; 17 Edge captures at phone/tablet/desktop/reduced-motion states | PRESERVE COMPLETE | AC-18 |
| C096 | Model/chat loss is explicit while deterministic domain views remain usable | Gate 7 items 6/7A; C044/C046 | Separate chat policy states, Settings pills/reasons, Doctor fields, degraded rendered process proof | MODERNIZE COMPLETE | AC-14, AC-18, AC-23 |

## Source coverage index

## Gate 2 traceability update

The runtime/delivery rows in this crosswalk are now backed by `adminme/events/writer.py`, `adminme/runtime/application.py`, the explicit registry in `adminme/projections/runner.py`, product routers under `adminme/products/`, and the process-boundary acceptance test in `tests/acceptance/test_runtime_spine.py`. This closes composition and delivery plumbing only. Rows requiring a communication-to-reviewed-commitment loop, member bridge authorization/search isolation, full workbook round trip, browser behavior, bootstrap, backup, or release rehearsal remain governed by their later acceptance gates.

| Source | Material sections covered by rows |
|---|---|
| VISION | product thesis C001–C008; architecture C027–C028; projections C011–C024; adapters/packs C034–C043; bootstrap C079–C080 |
| BUILD | layers/log/bus/projections C009–C033; adapters/pipelines/OpenClaw C034–C068; xlsx/UI/packs/bootstrap/operations C069–C086 |
| PATTERNS | identity/session C057–C061; guarded writes C062–C068; UI privacy/rewards/degraded/observation C071–C076 |
| HTML | surface/navigation/modes/interactions C071–C077; superseded mock flows C077; rendered Gate 1 evidence in `docs/refactor/evidence/console-reference/` |
| DIAGRAMS | dependency/event/replay/control/install/bootstrap relationships C007, C010, C025, C027, C030–C032, C041, C069, C079–C082, C090 |
| FIELD | daily/weekly/administrative/recovery/deployment workflows C007–C008, C026, C065, C069, C081–C082, C086, C089 |
| EXAMPLES | adapter/pipeline/skill/profile/persona pack shapes C039–C043, LLM proposal patterns C051/C056, xlsx fixtures C070, tenant-example rule C083/C088 |
| SEQ | historical dependencies/status C087 and optional-provider sequencing C085; no prompt is accepted as runtime proof |
| MENTAL | system summary/layers/local-first/replay C024–C030; conflicts explicitly reconciled in ADR-0000 |
| CLAW | install/discovery, plugins, skills, commands, cron, sessions C048–C055; floating details require exact host pin |
| ARCH | later D17/D18/D22 resolutions, 13 projections, product ownership, profiles/personas, bootstrap C009–C024, C042–C055, C071–C080 |
