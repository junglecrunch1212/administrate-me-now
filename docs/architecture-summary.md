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

Per BUILD.md §L3 (§3.1–§3.11), each projection is a deterministic pure function from event subsets to a set of tables (or files). Each has a name, a version (bumped to trigger rebuild), a subscription list, a cursor, an idempotent `apply(event)`, and a `rebuild()` that truncates and replays from event 0. CLI: `adminme projections list|rebuild|lag`.

| # | Name | Subscribes to | Key tables/files | Notable properties |
|---|---|---|---|---|
| 3.1 | `parties` | `contacts.*`, `messaging.received/sent`, `telephony.*`, `identity.*`, `party.created`, `relationship.added` | `parties`, `identifiers`, `memberships`, `relationships` | CRM core. `identifiers.value_normalized` is canonicalized (E.164 phones, lowercased emails) for exact-match merge. |
| 3.2 | `interactions` | `messaging.*`, `telephony.*`, `calendar.event.concluded` | `interactions`, `interaction_participants`, `interaction_attachments` | Deduplicated touchpoints; aggregates multiple raw events per row (`raw_event_ids` JSON). Subject/summary lazy-LLM-extracted. |
| 3.3 | `artifacts` | `documents.*`, adapter artifact events | `artifacts`, `artifact_links` (polymorphic) | OCR/text extraction; typed structured extraction per kind (invoice, contract, medical_record, school_form, prescription). |
| 3.4 | `commitments` | `commitment.*` | `commitments` | Tracks propose→confirm→complete with full provenance (`source_interaction_id`, `source_skill@version`, `confirmed_by`, timestamps for proposed/confirmed/completed). Per BUILD.md §3.4. |
| 3.5 | `tasks` | `task.*`, `reminder.*` | `tasks` | AdministrateMe-specific (not in Hearth). Household work vs. obligation-to-outside-party; ADHD neuroprosthetic (rewards, paralysis, whatnow) operates over Tasks + Commitments unified in the inbox. Rich fields: energy, effort, micro_script, waiting_on, goal_ref, life_event. |
| 3.6 | `recurrences` | `recurrence.*` | `recurrences` | RFC 5545 RRULE strings, `next_occurrence`, `lead_time_days`, `trackable` flag (feeds scoreboard). |
| 3.7 | `calendars` | `calendar.*` | `calendar_events`, `availability_blocks` | Privacy filter applied at read time (see §6); `privacy` field may be `open`/`privileged`/`redacted`. `availability_blocks` stores busy-free-only source calendars. |
| 3.8 | `places_assets_accounts` | `place.*`, `asset.*`, `account.*`, association events | `places`, `place_associations`, `assets`, `asset_owners`, `accounts` | Three linked entity families in one projection. `accounts.login_vault_ref` is an `op://` / `1password://` pointer — never a raw credential. |
| 3.9 | `money` | `financial.*`, `plaid.*`, `money_flow.*`, `assumption.*` | `money_flows` | Amounts stored as `amount_minor` (smallest currency unit) + ISO 4217 currency. Links to artifact, account, interaction. |
| 3.10 | `vector_search` | `interactions.*`, `artifacts.*`, `parties.*` (non-privileged only) | `vector_index` virtual table via `sqlite-vec` | **Excludes privileged content** — `sensitivity='privileged'` is never embedded; privileged rows cannot enter cross-party semantic search. |
| 3.11 | `xlsx_workbooks` | all forward-trigger event families (tasks, recurrences, commitments, parties, list_items, money_flows, accounts, assumptions, plaid.sync, etc.) | `~/.adminme/projections/adminme-ops.xlsx`, `adminme-finance.xlsx` + sidecar `.xlsx-state/<workbook>/<sheet>.json` | **Bidirectional.** Forward daemon (`xlsx_sync/forward.py`) debounces 5s on event bursts and regenerates; reverse daemon (`xlsx_sync/reverse.py`) watches via `watchdog` and emits events on human edits. Derived cells silently ignored on reverse (UX not security). Lock contention resolved by skipping the reverse cycle. Computed values, not Excel formulas, for reproducibility + audit + round-trip safety. |

Per BUILD.md §L3-continued, there is no global DB connection — all reads/writes go through `Session(current_user, requested_scopes)` which auto-adds scope predicates. Privileged events never enter `vector_search`, are never summarized by LLM skills, never appear in cross-owner projections, and never appear in coach or `-kids` agent sessions. Sensitivity floor is enforced at the adapter level.

---

## 5. Pipelines

Per BUILD.md §L4, pipelines subscribe to events and produce derived events, proposals, or skill calls. Pipelines never write projections directly — they emit events, and projections consume those. Each pipeline lives in `adminme/pipelines/<namespace>/<name>/` with a `pipeline.yaml` manifest, `handler.py`, and `tests/`; plugin pipelines register via the `adminme.pipelines` entry point. Independently enable-able via `config/pipelines.yaml`.

