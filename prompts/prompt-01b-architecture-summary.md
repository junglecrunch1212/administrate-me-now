**Phase + repository + documentation + sandbox discipline.**

You are in Phase A on https://github.com/junglecrunch1212/administrate-me-now. Prompt 01a produced `docs/openclaw-cheatsheet.md` (already merged to main). This is prompt 01b: produce `docs/architecture-summary.md`.

**Why this prompt is structured the way it is.** An earlier attempt at a combined "read everything + produce both deliverables" prompt timed out. Root cause: the session loaded BUILD.md (3,503 lines), CONSOLE_PATTERNS.md (1,836 lines), REFERENCE_EXAMPLES.md (2,938 lines), and DIAGRAMS.md (1,118 lines) into context via sequential full-file reads, then tried to generate ~600 lines of cited prose in one turn. Context exhaustion + long generation = API timeout. This prompt fixes that two ways: **(1)** you navigate the specs via targeted `view_range` reads driven by a line-range table that has already been verified against the files on main; **(2)** you write the summary across **four batch commits** so a timeout mid-session loses at most one batch.

---

## Session start

```bash
git checkout main
git pull origin main
git checkout -b phase-01b-architecture-summary
# (harness will override with claude/<random>; work on whatever it assigns)
```

Verify the cheatsheet is on main before doing anything else:

```bash
ls -la docs/openclaw-cheatsheet.md
wc -l docs/openclaw-cheatsheet.md
```

If `docs/openclaw-cheatsheet.md` is missing, STOP — prompt 01a's PR has not been merged yet. Report to the operator and exit.

---

## Reading strategy — MANDATORY RULES

You will cite four spec artifacts at the repo root:

| File | Lines |
|---|---|
| `ADMINISTRATEME_BUILD.md` | 3,503 |
| `ADMINISTRATEME_CONSOLE_PATTERNS.md` | 1,836 |
| `ADMINISTRATEME_REFERENCE_EXAMPLES.md` | 2,938 |
| `ADMINISTRATEME_DIAGRAMS.md` | 1,118 |

**Rules that apply to every single `view` call in this session:**

1. **Never `view` without `view_range`.** Every call must pass `view_range=[start, end]`. Reading even one file cover-to-cover will exhaust your context budget and kill the generation phase.
2. **Keep each `view_range` under ~200 lines.** If the table below says a section spans more than that, split it into two reads.
3. **Never re-read a section you already read.** If you already pulled BUILD.md 1107–1266, don't pull it again — scroll back in your context if you need something.
4. **Do NOT read OpenClaw docs.** The cheatsheet is your interface to OpenClaw. Cite it (`openclaw-cheatsheet.md Qn`) instead of re-deriving anything.
5. **Do NOT use Explore sub-agents.** They reload their output into your main context when they return, defeating the context budget. Use direct `view` calls.
6. **Do NOT `view` CONSOLE_REFERENCE.html.** It's 134 KB of rendered HTML; the patterns you need are summarized in CONSOLE_PATTERNS.md.
7. **Do NOT `view` ADMINISTRATEME_FIELD_MANUAL.md.** Operator doc, not spec.

### Navigation procedure

For each section of the summary:
1. Check the line-range table below to find which spec section(s) you need.
2. `view` each range directly. Don't grep first — the ranges are pre-verified against current main.
3. Write that summary section.
4. Move on.

If a range in the table looks wrong when you read it, grep the TOC to correct it:
```bash
grep -n "^## \|^### " ADMINISTRATEME_BUILD.md
```

### Pre-verified line ranges

These ranges point to section **headers**; read from the header through the line before the next one. If a range is >200 lines, split into two reads.

