# AdministrateMe — mental model

_Read this before reading anything else._

This document is the mental model of AdministrateMe. It explains what the system **is**, what is **core** to it, and what is **connected** to it. It is the first read for any human or AI agent joining the project, and it is the reference for catching architectural drift before it becomes spec drift.

If anything in this document conflicts with `docs/architecture-summary.md`, `ADMINISTRATEME_BUILD.md`, or any other spec, **this document wins until reconciled.** Spec drift away from the binding mental model is the failure mode this document exists to prevent.

---

## In one paragraph

AdministrateMe is a household-scale, single-machine, append-only event log (`~/.adminme/data/events.db`) — SQLCipher-encrypted, owner-scope-partitioned, Pydantic-validated — fed by a configurable set of source adapters (Gmail, Calendar, Plaid, Reminders, iMessage, Apple Notes via per-member bridges, Voice Notes, Obsidian, etc., plus connector packs for any other knowledge system the household uses), enriched by a configurable set of reactive and proactive pipelines (identity resolution, noise filtering, commitment extraction, thank-you detection, recurrence extraction, artifact classification, relationship summarization, closeness scoring, reminder dispatch, morning digest, paralysis detection, reward dispatch, CRM gap nudges, coparent brief, graph mining, plus pack-extension), shaped into 12 deterministic projections (parties, interactions, artifacts, commitments, tasks, recurrences, calendars, places-assets-accounts, money, vector-search, xlsx workbooks, member-knowledge), and accessed by family members through a Node console + four Python product APIs + an OpenClaw assistant — all gated by Tailscale identity, guardedWrite governance, owner-scope partitioning, and per-source sensitivity floors, with the entire knowledge state recoverable at any time by replaying the log from event zero.

That sentence is the system. Everything else is layering, naming, or bolt-on.

---

## The picture