Two trigger mechanisms, chosen per pipeline (per BUILD.md §L4):

- **Reactive, event-subscription pipelines** run inside the AdministrateMe pipeline runner — pure AdministrateMe-layer code that subscribes via `triggers.events` in the manifest. Skills (when needed) go through the skill runner wrapper described below.
- **Proactive, scheduled pipelines** are registered as **OpenClaw standing orders** at product boot. OpenClaw handles the scheduling primitive, approval gating via `exec-approvals`, and channel delivery — giving proactive behaviors the same session/rate-limit/observation-mode context as an interactive chat turn. See §11 for the open question about exactly how this registration is done.

**Reactive pipelines** (one line each):
- `identity_resolution` — on new identifiers, exact-match or Levenshtein-similarity merge suggestions (never auto-merge above threshold; always human-confirmed).
- `noise_filtering` — classify inbound messages as noise/transactional/personal/professional/promotional via `classify_message_nature@v2`.
- `commitment_extraction` — scan interactions for implied obligations; output `CommitmentProposed` events for human approval.
- `thank_you_detection` — specialization of commitment extraction for gratitude; owner-scoped thank-you commitments.
- `recurrence_extraction` — birthdays from contacts, anniversaries from notes, renewals from parsed docs, service intervals from manuals — all as `recurrence.proposed`.
- `artifact_classification` — OCR → classify → typed structured extraction per kind (invoice, contract, medical_record, school_form, etc.).
- `relationship_summarization` — nightly 3-sentence Party summaries over a rolling 90-day window; writes to `Party.attributes`.
- `closeness_scoring` — nightly Party tier (1–5) from interaction frequency, mutuality, explicit labels, time-since-last-contact.
- `reminder_dispatch` — every 15 min; queries commitments/recurrences/tasks due within lead time; emits `reminder.surfaceable` (observation-mode aware).

**Proactive pipelines** (registered as OpenClaw standing orders; one line each):
- `morning_digest` — per-member at ~06:30 local; validation-guarded (any fabricated id zeroes the message with a sentinel).
- `reward_dispatch` — on `task.completed`/`commitment.completed`; reads profile reward mode (variable_ratio / event_based / child_warmth); picks persona template; emits `adminme.reward.dispatched`.
- `paralysis_detection` — per ADHD-profile member at 15:00 and 17:00 local; deterministic (never invokes LLM); uses persona `paralysis_templates.yaml`.
- `whatnow_ranking` — on-demand via `/whatnow`; pure deterministic scoring over tasks + commitments (energy, effort, location, urgency, endowed-progress); per-profile K (carousel=1, compressed=5, power=10).
- `scoreboard_projection` — maintains streaks, completion rates, grace tokens per member; feeds wall displays + kid scoreboard.
- `custody_brief` — 20:00 local daily if a coparent Relationship exists; compose via `compose_custody_brief@v1`.
- `crm_surface` — weekly + on-demand; emits `crm.gap_detected`, `crm.birthday_upcoming`, `crm.hosting_imbalance`.
- `graph_miner` — nightly 03:00 on `adminme-vault` if present else on hub; proposes parties, interactions, commitments, money flows from captures — all proposals, never auto-committed.

**Skill runner wrapper** (per BUILD.md §L4-continued, "THE SKILL RUNNER (LAYERED ON OPENCLAW)"). AdministrateMe **does not run its own LLM loop**. Every skill call flows through OpenClaw's skill runner. The AdministrateMe wrapper (`await run_skill(skill_id, inputs, ctx)`): validates inputs against `input.schema.json` → checks sensitivity (refuses privileged inputs unless skill declares `sensitivity_required: privileged`) → checks `context_scopes_required` ⊆ Session scopes → invokes `POST http://127.0.0.1:18789/skills/invoke` with `{skill_name, inputs, correlation_id, session_context, dmScope}` → optional `handler.py` `post_process` → validates output against `output.schema.json` → emits `skill.call.recorded` with full provenance (skill name, version, `openclaw_invocation_id`, inputs, outputs, provider, token counts, cost, duration, correlation_id) → returns validated output. **AdministrateMe does NOT talk directly to Anthropic / OpenAI / Ollama** — OpenClaw is the only LLM client on the host; OpenClaw owns provider routing, retries, token accounting, and cache policy. Every skill call is replayable via `adminme skill replay <skill_name> --since <ts>`, which re-runs and emits new records with `causation_id` pointing to the old call.

---

## 6. Security + privacy model

Per BUILD.md §AUTHORITY, OBSERVATION, GOVERNANCE and the twelve patterns in CONSOLE_PATTERNS.md, security lives at the intersection of the console (Node at `:3330`), the Python APIs (loopback), and the event log.