| Summary section | Primary reads |
|---|---|
| §1 Five-layer model | BUILD.md 255–324 (~70), DIAGRAMS.md 28–127 (~100) |
| §2 OpenClaw fit | BUILD.md 160–254 (~95); cross-ref cheatsheet Q1–Q7 |
| §3 Event log invariants | BUILD.md 377–459 (L2 EVENT LOG + BUS, ~82), DIAGRAMS.md 128–232 (~105) |
| §4 11 projections | BUILD.md 460–534 (intro + 3.1), 535–624 (3.2–3.3), 625–713 (3.4–3.6), 715–807 (3.7–3.9), 794–900 (3.10 + xlsx header), 900–1073 (3.11 xlsx body) |
| §5 Pipelines | BUILD.md 1107–1266 (~160), 1267–1337 (skill runner, ~70) |
| §6 Security + privacy | BUILD.md 2053–2168 (~115); CONSOLE_PATTERNS.md 145–291 (authMember, ~145), 292–420 (guardedWrite part 1), 420–560 (guardedWrite part 2), 860–993 (privacy filter), 994–1097 (HIDDEN_FOR_CHILD), 1576–1689 (observation); DIAGRAMS.md 340–489 (authMember/session), 787–910 (observation) |
| §7 Packs | BUILD.md 1832–1948 (profile + persona packs, ~115), 1949–1976 (registry, ~27); DIAGRAMS.md 686–786 (~100); REFERENCE_EXAMPLES.md 2913–2938 (install appendix) |
| §8 Console | CONSOLE_PATTERNS.md 1–51 (pattern index + intro); BUILD.md 1584–1651 (~67) |
| §9 Python product APIs | BUILD.md 1652–1803 (~150) |
| §10 Bootstrap wizard | BUILD.md 2169–2194 (~25); DIAGRAMS.md 911–1000 (part 1), 1000–1102 (part 2) |
| §11 Open questions | No new reads — use what you noticed + the three carry-forwards in the section spec |

**Budget target:** roughly 30 reads total at ~100–200 lines each = 3,000–6,000 lines of context accumulated across the session. That leaves enough headroom for four generation commits.

---

## Incremental commit discipline — MANDATORY

You write the summary in **four commits**, not one. If any turn times out mid-section, the prior commits preserve the prior sections so the next session can resume.

### Commit 1 — skeleton + §1–§3

1. Create `docs/architecture-summary.md` with the full header block and all 11 section stubs. Each stub is just `## N. <title>\n\n(pending)\n`. This scaffold lets a future session resume if anything goes wrong.
2. Fill in §1 (~50 lines), §2 (~55 lines), §3 (~40 lines).
3. Commit locally: `phase 01b-1: skeleton + §1-§3 (five layers, openclaw fit, event log)`.
4. **Do not push yet** — push only at the end.

### Commit 2 — §4–§6

1. Fill in §4 (~80 lines; table), §5 (~55 lines), §6 (~55 lines).
2. Commit: `phase 01b-2: §4-§6 (projections, pipelines, security)`.

### Commit 3 — §7–§9

1. Fill in §7 (~50 lines), §8 (~55 lines), §9 (~50 lines).
2. Commit: `phase 01b-3: §7-§9 (packs, console, python apis)`.

### Commit 4 — §10–§11 + verification + push

1. Fill in §10 (~50 lines), §11 (~80–100 lines).
2. Run the verification block below. Fix any failures.
3. Commit: `phase 01b-4: §10-§11 + verification`.
4. `git push origin HEAD`.

**If a turn times out mid-section:** STOP. Don't attempt heroic recovery in the dying session. The operator will reset; the next session picks up from the last commit.

---

## File scaffold for Commit 1

Use this exact opening. Don't add preamble, don't add a table of contents (the headings are the TOC).

```markdown
# AdministrateMe architecture summary

_Produced by prompt 01b. Reference for all later phase prompts. Update if the specs change._

_Cites `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `ADMINISTRATEME_DIAGRAMS.md`, and `docs/openclaw-cheatsheet.md`. Authored in four batch commits to stay within per-turn context limits — see commit history for batching._

## 1. The five-layer model

(pending)

## 2. How OpenClaw fits

(pending)

## 3. Event log invariants