```
                              ┌─────────────────────────────────┐
                              │         FAMILY MEMBERS          │
                              │  James, Laura, Charlie, ...     │
                              │  via iPhone, Mac, iPad, web     │
                              └────────────────┬────────────────┘
                                               │
                                               │  tailnet identity
                                               │  binds owner_scope
                                               ▼
   ╔═════════════════════════════════════════════════════════════════════╗
   ║                                                                     ║
   ║   ┌───────────────── TOUCH-AND-USE LAYER ─────────────────┐         ║
   ║   │                                                        │         ║
   ║   │   Tailscale  (identity, every device)                  │         ║
   ║   │   OpenClaw  :18789  (assistant, channels, slash, cron) │         ║
   ║   │   Console  :3330  (Next.js — the human surface)        │         ║
   ║   │   guardedWrite  (3-layer: allow → govern → rate-limit) │         ║
   ║   │   Product APIs (FastAPI, loopback):                    │         ║
   ║   │       :3331 core         :3334 comms                   │         ║
   ║   │       :3332 automation   :3335 capture                 │         ║
   ║   │       :3337 bridge   ← only non-loopback, post-amend   │         ║
   ║   │                                                        │         ║
   ║   └────────────────────────────┬───────────────────────────┘         ║
   ║                                │                                     ║
   ║                                │  reads projections,                 ║
   ║                                │  writes via guardedWrite            ║
   ║                                ▼                                     ║
   ║                                                                     ║
   ║   ┌──────────── SORT-AND-MAINTAIN LAYER ─────────────┐               ║
   ║   │                                                   │               ║
   ║   │   Reactive pipelines (L4)                         │ ◄── ┐         ║
   ║   │      identity, noise, commitment, thank_you,      │     │         ║
   ║   │      recurrence, artifact, relationship,          │     │         ║
   ║   │      closeness, reminder_dispatch                 │     │         ║
   ║   │                                                   │     │         ║
   ║   │   Proactive pipelines + scheduler                 │     │         ║
   ║   │      morning_digest, paralysis_detection,         │     │         ║
   ║   │      reward_dispatch, crm_surface,                │     │         ║
   ║   │      custody_brief, graph_miner                   │     │         ║
   ║   │                                                   │     │         ║
   ║   │   Skill runner   (wraps OpenClaw skill HTTP)      │     │         ║
   ║   │                                                   │     │         ║
   ║   │   Projections (L3) — 12:                          │     │         ║
   ║   │      parties, interactions, artifacts,            │     │         ║
   ║   │      commitments, tasks, recurrences,             │     │         ║
   ║   │      calendars, places_assets_accounts, money,    │     │         ║
   ║   │      vector_search, xlsx_workbooks,               │     │         ║
   ║   │      member_knowledge ← post-Conception-C         │     │         ║
   ║   │                                                   │     │         ║
   ║   │   Event bus   (asyncio pub/sub +                  │     │         ║
   ║   │                durable per-subscriber offsets)    │     │         ║
   ║   │                                                   │     │         ║
   ║   └──────────────────────────┬────────────────────────┘     │         ║
   ║                              │                              │         ║
   ║                              │  subscribes,                 │         ║
   ║                              │  projects                    │         ║
   ║                              ▼                              │         ║
   ║                                                             │         ║
   ║   ┌──────────────────── BEDROCK ──────────────────────┐     │         ║
   ║   │                                                    │     │         ║
   ║   │           ┌──────────────────────────┐             │     │         ║
   ║   │           │                          │             │     │         ║
   ║   │           │     THE EVENT LOG        │             │     │         ║
   ║   │           │     ~/.adminme/data/     │             │     │         ║
   ║   │           │         events.db        │             │     │         ║
   ║   │           │                          │             │     │         ║
   ║   │           │   append-only            │             │     │         ║
   ║   │           │   SQLCipher-encrypted    │             │     │         ║
   ║   │           │   owner-scope-partitioned│             │     │         ║
   ║   │           │   Pydantic-validated     │             │     │         ║
   ║   │           │   ULID-keyed             │             │     │         ║
   ║   │           │                          │             │     │         ║
   ║   │           │   single writer:         │             │     │         ║
   ║   │           │   EventStore.append()    │             │     │         ║
   ║   │           │                          │             │     │         ║
   ║   │           └──────────────▲───────────┘             │     │         ║
   ║   │                          │                         │     │         ║
   ║   │   Built on: Python 3.11 + asyncio                  │     │         ║
   ║   │             SQLite + SQLCipher + sqlite-vec        │     │         ║
   ║   │             Pydantic v2                            │     │         ║
   ║   │             ULID time-sortable IDs                 │     │         ║
   ║   │                                                    │     │         ║
   ║   └──────────────────────────┼─────────────────────────┘     │         ║
   ║                              │                               │         ║
   ║                              │  EventStore.append(envelope)  │         ║
   ║                              │                               │         ║
   ╚══════════════════════════════│═══════════════════════════════│═════════╝
                                  │                               │
                                  │ typed events                  │  attaches at L4
                                  │ owner-scoped, sensitivity-    │  via pack registry
                                  │ stamped at ingest             │
                                  │                               │
       ┌──────────────────────────┴─────────────────────────┐  ┌──┴────────────────────────┐
       │                                                    │  │                            │
       │            SOURCE BOLT-ONS  (L1 adapters)          │  │  CAPABILITY BOLT-ONS       │
       │                                                    │  │                            │
       │  ┌──── CENTRAL-SIDE ──────┐  ┌── BRIDGE-SIDE ───┐  │  │   Pipeline packs           │
       │  │  (CoS Mac Mini)        │  │ (per-member      │  │  │   Skill packs              │
       │  │                        │  │  Mac Mini)       │  │  │   Profile packs            │
       │  │  Gmail                 │  │                  │  │  │   Persona packs            │
       │  │  Google Calendar       │  │  Apple Notes     │  │  │                            │
       │  │  Google Drive/Contacts │  │  Voice Notes     │  │  │   Each pack registers      │
       │  │  Plaid                 │  │  Obsidian opt-in │  │  │   via entry-point and is   │
       │  │  Apple Reminders       │  │                  │  │  │   independently install-   │
       │  │  Apple Contacts        │  │  ─ all emit via  │  │  │   able and disable-able.   │
       │  │  CalDAV                │  │    :3337 bridge  │  │  │                            │
       │  │  iOS Shortcuts hooks   │  │    ingest, with  │  │  │   Adding capability does   │
       │  │  iMessage (BlueBubbles)│  │    Tailscale-    │  │  │   not change the core.     │
       │  │  Telegram, Discord …   │  │    identity      │  │  │                            │
       │  │                        │  │    stamp         │  │  │                            │
       │  └────────────────────────┘  └──────────────────┘  │  └────────────────────────────┘
       │                                                    │
       │  + connector packs  (Notion, Logseq, Roam, Strava, │
       │    Health, …)  install on bridges or centrally     │
       │                                                    │
       └─────────────────────────────▲──────────────────────┘
                                     │
                                     │  one adapter per source —
                                     │  reads, translates, emits
                                     │
       ┌─────────────────────────────┴──────────────────────┐
       │                                                    │
       │                  EXTERNAL SOURCES                  │
       │                                                    │
       │  email · calendars · contacts · files · banks ·    │
       │  SMS · iMessage · voice memos · notes · vaults ·   │
       │  reminders · third-party calendars · webhooks ·    │
       │  sensors · device data · anything a family member  │
       │  captures or any service emits                     │
       │                                                    │
       └────────────────────────────────────────────────────┘
```

