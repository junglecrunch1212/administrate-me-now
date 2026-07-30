# ADR-0000: Constitutional reconciliation

- Status: Accepted for Gate 1
- Date: 2026-07-29
- Scope: semantics and first-Mac deployment topology
- Baseline: `64192f465606d0bcf92f5137cb0c1f0e4c729297`

## Decision

AdministrateMe v2 preserves the household product and its domain contracts while modernizing the central deployment into one long-lived Python composition root. It does not authorize production deletion in Gate 1. Every later change must prove semantic compatibility through `ACCEPTANCE-CONTRACT.md` and update the crosswalk and handoff.

The governing order is:

1. The immutable architecture contract supplied for this refactor.
2. This ADR and later explicitly accepted v2 ADRs.
3. The source-specific precedence in the refactor brief: `docs/00-mental-model.md` for an unresolved conflict; BUILD for product/data; CONSOLE_PATTERNS for implementation/security; the reference HTML for demonstrated visual/interaction behavior; REFERENCE_EXAMPLES for pack shape; diagrams for explanation only.
4. Within a source, a clearly labeled later amendment supersedes the older text it amends.
5. Code and tests are evidence of current behavior, not authority to erase a constitutional promise.

## Source ledger

All eleven files were read from start to finish at the baseline. SHA-256 makes the reviewed inputs reproducible.

| Source | Bytes | Lines | SHA-256 |
|---|---:|---:|---|
| `WHAT-IS-ADMINISTRATEME.md` | 47,915 | 658 | `f05121456638f0399a2bf78753e2012c4fa76a3845c99b03e62983543d410fdd` |
| `ADMINISTRATEME_BUILD.md` | 281,114 | 4,155 | `92f0267e8d1c37e9105f53d234e94f0b0b55c38ef462459edc41812ad4236293` |
| `ADMINISTRATEME_CONSOLE_PATTERNS.md` | 84,531 | 1,836 | `3712df63ec9d46fa1d72ead52117ae87841eee96ccbb0b5a175721df713bd840` |
| `ADMINISTRATEME_CONSOLE_REFERENCE.html` | 137,221 | 3,266 | `98ac268f39c0d367f5c904ab2af5e0e35389eae8ff6bffd4cede7ef8bfdd0a3b` |
| `ADMINISTRATEME_DIAGRAMS.md` | 105,192 | 1,515 | `9c4bb29670804c52c8b7970652ddb587fe8cdd3210e415e8f2ea6d29c9845eae` |
| `ADMINISTRATEME_FIELD_MANUAL.md` | 53,848 | 864 | `8a1ecb486e506331483488680087ad9cbd437e5d307d8e42c7928a8c80455759` |
| `ADMINISTRATEME_REFERENCE_EXAMPLES.md` | 115,259 | 2,938 | `dc11b8ffef78f277a25462afb2cc8eaa692c498e84e75a72c80ef3e03b1f233c` |
| `prompts/PROMPT_SEQUENCE.md` | 60,248 | 410 | `83837285ea21aea0cc73af941456605788dec635ab94dd677284936af7158667` |
| `docs/00-mental-model.md` | 24,923 | 249 | `448842bf4a7b75158abc18ae53f2873e32c64c346cca44ad344cb078ec7a3c08` |
| `docs/openclaw-cheatsheet.md` | 8,987 | 76 | `02823dbc862cf849d427583831ff66a4d39eb68c02a402a196ae84fa3673aa0d` |
| `docs/architecture-summary.md` | 51,872 | 235 | `1217131f3912bd50511e5cd57cc29b6786a472bd3624e3501e9e2d490a4f0e6a` |

## Approved reconciliations