(pending)

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
```

---

## Section specifications

Target lengths are guidance, not hard caps. Stay close to them. Tight prose > long prose.

### §1 Five-layer model (~50 lines)

Per BUILD.md "THE ARCHITECTURE — FIVE LAYERS" and DIAGRAMS.md §1.

One short paragraph each for L1 (adapters), L2 (event log + bus), L3 (projections), L4 (pipelines + skills), L5 (surfaces). For L3, append a one-line name list of the 11 projections. For L4, note the reactive-vs-proactive distinction and that the skill runner wraps OpenClaw's runner. For L5, note the Node console (`:3330`) vs Python product APIs (`:3333–:3336`) split.

Cite `per BUILD.md §THE ARCHITECTURE` and `per DIAGRAMS.md §1`.

### §2 How OpenClaw fits (~55 lines)

Per BUILD.md "OPENCLAW IS THE ASSISTANT SUBSTRATE".

- The four seams — skills, slash commands, standing orders, channels — 2–3 sentences each. Cross-reference `openclaw-cheatsheet.md Q1–Q4`.
- Short list: what OpenClaw provides vs. what AdministrateMe adds.
- One sentence on state boundary: OpenClaw memory stays in OpenClaw; AdministrateMe event log is AdministrateMe's; bridged via `openclaw-memory-bridge` plugin.
- One sentence noting that OpenClaw's approval gates (per cheatsheet Q7) run at the tool-execution boundary on the host, while AdministrateMe's `guardedWrite` runs at the HTTP API boundary — two independent gates, both must pass. §6 covers guardedWrite in detail.

### §3 Event log invariants (~40 lines)

Per BUILD.md §"L2: THE EVENT LOG" and §"L2: THE EVENT BUS".

- Append-only, enforced by code + SQLite trigger.
- SQLCipher-encrypted at rest.
- Partitioned by `owner_scope` (indexed, not physically partitioned).
- Schema fields (list them): `event_id` (ULID), `event_type` (dotted), `schema_version`, `occurred_at`, `recorded_at`, `source_adapter`, `source_account_id`, `owner_scope`, `visibility_scope`, `sensitivity`, `correlation_id`, `causation_id`, `payload_json`, `raw_ref`, `actor_identity`.
- Payloads >64KB sidecar to `~/.adminme/data/raw_events/`; artifacts to `~/.adminme/data/artifacts/`.
- Events immutable but correctable via new event with `causation_id`.
- Event bus is in-process asyncio queues with durable per-subscriber offsets; `RedisStreamsBus` is a spec'd alternate impl.

Cite `per BUILD.md §L2` and `per DIAGRAMS.md §2`.

### §4 The 11 projections (~80 lines; use a table)

Per BUILD.md §"L3: PROJECTIONS" (subsections 3.1–3.11).

Build a table with columns: **Name | Subscribes to (key event types) | Key tables/files | Notable properties**.

The 11, in spec order: `parties`, `interactions`, `artifacts`, `commitments`, `tasks`, `recurrences`, `calendars`, `places_assets_accounts`, `money`, `vector_search`, `xlsx_workbooks`.

Special-property notes to surface in "Notable":
- `xlsx_workbooks` is **bidirectional** (forward daemon regenerates xlsx on event bursts with 5s debounce; reverse daemon watches filesystem and emits events on human edits; derived cells protected; sidecar state at `.xlsx-state/`).
- `vector_search` uses `sqlite-vec`; **excludes privileged content** (never embeds sensitivity=privileged).
- `places_assets_accounts` is three linked tables in one projection.
- `commitments` tracks the propose→confirm→complete lifecycle with provenance (source_interaction, source_skill version, confirmed_by).
- `tasks` is AdministrateMe-specific (not in Hearth); household work vs. obligation-to-an-outside-party.

Cite `per BUILD.md §3.N` per row where useful.

### §5 Pipelines (~55 lines)

Per BUILD.md §"L4: PIPELINES" and §"L4 CONTINUED: THE SKILL RUNNER".

**Reactive pipelines** (event-subscription, run inside AdministrateMe pipeline runner) — one line each:
`identity_resolution`, `noise_filtering`, `commitment_extraction`, `thank_you_detection`, `recurrence_extraction`, `artifact_classification`, `relationship_summarization`, `closeness_scoring`, `reminder_dispatch`.

**Proactive pipelines** (scheduled; registered as OpenClaw standing orders) — one line each:
`morning_digest`, `reward_dispatch`, `paralysis_detection`, `whatnow_ranking`, `scoreboard_projection`, `custody_brief`, `crm_surface`, `graph_miner`.

**Skill runner wrapper** (one paragraph): AdministrateMe validates inputs against per-skill JSON schema → invokes OpenClaw at `POST :18789/skills/invoke` → optional `handler.py` post-processes → validates output → emits `skill.call.recorded` event with provenance (skill name, version, openclaw_invocation_id, tokens, cost, duration, correlation_id) → returns to caller. **AdministrateMe does NOT talk directly to Anthropic/OpenAI**; OpenClaw owns provider routing and token accounting.

Cite `per BUILD.md §L4` throughout.

### §6 Security + privacy model (~55 lines)

Per BUILD.md §"AUTHORITY, OBSERVATION, GOVERNANCE" and CONSOLE_PATTERNS.md §2, §3, §6, §7, §11 plus DIAGRAMS.md §4, §5, §9.

- **guardedWrite three layers** — agent allowlist → governance `action_gate` (`allow`/`review`/`deny`/`hard_refuse` per `config/authority.yaml`) → sliding-window rate limit. Short-circuits on first denial; `review` emits a review_request event and returns 202 `held_for_review`. Cite `per CONSOLE_PATTERNS.md §3`.
- **authMember vs viewMember split** — authMember governs write permissions (what you can do), viewMember governs data shown (whose data you see). Only principals can set view-as; non-principals' view-as requests are ignored. Cite `per CONSOLE_PATTERNS.md §2` and `per DIAGRAMS.md §4`.
- **Scope enforcement sites** — enumerated in DIAGRAMS.md §5: session construction, projection queries (auto-added scope predicates), privacy filter at read, nav middleware, guardedWrite, outbound filter, observation-mode wrapper. Every query goes through `Session(current_user, requested_scopes)`; `ScopeViolation` on any attempt to read outside scope.
- **Observation mode** — enforced at the **final outbound filter**, not the policy layer. All internal logic (pipelines, skill calls, projection updates, local console UI) runs normally; only the external side effect is suppressed and recorded as `observation.suppressed` with the full would-have-been payload. Default-on for new instances. Cite `per CONSOLE_PATTERNS.md §11` and `per DIAGRAMS.md §9`.
- **HIDDEN_FOR_CHILD** — combination of client-side nav filter and server-side prefix blocklist (`/api/inbox`, `/api/crm`, `/api/capture`, `/api/finance`, `/api/calendar`, `/api/settings` plus `/api/tasks`, `/api/chat`, `/api/tools`). Today and Scoreboard remain visible. Cite `per CONSOLE_PATTERNS.md §7`.
- **Calendar privacy filter** — applied at read time, not ingest time. Privileged events become opaque "busy" blocks for non-owners; children get an additional tag-based filter (finance/health/legal tags dropped regardless of sensitivity). Cite `per CONSOLE_PATTERNS.md §6`.
- **Privileged-access log** — every non-owner read of `sensitivity=privileged` data is logged with actor identity, target, call stack. Surfaces in `adminme audit privileged-access`.

### §7 Packs (~50 lines)

Per BUILD.md "PROFILE PACKS", "PERSONA PACKS", "PACK REGISTRY"; REFERENCE_EXAMPLES.md appendix; DIAGRAMS.md §8.

- **Six kinds** (one-line each): adapter, pipeline, skill, projection, profile, persona.
- **Install lifecycle** — seven stages per DIAGRAMS.md §8: validate manifest → platform compat check → resolve deps → compile (JSX for profile packs via esbuild at install time) → stage in fixture instance + run pack tests → commit into live instance → emit `pack.installed` event. Test failure rolls back with no log entry. Cite `per DIAGRAMS.md §8`.
- **pack.yaml required fields**: `id`, `kind`, `version`, `min_platform`, `description`. Per-kind additions (inputs/outputs for skills, triggers for pipelines, views for profiles, reward templates for personas) documented in REFERENCE_EXAMPLES.md §1–§7.
- **Directory convention**: `~/.adminme/packs/<kind>/<id>/` containing `pack.yaml`, source files, `tests/`, optional `compiled/` for profile packs.
- **Registry** — v1 is a GitHub repo with `packs.yaml` index; install via `adminme pack install <id|url|path>`.

Cite `per REFERENCE_EXAMPLES.md` appendix for the install-flow narrative.

### §8 The console (~55 lines)

Per CONSOLE_PATTERNS.md index and BUILD.md §"L5: THE NODE CONSOLE SHELL".

- **Node Express at `:3330`**. Tailscale-terminated TLS (primary auth is `Tailscale-User-Login` header). Single-process per household. No login page — on the tailnet = authenticated.
- **12 patterns** (one line each; cite §N): (1) Tailscale identity resolution, (2) session model / authMember+viewMember, (3) guardedWrite three-layer, (4) RateLimiter sliding window, (5) SSE chat handler (proxies to OpenClaw `:18789`), (6) calendar privacy filter, (7) HIDDEN_FOR_CHILD nav, (8) reward toast dual-path emission, (9) degraded-mode fallback with two-TTL cache, (10) HTTP bridge to Python APIs, (11) observation mode enforcement, (12) error handling + correlation IDs.
- **Eight nav surfaces**: today, inbox, crm, capture, finance, calendar, scoreboard, settings.
- **Three view modes**: carousel (adhd_executive profile), compressed (minimalist_parent profile), child (kid_scoreboard profile). *Flag in §11:* CONSOLE_PATTERNS.md names these but doesn't give them a dedicated contract section — definitions live across profile packs (REFERENCE_EXAMPLES.md §6) and the rendered HTML (CONSOLE_REFERENCE.html).

Cite `per CONSOLE_PATTERNS.md §N` throughout.

### §9 Python product APIs (~50 lines)

Per BUILD.md §"L5 CONTINUED: PYTHON PRODUCT APIS".

Four FastAPI services, **loopback-only** (only the Node console is tailnet-facing):

- **Core `:3333`** — tasks, commitments, recurrences, whatnow, digest, scoreboard, energy states, today-stream, observation-mode, emergency playbook.
- **Comms `:3334`** — unified inbox, draft queue, approve/send, channel health, interactions.
- **Capture `:3335`** — quick-capture (prefix-routed), voice ingest, triage, recipes, CRM parties/places/assets/accounts, semantic+structured search.
- **Automation `:3336`** — Plaid institutions/sync/go-live, money flows, budget, balance sheet, 5-year pro-forma, subscription audit, Home Assistant bridge.

Each product owns its pipelines + slash commands + scheduled jobs. Proactive scheduled jobs register as OpenClaw standing orders; internal-only jobs use APScheduler. Bridge pattern per CONSOLE_PATTERNS.md §10 — tenant header injection, correlation ID propagation, canonical `BridgeError` shape.

Cite `per BUILD.md §L5`.

### §10 Bootstrap wizard (~50 lines)

Per BUILD.md §"BOOTSTRAP WIZARD" and DIAGRAMS.md §10.

Nine sections, one-line each:
1. Environment preflight (macOS, FileVault, Tailscale, Node 22+, Python 3.11+, OpenClaw on `:18789`, 1Password CLI, Homebrew, etc.)
2. Name your assistant (persona name, emoji, voice preset, reward style, palette)
3. Household composition (members, principals, children, coparents, helpers)
4. Assign profiles
5. Assistant credentials (Apple ID, Google Workspace, Anthropic, Tailscale, Twilio, BlueBubbles, Telegram, etc.)
6. Plaid (sandbox-first)
7. Seed household data (address, properties, vehicles, bills, healthcare, schools, vendors, CRM seed)
8. Channel pairing (iMessage via BlueBubbles, Telegram, Discord, Apple Reminders, Gmail)
9. Observation briefing

- **Resumability** — state in encrypted `~/.adminme/bootstrap-answers.yaml.enc` + event log; re-run jumps to last incomplete section.
- **Idempotency** — completed sections are no-ops on re-run; same answers produce same events.
- **Abort semantics** — §1 aborts on failure; other sections create inbox tasks for skipped sub-items rather than blocking.

Cite `per BUILD.md §BOOTSTRAP WIZARD` and `per DIAGRAMS.md §10`.

### §11 Open questions (~80–100 lines)

**This section MUST NOT be empty.** It is the primary value for later prompts — it tells future-you what to ask the operator before generating code that depends on ambiguous specs. Minimum 3 items (the carry-forwards below); add more as you notice them while writing §1–§10.

**Three carry-forwards you MUST include:**

1. **OpenClaw standing orders are markdown prose, not machine-parsed metadata.** Per `openclaw-cheatsheet.md Q3`, OpenClaw's standing-orders system is text in `AGENTS.md` plus paired cron jobs — not a typed registration API. AdministrateMe specs (BUILD.md §L4, §L5) describe ~7 proactive pipelines "registered as OpenClaw standing orders." **Open question:** Does AdministrateMe's bootstrap wizard write the proactive pipelines into `AGENTS.md` as prose + `openclaw cron add` invocations, or is there a programmatic registration path (plugin-hook-registered handlers fired by cron)? Affects prompt 10c.

2. **Bus / SSE object identity is ambiguous across the console patterns.** CONSOLE_PATTERNS.md §5 (chat SSE proxying to OpenClaw `:18789`), §8 (in-memory `reward_subscribers` fan-out for reward toasts), and §9 (`degradedSubscribers` for degraded-mode notifications) each describe SSE-ish mechanisms. BUILD.md §L2 describes the event bus. **Open question:** Is there one unified `Bus` class in the console that handles all four, or are these four independent components that happen to all use SSE? Affects prompt 04 (event bus scaffold) and prompt 12 (console).

3. **View mode contracts are not authoritatively specified.** CONSOLE_PATTERNS.md §2 and REFERENCE_EXAMPLES.md §6 reference three view modes (carousel, compressed, child) but no section defines them as a contract. CONSOLE_REFERENCE.html shows rendered UI for each. **Open question:** Is each view mode (a) a JSX component signature that profile packs implement, (b) a data-shape contract that the console negotiates, or (c) a console-level protocol defined in the shell? Affects prompt 07 (L5 console) and prompt 11 (profile packs).

Add additional items as you notice them. Each must cite the file and section where the ambiguity lives. 4–7 items total is a healthy range — stop at that density, don't pad.

---

## Final verification (run before Commit 4)

```bash
# Length
wc -l docs/architecture-summary.md
# expect ≤ 600

