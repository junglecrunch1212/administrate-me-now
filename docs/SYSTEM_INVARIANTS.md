# AdministrateMe system invariants

_The constitutional reference for the build. Every subsequent prompt (02 through 19) reads this before acting. If any invariant below is violated by code being written, stop and flag it._

Version: 1.0 (produced by prompt 01c, 2026-04-23)

Format: each invariant is numbered within its section, stated in one sentence (or at most two), and cites its source. Citation shorthand:

- `[arch §N]` → `docs/architecture-summary.md` section N
- `[cheatsheet Qn]` → `docs/openclaw-cheatsheet.md` question n
- `[BUILD.md §X]` → `ADMINISTRATEME_BUILD.md` section X
- `[DIAGRAMS.md §N]` → `ADMINISTRATEME_DIAGRAMS.md` section N
- `[CONSOLE_PATTERNS.md §N]` → `ADMINISTRATEME_CONSOLE_PATTERNS.md` section N
- `[REFERENCE_EXAMPLES.md §N]` → `ADMINISTRATEME_REFERENCE_EXAMPLES.md` section N

Authored in four batch commits to stay within per-turn context limits — see commit history for batching.

---

## Section 1: The event log is sacred

The event log at L2 is the single source of truth for AdministrateMe; every other persistent state in the system is a derived projection that can be torn down and rebuilt from the log without data loss. [arch §1, arch §3, BUILD.md §L2]

1. The event log is **the only source of truth**; every projection, workbook, cache, index, vector store, and materialized view in the system is a pure function of the log plus its own handler logic. [arch §3, BUILD.md §L2]
2. The log is **append-only**, enforced by code (a single `EventStore.append(event)` owns the only writable connection; all other connections open read-only) AND by a SQLite trigger that refuses UPDATE or DELETE on `events`, AND by a unit test that asserts both. [arch §3, BUILD.md §CRITICAL OPERATING RULES rule 5, BUILD.md §L2]
3. Writes go through `EventStore.append()`, which validates the payload against the Pydantic model registered for the `event_type` and refuses insertion on validation failure. [arch §3, BUILD.md §L2]
4. Append is **transactional with publish**: `append()` validates, inserts, commits, then publishes to the in-process bus — all within the same transaction boundary, so a crash between insert and publish cannot produce a silently-lost event. [arch §3]
5. Row schema fields are: `event_id` (ULID, 16 bytes), `event_type` (dotted), `schema_version`, `occurred_at`, `recorded_at` (both ISO 8601 UTC), `source_adapter`, `source_account_id`, `owner_scope`, `visibility_scope`, `sensitivity` (`normal` / `sensitive` / `privileged`), `correlation_id`, `causation_id`, `payload_json`, `raw_ref`, `actor_identity`. [arch §3]
6. The log is **partitioned logically by `owner_scope`** — `owner_scope` is an indexed column (`idx_events_scope_time`), not a separate physical table; values are `private:<member_id>`, `shared:household`, `org:<id>`. [arch §3]
7. Oversized payloads sidecar to `~/.adminme/data/raw_events/<yyyy>/<mm>/<event_id>.json.zst` when >64KB; artifacts (email bodies, images, PDFs) land at `~/.adminme/data/artifacts/<yyyy>/<mm>/<sha256>.<ext>` and are referenced as `artifact://<sha256>` in `raw_ref`, encrypted at rest via `cryptography.fernet` keyed from the SQLCipher master key. [arch §3]
8. Events are **immutable but correctable** — a bad classification is fixed by emitting a new corrective event (e.g. `classification.corrected`) with `causation_id` pointing to the original; projections honor latest truth. No event is ever deleted or mutated in place. [arch §3, BUILD.md §CRITICAL OPERATING RULES rule 5]
9. Every `event_type` ships a Pydantic model under `adminme/lib/event_types/<namespace>/<type>.py`, a `schema_version` integer, and an upcaster on schema change; plugin-introduced types register via the `hearth.event_types` entry point in their own dotted namespace. [arch §3, BUILD.md §CRITICAL OPERATING RULES rule 18]
10. Two bus implementations ship against the same `EventBus` Protocol — `InProcessBus` (asyncio queues + durable `bus_consumer_offsets` table) is the default, `RedisStreamsBus` is the spec'd alternate for future scale-out — and the full pipeline integration suite runs against both. [arch §3]

## Section 2: Projections are derived; they are never truth

Projections at L3 are read-only consumers of the event log; they synthesize state for fast queries but never own it. [arch §4, BUILD.md §L3]