**Core** is everything inside the double-walled box. **Connected** is everything outside it.

---

## How the system works, in three flows

**Inbound (data path).** External sources emit. L1 adapters read those sources and translate them into typed events. Bridge-side adapters (Apple Notes, Voice Notes, Obsidian) emit over the tailnet to the `:3337 bridge` ingest endpoint, where Tailscale identity stamps the event's `owner_scope`. Central-side adapters emit directly into the event log via `EventStore.append()`. Everything ends up in the same log: one append-only SQLCipher-encrypted SQLite table, every row a Pydantic-validated typed envelope.

**Enrichment (derivation path).** L4 pipelines subscribe to events on the bus and emit derived events. `identity_resolution` turns "an unknown phone number sent a message" into "this is the same person as party `pty_xxx`." `commitment_extraction` turns "James said 'I'll pick up groceries Tuesday'" into a `commitment.proposed` event. Pipelines never write projections directly; they emit events and projections consume them. When a pipeline needs model intelligence, it goes through the skill runner, which wraps OpenClaw's HTTP API and emits a `skill.call.recorded` event with full provenance.

**Outbound (read path).** L3 projections shape the log into queryable read-models. There are 12: `parties`, `interactions`, `artifacts`, `commitments`, `tasks`, `recurrences`, `calendars`, `places_assets_accounts`, `money`, `vector_search`, `xlsx_workbooks`, `member_knowledge`. Family members access the system through the Node console (`:3330`) and the four Python product APIs (`:3331-:3336`); the console reads projections and writes back through `guardedWrite` (a three-layer allow → govern → rate-limit gate). OpenClaw is the assistant substrate — it owns channels, slash commands, standing orders, approval gates. When James types `/digest` in iMessage, OpenClaw routes to AdministrateMe's handler. When the morning-digest standing order fires at 06:30, OpenClaw is the scheduler.

The whole knowledge state is recoverable at any time by replaying the log from event zero.

---

## What is core

Thirteen things, in three groups. Take any one of them away and the system cannot be built as designed.

**The bedrock (5).** Python 3.11 + asyncio. SQLite + SQLCipher + sqlite-vec. Pydantic v2. ULIDs (time-sortable IDs). The `EventStore` append-only primitive. These five are what makes the log a trustable, queryable, replayable substrate. Take any one away and the log is no longer the kind of thing the rest of the system can be built on.

**The sort-and-maintain layer (5).** The event bus (asyncio pub/sub + durable per-subscriber offsets). Projections (L3 — the 12 deterministic pure-function read-models). Reactive pipelines (L4 — event-subscribed enrichment). The skill runner (the seam to OpenClaw for model-based decisions, with provenance). Proactive pipelines + scheduler (time-based enrichment registered as OpenClaw standing orders). These five turn the log from a pile of events into a queryable knowledge system.

**The touch-and-use layer (3).** Tailscale (identity, per-device, binds `owner_scope` at every entry point). The Node console + four Python product APIs + `guardedWrite` (the human-facing read/write surface, with governance). OpenClaw (the assistant substrate at `:18789`, the gateway for channels, skills, slash commands, and standing orders). These three are what makes the system accessible to family members.

---

## What is connected

Everything outside the double-walled box. Two families.

**Source bolt-ons (L1 adapters)** — every connector that feeds external data into the event log. Central-side adapters run on the CoS Mac Mini (Gmail, Plaid, BlueBubbles plugin, Google Calendar, etc.). Bridge-side adapters run on per-member bridge Mac Minis (Apple Notes, Voice Notes, Obsidian — see `04-architecture-amendment-knowledge-vaults-and-member-bridges.md` and D17). Connector packs extend either side (Notion, Logseq, Roam, Strava, Health, etc., shipped as packs and installed per-bridge or centrally). Adding or removing an adapter does not change the core; it changes what data flows in.

**Capability bolt-ons** — every pipeline pack, skill pack, profile pack, and persona pack. These extend what the core does *with* the events it has. Pipeline packs add new enrichment behaviors. Skill packs add new model-based capabilities. Profile packs configure per-member preferences. Persona packs configure how AdministrateMe speaks (warmth, terseness, professionalism dial). Each registers via an entry-point and is independently install-able and disable-able. Adding or removing a capability does not change the core; it changes what the system *does* with the data.