# Every numbered section has at least one citation
for n in 1 2 3 4 5 6 7 8 9 10 11; do
  next=$((n+1))
  count=$(awk "/^## $n\\. /,/^## $next\\. /" docs/architecture-summary.md \
    | grep -cE "per BUILD\.md|per CONSOLE_PATTERNS\.md|per DIAGRAMS\.md|per REFERENCE_EXAMPLES\.md|openclaw-cheatsheet\.md")
  echo "§$n: $count citations"
done
# every section should show ≥ 1

# §11 has at least 3 items
awk '/^## 11\./,0' docs/architecture-summary.md | grep -cE "^[0-9]+\."
# expect ≥ 3
```

If any numbered section shows 0 citations, go back and add a proper cite before the final commit. If §11 has fewer than 3 items, add the three carry-forwards listed in the §11 spec.

---

## Final push (end of Commit 4)

```bash
git log --oneline | head -6   # expect 4 phase 01b-N commits on top of main
git status                    # expect clean working tree
git push origin HEAD
```

---

## Stop condition + summary

When all four commits are pushed and verification is green, produce a brief summary for the operator:

- Branch name (harness-assigned).
- Final line count.
- Citation counts per section (paste the loop output).
- Number of open questions in §11.
- Any deviations from the 4-commit batching strategy.
- Any spec ambiguities you hit that aren't captured in §11.

Then STOP. Do not open the PR, do not push to main, do not proceed to prompt 02.

---

## If a turn times out

If a single turn times out during this session, the prior commits are preserved. STOP — do not try to recover within the dying session. The operator will delete the instance and open a fresh session on the same branch. That fresh session:

1. Runs `git log --oneline` to see committed batches (e.g., commits 1 and 2 landed, commit 3 died mid-generation).
2. Reads the existing `docs/architecture-summary.md` to see which sections are complete and which still say `(pending)`.
3. Resumes from the first `(pending)` section using the reading strategy + section specs above.
4. Continues the batch-commit discipline from where it left off.

The 4-commit design means at most one batch of work (3 sections at most) is lost to any single timeout. That is acceptable.
