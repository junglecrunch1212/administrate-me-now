# Prompt 01b: System invariants document

**Phase:** Phase A. Runs after prompt 01 (read artifacts), before prompt 02 (scaffold).
**Depends on:** Prompt 01. `docs/architecture-summary.md` and `docs/openclaw-cheatsheet.md` exist.
**Estimated duration:** 2 hours.
**Stop condition:** `docs/SYSTEM_INVARIANTS.md` exists, under 800 lines, and every invariant is explicit and cross-referenced. Every subsequent prompt (02-19) will read this first.

---

## Read first (required)

1. `docs/architecture-summary.md` (your own work from prompt 01).
2. `docs/openclaw-cheatsheet.md` (your own work from prompt 01).
3. `ADMINISTRATEME_BUILD.md` — you should have already read it in prompt 01. Re-open specific sections as you work: L2 invariants, L3 projection contract, L4 pipeline boundaries, AUTHORITY / OBSERVATION / GOVERNANCE, and BOOTSTRAP WIZARD.
4. `ADMINISTRATEME_DIAGRAMS.md` — all 10 diagrams. The diagrams encode relationships; this prompt makes those relationships explicit in prose.
5. `ADMINISTRATEME_CONSOLE_PATTERNS.md` — the patterns encode Console-to-Python relationships.
6. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` — the worked examples show concrete cross-cutting behavior.

## Operating context

Prompt 01's `architecture-summary.md` covers the **layers** — L1-L5, each self-contained. This prompt covers the **relationships between layers and projections**, the invariants that hold across the system, and the *explicit non-connections* (things the spec says should NOT happen). These are the load-bearing constraints that, if violated by any later prompt, produce subtle bugs nobody notices until production.

The document you produce here is **the constitutional reference for the build**. Every subsequent prompt reads it first. Claude Code in future sessions will consult it when making any decision about how two components should interact.

**Required invariants (must be covered explicitly).** In addition to whatever you derive from reading the specs, the following two invariants MUST appear in the final document, stated precisely and cited. They are load-bearing and commonly-missed:

1. **Proactive-behavior scheduling boundary.** User-visible proactive behavior (morning digest, paralysis detection, reminder dispatch, reward dispatch, custody brief, CRM surface) fires via OpenClaw standing orders. APScheduler (in-process) is used ONLY for internal Python-only schedules: adapter polling cadences, bus heartbeat, xlsx forward/reverse watchers. No proactive user-visible behavior is implemented as an APScheduler job. [BUILD.md §L4, prompt 10c]

2. **Instance-path resolution discipline.** No module under `adminme/` (the Python package) hardcodes the string `~/.adminme/` or any subpath of it. All instance-directory paths come from an `InstanceConfig` object that is populated at service-start time from the config files in the instance directory. Tests use an isolated tmp path; the bootstrap wizard populates `~/.adminme/`; production code resolves paths through config. Violations are caught by a grep-based canary test. [BUILD.md §"Tenant-agnostic codebase", diagnostic d01]

These are in addition to the invariants you derive from the specs; they're listed here because past builds have violated them silently.

## Objective

Produce `docs/SYSTEM_INVARIANTS.md` — a cross-cutting contract document. It is NOT a tutorial or a design doc — it's a list of invariants, each stated precisely, each citing its authoritative source.

Target length: 500-800 lines. Shorter than that and you've under-specified; longer and you've copied material that belongs in other docs.

## Out of scope

- Do NOT restate the five-layer architecture (that's in `architecture-summary.md`).
- Do NOT explain how individual components work internally — only how they connect.
- Do NOT speculate or add invariants not derivable from the specs. If you think something should be invariant but it's not stated in the specs, list it in a "Proposed invariants" section at the end for operator review.
- Do NOT write code. This is prose.

## Deliverables

### `docs/SYSTEM_INVARIANTS.md`

Structure — each section contains a list of numbered invariants, each one stated as a single sentence with a citation:

```markdown
# AdministrateMe system invariants

_The constitutional reference for the build. Every subsequent prompt (02-19) reads this before acting. If any invariant below is violated by any code being written, stop and flag it._

Version: 1.0 (produced by prompt 01b, <date>)

Format: each invariant is numbered, stated in one sentence, and cites its source. Citations look like `[BUILD.md §L2]` or `[DIAGRAMS.md §4]` or `[CONSOLE_PATTERNS.md §6]`.

---

## Section 1: The event log is sacred