1. Every projection is a pure function of the event log plus its own handler logic: `projection.rebuild(name)` truncates the projection's tables/files and replays from event 0, producing state equivalent to the live cursor. [arch §4]
2. Projections **never write back to the event log** — they are strictly read-only consumers; a projection emitting an event is a bug. [arch §4, BUILD.md §L3]
3. Projection handlers are deterministic: no wall-clock reads, no random, no UUID generation, no network calls, no calls to other projections, no calls to the event log beyond the cursor advance — `apply(event)` is a pure function over `(state, event)`. [arch §4]
4. Each projection has a `name`, a `version` integer (bumped to trigger rebuild), an event-type subscription list, a durable cursor, an idempotent `apply(event)`, and a `rebuild()`. [arch §4]
5. There are exactly **12 v1 projections**: `parties`, `interactions`, `artifacts`, `commitments`, `tasks`, `recurrences`, `calendars`, `places_assets_accounts`, `money`, `vector_search`, `xlsx_workbooks`, `member_knowledge`; adding a thirteenth requires an ADR. The 12th projection `member_knowledge` was added by the Conception-C architecture amendment per D17 (see `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`). [arch §1, arch §4, D17]
6. Plugin-provided projections namespace their tables under the plugin's dotted id; they never share table names with core projections, and the projection schema is not part of the authority model (authority is checked at Session construction, not at projection-row level). [arch §4, arch §6]
7. Projection schemas never embed hardcoded tenant identity; all tenant-specific values come from row data populated by events, which in turn originate in the instance's config/bootstrap. [BUILD.md §CRITICAL OPERATING RULES rule 4, arch §4]

## Section 3: The CRM spine — parties, interactions, artifacts

The CRM is not a sub-feature of the Capture product; the CRM's data model (Parties + Identifiers + Memberships + Relationships + Interactions + Commitments + Artifacts) IS the platform's data model — every other product reads and refines it. [arch §9, BUILD.md §THE CRM IS THE SPINE]

1. `parties` is the CRM core; every addressable entity in the system — persons, organizations, households — is a Party with a stable `party_id` (ULID) that never changes for the life of that identity. [arch §4 table row 3.1]
2. Parties are uniquely identified by `(tenant_id, party_id)`; the same email or phone across two tenants produces two different `party_id`s — there is no cross-tenant identity resolution. [arch §4, BUILD.md §CRITICAL OPERATING RULES rule 17]
3. Identity merges emit a `party.merged` event; after a merge, references to the collapsed `party_id` still resolve via the identity index so no link in the log or in any projection ever dangles. [arch §3, arch §4]
4. Household members are Parties with a `Membership` linking them to the household Party; members carry a `member_id` in addition to `party_id`, and session/scope logic keys off `member_id` rather than raw `party_id`. [arch §4, arch §10 bootstrap §3]
5. `interactions` is a deduplicated timeline: one row may aggregate multiple raw events (via a `raw_event_ids` JSON list), and rows are never edited after insertion — the timeline is append-only as a projection and the underlying events remain in the log. [arch §4 table row 3.2]
6. `artifacts` records documents, images, and structured records; links from artifacts to Parties flow through the polymorphic `artifact_links` table, not through a party_id column on `artifacts` itself. [arch §4 table row 3.3]
7. The CRM spine is consumed by every product surface: Capture renders it, Core reads it for whatnow/digest/custody_brief ranking, Comms resolves counterparties against it, Automation attributes money flows against its Parties and Accounts. [arch §9]

## Section 4: Commitments, tasks, recurrences — the domain spine

Commitments, tasks, and recurrences are three distinct projections with three distinct semantics; they collaborate but they do not collapse into one concept. [arch §4, BUILD.md §3.4, BUILD.md §3.5, BUILD.md §3.6]

1. A **Commitment** is an obligation between parties: `owed_by_party` + `owed_to_party` + `kind` + `description`; it tracks propose→confirm→complete with full provenance (`source_interaction_id`, `source_skill@version`, `confirmed_by`, timestamps for proposed/confirmed/completed). [arch §4 table row 3.4, BUILD.md §3.4]
2. Commitments are **proposed** by pipelines (`CommitmentProposed` events from `commitment_extraction` or similar); they are never directly created by a user-facing surface — surfaces confirm or dismiss proposals per the rule-6 propose-then-commit pattern. [arch §5, BUILD.md §CRITICAL OPERATING RULES rule 6]
3. A **Task** is household work; tasks may derive from a commitment (via linkage) or be standalone, and the `tasks` projection is AdministrateMe-specific (not inherited from Hearth). [arch §4 table row 3.5]
4. Completing a task that derives from a commitment emits `task.completed` AND may trigger commitment fulfillment logic; whether a commitment is considered fulfilled may require multiple tasks or specific conditions — task completion alone does not automatically complete the commitment. [arch §4 table row 3.4, arch §4 table row 3.5, BUILD.md §3.4]
5. A **Recurrence** is a template (RFC 5545 RRULE string) with `next_occurrence`, `lead_time_days`, and a `trackable` flag; recurrences generate scheduled occurrences but **are not tasks themselves** — a recurrence firing does not automatically produce a task row unless a pipeline explicitly materializes it. [arch §4 table row 3.6]
6. Commitments are never completed by a recurrence firing; they complete only on explicit `commitment.confirmed` / `commitment.completed` or on task completion whose downstream fulfillment logic decides so. [BUILD.md §3.4, BUILD.md §3.6]
7. Every LLM-produced proposal (commitment, recurrence, thank-you, artifact-classification) carries `source_skill@version` provenance; direct LLM writes to live state are prohibited — the LLM proposes, the human confirms. [arch §5, BUILD.md §CRITICAL OPERATING RULES rule 6]

