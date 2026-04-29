# Mission: Build AdministrateMe — An Event-Sourced Household CoS + Relationship System

You are Claude Code Opus 4.7 running in Anthropic's sandbox, working against https://github.com/junglecrunch1212/administrate-me-now. Build **AdministrateMe** (code identifier: `adminme`) — a self-hostable, long-lived, event-sourced household operating system. It is simultaneously:

1. A household Chief of Staff (tasks, calendars, bills, recurring operations, emergency protocols)
2. An ADHD cognitive neuroprosthetic (variable-ratio rewards, fog-window paralysis detection, endowed progress, Zeigarnik teasers, guilt-vocabulary filtering)
3. A **complete household and professional CRM** (Parties, Identifiers, Memberships, Relationships, Interactions, Commitments, Artifacts, Recurrences, MoneyFlow — event-sourced with provenance)
4. A unified communications stream (Gmail + iMessage + SMS + WhatsApp + Telegram + Discord)
5. A working-memory + knowledge system (captures, voice notes, OCR, structured extraction)
6. A sensor/signal integration layer (Plaid financial, Apple Reminders bidirectional, Home Assistant, calendars)

**This is a platform.** The first family it runs for is the Stice family (James + Laura + Charlie + baby girl due May 2026, Atlanta GA); their assistant is named Poopsy; their data lives in `~/.adminme/` on their Mac Mini. **The Stice family is NOT in the codebase.** Any household — James's sister's family, friends, anyone — can run the one-command bootstrap, answer the onboarding questions, and have their own working instance with their own named assistant in under an hour.

The architecture is based on the **Hearth** event-sourced model. The event log is the source of truth; every projection (including human-editable xlsx workbooks) is derived. This is a deliberate architectural decision that governs everything downstream: provenance, replay, auditability, privacy enforcement, and the extensibility contract that will let this system keep growing for a decade.

**Two-phase build model.** AdministrateMe is built and deployed in two distinct phases:

- **Phase A (Claude Code, now):** generate the AdministrateMe codebase on GitHub. You work entirely in Anthropic's sandbox, against this GitHub repo. You write code, run tests that can execute in your sandbox with mocked external services, commit, push. The Mac Mini is NOT involved in Phase A. No live OpenClaw gateway. No Tailscale. No Plaid credentials. Tests that require those are marked `@pytest.mark.requires_live_services` and skipped. When the prompt sequence completes (prompt 18 passes), the repo is build-complete.

- **Phase B (operator, later):** the operator (James) goes to the Mac Mini, installs OpenClaw + BlueBubbles + Tailscale + 1Password CLI, clones the repo, runs `./bootstrap/install.sh`. The wizard installs packs into OpenClaw, registers slash commands, pairs channels, prompts for Plaid / Google / Apple credentials. At the end, the instance is live. You (Claude Code) do NOT participate in Phase B — your job is to have built a bootstrap wizard that Just Works when the operator runs it.

**You are driven by the prompt sequence in `prompts/PROMPT_SEQUENCE.md`, not by this spec directly.** The prompts break this spec into ~33 phases (Phase A) + 1 handoff (Phase B), each with a narrow scope, specific deliverables, verification commands, and a stop condition. This file is the authoritative *reference* — when a prompt tells you to implement L2 event log per BUILD.md §L2, you read this spec for the contract. But you do not attempt to work through this document linearly; the prompt sequence is the driver.

Work phase by phase. At the end of each prompt, run all tests in your sandbox, produce a summary of what you built and what the tests said, commit to a branch, and stop for human review before proceeding.

---

## THIS IS A FIVE-FILE DELIVERABLE — READ ALL FIVE BEFORE STARTING

You have been given five files. They are companion artifacts, not alternatives. Each one answers a specific class of question. Before you write a line of code, open all five. Skip one and you will either reinvent it poorly or block on questions it already answers.

### BUILD.md (this file)
**Role:** the spec.
**Answers:** what the platform is, how the layers compose, what events exist, what projections hold, how pipelines orchestrate, what pack contracts look like, how the bootstrap wizard flows, what phases to build in, what the operating rules are.
**Does NOT answer:** how specific Node handlers are written, what the console visually looks like, what a real-world populated pack looks like.

### CONSOLE_REFERENCE.html
**Role:** the design anchor and interaction vocabulary.
**Answers:** what the console looks like when populated; how the three view modes (carousel / compressed / child) render; how a Party detail page looks; what the reward toast UI is; how the inbox approval flow animates; what the scoreboard feels like on a wall display; what a degraded banner looks like; what settings panes contain. The file is fully interactive — open it in a browser, click through all 8 tabs, fire toasts, toggle observation mode, watch the calendar privacy filter redact Laura's work events. The PIB v5 design language (Fraunces + DM Sans + JetBrains Mono, cream + blush + lavender + teal palette, 12px/8px radii, fadeUp animations, pill badges) IS the platform's default visual vocabulary.
**Mock data disclaimer:** the file uses Stice-family data (James, Laura, Charlie, Kate, Dr. Dalton, 761 E Morningside Dr NE, etc.) as seed content to show what a populated instance looks like. **These names MUST NOT appear in `adminme/`, `bootstrap/`, `profiles/`, `personas/`, `integrations/`, or `tests/`.** The identity scan (`tests/unit/test_no_hardcoded_identity.py`) enforces this. Port the *patterns and design tokens*, not the data.

### CONSOLE_PATTERNS.md
**Role:** Node/Express code patterns for the L5 console shell.
**Answers:** exactly how to write the 12 platform-critical console patterns — Tailscale identity resolution, the authMember/viewMember split, the three-layer guardedWrite check, the sliding-window RateLimiter, SSE chat streaming, calendar privacy filtering, HIDDEN_FOR_CHILD navigation blocking, reward toast emission (dual-path design: sync preview + async SSE canonical), degraded-mode fallback with cache TTLs, the HTTP bridge to Python APIs, observation-mode enforcement, and error handling with correlation IDs. Each pattern has working code, a "why it matters" failure-mode analysis, and testing notes.
**Tenant-agnostic throughout.** Read this *before* writing PHASE 12 (Node Console Shell).

### REFERENCE_EXAMPLES.md
**Role:** seven worked pack examples showing the extension-point contracts filled in.
**Answers:** what a real adapter, pipeline, skill, projection, event type, profile pack, and persona pack look like when fully specified — including manifests, schemas, prompt templates, handlers, test fixtures. Uses Stice-family subject matter (Poopsy persona, `adhd_executive` profile for James, the BlueBubbles adapter bound to the household Mac Mini, the commitment_extraction pipeline with test fixtures involving Kate and Coach Mark) because pack content IS instance-specific by design. When the bootstrap wizard runs for the Stice household, these exact packs are the seed content that lands in `~/.adminme/packs/`. Other households will generate different packs. **The platform code that runs these packs stays tenant-agnostic.**

### DIAGRAMS.md
**Role:** ten ASCII diagrams for the architecture, flows, security model, and operational state machines.
**Answers:** the ten structural questions that a diagram conveys more clearly than prose: five-layer architecture, one incoming message's full end-to-end flow, the three-layer guardedWrite check, the authMember/viewMember split, the session+scope enforcement matrix, the xlsx round-trip mechanics, machine topology, pack installation state machine, observation-mode fire-vs-suppress routing, and the bootstrap wizard state machine. Read before implementing any of the corresponding layers. Every diagram cross-references back to the authoritative prose in BUILD.md, CONSOLE_PATTERNS.md, or REFERENCE_EXAMPLES.md — the diagrams do not introduce new contracts.

### How to use the five files together

When you hit a question, go to the file designed to answer it:

- "What is OpenClaw and how does it relate to AdministrateMe?" → BUILD.md "OPENCLAW IS THE ASSISTANT SUBSTRATE" section
- "What OpenClaw docs do I need?" → `docs/reference/openclaw/` (mirrored by prompt 00.5 from `openclaw/openclaw/docs/` on GitHub)
- "What event is emitted when X happens?" → BUILD.md event taxonomy
- "What does the today view look like when James has 6 tasks?" → CONSOLE_REFERENCE.html, Today tab
- "How do I write the SSE chat handler?" → CONSOLE_PATTERNS.md section 5
- "What's the shape of a profile pack's pack.yaml?" → REFERENCE_EXAMPLES.md section 6
- "What tests must a pipeline ship with?" → REFERENCE_EXAMPLES.md section 2 (fixtures pattern)
- "How does observation mode interact with the outbound filter?" → CONSOLE_PATTERNS.md section 11 + BUILD.md section on governance
- "What does a populated `~/.adminme/` directory look like?" → BUILD.md "Sample instance" section
- "What's the design token palette for persona.poopsy?" → CONSOLE_REFERENCE.html CSS or REFERENCE_EXAMPLES.md section 7
- "How do the five layers actually compose for one incoming message?" → DIAGRAMS.md §2
- "What does the authMember/viewMember split look like as a picture?" → DIAGRAMS.md §4
- "What's suppressed in observation mode and what still runs?" → DIAGRAMS.md §9 (CONSOLE_PATTERNS.md §11 has the code)

**Beyond the five spec files, the repo also contains:**

- **`docs/SYSTEM_INVARIANTS.md`** (produced by prompt 01b) — the constitutional cross-cutting contracts document. ~80 numbered invariants organized by concern. Read this at the start of every later prompt as the source of truth on relationships between layers, projections, and boundaries.
- **`prompts/PROMPT_SEQUENCE.md`** — the driver. This spec is authoritative, but you don't work through it linearly; the prompt sequence breaks it into ~33 phases each with narrow scope.
- **`ADMINISTRATEME_FIELD_MANUAL.md`** — the operator's guide for Phase B and beyond. Not for Claude Code to consume during Phase A work; it's for the human operating the Mac Mini.
- **`docs/reference/`** — mirrored external documentation (OpenClaw, Plaid, BlueBubbles, Google APIs, Textual, SQLCipher, etc.) populated by prompt 00.5 via GitHub-first fetching.

When the files appear to disagree: **BUILD.md wins for contracts, CONSOLE_PATTERNS.md wins for Node implementation details, CONSOLE_REFERENCE.html wins for visual + interaction design, REFERENCE_EXAMPLES.md wins for pack shape, DIAGRAMS.md wins for nothing — it's mental-model formation, always defer to the specs.** `SYSTEM_INVARIANTS.md` cites its sources — it's a derived view of the specs. If a genuine contradiction appears, flag and ask before writing code.

---

## CRITICAL OPERATING RULES — READ FIRST, REFERENCE CONSTANTLY

1. **Read from the local mirror.** When this spec says "consult OpenClaw docs" or "Plaid docs" or any external documentation reference, read from `docs/reference/<section>/` in the repo — not from the live internet. The sandbox has an egress allowlist that permits `github.com` / `raw.githubusercontent.com` but blocks most other hosts with HTTP 403 `host_not_allowed`. Prompt 00.5 has populated `docs/reference/` from GitHub-based sources before Phase A work begins. If a referenced path is missing, check `docs/reference/_gaps.md` — it's either a documented gap with operator remediation notes, or a sign that prompt 00.5 didn't complete.

2. **Verify, do not assume.** When you think you know a CLI flag, config key, tool signature, schema, or event format, check `docs/reference/<section>/`. If the mirror doesn't contain the answer, either (a) it's a documented gap — flag it and make the most conservative assumption, or (b) ask the operator before guessing. Do not rely on training data for API specifics — these APIs change.

3. **The prompt sequence drives you.** You do not work through this spec linearly. The prompts in `prompts/` break the work into narrow, verifiable phases with explicit stop conditions. Read this spec as reference material when a prompt tells you to. Do not skip ahead; do not compress phases. Each prompt ends with a required human review.

4. **Tenant-agnostic codebase.** No family name, person's name, address, phone number, email, account number, or medical detail appears anywhere in platform code. All such data lives in the instance directory (`~/.adminme/`), populated by the bootstrap wizard during Phase B. A static identity scan test runs from Phase 0 and fails CI if any hardcoded tenant data slips into platform code or tests (except explicitly-marked fixtures under `tests/fixtures/`).

5. **The event log is the source of truth.** Projections — including xlsx workbooks, SQLite read tables, dashboard caches — are derived. Never write to a projection except through a projector. Never mutate an event after it is recorded.

6. **Propose, then commit, for LLM-authored state changes.** Pipelines running LLM skills emit *proposal events* (`CommitmentProposed`, `TaskProposed`, `PartyMergeSuggested`). Humans approve via the inbox; approval emits a confirmation event (`CommitmentConfirmed`); projections update from the confirmed event. Direct LLM writes to "live" state are prohibited. Deterministic adapters and deterministic pipelines may write directly (e.g., `MessageReceived` is direct from the IMAP adapter; no human approval needed to record that a message arrived).

7. **Observation mode is first-class and default-on.** At bootstrap, `ADMINME_OBSERVATION_MODE=true`. Every outbound adapter (email send, iMessage send, SMS send, voice call, physical mail, Privacy.com charge, Apple Reminders write, Plaid-triggered alert) and every proactive pipeline (digest, reward, paralysis, reminder dispatch) checks observation mode before firing externally. In observation mode the action is composed and recorded as an event (`OutboundSuppressed`), not fired. The tenant flips observation off after 5-7 days of review (during Phase B, after bootstrap).

8. **Identity-first privacy.** Privileged work content is never shared to the assistant's accounts (Apple ID, Google Workspace), so it never enters the ingest layer. This is the primary privacy boundary. Session-scope (`dmScope: per-channel-peer` in OpenClaw) is secondary. Event-level `sensitivity: privileged` with `visibility_scope` narrowing is tertiary. All three layers must be present.

9. **Ask before destructive operations.** Never delete, drop, truncate, reset, or uninstall without a dry-run preview first, even during development.

10. **Validate at every boundary.** All events, API inputs, API outputs, skill inputs, and skill outputs use Pydantic v2 models. Open enums use a sentinel pattern so plugins can extend. No `dict[str, Any]` crossing a layer boundary.

11. **Children are data-model members, not users.** Kids exist in the Party table (with `kind: person`, age, Memberships to the household). They do not receive agent bindings. They do not receive outbound messages. They interact only through display-only surfaces (scoreboard) and parent-mediated captures.

12. **Coparents are Parties of kind `person` with `Relationship(label: parent_of, party_b: child, direction: →)`.** Their phones are in channel allowlists so their messages don't bounce. Their messages create events and projections, but they receive no autonomous outbound; all replies go through an inbox approval.

13. **Mode 1 only for sending.** The assistant sends as itself (persona handle). Mode 2 (draft-for-principal-to-send) is routed to the principal's approval inbox. Mode 3 (auto-send as a principal) is prohibited.

14. **Never reference private medical, legal, or financial details to outside parties.** Enforced at the outbound adapter layer via a final content-filter pass that checks for PII leakage using a dedicated skill.

15. **Never describe social institutions, schools, or clubs as prestigious or exclusive.** Enforced at the voice layer (persona `voice.md`) and at the final outbound filter.

16. **Profile packs and persona packs are the unit of customization.** Profile = bundle assigned to a member (views + engines + tuning). Persona = bundle defining the agent's identity (one per instance). Both are installable, versionable, shareable via a registry. Neither can modify the event log schema or the authority model.

17. **Platform repo contains no tenant data.** Running the bootstrap wizard twice with different `ADMINME_INSTANCE_DIR` values produces two fully independent instances on the same machine. CI verifies this via isolation canary tests.

18. **Migration framework from day one.** Every schema change ships a numbered migration. Migrations are idempotent. Existing tenants can always upgrade without data loss. Event schemas get `schema_version` + upcaster functions. This is non-negotiable for a platform that will exist for 10+ years.

19. **Stop between phases.** After each phase commit, report what was built, what tests passed, what was deferred. Wait for explicit human approval before proceeding. This is enforced by every prompt's explicit stop condition.

20. **Prefer deterministic over LLM.** When there is a rule to encode, encode it as code + config, not as a prompt. LLM is for fuzzy composition (draft emails, digest narrative, classification, extraction). Everything else is deterministic. This is especially important for paralysis templates, reward dispatch, and any adversarial-safe surface.

21. **Prefer extensibility over features.** If you catch yourself writing a switch statement on entity type, or an `if member_name == "James"` check, or an embedded SQL query in pipeline code — stop. That's a sign something wants to be an open enum, a plugin table, a Party attribute, or a projection.

---

## TERMINOLOGY — USE THESE WORDS EXACTLY

