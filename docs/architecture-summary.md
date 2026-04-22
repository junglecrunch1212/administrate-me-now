# AdministrateMe architecture summary

_Produced by prompt 01b. Reference for all later phase prompts. Update if the specs change._

_Cites `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `ADMINISTRATEME_DIAGRAMS.md`, and `docs/openclaw-cheatsheet.md`. Authored in four batch commits to stay within per-turn context limits — see commit history for batching._

## 1. The five-layer model

Per BUILD.md §THE ARCHITECTURE and DIAGRAMS.md §1, every file in the system belongs to exactly one of five stacked layers. Layer N+1 depends on Layer N; the reverse direction is a build failure. OpenClaw sits above the stack as the assistant substrate (loopback `:18789`) — not a layer itself, but the gateway that channels, skills, slash commands, and standing orders run through.

**L1 — Adapters.** Channel-family-specific translators that read from external sources (Gmail, Google Calendar, Google Drive/Contacts, Plaid, Apple Reminders, CalDAV, Apple Contacts, iOS Shortcuts webhooks) and emit typed events into L2. Messaging adapters (iMessage via BlueBubbles, Telegram, Discord) are OpenClaw plugins; data adapters are standalone Python processes. Adapters never write projections, never call pipelines, and never compose outbound messages — that path is owned by OpenClaw's outbound channels or by adapter-specific write surfaces (Plaid writes, Apple Reminders writes).

**L2 — Event log + event bus.** The source of truth: one append-only SQLCipher-encrypted table partitioned logically by `owner_scope`, with an in-process asyncio pub/sub bus layered on top. OpenClaw's own memory is separate and is bridged into L2 via the `openclaw-memory-bridge` plugin (which emits `messaging.received` and `conversation.turn.recorded`). See §3 for invariants.

**L3 — Projections.** Deterministic pure functions from event subsets to read-model tables (or files, in the case of xlsx). Each projection has a name, version, event-type subscription list, and cursor, and can be rebuilt from the log at any time. The 11 projections are: `parties`, `interactions`, `artifacts`, `commitments`, `tasks`, `recurrences`, `calendars`, `places_assets_accounts`, `money`, `vector_search`, `xlsx_workbooks`.

**L4 — Pipelines + skill runner.** Reactive pipelines subscribe to events and run inside the AdministrateMe pipeline runner; proactive pipelines are scheduled and register as OpenClaw standing orders (per DIAGRAMS.md §1). When a pipeline needs model intelligence, it invokes a skill through the AdministrateMe skill runner, which wraps OpenClaw's skill runner at `POST :18789/skills/invoke` and emits a `skill.call.recorded` event with full provenance.

**L5 — Surfaces.** Two surface families on the same host. The **Node console at `:3330`** is the single tailnet-facing HTTP entry point, delivering the visual views (Today, Inbox, CRM, Capture, Finance, Calendar, Scoreboard, Settings) that text channels can't render; it proxies the chat pane into OpenClaw. The **Python product APIs at `:3333–:3336`** (core, comms, capture, automation) are loopback-only and carry product state for the console and for slash-command handlers. OpenClaw's channels (iMessage, Telegram, Discord, web) are the other L5 surface — owned by OpenClaw, populated with AdministrateMe-installed skills and slash commands.

---

## 2. How OpenClaw fits

Per BUILD.md §OPENCLAW IS THE ASSISTANT SUBSTRATE, AdministrateMe is not a standalone app; it is the Chief-of-Staff content-and-substrate layer installed on top of OpenClaw. OpenClaw owns the agent loop, channel connections, sessions, and the skill-runner/standing-orders/slash-command/plugin machinery. AdministrateMe owns the event log, projections, pipelines, adapters, and consoles.

**The four seams** — how the two systems meet:

- **Skills.** AdministrateMe's skill packs (e.g. `classify_commitment_candidate`, `extract_commitment_fields`, `compose_morning_digest`) are installed into OpenClaw via ClawHub or as local skill directories (per `openclaw-cheatsheet.md Q1`). When a pipeline needs intelligence, it calls OpenClaw's skill runner — same session scoping, same approval gates as a user-typed slash command. AdministrateMe's SKILL.md format is compatible with OpenClaw's (per `openclaw-cheatsheet.md Q5`).
- **Slash commands.** AdministrateMe registers household-CoS verbs (`/digest`, `/whatnow`, `/capture`, `/comms`, `/bill`, `/approve`, `/crm`, `/commit`, `/review`, `/scoreboard`) with OpenClaw's slash dispatcher. When James types `/digest` in iMessage, OpenClaw routes to the AdministrateMe handler (per `openclaw-cheatsheet.md Q2`).
- **Standing orders.** AdministrateMe registers proactive rules (paralysis detection, morning digest, reward dispatch, reminder dispatch, CRM gap nudges, coparent brief). Per `openclaw-cheatsheet.md Q3`, OpenClaw's standing-orders mechanism is **workspace prose in `AGENTS.md` paired with cron jobs** (`openclaw cron add ...`) — not a typed registration API. See §11 for the open question about how AdministrateMe writes these programs.
- **Channels.** The `openclaw-memory-bridge` plugin ingests OpenClaw conversation state into L2 as `messaging.received` and `conversation.turn.recorded` events. Outbound drafts composed by AdministrateMe get delivered through OpenClaw's channel plugins (per `openclaw-cheatsheet.md Q4`).

**What OpenClaw provides vs. what AdministrateMe adds.** OpenClaw: gateway daemon, agent loop, channel plugins, skill runner, slash dispatcher, sessions with `dmScope: per-channel-peer` (per `openclaw-cheatsheet.md Q6`), standing orders, cron, hooks, approval gates, SOUL.md, nodes, memory-core/memory-wiki. AdministrateMe: event log, 11 projections, 17 pipelines, data adapters (Gmail, Calendar, Plaid, Reminders, CalDAV, etc.), skill packs, slash-command handlers, Node console, 4 Python product APIs, persona packs compiled into SOUL.md, profile packs.

**State boundary.** OpenClaw's memory stays in OpenClaw at `~/.openclaw/` (per `openclaw-cheatsheet.md Q8`); AdministrateMe's event log stays in AdministrateMe at `~/.adminme/`. The two are bridged one-way through the memory-bridge plugin; they never share a database.

**Two independent gates.** OpenClaw's approval-gate system (per `openclaw-cheatsheet.md Q7`) runs at the tool-execution boundary on the host — after tool policy, before an exec actually runs — and is the stricter of requested + host-local policy. AdministrateMe's `guardedWrite` (per CONSOLE_PATTERNS.md §3) runs at the HTTP API boundary inside the Node console, before any write reaches a Python product API. Both gates must pass; neither can substitute for the other. §6 covers guardedWrite in detail.

---

## 3. Event log invariants

Per BUILD.md §L2 and DIAGRAMS.md §2, the event log is the source of truth for AdministrateMe. These invariants are non-negotiable.

- **Append-only, enforced by code AND by a SQLite trigger.** A single `EventStore.append(event)` function owns the only writable connection to `events`; all other connections open read-only. A unit test enforces that no UPDATE or DELETE statement against `events` ever succeeds. Events are immutable but **correctable** — a bad classification is fixed by emitting a new `classification.corrected` event with `causation_id` pointing to the original; projections honor latest truth.
- **SQLCipher-encrypted at rest.** WAL mode. Attachments (email bodies, images, PDFs) are encrypted at rest via `cryptography.fernet` keyed from the SQLCipher master key.
- **Partitioned by `owner_scope`.** Logical, not physical — owner_scope is an indexed column (`idx_events_scope_time`), not a separate table. Values: `private:<member_id>`, `shared:household`, `org:<id>`.
- **Row schema.** `event_id` (ULID, 16 bytes), `event_type` (dotted, e.g. `messaging.received`), `schema_version`, `occurred_at`, `recorded_at` (both ISO 8601 UTC), `source_adapter`, `source_account_id`, `owner_scope`, `visibility_scope` (may widen or narrow owner_scope), `sensitivity` (`normal` / `sensitive` / `privileged`), `correlation_id`, `causation_id`, `payload_json` (validated against the event-type's Pydantic model), `raw_ref`, `actor_identity`.
- **Sidecar storage for oversized payloads.** Payloads >64KB live at `~/.adminme/data/raw_events/<yyyy>/<mm>/<event_id>.json.zst`; artifacts (large email bodies, images, PDFs) at `~/.adminme/data/artifacts/<yyyy>/<mm>/<sha256>.<ext>`, referenced as `artifact://<sha256>` in `raw_ref`.
- **Typed event registry.** Every event_type has a Pydantic model under `adminme/lib/event_types/<namespace>/<type>.py`, a `schema_version`, and an upcaster on schema change. Plugins register new types via the `hearth.event_types` entry point in their own dotted namespace.
- **Transactional append + publish.** `append()` validates, inserts, commits, then publishes to the in-process bus — same transaction boundary.
- **In-process event bus.** Asyncio queues with durable per-subscriber offsets in a `bus_consumer_offsets` table. Two bus implementations ship against the same `EventBus` Protocol: `InProcessBus` (default) and `RedisStreamsBus` (spec'd alternate for future scale-out). An integration test runs the full pipeline suite against both.
- **Canonical topic prefixes.** `messaging.*`, `calendar.*`, `contacts.*`, `documents.*`, `telephony.*`, `financial.*`, `identity.*`, `commitment.*`, `task.*`, `recurrence.*`, `skill.*`, `adminme.reward.*`, `adminme.paralysis.*`, `adminme.digest.*`, `observation.*`, `plaid.*`, `reminder.*`, `member.*`, `profile.*`, `persona.*`, `system.*`.

Per DIAGRAMS.md §2, one inbound iMessage flows through adapter → bus → extraction pipeline → 2 skill calls → projection updates → console confirm → `commitment.confirmed` → reward dispatch — six events, one correlation_id, full audit trail by `grep correlation_id`.

## 4. The 11 projections

(pending)

## 5. Pipelines

(pending)

## 6. Security + privacy model

(pending)

## 7. Packs

(pending)

## 8. The console

(pending)

## 9. Python product APIs

(pending)

## 10. Bootstrap wizard

(pending)

## 11. Open questions

(pending)