1. The event log is the only source of truth; every other persistent state can be rebuilt from it. [BUILD.md §L2]
2. The event log is append-only; no event is ever deleted or modified after write. [BUILD.md §L2]
3. The only way to write to the event log is through `EventLog.append()`, which validates the payload against the schema registry before insertion. [BUILD.md §L2, prompt 04]
4. Every event has `type`, `version`, `tenant_id`, `owner_scope`, `event_at_ms`, and `payload` at minimum; additional fields include `correlation_id` and `source`. [BUILD.md §L2]
5. Events are partitioned by `owner_scope` (either `"household"` or `"private:m-<member_id>"`); cross-scope queries require explicit session permission. [BUILD.md §L2]
... etc.

## Section 2: Projections are derived; they are never truth

1. Every projection is a pure function of the event log + its own handler logic; calling `projection.rebuild(name)` must produce byte-identical state to the live projection at a given cursor. [BUILD.md §L3, diagnostic d03]
2. Projections NEVER write back to the event log. They are read-only consumers. [BUILD.md §L3]
3. Projection handlers must be deterministic — no wall-clock calls, no random, no UUIDs, no side effects, no calls to other projections or the event log. [diagnostic d03]
4. There are exactly 11 projections (named in prompt 05/06/07). Any additional projection is introduced only via an ADR. [BUILD.md §L3 table]

## Section 3: The CRM spine — parties, interactions, artifacts

1. `parties` is the spine of the CRM; every entity that can be addressed, messaged, or referenced (humans, organizations, households) is a party. [BUILD.md §3.1]
2. `parties` is written by events from the identity_resolution pipeline (10b) and by direct operator edits via the CRM surface. [prompt 05, prompt 14b]
3. Every party has a stable `party_id` that never changes, even when parties are merged (merge creates a `party.merged` event; all references still resolve). [BUILD.md §3.1]
4. Parties are uniquely identified by (tenant_id, party_id); the same email or phone number across tenants produces different party_ids. [BUILD.md §3.1]
5. Household members are parties with a `membership` record linking them to the household; they have `member_id` in addition to `party_id`. [BUILD.md §3.1]
6. `interactions` records every touchpoint between parties (messages sent/received, calls, in-person); it is append-only and never edited — it's a timeline. [BUILD.md §3.2]
7. `artifacts` records documents, images, and structured records that reference parties; artifact events include the list of referenced party_ids. [BUILD.md §3.3]

## Section 4: Commitments, tasks, recurrences — the domain spine

1. A commitment is an obligation one party owes to another; it always has `owed_by_member_id` (household member) and `owed_to_party_id` (any party). [BUILD.md §3.4, REFERENCE_EXAMPLES §5]
2. Commitments are proposed by pipelines (not directly created by surfaces); surfaces confirm or dismiss. [BUILD.md §L4 commitment_extraction]
3. A task is a discrete actionable item in a member's queue; tasks can derive from commitments (task.source=commitment) or be standalone (task.source=direct). [BUILD.md §3.5]
4. Completing a task that derives from a commitment emits `task.completed` AND triggers `commitment.check_fulfilled` logic; the commitment may or may not be fulfilled depending on its kind. [BUILD.md §3.5]
5. Recurrences are templates; they generate task events on schedule but are NOT tasks themselves. [BUILD.md §3.6]
6. Commitments are NEVER completed by recurrence — only by explicit confirmation or by task.completed that marks them fulfilled. [BUILD.md §3.4]

## Section 5: Calendar and its relationship to tasks/recurrences

1. Calendar is populated by external adapters (Google Calendar, iCloud CalDAV); AdministrateMe NEVER directly creates calendar events to the external source unless explicitly configured. [BUILD.md §3.7, prompt 11]
2. Calendar events are read-only from AdministrateMe's perspective; modifications flow the other way (external → internal projection). [BUILD.md §3.7]
3. A task or recurrence with a `scheduled_at` does NOT create a calendar event; scheduled_at is an internal time hint only. [BUILD.md §3.5, §3.6]
4. Calendar and task queries can overlap semantically ("what's on my calendar today" vs. "what do I have to do today") but are backed by different projections; surfaces that show both must merge them at read time, never at write time. [BUILD.md §3.7]
5. Private calendar events from other members are redacted to busy-blocks (start_time, duration only) when queried by a non-owner, per the privacy filter. [CONSOLE_PATTERNS.md §6, DIAGRAMS.md §5]