## Section 5: Calendar and its relationship to tasks/recurrences

The `calendars` projection is effectively read-only from AdministrateMe's perspective — external calendar providers own the authoritative state; AdministrateMe ingests, merges at read time, and filters at read time. [arch §4 table row 3.7, arch §6]

1. The `calendars` projection is populated by external adapters (Google Calendar, iCloud CalDAV, etc.); AdministrateMe does **not** write back to external calendars unless an adapter is explicitly configured for bidirectional sync. [arch §4 table row 3.7, arch §5]
2. Calendar events flow external → internal; modifications made inside AdministrateMe surfaces (if any) do not round-trip back to the external provider by default. [arch §4 table row 3.7]
3. A task or recurrence carrying a `scheduled_at` timestamp does **not** create a calendar event; `scheduled_at` is an internal time hint consumed by whatnow/digest/reminder_dispatch and surfaced in internal views — it never becomes a row in `calendar_events`. [arch §4 table row 3.6, arch §5]
4. Calendar queries and task queries overlap semantically but are backed by different projections with different schemas; surfaces that present a unified day view (e.g. Today, digest) must merge them **at read time**, never by joining at write time or by copying events between projections. [arch §4, arch §9]
5. Privacy filtering on calendar events is applied at **read time**, not at ingest; events remain intact in the projection with their original sensitivity, and only the rendered view is redacted. `redactToBusy` is allowlist-shaped so new `Event` fields do not accidentally leak. [arch §6, CONSOLE_PATTERNS.md §6]
6. Private calendar events owned by other members are rendered as opaque `[busy]` blocks (time + duration only, optional first-name owner hint) when queried by a non-owner; events tagged `finance`, `health`, `legal`, or `adult_only` are dropped entirely for children regardless of sensitivity. [arch §6, CONSOLE_PATTERNS.md §6]

## Section 6: Security — session, scope, governance, observation

Security is defense-in-depth across the session layer, scope enforcement at every projection query, `guardedWrite` at the HTTP write boundary, privacy filtering at read, child-role nav/API enforcement, observation mode at the outbound filter, and the privileged-access log. No single layer is load-bearing alone. [arch §6, BUILD.md §AUTHORITY, BUILD.md §OBSERVATION, BUILD.md §GOVERNANCE, BUILD.md §L3-continued, CONSOLE_PATTERNS.md §§2/3/6/7/11, DIAGRAMS.md §§3/4/5/9]

**Session + scope:**

1. Every read and every write happens under a `Session(current_user, requested_scopes)` object; there is no global DB connection and no code imports `sqlalchemy.orm.Session` directly. [arch §6, BUILD.md §L3-continued]
2. Sessions carry **both** an `authMember` (governs what you can do) and a `viewMember` (governs whose data you are reading); only principals may set view-as, ambient entities cannot be viewed-as, and children cannot view-as. [arch §6, CONSOLE_PATTERNS.md §2, DIAGRAMS.md §4]
3. Writes always use `authMember`; `viewMember` never authorizes a write — a principal viewing-as another principal still writes under their own identity, and two-member commitments record both ids separately (`approved_by=A`, `owner=B`). [arch §6, CONSOLE_PATTERNS.md §2]
4. Scope predicates are auto-appended to every projection query (`WHERE visibility_scope IN (allowed_scopes) AND (sensitivity != 'privileged' OR owner_scope = current_user)`); every projection test ships a canary that expects `ScopeViolation` on out-of-scope reads. [arch §6, DIAGRAMS.md §5]

**guardedWrite — three layers, in order:**

5. Every console-originated write (and every pipeline write that routes through the HTTP bridge) passes through `console/lib/guarded_write.js`, in strict order: (1) **agent allowlist** (is this agent even permitted to attempt this action?), (2) **governance `action_gate`** (`allow` / `review` / `deny` / `hard_refuse`), (3) **sliding-window rate limit** keyed by `${tenantId}:${scope}:${action}`. [arch §6, CONSOLE_PATTERNS.md §3]
6. The first layer to refuse short-circuits; the denial event records which layer refused (`allowlist` / `governance` / `rate_limit`) so audit can attribute causes unambiguously. [arch §6, CONSOLE_PATTERNS.md §3]
7. `hard_refuse` gates are **never overridable** — send_as_principal, auto-answer unknown coparent, and reference-privileged-medical/legal-in-outbound all land in this bucket. [arch §6, BUILD.md §GOVERNANCE]
8. `review` gates emit a `review_request` event and return 202 `held_for_review` instead of firing; the action executes later only after an explicit operator approval. [arch §6, CONSOLE_PATTERNS.md §3]

**Privacy + privileged:**