- **AdministrateMe** — the platform. Code identifier: `adminme`. CLI: `adminme`.
- **Instance** — one tenant's deployment, lives in `~/.adminme/` on the host.
- **Tenant** — the household running one instance.
- **Assistant** — the AI agent. Named by the tenant at bootstrap (default exemplar in docs: "Poopsy", the Stice family's name).
- **Principal** — an adult Party with `can_act_as_principal: true` in the household — can approve, can receive proactive messages, can view-as other members.
- **Member** — any Party belonging to the household via a Membership row. Principals, children, expected arrivals, ambient members.
- **Party** — universal entity: a person, organization, or household. The CRM primitive.
- **Interaction** — a projected, deduplicated, normalized touchpoint between Parties. Derived from one or more raw message/call/meeting events.
- **Commitment** — something owed by one Party to another. Generated from interactions by a pipeline; confirmed by a human; tracked to completion.
- **Task** — household work not owed to a specific outside Party (mow lawn, renew license, clean gutters). Assigned to a member of the household. Separate from Commitment.
- **Artifact** — document, image, audio, video, or structured record (parsed invoice, school form). Has mime_type, extracted text, extracted structured data, links to Parties/Assets/Accounts/Places.
- **Profile** — reusable bundle assigned to a member: views (Jinja? no — JSX compiled at install time), engines (reward mode, paralysis toggles, digest format), tuning (knobs), prompts (system additions for agent sessions with this member).
- **Persona** — bundle defining the assistant's identity: name, emoji, voice guide, color palette, reward template pools, signatures. One per instance.
- **Pack** — shareable, installable extension: profile packs, persona packs, adapter packs, pipeline packs, skill packs. Each has a manifest, version, author, license.
- **Registry** — discovery mechanism for packs. v1 is a public GitHub repo with a `packs.yaml` index.
- **Event log** — the authoritative append-only log of everything that happened. SQLCipher-encrypted, partitioned by owner_scope.
- **Projection** — a read model derived deterministically from the event log. Can be rebuilt at any time by replaying events.
- **Adapter** — a translator from an external source (email server, calendar, file system, bank via Plaid, Apple Reminders) into typed events. Adapters emit events; they do not write projections or call pipelines.
- **Pipeline** — a subscriber to events that produces derived events, proposals, or triggers LLM skills. Pipelines never write projections directly.
- **Skill** — a versioned, provenance-tracked LLM call. Markdown + YAML frontmatter + input/output JSON schemas. Every call recorded as a `SkillCallRecorded` event.
- **Surface** — where a human touches the system: console panes (Node shell at :3330 proxying to Python product APIs), iMessage, Telegram, Apple Reminders, morning digest, scoreboard TV, quick-capture bar, voice.
- **Product** — a logical grouping of pipelines+skills+slash-commands+API endpoints into a shippable unit. Four ship with the platform: **Core** (CoS), **Comms** (unified inbox), **Capture** (quick-capture and mining), **Automation** (financial signals, sensors, Plaid). Products share the event log and projections; they do not have separate data stores.
- **Substrate** — the shared infrastructure all products use: event log, projections, Pydantic models, event bus, Skill runner, Session/scope enforcement, authority gate, observation mode, adapter registry, pipeline registry, pack registry.



---

## OPENCLAW IS THE ASSISTANT SUBSTRATE

AdministrateMe does not run as a standalone app. It runs **on top of OpenClaw**, which is the assistant gateway that handles the agent loop, channel connections, tool invocation, session management, standing orders, and multi-agent routing on the Mac Mini.

Read this section carefully. Many things in this spec only make sense if you already understand this layering:

**What OpenClaw is.** OpenClaw is an open-source, self-hosted personal assistant gateway. It runs as a daemon on the Mac Mini (`ai.openclaw.gateway`), listens on a loopback port (18789 by default), and exposes the assistant via channels (iMessage via BlueBubbles, Telegram, Discord, web). It has a concept of a Workspace (the agent's home directory: `~/Chief` for this deployment), a SOUL.md (persona definition), Skills (installable capabilities with SKILL.md + handler + schemas), Tools (built-in primitives: exec, read, write, web search, approvals, elevated mode), Plugins (shared-state extensions: memory-core, memory-wiki, and our own), Slash Commands (verbs the user types: `/digest`, `/whatnow`, `/capture`), Sessions (with `dmScope: per-channel-peer` for multi-principal households), Nodes (paired devices: family members' laptops and phones), Cron jobs, Hooks, Standing Orders (persistent proactive rules), and Subagents (specialized agents invoked by the main one). All of that exists in OpenClaw already. **Do not reinvent it.**

**What AdministrateMe adds.** AdministrateMe is the Chief-of-Staff **content and substrate layer** on top of OpenClaw. Specifically, AdministrateMe provides:

- **The event log and projections** (L2-L3 in the five-layer architecture that follows) — OpenClaw has `memory-core` and `memory-wiki` for conversational memory, but it does not provide an event-sourced household CRM, financial ledger, commitment tracker, or reversible xlsx projection. AdministrateMe does.
- **The adapter layer** (L1) — OpenClaw has some channels built in (iMessage, Telegram, Discord). AdministrateMe adds and manages the *data ingest* adapters that OpenClaw doesn't ship: Gmail, Google Calendar, Google Drive, Google Contacts, Plaid, Apple Reminders, CalDAV, Apple Contacts. These emit events into the AdministrateMe event log. Some live as OpenClaw plugins; some are standalone Python processes; the choice is per-adapter (see L1 section).
- **The pipelines** (L4) — OpenClaw has cron jobs, hooks, and standing orders. AdministrateMe's pipelines subscribe to AdministrateMe events (not OpenClaw events) and emit either AdministrateMe events or OpenClaw skill invocations. They're AdministrateMe-layer constructs that *use* OpenClaw's automation primitives where relevant.
- **The Skills** (most of them) — AdministrateMe's skill packs (classify_commitment_candidate, extract_commitment_fields, compose_morning_digest, classify_thank_you_candidate, etc.) are installed into OpenClaw via ClawHub or as local skill packs. When a pipeline needs a skill, it invokes it through OpenClaw's skill runner — same mechanism as slash commands, same session scoping, same approval gates. AdministrateMe's skill pack format (REFERENCE_EXAMPLES.md §3) is compatible with OpenClaw's SKILL.md format.
- **The Slash Commands** — AdministrateMe registers household-CoS slash commands as OpenClaw slash commands: `/digest`, `/whatnow`, `/capture`, `/comms`, `/bill`, `/approve`, `/crm`, `/commit`, `/review`, `/scoreboard`. When James types `/digest` in iMessage, OpenClaw routes it to the AdministrateMe slash-command handler, which reads projections and returns a response.
- **Persona = SOUL.md.** AdministrateMe's active persona pack compiles to a `SOUL.md` that OpenClaw loads. Persona theme tokens live in AdministrateMe's pack (consumed by the Node console), but the persona's name, voice, emoji, and voice guidelines are written into the SOUL.md so OpenClaw renders them across every channel.
- **The console** (L5 Node) — AdministrateMe's console at :3330 is a separate surface from OpenClaw's channels; it provides the visual consoles (Today, Inbox, CRM, Capture, Finance, Calendar, Scoreboard, Settings) that OpenClaw's text-first channels can't represent well. The console talks to AdministrateMe's Python product APIs (:3333-:3336), not to OpenClaw. The chat pane inside the console is the one place the two systems meet: the console SSE chat endpoint proxies into OpenClaw's gateway so the chat experience is consistent with iMessage and Telegram.
- **The Python product APIs** (L5 Python) — Core, Comms, Capture, Automation. These expose AdministrateMe-layer state (tasks, commitments, inbox, parties, transactions, calendar) over HTTP for the console and for slash-command handlers to read. They do not replace OpenClaw's gateway API.
- **Standing Orders** — AdministrateMe installs standing orders into OpenClaw for the proactive behaviors that need to run regardless of console state: paralysis detection, morning digest delivery, reward dispatch, reminder dispatch, CRM gap nudges, coparent brief generation. Each one is an OpenClaw standing order whose handler is an AdministrateMe skill.

**Where OpenClaw and AdministrateMe share state.** They share the **session** (via `dmScope` rules), they share the **member roster** (OpenClaw's nodes correspond to AdministrateMe's Members + Parties), they share **skills** (OpenClaw's skill runner invokes AdministrateMe's skill packs), they share **slash commands** (AdministrateMe registers its verbs with OpenClaw), and they share **standing orders** (AdministrateMe registers its proactive rules with OpenClaw). They do **not** share an event log: OpenClaw's memory is OpenClaw's; AdministrateMe's event log is AdministrateMe's. Relevant OpenClaw memory (session history, conversation summaries) is ingested into AdministrateMe as events via a dedicated OpenClaw plugin (`openclaw-memory-bridge`), which emits `messaging.received` and `conversation.turn.recorded` events so pipelines can subscribe.

**Concrete example of how one request flows:** James texts Poopsy (at assistant's Apple ID on the Mac Mini) "what's on my plate this afternoon?" BlueBubbles delivers the iMessage to OpenClaw. OpenClaw's agent loop routes it to a chat session (dmScope: james-only). The session handler invokes the `/whatnow` slash command (auto-detected by OpenClaw from intent, or James types it directly). OpenClaw's slash-command dispatcher hands off to the AdministrateMe `whatnow` handler. The handler reads the AdministrateMe `tasks` + `commitments` + `calendars` projections via the Core API (:3333), runs the deterministic whatnow_ranking scoring function, returns a prose summary with top-3 tasks. OpenClaw sends the response back through BlueBubbles. The whole round-trip is logged in both systems: OpenClaw records the session turn, AdministrateMe records `slash_command.invoked` + `whatnow.ranked` events. Correlation IDs connect the two.

---

## BEFORE YOU START: LEARN OPENCLAW

**How documentation is sourced for this build.** Claude Code runs in a sandbox with an egress allowlist: `github.com` and `raw.githubusercontent.com` are allowed, but `docs.openclaw.ai`, `plaid.com`, `developer.apple.com`, `tailscale.com`, and most other hosts are NOT. Attempts to WebFetch those return `HTTP 403 host_not_allowed` from Anthropic's proxy. The build handles this by mirroring documentation from public GitHub repos into `docs/reference/` before any code work starts. This is the job of **prompt 00.5** (see `prompts/PROMPT_SEQUENCE.md`).

**Do not WebFetch the URLs listed below directly.** They are retained here as the *authoritative sources* — each one is mirrored via GitHub by prompt 00.5 into the corresponding directory under `docs/reference/`. When this spec or a later prompt tells you to "consult the OpenClaw docs," read the mirrored files in `docs/reference/openclaw/`.

**Workflow for every prompt:**
1. Prompt 00.5 has run, so `docs/reference/` is populated.
2. When this spec references OpenClaw / Plaid / BlueBubbles / Google / Apple / Tailscale / Textual / SQLite documentation, read from `docs/reference/<section>/`, not live.
3. If a file is missing from `docs/reference/`, consult `docs/reference/_gaps.md` — it may be a documented gap (Apple EventKit pages, a few Tailscale KB entries) with operator remediation notes.

**OpenClaw sources (mirrored from `openclaw/openclaw/docs/` on GitHub, available at `docs/reference/openclaw/`):**

Read everything in `docs/reference/openclaw/` in this logical order. The original URLs are kept below only so you know what each file corresponds to if you need to look something up.

1. `index.md` ← was `https://docs.openclaw.ai/` — Overview
2. `start/openclaw.md` ← `docs.openclaw.ai/start/openclaw` — Personal assistant setup. AdministrateMe presents itself to OpenClaw as a Personal Assistant on a Mac Mini, upgraded into a Chief of Staff via skills, tools, plugins, slash commands. This is the mental model for the entire build.
3. `install/` (directory) ← `docs.openclaw.ai/install` — Installation. Contains `installer.md`, `uninstall.md`, `node.md`. The bootstrap wizard's Section 1 verifies OpenClaw is installed before proceeding. (Bootstrap runs in **Phase B** on the Mac Mini — not in Claude Code's sandbox.)
4. `concepts/architecture.md` — Gateway architecture
5. `concepts/agent-workspace.md` — Workspace layout. AdministrateMe's workspace is `~/Chief` by convention, created during Phase B bootstrap.
6. `concepts/soul.md` — SOUL.md personality guide. Maps to AdministrateMe's persona pack (REFERENCE_EXAMPLES.md §7).
7. `concepts/agent-loop.md` — How an agent turn works
8. `concepts/memory.md` — Memory system. Understand the boundary with AdministrateMe's event log (see SYSTEM_INVARIANTS.md §8).
9. `concepts/session.md` — Session management. **Critical:** `dmScope: per-channel-peer` for multi-principal households.
10. `concepts/multi-agent.md` — Multi-agent routing.
11. `tools/index.md` — Built-in tools overview
12. `tools/skills.md` — **Skills system.** The mechanism that upgrades a Personal Assistant into a Chief of Staff.
13. `tools/creating-skills.md` — SKILL.md + handler + tests. Compare with REFERENCE_EXAMPLES.md §3.
14. `tools/slash-commands.md` — Slash command reference. AdministrateMe registers ~22 commands across the four Python product APIs.
15. `tools/plugin.md` — Plugin system. AdministrateMe installs its own plugins during Phase B bootstrap: `memory_bridge`, `channel_bridge_bluebubbles`, etc.
16. `tools/exec.md` — Exec tool
17. `tools/exec-approvals.md` — Approval gates. These map to AdministrateMe's `guardedWrite` governance layer.
18. `tools/elevated.md` — Elevated mode
19. `gateway/security/` (directory) — Security model. Read all files within.
20. `gateway/sandboxing.md` — Sandboxing
21. `gateway/configuration.md` — Configuration reference
22. `gateway/heartbeat.md` — Heartbeat
23. `automation/cron-jobs.md` — Cron
24. `automation/hooks.md` — Hooks
25. `automation/standing-orders.md` — **Standing orders.** How AdministrateMe's proactive behaviors fire (paralysis detection, morning digest, reward dispatch).
26. `tools/subagents.md` — Sub-agents
27. `channels/bluebubbles.md` — BlueBubbles (iMessage on Mac). The assistant signs into its own Apple ID on the Mac Mini during Phase B.
28. `channels/telegram.md` — Telegram
29. `channels/discord.md` — Discord
30. `channels/pairing.md` — Channel pairing
31. `gateway/protocol.md` — WS protocol
32. `nodes/` (directory) — Nodes. Family members' laptops and phones pair as nodes during Phase B.
33. `platforms/macos.md` — macOS companion app
34. `tools/clawhub.md` — ClawHub skill registry. The bootstrap wizard installs from ClawHub: `apple-reminders`, `apple-notes`, `apple-contacts`, etc.
35. `cli/` (directory) — OpenClaw CLI reference. Contains per-command pages (`agent.md`, `channels.md`, `config.md`, etc.).

**Platform-specific documentation (mirrored via GitHub-first; see prompt 00.5):**

- **Plaid** → `docs/reference/plaid/` (from `plaid/plaid-openapi` on GitHub — full OpenAPI spec) — covers Link, Transactions, Balance, Identity, Investments, Liabilities, webhooks. Narrative UX docs are a small documented gap.
- **BlueBubbles** → `docs/reference/bluebubbles/` (from `BlueBubblesApp/bluebubbles-docs`) — server, API, WebSocket events.
- **Google Gmail + Calendar APIs** → `docs/reference/google-gmail/` and `docs/reference/google-calendar/` (single TypeScript reference files from `googleapis/google-api-nodejs-client`).
- **Apple EventKit + Shortcuts** → `docs/reference/apple-eventkit/` and `docs/reference/apple-shortcuts/` — **documented gap** (Apple does not publish docs source anywhere public); prompt 00.5 produces a manual-clip checklist for the operator.
- **Tailscale** → `docs/reference/tailscale/` — partial gap; the small Tailscale KB pages (Serve, Funnel, identity headers) can be allowlisted or manually clipped.
- **Textual** (TUI framework for bootstrap wizard) → `docs/reference/textual/` (from `Textualize/textual/docs/`).
- **SQLCipher, sqlite-vec, aiosqlite, CalDAV** → various `docs/reference/<section>/` directories populated from their respective GitHub repos.

If any referenced path is missing during Phase A work, check `docs/reference/_gaps.md` — the content is either a documented gap (proceed with note) or a sign that prompt 00.5 didn't complete (stop and finish it).

**Deliverables of prompt 01 (not this section):** `docs/openclaw-cheatsheet.md` (≤100 lines) and `docs/architecture-summary.md` (≤600 lines), produced by Claude Code after reading the mirrored docs. Those files answer the operational questions: how to install a skill pack, how to register a slash command, how standing orders work, how SOUL.md compiles, how `dmScope: per-channel-peer` interacts with guardedWrite, etc.

**Phase A vs. Phase B note:** Installing things into a live OpenClaw gateway (skills, plugins, standing orders, channels) happens during **Phase B bootstrap**, not during Phase A code generation. The prompts in `prompts/` make this distinction clear per prompt — Phase A prompts validate structure and queue installations for Phase B; Phase B is the operator running `./bootstrap/install.sh` on the Mac Mini.

---

## THE ARCHITECTURE — FIVE LAYERS

Every file you write belongs to exactly one layer. Layer violations are build failures.

```
                             ┌──────────────────────────────────────┐
                             │ OPENCLAW GATEWAY (loopback :18789)   │
                             │ Agent loop · Channels · Skill runner │
                             │ Slash commands · Sessions · Standing │
                             │ orders · Plugins · SOUL.md · Nodes   │
                             │                                      │
                             │ AdministrateMe installs skills,      │
                             │ plugins, slash commands, and         │
                             │ standing orders INTO OpenClaw.       │
                             └───────┬──────────────────────────────┘
                                     │ invokes skills / slash commands
                                     │ emits conversation.turn events via bridge
                                     │ receives outbound drafts for channels
                                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│  L5  Surfaces        Node console (:3330) · Python APIs (:3333-3336) │
│                      CLI · Mobile Shortcuts webhooks · Outbound      │
│                      channels routed via OpenClaw (iMessage, email,  │
│                      SMS, Telegram, etc.) · Slash commands handled   │
│                      by AdministrateMe, dispatched by OpenClaw       │
├──────────────────────────────────────────────────────────────────────┤
│  L4  Pipelines       Identity resolution · Noise filtering ·         │
│                      Commitment extraction · Thank-you detection ·   │
│                      Recurrence extraction · Artifact classification │
│                      Relationship summarization · Closeness scoring  │
│                      Reminder dispatch · Reward dispatch · Paralysis │
│                      Morning digest · What-now ranking · Scoreboard  │
│                      Custody brief · CRM surfacing · Graph miner     │
│                      (pipelines invoke skills via OpenClaw's runner) │
├──────────────────────────────────────────────────────────────────────┤
│  L3  Projections     Read models built deterministically from L2:    │
│                      parties · interactions · artifacts · commits ·  │
│                      tasks · recurrences · calendars · money ·       │
│                      places_assets_accounts · vector_search ·        │
│                      xlsx_workbooks (bidirectional) · scoreboards    │
├──────────────────────────────────────────────────────────────────────┤
│  L2  Event log       Append-only, SQLCipher-encrypted, partitioned   │
│                      by owner_scope. Source of truth.                │
│                      OpenClaw memory is separate; bridged into L2    │
│                      via the openclaw-memory-bridge plugin.          │
├──────────────────────────────────────────────────────────────────────┤
│  L1  Adapters        Channel-family-specific translators from        │
│                      external sources to typed events. Two places:   │
│                      central (on CoS Mac Mini) for messaging /       │
│                      calendaring / financial / etc.; bridge (on      │
│                      member Mac Minis) for knowledge sources         │
│                      (Apple Notes, Voice Notes, Obsidian, connector  │
│                      packs). Bridges emit owner-scoped events into   │
│                      the central event log via the :3337 bridge      │
│                      ingest endpoint over the tailnet. Never write   │
│                      projections; never call pipelines; never        │
│                      compose outbound (that's the send path via      │
│                      OpenClaw's outbound channels for messaging,     │
│                      or adapter-specific outbound for Plaid writes,  │
│                      Apple Reminders writes, etc.) See §MEMBER       │
│                      BRIDGES below.                                  │
└──────────────────────────────────────────────────────────────────────┘
```

**Coupling rules:**
- Layer N+1 depends on Layer N. Never the reverse.
- Within a layer, components are independent. Two pipelines do not import each other; they communicate through events.
- Two products do not import each other. They share the event log and projections. Cross-product signaling is via events.
- The bootstrap wizard may touch any layer (it's how the instance is born).
- The CLI (`adminme`) orchestrates but contains no business logic; it shells out to product APIs or invokes pipeline/skill runners directly.

**Machine layout** (these are L1/L0, beneath the five layers):
- **adminme-hub** — Mac Mini (Apple Silicon, macOS Sequoia+ or Tahoe), always-on. Primary host. Runs Python L1-L4 services, Node console, OpenClaw gateway, BlueBubbles server, all LaunchAgents, local SQLCipher event log + projections. Does **not** sign into any family member's iCloud account.
- **adminme-bridge-{member}** — Mac Mini per Apple-using family member, on the household tailnet, signed into that member's iCloud account. Runs the `adminme-bridge` daemon and its assigned knowledge-source adapters (Apple Notes, Voice Notes, optionally Obsidian, optional connector packs). Emits owner-scoped `note.*` and `voice_note.*` events to the central CoS Mac Mini's `:3337 bridge` ingest endpoint. See §MEMBER BRIDGES below for full spec.
- **adminme-vault** — optional Linux VPS or Raspberry Pi 5 on the same tailnet. Runs nightly backups, graph miner, read replica. Not required for v1.
- **adminme-edge** — wall displays (kitchen iPad, old iPhones) running the scoreboard surface. Tailscale-authenticated but not mapped to a member.

---

## STACK

**Assistant substrate (read OPENCLAW IS THE ASSISTANT SUBSTRATE section above first):**
- **OpenClaw gateway** — runs as a daemon (`ai.openclaw.gateway`) on loopback port 18789. Hosts the agent loop, channels (iMessage via BlueBubbles, Telegram, Discord), skill runner, slash-command dispatcher, session manager, plugins, standing orders, cron jobs, hooks. Workspace at `~/Chief`. SOUL.md compiled from the active AdministrateMe persona pack. Skills installed from AdministrateMe skill packs via `openclaw skill install <path>` or ClawHub.

**Language split for AdministrateMe's own code:**
- **Python 3.11+** for L1-L4 and L5's Python surfaces (product APIs, CLI, bootstrap wizard, daemons): event log, projections, adapters, pipelines, skill handlers, scheduler, event bus, API servers, ingest webhooks. AdministrateMe skill handlers are Python modules that OpenClaw's skill runner invokes via its standard handler contract.
- **Node 22+** for the L5 console shell: Express at :3330 serving compiled JSX profile views, proxying to Python product APIs (:3333, :3334, :3335, :3336). Implements the console patterns specified in **CONSOLE_PATTERNS.md** (Tailscale identity resolution, authMember/viewMember split, guardedWrite, SSE chat, RateLimiter, degraded-mode UX, carousel/compressed/child view modes, reward toast). The console's SSE chat endpoint proxies into OpenClaw's gateway so the chat UX is identical to iMessage/Telegram/Discord.

**Python core deps:**
- `pydantic>=2.6` — all boundary models; open-enum sentinel pattern for extensibility
- `sqlalchemy>=2.0` + `alembic` — migrations and ORM (read side; the event log uses raw SQL for append performance)
- `sqlcipher3-binary` — encrypted SQLite (bundled wheel; no system headers required; drop-in replacement for `pysqlcipher3` with identical API)
- `sqlite-vec` — vector search extension for SQLite
- `fastapi>=0.110` + `uvicorn` — product APIs
- `apscheduler>=3.10` — cron/interval triggers for pipelines
- `typer>=0.12` — CLI
- `textual` + `rich` — bootstrap wizard TUI
- `openpyxl` + `pandas` — xlsx projection read/write
- `watchdog` — file watching (document adapters, xlsx round-trip)
- `anthropic` — LLM client (abstracted behind a provider interface)
- `plaid-python` — Plaid client
- `google-api-python-client` + `google-auth` — Gmail, Calendar, Drive, People, Contacts adapters
- `msgraph-sdk` or `msgraph-core` — Microsoft Graph adapters (Outlook, Calendar, OneDrive, Contacts)
- `caldav`, `vobject` — CalDAV / CardDAV adapters
- `keyring` — OS keychain integration; secrets never on disk in plaintext
- `structlog` — structured JSON logs
- `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis` — tests
- `ruff`, `mypy`, `pyright` — lint + type check

Pin exact versions in `pyproject.toml`. Use `poetry` for dependency management. (Note: this was revised from the original `uv` directive to match the verification commands in prompts 02–18, which all use Poetry. Either tool works; the consistency matters more than the choice.)

**Node console deps:**
- `express ^4.21`
- `better-sqlite3 ^11` — readonly access to projection SQLite (not the event log)
- `js-yaml ^4.1`
- `http-proxy-middleware` — for proxying to Python APIs
- `esbuild ^0.24` — JSX compilation at profile-pack-install time
- `react ^18`, `react-dom ^18` — for profile pack JSX authoring
- No runtime build server; esbuild runs once per pack install, outputs go to `~/.adminme/packs/profiles/<id>/compiled/`

**Type safety:**
- Python: pyright strict. `mypy` as a backup check.
- Node: TypeScript on new Node code; JSX compiled with esbuild's type stripping; stubs for types where worthwhile.

**Tests:**
- 85% line coverage minimum on Python core (L1-L4); adapter integration tests run against fixtures or sandbox accounts but are excluded from the coverage floor.
- End-to-end smoke tests on Node console.
- Canary test suite (privacy, identity, isolation) runs on every CI build.

---

## L2: THE EVENT LOG (SOURCE OF TRUTH)

One append-only table. SQLCipher-encrypted. WAL mode. Partitioned logically by `owner_scope` (indexed, not physically partitioned in SQLite).

```sql
CREATE TABLE events (
  event_id          BLOB PRIMARY KEY,       -- ULID (16 bytes)
  event_type        TEXT NOT NULL,          -- dotted: "messaging.received"
  schema_version    INTEGER NOT NULL,
  occurred_at       TEXT NOT NULL,          -- ISO 8601 UTC
  recorded_at       TEXT NOT NULL,          -- ISO 8601 UTC
  source_adapter    TEXT NOT NULL,          -- "messaging:gmail_api"
  source_account_id TEXT NOT NULL,          -- opaque per-adapter account id
  owner_scope       TEXT NOT NULL,          -- "private:<member_id>" | "shared:household" | "org:<id>"
  visibility_scope  TEXT NOT NULL,          -- widened/narrowed from owner_scope
  sensitivity       TEXT NOT NULL,          -- "normal" | "sensitive" | "privileged"
  correlation_id    BLOB,                   -- ULID, optional
  causation_id      BLOB,                   -- ULID of event that caused this
  payload_json      TEXT NOT NULL,          -- validated against event_type's Pydantic model
  raw_ref           TEXT,                   -- path to cold storage for oversized payloads
  actor_identity    TEXT                    -- who/what caused this event to be recorded
);

CREATE INDEX idx_events_type_time ON events (event_type, occurred_at);
CREATE INDEX idx_events_scope_time ON events (owner_scope, occurred_at);
CREATE INDEX idx_events_correlation ON events (correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX idx_events_causation ON events (causation_id) WHERE causation_id IS NOT NULL;
```

**Raw payload sidecar.** Payloads >64KB are stored at `~/.adminme/data/raw_events/<yyyy>/<mm>/<event_id>.json.zst` (zstd-compressed); `raw_ref` in the event row points to the path. Attachments (email bodies with large HTML, images, PDFs) go to `~/.adminme/data/artifacts/<yyyy>/<mm>/<sha256>.<ext>` with a `raw_ref` of the form `artifact://<sha256>`. Artifact content is encrypted at rest (SQLCipher for the row; file encryption for the blob via `cryptography.fernet` keyed from the SQLCipher master key).

**Typed event registry.** Every event type has:
- A dotted name: `messaging.received`, `calendar.event.changed`, `commitment.proposed`, `skill.call.recorded`, `observation.suppressed`, `adminme.reward.dispatched`
- A Pydantic payload model registered in `adminme/lib/event_types/<namespace>/<type>.py`
- A `schema_version`; schema changes require an upcaster function
- Plugins register new event types via `hearth.event_types` entry point under their own dotted namespace (e.g., `myplugin.things_happened`)

**Append-only is enforced by code.** There is a single `EventStore.append(event)` function. The SQLite connection used for `events` writes has `PRAGMA query_only=0` only within that function; all other connections open it read-only. No UPDATE or DELETE on `events` table ever. A unit test uses SQLite triggers to enforce this.

**Appending is transactional.** `append()` validates the payload against the Pydantic model for the event type, inserts, commits. On commit, it publishes to the in-process event bus.

**Events are immutable but correctable.** If an event is wrong (e.g., classifier bug produced a wrong classification), the correction is a new event: `classification.corrected` with `causation_id` pointing to the original. Projections honor the latest truth.

---

## L2: THE EVENT BUS

In-process pub/sub using asyncio queues. Every event appended to the log is published to the bus. Subscribers (pipelines) receive events matching their filter.

```python
class EventBus(Protocol):
    async def publish(self, event: BaseEvent) -> None: ...
    async def subscribe(self, filter: EventFilter, handler: EventHandler) -> Subscription: ...
    async def replay(self, consumer_id: str, since: EventCursor) -> AsyncIterator[BaseEvent]: ...
```

**Swappability.** Two implementations ship:
- `InProcessBus` — asyncio queues, fire-and-forget with durable-consumer offsets stored in `bus_consumer_offsets` table
- `RedisStreamsBus` — backed by Redis Streams (for future scale-out)

Subscribers are the same code against both. An integration test runs the full pipeline suite against both bus implementations.

**Canonical topics** (event_type prefixes pipelines subscribe to):
- `messaging.*` — all messaging events
- `calendar.*` — calendar events
- `contacts.*` — contacts events
- `documents.*` — artifact events
- `telephony.*` — call, voicemail, SMS
- `financial.*` — MoneyFlow, account changes
- `identity.*` — identifier changes, merges
- `commitment.*` — commitment lifecycle
- `task.*` — task lifecycle (AdministrateMe-specific)
- `recurrence.*` — recurrence lifecycle
- `skill.*` — skill call records
- `adminme.reward.*`, `adminme.paralysis.*`, `adminme.digest.*` — CoS events
- `observation.*` — observation-mode suppression log
- `plaid.*` — Plaid-specific events
- `reminder.*` — Apple Reminders bidirectional events
- `member.*`, `profile.*`, `persona.*` — platform events
- `system.*` — health, backup, restore

---

## L3: PROJECTIONS

Each projection is a deterministic pure function from a subset of events to a set of tables (or files, in the case of xlsx). Projections have:
- A **name** (`parties`, `interactions`, `commitments`, `xlsx_ops_workbook`, …)
- A **version** (bumped when projection logic changes, triggers rebuild)
- A list of **event types** it subscribes to
- A **cursor** (last processed event id)
- An idempotent `apply(event)` method
- A `rebuild()` method that truncates its tables and replays from event 0

**Projection registry** tracks status; CLI: `adminme projections list`, `adminme projections rebuild <name>`, `adminme projections lag`.

**Required v1 projections:**

### 3.1 `parties` projection — the CRM core
Writes to tables `parties`, `identifiers`, `memberships`, `relationships`. Subscribes to: `contacts.*`, `messaging.received`, `messaging.sent`, `telephony.*`, `identity.*`, and manual events (`party.created`, `relationship.added`).

```sql
CREATE TABLE parties (
  party_id         TEXT PRIMARY KEY,         -- ULID, persistent
  kind             TEXT NOT NULL,            -- 'person' | 'organization' | 'household'
  display_name     TEXT NOT NULL,
  sort_name        TEXT NOT NULL,            -- for alphabetizing
  nickname         TEXT,
  pronouns         TEXT,
  birthday         TEXT,                     -- ISO date, partial OK (--MM-DD)
  deathday         TEXT,
  primary_identifier_id TEXT,
  notes            TEXT,
  attributes_json  TEXT NOT NULL DEFAULT '{}', -- open; plugins namespace their keys
  owner_scope      TEXT NOT NULL,
  visibility_scope TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  last_event_id    BLOB NOT NULL             -- for projection rebuild idempotency
);

CREATE TABLE identifiers (
  identifier_id    TEXT PRIMARY KEY,
  party_id         TEXT NOT NULL REFERENCES parties(party_id),
  kind             TEXT NOT NULL,            -- open enum: email, phone, imessage_handle, ...
  value            TEXT NOT NULL,
  value_normalized TEXT NOT NULL,            -- canonicalized for matching (E.164, lowercase email)
  verified         INTEGER NOT NULL DEFAULT 0,
  primary_for_kind INTEGER NOT NULL DEFAULT 0,
  source_event_id  BLOB NOT NULL,
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  UNIQUE (kind, value_normalized)
);

CREATE TABLE memberships (
  membership_id    TEXT PRIMARY KEY,
  party_id         TEXT NOT NULL REFERENCES parties(party_id),
  parent_party_id  TEXT NOT NULL REFERENCES parties(party_id),
  role             TEXT NOT NULL,            -- open: member, principal, child, employee, owner
  started_at       TEXT,
  ended_at         TEXT,
  attributes_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE relationships (
  relationship_id  TEXT PRIMARY KEY,
  party_a          TEXT NOT NULL REFERENCES parties(party_id),
  party_b          TEXT NOT NULL REFERENCES parties(party_id),
  label            TEXT NOT NULL,            -- open: spouse, parent_of, child_of, sibling,
                                             --       friend, colleague, client, service_provider,
                                             --       coparent, neighbor, ex_spouse
  direction        TEXT NOT NULL,            -- 'a_to_b' | 'b_to_a' | 'mutual'
  since            TEXT,
  ended_at         TEXT,
  attributes_json  TEXT NOT NULL DEFAULT '{}'
);
```

### 3.2 `interactions` projection — deduplicated touchpoints
Writes to `interactions`, `interaction_participants`, `interaction_attachments`. Subscribes to `messaging.*`, `telephony.*`, `calendar.event.concluded`.

```sql
CREATE TABLE interactions (
  interaction_id   TEXT PRIMARY KEY,
  direction        TEXT NOT NULL,            -- 'inbound' | 'outbound' | 'mutual'
  channel_family   TEXT NOT NULL,            -- 'messaging' | 'telephony' | 'calendaring' | 'in_person'
  channel_specific TEXT NOT NULL,            -- 'gmail' | 'imessage' | 'sms' | 'ringcentral_voicemail' | ...
  occurred_at      TEXT NOT NULL,
  subject          TEXT,                     -- LLM-extracted (lazy)
  summary          TEXT,                     -- LLM-extracted (lazy)
  body_ref         TEXT,                     -- artifact:// or inline pointer
  raw_event_ids    TEXT NOT NULL,            -- JSON array of event_ids this interaction aggregates
  owner_scope      TEXT NOT NULL,
  visibility_scope TEXT NOT NULL,
  sensitivity      TEXT NOT NULL,
  response_urgency TEXT,                     -- 'now' | 'today' | 'this_week' | 'whenever' | 'none'
  suggested_action TEXT,                     -- 'reply' | 'create_task' | 'file' | 'confirm' | 'none'
  auto_handled     INTEGER NOT NULL DEFAULT 0,
  last_event_id    BLOB NOT NULL
);

CREATE TABLE interaction_participants (
  interaction_id   TEXT NOT NULL REFERENCES interactions(interaction_id),
  party_id         TEXT NOT NULL REFERENCES parties(party_id),
  role             TEXT NOT NULL,            -- 'from' | 'to' | 'cc' | 'bcc' | 'mentioned'
  PRIMARY KEY (interaction_id, party_id, role)
);

CREATE TABLE interaction_attachments (
  interaction_id   TEXT NOT NULL REFERENCES interactions(interaction_id),
  artifact_id      TEXT NOT NULL REFERENCES artifacts(artifact_id),
  PRIMARY KEY (interaction_id, artifact_id)
);
```

### 3.3 `artifacts` projection
```sql
CREATE TABLE artifacts (
  artifact_id        TEXT PRIMARY KEY,
  mime_type          TEXT NOT NULL,
  byte_size          INTEGER NOT NULL,
  sha256             TEXT NOT NULL UNIQUE,
  source_adapter     TEXT NOT NULL,
  storage_ref        TEXT NOT NULL,         -- artifact://<sha256> or path
  title              TEXT,
  extracted_text     TEXT,                  -- OCR / document text extraction
  extracted_structured_json TEXT,           -- typed payloads (invoice, contract, prescription, etc.)
  extracted_structured_kind TEXT,           -- open enum: invoice, contract, medical_record, school_form, ...
  captured_at        TEXT NOT NULL,
  owner_scope        TEXT NOT NULL,
  visibility_scope   TEXT NOT NULL,
  sensitivity        TEXT NOT NULL,
  last_event_id      BLOB NOT NULL
);

CREATE TABLE artifact_links (                -- polymorphic link table
  artifact_id   TEXT NOT NULL REFERENCES artifacts(artifact_id),
  linked_kind   TEXT NOT NULL,              -- 'party' | 'asset' | 'account' | 'place' | 'interaction'
  linked_id     TEXT NOT NULL,
  link_role     TEXT,                       -- 'mentioned' | 'owner' | 'subject' | 'sender' | 'recipient'
  PRIMARY KEY (artifact_id, linked_kind, linked_id, link_role)
);
```

### 3.4 `commitments` projection — the obligation tracker
```sql
CREATE TABLE commitments (
  commitment_id    TEXT PRIMARY KEY,
  owed_by_party    TEXT NOT NULL REFERENCES parties(party_id),
  owed_to_party    TEXT NOT NULL REFERENCES parties(party_id),
  kind             TEXT NOT NULL,           -- open: reply, thank_you, payment, deliverable,
                                            --       follow_up, gift, appointment, decision, introduction
  description      TEXT NOT NULL,
  due_at           TEXT,
  status           TEXT NOT NULL,           -- 'pending' | 'snoozed' | 'done' | 'cancelled' | 'delegated'
  confidence       REAL,                    -- 0.0-1.0 if extracted by skill
  source_interaction_id TEXT REFERENCES interactions(interaction_id),
  source_skill     TEXT,                    -- 'commitment_extraction@v3'
  proposed_at      TEXT,                    -- when pipeline proposed
  confirmed_at     TEXT,                    -- when human approved
  confirmed_by     TEXT,                    -- party_id of approver
  completed_at     TEXT,
  owner_scope      TEXT NOT NULL,
  visibility_scope TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);
```

### 3.5 `tasks` projection — household work (AdministrateMe-specific, not Hearth)
Tasks are not Commitments. Commitments are owed to a Party; Tasks are household work (mow lawn, renew license, reorder filter). The ADHD neuroprosthetic (rewards, paralysis, whatnow) operates primarily over Tasks + Commitments together, unified in the inbox.

```sql
CREATE TABLE tasks (
  task_id          TEXT PRIMARY KEY,
  title            TEXT NOT NULL,
  status           TEXT NOT NULL,           -- inbox | next | in_progress | waiting_on |
                                            -- deferred | done | dismissed
  assignee_party   TEXT REFERENCES parties(party_id),  -- NULL = household-shared
  domain           TEXT NOT NULL,           -- open: tasks, home, health, finance, travel,
                                            --       kids, pets, vehicles, social
  energy           TEXT,                    -- low | medium | high
  effort           TEXT,                    -- tiny | small | medium | large
  item_type        TEXT NOT NULL,           -- task | purchase | appointment | research |
                                            -- decision | chore | maintenance
  due_date         TEXT,
  micro_script     TEXT,                    -- first concrete step (for ADHD members)
  linked_item_id   TEXT,                    -- polymorphic: party/asset/account/place
  linked_item_kind TEXT,
  recurring_id     TEXT REFERENCES recurrences(recurrence_id),
  depends_on_json  TEXT NOT NULL DEFAULT '[]',  -- array of task_ids
  goal_ref         TEXT,                    -- parent task_id (for sub-tasks under a goal)
  life_event       TEXT,                    -- 'move' | 'baby' | 'closing' | null
  auto_research    INTEGER NOT NULL DEFAULT 0,
  waiting_on       TEXT,                    -- party_id or free text
  waiting_since    TEXT,
  created_at       TEXT NOT NULL,
  created_by       TEXT,                    -- party_id or adapter id
  completed_at     TEXT,
  completed_by     TEXT,
  source_system    TEXT,                    -- 'manual' | 'reminders' | 'siri' | 'skill' | 'recurring'
  notes            TEXT,
  owner_scope      TEXT NOT NULL,
  visibility_scope TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);
```

### 3.6 `recurrences` projection
```sql
CREATE TABLE recurrences (
  recurrence_id    TEXT PRIMARY KEY,
  linked_kind      TEXT NOT NULL,           -- 'party' | 'asset' | 'account' | 'household'
  linked_id        TEXT NOT NULL,
  kind             TEXT NOT NULL,           -- birthday | anniversary | annual_physical |
                                            -- oil_change | license_renewal | bill | chore | dock_in
  rrule            TEXT NOT NULL,           -- RFC 5545 RRULE string
  next_occurrence  TEXT NOT NULL,
  lead_time_days   INTEGER NOT NULL DEFAULT 0,
  trackable        INTEGER NOT NULL DEFAULT 0,  -- feeds scoreboard
  notes            TEXT,
  owner_scope      TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);
```

### 3.7 `calendars` projection
```sql
CREATE TABLE calendar_events (
  calendar_event_id TEXT PRIMARY KEY,
  calendar_source   TEXT NOT NULL,          -- which adapter+account this came from
  external_uid      TEXT NOT NULL,
  owner_party       TEXT REFERENCES parties(party_id),
  summary           TEXT,
  description       TEXT,
  location          TEXT,
  start_at          TEXT NOT NULL,
  end_at            TEXT NOT NULL,
  all_day           INTEGER NOT NULL DEFAULT 0,
  attendees_json    TEXT NOT NULL DEFAULT '[]',
  privacy           TEXT NOT NULL DEFAULT 'open',  -- open | privileged | redacted
  title_redacted    TEXT,                   -- shown when privacy=privileged
  owner_scope       TEXT NOT NULL,
  visibility_scope  TEXT NOT NULL,
  sensitivity       TEXT NOT NULL,
  last_event_id     BLOB NOT NULL,
  UNIQUE (calendar_source, external_uid)
);

CREATE TABLE availability_blocks (          -- for busy-free calendars (no content)
  availability_id  TEXT PRIMARY KEY,
  party_id         TEXT NOT NULL REFERENCES parties(party_id),
  start_at         TEXT NOT NULL,
  end_at           TEXT NOT NULL,
  source_adapter   TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);
```

### 3.8 `places_assets_accounts` projection
```sql
CREATE TABLE places (
  place_id         TEXT PRIMARY KEY,
  display_name     TEXT NOT NULL,
  kind             TEXT NOT NULL,           -- home | second_home | office | school | medical |
                                            -- gym | church | cemetery | ...
  address_json     TEXT NOT NULL,
  geo_lat          REAL,
  geo_lon          REAL,
  attributes_json  TEXT NOT NULL DEFAULT '{}',
  owner_scope      TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);

CREATE TABLE place_associations (
  place_id         TEXT NOT NULL REFERENCES places(place_id),
  party_id         TEXT NOT NULL REFERENCES parties(party_id),
  role             TEXT NOT NULL,           -- homeowner | tenant | employee | student | member
  started_at       TEXT,
  ended_at         TEXT,
  PRIMARY KEY (place_id, party_id, role)
);

CREATE TABLE assets (
  asset_id         TEXT PRIMARY KEY,
  display_name     TEXT NOT NULL,
  kind             TEXT NOT NULL,           -- vehicle | appliance | instrument | boat | firearm | pet
  linked_place     TEXT REFERENCES places(place_id),
  attributes_json  TEXT NOT NULL DEFAULT '{}',
  owner_scope      TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);

CREATE TABLE asset_owners (
  asset_id         TEXT NOT NULL REFERENCES assets(asset_id),
  party_id         TEXT NOT NULL REFERENCES parties(party_id),
  ownership_type   TEXT NOT NULL DEFAULT 'owner',  -- owner | co_owner | user
  PRIMARY KEY (asset_id, party_id)
);

CREATE TABLE accounts (
  account_id       TEXT PRIMARY KEY,
  display_name     TEXT NOT NULL,
  organization     TEXT NOT NULL REFERENCES parties(party_id),  -- the counterparty (utility, bank)
  kind             TEXT NOT NULL,           -- utility | subscription | insurance | license |
                                            -- bank | credit_card | loan | brokerage
  status           TEXT NOT NULL,           -- active | dormant | cancelled | pending
  billing_rrule    TEXT,                    -- when does this recur
  next_renewal     TEXT,
  login_vault_ref  TEXT,                    -- op://... or 1password://... — never the password itself
  linked_asset     TEXT REFERENCES assets(asset_id),
  linked_place     TEXT REFERENCES places(place_id),
  attributes_json  TEXT NOT NULL DEFAULT '{}',
  owner_scope      TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);
```

### 3.9 `money` projection
```sql
CREATE TABLE money_flows (
  flow_id          TEXT PRIMARY KEY,
  from_party       TEXT REFERENCES parties(party_id),
  to_party         TEXT REFERENCES parties(party_id),
  amount_minor     INTEGER NOT NULL,        -- amount in smallest currency unit (cents)
  currency         TEXT NOT NULL,           -- ISO 4217
  occurred_at      TEXT NOT NULL,
  kind             TEXT NOT NULL,           -- paid | received | owed | reimbursable
  category         TEXT,                    -- open; learned tenant-level
  linked_artifact  TEXT REFERENCES artifacts(artifact_id),
  linked_account   TEXT REFERENCES accounts(account_id),
  linked_interaction TEXT REFERENCES interactions(interaction_id),
  notes            TEXT,
  owner_scope      TEXT NOT NULL,
  last_event_id    BLOB NOT NULL
);
```

### 3.10 `vector_search` projection
Uses `sqlite-vec`. Embeds Interaction summaries, Artifact extracted_text, Party notes. Respects `sensitivity` — privileged content is never embedded (never enters the LLM context window for cross-party semantic search).

```sql
CREATE VIRTUAL TABLE vector_index USING vec0(
  embedding_id TEXT PRIMARY KEY,
  linked_kind TEXT,
  linked_id TEXT,
  embedding float[1536],  -- model-dependent
  sensitivity TEXT,
  owner_scope TEXT
);
```

### 3.11 `xlsx_workbooks` projection — BIDIRECTIONAL HUMAN-EDITABLE VIEW

AdministrateMe-specific. Two xlsx files are generated as views of the event log so principals can edit in Numbers/Excel during weekly reviews, paying bills, or doing quarterly financial planning:

- `~/.adminme/projections/adminme-ops.xlsx` — Tasks, Recurrences, Commitments, curated People view, Lists
- `~/.adminme/projections/adminme-finance.xlsx` — MoneyFlows (Raw Data), Accounts, Assumptions, Dashboard, Balance Sheet, 5-Year Pro Forma, Budget vs Actual

The workbooks are not an afterthought. Some households treat spreadsheets as a load-bearing tool: financial planning happens in Excel, weekly reviews happen in Excel, quarterly "what's the kids' activities cost" runs in Excel. The platform embraces this by making the xlsx a **first-class projection** — the event log is the source of truth, but the xlsx round-trips so that human edits (not just reads) produce events. If the xlsx is deleted or corrupted, it regenerates fully from the event log on next projection rebuild.

#### Workbook: `adminme-ops.xlsx`

Seven sheets, bidirectional where indicated:

**Sheet 1: `Tasks` [bidirectional]**

Header row (frozen):
```
task_id | title | status | assigned_member | owed_to_party | due_date | urgency | effort_min | energy | context | notes | created_at | completed_at
```

- `task_id` is the canonical id from the tasks projection. For new rows added by the tenant, leave blank — the reverse projector assigns one.
- `status` is a dropdown-validated cell (data validation list): `todo | in_progress | blocked | done | cancelled`.
- `assigned_member` and `owed_to_party` are dropdown-validated against the parties projection (member list + party list respectively).
- `due_date` is an Excel date; the reverse projector converts to ISO.
- All other cells are free-form, reverse-projected as string/int/enum per the column type.
- Added rows trigger `task.created`; modified rows trigger `task.updated`; rows whose status changes to `done` additionally trigger `task.completed`; rows deleted (row-erase) trigger `task.deleted` after a 5-second debounce (prevents accidental deletion from propagating).

**Sheet 2: `Recurrences` [bidirectional]**

```
recurrence_id | title | cadence | next_due | assigned_member | notes | active | last_completed_at
```

- `cadence` is a free-form string parsed by the recurrence parser skill on ingest. Common forms: `"every 2 weeks"`, `"first of month"`, `"weekly mon"`, `"every 90 days"`.
- `next_due` is derived and protected; edits rejected (see protection below).
- `last_completed_at` is derived and protected.

**Sheet 3: `Commitments` [bidirectional but restricted]**

```
commitment_id | owed_by_member | owed_to_party | kind | text_summary | suggested_due | status | confidence | strength | source_summary
```

- Status transitions via cell edit: `pending → confirmed | dismissed | completed`.
- `confidence`, `strength`, and `source_summary` are derived and protected (come from the extraction pipeline).
- Principals can edit `text_summary` (override the skill's wording) and `suggested_due` (override the skill's guess).

**Sheet 4: `People` [read-only curated view]**

A curated subset of the parties projection: tier-2-and-above parties with columns:

```
party_id | display_name | tier | tags | primary_email | primary_phone | last_contact | cadence_target | open_commitments
```

Fully protected. Purpose: a quick human-readable CRM overview for weekly reviews. Edits rejected with a toast; to modify a party, use the CRM surface in the console.

**Sheet 5: `Lists` [bidirectional]**

Flat rollup of active shopping/packing/errand lists. One row per item:

```
list_name | item | added_by | added_at | completed | completed_at | notes
```

- `list_name` is the list's short key (`grocery`, `hardware`, `vitamins`, etc.). Dropdown-validated against existing lists; typing a new name creates a new list.
- Adding a row emits `list_item.added`; setting `completed` to TRUE emits `list_item.completed`.
- Syncs bidirectionally with Apple Reminders via the reminders adapter (see §10).

**Sheet 6: `Members` [read-only]**

```
member_id | role | profile | display_name | primary_email | primary_phone | active | joined_at
```

Fully protected. Shows the household's members with their profile assignments. Edit in the console's Settings → Members pane.

**Sheet 7: `Metadata` [read-only]**

```
key | value
---
generated_at | 2026-04-21T13:42:00-04:00
projection_version | 4
last_event_id | ev_0j5k8m2n4p6q8r
tenant_id | stice-household
```

A small provenance sheet so the principal can verify what projection state produced the file. Fully protected.

#### Workbook: `adminme-finance.xlsx`

Seven sheets, bidirectional where indicated:

**Sheet 1: `Raw Data` [bidirectional with Plaid priority]**

```
txn_id | date | account_last4 | merchant_name | merchant_category | amount | memo | plaid_category | assigned_category | notes | is_manual
```

- Plaid-sourced rows have `is_manual = FALSE` and `txn_id` non-blank. These rows' `date`, `account_last4`, `merchant_name`, `amount`, and `plaid_category` are protected (Plaid is authoritative for those fields). Principals can edit `assigned_category`, `notes`, and `memo`.
- Principal-added rows (`is_manual = TRUE`) are fully editable and emit `money_flow.manually_added` events on save. Deleting a manual row emits `money_flow.manually_deleted`. Deleting a Plaid row is rejected (use the Plaid sync toggle instead).
- `assigned_category` overrides `plaid_category` for downstream aggregation.

**Sheet 2: `Accounts` [read-only]**

```
account_id | institution | account_name | account_type | last4 | current_balance | available_balance | as_of | plaid_linked | link_healthy
```

Fully protected. Reflects the accounts projection. To add an account, link via Plaid in Settings; to remove, unlink in Settings.

**Sheet 3: `Assumptions` [bidirectional]**

The tenant's input to the pro-forma model. This is where the principals do their financial planning.

```
category | assumption | value | unit | notes | as_of
```

Examples of rows (generic — the real values come from the bootstrap wizard's financial section):

```
income        | principal_a_gross_annual        | <USD/yr>  | USD/yr    | Note        | <ISO date>
income        | principal_b_gross_annual        | <USD/yr>  | USD/yr    | Note        | <ISO date>
savings_rate  | target_pretax_pct               | <ratio>   | ratio     |             | <ISO date>
mortgage      | primary_residence_principal     | <USD>     | USD       | as-of note  | <ISO date>
mortgage      | primary_residence_rate          | <ratio>   | annual    |             | <ISO date>
kids          | child_n_activities_yr           | <USD/yr>  | USD/yr    | notes       | <ISO date>
childcare     | childcare_monthly               | <USD/mo>  | USD/mo    | start date  | <ISO date>
tax           | marginal_federal                | <ratio>   | ratio     |             | <ISO date>
tax           | state_<state_code>              | <ratio>   | ratio     |             | <ISO date>
```

Each row is a projected `assumption.*` event. The pro-forma sheets read from this. Principals can add, edit, or delete rows freely.

**Sheet 4: `Dashboard` [read-only, derived]**

A one-page summary: net worth, MTD spending, top 5 categories, top 5 merchants, savings rate vs target, 3 leading anomalies. Fully protected. Refreshes on every forward-projection regeneration.

**Sheet 5: `Balance Sheet` [read-only, derived]**

Two columns: assets and liabilities, as of the most recent Plaid sync timestamp. Net worth at bottom. Fully protected.

**Sheet 6: `5-Year Pro Forma` [read-only, derived]**

60 monthly columns, one row per income/expense line, subtotals by category, running cumulative savings, projected net worth curve. Formulas reference the Assumptions sheet — but the formulas are written **as values**, not Excel formulas, because the projector computes each cell deterministically from the event-sourced assumption history. (See "Why computed values, not formulas" below.)

**Sheet 7: `Budget vs Actual` [read-only, derived]**

Per-category rows: budgeted monthly from Assumptions, actual MTD from Raw Data, variance in $ and %, projected month-end based on current pace. Color-coded cells (conditional formatting applied at projection time via openpyxl cell.fill).

#### Forward projection (events → xlsx)

A Python daemon (`adminme/daemons/xlsx_sync/forward.py`) subscribes to the event bus, filters to event types that affect either workbook, and debounces regenerations.

**Trigger events for `adminme-ops.xlsx`:**
```
task.*, recurrence.*, commitment.*, party.*, identifier.*,
party.tag.*, party.tier.computed, list_item.*,
member.created, member.profile_changed, member.role_changed
```

**Trigger events for `adminme-finance.xlsx`:**
```
money_flow.*, account.*, assumption.*, plaid.sync.completed,
institution.*, plaid.go_live
```

**Debounce:** 5 seconds after the most recent trigger event. If a burst of 40 task updates lands in 2 seconds, the projector waits until 5s of silence then regenerates once.

**Generation algorithm:**

1. Acquire a file-level lock on the workbook (flock on a sidecar `.lock` file). The reverse projector also checks this lock; if held, it waits.
2. Open the file in `openpyxl` with `keep_vba=False`, `data_only=False`.
3. For each bidirectional sheet: query the relevant projection for current state; diff against existing rows by `*_id`; update changed cells; append new rows; mark (via the deletion convention) rows no longer in the projection. Do not delete rows from the sheet in forward projection — deletions in the sheet must originate from the event log, which the reverse projector handled already.
4. For each read-only sheet: regenerate contents from scratch (they have no bidirectional state to preserve).
5. Apply protection: every cell whose column is tagged `[derived]` in the header row gets `cell.protection = Protection(locked=True)`. Sheet protection is enabled with the sheet password stored in the instance's secrets.
6. Apply conditional formatting on derived sheets (dashboard tiles, budget variance coloring).
7. Write to a temp file; rename atomically to replace the target (prevents readers from seeing a partial file).
8. Release the lock.
9. Emit `xlsx.regenerated` event with sheet list + generation time + last_event_id consumed.

**Idempotency:** running forward projection twice in a row with no intervening events produces byte-identical output (modulo the `Metadata` sheet's `generated_at` field, which is deterministic from the last_event_id if we want true byte-equality — we don't; human-useful timestamp wins).

#### Reverse projection (xlsx → events)

A separate daemon (`adminme/daemons/xlsx_sync/reverse.py`) watches the two files via `watchdog` (inotify on Linux, FSEvents on macOS).

**Detection:**

On any `modified` event:
1. Wait 2 seconds for the writer (Numbers, Excel) to finish flushing multi-write saves.
2. Check the file-level lock held by the forward projector. If held, wait up to 10 seconds; if still held, skip this cycle (forward will emit a regenerate event that picks up the human edit on round-trip).
3. Acquire the lock ourselves.
4. Open with `openpyxl(data_only=True)` to get computed values.

**Diff algorithm:**

For each bidirectional sheet:
1. Load current sheet state into a DataFrame keyed by `*_id`.
2. Load the last-known sheet state from the sidecar file `~/.adminme/projections/.xlsx-state/<workbook>/<sheet>.json`. This is a snapshot of the sheet as forward projection last wrote it.
3. Diff the two DataFrames:
   - Rows in current not in last-known: **added** — emit creation event (`task.created`, `money_flow.manually_added`, etc.) with reverse-projected payload.
   - Rows in last-known not in current: **deleted** — emit deletion event, guarded by a 5-second "undo window" where if the principal CTRL-Zs within 5s the deletion is cancelled.
   - Rows in both with differing cells: **modified** — emit update event with only the changed fields in the payload.
4. Emit events through the standard `emit_event` path (not direct to log — goes through validation and guardedWrite allowlist).
5. Write the new sheet state to the sidecar.
6. Release the lock.

**Derived-cell protection at reverse time:**

Excel's cell protection is advisory — a determined tenant can unlock the sheet. The reverse projector enforces independently:

1. Any edit to a cell whose column is tagged `[derived]` in the header row is **ignored** (not rejected, not logged — silently dropped, because a rejection here would be a UX bug not a security violation).
2. Any edit to a cell in a read-only sheet is **ignored** with a warning log entry.
3. Any attempt to edit `txn_id`, `task_id`, `recurrence_id`, `commitment_id`, `account_id`, or `party_id` columns: ignored silently — these are projection-assigned and user edits are treated as accidental.
4. Any attempt to edit `is_manual` from FALSE to TRUE on a Plaid row: ignored (can't convert a Plaid row to manual; delete it and add a new manual row instead).

When a reverse projection cycle completes, the daemon emits `xlsx.reverse_projected` with the list of events it emitted. This event then triggers forward projection (via the event bus), which regenerates the file. The round trip confirms: human edits → events → projection → regenerated xlsx. Byte-level changes between the human's saved file and the regenerated file are inevitable (formula-style formatting, cell widths, etc.) but semantic changes are preserved.

#### Why computed values, not Excel formulas?

The pro-forma and budget sheets could be implemented with Excel formulas that reference the Assumptions sheet. We don't, because:

1. **Reproducibility.** If the tenant opens the workbook on a different device or Excel version, formulas behave differently (date math, tax functions, string comparisons). Computed values are byte-equivalent on every device.
2. **Audit.** The event log records every assumption change with timestamps. The pro-forma computed values reflect exactly that history. If a principal wants to know "what did our 2028 projection look like on April 1?", the platform can replay to that date — not possible with live formulas.
3. **Round-trip safety.** If a formula produces `#REF!` because a tenant deleted a row the formula depended on, the xlsx becomes corrupted and the projector's diff algorithm sees phantom changes. Computed values have no such fragility.
4. **Tenant sanity.** A principal should not accidentally break the pro-forma by editing a formula. Derived sheets are fully protected; the actual math happens in the projector code, which is unit-tested against fixtures.

#### Conflict resolution

If the forward projector is regenerating when the tenant saves the file:
1. The reverse projector's lock-check detects the hold and skips the cycle.
2. The forward projector completes its regeneration — which **overwrites** the tenant's in-progress save.
3. The tenant's edits are lost on that cycle.

This is a known tradeoff. Mitigations:
- The forward projector's debounce is 5 seconds, so the window is small.
- If the reverse projector detects a skip, it logs `xlsx.reverse_skipped_during_forward` so the principal can see if it's happening repeatedly.
- In practice the tenant rarely saves during a forward regeneration because forward regenerations are near-instant (<500ms for typical workbooks; the 5s debounce dominates).

If the tenant never opens the xlsx and edits live in the console, this entire pathway is idle. The xlsx regenerates on event bursts but nothing reads it; no round-trip happens.

#### Rebuild from scratch

`adminme projection rebuild xlsx_workbooks` deletes both xlsx files plus their sidecar state and regenerates from a full event-log replay. Used after major schema migrations or when the sidecar state gets out of sync (rare; symptoms include repeated spurious add/delete events on reverse projection).

#### Testing

Fixture tests in `adminme/projections/xlsx_workbooks/tests/`:
- `test_forward_tasks_roundtrip.py` — event sequence → regenerate → assert cell values.
- `test_reverse_new_task.py` — add a row → reverse → assert event emitted with correct payload.
- `test_reverse_protected_cell_ignored.py` — edit a derived cell → reverse → assert no event.
- `test_lock_contention.py` — forward and reverse racing → assert no duplicate events, no corruption.
- `test_plaid_row_protection.py` — attempt to edit merchant_name on a Plaid row → reverse → assert unchanged.
- `test_replay_equivalence.py` — run forward from an empty state vs. from an existing state; assert identical output.
- `test_assumption_pro_forma_math.py` — assumptions-table snapshot → assert specific pro-forma values (catches regressions in the math).

Every test uses an isolated instance directory; never touches `~/.adminme/`.

This means the xlsx files remain a first-class human surface, but the event log remains authoritative. If the xlsx is deleted or corrupted, it regenerates fully on next projection rebuild.


---

## L3 CONTINUED: THE SESSION & SCOPE ENFORCEMENT

There is no "global database connection" in Python code. All reads and writes go through a `Session` object constructed with `(current_user: PartyId, requested_scopes: list[Scope])`. The Session rejects queries whose data is not in an allowed scope.

```python
class Session:
    def __init__(self, current_user: PartyId, requested_scopes: list[Scope]): ...

    def parties(self) -> PartyQuery: ...
    def interactions(self) -> InteractionQuery: ...
    def commitments(self) -> CommitmentQuery: ...
    def artifacts(self) -> ArtifactQuery: ...
    def tasks(self) -> TaskQuery: ...
    # ...

    def append_event(self, event: BaseEvent) -> None: ...  # validates owner_scope is in allowed_scopes
```

**Every query adds scope predicates automatically.** A query for Interactions with a principal Session filters `WHERE visibility_scope IN (allowed_scopes) AND sensitivity != 'privileged' OR owner_scope = current_user`. A Session for a coach-role LLM call strips financial+health columns.

**Test enforcement.** Every projection test includes a canary: attempt to read data outside scope → raises `ScopeViolation`. Every API endpoint test: unauthenticated → 401; wrong scope → 403. The static analysis rule: no code imports `sqlalchemy.orm.Session` directly; all DB access goes through `adminme.lib.session.Session`.

**Privileged channels.** When an adapter is configured as privileged (e.g., a law-practice email account for Laura), `sensitivity: privileged` is enforced at the adapter level on every event it emits. Privileged events do not:
- Enter the `vector_search` projection
- Get summarized by LLM skills
- Appear in cross-owner projections
- Appear in coach-role sessions
- Appear in the `-kids` agent's context

The adapter has a hardcoded sensitivity floor. If misconfigured in a way that would lower it, the config loader rejects the config with an explicit error.

---

## L4: PIPELINES

Pipelines subscribe to events and produce derived events, proposals, or skill calls. Never write projections directly. Independently enable-able via `config/pipelines.yaml`.

**How pipelines get triggered.** Two mechanisms, chosen per pipeline:

- **Event-subscription pipelines** (most of them — `identity_resolution`, `commitment_extraction`, `noise_filtering`, `artifact_classification`, etc.) run *reactively* inside the AdministrateMe pipeline runner. Each subscribes to a set of event types via `triggers.events` in its pack manifest; when a matching event lands in the log, the event bus invokes the pipeline's handler. These pipelines do not involve OpenClaw directly — they are pure AdministrateMe-layer code that may call skills (which do go through OpenClaw's skill runner, per L4 Skill Runner section).
- **Scheduled/proactive pipelines** (`morning_digest`, `paralysis_detection`, `reminder_dispatch`, `crm_surface`, `custody_brief`) run on a clock and may produce outbound side effects (a message to a principal, a push notification). These are registered as **OpenClaw standing orders** during product boot. The standing order's handler is the pipeline's entrypoint; OpenClaw handles the scheduling primitive, the approval gating (via `exec-approvals`), and — critically — the channel delivery of whatever the pipeline composes. This gives the proactive behaviors the same session/rate-limit/observation-mode context as interactive chat turns.

**Required v1 pipelines** (Hearth's plus AdministrateMe's):

### Hearth-derived pipelines

#### `identity_resolution`
Subscribes: `contacts.record_changed`, `messaging.received`, `messaging.sent`, `telephony.*`.

On a new Identifier (email, phone, handle):
1. Check `identifiers.value_normalized` for exact match → link to existing Party
2. If no match, compute similarity (Levenshtein on display name, domain heuristics for email, carrier lookup for phone)
3. Above threshold 0.85 → emit `identity.merge_suggested` (not auto-merged; goes to human review queue in the inbox)
4. Below threshold → emit `party.created` with new Party and first Identifier
5. Confidence-scored; all merges are provenance-tracked via `source_skill` or `source_heuristic`

Never auto-merges above threshold. Always human-approved. Humans can accept merges that themselves merge (transitive closure propagates via events).

#### `noise_filtering`
Subscribes: `messaging.received`, `telephony.sms_received`.

Classifies inbound messages as: `noise` (bulk marketing, alerts you've filtered before), `transactional` (receipts, shipping notifications), `personal`, `professional`, `promotional`. Skill: `classify_message_nature@v2`. Learns from user feedback (thumbs-up/down in inbox).

Emits `messaging.classified` with the classification + confidence + skill version. The `interactions` projection uses this to decide whether to create an Interaction row or suppress to the noise bucket.

#### `commitment_extraction`
Subscribes: `messaging.received`, `messaging.sent`, `telephony.voicemail_transcribed`, `calendar.event.concluded`, `capture.note_created`.

Scans interactions for implied commitments:
- "Can you send me the report by Friday?" → commitment(owed_by: Laura, owed_to: <sender>, kind: deliverable, due_at: Friday)
- "Thank you for picking up the dry cleaning" → nothing owed (ack); update Relationship last_meaningful_contact
- "I'll get back to you tomorrow on this" → commitment(owed_by: <self>, owed_to: <recipient>, kind: follow_up, due_at: tomorrow)
- "The plumber came by and fixed the leak; we owe him $450" → commitment(owed_by: household, owed_to: plumber_party, kind: payment, amount: 450)

Skill: `extract_commitments@v4`. Output: list of `CommitmentProposed` events. Humans approve via inbox; approval emits `commitment.confirmed`; projection promotes to active commitment.

#### `thank_you_detection`
Specialization of commitment extraction for gratitude. Subscribes: same as above, plus `financial.money_flow_recorded` (e.g., a completed service).

When service-providers complete significant work, or when a friend does something kind, proposes a `thank_you` commitment owed by the household to them. Owner-scoped (James's thank-yous are James's; Laura's are Laura's; shared household thank-yous go to both inboxes with ability to claim).

#### `recurrence_extraction`
Subscribes: `contacts.record_changed`, `documents.artifact_classified`, `capture.note_created`.

- Birthdays from contact records → `recurrence.added` with kind=birthday, rrule=yearly on that date
- Anniversaries from notes → `recurrence.added` with kind=anniversary
- License/renewal dates from parsed documents → `recurrence.added` with kind=license_renewal, lead_time=30 days
- Service intervals (oil change, filter replacement) from manuals → `recurrence.added` with lead_time

All recurrences are proposals (`recurrence.proposed`) until human-confirmed.

#### `artifact_classification`
Subscribes: `documents.artifact_discovered`.

Runs OCR (if image/PDF) → extracts text → runs `classify_artifact@v3` skill → structured extraction skill per type:
- Invoice → `InvoiceData(vendor, amount, due_at, line_items)`
- Contract → `ContractData(parties, effective_at, renewal, terms)`
- Medical record → `MedicalRecordData(patient, provider, date, summary)` — owner-scoped to the patient, sensitivity=sensitive
- School form → `SchoolFormData(school, child, due_at, action_required)`
- Prescription → ...
- (open set; plugins add types)

Emits `artifact.classified` + `artifact.structured_extracted` with the typed payload. Values include `source_skill@version` for provenance.

#### `relationship_summarization`
Subscribes: periodic trigger (nightly) + `interactions.*` over a rolling 90-day window.

For each Party with recent interactions, generates a 3-sentence "who they are, how we know them, what's current" summary. Skill: `summarize_relationship@v2`. Writes to Party.attributes under a namespaced key. Versioned; can be re-derived.

#### `closeness_scoring`
Subscribes: periodic trigger.

Assigns each Party a tier (1=inner circle, 2=close, 3=regular, 4=acquaintance, 5=distant). Conservative, transparent factors: interaction frequency, mutual interactions, explicit relationship labels (spouse = tier 1 always), time since last interaction. Skill: `score_relationship_tier@v2`. Writes to Party.attributes.

#### `reminder_dispatch`
Subscribes: periodic (every 15 min).

Queries:
- Commitments due_at <= now + lead_time, status=pending
- Recurrences next_occurrence <= now + lead_time
- Tasks due_date <= today, status IN (inbox, next)

For each, emits `reminder.surfaceable` — picked up by surfaces (inbox pane, morning digest, optional outbound to member's preferred channel).

Observation-mode aware.

### AdministrateMe-specific CoS pipelines

#### `morning_digest`
Scheduled per member (default 06:30 local in that member's timezone). Gathers: today's calendar events (via `calendars` projection, respecting privacy), due commitments + tasks, due recurrences, overnight inbox count, streak status, reward stats. Runs `compose_digest@v3` skill with profile-appropriate format. Runs the **validation guard**: every claimed calendar event, every claimed commitment/task id is verified against projections post-composition. Any fabrication zeroes the message with sentinel "No morning brief available; underlying data changed."

Output: `adminme.digest.composed` event → surface delivery (outbound adapter in member's preferred channel, OR inbox display, per config).

#### `reward_dispatch`
Subscribes: `task.completed`, `commitment.completed`.

Reads member's profile → if `rewards.mode=variable_ratio`, samples tier per distribution; if `event_based`, uses tier=done; if `child_warmth`, uses child-appropriate template. Picks a template from persona's `reward_templates.yaml`. Emits `adminme.reward.dispatched` with tier + message. Surface: in-UI toast + optional Zeigarnik teaser 60s later.

#### `paralysis_detection`
Scheduled per ADHD-profile member (configurable; default 15:00 and 17:00 local).

Pre-condition: no completions in prior 2 hours, `adminme_energy_states.level <= low`, it's currently within the member's fog window.

**Deterministic.** Never invokes LLM. Picks a template from persona's `paralysis_templates.yaml`. Emits `adminme.paralysis.triggered` with template id + single-action framing. Surface: inbox + optional outbound.

#### `whatnow_ranking`
On-demand (via `/whatnow` slash command or API call).

Scoring function across tasks + commitments considering: energy match, effort available, location match, requires match, urgency, endowed-progress dots. Returns top K with reasoning (top 1 in fog window; top 5 otherwise). Per-profile behavior (carousel = 1, compressed = 5, power = 10). This is a pure scoring pipeline — no LLM calls; deterministic weights per profile config.

#### `scoreboard_projection`
Subscribes: `recurrences.triggered`, `task.completed` for trackable recurrences, daily rollover.

Maintains streak counters, completion rates, grace tokens per member. Fed to the scoreboard surface (wall displays, kid scoreboard view).

#### `custody_brief`
Scheduled 20:00 local daily (if household has a coparent Relationship).

Composes a brief summary of the day's child-related events relevant to custody coordination. Owner-scoped to the custodial parent. Skill: `compose_custody_brief@v1`. Output surfaces in the parent's inbox.

#### `crm_surface`
Scheduled weekly + on-demand.

For each active Party:
- Compute contact gap (days since last meaningful interaction)
- If gap > desired_contact_frequency (from Party.attributes) → emit `crm.gap_detected`
- Upcoming birthdays (next 14 days) → emit `crm.birthday_upcoming`
- Hosting balance asymmetry (we've hosted them N times; they've hosted us M times; |N-M| > threshold) → emit `crm.hosting_imbalance`

Surfaces in inbox.

#### `graph_miner`
Scheduled nightly 03:00 (runs on `adminme-vault` if present, else on hub).

Scans recent captures and interactions for implied entities and relationships:
- "Talked to Kate about the kitchen bid" → if Kate ambiguous, no-op; if resolvable, propose Interaction participant addition
- "Tom from Southern Pest is coming Thursday" → propose Party(Tom, Southern Pest) if not exists; propose calendar event
- "Dr. Dalton's office called about rescheduling" → propose Commitment(call back)
- Financial signals: "paid Jorge $800 for the cleaning job" → propose MoneyFlow

All outputs are proposals; none are auto-committed.

### Pipelines are pluggable

Each pipeline lives in `adminme/pipelines/<namespace>/<name>/`:
- `pipeline.yaml` (manifest: name, version, subscribes, triggers, required_skills, description)
- `handler.py` (the subscribe handler)
- `tests/`

Plugin pipelines via `adminme.pipelines` entry point.

---

## L4 CONTINUED: THE SKILL RUNNER (LAYERED ON OPENCLAW)

**AdministrateMe does not run its own LLM loop.** Every LLM call goes through **OpenClaw's skill runner**. AdministrateMe's "skill runner" module is a thin Python wrapper that (a) validates inputs against the skill's input schema, (b) invokes OpenClaw's skill execution API, (c) validates the response against the output schema, (d) records a `skill.call.recorded` event in the AdministrateMe event log with full provenance, and (e) returns the parsed output to the caller. OpenClaw owns the agent loop, prompt rendering, provider routing, retries, token accounting, and approval gates. AdministrateMe owns the event-sourced record of "this skill was called with these inputs and returned this output."

This is the seam between the two systems for LLM work. If AdministrateMe tries to manage its own LLM calls independently of OpenClaw, you end up with two session models, two cost ledgers, two approval systems — and the assistant's chat turns in iMessage produce different outputs than the same skill called from a pipeline. Always go through OpenClaw.

Every skill ships as an OpenClaw-format SKILL.md with AdministrateMe conventions layered on:

```
~/.adminme/packs/skills/<namespace>/<name>/
├── SKILL.md                   # OpenClaw-format: YAML frontmatter + prompt body
├── input.schema.json          # AdministrateMe convention; enforced by wrapper
├── output.schema.json         # AdministrateMe convention; enforced by wrapper
├── handler.py                 # optional: Python post-processing of LLM output
├── examples/
│   ├── input_01.json
│   ├── output_01.json
│   └── ...
└── tests/
```

**`SKILL.md` frontmatter** (OpenClaw-compatible, with AdministrateMe extensions):
```yaml
---
name: classify_thank_you_candidate
namespace: adminme
version: 3
description: Decide whether an interaction creates an unfulfilled thank-you obligation.
input_schema: ./input.schema.json
output_schema: ./output.schema.json
provider_preferences:
  - anthropic/claude-opus-4-7
  - anthropic/claude-sonnet-4-6
  - anthropic/claude-haiku-4-5
max_tokens: 800
temperature: 0.0
estimated_cost_per_call_usd: 0.004
sensitivity_required: normal         # 'normal' | 'sensitive'; skill refuses privileged inputs
context_scopes_required:
  - interactions:read
  - parties:read
timeout_seconds: 15
---
```

**Wrapper flow (AdministrateMe Python side):**
1. Caller invokes `await run_skill(skill_id, inputs, ctx)` with `ctx` carrying `correlation_id`, `session`, and `tenant_id`.
2. Validate `inputs` against `input.schema.json`.
3. Sensitivity check: if any input carries privileged content, refuse unless the skill declares `sensitivity_required: privileged` (few do).
4. Scope check: `context_scopes_required` must be a subset of the calling Session's allowed scopes.
5. Translate the skill manifest + caller inputs into an OpenClaw `llm-task` tool invocation. POST to OpenClaw's gateway HTTP API: `POST http://127.0.0.1:18789/tools/invoke` with body `{tool: "llm-task", action: "json", args: {prompt: <SKILL.md body>, input: <caller inputs>, schema: <output.schema.json>, provider, model, maxTokens, timeoutMs, ...}, sessionKey: <derived from dmScope>, dryRun: false}`. OpenClaw routes the call to the chosen provider, runs the LLM, validates structured output against the schema, and returns the parsed JSON. Provider-fallback iteration over `provider_preferences` happens in the wrapper, NOT in OpenClaw — OpenClaw picks one provider per `/tools/invoke` call. See `docs/reference/openclaw/tools/llm-task.md` for the tool's params and response shape; see `docs/adrs/0002-skill-runner-endpoint-correction.md` for the full translation contract.
6. If the skill pack includes a `handler.py`, call its `post_process(raw_response, inputs, ctx)` for Python-side shaping (regex extraction, JSON cleanup, enrichment from AdministrateMe projections).
7. Validate output against `output.schema.json`. On failure: log the raw response to `~/.adminme/raw_events/skill_validation_failures/`, return a defensive default per skill policy (usually a low-confidence negative).
8. Emit `skill.call.recorded` event with: skill name, version, `openclaw_invocation_id`, inputs (size-capped; large inputs saved to `~/.adminme/raw_events/`), outputs, provider, token counts, cost, duration, correlation_id.
9. Return the validated output to the caller.

**Every skill call is replayable.** Upgrade a skill → `adminme skill replay <skill_name> --since <ts>` → wrapper re-runs against all historical inputs through OpenClaw's skill runner → new `skill.call.recorded` events emitted with `causation_id` pointing to the old call. Projections that depended on the output re-derive from the new events. Both old and new outputs are in the event log; no data is lost; both are auditable.

**Provider routing is OpenClaw's job, not AdministrateMe's.** AdministrateMe's skill manifest expresses preferences via `provider_preferences` (list of provider-qualified model names in order); OpenClaw picks the first available. If every provider on the list is unavailable, OpenClaw returns an error and the wrapper fails the call — no silent fallback to an unpreferred provider, because the principal needs to know when a skill is running on the wrong model.

**Cost ledger.** All `skill.call.recorded` events aggregate into a daily/monthly LLM spend report in Settings → LLM usage. Cost comes from the response OpenClaw returns (OpenClaw in turn reads the provider's response headers). AdministrateMe does not estimate cost.

**AdministrateMe does NOT:**
- Talk directly to Anthropic / OpenAI / Ollama. OpenClaw is the only LLM client on the host.
- Manage LLM retries. OpenClaw does.
- Cache LLM responses. OpenClaw can; AdministrateMe sets per-skill cache policy in the skill manifest if caching is appropriate (usually it isn't — skills run on changing input).
- Handle approvals for LLM-triggered writes. OpenClaw's `exec-approvals` and AdministrateMe's `guardedWrite` compose: guardedWrite runs at the AdministrateMe API boundary; `exec-approvals` runs at OpenClaw's tool-execution boundary. A write-adjacent skill must pass both.


---

## L1: ADAPTERS

An adapter is a translator from an external source to typed events. Adapters:
- Publish events (never write projections, never call pipelines)
- Declare their **family** (messaging, calendaring, contacts, documents, telephony, financial, manual, iot, webhook)
- Declare their **capabilities** (poll, subscribe, send, fetch_artifact)
- Declare a **required_config_schema** (Pydantic) the bootstrap wizard renders as a form
- Handle their own **authentication** (OAuth2 flows, credential rotation, token refresh)
- Maintain a **cursor** per account so polling is incremental
- Declare their **sensitivity floor** (a privileged-email adapter enforces `sensitivity: privileged` on all events it emits; cannot be lowered)
- Emit **structured errors** as events (`adapter.error` with diagnostic payload) — never silently fail

**Three adapter runtimes.** Adapters live in one of three runtimes based on what they're translating from:

1. **Standalone Python processes** on the CoS Mac Mini (`adminme/adapters/<family>/<n>/`) supervised by the AdministrateMe adapter supervisor. These cover data sources: Gmail API, Plaid, CalDAV, Apple Reminders, Google Calendar, etc.

2. **OpenClaw plugins** — the channel adapters that OpenClaw already has first-class support for (iMessage via BlueBubbles, Telegram, Discord, web). For those channels, AdministrateMe ships an `openclaw-to-adminme` bridge plugin that translates OpenClaw's inbound message events into AdministrateMe `messaging.received` events. The bridge direction is: OpenClaw channel → OpenClaw plugin → AdministrateMe event log. The reverse direction for outbound messaging (AdministrateMe composes a draft → OpenClaw channel sends it) goes through OpenClaw's send API, not via adapter code.

3. **Bridge-side Python adapters** — running on member bridges (per §MEMBER BRIDGES), reading per-member personal knowledge. Built-in: Apple Notes (reads `NoteStore.sqlite` + AppleScript fallback), Voice Notes (watches Voice Memos folder), Obsidian (filesystem watcher on configured vault path). Connector packs add additional knowledge sources (Notion, Logseq, Roam, etc.). Bridge adapters emit `note.*` and `voice_note.*` events to the central `:3337 bridge` ingest endpoint over the tailnet — they do not write to the central event log directly and do not hold the AdministrateMe SQLCipher master key.

Decision rule for new adapters: **(a) if OpenClaw already has the channel, use OpenClaw + bridge plugin; (b) if the source is per-member personal knowledge on the member's own device, write a bridge-side adapter; (c) otherwise (data sources reachable from any member's identity — Gmail, Plaid, etc.), write a standalone Python adapter.**

```python
class Adapter(Protocol):
    metadata: AdapterMetadata

    async def health_check(self) -> HealthResult: ...
    async def authenticate(self, ctx: AuthContext) -> AuthResult: ...
    async def poll(self, since: Cursor) -> AsyncIterator[RawEvent]: ...
    async def subscribe(self) -> AsyncIterator[RawEvent] | None: ...
    async def send(self, action: OutboundAction) -> SendResult: ...
    async def fetch_artifact(self, ref: ArtifactRef) -> bytes: ...
```

Standalone Python adapters are loaded via `adminme.adapters` entry point → built-in adapters live in `adminme/adapters/<family>/<n>/`. OpenClaw-plugin adapters are registered via OpenClaw's plugin system and live in `adminme/openclaw-plugins/<n>/`.

### v1 adapter implementation status — BE EXPLICIT

**Hearth-specified, full implementation:**
- `messaging:imap` — generic IMAP + SMTP. Works against Gmail (app password or XOAUTH2), Fastmail, iCloud, Outlook.
- `messaging:gmail_api` — richer metadata than IMAP.
- `messaging:microsoft_graph` — Outlook/Exchange.
- `calendaring:caldav` — iCloud, Fastmail, Nextcloud.
- `calendaring:google_calendar_api`
- `calendaring:microsoft_graph_calendar`
- `calendaring:ics_subscription` — any webcal/https ICS URL, authenticated or not.
- `contacts:carddav`
- `contacts:google_people`
- `contacts:microsoft_graph_contacts`
- `contacts:vcard_file` — watches folder for .vcf drops.
- `documents:local_folder` — watchdog-based.
- `documents:google_drive`
- `telephony:ringcentral` — voicemail (with transcription via RingCentral AudioTranscription), SMS, call log.
- `telephony:twilio` — SMS, call log, voicemail.
- `manual:cli` — manual event entry via CLI.
- `manual:web_form` — web-form entry via console.
- `manual:email_to_self` — dedicated email address (configured per instance) becomes an ingest surface.
- `webhook:generic` — configurable inbound webhook with per-route schema validation.

**Hearth-specified, stubs with real interfaces (fail fast if invoked without availability):**
- `messaging:imessage_chatdb` — macOS Full Disk Access required; read-only from ~/Library/Messages/chat.db
- `messaging:matrix`, `messaging:signal` (signal-cli path), `messaging:whatsapp_business`, `messaging:telegram_bot`, `messaging:slack`, `messaging:discord`, `messaging:sms_twilio`
- `calendaring:exchange_ews`
- `documents:icloud_drive`, `documents:dropbox`, `documents:onedrive`, `documents:s3`, `documents:scanned_mail`
- `telephony:google_voice`
- `financial:plaid` — see dedicated section below
- `financial:statement_ocr` — pipes into documents pipeline
- `manual:voice_memo` — audio file → transcription skill → manual.cli
- `iot:homeassistant`

**AdministrateMe-specific additions (not in Hearth):**

**`messaging:bluebubbles_adminme`** — full implementation. HMAC-authenticated webhook from BlueBubbles Server running on the Mac Mini signed into the assistant's Apple ID. Handles iMessage + SMS (if BlueBubbles is bridging). Full send capability. This is the primary iMessage surface for AdministrateMe and takes precedence over `messaging:imessage_chatdb` (which becomes read-only backup).

**`messaging:apple_reminders`** — full implementation. Bidirectional via ClawHub `apple-reminders` skill (wraps EventKit) primary, `osascript` fallback. 
- **Inbound:** watches assistant's iCloud Reminders lists. `reminders-list-mapping.yaml` routes each list to a target (tasks, lists, captures). Reminder created externally → `reminder.created_externally` event → pipeline routes to `task.created`, `list_item.added`, or `capture.created` based on mapping.
- **Outbound:** when a task is created/updated, if its list is in the mapping, update corresponding Reminder. Observation-mode aware. Never-sync rules: lists with substrings "Work/Case/Client/Evolve"; items tagged `#private`; items owned by child or ambient members.
- **Conflicts:** `reminder_sync_map` projection tracks reminder UUID ↔ task_id mapping. Last-writer-wins with 5s debounce. Conflicts → `reminder.conflict` event → inbox with diff.

**`financial:plaid`** — full implementation (not stub). Replaces manual bank-CSV import as primary path:
- Uses Plaid products: Link (OAuth-style connect), Transactions (incremental sync via cursor), Balance (real-time), Identity (account owner → Party resolution), Investments (balance sheet), Liabilities (mortgages, credit cards).
- Sandbox-first at bootstrap. Production requires `adminme plaid go-live` + observation-off.
- Access tokens per institution stored in 1Password via `op://adminme-plaid/<institution_id>/access_token`.
- Incremental sync via cursor every 4 hours (live) or daily (observation).
- Emits: `financial.transaction_observed`, `financial.balance_updated`, `financial.account_discovered`, `financial.liability_updated`.
- Webhook handler at `/hooks/plaid` (Tailscale Funnel), HMAC-authenticated. Handles: `TRANSACTIONS:DEFAULT_UPDATE`, `ITEM:ERROR`, `ITEM:PENDING_EXPIRATION`.
- Owner detection: on account discovery, uses Plaid Identity → matches account holder name against Parties → links account to member; creates Party proposal if no match.

**`financial:bank_csv_watcher`** — full implementation (fallback). Watches `~/.adminme/inbox/bank/` for CSVs. Per-bank parser config. Emits same events as Plaid. Used when a bank isn't on Plaid or during Plaid outages.

**`financial:privacy_com`** — stub interface. Monitors Privacy.com virtual card charges; surfaces unexpected charges as TASKs in inbox.

**`manual:ios_shortcuts`** — full implementation. Configurable webhook endpoint (Tailscale-internal) accepting POSTs from iOS Shortcuts. Standard payloads: quick-capture text, voice-memo audio, location drop, photo drop. Emits `capture.created`, `artifact.discovered`, etc.

**`manual:siri_via_reminders`** — degenerates to `messaging:apple_reminders` — Siri-created Reminders flow through the Apple Reminders adapter's ingest path.

**Stubs are real code.** Each stub implements the Adapter protocol, registers metadata with `available: false` + reason, fails fast with `NotSupported` if invoked, and includes a preparedness check (e.g., `iMessage chatdb` stub checks for Full Disk Access on macOS and reports readiness in `hearth adapters status`).

---

## PLAID — DETAILED SPEC

Plaid is the primary financial data adapter. Enough detail to get this right matters because Plaid is the difference between "can track finances" and "can't."

### Initial connection (during bootstrap Section 6)

1. Bootstrap wizard prompts for Plaid `client_id` and secret. Sandbox by default.
2. Wizard creates a link token via `POST /link/token/create`:
   - `client_name: "AdministrateMe"`
   - `products: ["transactions", "auth", "identity", "investments", "liabilities"]`
   - `country_codes: ["US"]`
   - `language: "en"`
   - `user.client_user_id: <household_id>`
3. Wizard opens a local webview (or Safari to a local URL) hosting Plaid Link via the Plaid JS drop-in.
4. Tenant completes the Link flow; webhook receives `SUCCESS` with public token.
5. Wizard exchanges public token for access token via `POST /item/public_token/exchange`.
6. Access token stored in 1Password at `op://adminme-plaid/<institution_id>/access_token`. `instance/plaid.yaml` stores non-secret refs (institution name, product list, cursor state).
7. Initial sync triggered: fetch `/accounts/get` → emit `financial.account_discovered` per account; fetch `/identity/get` → emit `financial.account_identity_observed`.

### Ongoing sync

- Cron every 4h (live mode) or daily (observation mode):
  - For each institution, fetch `/transactions/sync` with stored cursor
  - Stage results in `plaid_transactions_staging` table
  - Emit one `financial.transaction_observed` event per new transaction
  - Update cursor
- Balance sync every 1h during live mode (for balance alerts)
- Investments + Liabilities weekly
- On Plaid webhook `TRANSACTIONS:DEFAULT_UPDATE`: trigger immediate sync for that institution

### Downstream pipelines

- `money_flows_projection` receives `financial.transaction_observed`, creates `money_flows` rows (subject to dedup against manual entries)
- `categorization_pipeline` uses Plaid's category taxonomy plus tenant-learned overrides → emits `financial.transaction_categorized`
- `xlsx_finance_projection` updates `adminme-finance.xlsx` Raw Data sheet on every categorized transaction
- `subscription_audit_pipeline` monthly detects dormant subscriptions (account active, no use in 90 days)
- `balance_sheet_projection` aggregates Plaid Balance + Liabilities + Investments into Balance Sheet xlsx sheet

### Observation mode

- Reads proceed normally (we need the data to learn).
- Writes to Plaid (if any — unlikely for v1) suppress.
- Downstream alerts (TASK creation, budget warnings, Comms outbound) suppress to `observation.suppressed` events.
- Tenant reviews via `adminme review` before flipping live.

### Safety

- Access tokens never in logs (regression test greps logs for token patterns)
- Token rotation: `adminme plaid rotate <institution_id>` → re-runs Link for that institution
- Webhook HMAC verification required; wrong signatures logged as `adapter.error` + rate-limited
- Rate limits per Plaid: configured per instance; 429 responses cause exponential backoff

---

## APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC

### Identity model

Reminders lives on the **assistant's iCloud account** (configured during bootstrap Section 5a). Family members share specific Reminders lists TO the assistant's Apple ID via native iCloud sharing. If a list is not shared, it is never synced. The assistant never has credentials into any family member's personal iCloud.

### Configuration

`~/.adminme/config/reminders-list-mapping.yaml`:

```yaml
lists:
  - reminders_list_name: "Family Grocery"
    target: list                     # 'list' | 'tasks' | 'captures'
    list_name: grocery
    completed_marks_checked: true
    auto_commit: true
    notes: Used by whole household; low-friction.
  - reminders_list_name: "Family Packing"
    target: list
    list_name: packing
    auto_commit: true
  - reminders_list_name: "James Tasks"
    target: tasks
    defaults:
      assignee_party: m-james
      status: inbox
      domain: tasks
      source_system: reminders
    auto_commit: false                # requires human confirm before promotion
  - reminders_list_name: "Laura Tasks"
    target: tasks
    defaults:
      assignee_party: m-laura
      status: inbox
      source_system: reminders
  - reminders_list_name: "Shared Household"
    target: tasks
    defaults:
      assignee_party: null            # null = shared
      status: inbox
      domain: home
      source_system: reminders

excluded_members: []                  # auto-populated from kid/ambient profiles

never_sync_patterns:
  list_name_substrings: ["Work", "Case", "Client", "Evolve"]
  item_tags: ["#private"]
  exclude_if_item_notes_contain: ["[privileged]"]
```

### Ingest (Reminders → events)

- ClawHub `apple-reminders` skill primary; `osascript` fallback
- EventKit push notifications + 30s polling safety net
- On new Reminder in a mapped list:
  - Emit `reminder.created_externally` event
  - Pipeline `reminders_router` consumes → emits `task.created` or `list_item.added` or `capture.created` per mapping
  - For mapped-to-tasks with auto_commit=false: promotion to active Task requires human confirmation in inbox
- On Reminder mutation (title, notes, due, completed state): emit `reminder.updated_externally`
- On Reminder deletion: emit `reminder.deleted_externally`; pipeline dismisses corresponding Task

### Outbound (events → Reminders)

- When Task created/updated, if its list mapping exists and `auto_commit`, update Reminder
- When Task status transitions to done → complete Reminder
- When Task dismissed → delete Reminder
- When Task deferred → leave Reminder alone (no state map)

### Sync map

```sql
CREATE TABLE reminder_sync_map (
  task_id          TEXT,
  list_item_id     TEXT,
  reminder_uuid    TEXT NOT NULL,
  reminders_list   TEXT NOT NULL,
  last_synced_at   TEXT NOT NULL,
  last_pib_hash    TEXT,
  last_reminder_hash TEXT,
  UNIQUE (reminder_uuid),
  CHECK (task_id IS NOT NULL OR list_item_id IS NOT NULL)
);
```

### Conflict resolution

- 5-second debounce window. If both sides change within debounce: both changes recorded as events, conflict resolver picks latest timestamp as winner, loser surfaces as `reminder.conflict` event → inbox with diff panel.

### Observation mode

- Reads proceed.
- Writes suppress: instead of updating Reminder, emit `observation.suppressed` with the intended action.
- Reconciliation runs after observation-off: replay suppressed events to catch up.

---

## L5: THE NODE CONSOLE SHELL

The Node console lives at `adminme/console/`. Port 3330. Express server serving `shell.html` and proxying to Python product APIs.

### What the console does
- Serves the compiled profile pack JSX views
- Resolves Tailscale identity, maps to Party via `party_tailscale_binding` projection
- Handles authMember/viewMember split
- Proxies `/api/core/*` → Python :3333, `/api/comms/*` → :3334, `/api/capture/*` → :3335, `/api/automation/*` → :3336
- Serves the sticky quick-capture bar (posts to `/api/capture/capture`)
- Renders observation-mode + degraded banners
- Loads persona.yaml → injects theme + persona name + signatures
- Loads member's profile → loads that profile's compiled views
- Handles rate limiting (per-source sliding window, config from governance.yaml)
- SSE chat endpoint
- Static assets (persona themes, profile assets)

### What the console does NOT do
- Business logic (all in Python products)
- Read event log directly (never)
- Read projection SQLite directly for *writes* — proxies to Python APIs
- May read projection SQLite for *read-only* UI rendering — performance optimization only, via `better-sqlite3` opened readonly

### Console patterns — see CONSOLE_PATTERNS.md

The Node/Express code for the platform-critical console patterns is specified in the companion file **CONSOLE_PATTERNS.md**. Read that file before writing any code in `adminme/console/`. The patterns it covers are:

- **Tailscale Serve identity resolution.** Primary auth via `Tailscale-User-Login` header. `X-ADMINME-Member` dev-mode header gated by `ADMINME_ENV=dev` AND loopback remote addr — both required. Full implementation in CONSOLE_PATTERNS.md §1.
- **`authMember` vs `viewMember` split.** Principals may pass `?view_as=<member_id>` or `x-adminme-view-as` header to see another member's data while retaining their own write permissions. Non-principals' view-as requests are ignored. Full implementation in CONSOLE_PATTERNS.md §2.
- **Readonly / writeable DB split.** Console's read connection is readonly. Writes proxy to Python.
- **`guardedWrite(action, writeFn)`.** Three-layer check: agent allowlist, governance action_gate, rate limit. Returns 403 on deny, 202 with pending_approval on confirm, proceeds on allow. Audit logs every attempt. Full implementation in CONSOLE_PATTERNS.md §3.
- **RateLimiter.** Sliding-window limiter. Sources: `web_chat`, `writes_per_minute`, per-endpoint categories. 429 responses include `retry_after`. Full implementation in CONSOLE_PATTERNS.md §4.
- **Calendar privacy filtering.** When `cal_classified_events.privacy='privileged'`: title → `title_redacted || "[busy]"`, description → null. When `='redacted'`: title → `"[unavailable]"`, description → null. Full implementation in CONSOLE_PATTERNS.md §6.
- **Captures / quick-capture bar.** Sticky input at top of every page posting to `/api/capture` with `{text, source}`. Natural-language prefix routing ("grocery:", "call:", "idea:", "recipe:"). See CONSOLE_REFERENCE.html for the exact visual + interaction.
- **Today-stream contract.** Returns `{stream, summary, activeIdx, energy, streak, whatNow}`. Chronologically-sorted mixed array of tasks + calendar items + commitments. `activeIdx` points to current/next.
- **Three view modes.** `carousel` (ADHD profile — one task at a time, large dots, reward toast), `compressed` (minimalist_parent — decision queue, no animations), `child` (kid_scoreboard — hidden sidebar, zero admin nav). HIDDEN_FOR_CHILD nav filter per CONSOLE_PATTERNS.md §7.
- **Reward toast.** `/api/tasks/:id/complete` returns `{reward_preview: {tier, message, sub}}` for immediate local display; canonical `reward.ready` event fans out via SSE for cross-tab. UI renders centered toast with tier-matched border + emoji (🎰 💎 🔥 ✓). Templates from persona's `reward_templates.yaml`. Full dual-path implementation in CONSOLE_PATTERNS.md §8.
- **SSE chat.** `/api/chat/stream` is Server-Sent Events. Session id format `sess-${Date.now()}`. Rate-limited under `web_chat`. Upstream relay with AbortController propagation. Full implementation in CONSOLE_PATTERNS.md §5.
- **TV scoreboard uses `requireTailscale`.** Any Tailscale device, mapped or not, renders the scoreboard. Role becomes `'device'`. Writes still gated.
- **`comms_channel_member_access`.** Per-member-per-channel access level + flags.
- **Degraded mode UX.** When backend unreachable, UI shows `degraded-banner` and falls back to `lastKnownData`. Two-TTL cache (fresh 60s, degraded 5min) with write queueing per CONSOLE_PATTERNS.md §9.
- **HTTP bridge to Python APIs.** Canonical `BridgeError` shape, tenant header auto-injection, correlation-ID propagation. Full implementation in CONSOLE_PATTERNS.md §10.
- **Observation mode enforcement.** Final-outbound-filter pattern — checked at the last step before an external call, not at policy layer. Full implementation in CONSOLE_PATTERNS.md §11.
- **Error handling + correlation IDs.** Allowlist-only error codes in client responses; full context in logs. Full implementation in CONSOLE_PATTERNS.md §12.

### What the console adds on top of these patterns

- **Profile view loader.** Member's profile → serves that profile's compiled `views/*.jsx` bundles from `~/.adminme/packs/profiles/<id>/compiled/`.
- **Persona theme loader.** Reads `~/.adminme/config/persona.yaml` at boot → renders theme CSS + substitutes name/emoji/signature throughout templates.
- **Observation-mode banner.** When `/api/core/observation-mode/status` returns `active: true`, top banner visible.
- **Plaid status pane** in settings. Institution list, sync state, relink, go-live.
- **Reminders sync status pane** in settings. Per-list status, pause/resume, conflicts.
- **Pack management UI** in settings. List/install/update/remove profile+persona+adapter+skill+pipeline packs.

### JSX compilation

Profile packs ship `views/*.jsx` source. At pack install time, `esbuild` compiles each to:
- Server-rendered string bundle (`compiled/<view>.ssr.js`) for initial HTML
- Client-side hydration bundle (`compiled/<view>.client.js`) for interactivity
- CSS extract (`compiled/<view>.css`)

Output goes to `~/.adminme/packs/profiles/<id>/compiled/`. The Node console serves these as static assets with appropriate caching. No runtime build server; no esbuild in the running process.

**Test:** every profile pack has a test that installs it into a fixture instance directory → verifies compiled bundles are produced → verifies the view renders with mock member data.


---

## L5 CONTINUED: PYTHON PRODUCT APIS

The four "products" are FastAPI services. Each on its own port. Each with its own router set, slash-command handlers, and scheduled jobs. **They share the event log and projections**; they do not have separate data stores. The split is about code organization and deployment cadence, not data ownership.

**Where slash commands and scheduled jobs actually live.** The slash commands listed per product below are **registered with OpenClaw** during product startup (each product, on boot, calls OpenClaw's slash-command registration API to attach its verbs). When a user types `/whatnow` in iMessage, OpenClaw's dispatcher looks up the registered handler, which hands off to the Core product's HTTP endpoint (via OpenClaw's tool/plugin invocation). The product returns a response, OpenClaw renders it back through the channel. Similarly, **scheduled jobs that represent proactive behaviors are registered as OpenClaw standing orders**; their handlers are product endpoints. APScheduler inside each product is used only for internal, non-user-facing schedules (cache refreshes, projection compaction, log rotation). Anything that might surface to a principal goes through OpenClaw's standing-orders system so it shares the approval, observation-mode, and rate-limit machinery.

### Product A: `core` — Chief of Staff (FastAPI at :3333)

**Owns the surfaces for:** Tasks, Commitments, Recurrences, Scoreboard, What-now, Rewards, Paralysis, Digests, Custody Brief, Calendar playbook, Emergency protocols.

**Routers:**
- `/api/core/tasks` — CRUD, status transitions, completion (triggers reward dispatch)
- `/api/core/commitments` — CRUD, status transitions
- `/api/core/recurrences` — CRUD, upcoming view
- `/api/core/whatnow` — on-demand whatnow ranking
- `/api/core/digest` — trigger/preview digest
- `/api/core/scoreboard` — streaks, trackable recurrences, completion rates
- `/api/core/energy` — member energy states
- `/api/core/today-stream` — unified today view
- `/api/core/observation-mode` — get/set
- `/api/core/emergency` — emergency playbook

**Skills used (grouped):**
- Task/commitment: `classify_item_nature@v2`, `extract_micro_script@v1`, `infer_due_date@v2`
- Digest: `compose_morning_digest@v3`, `compose_weekly_review@v2`
- Rewards: templates are deterministic; skill `compose_zeigarnik_teaser@v1` is optional per profile

**Slash commands:** `/whatnow`, `/digest`, `/bill`, `/remind`, `/done`, `/skip`, `/standing`, `/observation`

**Scheduled jobs (APScheduler):**
- `morning_digest` per member at member's configured time
- `paralysis_detection` 15:00 and 17:00 per ADHD-profile member (configurable)
- `reminder_dispatch` every 15 min
- `weekly_review` Sunday 16:00 per member (if enabled)
- `velocity_celebration` triggered by task.completed when daily count ≥ 5 (ADHD profiles)
- `overdue_nudge` daily 09:00, deferential, max 1/item/week
- `custody_brief` 20:00 if household has a coparent relationship
- `scoreboard_rollover` midnight local per member

### Product B: `comms` — Unified Communications (FastAPI at :3334)

**Owns:** Inbox aggregation across channels, propose/commit outbound, approval queue, per-member-per-channel access control, batch windows.

**Routers:**
- `/api/comms/inbox` — unified inbox view (across all adapters)
- `/api/comms/draft-queue` — pending drafts
- `/api/comms/approve` — approve a draft (emits approval event, triggers actual send)
- `/api/comms/send` — compose outbound (routes to appropriate adapter)
- `/api/comms/channels` — channel management
- `/api/comms/health` — channel health report
- `/api/comms/interactions/:party_id` — Interaction history with a Party

**Skills used:**
- `classify_message_nature@v2` (noise filter)
- `extract_participants@v2` (identity resolution support)
- `summarize_interaction@v3` (lazy; on-demand)
- `propose_commitment@v4` (commitment extraction)
- `draft_reply@v2` (outbound drafts)
- `draft_thank_you@v2` (thank-you notes)

**Slash commands:** `/inbox`, `/approve`, `/send`, `/snooze`, `/comms health`

**No scheduled jobs of its own**; all work is event-driven. Adapters poll on their own schedules and emit events; pipelines subscribe and react.

### Product C: `capture` — Knowledge Surfaces + CRM Surfaces (FastAPI at :3335)

**Owns:** Read surfaces over the personal knowledge layer (per-member knowledge view, household knowledge view), recipes, CRM surfacing (overdue contacts, birthdays, hosting balance), Party detail views, Place/Asset/Account views, and semantic + structured search across Interactions/Artifacts/Parties/Notes.

**Capture is a read surface, not an input pipeline (per D17).** Personal knowledge is captured by family members in their own knowledge tools — Apple Notes by default, Voice Notes, optionally Obsidian, optional third-party connector packs — and ingested via member bridges (see §MEMBER BRIDGES). Tasks, commitments, recurrences, and relationships flow through the existing reactive pipelines (`commitment_extraction`, `recurrence_extraction`, `relationship_summarization`), which have `note.*` and `voice_note.*` added to their subscription lists. There is no quick-capture prefix routing, no central voice-note ingest, no triage queue.

**Routers:**
- `/api/capture/knowledge` — current member's knowledge view: notes + voice-note transcripts, owner_scope=private:<member_id>, paginated
- `/api/capture/knowledge/household` — household knowledge view: shared:household scope only (member-private content excluded), paginated
- `/api/capture/recipes` — recipes view (a saved-query over notes tagged `recipe` and the structured-recipe artifact family)
- `/api/capture/parties` — CRM Party list + detail (the primary CRM UI)
- `/api/capture/parties/:id` — Party detail: interactions, commitments, tagged artifacts, relationships, relationship summary, closeness tier, contact gap, hosting balance, upcoming birthday/anniversary, linked assets/accounts/places
- `/api/capture/places` — Places view
- `/api/capture/assets` — Assets view
- `/api/capture/accounts` — Accounts view
- `/api/capture/search` — semantic + structured search over Interactions/Artifacts/Parties/Notes

**Skills used:**
- `summarize_relationship@v2`
- `score_relationship_tier@v2`

**Slash commands:** `/recipe`, `/party`, `/birthdays`, `/thank`, `/hosted`

**Scheduled jobs:**
- `relationship_summarization` nightly 02:00 over 90-day window
- `closeness_scoring` weekly Sunday 04:00
- `crm_surface` daily 09:00 + on-demand
- `graph_miner` nightly 03:00 (runs on adminme-vault if present)
- `recurrence_extraction` daily 04:00 across new artifacts, contact changes, and notes

### Product D: `automation` — Ambient Signal Layer (FastAPI at :3336)

**Owns:** Plaid integration surfaces, financial projections + dashboards, budget enforcement, subscription auditing, Home Assistant bridge, Privacy.com monitoring.

**Routers:**
- `/api/automation/plaid/institutions` — list, status, relink
- `/api/automation/plaid/sync` — force sync
- `/api/automation/plaid/go-live` — switch sandbox → production
- `/api/automation/money-flows` — MoneyFlow view (Raw Data equivalent)
- `/api/automation/budget` — current month budget vs actual
- `/api/automation/balance-sheet` — net worth snapshot
- `/api/automation/pro-forma` — 5-year projection
- `/api/automation/subscriptions` — subscription audit
- `/api/automation/household-status` — overall financial health glance
- `/api/automation/ha/*` — Home Assistant events (if enabled)

**Skills used:**
- `categorize_transaction@v3` (with learned tenant overrides)
- `extract_financial_facts@v2` (from receipts, invoices)
- `summarize_subscription@v1` (dormant-sub audits)
- `explain_anomaly@v1` (budget variance explanations)

**Slash commands:** `/budget`, `/worth`, `/forecast`, `/txn`, `/subs`, `/plaid`

**Scheduled jobs:**
- Plaid transactions sync every 4h (live) or daily (observation)
- Plaid balance sync every 1h (live)
- Plaid investments + liabilities weekly Sunday 05:00
- Categorization of uncategorized transactions nightly 04:30
- Subscription audit monthly 1st 08:00
- Budget pace check Mon/Thu 10:00
- Balance sheet rollup nightly 06:00

### Product E: `bridge` — Knowledge Ingest (FastAPI at :3337)

**Owns:** the inbound endpoint that receives owner-scoped knowledge events from member bridges (per §MEMBER BRIDGES and D17). Bridge is non-interactive — no slash commands, no scheduled jobs, no human-facing UI — just an authenticated ingest endpoint.

**Routers:**
- `/api/bridge/ingest` — single-event ingest. Body: `{event_type, schema_version, payload, occurred_at, correlation_id}`. Tailscale-User-Login header binds the inbound `owner_scope` to the bridge's assigned member; mismatch (e.g. a bridge tries to emit on another member's owner_scope) returns 403.
- `/api/bridge/ingest/batch` — batch ingest. Body: `{events: [...]}` up to N events per call (debounced bulk emit on a long-idle bridge that just woke up).
- `/api/bridge/health` — bridge → central health check; returns `{server_time, last_event_received_at}`. Polled by the bridge daemon every 30s.

**Skills used:** none. The bridge product is pure transport.

**Slash commands:** none.

**Scheduled jobs:** none. All work is bridge-driven.

**Authentication.** Tailscale identity at the tailnet edge. Each bridge has a tailnet identity (e.g. `james-bridge@<tailnet>`) bound at bridge enrollment to a `member_id`. The ingest endpoint reads the `Tailscale-User-Login` header, looks up the binding in `party_tailscale_binding`, and uses that `member_id` to derive the inbound `owner_scope`. The bridge cannot override `owner_scope` — bridges submit events; the central system assigns scope based on tailnet identity.

### Cross-product event flow (illustration)

A reply arrives in the assistant's Gmail:
1. `messaging:gmail_api` adapter (L1) polls → emits `messaging.received` event
2. Event log appends
3. Event bus publishes
4. Pipelines subscribed to `messaging.received` run concurrently:
   - `identity_resolution` — resolve sender email → Party
   - `noise_filtering` — classify as personal/transactional/professional/noise
   - `commitment_extraction` — if the message implies a commitment, emits `commitment.proposed`
5. `interactions` projection (L3) creates/updates Interaction row
6. `parties` projection updates last_seen_at on sender's Identifier
7. If commitment proposed, appears in inbox surface (L5) via comms API
8. Human approves via inbox UI → POST to `/api/comms/approve` → emits `commitment.confirmed`
9. `commitments` projection promotes proposal to active commitment
10. Tomorrow morning, `morning_digest` pipeline includes the commitment in James's digest
11. Reward toast fires when commitment marked complete → `task.completed` event → `reward_dispatch` pipeline → `adminme.reward.dispatched` event → surface toast

**At every step, if observation mode is on**, the outbound surface (digest delivery, toast display on another's screen, etc.) records `observation.suppressed` instead of firing.

**At every step, scope is enforced.** If the email arrived on Laura's privileged work account, `sensitivity: privileged`, `owner_scope: private:m-laura` — it never reaches the vector index, never reaches the graph miner, never appears in James's inbox view.

---

## THE CRM IS THE SPINE OF THIS SYSTEM

The CRM is not a sub-feature of the capture product. The CRM is the spine of the entire system. Every interaction, every commitment, every financial flow, every document, every appointment connects to a Party. The CRM's data model (Parties / Identifiers / Memberships / Relationships + Interactions + Commitments + Artifacts) IS the data model of AdministrateMe. Build it as such.

What the CRM gives you once it's properly built:

**Polymorphic talking-to.** You interact with organizations the same way you interact with people. "Call the pediatrician's office" and "Call Kate" are the same shape: find Party by name, find primary phone Identifier, dial. This is why Party is polymorphic on kind (`person` | `organization` | `household`).

**Household as first-class Party.** The Stice household is a Party. James, Laura, Charlie, baby are Parties with Memberships to the Stice household. This makes "what do we collectively owe" a clean query: Commitments where owed_by = household Party. Shared calendars, shared vendors, shared accounts all belong to the household Party.

**Proper identity resolution.** Kate's email is an Identifier linked to her Party. Her phone is another Identifier. Her iMessage handle is another. When Kate emails from a new address, identity_resolution proposes adding it to her Party rather than creating a new "Kate?" duplicate. Adapter-agnostic — doesn't matter whether her message came via Gmail or iMessage or SMS, the participant resolves to the same Party.

**Commitment tracking with provenance.** "You said you'd send Kate the plumber's number by Friday" is a Commitment with a source_interaction pointing to the email exchange that created it, a source_skill pointing to the extraction skill version, a confirmed_by pointing to you, and a due_at on Friday. It appears in your inbox daily until resolved. When resolved, it's resolved *with provenance* — why it existed, when it was confirmed, when it was completed.

**Relationship semantics.** "Kate is Laura's sister" is a Relationship. "Atlanta Water Authority supplies water to 761 E Morningside Dr NE" is a Relationship. "James is a coparent with Mike re: Charlie" is a Relationship. Queries like "who's family" or "which vendors serve this property" or "which people does Charlie have coparents" become direct graph queries, not inferences.

**Closeness tier + contact gap as surfacing mechanisms.** Every Party has a closeness tier (1-4) computed from interaction data. Every Party has a `desired_contact_frequency`. The `crm_surface` pipeline surfaces Parties whose contact gap exceeds their desired frequency — "you haven't meaningfully talked to Kate in 6 weeks; you'd intended to be weekly" — as inbox items. The intended cadence is tenant-editable; the pipeline is deterministic about reminding.

**Hosting balance.** Interactions tagged `hosting_us` (we went to their place) vs `hosting_them` (they came to ours) accumulate. Large asymmetries surface as "you've had the Kleins over four times; they haven't had you in return since 2024" — useful, not judgmental. Optional (toggle per tenant).

**Service provider history.** Every plumber, every handyman, every cleaner is a Party. Every invoice is an Artifact linked to them. Every MoneyFlow to/from them is tracked. The Party detail page shows total spend with them, service history, last contact, rating notes. When the water heater breaks at 2am, "who did we use last time?" is one query.

**Shared vendors across household members.** James and Laura use the same lawn service. The lawn service is one Party. Memberships let both of them "own" the relationship. Commitments route correctly (who's supposed to pay, who handles scheduling) via owner_scope.

**Why the CRM needs to be this load-bearing.** A household CoS whose CRM is shallow (just a contacts list, or just an inbox view of senders) has to keep reinventing identity and relationship tracking inside every other feature — commitments can't trace back to "who asked," calendars can't link to "whose vendor," finance can't link to "which service provider." When Party is first-class and every other projection links to it, a lot of features fall out for free. The household's specific CRM data — every friend, every doctor, every vendor, every school, every government-services entity — lives in that tenant's event log and projections, populated by the bootstrap wizard seed questions plus ongoing ingest from contacts/email/iMessage/calendar. The tenant never types a contact into a flat list; contacts accrue from the actual communication and are enriched by the identity-resolution pipeline.

---

## PROFILE PACKS — ADJUSTED FOR JSX + HTTP API

A profile is a reusable bundle of views + engines + tuning + prompts assigned to a member. Member profile switch → different views load, different engines run, different tuning applies. No code changes, no restart.

**Directory structure:**

```
profiles/<profile_id>/
├── profile.yaml                   # manifest
├── views/                         # JSX source (React 18 compatible)
│   ├── today.jsx                  # Today pane (Core)
│   ├── tasks.jsx                  # Tasks pane
│   ├── schedule.jsx               # Calendar pane
│   ├── inbox.jsx                  # Comms Stream inbox
│   ├── capture.jsx                # Capture pane
│   ├── parties.jsx                # CRM list
│   ├── party_detail.jsx           # CRM Party detail
│   ├── scoreboard.jsx
│   ├── chat.jsx
│   ├── settings.jsx
│   └── nav.yaml                   # nav items, order, which show for this profile
├── engines/
│   ├── rewards.yaml               # reward dispatch config
│   ├── paralysis.yaml             # fog-window detection + template selection
│   ├── digest.yaml                # morning/weekly digest format
│   ├── whatnow.yaml               # task ranking knobs
│   ├── filters.yaml               # guilt filter on/off + other content filters
│   └── surfaces.yaml              # which surfaces active for this profile
├── tuning.yaml                    # numeric/boolean knobs (schema in profile.yaml)
├── prompts/
│   ├── system_additions.md        # appended to agent system prompt for this member
│   └── voice_notes.md             # how assistant talks TO this member
├── assets/
│   ├── colors.css                 # optional overrides (rare)
│   └── icons/
├── tests/                         # profile-level regressions
└── README.md
```

**JSX build at install time:** when a profile pack is installed, `adminme pack install` runs `esbuild` against `views/*.jsx` producing compiled bundles in `~/.adminme/packs/profiles/<id>/compiled/`. No runtime build server.

**JSX authoring contract:**
- React 18 functional components only
- Props typed via `.d.ts` provided by the platform (`@adminme/profile-types`)
- Components receive `{ member, persona, data, api }` where:
  - `member` is the Party + effective profile tuning
  - `persona` is the instance persona
  - `data` is a typed snapshot (tasks, commitments, today-stream, etc.) fetched from the product APIs
  - `api` is a scoped HTTP client calling the Python product APIs
- Components do NOT directly fetch; the shell pre-fetches `data` before render. Interactive updates go through `api`.
- No `<form>` tags (React artifact convention). Use onClick/onChange handlers.

**Profile manifest (`pack.yaml`):** full schema and a worked example (the `adhd_executive` profile) are in REFERENCE_EXAMPLES.md §6. Key fields: `runtime` (language, entrypoint), `views` (JSX paths), `reward_distribution` (done/warm/delight/jackpot probabilities), `pipelines` (on/off + per-profile tuning), `nudge_caps`, `text_filters` (guilt_filter etc.), `skills_overrides` (per-profile skill parameter overrides), `view_config`, `tests`. `min_platform` is a semver; incompatible profiles fail install. `visible_to_roles` restricts which members the profile can be assigned to.

### The 5 built-in profiles

**1. `adhd_executive`** — for members with ADHD-PI. Variable-ratio rewards 60/25/10/5 (done/warm/delight/jackpot), fog-window paralysis 15-17 local, carousel today view, endowed progress dots, Zeigarnik teaser enabled, guilt filter on, velocity cap 15, morning digest format=fog_aware.

**2. `minimalist_parent`** — compressed view, event_based rewards, no paralysis, max_daily_messages 2, velocity_cap 8, guilt filter on, morning=compressed, afternoon=none.

**3. `power_user`** — dense dashboard, all engines configurable, velocity_cap 25 default, all filters tunable.

**4. `kid_scoreboard`** — star-driven chore scoreboard, HIDDEN_FOR_CHILD nav filter (only today view), child_warmth rewards, no comms/captures/outbound, age tuning knob.

**5. `ambient_entity`** — data-model only. No surfaces. For babies, toddlers, expected arrivals, aged-out elders.

### Profile lifecycle

- **Switch.** Settings → Profile dropdown → select new → confirm diff (you'll gain X, lose Y) → saves new profile on member, tuning defaults from new schema replace old tuning where schemas don't overlap. Emits `profile.switched` event. Next page load uses new views + engines.
- **Fork.** `adminme profile fork adhd_executive my_variant` copies to instance's local profiles dir for tenant editing. Forks don't auto-update; `adminme profile rebase my_variant` pulls upstream changes interactively.
- **Publish.** `adminme profile publish my_variant` → GitHub repo, local tarball, or registry PR.
- **Install from registry.** `adminme pack install profile:<id>` → fetch manifest → validate → show diff → install → run `esbuild` on views → register.

---

## PERSONA PACKS

Persona = agent identity. One per instance. The active persona's voice and identity materialize as the **SOUL.md** that OpenClaw loads — every chat turn in iMessage, Telegram, Discord, or the console chat pane is rendered with SOUL.md in context. Changing the active persona recompiles SOUL.md, restarts OpenClaw, and every subsequent conversation reflects the new identity.

**Directory structure:**

```
~/.adminme/packs/personas/<persona_id>/
├── pack.yaml                   # manifest: name, emoji, handle, voice_preset_ref, theme, reward_style, guardrails
├── voice.md                    # voice guide — source material for SOUL.md
├── reward_templates.yaml       # tier-matched phrase pools (done/warm/delight/jackpot/kid_warm/minimal)
├── paralysis_templates.yaml    # deterministic single-action templates
├── digest_templates.yaml       # morning + weekly digest framing
├── signature.yaml              # outbound signatures per channel
├── theme.css                   # color palette, fonts — consumed by the Node console
├── boundaries.md               # what this persona will never do (medical advice, prestige framing, etc.)
├── tests/
│   └── fixtures/               # voice-guideline checks (see REFERENCE_EXAMPLES.md §7)
└── compiled/
    └── SOUL.md                 # generated at persona-activation time; loaded by OpenClaw
```

**SOUL.md compilation.** When a persona is activated (`adminme persona activate <id>`), the activation step concatenates voice.md + the persona metadata + boundaries.md + a summary of the reward tier styles into a single SOUL.md file in the persona's `compiled/` directory, then points OpenClaw's workspace config (`~/Chief/.openclaw/soul.md` or equivalent per OpenClaw's convention — verify in the docs) at that file and restarts the OpenClaw gateway. The compiled SOUL.md is always derivable from the source files; never edit it directly.

**What lives in the persona pack vs. what lives elsewhere:**

- Persona pack: **identity, voice, theme, reward/paralysis/digest template pools, channel signatures, voice-guideline tests.**
- Profile pack: **per-member UX tuning** (view mode, reward distribution, pipeline toggles, nudge caps, text filters).
- Governance config: **rate limits, action gates, approval requirements** — these are instance-wide, not persona-specific, because changing persona shouldn't change what actions are allowed.
- Authority config: **who can act as principal, view-as targets, role assignments** — also instance-wide.

### 4 built-in personas

- `poopsy` — warm_decisive, warm cream + blush palette, corny_disproportionate rewards (Stice exemplar; see REFERENCE_EXAMPLES.md §7 for the full pack)
- `butler_classic` — precise_formal, monochrome, formal rewards
- `friendly_robot` — playful_casual, blue, minimal rewards
- `quiet_assistant` — quiet_minimal, slate, formal rewards

Persona boundaries: provides voice/identity/theme/templates/guardrails. Does NOT provide views, engines, authority rules, governance, or LLM model selection (that's skills + profiles). Changing persona is a full-instance operation that restarts OpenClaw.

---

## PACK REGISTRY

v1: public GitHub repo at `github.com/adminme/registry` (or similar). Structure:

```
registry/
├── packs.yaml                   # master index
├── profiles/
│   ├── adhd_executive.yaml      # metadata: id, versions, url, author, license, description
│   ├── minimalist_parent.yaml
│   └── community/
├── personas/
├── adapters/
├── pipelines/
├── skills/
└── themes/
```

Metadata points to git URLs or tarballs. Registry is index-only; packs decentralized.

**`adminme pack` CLI:**
- `list` | `search <query>` | `info <id>` | `install <id|url|path>` | `update [id|--all]` | `remove <id>` | `publish <path>`

Install flow: fetch manifest → validate schema → check compatibility + dependencies → preview permissions + surfaces → confirm → clone/download → run sandboxed `post_install.py` → compile JSX (profiles) → register → log `pack.installed`.


---

## MACHINE TOPOLOGY & NETWORK

Unchanged from prior design; preserved here for completeness.

**adminme-hub** — Mac Mini, always-on. macOS user `adminme` (FileVault on). 
- `~/.adminme/`:
  - `VERSION`
  - `config/` (YAML configs, git-versioned)
  - `data/`:
    - `events.db` (SQLCipher, source of truth)
    - `projections.db` (SQLite, derived)
    - `raw_events/<yyyy>/<mm>/` (zstd-compressed oversized payloads)
    - `artifacts/<yyyy>/<mm>/` (binary blobs, encrypted)
  - `projections/`:
    - `adminme-ops.xlsx` (bidirectional projection)
    - `adminme-finance.xlsx` (bidirectional projection)
  - `packs/`:
    - `profiles/<id>/{manifest, source, compiled}`
    - `personas/<id>/`
    - `adapters/<id>/`
    - `pipelines/<id>/`
    - `skills/<id>/`
  - `secrets/.env` (rendered from 1Password, chmod 600)
  - `logs/`
  - `backups/` (local git + B2 mirror)
  - `cache/`
- `/Applications/BlueBubbles Server.app` signed into assistant's Apple ID
- LaunchAgents:
  - `ai.openclaw.gateway.plist` (OpenClaw gateway)
  - `com.adminme.core.plist` (Python :3333)
  - `com.adminme.comms.plist` (Python :3334)
  - `com.adminme.capture.plist` (Python :3335)
  - `com.adminme.automation.plist` (Python :3336)
  - `com.adminme.console.plist` (Node :3330)
  - `com.adminme.event-bus.plist` (background event dispatcher)
  - `com.adminme.projections.plist` (projection maintainer daemon)
  - `com.adminme.xlsx-sync.plist` (bidirectional xlsx projection watcher)
  - `com.adminme.reminders-sync.plist` (Apple Reminders bidirectional)
  - `com.adminme.pipelines.plist` (pipeline runtime)
  - `com.adminme.scheduler.plist` (APScheduler)
  - `com.adminme.observation-monitor.plist` (monitors the flag)
- Tailscale:
  - Tailnet-only: 3330 (console), 18789 (OpenClaw gateway)
  - Funnel: `/hooks/gmail`, `/hooks/plaid`, `/hooks/bluebubbles` (only these, nothing else public)
  - Machine name: `adminme-hub`

**adminme-vault** — optional Linux VPS or Pi 5. Nightly backups, analytics, read replica. Not required for v1.

**adminme-edge** — wall displays. Tailscale-authenticated, `role: device`, render scoreboard only.

**Network rules:**
- Tailscale mesh is the trust perimeter
- Tailscale Serve terminates TLS, injects Tailscale-User-Login header
- Funnel is the only public path, narrow to 3 webhook endpoints
- Default-deny egress via pf or LittleSnitch — allowlist the services in use
- No shared passwords; identity = Tailscale mapping to Party

**Agent roster** (OpenClaw multi-agent):
| agentId | Purpose | Model | Sandbox |
|---|---|---|---|
| `<persona.handle>` | Main CoS | claude-opus-4-7 | off |
| `<persona.handle>-family` | Family group chat, kid-safe | claude-sonnet-4-6 | mode:all, ro |
| `<persona.handle>-ops` | Primary adult's technical channel | claude-opus-4-7 | off |
| `<persona.handle>-house` | Smart home + utilities | claude-sonnet-4-6 | mode:non-main |
| `<persona.handle>-money` | Finance deep dives | claude-opus-4-7 | mode:all, ro |
| `<persona.handle>-travel` | Travel planning | claude-sonnet-4-6 | off |
| `<persona.handle>-health` | Health coordination, privacy-first | claude-opus-4-7 | mode:all, ro |
| `<persona.handle>-kids` | Child-scoped | claude-sonnet-4-6 | mode:all, none |

Cross-agent memory: family/house/money/travel/health get ro access to main; `-kids` gets none.
Coach-role sessions strip financial+health context via `before_prompt_build` hook.
Coparent phones: `allowFrom` but no agent binding; messages → inbox.
Session scope: `dmScope: per-channel-peer` (not main) — multi-principal isolation.

---

## MEMBER BRIDGES

Personal knowledge — notes, voice notes, optionally Obsidian vaults — is captured by each family member in their own tools on their own device. AdministrateMe ingests this knowledge via **member bridges**: a Mac Mini per Apple-using family member, physically present on the shelf next to the central CoS Mac Mini, signed into that member's iCloud account, running an `adminme-bridge` daemon. This is the physical-layer reinforcement of identity-first privacy ([§6.12]) and the architecture of D17.

**Why bridges, not central ingestion.** Apple Notes has no public API; reading a member's notes requires their iCloud signin on a Mac with Full Disk Access. A central CoS Mac Mini signed into multiple members' iCloud accounts would be a single-point-of-failure for household privacy. The bridge model puts each member's iCloud key material on a physically distinct machine, with no cross-member knowledge access path.

**Physical arrangement.** All member bridges + the CoS Mac Mini sit on the same shelf at the household site, on the household tailnet, on the same power. Family-member personal Macs (separate from bridges) remain ordinary tailnet endpoints, used by their member for their normal computing. Bridges are dedicated AdministrateMe infrastructure; they have no role in the member's personal computing.

**What the bridge runs:**

- `adminme-bridge` daemon (`~/.adminme-bridge/`) — the supervisor process. Manages the adapters, the `:3337 bridge` ingest connection, and bridge-side state (cursors, last-seen ids).
- **Apple Notes adapter** — reads the local `NoteStore.sqlite` database (bulk reads + initial backfill) and falls back to AppleScript for live edit detection on changed notes. Emits `note.added@v1` / `note.updated@v1` / `note.deleted@v1` events. The exact read mechanism (SQLite direct vs AppleScript vs hybrid) is finalized at prompt 11c orientation; recommendation per memo §1.3 is hybrid.
- **Voice Notes adapter** — watches the Voice Memos recordings folder (`~/Library/Application Support/com.apple.voicememos/Recordings/`); emits `voice_note.added@v1` events with the audio artifact reference. Transcription is central-side (per memo §8) — the bridge uploads the audio artifact to the central artifact store and emits the event; the central skill runner transcribes via the existing skill-call provenance path.
- **Obsidian adapter** — filesystem watcher on a configured vault path; emits `note.added@v1` events with `source_kind=obsidian`. Built-in but only active if the member has configured a vault path. **Excluded from kid-bridge variant.**
- **Connector-pack slot** — the extension point for additional knowledge-source adapters (Notion, Logseq, Roam, etc.) shipped as packs. Connector packs install on bridges as `kind: adapter, subkind: knowledge-source`. The exact pack interface is finalized at prompt 11c refactor time.
- **Tailscale client** — the bridge is a tailnet device with its own identity (e.g. `james-bridge@<tailnet>`). Tailscale identity is what binds the bridge's emissions to the member's `owner_scope` at the central ingest endpoint.

**What the bridge does NOT run:**

- No event log. Events are emitted over the tailnet to the central CoS Mac Mini's `:3337 bridge` ingest endpoint. The bridge does not hold the AdministrateMe SQLCipher master key.
- No projections.
- No console.
- No pipelines, no skill runner, no OpenClaw.
- **No iCloud credentials for any other member.** James's bridge has James's iCloud, only James's. **Hard.** A bridge configured with multiple members' iCloud signins is a misconfiguration and is detected at bootstrap (per [§6.19]).

**Kid-bridge variant.** A bridge assigned to a child member runs a restricted adapter set: Apple Notes + Voice Notes only. Obsidian is excluded. Cross-member knowledge graph derivation that touches kid-owned events is sandboxed per the Conception-C amendment §2.5. Otherwise the bridge model is identical — kid-bridges are first-class bridges; they emit to the same `:3337 bridge` ingest endpoint, with `owner_scope=private:<kid_member_id>`.

**Bridge enrollment.** During bootstrap §10 (per §BOOTSTRAP WIZARD), the central wizard generates an enrollment package per bridge — a tarball containing the per-member identity, the tailnet auth key, the adapter set, and the bridge bootstrap mini-wizard. The operator copies the package to each bridge Mac Mini (rsync over the tailnet, or sneakernet on initial setup). The mini-wizard runs on the bridge: verifies macOS version, iCloud signin (must be the right member's account), Tailscale auth, Apple Notes accessibility (Full Disk Access permission); installs the bridge daemon under `launchd`; configures which adapters are active (Apple Notes always; Voice Notes by default; Obsidian if a vault path is configured); tests the `:3337 bridge` ingest endpoint roundtrip (submits a `bridge.enrollment_completed` event); hands control back to the central wizard. Each successful enrollment emits `bridge.enrolled {member_id, bridge_node_id, adapters_active}`.

**Bridge daemon code.** Bridge daemon code lives in `bridge/` at the repo root (parallel to `adminme/`), NOT in `adminme/adapters/` — bridges are a separate machine and a separate runtime. Per PM-29: future prompts that add knowledge-source adapters land in `bridge/`, not `adminme/`. The bridge daemon and the central system share event-schema models via editable install or vendored copy (UT-15, decided at 11c orientation).

**The five new event schemas** (registered at v1; finalized in prompt 11c):

- `note.added@v1` — payload: `note_id` (bridge-source-stable), `source_kind` (`apple_notes`/`obsidian`/`<connector-pack>`), `title`, `body`, `tags`, `created_at`, `modified_at`, `folder_path`.
- `note.updated@v1` — same payload + `prior_modified_at`.
- `note.deleted@v1` — payload: `note_id`, `deleted_at`.
- `voice_note.added@v1` — payload: `voice_note_id`, `source_kind` (`apple_voice_memos`), `audio_artifact_ref`, `duration_seconds`, `recorded_at`. Transcript becomes available later via `skill.call.recorded` once central-side transcription completes.
- `bridge.enrollment_completed@v1` — payload: `bridge_node_id`, `member_id`, `adapters_active`, `bridge_daemon_version`.

All five register at v1 per [§7]/[D7]. Migrations and upcasters land in prompt 11c.

**The bridge is the privacy boundary.** Cross-member knowledge access happens only through projection queries on the central CoS Mac Mini (read-time scope predicates, identity-first privacy, privileged-content exclusion from `vector_search` per [§6.9]). Bridge-to-bridge access is forbidden by [§6.19] and is not implementable: bridges cannot reach each other's iCloud accounts, and the bridge daemon does not expose any inbound HTTP surface beyond its own loopback management interface.

---

## AUTHORITY, OBSERVATION, GOVERNANCE

### `config/authority.yaml`

```yaml
# Who can do what, to whom, about what.
# Every outbound action, every sensitive read, every proactive surfacing
# passes through authority.can(action, actor, target, context).

actors:
  - <persona.handle>                  # the assistant itself
  - <persona.handle>-family
  - <persona.handle>-kids
  - coach                             # coach-role sessions
  - adminme.pipelines.*               # deterministic pipelines

actions:
  communication.send:
    <persona.handle>:
      to_principals: allow
      to_household_vendors: allow
      to_friends_close_tier: allow
      to_friends_distant_tier: confirm
      to_opposing_counsel: deny
      to_unknown: confirm
    <persona.handle>-family:
      to_family_group: allow
      to_other: deny

  financial.write_assumption:
    <persona.handle>: deny            # assumption changes require explicit approval_token
    principal:
      with_approval_token: allow

  financial.trigger_alert:
    adminme.pipelines.budget: allow
    adminme.pipelines.plaid: allow

  data.read_privileged:
    <persona.handle>-health: deny     # privileged data is owner-only
    <persona.handle>-kids: deny
    coach: deny
    principal_of_owner: allow

  projections.rebuild:
    principal: confirm
    <persona.handle>: deny

rate_limits:
  proactive_per_member_per_day:
    adhd_executive_profile: 15
    minimalist_parent_profile: 3
    power_user_profile: 25
    kid_scoreboard_profile: 0
  writes_per_minute: 60
  skill_calls_per_hour: 200

never:
  # Hard refusals, not overridable by authority rules
  - send_to: opposing_counsel
  - reference_in_outbound: privileged_medical
  - reference_in_outbound: privileged_legal
  - describe_as_prestigious: [schools, clubs, institutions]
  - auto_send_as: principal                   # Mode 3 prohibited
  - auto_answer: unknown_sender_coparent      # always inbox
```

### `config/governance.yaml` — console gates

```yaml
action_gates:
  task.create: allow
  task.complete: allow
  task.delete: confirm
  commitment.confirm: allow
  commitment.cancel: confirm
  commitment.delegate: confirm
  outbound.send: (varies; see authority.communication.send)
  plaid.go_live: confirm
  observation.off: confirm
  projections.rebuild: confirm

rate_limits:                          # per-source sliding window on the Node console
  web_chat:
    window_sec: 60
    max_calls: 20
  writes_per_minute:
    window_sec: 60
    max_calls: 60

privacy_filters:
  calendar_privileged_display: busy   # 'busy' or 'redacted'
```

### Observation mode

- Env var: `ADMINME_OBSERVATION_MODE` (default `true` at bootstrap)
- Runtime override: `observation_mode_override` in `config/runtime.yaml` (set via `adminme observation on|off`)
- Checked by `lib/observation_mode.is_active()`
- Every outbound adapter, every proactive pipeline, every send endpoint checks this before firing
- Suppression emits `observation.suppressed` event with full diagnostic payload (what would have fired, to whom, via what)
- `adminme review` TUI shows all `observation.suppressed` events since last review, categorized, with approve/dismiss actions
- Recommended review cadence: daily for the first week

### Privileged-access log

Every read of a `sensitivity=privileged` record by anything other than its owner is logged:
- Actor identity (which agent, which principal, which pipeline)
- Target event/projection row
- Call stack
- Timestamp

Surfaces in `adminme audit privileged-access` — the tenant can verify no cross-contamination.

---

## BOOTSTRAP WIZARD

The bootstrap wizard takes a fresh machine from zero to a running AdministrateMe instance. Nine sections, resumable, idempotent. DIAGRAMS.md §10 has the state machine; here are the section-by-section contents:

**Section 1: Environment preflight.** macOS version, user, FileVault on, Tailscale auth, Node 22+, Python 3.11+, **OpenClaw gateway installed and reachable on :18789** (install via `https://docs.openclaw.ai/install` if missing — wizard offers to run the installer), **OpenClaw workspace created at `~/Chief`** (initialized if missing), Homebrew, git, gh, rclone, LibreOffice (for xlsx formula recalc), 1Password CLI. Exit with clear message on any failure. On OpenClaw missing: wizard offers to bootstrap OpenClaw first — if the tenant accepts, the installer runs and the wizard rechecks; if the tenant declines, wizard exits.

**Section 2: Name your assistant.** Name + emoji + voice preset (warm_decisive / precise_formal / playful_casual / quiet_minimal / custom) + reward style (corny_disproportionate / minimal / formal / kid_warm) + color palette (4 presets + custom). Writes `config/persona.yaml`, compiles the `SOUL.md` for OpenClaw from the selected persona pack (writing to `~/Chief/.openclaw/soul.md` or OpenClaw's equivalent — verify path in the OpenClaw docs), and triggers an OpenClaw reload so the assistant identity is live immediately.

**Section 3: Household composition.** Household name + address + timezone. Adults (principals). Children. Expected arrivals. Coparents. Household helpers. Writes members to event log via `party.created` + `membership.added` + `relationship.added` events; `parties` projection builds from these. Each principal is also registered as an OpenClaw node via `openclaw node add <device>` during section 8 (channel pairing); the principal-to-node mapping is stored in `config/members.yaml`.

**Section 4: Assign profiles.** Dropdown per adult (adhd_executive / minimalist_parent / power_user / custom). Dropdown per child (kid_scoreboard / ambient_entity). Tuning stays at defaults; tenant tunes later.

**Section 5: Assistant credentials.** Walks through Apple ID for assistant, phone number for assistant (Mint Mobile recommended), Google Workspace email for assistant, 1Password service account token, Anthropic API key (provided to OpenClaw, not consumed by AdministrateMe directly), OpenAI (optional, for OpenClaw), Tailscale auth key, Twilio (optional), BlueBubbles (during Section 8), Telegram bot token (optional), Discord bot token (optional), Tavily or Brave Search API key, ElevenLabs (optional), Deepgram (optional), Privacy.com (optional), Lob (optional), Home Assistant token (optional), Backblaze B2, GitHub remote URL. Every credential tested. LLM provider credentials are written to OpenClaw's secret store via `openclaw secrets set`. Skippable credentials addable later via `adminme credentials add`.

**Section 6: Plaid.** Plaid account setup, client_id + sandbox secret, initial Link flow for first institution. Safe in sandbox — no real money moves.

**Section 7: Seed household data.** Address, secondary properties, vehicles, mortgage, recurring bills, healthcare providers, schools, active projects, vendors, friends & family (CRM). Each answer maps to specific events (`party.created`, `place.added`, `asset.added`, `account.added`, `recurrence.added`, `relationship.added`, `task.created` for project goals). **Skipped sections create inbox tasks** prompting completion later.

**Section 8: Channel pairing.** Interactive pairing per selected channel. Each paired channel is registered with OpenClaw via its channel API (see OpenClaw's `channels/pairing` docs). For iMessage: verify assistant's Apple ID signin on the Mac Mini, install BlueBubbles, register the BlueBubbles channel with OpenClaw, test send. For Telegram/Discord: create bot, exchange tokens, register with OpenClaw. For Apple Reminders: list iCloud lists (standalone Python adapter, not OpenClaw), configure mapping, test bidirectional. For Gmail: OAuth flow with assistant's Workspace, PubSub setup, Funnel endpoint (standalone Python adapter). This section also installs AdministrateMe's skill packs + plugins into OpenClaw (`openclaw skill install <path>`, `openclaw plugin install <path>` for each) and registers AdministrateMe's slash commands and standing orders via OpenClaw's CLI.

**Section 9: Observation briefing.** Explain observation mode, how to review, how to flip live. First outbound message sent (to primary adult via their preferred channel — routed through OpenClaw, suppressed if observation mode is on, which it is by default so this first message actually shows as a suppression event the tenant reviews in Settings → Observation).

**Section 10: Bridge enrollment.** Per §MEMBER BRIDGES. The central wizard generates an enrollment package per member bridge — a tarball containing the per-member identity, the tailnet auth key, the adapter set (Apple Notes + Voice Notes always; Obsidian if the member configures a vault path; kid-bridge variant excludes Obsidian per [§6.19]), and the bridge bootstrap mini-wizard. The operator copies this package to each bridge Mac Mini (rsync over the tailnet, or sneakernet on initial setup). The bridge bootstrap mini-wizard runs on the bridge: verifies macOS + iCloud signin (must be the assigned member's account) + Tailscale auth + Apple Notes Full Disk Access; installs the bridge daemon under `launchd`; configures active adapters; submits a `bridge.enrollment_completed` event to the central `:3337 bridge` ingest endpoint; hands control back to the central wizard. Each successful enrollment emits `bridge.enrolled {member_id, bridge_node_id, adapters_active}`. **§10 is required for any household with at least one Apple-using member.** A household with no Apple-using members has no §10. Households running OpenClaw-based knowledge sources only (no Apple ID, no Voice Notes, no Obsidian) skip §10 entirely.

**Implementation:** Textual/Rich TUI. Idempotent; resumable via encrypted `~/.adminme/bootstrap-answers.yaml.enc`. Bootstrap report at `~/.adminme/bootstrap-report.md`.

---

## SAMPLE INSTANCE — WHAT `~/.adminme/` LOOKS LIKE AFTER BOOTSTRAP

This section shows the concrete end-state a successful bootstrap produces. Claude Code: use this as a target for end-to-end integration tests and as a sanity check that all the phases wire together correctly. Everything below is a populated instance; the platform code and the bootstrap wizard produce this from the tenant's answers.

**The example tenant is the Stice household.** (Same rule as the companion artifacts: instance-specific data lives in instance files, not in platform code. The bootstrap wizard is the mechanism that produces these files from answers; the wizard itself is tenant-agnostic.)

### Directory tree after bootstrap

```
~/.adminme/
├── bootstrap-answers.yaml.enc               # encrypted; source of truth for re-run
├── bootstrap-report.md                       # what happened during bootstrap
├── .lock                                     # daemon-coordination lock
├── config/
│   ├── instance.yaml                         # tenant metadata
│   ├── persona.yaml                          # active persona (pointer + overrides)
│   ├── members.yaml                          # member-to-profile assignment
│   ├── authority.yaml                        # action gates + principal rules
│   ├── governance.yaml                       # rate limits, review policies, observation
│   ├── agent_capabilities.yaml               # per-agent action allowlists
│   ├── channels.yaml                         # comms routing
│   ├── plaid.yaml                            # institutions, sandbox/prod flag
│   ├── reminders-list-mapping.yaml           # iCloud Reminders sync
│   └── secrets/
│       └── sheet-protection-key              # 32-byte key for xlsx sheet protection
├── db/
│   ├── event_log.db                          # SQLCipher. Append-only.
│   ├── event_log.db-wal
│   └── event_log.db-shm
├── projections/
│   ├── parties.db                            # CRM projection
│   ├── interactions.db
│   ├── artifacts.db
│   ├── commitments.db
│   ├── tasks.db
│   ├── recurrences.db
│   ├── calendars.db
│   ├── places_assets_accounts.db
│   ├── money.db
│   ├── vector_search.db                      # sqlite-vec
│   ├── adminme-ops.xlsx                      # bidirectional human-editable
│   ├── adminme-finance.xlsx                  # bidirectional human-editable
│   └── .xlsx-state/                          # sidecar state for diff algorithm
│       ├── adminme-ops/
│       │   ├── Tasks.json
│       │   ├── Recurrences.json
│       │   ├── Commitments.json
│       │   └── Lists.json
│       └── adminme-finance/
│           ├── Raw_Data.json
│           └── Assumptions.json
├── packs/
│   ├── installed.yaml                        # registry of installed packs
│   ├── profiles/
│   │   ├── adhd-executive/
│   │   │   ├── pack.yaml
│   │   │   ├── reward_sampling.yaml
│   │   │   ├── views/today.jsx
│   │   │   ├── views/inbox.jsx
│   │   │   ├── views/crm.jsx
│   │   │   ├── tests/
│   │   │   └── compiled/                     # produced at install time
│   │   │       ├── today.ssr.js
│   │   │       ├── today.client.js
│   │   │       └── today.css
│   │   ├── minimalist-parent/
│   │   ├── kid-scoreboard/
│   │   └── ambient-entity/
│   ├── personas/
│   │   └── poopsy/
│   │       ├── pack.yaml
│   │       ├── reward_templates/
│   │       ├── paralysis_templates/
│   │       └── theme/
│   ├── adapters/
│   │   ├── messaging-gmail-api/
│   │   ├── messaging-bluebubbles-adminme/
│   │   ├── messaging-sms-twilio/
│   │   ├── calendaring-google-api/
│   │   ├── calendaring-caldav-icloud/
│   │   ├── contacts-google-people/
│   │   ├── documents-google-drive/
│   │   ├── financial-plaid/
│   │   └── reminders-apple-eventkit/
│   ├── pipelines/
│   │   ├── identity-resolution/
│   │   ├── noise-filtering/
│   │   ├── commitment-extraction/
│   │   ├── thank-you/
│   │   ├── recurrence-extraction/
│   │   ├── artifact-classification/
│   │   ├── relationship-summarization/
│   │   ├── closeness-scoring/
│   │   ├── reminder-dispatch/
│   │   ├── morning-digest/
│   │   ├── reward-dispatch/
│   │   ├── paralysis-detection/
│   │   ├── whatnow-ranking/
│   │   ├── scoreboard-projection/
│   │   ├── custody-brief/
│   │   ├── crm-surface/
│   │   └── graph-miner/
│   └── skills/
│       ├── classify-commitment-candidate/
│       ├── extract-commitment-fields/
│       ├── classify-thank-you-candidate/
│       ├── compose-morning-digest/
│       ├── classify-capture-intent/
│       ├── extract-structured-from-capture/
│       ├── infer-due-date/
│       └── …                                 # ~30 total
├── replay-archive/
│   └── skills/                               # recorded model calls for replay tests
│       ├── classify-commitment-candidate/
│       │   └── 3.2.1/
│       │       ├── kate-kitchen-2026-04-21.json
│       │       └── coach-practice-2026-04-20.json
│       └── …
├── logs/
│   ├── adminme.jsonl                         # correlation-ID-stamped
│   ├── adapters.jsonl
│   ├── pipelines.jsonl
│   ├── skills.jsonl
│   └── console.jsonl
├── queued/
│   └── writes/                                # degraded-mode write queue
└── backups/
    └── (Backblaze B2 sync target; local cache)
```

### Key config files (representative)

**`config/instance.yaml`** — tenant metadata and runtime knobs.

```yaml
tenant_id: "stice-household"                   # generated, stable
display_name: "Stice household"
created_at: "2026-01-18T10:00:00-05:00"
timezone: "America/New_York"
address:
  line1: "761 E Morningside Dr NE"
  city: "Atlanta"
  state: "GA"
  postal: "30306"
coordinates: [33.7858, -84.3541]
platform_version: "0.4.2"
api_ports:
  core: 3333
  comms: 3334
  capture: 3335
  automation: 3336
  console: 3330
tailscale:
  tailnet: "stice-family.ts.net"
  serve_config: "/Users/poopsy/.config/tailscale/serve.json"
xlsx_projection:
  enabled: true
  debounce_seconds: 5
  reverse_undo_window_seconds: 5
observation_mode:
  active: false                                # flipped off after 7-day review
  enabled_history:
    - { ts: "2026-01-18T10:15:00-05:00", by: "bootstrap" }
    - { ts: "2026-01-25T09:03:00-05:00", by: "stice-james", reason: "7-day review complete" }
```

**`config/persona.yaml`** — active persona pointer + optional per-instance overrides.

```yaml
active_persona: "persona:poopsy"
assistant_apple_id: "poopsy.stice@icloud.com"
assistant_phone: "+14045550127"
assistant_gmail: "poopsy@stice.family"
overrides:
  # Instance can override specific persona reward templates if desired.
  # None in this instance; keep built-ins.
  reward_templates: {}
  theme_tokens: {}
```

**`config/members.yaml`** — the household's members with profile assignments.

```yaml
members:
  - member_id: "stice-james"
    party_id: "p-james-stice"
    role: "principal"
    profile: "profile:adhd_executive"
    display_name: "James"
    given_name: "James"
    family_name: "Stice"
    tailscale_login: "james@stice.family.ts.net"
    primary_channels:
      imessage: "+14045550101"
      email: "james@stice.family"
    view_as_allowed: true
    active: true

  - member_id: "stice-laura"
    party_id: "p-laura-stice"
    role: "principal"
    profile: "profile:minimalist_parent"
    display_name: "Laura"
    given_name: "Laura"
    family_name: "Stice"
    tailscale_login: "laura@stice.family.ts.net"
    primary_channels:
      imessage: "+14045550102"
      email: "laura@stice.family"
    privileged_ingest:
      - adapter: "messaging:gmail_api"
        account: "laura@evolvefamilylaw.com"
        sensitivity: "privileged"
      - adapter: "calendaring:google_calendar_api"
        calendar_id: "laura@evolvefamilylaw.com"
        sensitivity: "privileged"
    view_as_allowed: true
    active: true

  - member_id: "stice-charlie"
    party_id: "p-charlie-stice"
    role: "child"
    profile: "profile:kid_scoreboard"
    display_name: "Charlie"
    given_name: "Charlie"
    family_name: "Stice"
    date_of_birth: "2019-03-14"
    tailscale_login: null                      # no device login
    primary_channels: {}
    view_as_allowed: false
    active: true

  - member_id: "stice-babygirl"
    party_id: "p-babygirl-stice"
    role: "ambient"
    profile: "profile:ambient_entity"
    display_name: "Baby girl"
    given_name: null
    family_name: "Stice"
    expected_arrival: "2026-05-18"
    active: false                              # becomes active at birth event

non_member_parties:
  # Coparents, close family, service providers with privileged context —
  # recorded here for routing config but not household members.
  - party_id: "p-mike-coparent"
    role: "coparent_non_user"
    relation_to_member: "stice-charlie"
    primary_channels:
      sms: "+14045550303"
    outbound_policy: "inbox_only_no_autonomous"
```

**`config/authority.yaml`** — what each role/agent can do.

```yaml
principals:
  - member_id: "stice-james"
    can_act_as_principal: true
    can_view_as: ["stice-laura", "stice-charlie"]
  - member_id: "stice-laura"
    can_act_as_principal: true
    can_view_as: ["stice-james", "stice-charlie"]

action_gates:
  # 'allow' | 'review' | 'deny' | 'hard_refuse'
  message.send: "allow"
  email.send: "allow"
  sms.send: "allow"
  push.send: "allow"
  commitment.confirm: "allow"
  task.create: "allow"
  task.complete: "allow"
  party.merge: "review"                        # principal must approve
  money_flow.manually_added: "allow"
  plaid.link_institution: "review"             # principal must approve new Plaid link
  send_as_principal: "hard_refuse"             # never; Mode 3 prohibited
  outbound_to_opposing_counsel: "hard_refuse"  # detected via party tag

allowed_personas_sending_as_assistant:
  - "persona:poopsy"

forbidden_outbound_parties:
  # Tag-based; no events or messages can be sent to parties with these tags
  - tag: "opposing_counsel"
  - tag: "privileged:ex_spouse"                # none present, but scaffold

coach_role_access:
  # 'coach' session type (external-context chat) strips these columns before build
  denied_columns:
    - "financial_*"
    - "health_*"
    - "privileged_*"
```

**`config/governance.yaml`** — rate limits, review policies, observation mode knobs.

```yaml
rate_limits:
  writes_per_minute:
    max: 60
    window_s: 60
  email.send:
    max: 20
    window_s: 3600
  sms.send:
    max: 30
    window_s: 3600
  push.send:
    max: 40
    window_s: 3600
  chat.message:
    max: 40
    window_s: 300
  web_chat:                                    # legacy naming for SSE chat
    max: 40
    window_s: 300

per_member_proactive_caps:
  stice-james:
    total_per_day: 15
    paralysis_nudges_per_day: 2
    reward_per_hour: 6
  stice-laura:
    total_per_day: 4                           # minimalist_parent cap
    paralysis_nudges_per_day: 0                # disabled
    reward_per_hour: 3

observation_mode:
  default_on_fresh_instance: true
  suppressed_events_ttl_days: 60
  external_call_allow_when_observing: false    # read-only; no outbound
  local_toast_when_observing: true             # console still fires toasts locally

review_queue:
  default_ttl_days: 14
  auto_dismiss_after_ttl: true
  notify_principals_of_pending: true

privacy:
  calendar_privileged_redaction: "busy"        # alternatives: "hidden", "owner_hint"
  calendar_owner_hint: "first_name"            # "none", "first_name", "full_name"
  child_forbidden_tags: ["finance", "health", "legal", "adult_only"]
```

**`config/plaid.yaml`** — institutions and Plaid environment state.

```yaml
environment: "sandbox"                         # flipped to "production" after observation
plaid_client_id_ref: "op://Private/Plaid adminme/client_id"
plaid_secret_ref: "op://Private/Plaid adminme/sandbox_secret"
webhook_url: "https://adminme-funnel.stice.family/webhooks/plaid"

institutions:
  - name: "USAA"
    plaid_institution_id: "ins_109510"
    access_token_ref: "op://Private/Plaid adminme/usaa_access_token"
    cursor: "abc123f..."
    linked_at: "2026-01-18T10:40:00-05:00"
    last_sync_at: "2026-04-21T12:00:00-04:00"
    health: "healthy"
    accounts:
      - account_id: "plaid-acct-1847"
        type: "depository"
        subtype: "checking"
        last4: "1847"
      - account_id: "plaid-acct-9923"
        type: "depository"
        subtype: "savings"
        last4: "9923"
      - account_id: "plaid-acct-0041"
        type: "credit"
        subtype: "credit card"
        last4: "0041"
  - name: "Wells Fargo"
    plaid_institution_id: "ins_127989"
    access_token_ref: "op://Private/Plaid adminme/wells_access_token"
    cursor: "9ef331b..."
    linked_at: "2026-01-18T10:45:00-05:00"
    last_sync_at: "2026-04-21T12:00:00-04:00"
    health: "healthy"
    accounts:
      - account_id: "plaid-acct-3891"
        type: "loan"
        subtype: "mortgage"
        last4: "3891"
  - name: "Fidelity"
    plaid_institution_id: "ins_12"
    access_token_ref: "op://Private/Plaid adminme/fidelity_access_token"
    cursor: "…"
    linked_at: "2026-01-19T09:00:00-05:00"
    last_sync_at: "2026-04-10T12:00:00-04:00"
    health: "link_expired"
    accounts:
      - account_id: "plaid-acct-5538"
        type: "investment"
        subtype: "brokerage"
        last4: "5538"
      - account_id: "plaid-acct-7712"
        type: "investment"
        subtype: "ira"
        last4: "7712"
```

**`config/reminders-list-mapping.yaml`** — Apple Reminders bidirectional sync.

```yaml
assistant_apple_id: "poopsy.stice@icloud.com"

lists:
  - icloud_list_name: "Family Grocery"
    target: "list"
    target_list_key: "grocery"
    auto_commit: true
    shared_with:
      - "+14045550101"                         # James
      - "+14045550102"                         # Laura
  - icloud_list_name: "James Tasks"
    target: "tasks"
    owner_member_id: "stice-james"
    auto_commit: false                         # route to inbox for approval
  - icloud_list_name: "Laura Tasks"
    target: "tasks"
    owner_member_id: "stice-laura"
    auto_commit: true
  - icloud_list_name: "Family Packing"
    target: "list"
    target_list_key: "packing"
    auto_commit: true

never_sync_rules:
  list_name_substrings: ["Work", "Case", "Client", "Evolve"]
  item_tags: ["#private"]
  item_owned_by_roles: ["child", "ambient"]
```

### Event log state (representative sample)

After a completed bootstrap + 3 months of ingest, `db/event_log.db` contains roughly 45,000-80,000 events. A small, illustrative slice:

```json
{"type": "tenant.initialized", "event_id": "ev_001", "tenant_id": "stice-household", "event_at_ms": 1737218400000, "payload": {"platform_version": "0.4.0"}}
{"type": "persona.activated", "event_id": "ev_002", "event_at_ms": 1737218500000, "payload": {"persona_id": "persona:poopsy"}}
{"type": "member.created", "event_id": "ev_003", "event_at_ms": 1737218600000, "payload": {"member_id": "stice-james", "role": "principal", "profile_id": "profile:adhd_executive"}}
{"type": "party.created", "event_id": "ev_004", "event_at_ms": 1737218610000, "payload": {"party_id": "p-james-stice", "kind": "person", "display_name": "James Stice"}}
{"type": "identifier.added", "event_id": "ev_005", "event_at_ms": 1737218620000, "payload": {"identifier_id": "id-1", "party_id": "p-james-stice", "kind": "email", "value": "james@stice.family", "verified": true, "is_primary": true}}
{"type": "member.created", "event_id": "ev_006", "event_at_ms": 1737218700000, "payload": {"member_id": "stice-laura", "role": "principal", "profile_id": "profile:minimalist_parent"}}
{"type": "observation.enabled", "event_id": "ev_010", "event_at_ms": 1737219000000, "payload": {"previous_state": false}}
{"type": "pack.installed", "event_id": "ev_020", "event_at_ms": 1737219300000, "payload": {"pack_id": "adapter:messaging:bluebubbles_adminme", "version": "2.1.3"}}
{"type": "plaid.institution.linked", "event_id": "ev_040", "event_at_ms": 1737220200000, "payload": {"institution_id": "ins_109510", "account_count": 4}}
{"type": "messaging.received", "event_id": "ev_2001", "event_at_ms": 1745231400000, "payload": {"channel": "imessage", "text": "Hey! Any interest in swinging by Sat around 2?...", "from_handle": "kate@icloud.com"}}
{"type": "commitment.proposed", "event_id": "ev_2002", "correlation_id": "c_m1_abc123", "event_at_ms": 1745231405123, "payload": {"kind": "reply", "owed_by_member_id": "stice-james", "owed_to_party_id": "p-kate", "confidence": 0.87, "strength": "confident"}}
{"type": "commitment.confirmed", "event_id": "ev_2003", "correlation_id": "c_m1_abc123", "event_at_ms": 1745235200000, "payload": {"proposal_event_id": "ev_2002", "approved_by": "stice-james"}}
```

The event log is the one file that, lost, cannot be reconstructed from anything else. Back it up. Encrypt it. Don't trust any projection that disagrees with it.

### Projection state

After the above event log, `projections/parties.db` contains roughly 350-600 party rows (the household + close contacts + service providers + schools + vendors). `projections/commitments.db` holds ~30-60 open commitments at steady state. `projections/tasks.db` holds 15-40 active tasks per member. `projections/money.db` holds several thousand transactions (all Plaid history since linking). `projections/adminme-finance.xlsx` weighs ~800KB with the Raw Data sheet dominating; `projections/adminme-ops.xlsx` weighs ~150KB.

### What "healthy instance" looks like

Signs that a bootstrap succeeded and the platform is steady:

- `ps aux | grep adminme` shows all four Python APIs (core, comms, capture, automation) plus the xlsx forward daemon, xlsx reverse daemon, pack supervisor, and adapter supervisor running — 8 processes.
- The Node console at `:3330` responds to `curl -H 'X-Tailscale-User-Login: james@stice.family.ts.net' http://localhost:3330/api/today` with the six-task payload.
- `adminme status` exits zero with `health: healthy` for all subsystems.
- `adminme projection rebuild parties --dry-run` shows no pending deltas (the projection matches the event log).
- Logs in `~/.adminme/logs/adminme.jsonl` show correlation IDs flowing end-to-end — one inbound message → one classify skill call → one extract skill call → one commitment.proposed event → one inbox surface update.

### What to build to verify it all works

End-to-end test in `tests/e2e/test_bootstrap_to_populated.py`:

1. Spin up a clean `ADMINME_INSTANCE_DIR=/tmp/adminme-e2e-XXXX/`.
2. Run `bootstrap/install.sh` with answers from a YAML fixture.
3. Assert the directory tree matches the structure above.
4. Assert all platform services start.
5. Post a fixture `messaging.received` event.
6. Assert within 10s: `commitment.proposed` event is emitted, inbox projection updates, xlsx forward regenerates with a new Commitments row.
7. Tear down cleanly.

If this test passes, the 16-phase build is substantially done.

---

## PHASE PLAN

Work phase by phase. After each phase: run all tests, commit, summarize, STOP for human review.

### PHASE 0: Scaffolding + Identity/Layer Scans

1. Repo structure:

```
adminme/
├── README.md
├── LICENSE                              # PolyForm Noncommercial 1.0.0 default
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── VERSION
├── CHANGELOG.md
├── Makefile
├── pyproject.toml                        # poetry-managed
├── .pre-commit-config.yaml
├── .gitignore
├── docs/
│   ├── ARCHITECTURE.md
│   ├── EXTENDING.md
│   ├── THREAT_MODEL.md
│   ├── DECISIONS.md
│   ├── SYSTEM_INVARIANTS.md                # produced by prompt 01b; cross-cutting contracts
│   ├── architecture-summary.md             # produced by prompt 01
│   ├── openclaw-cheatsheet.md              # produced by prompt 01
│   ├── reference/                          # mirrored external docs (prompt 00.5, GitHub-first)
│   │   ├── _status.md
│   │   ├── _gaps.md
│   │   ├── _refresh-schedule.md
│   │   ├── openclaw/                       # from openclaw/openclaw/docs/
│   │   ├── plaid/                          # from plaid/plaid-openapi
│   │   ├── bluebubbles/                    # from BlueBubblesApp/bluebubbles-docs
│   │   ├── google-gmail/                   # from googleapis/google-api-nodejs-client
│   │   ├── google-calendar/                # from googleapis/google-api-nodejs-client
│   │   ├── textual/                        # from Textualize/textual/docs/
│   │   ├── aiosqlite/                      # from omnilib/aiosqlite/docs/
│   │   ├── sqlite-vec/                     # from asg017/sqlite-vec
│   │   ├── sqlcipher/                      # from sqlcipher/sqlcipher
│   │   ├── caldav/                         # from python-caldav/caldav
│   │   ├── apple-eventkit/                 # GAP — manual clip (see _gaps.md)
│   │   ├── apple-shortcuts/                # GAP — manual clip
│   │   └── tailscale/                      # partial gap — see _gaps.md
│   ├── bootstrap.md
│   ├── profiles.md
│   ├── personas.md
│   ├── packs.md
│   ├── registry.md
│   ├── multi-tenant.md
│   ├── security-model.md
│   ├── recovery.md
│   ├── PHASE_B_SMOKE_TEST.md               # produced by prompt 19
│   └── release-process.md
├── adminme/                              # Python package (was `platform/`; renamed to avoid stdlib shadow)
│   ├── lib/                              # core Python libs (L2+L3 support)
│   │   ├── event_log/
│   │   ├── event_bus/
│   │   ├── event_types/
│   │   ├── projections/
│   │   ├── session/
│   │   ├── scope/
│   │   ├── authority/
│   │   ├── observation_mode/
│   │   ├── skill_runner/
│   │   ├── pack_registry/
│   │   ├── profile_loader/
│   │   ├── persona_loader/
│   │   ├── identity/
│   │   ├── secrets/
│   │   ├── xlsx_sync/
│   │   └── ...
│   ├── adapters/                         # L1
│   │   ├── messaging/
│   │   ├── calendaring/
│   │   ├── contacts/
│   │   ├── documents/
│   │   ├── telephony/
│   │   ├── financial/
│   │   ├── manual/
│   │   ├── iot/
│   │   └── webhook/
│   ├── pipelines/                        # L4
│   │   ├── identity_resolution/
│   │   ├── noise_filtering/
│   │   ├── commitment_extraction/
│   │   ├── thank_you_detection/
│   │   ├── recurrence_extraction/
│   │   ├── artifact_classification/
│   │   ├── relationship_summarization/
│   │   ├── closeness_scoring/
│   │   ├── reminder_dispatch/
│   │   ├── morning_digest/
│   │   ├── reward_dispatch/
│   │   ├── paralysis_detection/
│   │   ├── whatnow_ranking/
│   │   ├── scoreboard_projection/
│   │   ├── custody_brief/
│   │   ├── crm_surface/
│   │   └── graph_miner/
│   ├── skills/                           # L4
│   │   └── (one dir per skill; see Skill Runner spec)
│   ├── products/                         # L5 Python FastAPI apps
│   │   ├── core/
│   │   ├── comms/
│   │   ├── capture/
│   │   └── automation/
│   ├── console/                          # L5 Node shell — see CONSOLE_PATTERNS.md
│   │   ├── server.mjs
│   │   ├── shell.html
│   │   ├── middleware/
│   │   │   ├── identity.js                # Tailscale identity resolution
│   │   │   ├── session.js                 # authMember / viewMember split
│   │   │   ├── correlation.js
│   │   │   └── error.js
│   │   ├── lib/
│   │   │   ├── guarded_write.js
│   │   │   ├── rate_limiter.js
│   │   │   ├── privacy_filter.js
│   │   │   ├── bridge.js                  # HTTP bridge to Python APIs
│   │   │   ├── nav.js                     # HIDDEN_FOR_CHILD
│   │   │   └── observation.js
│   │   ├── routes/
│   │   │   ├── today.js
│   │   │   ├── inbox.js
│   │   │   ├── crm.js
│   │   │   ├── capture.js
│   │   │   ├── finance.js
│   │   │   ├── calendar.js
│   │   │   ├── scoreboard.js
│   │   │   ├── settings.js
│   │   │   ├── chat.js                    # SSE streaming
│   │   │   └── reward_stream.js           # SSE reward channel
│   │   ├── theme_loader.mjs
│   │   └── package.json
│   ├── daemons/
│   │   ├── event_dispatcher/
│   │   ├── xlsx_sync/
│   │   └── reminders_sync/
│   ├── cli/
│   │   └── adminme/                      # typer-based
│   └── migrations/
│       ├── 001_initial_schema.py
│       └── README.md
├── bootstrap/
│   ├── install.sh
│   ├── wizard/
│   │   ├── main.py
│   │   ├── sections/
│   │   ├── validators.py
│   │   ├── renderer.py
│   │   └── answers_store.py
│   └── templates/
│       ├── adminme-ops-blank.xlsx        # the xlsx template for the xlsx projection
│       ├── adminme-finance-blank.xlsx
│       ├── instance.yaml.template
│       ├── persona.yaml.template
│       ├── members.yaml.template
│       ├── authority.yaml.template
│       ├── governance.yaml.template
│       ├── agent_capabilities.yaml.template
│       ├── channels.yaml.template
│       ├── plaid.yaml.template
│       ├── reminders-list-mapping.yaml.template
│       └── standing-orders/
├── profiles/                             # 5 built-in packs
│   ├── adhd_executive/
│   ├── minimalist_parent/
│   ├── power_user/
│   ├── kid_scoreboard/
│   └── ambient_entity/
├── personas/                             # 4 built-in packs
│   ├── poopsy/
│   ├── butler_classic/
│   ├── friendly_robot/
│   └── quiet_assistant/
├── integrations/                         # built-in adapter packs
│   ├── gmail/
│   ├── imessage-bluebubbles/
│   ├── apple-reminders/
│   ├── google-calendar/
│   ├── google-drive/
│   ├── google-people/
│   ├── microsoft-graph/
│   ├── caldav/
│   ├── carddav/
│   ├── plaid/
│   ├── home-assistant/
│   ├── privacy-com/
│   ├── twilio/
│   ├── ringcentral/
│   ├── telegram/
│   ├── discord/
│   └── webhook-generic/
├── scripts/
│   ├── preflight.sh
│   ├── install-openclaw.sh
│   ├── configure.sh
│   ├── install-skills.sh
│   ├── install-daemons.sh
│   ├── pair-channels.sh
│   ├── smoke-test.sh
│   ├── rotate-tokens.sh
│   ├── backup-now.sh
│   └── restore-from-backup.sh
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── canaries/                         # privacy, identity, isolation
│   ├── fixtures/
│   └── smoke/
└── .github/workflows/
    ├── test.yml
    ├── lint.yml
    └── release.yml
```

2. Initialize git. `.gitignore` excludes `~/.adminme/`, `.env`, `tests/fixtures/real-*`, `node_modules`, `__pycache__`, `.venv`, compiled JSX.

3. **Identity scan test** (`tests/unit/test_no_hardcoded_identity.py`). Fails CI if adminme/bootstrap/profiles/personas/integrations/tests contain literal email patterns, E.164 phones, street-address patterns, or tenant-name blocklist entries.

4. **Layer violation scan test** (`tests/unit/test_layer_violations.py`). Fails if any `adminme/pipelines/<X>/` imports from `adminme/pipelines/<Y>/`, or any `adminme/products/<X>/` imports from `adminme/products/<Y>/`, or any code outside `adminme/lib/event_log/` writes to `events.db`.

5. **Append-only event log test**. Fails if any code path outside `adminme/lib/event_log/` has `UPDATE events` or `DELETE FROM events` or equivalent.

6. `Makefile` targets: `test`, `test-unit`, `test-integration`, `test-canary`, `lint`, `format`, `bootstrap`, `deploy`, `backup`, `restore`, `docs`, `scan-identity`, `scan-layer-violations`, `scan-event-log-purity`.

7. uv setup with pinned deps. Node install at `adminme/console/`.

8. CI pipeline: lint → type-check (pyright strict) → unit → integration → canary → coverage gate (85% on core).

9. Verify: `uv run pytest --collect-only`, `cd adminme/console && npm install`, identity scan passes, layer scan passes, append-only scan passes.

**Commit:** `phase 0: scaffolding with three static scans`
**Stop and report.**

---

### PHASE 1: Event Log + Event Bus + Pydantic Models + Session

This is the foundation. Build it right.

1. **`adminme/lib/event_log/`:**
   - `BaseEvent` Pydantic model with all fields from spec
   - `EventStore` with `append(event) -> None`, `read_since(cursor) -> AsyncIterator`, `read_by_correlation(id)`, `read_by_causation(id)`. Append-only enforced by code + SQLite trigger.
   - SQLCipher setup: key from `keyring` (OS keychain); WAL; pragma tuning
   - Raw payload sidecar: payloads >64KB → `raw_events/` directory with zstd compression; `raw_ref` points
   - Artifact storage: encrypted blob storage at `artifacts/` with sha256 dedup; retrieval via `ArtifactStore.get(ref)`

2. **`adminme/lib/event_types/`:** 
   - One subdir per namespace (`messaging`, `calendar`, `contacts`, `documents`, `telephony`, `financial`, `identity`, `commitment`, `task`, `recurrence`, `skill`, `adminme_reward`, `adminme_paralysis`, `adminme_digest`, `observation`, `plaid`, `reminder`, `member`, `profile`, `persona`, `system`)
   - Per event type: Pydantic model + schema_version + upcaster (identity function at v1)
   - Registry: `EventTypeRegistry.register(event_type_name, model, upcaster_chain)`
   - Lookup: `EventTypeRegistry.validate_payload(event_type, payload_dict)` → validated Pydantic model

3. **`adminme/lib/event_bus/`:** 
   - `EventBus` protocol
   - `InProcessBus` implementation (asyncio queues + durable offsets in `bus_consumer_offsets` SQLite table)
   - `RedisStreamsBus` skeleton (for Phase N future) — not functional in v1 but protocol-compliant stub

4. **`adminme/lib/session/`:** 
   - `Session(current_user, requested_scopes)` class
   - Query builders that automatically add scope predicates
   - Scope enforcement test harness

5. **`adminme/lib/scope/`:** 
   - `OwnerScope`, `VisibilityScope`, `Sensitivity` types
   - Scope arithmetic: `widen`, `narrow`, `intersect`
   - Privileged-access logging

6. **Migrations:** 
   - Alembic setup
   - `001_initial_schema.py` creates events table + bus_consumer_offsets

7. **Tests (≥95% coverage on this layer):**
   - EventStore append/read round-trip
   - Oversized payload to sidecar + read-through
   - Artifact dedup by sha256
   - Append-only: attempt UPDATE → fails
   - Event type validation: invalid payload → rejected at append
   - Upcaster chain: v1 event read through v1-to-v2 upcaster produces v2 shape
   - InProcessBus pub/sub
   - InProcessBus durable consumer offset: restart → resumes from last offset
   - Session: cross-scope query → raises ScopeViolation
   - Privileged data: session without privileged scope → cannot read
   - Privileged-access log records every privileged read by non-owner

**Commit:** `phase 1: event log, event bus, event type registry, session/scope`
**Stop and report.**


---

### PHASE 2: Projections Framework + Parties Projection (The CRM Spine)

This is the thin-slice anchor. Get the CRM spine working end-to-end before adding more.

1. **`adminme/lib/projections/`:** 
   - `Projection` base class: name, version, subscribes_to, cursor, apply(event), rebuild()
   - `ProjectionRegistry`: register, start_all, rebuild_by_name, lag_report
   - SQLite projections database at `~/.adminme/data/projections.db` (separate from events.db)
   - Migrations via Alembic (separate migration tree from events)

2. **`parties` projection:** 
   - Subscribes: `party.created`, `party.updated`, `party.merged`, `identity.identifier_added`, `identity.identifier_removed`, `membership.added`, `membership.ended`, `relationship.added`, `relationship.updated`, `relationship.ended`, plus passive observation of `messaging.received`/`messaging.sent`/`telephony.*` for last_seen_at updates
   - Creates/updates `parties`, `identifiers`, `memberships`, `relationships` tables
   - Handles party merges (identity_resolution pipeline emits `party.merge_approved` events)
   - Idempotent: reapplying the same event → no-op (via `last_event_id` tracking)
   - Rebuildable: `adminme projections rebuild parties` drops tables + replays

3. **CRM query layer:** 
   - `PartyQuery` with fluent filters: `.by_kind()`, `.by_tier()`, `.by_tag()`, `.overdue_for_contact()`, `.birthdays_in_next(days)`, `.search_by_name()`, `.with_relationships_of_type()`
   - Returns scope-filtered Pydantic models via Session

4. **Manual events adapter (`manual:cli`):** 
   - CLI subcommand `adminme party add --kind person --name "..."` → emits `party.created` event
   - `adminme party link --party A --party B --label spouse` → emits `relationship.added`
   - Good enough for hand-entering seed data before any ingest adapter is live

5. **Tests:**
   - Create party → event → projection row
   - Add identifier → projection updates
   - Add membership → projection updates
   - Add relationship (bidirectional and directional labels) → projection updates
   - Merge two parties (via `party.merged` event) → identifiers reassigned, relationships updated, no orphans
   - Rebuild projection → identical to prior state
   - Scope enforcement: party in private:user_a scope not visible to session for user_b
   - CRM queries: overdue_for_contact works; birthdays_in_next returns correct window

**Commit:** `phase 2: projections framework + parties projection (CRM spine)`
**Stop and report.**

---

### PHASE 3: Skill Runner + Authority Gate + Observation Mode

Support layers required before pipelines become useful.

1. **`adminme/lib/skill_runner/`:** 
   - `Skill` manifest loader (parses skill.md frontmatter)
   - Input/output JSON schema validation
   - Provider abstraction (`LLMProvider` protocol, `AnthropicProvider` concrete)
   - `SkillRunner.invoke(skill_name, input)` → validates, renders prompt, calls, validates output, records `skill.call_recorded` event
   - Replay: `SkillRunner.replay(skill_name, since_ts)` → fetch historical inputs from skill.call_recorded events, re-run with current version, emit correction events
   - Sensitivity enforcement: skill with `sensitivity_required: normal` refuses input with `sensitivity: privileged` marker
   - Cost ledger aggregation from skill.call_recorded events

2. **`adminme/lib/authority/`:** 
   - `authority.yaml` parser → in-memory policy tree
   - `authority.can(action, actor, target, context) -> Decision(allow|confirm|deny, reason)`
   - Rate limit tracking with sliding windows
   - Hard `never` rules that aren't overridable

3. **`adminme/lib/observation_mode/`:** 
   - `is_active() -> bool`
   - `compose_and_log(action_description, payload, actor) -> None` → emits `observation.suppressed`
   - Override control: `adminme observation on|off` → writes to `config/runtime.yaml` + emits `observation.mode_changed`

4. **First skills (≥3):**
   - `classify_message_nature@v1` — noise/transactional/personal/professional/promotional
   - `extract_commitments@v1` — extract commitments from interactions
   - `summarize_interaction@v1` — lazy summarizer

5. **Tests:**
   - Skill runner validates input/output
   - Skill runner records every call as event
   - Replay: change skill v1 → v2, re-run over historical inputs, projections re-derive
   - Authority: `deny` rules refuse; `confirm` returns 202 pending; `allow` proceeds
   - Observation mode: outbound-capable action → suppressed event emitted; no external call
   - Observation mode override: `adminme observation off` flips flag; next action fires

**Commit:** `phase 3: skill runner + authority + observation mode`
**Stop and report.**

---

### PHASE 4: Thin Slice End-to-End — IMAP Adapter → Events → Interactions Projection → Identity Resolution Pipeline → CLI Query

This is the Hearth thin-slice test. Prove the whole loop works.

1. **`messaging:imap` adapter:** 
   - OAuth2 XOAUTH2 + app-password modes
   - Folder polling with UIDVALIDITY + UIDNEXT cursor
   - Per-account config
   - Emits `messaging.received` / `messaging.sent` events with full MIME metadata, bodies stored as artifacts for >64KB
   - Handles attachments as separate `documents.artifact_discovered` events
   - Integration test against a local test IMAP server (greenmail or similar) with fixture mailbox

2. **`interactions` projection:** 
   - Subscribes: `messaging.received`, `messaging.sent`, `telephony.*`
   - Dedup: multiple ingestion paths for the same message → one Interaction row (match on Message-Id header when available)
   - Links to Parties via identifier resolution (lazy; happens after identity_resolution pipeline runs)

3. **`identity_resolution` pipeline:** 
   - Subscribes: events that introduce identifiers
   - Exact match on normalized value → link to existing Party
   - Fuzzy match → emit `identity.merge_suggested`
   - No match → emit `party.created`
   - Confidence-scored; all decisions provenance-tracked

4. **CLI query:** `adminme query "show me recent interactions with Kate"` 
   - Resolves "Kate" → Party via fuzzy search
   - Returns recent Interactions where Kate is participant
   - Respects scope (user's own allowed scopes)

5. **Integration test:** 
   - Fixture mailbox with 20 realistic messages (from various senders, some dups, some with attachments)
   - Run IMAP adapter → events emitted
   - Run identity_resolution pipeline → Parties resolved
   - Run interactions projection → Interaction rows created
   - Execute CLI query → correct results

**Commit:** `phase 4: thin slice end-to-end — IMAP → events → interactions → parties → CLI query`
**Stop and report.** This is the proof-of-life milestone. The architecture either works here or it doesn't.

---

### PHASE 5: Broader Adapter Coverage

Full implementations, in priority order:

1. **`calendaring:caldav`** + **`calendaring:google_calendar_api`** + **`calendaring:microsoft_graph_calendar`** + **`calendaring:ics_subscription`**. `calendars` projection. Privacy flag on events.
2. **`contacts:carddav`** + **`contacts:google_people`** + **`contacts:microsoft_graph_contacts`** + **`contacts:vcard_file`**. Feeds `parties` projection.
3. **`documents:local_folder`** + **`documents:google_drive`**. `artifacts` projection.
4. **`telephony:ringcentral`** + **`telephony:twilio`**. Voicemail + SMS + call log events.
5. **`messaging:gmail_api`** + **`messaging:microsoft_graph`** (richer than IMAP).

All with integration tests against sandbox accounts or fixtures.

**Commit per sub-phase.** Stop and report after all five families done.

---

### PHASE 6: Hearth-Spec Pipelines

In order:
1. `noise_filtering` — feeds back into interactions classification
2. `commitment_extraction` — populates commitments proposals
3. `thank_you_detection` — specialization of above
4. `recurrence_extraction` — populates recurrences from contacts/artifacts/notes
5. `artifact_classification` — OCR + structured extraction of new documents

Each with canary tests.

**Commit per pipeline.** Stop and report after all five done.

---

### PHASE 7: AdministrateMe CoS Pipelines + Skills

The ADHD neuroprosthetic + CoS layer. These operate over Tasks + Commitments + Recurrences unified.

1. `reward_dispatch` — subscribes to `task.completed`, `commitment.completed`; reads profile; picks template from persona; emits `adminme.reward.dispatched`
2. `paralysis_detection` — scheduled; deterministic template pool; emits `adminme.paralysis.triggered`
3. `whatnow_ranking` — deterministic scoring function (no LLM); per-profile behavior (carousel=1, compressed=5, power=10)
4. `morning_digest` — scheduled per member; composes digest; validation guard; emits `adminme.digest.composed`
5. `scoreboard_projection` — maintains streak counters
6. `custody_brief` — scheduled if coparent relationship exists
7. `crm_surface` — gap detection, birthdays, hosting imbalance
8. `graph_miner` — nightly mining of captures/interactions for implied entities

With skills:
- `classify_item_nature@v2`
- `extract_micro_script@v1`
- `infer_due_date@v2`
- `compose_morning_digest@v3`
- `compose_weekly_review@v2`
- `compose_custody_brief@v1`
- `explain_anomaly@v1`
- `summarize_relationship@v2`
- `score_relationship_tier@v2`

**Commit per pipeline.** Stop and report.

---

### PHASE 8: Python Product APIs

Build the four FastAPI services (`core`, `comms`, `capture`, `automation`). Each:
- Implements its router per spec
- Integrates with its pipelines + skills
- Serves observation-mode + health endpoints
- Auto-generates OpenAPI docs

**Commit per product.**

---

### PHASE 9: Plaid Integration (Full, not stub)

Implement `financial:plaid` adapter per detailed spec. `financial:bank_csv_watcher` as fallback. `money_flows` projection. `balance_sheet` projection. `adminme-finance.xlsx` projection.

**Commit:** `phase 9: plaid integration complete`

---

### PHASE 10: Apple Reminders Bidirectional (Full)

Implement `messaging:apple_reminders` adapter + reminders-sync daemon + `reminder_sync_map` tracking + conflict resolution.

**Commit:** `phase 10: apple reminders bidirectional`

---

### PHASE 11: Profile & Persona Packs

Build the 5 built-in profile packs + 4 built-in persona packs per spec.

Build the JSX-compile-at-install pipeline (esbuild invoked by pack install flow).

**Commit:** `phase 11: profile and persona packs`

---

### PHASE 12: Node Console Shell

Implement the L5 Node console shell per **CONSOLE_PATTERNS.md** (all 12 patterns: Tailscale identity, session model, guardedWrite, RateLimiter, SSE chat, privacy filter, HIDDEN_FOR_CHILD, reward toast emission, degraded mode, HTTP bridge, observation-mode enforcement, error handling). Match the visual + interaction vocabulary from **CONSOLE_REFERENCE.html** (carousel/compressed/child view modes, Party detail page, inbox approval flow, Settings panes, quick-capture bar, chat FAB). Implement profile view loader (serves compiled JSX from `~/.adminme/packs/profiles/<id>/compiled/`), persona theme loader, observation-mode banner, degraded mode, pack management UI, settings panes (Plaid, Reminders, packs).

**Commit:** `phase 12: node console shell`

---

### PHASE 13: Bootstrap Wizard

Full Textual/Rich TUI wizard per spec. 9 sections. Resumable. Produces a working instance from zero.

End-to-end test: spin up fresh `~/.adminme/` with fake credentials → wizard runs → all services start → observation-mode briefing delivers → instance is healthy.

**Commit:** `phase 13: bootstrap wizard`

---

### PHASE 14: CLI + Deployment Scripts + Migration Framework

1. **`adminme` CLI.** Typer-based. Subcommand groups:
   - `adminme status` — health summary of all subsystems (event log, each projection, each adapter, each pipeline, console). Exit 0 healthy, non-zero degraded.
   - `adminme bootstrap` — run/resume the bootstrap wizard (see PHASE 13). `adminme bootstrap reconfigure <section>` re-runs one section.
   - `adminme credentials {add, list, rotate, remove}` — manage 1Password credential references post-bootstrap.
   - `adminme instance {reset, backup, restore}` — hard instance operations. `reset` requires two confirmations and purges event log.
   - `adminme pack {list, install, update, remove, force-uninstall}` — pack lifecycle. `install` accepts a path or registry id.
   - `adminme projection {list, rebuild, status}` — project state. `rebuild <name>` replays events.
   - `adminme event {tail, grep, export}` — event log reads only.
   - `adminme skill {list, replay, record-fixture}` — skill management and replay.
   - `adminme pipeline {list, pause, resume, logs}` — pipeline supervisor control.
   - `adminme adapter {list, restart, test, logs}` — adapter supervisor control.
   - `adminme xlsx {regenerate, rebuild-state}` — explicit xlsx projection operations.
   - `adminme query "<natural language>"` — light CLI query surface (resolves against parties, interactions, commitments).
   - `adminme migrate {status, apply, rollback}` — migration framework (see #3 below).
   - `adminme observation {status, enable, disable, suppressed-log}` — observation-mode control.
   - `adminme plaid {status, sync, go-live, relink}` — Plaid operations.
   - `adminme reminders {status, pause, resume}` — Apple Reminders sync control.
2. Deployment scripts (preflight, install-openclaw, configure, install-skills, install-daemons, pair-channels, smoke-test, rotate-tokens, backup, restore)
3. Migration framework: numbered migrations, idempotent, applied via `adminme migrate`, fixture tests per migration

**Commit:** `phase 14: cli + deployment + migrations`

---

### PHASE 15: Pack Registry

Public GitHub repo at `adminme/registry` with packs.yaml index. `adminme pack` CLI fully functional. Pack publish flow (PR to registry repo). Pack install safety (permission preview, sandbox post_install.py, rollback on failure). Lockfile.

**Commit:** `phase 15: pack registry`

---

### PHASE 16: Tests, Docs, Privacy Canaries, Deployment Docs

1. **Integration tests:** 
   - Full task lifecycle across products
   - Full message ingest flow
   - Full transaction ingest (Plaid)
   - Cross-product events (capture → task → reward)
   - Multi-agent routing
   - Coparent routing
   - Approval flow
   - Observation-mode end-to-end
   - Member profile switch (UI + engines change, no restart)
   - Persona change (theme + name + voice, services restart)
   - Pack install/remove
   - Migration test: bootstrap old instance → run migrate → verify data preserved

2. **Privacy canaries:** 
   - Privileged content never in non-owner sessions (≥12 scenarios)
   - Kid data: aggregated in adult views; row-level only in `-kids` agent
   - Coach role: no financial, no health
   - Plaid tokens never in logs
   - OAuth rotation preserves functionality

3. **Tenant isolation canaries:** 
   - Two instances via `ADMINME_INSTANCE_DIR` → fully independent
   - Identity static scan: no tenant data in platform code
   - Layer scan: no cross-product or cross-pipeline imports
   - Append-only scan: no code path can mutate events table

4. **Docs:** 
   - `ARCHITECTURE.md` — canonical version of the five-layer model
   - `EXTENDING.md` — how to write a new adapter, pipeline, skill, projection, event type (worked examples)
   - `THREAT_MODEL.md` — what we defend against, what we don't
   - `DECISIONS.md` — decisions accumulated during the build (conservative-default principle)
   - `DEPLOYMENT.md` — prerequisites, deployment, first 24h, first 5-7 days (observation), ongoing, for friends and family, pack ecosystem, known limitations, support

5. **Final audit:** 
   - No hardcoded secrets
   - No real tenant data in code
   - All secret references go through `lib/secrets`
   - Sandbox policies match trust tiers
   - Every `deny` rule has a test
   - Every `never` rule has a test
   - Identity scan passes
   - Layer scan passes
   - Append-only scan passes

**Commit:** `phase 16: deployment package ready — adminme v0.1.0`

---

## FINAL CHECKS — PLATFORM LEVEL

- [ ] docs/reference/ populated (GitHub-first mirror per prompt 00.5); `_status.md` shows ≥90% coverage; `_gaps.md` reviewed
- [ ] Identity static scan passes across all of adminme/, bootstrap/, profiles/, personas/, integrations/, tests/ (fixtures excluded)
- [ ] Layer violation scan passes (no cross-product imports, no cross-pipeline imports, no direct event log access outside lib/event_log)
- [ ] Append-only event log enforced by trigger + tested
- [ ] Bootstrap wizard runs end-to-end; resumable after interruption at every section
- [ ] Two instances on same machine via `ADMINME_INSTANCE_DIR` fully isolated
- [ ] `adminme migrate` idempotent with fixture-based migration tests

Product level:

- [ ] Core product: all pipelines wired; slash commands functional; scheduled jobs firing
- [ ] Comms product: identity_resolution + noise_filtering + commitment_extraction at ≥80% accuracy on 20 synthetic messages; coparent routing to inbox; Mode 3 refused
- [ ] Capture product: quick-capture prefix routing; triage flow; CRM Party detail page shows interactions/commitments/artifacts/relationships/summary/tier/gap/balance; graph_miner nightly
- [ ] Automation product: Plaid sandbox sync working; categorization ≥90% on fixtures; Assumptions sheet write without approval_token refused; bank-CSV fallback operational

Projection level:

- [ ] parties projection rebuildable; scope-enforced; merge-safe
- [ ] interactions projection dedup correct; attachment linking; sensitivity honored
- [ ] commitments projection: proposal → confirm → active → done lifecycle with provenance
- [ ] tasks projection: full lifecycle including deferred/waiting/dismissed
- [ ] artifacts projection: OCR + structured extraction; scope-enforced
- [ ] money projection: Plaid + CSV + manual; dedup; transfer exclusion
- [ ] xlsx_workbooks projection: bidirectional with diff-to-events round-trip; derived cells protected
- [ ] vector_search: privileged content excluded

Profile level:

- [ ] 5 profiles complete with compiled views, engines, tuning, prompts, tests
- [ ] adhd_executive: variable_ratio distribution within ±2% at N=10,000
- [ ] minimalist_parent: 3rd proactive in a day suppressed
- [ ] kid_scoreboard: no comms/captures/outbound visibility
- [ ] Profile switch in settings: new views + engines + tuning, no code change, no restart

Persona level:

- [ ] 4 personas complete
- [ ] Template substitution works throughout shell
- [ ] Theme CSS swap works
- [ ] Reward templates render per tier for all styles

Console:

- [ ] Tailscale primary auth; X-ADMINME-Member dev+loopback-gated
- [ ] authMember/viewMember with view-as for principals
- [ ] SSE chat functional
- [ ] Rate limit 429 with retry_after
- [ ] guardedWrite: confirm→202, deny→403
- [ ] Observation-mode banner when active
- [ ] Degraded banner when backend unresponsive
- [ ] Pack settings UI functional

Apple Reminders:

- [ ] Bidirectional canaries pass (both directions within 60s)
- [ ] Never-sync rules enforced
- [ ] Conflicts surface to inbox with diff
- [ ] Observation mode suppresses writes, allows reads

Plaid:

- [ ] Link works in sandbox
- [ ] Cursor-based sync; dedup correct
- [ ] Categorization ≥90%
- [ ] Identity-based owner detection
- [ ] Balance Sheet reads from Balance + Liabilities + Investments
- [ ] Sandbox → production gated
- [ ] Webhook HMAC verified
- [ ] Tokens never in logs

Observation mode:

- [ ] Every outbound-capable action and every proactive pipeline checks observation mode
- [ ] Every suppressed action emits `observation.suppressed` event
- [ ] `adminme review` shows suppressed actions with approve/dismiss
- [ ] `adminme observation off` flips flag + restarts affected services

Privacy:

- [ ] Identity-first: privileged content never enters ingest layer (by configuration)
- [ ] Session-scope: dmScope per-channel-peer prevents cross-principal leakage
- [ ] Software-scope: privileged events excluded from cross-scope features
- [ ] Coach role: no financial, no health
- [ ] Children: data-model only; no outbound; no agent binding
- [ ] Privileged-access log catches every non-owner read

Backup & recovery:

- [ ] Event log git-pushed nightly
- [ ] Projections reproducible from event log
- [ ] B2 rclone weekly
- [ ] `adminme restore` works end-to-end on fresh machine

Authority:

- [ ] Every deny rule has test
- [ ] Every never rule has test
- [ ] Every per-member, per-profile rule has test
- [ ] Rate limits enforced

Docs:

- [ ] README tells tenant what to do
- [ ] ARCHITECTURE.md canonical
- [ ] EXTENDING.md with adapter/pipeline/skill/projection/event-type walkthroughs
- [ ] THREAT_MODEL.md
- [ ] DECISIONS.md
- [ ] DEPLOYMENT.md

Multi-tenant:

- [ ] Fresh bootstrap with different name + members + profiles → works with zero code changes
- [ ] Platform update on an existing instance via `adminme migrate` preserves data

---

## TONE, DECISIONS, USING YOUR POWER

**Tone.** The platform is warm, decisive, opinionated, brief. Built-in personas reflect that. Comments: minimal, intent not mechanics. Docs: direct, short paragraphs, no filler. Commit messages: present tense, describe why. Skill descriptions: actionable one-liners. README voice: thoughtful colleague, not corporate onboarding. CLI output: terse + colored; no emoji spam. Error messages: what went wrong + what to do next; never blame the user.

**Ask vs decide.**

Ask when:
- Security posture could go two ways (err restrictive; ask to relax)
- OpenClaw / Plaid / BlueBubbles / Tailscale / EventKit docs are ambiguous in a way affecting correctness
- A profile pack's behavior has mental-health implications
- An integration requires credentials the user must provide
- Something in this spec contradicts something in CONSOLE_PATTERNS.md, CONSOLE_REFERENCE.html, REFERENCE_EXAMPLES.md, or Hearth — flag, propose resolution
- You need to invent a new event type or projection that isn't in this spec

Decide (with rationale in DECISIONS.md and commit messages) when:
- Naming, file layouts, test structures
- Default values for non-security-sensitive config
- Which Python/Node libraries to use (prefer stdlib + pinned minimal deps)
- Internal code structure
- UI component composition within an established design system
- Tradeoffs between test thoroughness and build time (err toward thoroughness)

**Use all your power.** You are Claude Code Opus 4.7.

- **Local doc mirror (`docs/reference/`)** — read the mirrored docs before writing code that depends on OpenClaw, Plaid, BlueBubbles, Google APIs, Textual, SQLCipher, sqlite-vec, CalDAV, aiosqlite. Do not rely on training data for specifics — these APIs change. Do not attempt to WebFetch the original URLs directly; the sandbox will return 403 `host_not_allowed` for non-GitHub hosts. Apple EventKit content is a documented gap (see `docs/reference/_gaps.md`).
- **Bash** — run tests iteratively; run linters; run the wizard end-to-end against fixtures.
- **File editing** — iterate on code; don't produce giant single commits.
- **Task decomposition** — plan each phase's files before writing; write tests first where reasonable.
- **Running code** — compile + test each component before moving on.
- **Debugging** — if something doesn't work, dig in; don't paper over.

**Do not be lazy.** This is a platform with 10-year horizons. Cutting corners in Phase 1 compounds into rewrites by Phase 14. If a phase takes 3x longer than estimated, fine. If a phase takes 3x shorter, you missed something — go back and verify.

**Work phase by phase. Stop between phases.** Non-negotiable. After each phase commit: produce a report, show test results, summarize what was built and what was skipped or flagged. Wait for explicit human "continue" before proceeding.

**Prefer deterministic over LLM.** Encode rules as code + config, not prompts. LLM is for fuzzy composition (drafts, digests, classifications, extractions).

**Prefer propose/commit over direct-write.** LLM proposes; humans commit.

**Prefer events over state.** Event log is the truth; projections are views.

**Prefer extensibility over features.** If you catch yourself writing a switch on entity type, or an `if member_name == "James"`, or an embedded SQL query in pipeline code — stop. Something wants to be an open enum, a plugin table, a Party attribute, or a projection.

**Prefer simplicity over cleverness.** Simple attracts contributors. Clever attracts bugs.

---

## ONE MORE REMINDER

**The first family this runs for is the Stice family. Their assistant is Poopsy. But the code doesn't know them.**

When James runs `adminme bootstrap`:
- He types "Poopsy" → writes to his instance's `persona.yaml`
- He types "James, Laura, Charlie, [baby]" → emits `party.created` events + `membership.added` into his event log
- He picks `adhd_executive` for himself, `minimalist_parent` for Laura, `kid_scoreboard` for Charlie, `ambient_entity` for baby → writes profile assignments
- He enters credentials → 1Password references
- He answers seed questions → emits events that project into his xlsx workbooks + CRM tables
- He pairs channels → writes his instance's `channels.yaml`

Poopsy is born in his `~/.adminme/`. Poopsy is not in the platform repo. The platform repo has tests that fail if Poopsy or the Stices ever appear in it.

When James's sister runs the same bootstrap, she gets **Goose** or whatever she names hers. Different name. Different household. Different Parties. Different event log. Same platform. Zero conflict.

---

## BEGIN

**Do not start from this section.** This spec is a reference document. You are driven by the prompt sequence in `prompts/PROMPT_SEQUENCE.md`.

The prompt sequence opens with:

1. **Prompt 00** — preflight (verify your sandbox + repo state).
2. **Prompt 00.5** — populate `docs/reference/` with mirrored external documentation (GitHub-first).
3. **Prompt 01** — read all five artifact files + `docs/reference/openclaw/`, produce `docs/openclaw-cheatsheet.md` and `docs/architecture-summary.md`.
4. **Prompt 01b** — produce `docs/SYSTEM_INVARIANTS.md` (the cross-cutting contracts document).
5. **Prompt 02** — scaffold the repo structure (what this spec's PHASE 0 describes, but narrower).

From prompt 02 onward, the sequence maps roughly to this spec's PHASE PLAN but with finer granularity, explicit stop conditions, verification commands, and four architectural-safety checkpoints (01b, 07.5, 10d, 14e, 15.5).

**If you are reading this spec directly without a prompt, stop. Find the next prompt in the sequence and follow it instead.**

Use your full toolset. Test as you go. Commit after every phase. Stop between phases. Never invent; ask when unclear; decide with rationale when the call is yours.

The CRM is the spine. The event log is the source of truth. The platform is tenant-agnostic. Observation mode is the default. Privileged data never leaves its owner's scope. Children are not users.

Build well.