The 12 specific projections sit ambiguously between core and connected. The **projection layer** (L3) is core — without it, the log is unreadable at scale. The **specific list** of 12 is tuned-but-stable: a 12th was added by the Conception-C amendment, and a future ADR could add more. Treat the list as core in practice, with the understanding that growth happens through architectural amendment, not through pack installation.

---

## The test

For any element of the system, ask:

> **If you remove it, does the core still work?**
>
> If yes — it is connected.
> If no — it is core.

Worked examples:

- **Apple Notes adapter** — remove it, system still works (just one less knowledge source). **Connected.**
- **The event log** — remove it, system is gone. **Core.**
- **`commitment_extraction` pipeline** — remove it, system still works (just doesn't auto-extract commitments). **Connected.**
- **The skill runner** — remove it, every enrichment that needs model intelligence breaks. **Core.**
- **Tailscale** — remove it, no auth model. **Core.**
- **Pydantic** — remove it, log payloads are unverifiable. **Core.**
- **The Capture product (`:3335`)** — remove it, family members lose the read surfaces over knowledge and CRM, but the underlying knowledge layer continues to ingest, project, and serve other surfaces. **Connected** in practice, though it is the primary surface for several core read patterns.
- **A specific projection (e.g. `parties`)** — remove it, the surfaces that read from it break, but the log and the rest of the projections continue. The projection layer itself is core; this specific projection is core-in-practice but architecturally a tuned member of a stable set.

The test is the structural defense against drift. If somebody describes a *surface* as a *source* (e.g., "Capture is where capture happens"), or describes an *adapter* as part of the *core* (e.g., "Apple Notes is part of the system"), or describes a *pipeline* as the place where canonical state lives (e.g., "tasks are owned by `commitment_extraction`"), that is drift. The test gives a clean answer in all directions.

---

## What this doc is *not*

This doc is the **mental model**. It is not the spec. The spec is in `ADMINISTRATEME_BUILD.md`, `docs/architecture-summary.md`, `docs/SYSTEM_INVARIANTS.md`, `docs/DECISIONS.md`, and `ADMINISTRATEME_DIAGRAMS.md`. The spec contains the row schemas, the exact event types, the exact router paths, the exact pipeline manifests. **Read the spec for what to build. Read this doc for what the system is.**

This doc is also not the prompt sequence (`prompts/PROMPT_SEQUENCE.md`) or the build log (`docs/build_log.md`). Those are operational artifacts about *how* the system gets built, prompt by prompt. This doc is about *what* gets built, regardless of build order.

---

## How to use this doc

**For humans:** read it once before reading any other doc in the project. Re-read whenever the architecture feels confusing — the answer to "what is this thing" is almost always in the diagram.

**For Partner sessions (AI agents):** this doc is part of the mandatory orientation read at the start of every session, alongside `partner_handoff.md` and `qc_rubric.md`. It anchors what the system *is* before any specific spec or prompt is read. If a future spec, prompt, or memo describes the system in a way that conflicts with this doc, **flag the conflict to James before proceeding** — that is the drift-detection signal this doc is designed to surface.

**For Claude Code sessions:** Claude Code does not read this doc directly. The Partner sessions that produce build prompts read it. Build prompts inherit the mental model through the Partner's framing.

---

## When this doc is updated

This doc is updated when the architectural mental model changes — i.e., when the answer to "what is core, what is connected" shifts. Recent example: the Conception-C architecture amendment of 2026-04-29 added the per-member bridge Mac Mini as a new architectural unit (the `:3337 bridge` ingest endpoint, the `member_knowledge` projection, the bridge-side adapter family). That amendment updated this doc.

Routine spec changes (new event types, new pipeline packs, new projection details) do **not** update this doc. Those flow through `BUILD.md` / `architecture-summary.md` / `DECISIONS.md`. This doc only updates when the **shape of core vs. connected** moves.

If this doc ever feels out of date, check the most recent architecture-amendment memo in `docs/` (the `04-architecture-amendment-*.md` series) — that's where the canonical decision lives. This doc reflects those decisions, not the other way around.

---

## End

If you're a future Partner reading this for the first time: this is what AdministrateMe is. Read the spec next. If something in the spec feels like it doesn't fit the mental model above, that is the signal to pause and surface to James before proceeding. That is the discipline this doc exists to encode.