9. Events marked `sensitivity='privileged'` are never embedded into `vector_search`, never summarized by LLM skills, never appear in cross-owner projections, and never appear in coach or `-kids` agent sessions. [arch §4 table row 3.10, arch §6, BUILD.md §L3-continued]
10. Adapters configured as privileged (e.g. a law-practice email account) have a hardcoded sensitivity floor at the adapter level; the config loader rejects any configuration that would lower it. [arch §6]
11. Every non-owner read of a `sensitivity='privileged'` record is logged to the privileged-access log with actor identity, target event/row, call stack, and timestamp, surfaced via `adminme audit privileged-access`. [arch §6]
12. Identity-first privacy is the primary boundary (privileged content never enters the assistant's accounts or the `vector_search` projection); session scope is secondary; event-level sensitivity is tertiary — all three must be present, none alone is sufficient. [arch §6]

**Observation mode:**

13. Observation mode is enforced at the **final outbound filter**, not at the policy layer and not at the action-decision layer — all internal logic (pipelines, skill calls, projection updates, console UI, reward previews) runs normally, and only the external side effect is suppressed. [arch §6, CONSOLE_PATTERNS.md §11, DIAGRAMS.md §9]
14. Every outbound-capable action (L5 surfaces, L4 pipelines, L1 adapters that can send) calls `outbound(ctx, actionFn)`; emitting `external.sent` anywhere else is a bug. [arch §6, CONSOLE_PATTERNS.md §11]
15. Observation mode is **per-tenant**, not per-agent or per-skill; the scope of the suppression is the whole instance, not a specific channel or persona. [arch §6]
16. Observation is **default-on for new instances** — bootstrap §9 ends with observation enabled; the principal opts out explicitly only after reviewing the suppressed-action log. Suppressed actions emit `observation.suppressed` with the full would-have-sent payload for tenant review. [arch §6, arch §10, CONSOLE_PATTERNS.md §11]

**HIDDEN_FOR_CHILD:**

17. Admin surfaces are enforced two ways for child sessions: a **client-side nav filter** (canonical list in `console/lib/nav.js`) AND a **server-side prefix blocklist** (`CHILD_BLOCKED_API_PREFIXES`); client-side is UX, server-side is security, and the two arrays are deliberately independent. [arch §6, CONSOLE_PATTERNS.md §7]
18. Child sessions see only `today` and `scoreboard` in the nav; `/api/inbox`, `/api/crm`, `/api/capture`, `/api/finance`, `/api/calendar`, `/api/settings`, `/api/tasks`, `/api/chat`, and `/api/tools` all return 403 for children regardless of UI state. [arch §6, CONSOLE_PATTERNS.md §7]

**Bridge sovereignty:**

19. Each member bridge runs only its assigned member's adapter set; a bridge Mac Mini that runs more than one member's adapter set is a misconfiguration. The central CoS Mac Mini never signs into a family member's iCloud account; cross-member knowledge access happens only through projection queries on the central CoS Mac Mini, never through bridge-to-bridge access. Kid bridges run Apple Notes + Voice Notes only — Obsidian is excluded for child-owned bridges. This is the physical-layer reinforcement of [§6.12] identity-first privacy. [BUILD.md §MEMBER BRIDGES, D17]

## Section 7: Pipelines — reactive and proactive

Pipelines at L4 turn events into derived events, proposals, and skill calls; they never write projections directly. The reactive/proactive split is a scheduling question, not a capability question — both kinds emit the same kinds of events. [arch §5, BUILD.md §L4]

1. **Reactive pipelines** run in-process inside the AdministrateMe `PipelineRunner`; they subscribe to the event bus via `triggers.events` in their `pipeline.yaml` manifest. [arch §5, BUILD.md §L4]
2. **Proactive pipelines** are registered as **OpenClaw standing orders** at product boot; OpenClaw owns the scheduling primitive, and the pipeline's handler is reached via a Python-product HTTP endpoint the standing order invokes. [arch §5, arch §9]
3. No pipeline writes directly to a projection table or to an xlsx file; pipelines emit events, projections consume them — a pipeline touching a projection cursor or projection row is a bug. [arch §4, arch §5]
4. Pipelines invoke skills **only** through `await run_skill(skill_id, inputs, ctx)`, which wraps `POST http://127.0.0.1:18789/tools/invoke` with `tool: "llm-task"` per OpenClaw's gateway HTTP API; the wrapper translates AdministrateMe skill-pack manifests into `llm-task` tool args at call time. Pipelines **never** import `anthropic` / `openai` / any provider SDK and never talk to a provider directly. [arch §5, arch §2, BUILD.md §L4-continued, ADR-0002, docs/reference/openclaw/gateway/tools-invoke-http-api.md, docs/reference/openclaw/tools/llm-task.md]
5. Every skill call emits `skill.call.recorded` with full provenance — skill name, version, `openclaw_invocation_id`, inputs, outputs, provider, token counts, cost, duration, `correlation_id` — so every piece of LLM-derived state in the system is traceable to a replayable call. [arch §5]
6. The skill wrapper validates inputs against `input.schema.json`, checks that the skill's `sensitivity_required` is satisfied (refusing privileged inputs unless the skill declares it), checks that `context_scopes_required ⊆ Session.requested_scopes`, then invokes; outputs are validated against `output.schema.json` before return. [arch §5, BUILD.md §L4-continued]
7. A pipeline failure on one event does **not** halt the bus; the runner records the failure, retries per policy, and continues processing — one bad event cannot stop the stream. [arch §3, arch §5]
8. Skill calls are replayable: `adminme skill replay <skill_name> --since <ts>` re-runs the call and emits a new `skill.call.recorded` with `causation_id` pointing to the original call. [arch §5]
9. AdministrateMe does **NOT** talk directly to Anthropic / OpenAI / Ollama; OpenClaw is the only LLM client on the host, owns provider routing, retries, token accounting, and cache policy. [arch §2, arch §5, BUILD.md §L4-continued]

## Section 8: OpenClaw boundaries

AdministrateMe and OpenClaw are two independent systems that meet at exactly four documented seams; every other putative interaction between them is a violation. [arch §2, cheatsheet Q1, cheatsheet Q2, cheatsheet Q3, cheatsheet Q4]

1. The two systems meet at exactly **four seams**: skills, slash commands, standing orders, and channel plugins. Any other integration path is a violation and requires an ADR. [arch §2]
2. **OpenClaw owns all LLM provider contact**; AdministrateMe never imports the `anthropic` or `openai` SDK and never opens a socket to `api.anthropic.com` / `api.openai.com` / any provider API. [arch §2, arch §5]
3. **OpenClaw owns all channel transport** (iMessage via BlueBubbles, Telegram, Discord, web); AdministrateMe receives inbound through the `openclaw-memory-bridge` plugin rather than opening its own connections to those services. [arch §2, cheatsheet Q4]
4. The `openclaw-memory-bridge` plugin is **one-way** (OpenClaw → AdministrateMe) — it emits `messaging.received` and `conversation.turn.recorded` events into the AdministrateMe event log; it does not read back. [arch §2, arch §3]
5. When AdministrateMe's `outbound()` wants to send on a channel, it calls OpenClaw's channel-send API via the channel bridge plugin; AdministrateMe does **not** open transports to BlueBubbles / Telegram / Discord / web directly. [arch §2]
6. Slash-command handlers live in **AdministrateMe** as HTTP endpoints inside the Python product APIs; OpenClaw dispatches to them when a user types the command, but the business logic is AdministrateMe's. [arch §2, arch §9, cheatsheet Q2]
7. OpenClaw's approval gates (tool-execution boundary, host-local, after tool policy and before exec) and AdministrateMe's `guardedWrite` (HTTP API boundary inside the Node console) are **independent** gates — both must pass and neither substitutes for the other. [arch §2, cheatsheet Q7]
8. OpenClaw memory stays in `~/.openclaw/`; AdministrateMe event log stays in `~/.adminme/`; the two systems **never share a database** and there is no shared SQLite file or symlink between them. [arch §2, cheatsheet Q8]
9. L1 adapters run in **two places**: central adapters (messaging, calendaring, financial, etc.) run as standalone Python processes on the CoS Mac Mini at `:333x` process scope, and bridge adapters (knowledge-source: Apple Notes, Voice Notes, Obsidian, connector packs) run on member bridges per BUILD.md §MEMBER BRIDGES. Bridges emit owner-scoped events into the central event log via the `:3337 bridge` HTTP ingest endpoint over the tailnet; Tailscale identity binds the inbound owner_scope at the endpoint. The bridge daemon does not hold the AdministrateMe SQLCipher master key and does not write to the central event log directly. [BUILD.md §MEMBER BRIDGES, D17, arch §1, arch §9]

## Section 9: Console is a rendering + authorization layer

The Node console at `:3330` is an HTTP edge: it authenticates, authorizes, proxies, and renders — it does not own data, does not host pipelines, and does not own long-lived state. [arch §8, BUILD.md §L5, CONSOLE_PATTERNS.md §§1/2/3/10]

1. The Node console at `:3330` is the **only tailnet-facing surface** on the host; Python product APIs (`:3333–:3336`) bind to loopback only. [arch §8, arch §9]
2. The Node console **never reads the event log directly** — all event-derived reads pass through Python product APIs over the HTTP bridge. [arch §8]
3. The Node console **never writes to projection SQLite directly**; writes proxy to the Python product APIs through the HTTP bridge with tenant header injection and correlation-ID propagation. [arch §8, CONSOLE_PATTERNS.md §10]
4. The Node console MAY read projection SQLite read-only via `better-sqlite3` opened in readonly mode, as a performance optimization for UI rendering; that is the only direct DB access the console has, and it is strictly read-only. [arch §8]
5. The console resolves `authMember` from the `Tailscale-User-Login` header and the `party_tailscale_binding` projection; it does not implement its own auth, and there is no login page — on the tailnet = authenticated. [arch §8, CONSOLE_PATTERNS.md §1]
6. The dev-mode `X-ADMINME-Member` header requires **both** `ADMINME_ENV=dev` AND a loopback `remoteAddr`; either condition failing rejects the header. [arch §8, CONSOLE_PATTERNS.md §1]
7. SSE chat at `/api/chat/stream` is a pass-through proxy to OpenClaw on `:18789`; the console adds `correlation_id` and rate-limits under `web_chat` before opening the upstream connection, and `AbortController` propagates cancellation upstream. [arch §8, CONSOLE_PATTERNS.md §5]

## Section 10: xlsx is a bidirectional projection

`xlsx_workbooks` is the only projection in the system that is bidirectional and the only projection that writes to disk files rather than SQLite tables; its bidirectionality is carefully bounded. [arch §4 table row 3.11, BUILD.md §3.11]

1. `xlsx_workbooks` is the **only bidirectional projection**: the forward daemon (`xlsx_sync/forward.py`) regenerates the workbook from events, and the reverse daemon (`xlsx_sync/reverse.py`) watches via `watchdog` and emits events on human edits. [arch §4 table row 3.11]
2. Sidecar state at `~/.adminme/projections/.xlsx-state/<workbook>/<sheet>.json` records what the workbook currently represents; the forward daemon writes it in the same lock as the xlsx write, and the reverse daemon reads it to tell a user edit from a forward-regeneration. [arch §4 table row 3.11]
3. Derived cells (columns tagged `[derived]` in the header row) are never accepted from the reverse projector; edits to them are silently dropped (UX clarity, not a security boundary — the event log remains the source of truth). [arch §4 table row 3.11]
4. Plaid-sourced transaction fields `date`, `account_last4`, `merchant_name`, `amount`, and `plaid_category` are protected on reverse; principals may edit `assigned_category`, `notes`, `memo` — edits to protected fields are dropped. [arch §4 table row 3.11, BUILD.md §3.11]
5. xlsx writes are debounced: **5s forward** on event bursts, **2s reverse** on file edits — enough to coalesce typical edit storms without producing noticeable lag. [arch §4 table row 3.11]
6. Forward regeneration writes **computed values**, not Excel formulas, so the workbook is reproducible across devices, auditable, and safe to round-trip through LibreOffice during bootstrap. [arch §4 table row 3.11]
7. A forward-write triggering a spurious reverse-detect is a **bug**, not an expected cycle; the fix is sidecar determinism plus lock ordering (the reverse daemon skips cycles when it sees the forward lock held). [arch §4 table row 3.11]
8. If the workbook is deleted or corrupted, `adminme projection rebuild xlsx_workbooks` regenerates it fully from the event log — the xlsx file carries no state that is not recoverable. [arch §4 table row 3.11]

## Section 11: Bootstrap is a one-time, resumable operation

The bootstrap wizard is a textual/Rich TUI that runs once per instance and leaves `~/.adminme/` in its canonical shape; it is resumable but not a routine administrative surface. [arch §10, BUILD.md §BOOTSTRAP WIZARD]

1. Bootstrap runs **once per instance** and produces `~/.adminme/` in its canonical shape; after completion, the ongoing administrative surface is the console and the `adminme` CLI, not the wizard. [arch §10]
2. Bootstrap is **resumable** via encrypted `~/.adminme/bootstrap-answers.yaml.enc` AND the event log; re-running `adminme bootstrap` reads the answers file and jumps to the first incomplete section. [arch §10]
3. Successfully completed sections are **idempotent on re-run**: events are already in the log, config files are not rewritten from stale answers, and no user-visible side effect repeats. [arch §10]
4. Observation mode is **enabled by default** at the end of §9 of the wizard; the tenant must explicitly opt out after reviewing the suppressed-action log. [arch §6, arch §10]
5. Only **§1 Environment preflight** aborts on failure; all later sections create inbox tasks for skipped sub-items rather than blocking wizard progress — incomplete setup is always resolvable via the normal task flow. [arch §10]

## Section 12: Tenant isolation

AdministrateMe is single-tenant by deployment and multi-tenant by codebase; the codebase must be entirely free of any specific tenant's identity. [BUILD.md §CRITICAL OPERATING RULES rules 4/17/18, BUILD.md §FINAL CHECKS]

1. AdministrateMe instances are **single-tenant by deployment** (one instance per household) but **multi-tenant at the code level**; every event, projection row, and config value carries `tenant_id`. [BUILD.md §CRITICAL OPERATING RULES rule 17, BUILD.md §FINAL CHECKS]
2. Two instances on the same machine, selected by distinct `ADMINME_INSTANCE_DIR` values, must be **fully independent** — no shared event log, projection DB, config directory, xlsx workbook, or sidecar state. [BUILD.md §CRITICAL OPERATING RULES rule 17, arch §10]
3. `tenant_id` is assigned at bootstrap time and is **immutable thereafter**; changing it would invalidate every foreign-key-like reference in the projections and the log. [BUILD.md §CRITICAL OPERATING RULES rule 17]
4. No family name, person's name, address, phone number, email, account number, or medical detail appears anywhere in platform code; tenant data lives **only** in the instance directory — code files, test fixtures (except those explicitly marked as such), and documentation files in the repo are all tenant-identity-free. [BUILD.md §CRITICAL OPERATING RULES rule 4, BUILD.md §FINAL CHECKS]
5. A static identity-scan test (`tests/unit/test_no_hardcoded_identity.py`) fails CI if hardcoded tenant data appears in `adminme/`, `bootstrap/`, `profiles/`, `personas/`, `integrations/`, or `tests/` (except fixtures flagged `# fixture:tenant_data:ok`). [BUILD.md §CRITICAL OPERATING RULES rule 4, BUILD.md §FINAL CHECKS]
6. Every schema change ships a numbered migration; migrations are idempotent; existing tenants can always upgrade without data loss. [BUILD.md §CRITICAL OPERATING RULES rule 18]

## Section 13: Explicit non-connections (things that look related but aren't)

This section names pairs of concepts that sound related but are deliberately independent in this system; getting any of these wrong produces a subtle class of bug that is hard to reverse once data has flowed.

1. `commitment.confirmed` and `task.completed` are **different events with different semantics**; a task's completion does not necessarily fulfill a commitment — commitment fulfillment depends on the commitment's own conditions, which may require multiple tasks or explicit confirmation. [arch §4 table row 3.4]
2. A `calendar.event_added` event from an external source does **not** automatically create a commitment, task, or recurrence; the calendar projection is independent of the domain projections until a pipeline explicitly bridges them (e.g. a pipeline proposing a task from a detected appointment). [arch §4 table rows 3.4/3.5/3.6/3.7]
3. `noise.filtered` does **not** delete the originating event; the underlying `messaging.received` (or equivalent) is still in the log, just flagged downstream as noise for ranking purposes. [arch §3, arch §5]
4. `identity.merge_suggested` does **not** auto-merge parties; merging requires explicit operator confirmation, and only after confirmation does a `party.merged` event land in the log. [arch §5]
5. The `xlsx_workbooks` projection is the **only** projection that writes to disk files; all other projections write to SQLite tables only, and a projection writing to disk outside that seam is a bug. [arch §4, arch §4 table row 3.11]
6. The console is **not** a pipeline host; proactive behaviors run inside the Python products (registered as OpenClaw standing orders), not inside the Node console process — the console renders, it does not schedule. [arch §8, arch §9]
7. Morning digest composition does **not** read projections directly; it reads via Python product APIs over HTTP, like any other caller — even though digest and projections may live in the same process, the boundary is kept explicit. [arch §9]
8. The `vector_search` projection does **not** include privileged content; `sensitivity='privileged'` rows are never embedded, and privileged content cannot enter cross-party semantic search under any circumstance. [arch §4 table row 3.10, arch §6]
9. Slash commands are **not** the same as REST endpoints — slash commands are registered with OpenClaw; REST endpoints live inside Python products; a slash-command handler usually calls a REST endpoint to do its work, but the two surfaces are wired independently and authorized independently. [arch §2, arch §9]
10. The `tasks` projection is AdministrateMe-specific (not inherited from Hearth); the `commitments` projection is Hearth-inherited — conflating the two would tie AdministrateMe-specific code into the Hearth upstream. [arch §4 table rows 3.4/3.5]

## Section 14: Proactive-behavior scheduling boundary

User-visible proactive behavior fires via OpenClaw standing orders — so it shares OpenClaw's approval gating, observation-mode enforcement, and rate-limit machinery with interactive chat turns. APScheduler inside each Python product is used ONLY for internal Python-only schedules. [arch §5, arch §9, BUILD.md §L5-continued]

1. All user-visible proactive behaviors fire via **OpenClaw standing orders** — not APScheduler — so each one is subject to the same approval gating, observation-mode enforcement, and rate-limit machinery as an interactive chat turn. [arch §5, arch §9]
2. The proactive behaviors in scope are: `morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, `custody_brief`, `crm_surface`, `scoreboard_projection`, `graph_miner`. Adding a ninth requires an ADR and the new entry MUST be registered as an OpenClaw standing order. [arch §5, arch §9]
3. APScheduler is reserved for **internal, non-user-facing schedules**: adapter polling cadences, bus heartbeat, xlsx forward/reverse watchers, cache refreshes, projection compaction, log rotation. No proactive user-visible behavior may be implemented as an APScheduler job. [arch §5, arch §9]
4. Anything that could surface to a principal goes through an OpenClaw standing order so it shares the approval, observation-mode, and rate-limit machinery; putting such a behavior in APScheduler bypasses three of AdministrateMe's core safeguards and is a violation. [arch §5, arch §6, arch §9]

## Section 15: Instance-path resolution discipline

No module under `adminme/` hardcodes the string `~/.adminme/` or any subpath of it. All instance-directory paths come from an `InstanceConfig` object populated at service-start time from the config files in the instance directory. [BUILD.md §CRITICAL OPERATING RULES rule 4, BUILD.md §CRITICAL OPERATING RULES rule 17, BUILD.md §FINAL CHECKS]

1. **No module under `adminme/` hardcodes `~/.adminme/`** or any subpath of it; the string does not appear outside explicitly-marked bootstrap or documentation code. [BUILD.md §CRITICAL OPERATING RULES rule 4, BUILD.md §CRITICAL OPERATING RULES rule 17]
2. All instance-directory paths resolve through an **`InstanceConfig`** object, constructed at service-start time from the config files in the instance directory; production code reads paths off the `InstanceConfig`, not off environment variables directly, and not off string literals. [BUILD.md §CRITICAL OPERATING RULES rule 17]
3. Tests pass an isolated tmp path to `InstanceConfig`; production code resolves through the real config; the bootstrap wizard populates `~/.adminme/` on a fresh instance — these three callers use the same `InstanceConfig` contract. [BUILD.md §CRITICAL OPERATING RULES rule 17, arch §10]
4. A **grep-based canary test** fails CI if `~/.adminme` or `.adminme/` appears as a string literal in a non-fixture module under `adminme/`, `bootstrap/`, `profiles/`, `personas/`, or `integrations/`. [BUILD.md §CRITICAL OPERATING RULES rule 4, BUILD.md §FINAL CHECKS]
5. Consequence: two instances on the same machine selected via distinct `ADMINME_INSTANCE_DIR` values work correctly without code changes — this is the concrete mechanism behind §12 tenant isolation; if `InstanceConfig` is bypassed anywhere, tenant isolation silently breaks. [BUILD.md §CRITICAL OPERATING RULES rule 17, BUILD.md §FINAL CHECKS]

## Section 16: Proposed invariants (operator review)

The invariants below are suspected true from a careful reading of the specs, but the specs do not state them precisely enough to commit to §§1–§15. Each is stated as a concrete one-sentence invariant and cites where the ambiguity lives. Operator: confirm or reject each before prompt 02 begins.

1. _Proposed:_ Proactive pipelines register with OpenClaw by writing prose programs into the workspace `AGENTS.md` plus `openclaw cron add` invocations at bootstrap §8; there is no programmatic plugin-hook path and no typed registration API. [cheatsheet Q3, arch §11 item 1, BUILD.md §L4 "Scheduled/proactive pipelines"] _Operator: confirm or reject._
2. _Proposed:_ The Node console hosts exactly one `Bus` class that handles all four SSE-ish fan-out mechanisms (chat stream proxy, reward toast fan-out, degraded-mode notifications, general event fan-out) against the in-process event bus; the four are facets of one object, not four independent components. [arch §11 item 2, CONSOLE_PATTERNS.md §§5/8/9, BUILD.md §L2] _Operator: confirm or reject._
3. _Proposed:_ Each profile pack's view mode (`carousel` / `compressed` / `child`) is a JSX component signature that the profile pack must implement against a shell-defined protocol; the mode is not a data-shape contract negotiated with a backend and not an API parameter. [arch §11 item 3, CONSOLE_PATTERNS.md §2, REFERENCE_EXAMPLES.md §6] _Operator: confirm or reject._
4. _Proposed:_ CRM-adjacent surfaces belong in the **Capture** product (because the CRM UI lives there); Core calls Capture's APIs when it needs CRM data for ranking, digest, or custody_brief — there is no cross-product "parties" API family. [arch §11 item 4, arch §9, BUILD.md §THE CRM IS THE SPINE] _Operator: confirm or reject._
5. _Proposed:_ The forward xlsx projector runs **unconditionally** regardless of observation mode, because the workbook is a purely local artifact and observation mode gates external side effects only; the tenant sees updates in the workbook during observation period as part of verifying the system is working. [arch §11 item 5, arch §6, BUILD.md §3.11] _Operator: confirm or reject._
6. _Proposed:_ The `openclaw-memory-bridge` plugin emits events into AdministrateMe by calling an HTTP endpoint on one of the Python product APIs (most likely Comms `:3334`), not by writing into the AdministrateMe SQLCipher database directly — because the plugin runs inside OpenClaw and does not carry the AdministrateMe SQLCipher master key. [arch §11 item 6, arch §2, BUILD.md §OPENCLAW IS THE ASSISTANT SUBSTRATE] _Operator: confirm or reject._
7. _Proposed:_ `schema_version` on events is a **monotonically increasing integer per `event_type`** (not semver, not a string); upcasters form a chain from `v1→v2→v3` such that an old event can always be decoded by composing upcasters in order. [arch §3, BUILD.md §CRITICAL OPERATING RULES rule 18] _Operator: confirm or reject._
8. _Proposed:_ The `correlation_id` on an event is set by the **originating adapter or surface** (the first entry point of a logical operation) and preserved unchanged through every derived event; the `causation_id` is set to the event id of the immediate parent. Neither is ever overwritten downstream. [arch §3, DIAGRAMS.md §2] _Operator: confirm or reject._