| Conflict | Decision | Reason and evidence |
|---|---|---|
| 12 projections in the mental model versus later lists amendment | Thirteen capabilities are binding: parties, interactions, artifacts, commitments, tasks, recurrences, calendars, places_assets_accounts, money, vector_search, xlsx_workbooks, member_knowledge, lists. | BUILD §3 plus its LISTS amendment and architecture-summary §4 are later and more specific. `member_knowledge` and `lists` are both required. |
| Nine bootstrap sections versus bridge amendment | Ten sections are binding; §10 enrolls per-member bridges. | BUILD “BOOTSTRAP WIZARD” adds §10 after the nine-section introduction. The older count is stale. |
| Capture quick-entry/voice/triage versus personal-data amendment | Capture is a read surface for Knowledge, CRM, Lists, recipes, and search. Quick-capture prefix routing, direct voice ingest, and triage are superseded. | Architecture-summary §9 records D17; personal knowledge is captured in member-owned tools and arrives through Cat-D bridges. The console may keep the visual Capture/Knowledge destination, not the retired mutation flow. |
| Four Python services and varying ports versus v2 topology | Product ownership and route namespaces remain; central deployment becomes one Python process served at loopback `:3330` behind Tailscale Serve. OpenClaw remains a separately pinned gateway at loopback `:18789`. | This is a deployment modernization expressly permitted by the refactor contract. Compatibility tests must pin route behavior. Member bridges remain separate edge daemons. |
| Node console as runtime versus one AdministrateMe process | Browser assets may be built with Node, but the central runtime serves them and all API namespaces from the Python composition root. | Keeps one AdministrateMe process without changing family surfaces or server-side authorization. Framework is not constitutional; behavior is. |
| Python OpenClaw plugins in scaffold/readme versus current native plugins | Replace with one current-format TypeScript plugin package with `openclaw.plugin.json`, package entrypoints, strict config schema, and an exact OpenClaw compatibility pin. | Current OpenClaw SDK documentation (reviewed 2026-07-29) requires native manifests and TS/JS entrypoints; the baseline directories contain only Python placeholders. |
| `/skills/invoke` or direct provider calls in old prose/examples versus ADR-0002 seam | One `llm-task` tool seam is binding. No direct provider SDK and no independent fallback router in AdministrateMe. | Architecture-summary §5 and current OpenClaw `llm-task` documentation. The baseline wrapper’s provider iteration is a rewrite target because OpenClaw owns routing/fallback. |
| AdministrateMe jobs, APScheduler, cron snippets versus OpenClaw scheduling | Proactive time-based work is registered with OpenClaw. AdministrateMe may use bounded internal timers only for debounce, retry, health, and transactional housekeeping—not product scheduling. | BUILD §OpenClaw integration and cheatsheet Q3. A recurrence remains domain meaning, not a cron job. |
| Three-layer guardedWrite versus OpenClaw/domain approval and content guard | Keep strict AdministrateMe order `action allowlist -> governance allow/review/deny/hard-refuse -> per-action sliding rate limit`. OpenClaw generic approval and AdministrateMe domain approval are independent prerequisites. The final outbound seam then applies content/persona/privacy and observation gates. Every terminal result is an event. | CONSOLE_PATTERNS §§3–4 governs the three ordered layers; the refactor contract adds the independent approvals and final guard without reordering them. |
| Observation applies broadly versus later Cat-B user-write language | Default observation suppresses every external side effect while internal log/projections/UI continue for a documented 5–7 day period. A user-initiated Cat-B mirror write may bypass suppression only after a named ADR, explicit session-intent rule, complete audit event, and an acceptance test. Until then it is suppressed. | Fail-closed reconciliation avoids guessing. Externally authoritative Cat-B inbound observation is always allowed; outbound mutation is the disputed case. |
| Tailscale identity header trusted by examples | Trust only the header delivered through a configured local Tailscale Serve boundary; strip/reject client-supplied identity headers before resolution. | CONSOLE_PATTERNS owns security behavior, but “trust absolutely” assumes a correctly isolated proxy. The v2 composition root must enforce that assumption. |
| REFERENCE_EXAMPLES uses household exemplars and Python-looking plugins | Preserve typed manifests/schemas/fixtures/provenance and tenant-specific pack concepts; do not copy exemplar identities into platform code or treat example runtime syntax as current API. | Example content is illustrative and partially stale. Static identity scanning and two-instance tests are binding. |
| Reference HTML demonstrates old Capture and adapter settings | Preserve its visual vocabulary and valid interactions; mark quick capture/triage and stale inventory as superseded. | The HTML governs demonstrated behavior, while later BUILD amendments govern product semantics. |
| Degraded write queue examples | Reads and diagnostics degrade explicitly. External writes fail closed unless an individual operation has a proven idempotency key, durable encrypted queue, expiry, reauthorization, and observation recheck. | At-least-once delivery plus privacy and authority make a generic queued-write promise unsafe. |

## Semantic invariants

- A log fact is immutable evidence; a projection is disposable derived state.
- A calendar event reserves/records time. A task is household work. A commitment is an obligation to a Party. A list item is mirrored/simple list state. A recurrence is a domain pattern. A scheduler job is an execution trigger. Links are explicit; types never collapse.
- The CRM spine is Parties, Identifiers, Memberships, Relationships, Interactions, Commitments, and Artifacts.
- The operational loop is observe -> understand -> organize -> propose -> authorize -> act/project -> verify outcome -> recalculate next action. It covers now, next hour, daypart, day, week, and longer horizons.
- Lifecycle feedback is typed: completion, deferral, snooze, blockage, correction, approval/rejection, suppression, delivery/failure, and verification.

## Consequences

- Existing domain schemas, normalization, privacy, governance, pack validation, projection logic, and tests are migration assets, but no isolated layer is considered a working runtime.
- Phase 2 starts with composition and the two golden vertical slices. It may change production code only behind executable compatibility gates.
- Optional providers may be deferred. Required product functions, projections, privacy/governance, workbook round trip, bootstrap safety, and the four OpenClaw seams may not.
- Gate 1 contains contracts and test skeletons only; it changes no production behavior.

## Gate 2 implementation note

Gate 2 implements, without changing this decision, the one-process runtime spine described here: a single typed asynchronous `EventSink`, commit-before-notify delivery, one durable bus owner, all thirteen explicit projection registrations (including `member_knowledge`), product router namespaces, bridge ingest, and bounded lifecycle ownership. The two golden product slices remain Phase 3 work and cannot be inferred from the runtime-spine pass.