- **`guardedWrite` three layers** (per CONSOLE_PATTERNS.md §3 + BUILD.md §AUTHORITY). Every console write — and every pipeline write that routes through the HTTP bridge — passes through `console/lib/guarded_write.js` in strict order: (1) **agent allowlist** (is this agent even permitted to *attempt* this action?), (2) **governance `action_gate`** from `config/governance.yaml` / `config/authority.yaml` — values `allow` / `review` / `deny` / `hard_refuse`; `hard_refuse` items (send_as_principal, auto-answer unknown coparent, reference privileged medical/legal in outbound) are never overridable, and `review` holds the payload in a review queue and returns 202 `held_for_review` via a `review_request` event; (3) **sliding-window rate limit** keyed by `${tenantId}:${scope}:${action}`. Short-circuits on the first denial; the denial layer (`allowlist` / `governance` / `rate_limit`) is recorded on the denial event.
- **authMember vs viewMember** (per CONSOLE_PATTERNS.md §2 and DIAGRAMS.md §4). **authMember** governs what you can do; **viewMember** governs whose data you are reading. The split matters when a principal view-as'es another principal: data is B's, privacy filtering is "what can authMember=A see of B's data?". Writes always use authMember (never viewMember). Children cannot view-as (enforced server-side regardless of UI); ambient entities cannot be viewed-as (no surface). Two-member commitments capture both ids separately (`approved_by=A`, `owner=B`) — do not collapse.
- **Scope enforcement sites** (per DIAGRAMS.md §5 + BUILD.md §L3-continued). There is no global DB connection. All reads/writes go through `Session(current_user, requested_scopes)`. Enforcement is defense-in-depth across: session construction, projection queries (auto-added `WHERE visibility_scope IN (allowed_scopes) AND (sensitivity != 'privileged' OR owner_scope = current_user)`), privacy filter at read, nav middleware (HIDDEN_FOR_CHILD), `guardedWrite`, the outbound filter (observation), and the observation-mode wrapper. Every projection test includes a canary: reading outside scope raises `ScopeViolation`. Static analysis rule: no code imports `sqlalchemy.orm.Session` directly.
- **Observation mode** (per CONSOLE_PATTERNS.md §11 and DIAGRAMS.md §9). Enforced at the **final outbound filter** — not at the policy layer and not at the action-decision layer. All internal logic (pipelines, skill calls, projection updates, local console UI, reward previews) runs normally; only the external side effect is suppressed and recorded as `observation.suppressed` with the full would-have-been payload. Per-tenant (not per-agent). **Default ON for new instances** — the bootstrap wizard ends with observation enabled; the principal opts out explicitly after review. Env var `ADMINME_OBSERVATION_MODE`, runtime override in `config/runtime.yaml` via `adminme observation on|off`, persisted in `tenant_config`.
- **HIDDEN_FOR_CHILD** (per CONSOLE_PATTERNS.md §7). Two-part enforcement: a **client-side nav filter** (canonical list in `console/lib/nav.js` — Inbox, CRM, Capture, Finance, Calendar, Settings hidden for child role; Today + Scoreboard always visible) plus a **server-side prefix blocklist** (`CHILD_BLOCKED_API_PREFIXES` covers `/api/inbox`, `/api/crm`, `/api/capture`, `/api/finance`, `/api/calendar`, `/api/settings`, `/api/tasks`, `/api/chat`, `/api/tools`). Client-side is UX; server-side is security. The two arrays are deliberately independent (e.g. `/api/chat` is server-blocked but has no nav entry because chat is a FAB). Child sees schedule only via `/api/scoreboard/schedule`, chores only via `/api/scoreboard/chores`.
- **Calendar privacy filter** (per CONSOLE_PATTERNS.md §6). Applied at **read time**, not ingest time — events remain intact in the `calendars` projection; only the view is censored. Sensitivity levels: `normal` (all household), `sensitive` (principals + owner), `privileged` (owner only; non-owners get opaque `[busy]` blocks with time/duration and an optional first-name owner hint). `redactToBusy` is allowlist-shaped (start empty, add back what's safe) so new fields on the Event type don't accidentally leak. Children get a **second layer**: events tagged `finance`, `health`, `legal`, or `adult_only` are dropped regardless of sensitivity.
- **Privileged-access log.** Every read of a `sensitivity=privileged` record by anything other than its owner is logged with actor identity, target event/row, call stack, and timestamp. Surfaces in `adminme audit privileged-access` so the tenant can verify no cross-contamination. Adapters configured as privileged (e.g. a law-practice email account) have a hardcoded sensitivity floor at the adapter level; the config loader rejects any configuration that would lower it.
- **Rate limits** (per BUILD.md §AUTHORITY rate_limits). Proactive-per-member-per-day varies by profile (adhd_executive=15, minimalist_parent=3, power_user=25, kid_scoreboard=0). Global `writes_per_minute=60`, `skill_calls_per_hour=200`. Plus per-action sliding windows in `config/governance.yaml` (e.g. `web_chat`: 20 calls per 60s).

---

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
