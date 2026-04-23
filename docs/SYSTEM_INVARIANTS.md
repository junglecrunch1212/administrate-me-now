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
5. There are exactly **11 v1 projections**: `parties`, `interactions`, `artifacts`, `commitments`, `tasks`, `recurrences`, `calendars`, `places_assets_accounts`, `money`, `vector_search`, `xlsx_workbooks`; adding a twelfth requires an ADR. [arch §1, arch §4]
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

## Section 7: Pipelines — reactive and proactive

Pipelines at L4 turn events into derived events, proposals, and skill calls; they never write projections directly. The reactive/proactive split is a scheduling question, not a capability question — both kinds emit the same kinds of events. [arch §5, BUILD.md §L4]

1. **Reactive pipelines** run in-process inside the AdministrateMe `PipelineRunner`; they subscribe to the event bus via `triggers.events` in their `pipeline.yaml` manifest. [arch §5, BUILD.md §L4]
2. **Proactive pipelines** are registered as **OpenClaw standing orders** at product boot; OpenClaw owns the scheduling primitive, and the pipeline's handler is reached via a Python-product HTTP endpoint the standing order invokes. [arch §5, arch §9]
3. No pipeline writes directly to a projection table or to an xlsx file; pipelines emit events, projections consume them — a pipeline touching a projection cursor or projection row is a bug. [arch §4, arch §5]
4. Pipelines invoke skills **only** through `await run_skill(skill_id, inputs, ctx)`, which wraps `POST http://127.0.0.1:18789/skills/invoke`; pipelines **never** import `anthropic` / `openai` / any provider SDK and never talk to a provider directly. [arch §5, arch §2, BUILD.md §L4-continued]
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

## Section 9: Console is a rendering + authorization layer

_(pending)_

## Section 10: xlsx is a bidirectional projection

_(pending)_

## Section 11: Bootstrap is a one-time, resumable operation

_(pending)_

## Section 12: Tenant isolation

_(pending)_

## Section 13: Explicit non-connections (things that look related but aren't)

_(pending)_

## Section 14: Proactive-behavior scheduling boundary

_(pending)_

## Section 15: Instance-path resolution discipline

_(pending)_

## Section 16: Proposed invariants (operator review)

_(pending)_
