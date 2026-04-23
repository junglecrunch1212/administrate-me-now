**Phase + repository + documentation + sandbox discipline.**

You are in Phase A on https://github.com/junglecrunch1212/administrate-me-now. Prompts 01a and 01b produced `docs/openclaw-cheatsheet.md` and `docs/architecture-summary.md` (both already merged to main). This is prompt 01c: produce `docs/SYSTEM_INVARIANTS.md` — the constitutional reference for the build.

**Why this document exists.** The architecture summary describes the layers. This document describes the **relationships between layers**, the **load-bearing constraints** that hold across the system, and the **explicit non-connections** (things that look related but aren't). Every subsequent prompt (02 through 19) reads `SYSTEM_INVARIANTS.md` first, before acting. If any code being written violates an invariant here, the prompt stops and flags it.

**Why this prompt is structured the way it is.** The same anti-timeout structure as prompt 01b: targeted `view_range` reads driven by a pre-verified line-range table, written across **four batch commits** so a timeout loses at most one batch. You're also cross-referencing two small already-merged docs (`architecture-summary.md` and `openclaw-cheatsheet.md`) which compress a lot of re-reading — the summary already states most of the layer-level facts, so this prompt can cite it for most sections and only pull targeted ranges from the raw specs when a specific invariant requires wording from the source.

---

## Session start

```bash
git checkout main
git pull origin main
git checkout -b phase-01c-system-invariants
# (harness will override with claude/<random>; work on whatever it assigns)
```

Verify both prior deliverables are on main before doing anything else:

```bash
ls -la docs/openclaw-cheatsheet.md docs/architecture-summary.md
wc -l docs/openclaw-cheatsheet.md docs/architecture-summary.md
```

If either file is missing, STOP — prompt 01a or 01b's PR has not been merged yet. Report to the operator and exit.

---

## Reading strategy — MANDATORY RULES

**Read first (in full, once).** These two files are small and are your primary cross-reference.

1. `docs/architecture-summary.md` — cite as `[arch §N]`. This already states most layer-level facts; most invariants can be written as "X, per [arch §N]" without going back to the specs.
2. `docs/openclaw-cheatsheet.md` — cite as `[cheatsheet Qn]`. Read once; covers the OpenClaw boundary.

These are both <300 lines. Reading them fully at session start is cheap and drops per-section re-reads later.

**Targeted reads only after that.** You will cite four spec artifacts at the repo root:

| File | Lines |
|---|---|
| `ADMINISTRATEME_BUILD.md` | 3,503 |
| `ADMINISTRATEME_CONSOLE_PATTERNS.md` | 1,836 |
| `ADMINISTRATEME_REFERENCE_EXAMPLES.md` | 2,938 |
| `ADMINISTRATEME_DIAGRAMS.md` | 1,118 |

**Rules that apply to every single `view` call after the two whole-file reads:**

1. **Never `view` a spec artifact without `view_range`.** Every call must pass `view_range=[start, end]`.
2. **Keep each `view_range` under ~200 lines.** Split longer sections into two reads.
3. **Never re-read a section you already read.** The summary already quotes most of these — cite the summary instead of re-opening the source unless an invariant needs exact spec wording.
4. **Do NOT read OpenClaw docs.** The cheatsheet is your interface to OpenClaw.
5. **Do NOT use Explore sub-agents.** They reload output into your main context, defeating the budget.
6. **Do NOT `view` CONSOLE_REFERENCE.html or ADMINISTRATEME_FIELD_MANUAL.md.** Not spec.

### Navigation procedure

For each section of the invariants document:
1. Write the section draft first, using `[arch §N]` and `[cheatsheet Qn]` citations wherever possible.
2. Only open a spec artifact if the invariant needs wording not already captured in the summary (e.g. exact enum values, exact table names, exact field semantics).
3. When you do open a spec, use the pre-verified line-range table below.

### Pre-verified line ranges (for sections needing source wording)

These point to section **headers**; read from the header through the line before the next one. If a range is >200 lines, split.

| Invariants section | Primary reads (if arch summary insufficient) |
|---|---|
| §1 Event log sacred | BUILD.md 84–128 (21 critical operating rules, ~45 — cite specific rules by number), 377–459 (L2 EVENT LOG + BUS, ~82) |
| §2 Projections derived | BUILD.md 460–534 (L3 intro + 3.1, ~75) — mostly just cite arch §4 |
| §3 CRM spine | BUILD.md 474–624 (3.1 parties + 3.2 interactions + 3.3 artifacts intro) — mostly cite arch §4 |
| §4 Commitments, tasks, recurrences | BUILD.md 601–713 (3.4–3.6) — mostly cite arch §4 |
| §5 Calendar relationships | BUILD.md 682–713 (3.7), REFERENCE_EXAMPLES.md 2037–2245 (commitment event type example — only if needed) |
| §6 Session + scope + governance + observation | BUILD.md 2053–2168 (AUTHORITY/GOVERNANCE/OBSERVATION, ~115), BUILD.md 1074–1106 (SESSION & SCOPE ENFORCEMENT, ~33); CONSOLE_PATTERNS.md 145–291 (§2 authMember, ~145), 292–420 + 420–560 (§3 guardedWrite), 1576–1689 (§11 observation) |
| §7 Pipelines reactive vs proactive | BUILD.md 1107–1266 (PIPELINES, ~160 — split 1107–1200 and 1200–1266); 1267–1337 (SKILL RUNNER, ~70) — mostly cite arch §5 |
| §8 OpenClaw boundaries | No new reads — cite `cheatsheet Q1–Q7` and `arch §2` |
| §9 Console as rendering layer | BUILD.md 1584–1651 (L5 console, ~67); mostly cite arch §8 and CONSOLE_PATTERNS references from the summary |
| §10 xlsx bidirectional | BUILD.md 808–1073 (L3 §3.11 xlsx — split 808–900 and 900–1073) |
| §11 Bootstrap one-time resumable | BUILD.md 2169–2194 (BOOTSTRAP WIZARD, ~25) — mostly cite arch §10 |
| §12 Tenant isolation | BUILD.md 84–128 (rules 4, 17, 18); 3300–3416 (FINAL CHECKS — platform and multi-tenant sections) |
| §13 Explicit non-connections | No new reads — synthesis of what you already read |
| §14 Required: scheduling boundary | No new reads — see "Required invariants" below |
| §15 Required: instance-path discipline | No new reads — see "Required invariants" below |
| §16 Proposed invariants | No new reads — carry forward from arch §11 + things you noticed |

**Budget target:** 0 reads of spec artifacts for most sections (arch summary is enough); ~5–8 targeted reads for sections that need exact spec wording (§1, §6, §10, §12). That's well under 2,000 lines of new context on top of the two whole-file reads.

---

## Required invariants — MUST APPEAR IN THE FINAL DOCUMENT

In addition to invariants you derive from the specs, these two MUST be stated precisely. They are load-bearing and commonly missed. Put them in dedicated sections (§14 and §15 below), not buried inside another section.

### Required §14 — Proactive-behavior scheduling boundary

User-visible proactive behavior (`morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, `custody_brief`, `crm_surface`, `scoreboard_projection`) fires via OpenClaw standing orders — so it shares OpenClaw's approval gating, observation-mode enforcement, and rate-limit machinery with interactive chat turns. APScheduler inside each Python product is used ONLY for internal Python-only schedules: adapter polling cadences, bus heartbeat, xlsx forward/reverse watchers, cache refreshes, projection compaction, log rotation. No proactive user-visible behavior is implemented as an APScheduler job. [arch §5, arch §9, BUILD.md §L5-continued]

### Required §15 — Instance-path resolution discipline

No module under `adminme/` (the Python package) hardcodes the string `~/.adminme/` or any subpath of it. All instance-directory paths come from an `InstanceConfig` object populated at service-start time from the config files in the instance directory. Tests use an isolated tmp path; the bootstrap wizard populates `~/.adminme/`; production code resolves paths through config. Violations are caught by a grep-based canary test. This is the concrete mechanism behind the "tenant-agnostic codebase" rule. [BUILD.md §CRITICAL OPERATING RULES rule 4, rule 17, FINAL CHECKS multi-tenant]

Both must be stated this precisely and cited.

---

## Incremental commit discipline — MANDATORY

Write the invariants across **four commits**, not one. If any turn times out mid-section, the prior commits preserve the prior sections.

### Commit 1 — skeleton + §1–§4

1. Create `docs/SYSTEM_INVARIANTS.md` with the full header block and all 16 section stubs. Each stub is just `## Section N: <title>\n\n_(pending)_\n`.
2. Fill in §1 (event log sacred, ~35 lines), §2 (projections derived, ~25 lines), §3 (CRM spine, ~30 lines), §4 (commitments/tasks/recurrences, ~30 lines).
3. Commit locally: `phase 01c-1: skeleton + §1-§4 (event log, projections, CRM, domain spine)`.
4. **Do not push yet** — push only at the end.

### Commit 2 — §5–§8

1. Fill in §5 (calendar, ~25 lines), §6 (session/scope/governance/observation, ~60 lines — this is the biggest section), §7 (pipelines reactive vs proactive, ~35 lines), §8 (OpenClaw boundaries, ~30 lines).
2. Commit: `phase 01c-2: §5-§8 (calendar, security, pipelines, openclaw)`.

### Commit 3 — §9–§13

1. Fill in §9 (console rendering layer, ~25 lines), §10 (xlsx bidirectional, ~30 lines), §11 (bootstrap, ~20 lines), §12 (tenant isolation, ~25 lines), §13 (explicit non-connections, ~40 lines).
2. Commit: `phase 01c-3: §9-§13 (console, xlsx, bootstrap, tenancy, non-connections)`.

### Commit 4 — §14–§16 + verification + push

1. Fill in §14 (scheduling boundary, ~15 lines — use the Required wording above), §15 (instance-path discipline, ~15 lines — use the Required wording above), §16 (proposed invariants, ~30–50 lines — carry forward arch §11 items + your own observations).
2. Run the verification block below. Fix any failures.
3. Commit: `phase 01c-4: §14-§16 + verification`.
4. `git push origin HEAD`.

**If a turn times out mid-section:** STOP. Don't attempt heroic recovery. The operator resets; the next session picks up from the last commit.

---

## File scaffold for Commit 1

Use this exact opening. Don't add preamble, don't add a manual TOC (the headings are the TOC).

```markdown
# AdministrateMe system invariants

_The constitutional reference for the build. Every subsequent prompt (02 through 19) reads this before acting. If any invariant below is violated by code being written, stop and flag it._

Version: 1.0 (produced by prompt 01c, <date>)

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

_(pending)_

## Section 2: Projections are derived; they are never truth

_(pending)_

## Section 3: The CRM spine — parties, interactions, artifacts

_(pending)_

## Section 4: Commitments, tasks, recurrences — the domain spine

_(pending)_

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
```

---

## Section specifications

Each section is a numbered list of single-sentence invariants with citations. Target lengths below are guidance. Tight > long; each invariant should be one sentence (two at most). Don't pad.

### §1 Event log sacred (~35 lines, ~7–10 invariants)

From BUILD.md §CRITICAL OPERATING RULES (rules 5, 6, 10, 18), §L2, §L2 EVENT BUS, and arch §3. Cover:

- Event log is the only source of truth; every other persistent state rebuildable from it.
- Append-only: no event ever deleted or modified after write (enforced by code + SQLite trigger + a unit test).
- Writes go through `EventStore.append()`, which validates the payload against the Pydantic model for the event type before insertion.
- Transactional append-and-publish: validate, insert, commit, publish to bus — same transaction boundary.
- Row schema fields (list them): `event_id` (ULID), `event_type`, `schema_version`, `occurred_at`, `recorded_at`, `source_adapter`, `source_account_id`, `owner_scope`, `visibility_scope`, `sensitivity`, `correlation_id`, `causation_id`, `payload_json`, `raw_ref`, `actor_identity`.
- Partitioned logically (indexed, not physical) by `owner_scope`; values `private:<member_id>` / `shared:household` / `org:<id>`.
- Payloads >64KB sidecar to `~/.adminme/data/raw_events/`; artifacts encrypted with Fernet keyed from SQLCipher master key.
- Events immutable but correctable via new event with `causation_id` pointing to the original.
- Event types require Pydantic payload model + `schema_version` + upcaster on schema change (rule 18: migrations).
- Two bus implementations against the same Protocol: `InProcessBus` (default, asyncio queues + durable `bus_consumer_offsets`) and `RedisStreamsBus` (alternate); integration tests run against both.

### §2 Projections derived (~25 lines, ~5–7 invariants)

From BUILD.md §L3, arch §4. Cover:

- Every projection is a pure function of the event log plus its own handler logic; `projection.rebuild(name)` must produce state equivalent to live for the same cursor.
- Projections never write back to the event log — they are read-only consumers.
- Projection handlers are deterministic: no wall-clock, no random, no UUIDs, no side effects, no calls to other projections or the event log.
- Each projection has a name, version (bumped triggers rebuild), event-type subscription list, cursor, idempotent `apply(event)`, and `rebuild()`.
- There are exactly 11 v1 projections: `parties`, `interactions`, `artifacts`, `commitments`, `tasks`, `recurrences`, `calendars`, `places_assets_accounts`, `money`, `vector_search`, `xlsx_workbooks`; additional projections require an ADR.
- Plugin projections namespace their tables; projection schema is not part of the authority model.
- Rule-4 corollary: projection schemas never introduce hardcoded tenant identity.

### §3 CRM spine (~30 lines, ~6–8 invariants)

From BUILD.md §3.1, §3.2, §3.3, §THE CRM IS THE SPINE, arch §4. Cover:

- `parties` is the CRM core; every addressable entity (persons, organizations, households) is a Party.
- Every Party has a stable `party_id` (ULID) that never changes; merges emit `party.merged` events and all references still resolve through the identity index.
- Parties uniquely identified by (tenant_id, party_id); the same email/phone across tenants produces different party_ids.
- Household members are Parties with a `Membership` linking them to the household; they have a `member_id` in addition to `party_id`.
- `interactions` records every touchpoint between parties; it is deduplicated (may aggregate multiple raw events per row) and append-only as a timeline — rows never edited.
- `artifacts` records documents/images/structured records that reference Parties via the polymorphic `artifact_links` table.
- The CRM (Parties + Identifiers + Memberships + Relationships + Interactions + Commitments + Artifacts) IS the data model of the whole platform, not a sub-feature of the Capture product.

### §4 Commitments, tasks, recurrences (~30 lines, ~6–8 invariants)

From BUILD.md §3.4, §3.5, §3.6, §CRITICAL OPERATING RULES (rule 6), arch §4. Cover:

- A Commitment is an obligation one party owes to another: `owed_by_party` + `owed_to_party` + `kind` + `description`.
- Commitments are **proposed** by pipelines (never directly created by surfaces); surfaces confirm or dismiss — rule 6's propose-then-commit.
- A Task is household work; tasks can derive from commitments (via linkage) or be standalone.
- Completing a task that derives from a commitment emits `task.completed` AND may trigger commitment fulfillment logic; a commitment may need multiple tasks or specific conditions to be considered fulfilled.
- Recurrences are templates (RFC 5545 RRULE); they generate scheduled occurrences but ARE NOT tasks themselves.
- Commitments are never completed by recurrence firing — only by explicit `commitment.confirmed/completed` or by `task.completed` that marks them fulfilled.
- Every LLM-produced proposal carries `source_skill@version` provenance; direct LLM writes to live state are prohibited (rule 6).

### §5 Calendar and its relationships (~25 lines, ~5–6 invariants)

From BUILD.md §3.7, arch §4, arch §6. Cover:

- Calendar is populated by external adapters (Google Calendar, iCloud CalDAV); AdministrateMe does NOT write back to external calendars unless explicitly configured per adapter.
- Calendar events are effectively read-only from AdministrateMe's perspective; modifications flow external → internal.
- A task or recurrence with a `scheduled_at` does NOT create a calendar event; `scheduled_at` is an internal time hint only.
- Calendar queries and task queries overlap semantically but are backed by different projections; surfaces that show both must merge them at read time, never at write time.
- Privacy filtering is applied at **read** time, not at ingest time; events remain intact in the projection, only views are redacted.
- Private calendar events from other members are redacted to opaque busy blocks (time + duration only) when queried by a non-owner; children get an additional tag-based filter.

### §6 Security — session, scope, governance, observation (~60 lines, ~10–14 invariants)

From BUILD.md §L3-continued + §AUTHORITY/OBSERVATION/GOVERNANCE, CONSOLE_PATTERNS.md §2/§3/§11, DIAGRAMS.md §3/§4/§5/§9, arch §6. This is the biggest section. Cover:

**Session + scope:**
- Every read and write happens under a `Session(current_user, requested_scopes)` object.
- Sessions carry `authMemberId` (write permissions) and `viewMemberId` (data shown); only principals may set view-as; ambient entities cannot be viewed-as.
- Writes always use authMember; viewMember never authorizes a write.
- There is no global DB connection — every query goes through Session and scope predicates are auto-appended.
- `ScopeViolation` is raised on any attempt to read data outside allowed scopes; every projection test has a canary.

**guardedWrite three layers:**
- Every console-originated write passes through `guardedWrite`: (1) agent allowlist, (2) governance `action_gate` (`allow`/`review`/`deny`/`hard_refuse`), (3) sliding-window rate limit — checked in that order.
- `hard_refuse` gates are never overridable (send_as_principal, auto-answer unknown coparent, reference privileged medical/legal in outbound).
- `review` gates emit a `review_request` event and return 202 `held_for_review` instead of firing.
- First layer to refuse short-circuits; the denial event records which layer refused.

**Privacy + privileged:**
- Privileged events (`sensitivity: privileged`) are never embedded into `vector_search`, never summarized by LLM skills, never appear in cross-owner projections, never appear in coach or `-kids` agent sessions.
- Adapters configured as privileged have a hardcoded sensitivity floor; the config loader rejects any configuration that would lower it.
- Every non-owner read of a privileged record is logged to the privileged-access log with actor identity, target, call stack, and timestamp.
- Identity-first privacy is the primary boundary (privileged content never enters the assistant's accounts); session scope is secondary; event-level `sensitivity` is tertiary — all three must be present.

**Observation mode:**
- Observation mode is enforced at the **final outbound filter**, not at the policy layer or action-decision layer — all internal logic runs normally.
- Every outbound-capable action (L5 surfaces, L4 pipelines, L1 adapters that can send) calls `outbound()`; emitting `external.sent` anywhere else is a bug.
- Observation mode is per-tenant, not per-agent or per-skill.
- Observation is **default-on for new instances**; bootstrap §9 ends with observation enabled.
- Suppressed actions emit `observation.suppressed` with the full would-have-sent payload for the tenant to review.

**HIDDEN_FOR_CHILD:**
- Admin surfaces are enforced two ways for child sessions: client-side nav filter AND server-side prefix blocklist; client-side is UX, server-side is security.
- Children see Today and Scoreboard only; `/api/inbox`, `/api/crm`, `/api/capture`, `/api/finance`, `/api/calendar`, `/api/settings`, `/api/tasks`, `/api/chat`, `/api/tools` all return 403 for child sessions.

### §7 Pipelines — reactive and proactive (~35 lines, ~7–9 invariants)

From BUILD.md §L4 + §L4 SKILL RUNNER, arch §5. Cover:

- Reactive pipelines run in-process inside the AdministrateMe PipelineRunner; they subscribe to the event bus via `triggers.events` in their manifest.
- Proactive pipelines run as OpenClaw standing orders; their handlers are Python product API endpoints; OpenClaw owns the scheduling.
- No pipeline writes directly to projections; pipelines emit events, projections consume them.
- Pipelines invoke skills via `run_skill()` which calls OpenClaw's `/skills/invoke` — pipelines NEVER call LLM providers directly.
- Every skill call emits `skill.call.recorded` with full provenance (skill name + version, openclaw_invocation_id, inputs, outputs, provider, tokens, cost, duration, correlation_id).
- A pipeline failure on one event does NOT halt the bus; it logs the failure, skips or retries per policy, and continues.
- Skill wrapper validates inputs against `input.schema.json` and outputs against `output.schema.json`; sensitivity and scope checks happen before invocation.
- AdministrateMe does NOT talk directly to Anthropic / OpenAI / Ollama — OpenClaw is the only LLM client on the host.
- Skill calls are replayable: `adminme skill replay <name> --since <ts>` re-runs and emits new `skill.call.recorded` events with `causation_id` pointing to the old call.

### §8 OpenClaw boundaries (~30 lines, ~6–8 invariants)

From arch §2 and cheatsheet Q1–Q7. No new spec reads needed. Cover:

- AdministrateMe layers on top of OpenClaw via exactly four seams: skills, slash commands, standing orders, channel plugins.
- OpenClaw owns all LLM provider contact; AdministrateMe never imports the `anthropic` or `openai` SDK.
- OpenClaw owns all channel transport (iMessage via BlueBubbles, Telegram, Discord, web); AdministrateMe receives inbound via the `openclaw-memory-bridge` plugin.
- The memory-bridge plugin is one-way (OpenClaw → AdministrateMe) — it emits `messaging.received` and `conversation.turn.recorded` events into the AdministrateMe event log.
- When AdministrateMe's `outbound()` wants to send on a channel, it calls OpenClaw's channel-send API via the channel bridge plugin; AdministrateMe does NOT open transports to BlueBubbles / Telegram / Discord directly.
- Slash command handlers live in AdministrateMe as HTTP endpoints in Python product APIs; OpenClaw dispatches to them when a user types the command.
- OpenClaw's approval gates (tool-execution boundary, host-local) and AdministrateMe's `guardedWrite` (HTTP API boundary) are independent gates — both must pass and neither substitutes for the other.
- OpenClaw memory stays in OpenClaw (`~/.openclaw/`); AdministrateMe event log stays in AdministrateMe (`~/.adminme/`); the two never share a database.

### §9 Console is a rendering + authorization layer (~25 lines, ~5–7 invariants)

From arch §8 and BUILD.md §L5. Cover:

- The Node console at `:3330` is the only tailnet-facing surface; Python product APIs (`:3333`–`:3336`) are loopback-only.
- The Node console never reads the event log directly.
- The Node console never writes to projection SQLite directly; writes proxy to the Python product APIs through the HTTP bridge.
- The Node console MAY read projection SQLite read-only via `better-sqlite3` as a performance optimization for UI rendering; that's the only direct DB access it has.
- The console resolves authMember from the Tailscale `Tailscale-User-Login` header; it does not implement its own auth; there is no login page.
- Dev-mode `X-ADMINME-Member` header requires BOTH `ADMINME_ENV=dev` AND loopback `remoteAddr`; either condition failing rejects the header.
- SSE chat at `/api/chat/stream` is a pass-through proxy to OpenClaw; the console adds correlation_id and rate-limits under `web_chat` before opening the upstream connection.

### §10 xlsx is a bidirectional projection (~30 lines, ~6–8 invariants)

From BUILD.md §3.11, arch §4 xlsx row. Cover:

- xlsx is the only bidirectional projection: forward daemon regenerates the workbook from events; reverse daemon emits events from user edits.
- The sidecar state at `~/.adminme/projections/.xlsx-state/<workbook>/<sheet>.json` records what the workbook currently represents; forward writes it in the same lock as the xlsx write; reverse reads it to detect user edits.
- Derived cells (columns tagged `[derived]` in the header row) cannot be edited by the user; reverse projector silently drops such edits (UX not security).
- Plaid-sourced transaction rows are protected for `date`/`account_last4`/`merchant_name`/`amount`/`plaid_category`; principals may edit `assigned_category`, `notes`, `memo`.
- xlsx writes are debounced: 5s forward, 2s reverse.
- Forward regeneration uses computed values, not Excel formulas — for reproducibility across devices and for audit replayability.
- A forward-write triggering a spurious reverse-detect is a bug; the fix is sidecar determinism + lock ordering (the reverse daemon skips cycles when it sees the forward lock held).
- If the xlsx is deleted or corrupted, `adminme projection rebuild xlsx_workbooks` regenerates fully from an event-log replay.

### §11 Bootstrap is one-time, resumable (~20 lines, ~5 invariants)

From arch §10 and BUILD.md §BOOTSTRAP WIZARD. Cover:

- Bootstrap runs once per instance and produces `~/.adminme/` in its canonical shape.
- Bootstrap is resumable via encrypted `~/.adminme/bootstrap-answers.yaml.enc` + event log; re-running jumps to the first incomplete section.
- Successfully completed sections are idempotent on re-run (no-op — events already in log); config files are never rewritten from stale answers.
- Observation mode is enabled by default at the end of §9; the tenant explicitly opts out after review.
- Bootstrap §1 environment preflight is the only section that aborts on failure; all later sections create inbox tasks for skipped sub-items rather than blocking.

### §12 Tenant isolation (~25 lines, ~5–6 invariants)

From BUILD.md §CRITICAL OPERATING RULES (rules 4, 17, 18) + §FINAL CHECKS (Multi-tenant, Platform level). Cover:

- AdministrateMe instances are single-tenant by deployment but multi-tenant at the code level; every event, projection row, and config value carries `tenant_id`.
- Two instances on the same machine (different `ADMINME_INSTANCE_DIR` values) must be fully independent — no shared event log, projection DB, or config directory.
- `tenant_id` is assigned at bootstrap time and is immutable thereafter.
- No family name, person's name, address, phone number, email, account number, or medical detail appears anywhere in platform code; tenant data lives only in the instance directory (rule 4).
- A static identity scan test (`tests/unit/test_no_hardcoded_identity.py`) fails CI if hardcoded tenant data appears in `adminme/`, `bootstrap/`, `profiles/`, `personas/`, `integrations/`, or `tests/` (except explicitly-marked fixtures).
- Every schema change ships a numbered migration; migrations are idempotent; existing tenants can always upgrade without data loss (rule 18).

### §13 Explicit non-connections (~40 lines, ~7–10 invariants)

This section states things that look related but aren't. Synthesis of what you already read. Cover:

- `commitment.confirmed` and `task.completed` are different events with different semantics; a task's completion does not necessarily fulfill a commitment.
- `calendar.event_added` from an external source does NOT create a commitment, task, or recurrence; calendar is independent of domain events until a pipeline explicitly bridges them.
- `noise.filtered` does NOT delete the originating event; the event is still in the log, just flagged.
- `identity.merge_suggested` does NOT auto-merge parties; merging requires explicit operator confirmation.
- The `xlsx_workbooks` projection is the only projection that writes to disk files; other projections write to SQLite tables only.
- The console is NOT a pipeline host; proactive behaviors run in the Python products (registered as OpenClaw standing orders), not in the console's Node process.
- Morning digest composition does NOT read projections directly; it reads via the Python product APIs over HTTP, like any other caller.
- The `vector_search` projection does NOT include privileged content — privileged rows cannot enter cross-party semantic search.
- Slash commands are NOT the same as REST endpoints — slash commands are registered with OpenClaw; REST endpoints live in Python products; a slash-command handler usually calls a REST endpoint to do its work.
- Tasks projection is AdministrateMe-specific (not Hearth); Commitments projection is Hearth-inherited.

### §14 Proactive-behavior scheduling boundary (~15 lines)

Use the Required §14 wording above, verbatim, expanded to 3–5 numbered invariants. Cover:

- User-visible proactive behaviors fire via OpenClaw standing orders (not APScheduler).
- Enumerate the proactive behaviors: `morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, `custody_brief`, `crm_surface`, `scoreboard_projection`, `graph_miner`.
- APScheduler is reserved for internal, non-user-facing schedules: adapter polling, bus heartbeat, xlsx forward/reverse watchers, cache refreshes, projection compaction, log rotation.
- Anything that could surface to a principal goes through OpenClaw standing orders so it shares the approval, observation-mode, and rate-limit machinery.
- Consequence: if a new proactive behavior is added in a future prompt, it MUST be registered as an OpenClaw standing order; putting it in APScheduler is a violation.

### §15 Instance-path resolution discipline (~15 lines)

Use the Required §15 wording above, verbatim, expanded to 3–5 numbered invariants. Cover:

- No module under `adminme/` hardcodes `~/.adminme/` or any subpath.
- All instance paths resolve through an `InstanceConfig` object constructed from config files in the instance directory at service-start time.
- Tests pass an isolated tmp path to `InstanceConfig`; production code resolves through the real config; the bootstrap wizard populates `~/.adminme/`.
- A grep-based canary test fails CI if `~/.adminme` appears in a non-fixture module under `adminme/`.
- Consequence: two instances on the same machine via `ADMINME_INSTANCE_DIR` work correctly without code changes; this is what makes rule 17 (tenant isolation) actually hold.

### §16 Proposed invariants (operator review) (~30–50 lines)

Invariants you (Claude Code) suspect are true from reading the specs but that aren't stated precisely enough to commit to §1–§15. **Carry forward the six open questions from `docs/architecture-summary.md §11`** as your starting point, then add anything else you noticed while writing §1–§15. Each proposed invariant must:

- Be stated as a concrete one-sentence invariant (not a question).
- Cite where the ambiguity lives.
- End with "_Operator: confirm or reject._"

Example format:

```markdown
1. _Proposed:_ Proactive pipelines register with OpenClaw by writing prose programs into `AGENTS.md` plus `openclaw cron add` invocations at bootstrap §8; there is no programmatic plugin-hook path. [cheatsheet Q3, BUILD.md §L4, arch §11 item 1] _Operator: confirm or reject._
```

Target 6–10 proposed invariants. If you noticed nothing beyond arch §11's six items, 6 is fine — do not pad to hit an arbitrary count.

---

## Final verification (run before Commit 4)

```bash
# Length
wc -l docs/SYSTEM_INVARIANTS.md
# expect 400–900 lines (prompt 01b landed at 231 — invariants will be denser; aim 500–800)

# Every section has citations
for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
  next=$((n+1))
  count=$(awk "/^## Section $n:/,/^## Section $next:/" docs/SYSTEM_INVARIANTS.md \
    | grep -cE "\\[arch §|\\[cheatsheet Q|\\[BUILD\\.md §|\\[DIAGRAMS\\.md §|\\[CONSOLE_PATTERNS\\.md §|\\[REFERENCE_EXAMPLES\\.md §")
  echo "§$n: $count citations"
done
# every section should show ≥ 2 citations (invariants are denser than summary)

# §14 and §15 exist and are non-empty
for n in 14 15; do
  lines=$(awk "/^## Section $n:/,/^## Section $((n+1)):/" docs/SYSTEM_INVARIANTS.md | wc -l)
  echo "§$n: $lines lines"
done
# expect both ≥ 10 lines

# §16 has at least 6 proposed invariants
awk '/^## Section 16:/,0' docs/SYSTEM_INVARIANTS.md | grep -cE "^[0-9]+\. "
# expect ≥ 6

# Required phrasing checks
grep -c "OpenClaw standing orders" docs/SYSTEM_INVARIANTS.md
# expect ≥ 3 (in arch-§5-style citation, §7, §14 at minimum)

grep -c "InstanceConfig" docs/SYSTEM_INVARIANTS.md
# expect ≥ 2 (required §15 mentions it twice)

grep -c "final outbound filter" docs/SYSTEM_INVARIANTS.md
# expect ≥ 1 (required §6 observation wording)
```

If any numbered section shows fewer than 2 citations, add more before the final commit. If §14 / §15 / §16 fail their checks, fix before committing.

---

## Final push (end of Commit 4)

```bash
git log --oneline | head -6   # expect 4 phase 01c-N commits on top of main
git status                    # expect clean working tree
git push origin HEAD
```

---

## Stop condition + summary

When all four commits are pushed and verification is green, produce a brief summary for the operator:

- Branch name (harness-assigned).
- Final line count.
- Citation counts per section (paste the loop output).
- §14, §15 line counts and §16 proposed-invariant count.
- Any deviations from the 4-commit batching strategy.
- Any additional spec ambiguities you hit that weren't captured in §16 (note them explicitly).

Then produce this explicit stop message to the operator:

> System invariants document in the repo. Every subsequent prompt (02 through 19) reads it first. Section 16 lists proposed invariants that need operator review — please confirm or reject each before prompt 02.
>
> If an important invariant is missing (a load-bearing constraint I should have captured but didn't), re-run this prompt with the addition.

Then STOP. Do not open the PR, do not push to main, do not proceed to prompt 02.

---

## If a turn times out

If a single turn times out during this session, the prior commits are preserved. STOP — do not try to recover within the dying session. The operator resets; the next session picks up from the last commit.

The fresh session:
1. Runs `git log --oneline` to see which batches committed.
2. Reads the existing `docs/SYSTEM_INVARIANTS.md` to see which sections are complete and which still say `_(pending)_`.
3. Resumes from the first `_(pending)_` section using the reading strategy + section specs above.
4. Continues the batch-commit discipline from where it left off.

Four commits mean at most one batch (4–5 sections) is lost to any single timeout. Acceptable.