## Section 6: Security — session, scope, governance, observation

1. Every read and every write happens under a `Session` object that carries (auth_member_id, auth_role, view_member_id, view_role, dm_scope, tenant_id). [BUILD.md "L3 CONTINUED", CONSOLE_PATTERNS.md §2]
2. `view_as` is allowed only for principal sessions; child, ambient, and coach-session sessions cannot view-as anyone. [DIAGRAMS.md §4]
3. Writes route through `guardedWrite`, which checks three layers in order: agent allowlist → governance action_gate → rate limiter. Any layer's refusal is final. [CONSOLE_PATTERNS.md §3, DIAGRAMS.md §3]
4. Observation mode is a single enforcement point in `outbound()`; when active, no external side effect fires and an `observation.suppressed` event is emitted instead. [DIAGRAMS.md §9, diagnostic d06]
5. EVERY outbound path (L5 surfaces, L4 pipelines, L1 adapters that can send) must call `outbound()`; emitting `external.sent` anywhere else is a bug. [diagnostic d06]
6. Privileged events are those with `sensitivity: privileged` in source metadata; queries that would return privileged data redact them unless the requesting session owns them. [CONSOLE_PATTERNS.md §6]
7. `HIDDEN_FOR_CHILD` routes (anything that's not today + scoreboard) return 403 for child sessions. [CONSOLE_PATTERNS.md §7]

## Section 7: Pipelines — reactive and proactive

1. Reactive pipelines run in-process via the AdministrateMe PipelineRunner; they subscribe to the event bus. [BUILD.md §L4, prompt 10a/10b]
2. Proactive pipelines run as OpenClaw standing orders; OpenClaw's scheduler fires them; their handler endpoints live in AdministrateMe's product APIs. [BUILD.md §L4, prompt 10c]
3. No pipeline writes directly to projections; pipelines emit events; projections consume them. [BUILD.md §L4]
4. Pipelines use skills via `run_skill()` (which calls OpenClaw `/skills/invoke`); pipelines NEVER call LLM providers directly. [BUILD.md §L4 skill runner, prompt 09a]
5. Every skill call emits `skill.call.recorded` with full provenance (inputs, outputs, tokens, cost, correlation_id). [prompt 09a]
6. A pipeline that fails on one event does NOT halt the bus; it logs the failure, skips the event (or retries per policy), and continues. [prompt 10a]

## Section 8: OpenClaw boundaries

1. OpenClaw is the assistant substrate; AdministrateMe layers on top via four seams: skills, slash commands, standing orders, channel plugins. [BUILD.md "OPENCLAW IS THE ASSISTANT SUBSTRATE"]
2. OpenClaw owns all LLM provider contact; AdministrateMe never imports the anthropic or openai SDK. [BUILD.md §L4]
3. OpenClaw owns all channel transport (iMessage via BlueBubbles, Telegram, Discord, web); AdministrateMe receives via plugin adapters. [prompt 12]
4. The memory bridge plugin ingests OpenClaw's session state into the event log; it's one-way (OpenClaw → AdministrateMe). [prompt 12]
5. When `outbound()` in AdministrateMe wants to send on a channel, it calls OpenClaw's channel-send API via the channel bridge plugin; AdministrateMe does NOT open WebSocket to BlueBubbles directly. [prompt 12]
6. Slash command handlers live in AdministrateMe (as HTTP endpoints in Python product APIs); OpenClaw dispatches to them when a user types the command. [BUILD.md §L5]

## Section 9: Console is a rendering + authorization layer

1. The Node console at :3330 is the only tailnet-facing surface; Python product APIs are loopback-only. [BUILD.md §L5]
2. The Node console never writes to projections directly; every write goes through the Python product APIs over the HTTP bridge. [CONSOLE_PATTERNS.md §10]
3. The Node console resolves authMember from the Tailscale identity header (`X-Tailscale-User-Login`); it does not implement its own auth. [CONSOLE_PATTERNS.md §1]
4. Reward toast is emitted dual-path: sync preview inline + SSE canonical after pipeline runs; the two are deduplicated by correlation_id. [CONSOLE_PATTERNS.md §8]
5. SSE chat is a pass-through proxy to OpenClaw's `/agent/chat/stream`; the console adds correlation_id and applies rate limit before opening the upstream connection. [CONSOLE_PATTERNS.md §5]
6. Degraded mode activates when Python API reachability drops below threshold; the console shows cached data with age hints and queues writes locally. [CONSOLE_PATTERNS.md §9]

## Section 10: xlsx is a bidirectional projection

1. xlsx is unique among projections: it's bidirectional. Forward daemon regenerates the workbook from events; reverse daemon emits events from user edits to the workbook. [BUILD.md §3.11]
2. The sidecar state JSON records "what the workbook currently represents"; forward writes it in the same lock as the xlsx write; reverse reads it to detect user edits. [BUILD.md §3.11]
3. Protected cells cannot be edited by the user; reverse daemon excludes them from diff. [BUILD.md §3.11]
4. xlsx writes in both directions are debounced (5s forward, 2s reverse) to avoid thrash. [BUILD.md §3.11]
5. A forward-write triggering a reverse-detect is a bug per diagnostic d08; the fix is sidecar determinism + lock ordering. [diagnostic d08]

## Section 11: Bootstrap is a one-time, resumable operation

1. Bootstrap runs once per instance and produces `~/.adminme/` in its canonical shape per BUILD.md SAMPLE INSTANCE. [prompt 16]
2. Bootstrap is resumable: `bootstrap-answers.yaml.enc` stores answers; event log records section completions; re-running picks up at the first incomplete section. [prompt 16]
3. Lab mode (`--lab-mode`) uses fixture credentials and mock services; real mode uses real credentials. Lab mode is Phase A testable; real mode is Phase B only. [prompt 16]
4. Bootstrap's `Section 9: Final readiness check` verifies every layer produces expected events, projections rebuild to matching state, and OpenClaw registrations match the registration-queue YAMLs. [prompt 16]

## Section 12: Tenant isolation

1. AdministrateMe instances are single-tenant by deployment but multi-tenant at the code level; every event, projection row, and config value carries a `tenant_id`. [BUILD.md §L2]
2. Two instances of AdministrateMe (e.g., Stice household + some other household) must never share an event log, projection DB, or config directory. [BUILD.md]
3. Tenant_id is assigned at bootstrap time and is immutable thereafter. [prompt 16]

## Section 13: Explicit non-connections (things that look related but aren't)

1. `commitment.confirmed` and `task.completed` are different events with different semantics; a task's completion does not necessarily fulfill a commitment (a commitment may require multiple tasks or specific conditions). [BUILD.md §3.4, §3.5]
2. `calendar.event_added` from an external source does NOT create a commitment, task, or recurrence; calendar is independent of domain events until a pipeline explicitly bridges them. [BUILD.md §3.7]
3. `noise.filtered` does NOT delete the originating event; the event is still in the log, just flagged. [prompt 10b]
4. `identity.merge_suggested` does NOT auto-merge parties; merging requires explicit operator confirmation via the CRM surface. [prompt 10b]
5. Morning digest composition does NOT read from projections directly; it reads via the Python product APIs over HTTP like any other caller. [BUILD.md §L4, §L5]
6. The console is NOT a pipeline host; proactive behaviors run as OpenClaw standing orders, not in the console's process. [BUILD.md §L5, §L4]

## Section 14: Proposed invariants (operator review)

_Invariants I (Claude Code) suspect are true based on reading the specs but that aren't stated explicitly. Operator should confirm or reject each before prompt 02 begins._

1. ??? (list any that came up during reading)
```

## Verification

```bash
# Line count
wc -l docs/SYSTEM_INVARIANTS.md
# Should be between 400 and 900 lines.

# Every invariant has a citation (rough check)
grep -cE '\[(BUILD\.md|CONSOLE_PATTERNS\.md|REFERENCE_EXAMPLES\.md|DIAGRAMS\.md|prompt|diagnostic)' docs/SYSTEM_INVARIANTS.md
# Count should be close to the total number of numbered invariants. If it's off by more than 10%, you have uncited claims — fix them.

# Every section present
for n in $(seq 1 14); do
  grep -q "^## Section $n:" docs/SYSTEM_INVARIANTS.md || echo "MISSING Section $n"
done

git add docs/SYSTEM_INVARIANTS.md
git commit -m "phase 01b: system invariants document"
git push
```

## Stop

**Explicit stop message:**

> System invariants document in the repo. Every subsequent prompt (02-19) reads it first. Section 14 lists proposed invariants that need operator review — please confirm or reject each before prompt 02.
>
> If the operator spots a missing invariant (a load-bearing constraint I should have captured but didn't), re-run this prompt with the addition.
