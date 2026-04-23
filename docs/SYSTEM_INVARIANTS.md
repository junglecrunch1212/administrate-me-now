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

_(pending)_

## Section 6: Security — session, scope, governance, observation

_(pending)_

## Section 7: Pipelines — reactive and proactive

_(pending)_

## Section 8: OpenClaw boundaries

_(pending)_

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
