# Tier C architecture amendment — Personal data layer (five-category adapter taxonomy)

**Author:** AdministrateMe Build Supervision Partner
**Date:** 2026-04-29
**Disposition:** Architecture amendment (Amendment-2; expansion of Conception-C cycle per PM-30)
**Verdict:** **AMEND.** The runtime-substrate framing of L1 (channel adapters / data adapters / knowledge-source adapters) is replaced with an epistemic-role taxonomy of five categories, with runtime as an orthogonal secondary axis. Multi-capability adapters are first-class. Adapter authoring becomes a fill-in-the-form exercise via a category-specific scaffold, not a one-off artisan process. Lists become a 13th projection (memo §5). Apple Calendar becomes v1 dual-deployment (memo §6). Apple + Google Contacts become v1 (memo §7). Home Assistant becomes the Cat-C+E reference implementation (memo §8). Four single-purpose PRs land the correction (memo §9).
**Authority:** James, 2026-04-29-B (multi-turn out-of-band consultation) — eight binding decisions D18–D25; six new PMs PM-30 through PM-35; twelve UTs UT-19 through UT-30 surfaced and tracked in `docs/partner_handoff.md`.
**Relationship to prior memo:** This memo sits alongside `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md` (Conception-C narrow cycle, FULLY LANDED on main as of 2026-04-29). It does not replace memo 04. The personal-knowledge category (Cat-D) in this memo is the same architectural unit memo 04 introduced as `subkind: knowledge-source`; the present memo generalizes the framing to all of L1.

---

## 1. Five-category adapter taxonomy

### 1.1 The framing change

Memo 04 introduced a third runtime variant for L1 adapters — bridge-side knowledge adapters running on per-member Mac Minis — alongside the two pre-existing runtime variants (OpenClaw-plugin channel adapters; standalone-Python data adapters). That introduction was correct for the narrow problem it solved (Apple Notes ingestion), but it left the L1 taxonomy organized by **runtime substrate** — *where* the adapter runs — when the more decision-relevant axis is **epistemic role** — *what kind of knowledge the adapter brings into the system*.

The runtime-substrate framing produces the wrong groupings for design decisions. Plaid (a standalone-Python adapter) and Stelo CGM (also a standalone-Python adapter) live in the same runtime bucket, but they make completely different demands on the system: Plaid's transactions become source data for the `money` projection and feed `transaction.classification` pipelines; Stelo's blood-glucose readings would feed a future `health` projection with `sensitivity_default: privileged` and entirely different consumer pipelines. Conversely, Apple Reminders (a standalone-Python adapter on the central Mac Mini) and Google Tasks (also standalone-Python on the central Mac Mini) live in the same runtime bucket as Plaid, but they are bidirectional state mirrors with round-trip semantics — closer in shape to Apple Calendar (also a state mirror) than to Plaid.

The decision-relevant axis is therefore **epistemic role**: what does the adapter contribute, who is the authoritative source, what direction does data flow, what projection does the data feed, what's the default sensitivity, what's the default owner-scope. Per D19 (2026-04-29-B), Amendment-2 reorganizes the L1 inventory along five epistemic categories. The runtime axis (central / bridge / dual-deployment) becomes a secondary, orthogonal dimension (§2).

### 1.2 The five categories

Each category is defined by its epistemic role, with a distinguishing property that separates it from adjacent categories. Category letters (A–E) are mnemonic only — they have no precedence ordering.

#### (a) Cat-A — Communication

**Definition.** Adapters that bring people-talking-to-people conversation into the event log.

**Distinguishing property.** Conversation between humans is the unit of work. The adapter's job is to faithfully represent both inbound (received) and outbound (sent) sides of human-to-human exchanges, preserving thread structure and party identity.

**Bidirectionality default.** Bidirectional required. Inbound-only is structurally incomplete (you can't run a CRM without seeing what *you* said). Outbound-only is meaningless (no one to talk to).

**Canonical members.** BlueBubbles/iMessage, Telegram, Discord, Gmail, SMS (Twilio) — but see §4 for SMS recategorization to Cat-E per D21.

**Why an integrator would add one.** A new chat or messaging surface the household uses (a workplace Slack-DM bridge, a regional messaging app, an email-list provider) needs to feed `interactions` for the CRM to be complete.

**Contract — events emitted.** `messaging.received@v1` (inbound), `messaging.sent@v1` (outbound), `conversation.turn.recorded@v1` (when both sides bridge through OpenClaw memory). Thread/conversation metadata included where upstream supports it.

**Contract — events consumed.** `messaging.send_requested@v1` (to drive outbound through OpenClaw's channel-send API per [§8.5]).

**Contract — projections fed.** `interactions` (primary), `parties.identifiers` (when a new party is observed), `vector_search` (for content embedding, modulo sensitivity gating).

**Contract — sensitivity default.** `sensitivity_default: normal`. Specific channels can override (e.g. a therapist-coordination Telegram thread might be marked `sensitive` per-conversation by skill output, not per-adapter).

**Contract — owner-scope default.** `owner_scope_default: shared:household` for household-shared accounts (the family iMessage line); `private:<member_id>` for member-scoped accounts (a member's personal Gmail). Operator-overridable at install per §2.5.

#### (b) Cat-B — External-State-Mirror

**Definition.** Adapters that mirror state the principal maintains in an external system. AdministrateMe is an SSOT mirror that **reads from and writes back to** the external system.

**Distinguishing property.** Round-trip semantics are central. The external system is the source of truth that the principal directly maintains via its native UI (Reminders.app, Google Tasks, Apple Calendar, etc.); AdministrateMe both observes and contributes to that state. Conflicts are possible and must be handled.

**Bidirectionality default.** Bidirectional where upstream API allows. Asymmetric write-capabilities are explicit per-operation (§2.3) — not a boolean. Some Cat-B adapters are add-only (Things 3 v1 export), some are toggle+add only (Apple Notes-checklists per D18 / UT-19), some are full CRUD (Apple Reminders, Google Tasks).

**Canonical members.** Apple Reminders, Google Tasks, Apple Calendar, Google Calendar, CalDAV (deferred to v2 per §4 / D25), Apple Contacts, Google Contacts. Cross-cat extension: Apple Notes-checklists (Cat-B + Cat-D — see §2.2).

**Why an integrator would add one.** The household uses a task / calendar / contacts / notes system not yet bundled, and they want the system to be a participant in that data — both reading state and writing state back from CoS-side workflow.

**Contract — events emitted.** Per the entity kind: `list.added@v1` / `list.updated@v1` / `list.deleted@v1` / `list_item.added@v1` / `list_item.updated@v1` / `list_item.completed@v1` / `list_item.deleted@v1` for list-shaped Cat-B adapters (per D18). Calendar-shaped: `calendar_event.added@v1` / `.updated@v1` / `.cancelled@v1`. Contacts-shaped: `party.identifier_observed@v1` / `party.profile_updated@v1`. Each event carries `external_<entity>_id` and `source_kind` discriminators per PM-31.

**Contract — events consumed.** Per the entity kind: `list_item.create_requested@v1`, `list_item.toggle_completion_requested@v1`, etc., gated by the adapter's declared `write_capabilities` list (§2.3).

**Contract — projections fed.** Lists adapters feed `lists` (the new 13th projection per D18); calendar adapters feed `calendars`; contacts adapters feed `parties.identifiers`. Cat-B projections universally include the `sharing_model` discriminator column per §2.4.

**Contract — sensitivity default.** `sensitivity_default: normal`. Lists named "Health" or marked privileged in upstream mark per-row at projection time.

**Contract — owner-scope default.** Depends on the deployment configuration. Shared lists / shared calendars / shared contact groups land at `shared:household` when discovered through household-shared accounts; private items at `private:<member_id>`. Sharing-model discriminator on each row distinguishes which.

#### (c) Cat-C — Inbound-Only Data

**Definition.** Adapters that ingest external data the principal does not directly maintain. Read-only from AdministrateMe's side; the external system is authoritative.

**Distinguishing property.** No write-back path. The principal's relationship to the data is observation, not maintenance. Plaid is the canonical case: the principal does not write transactions back to Plaid; Plaid is where transactions live and AdministrateMe reads.

**Bidirectionality default.** Inbound-only by definition. An adapter that reads a system AND writes back to it is Cat-B, not Cat-C — that's the discriminator.

**Canonical members.** Plaid (v1 — financial data ingestion). Stelo / Dexcom CGM (v2 / community pack — health telemetry, per UT-25). Apple Health (v2 / community pack — read-only HealthKit aggregate stream). Weather (v2 if needed standalone; likely inline within other surfaces). Market data (v2 if needed beyond Plaid's coverage).

**Why an integrator would add one.** The household has a domain of life with rich external state — health metrics, banking, brokerage telemetry, vehicle telemetry — and they want that state visible to CoS workflow without paying the round-trip cost of Cat-B.

**Contract — events emitted.** Per the data kind: `money.transaction_observed@v1`, `money.balance_observed@v1` (Plaid). Health-shaped: `health.metric_observed@v1` (future). Weather/market: `observation.<kind>@v1`.

**Contract — events consumed.** None (no write-back path).

**Contract — projections fed.** `money` (Plaid), `vector_search` (for searchable transaction memos), and future `health` projection for Cat-C health adapters.

**Contract — sensitivity default.** Varies by sub-domain. Financial Cat-C: `sensitivity_default: sensitive`. Health Cat-C: `sensitivity_default: privileged` (per [§13.6 — privileged-sensitive sources never embed in non-privileged search index]). Weather/market: `normal`.

**Contract — owner-scope default.** `private:<member_id>` for personal accounts (a member's individual brokerage); `shared:household` for joint accounts (the household checking account on Plaid). Operator-overridable at install.

#### (d) Cat-D — Personal-Knowledge

**Definition.** Adapters that ingest the principal's own knowledge work — prose notes, voice notes, personal vaults, personal annotations.

**Distinguishing property.** The data is the member's own thinking, captured in the member's own preferred tool. Owner-scope is **always per-member** (`private:<member_id>`); cross-member sharing is opt-in via downstream pipeline (vector-search index, graph-miner) rather than at the adapter layer. This is the category memo 04 introduced via the bridge runtime; per D19, it gets first-class category status here.

**Bidirectionality default.** Inbound-only by default. Cat-D adapters can extend with writeback for cross-cat capabilities (Apple Notes-checklists is Cat-D for prose ingestion AND Cat-B for checklist round-trip — see §2.2), but pure-D defaults to read-only.

**Canonical members.** Apple Notes (prose), Apple Voice Memos, Obsidian (opt-in per-member). Connector-pack slot: Notion, Roam, Day One, Bear, Logseq, Readwise — all community packs post-Phase-A.

**Why an integrator would add one.** A household member uses a knowledge tool not yet bundled (Logseq, Roam, Bear) and wants their personal notes searchable and pipeline-feedable into the household system, while keeping the source-of-truth in their tool of choice.

**Contract — events emitted.** `note.added@v1`, `note.updated@v1`, `note.deleted@v1`, `voice_note.added@v1`. Each carries `source_kind` (apple_notes, voice_memos, obsidian, notion, etc.) and `external_<entity>_id`.

**Contract — events consumed.** None for pure-D. Cross-cat extensions (e.g., Notes-checklists writeback) consume Cat-B-shaped requests via the adapter's secondary capability declaration.

**Contract — projections fed.** `member_knowledge` (the per-member 12th projection added by memo 04). Some `vector_search` ingestion modulo per-member privileged-content gating per [§6.12].

**Contract — sensitivity default.** `sensitivity_default: sensitive`. Personal notes are presumed sensitive unless explicitly marked otherwise. Per-note privileged tagging at the adapter or downstream skill layer.

**Contract — owner-scope default.** `private:<member_id>` always. Bridge runtime stamps owner_scope from Tailscale identity per memo 04 §2.2 — the bridge cannot lie about whose data it is submitting. Owner-scope is **not** overridable at install for Cat-D — privacy-first invariant.

#### (e) Cat-E — Outbound-Action

**Definition.** Adapters that execute actions in the world. The system *does something* through them, beyond the read-and-record pattern.

**Distinguishing property.** Side effects in the external world. Inbound is minimal — only what's needed to confirm the action happened (a service-call response, a delivery confirmation, a transaction notification). The action itself is the unit of work.

**Bidirectionality default.** Outbound-primary, with minimal-confirmation inbound. The discriminator from Cat-B is symmetry: Cat-B is roughly equally read and write; Cat-E is heavily skewed to write with read-only-as-confirmation.

**Canonical members.** Home Assistant (v1, Cat-C+E reference implementation per D24 — see §2.2 for multi-capability shape). Twilio voice (v2, voice-call-out). Lob (v2 community pack, postal mail dispatch — deferred per D25). Privacy.com (v2 community pack, virtual-card issuance — deferred per D25). Brokerage trading (v2+, high-stakes outbound).

**Why an integrator would add one.** The household wants the system to act, not just observe. Smart-home control, automated phone calls, postal letters, virtual-card creation, scheduled trades — all are outbound actions the system needs to execute on the principal's behalf, with appropriate gating.

**Contract — events emitted.** `action.requested@v1`, `action.executed@v1`, `action.failed@v1`. Plus `observation.suppressed@v1` when observation mode is active (per §2.4 and [§14.x] — same pattern messaging outbound uses today).

**Contract — events consumed.** `action.<kind>_requested@v1` per the specific action verb (`ha.service_call_requested@v1`, `twilio.voice_call_requested@v1`, etc.).

**Contract — projections fed.** Generally none — Cat-E adapters don't produce queryable state, they produce side effects. Exception: the inbound-confirmation half of multi-capability adapters (HA's Cat-C state-read seam) feeds whatever projection that capability's events serve.

**Contract — sensitivity default.** `sensitivity_default: normal` for the Cat-E events themselves. The decision-input events (commitments, tasks, etc.) carry their own sensitivity.

**Contract — observation-mode-required.** `observation_mode_required: true` is the default for Cat-E adapters. High-stakes Cat-E (brokerage trading, outbound voice calls) cannot ship without observation-mode integration. Per D24, HA's service-call seam suppresses to `observation.suppressed` exactly the way messaging outbound does.

**Contract — owner-scope default.** `shared:household` for household devices (the front-door lock, the living-room lights); `private:<member_id>` for personal devices (a member's individual phone). Operator-overridable at install.

### 1.3 Mapping the existing build-plan adapters

Every adapter named in the v1 build plan, mapped to its category. This is the inventory after the §4 cleanup; deferred / removed items are noted as such.

| Adapter | Category | Runtime | Notes |
|---|---|---|---|
| Gmail | Cat-A | central | Existing v1. Email as conversation. |
| BlueBubbles / iMessage | Cat-A (OpenClaw plugin variant) | central | Existing v1. Channel via OpenClaw plugin per [§8.3]. |
| Telegram | Cat-A (OpenClaw plugin variant) | central | Existing v1. |
| Discord | Cat-A (OpenClaw plugin variant) | central | Existing v1. |
| Twilio SMS | Cat-E | central | **Recategorized** per D21. Outbound fallback when iMessage delivery fails; inbound deferred to v2. |
| Plaid | Cat-C | central | Existing v1. Financial data ingestion, read-only. |
| Apple Reminders | Cat-B | dual (central + bridge) | Existing v1. Per D18 / D22 dual-deployment shape: central variant on CoS Apple ID, bridge variant per member Apple ID. |
| Google Tasks | Cat-B | central | Existing v1, gains Cat-B classification (was previously informally bundled with Reminders). |
| Apple Calendar | Cat-B | dual (central + bridge) | **NEW v1** per D22. Parallel shape to Apple Reminders dual-deployment. Lands in modified prompt 11b. |
| Google Calendar | Cat-B | central | Existing v1. |
| CalDAV (separate) | — | — | **REMOVED from v1** per D25. Apple Calendar covers iCloud, Google Calendar covers Google. v2 community pack if a non-iCloud-non-Google CalDAV server is needed. |
| Apple Contacts | Cat-B | bridge (per-member) | **NEW v1** per D23. Bridge runtime via Contacts.framework on member Mac Mini. Lands in new prompt 11e. |
| Google Contacts | Cat-B | central | **NEW v1** per D23. Central runtime via People API. Lands in new prompt 11e. |
| Apple Notes (prose) | Cat-D | bridge | Existing v1 per memo 04. |
| Apple Voice Memos | Cat-D | bridge | Existing v1 per memo 04. |
| Obsidian | Cat-D | bridge (opt-in) | Existing v1 per memo 04. |
| Apple Notes-checklists | Cat-B + Cat-D (cross-cat) | bridge | **NEW v1** per D18. Multi-capability adapter: prose ingestion (D) + checklist round-trip (B). Toggle+add write-capabilities only per UT-19. Lands in modified prompt 11c-ii. |
| Home Assistant | Cat-C + Cat-E (multi-capability) | central | **NEW v1** per D24. Multi-capability adapter: state-read seam (C) + service-call seam (E). Both seams ship in Phase A. Lands in new prompt 11g. Reference implementation for Cat-E. |
| Google Drive | — | — | **DEFERRED to v2** per D25. Removed from L1 inventory. |
| iOS Shortcuts webhooks | — | — | **REMOVED** per D25. Functionally retired by D17 (knowledge-source ingestion via bridges supersedes the Shortcuts webhook pattern). |
| iCloud (as separate adapter) | — | — | **CLARIFIED** per §4. iCloud is not its own adapter; it is accessed via the Reminders / Notes / iMessage / Calendar / Contacts adapters' use of the relevant Apple ID. |
| Lob | — | — | **DEFERRED to v2 community pack** per D25. |
| Privacy.com | — | — | **DEFERRED to v2 community pack** per D25. |

### 1.4 What this taxonomy is NOT

The five-category taxonomy is **descriptive of epistemic role**, not a runtime substrate, not a security boundary, not a permissions model. Specifically:

- **Not a runtime grouping.** Cat-B has both central and bridge runtimes; Cat-D is bridge-only by current convention but could be central in a hypothetical future where a member chooses to keep their notes on the central Mac Mini (architecturally permitted, just not the current default).
- **Not a sensitivity grouping.** Cat-D defaults to `sensitive`, Cat-C-health defaults to `privileged`, but sensitivity is per-event and per-row, not per-category. The category provides the default; the event payload can override.
- **Not an owner-scope grouping.** Cat-D is per-member-only, but Cat-A through Cat-C and Cat-E all have install-time owner-scope decisions.
- **Not exclusive.** Multi-capability adapters declare a list of capabilities, each with its own category (§2.2). Apple Notes-checklists is genuinely both Cat-D and Cat-B; HA is genuinely both Cat-C and Cat-E.

The purpose of the taxonomy is to anchor design conversations and authoring decisions. When a future integrator asks "what kind of adapter do I need to write for system X," the answer is "what's the epistemic role? Is X a conversation surface, a state mirror, an inbound data feed, a personal knowledge vault, or an action target?" — and the answer determines base class, manifest fields, default sensitivity, default scope, and contract shape.

---

## 2. Two-axis adapter model

### 2.1 Category × runtime, orthogonal

Per D19, the category axis (§1, epistemic role) and the runtime axis (central / bridge / dual-deployment) are orthogonal. Any category can run in any runtime.

The runtime axis values:

- **central** — the adapter runs on the central CoS Mac Mini, alongside the event log and projections. Standalone Python process, or OpenClaw plugin for Cat-A messaging adapters per [§8.3].
- **bridge** — the adapter runs on a per-member Mac Mini bridge per memo 04, emitting events to the central Mac Mini's `:3337 bridge` ingest endpoint. Owner-scope is stamped from Tailscale identity at ingest, not claimed by the adapter.
- **dual-deployment** — the same adapter codebase is deployed in both runtimes simultaneously. The central variant runs on the CoS Apple ID (capturing household-shared lists, calendars); the bridge variant runs on each member's Apple ID (capturing private lists, calendars). Apple Reminders (existing) and Apple Calendar (new per D22) are the dual-deployment cases. The pack manifest declares `runtime: dual` and provides two deployment configurations — addresses UT-21.

Examples of category × runtime cells in current use:

| | central | bridge | dual |
|---|---|---|---|
| **Cat-A** | Gmail; BlueBubbles/iMessage/Telegram/Discord (plugins) | — | — |
| **Cat-B** | Google Tasks, Google Calendar, Google Contacts | Apple Contacts, Apple Notes-checklists (writeback half) | Apple Reminders, Apple Calendar |
| **Cat-C** | Plaid; Home Assistant (state-read half) | — | — |
| **Cat-D** | — | Apple Notes (prose), Voice Memos, Obsidian | — |
| **Cat-E** | Twilio SMS; Home Assistant (service-call half) | — | — |

Empty cells are not architectural prohibitions — they are simply unrealized in v1. A future Cat-A bridge adapter (e.g., a household member's personal Discord that they don't want on the central system's tailnet identity) is architecturally permitted; it just doesn't ship in v1.

### 2.2 Capabilities-as-list (PM-35, UT-26)

A multi-capability adapter declares **a list of capabilities**, not a singleton category, in its pack manifest. Each capability is its own seam with its own write-capability declaration, event-family scope, and sensitivity default.

**Reference cases at amendment time:**

- **Notion** (community pack post-Phase-A): Cat-B database mode (rows in a Notion database read+written as structured records) + Cat-D page mode (free-form pages ingested as personal knowledge).
- **Home Assistant** (v1 reference per D24): Cat-C state-read (entity states observed) + Cat-E service-call (turn_on, turn_off, scene activation).
- **Tesla** (community pack, post-Phase-A): Cat-C telemetry (location, battery, charge state) + Cat-E control (lock/unlock, precondition, summon).
- **Apple Notes-checklists** (v1 cross-cat per D18): Cat-D prose (the body of the note as searchable knowledge) + Cat-B checklist (items within a checklist note as round-trippable list-items per §5).
- **Privacy.com** (deferred to v2 per D25, but illustrative): Cat-E issue (create virtual cards) + Cat-C transaction-notification (observe charges on those cards). In practice Plaid covers transaction observation; Privacy.com's Cat-C seam would only matter for cards Plaid doesn't see.

**Manifest shape (illustrative; canonical schema in §3 framework):**

```yaml
kind: adapter
id: home-assistant
version: 1.0.0
runtime: central
capabilities:
  - kind: cat_c_inbound_data
    event_families: [ha.state_observed, ha.event_observed]
    write_capabilities: []
    sensitivity_default: normal
  - kind: cat_e_outbound_action
    event_families: [ha.service_call_requested, ha.service_call_executed]
    write_capabilities: [service_call, scene_activation]
    sensitivity_default: normal
    observation_mode_required: true
```

Each capability has the same fields a single-category adapter would have — they are declared per-capability, not at the adapter level. The validator (§3) checks that each declared capability's event-families don't overlap with another capability's, and that the runtime is coherent for every declared capability (a single adapter can't have a Cat-D bridge capability and a Cat-A central capability simultaneously without duplicating the deployment).

### 2.3 Asymmetric write-capabilities (UT-27)

Bidirectionality is **not** a boolean. Cat-B and Cat-E adapters declare an explicit per-operation list of write capabilities; operations omitted from the list are not supported.

**Operation vocabulary** (per-category-kind, illustrative):

- For list-shaped Cat-B (Reminders, Tasks, Notes-checklists): `create_list`, `delete_list`, `rename_list`, `share_list`, `create_item`, `update_item_text`, `toggle_completion`, `delete_item`, `reorder_items`.
- For calendar-shaped Cat-B: `create_event`, `update_event`, `cancel_event`, `respond_to_invite`.
- For contacts-shaped Cat-B: `create_contact`, `update_contact_field`, `delete_contact`, `add_to_group`.
- For Cat-E: per-adapter (HA: `service_call`, `scene_activation`; Twilio: `voice_call`, `sms_send`).

**Reference cases:**

- **Things 3** (community pack, post-Phase-A): `write_capabilities: [create_item]`. Add-only — no completion-write, no edit, no delete via the adapter. The principal toggles completion in Things 3 itself; the adapter observes.
- **Apple Notes-checklists** (v1 per D18 / UT-19): `write_capabilities: [create_item, toggle_completion]`. Per James 2026-04-29-B, the toggle+add scope minimizes risk of AppleScript-write-back conflicts (last-writer-wins through iCloud sync is acceptable for these two operations, but problematic for in-place text edits or reorders). No `update_item_text`, no `delete_item`, no `reorder_items`.
- **iCloud Shared Photos** (community pack, post-Phase-A): `write_capabilities: [create_item]`. Add-only — the system uploads, but doesn't delete or edit.
- **Apple Reminders** (existing v1): `write_capabilities: [create_list, create_item, update_item_text, toggle_completion, delete_item]`. Full CRUD.
- **Google Tasks** (existing v1): `write_capabilities: [create_list, create_item, update_item_text, toggle_completion, delete_item]`. Full CRUD.

The validator (§3) checks at install time that the adapter's base class has methods for every declared write-capability. The runtime checks at every write attempt that the requested operation is in the declared list — a `write_capabilities`-not-in-list operation is a hard refusal at the adapter boundary, not at the projection or pipeline layer.

### 2.4 Sensitivity-default and observation-mode-required as orthogonal manifest fields

Two manifest fields control system-level safety posture independently of category:

- **`sensitivity_default`** — `normal` | `sensitive` | `privileged`. The default sensitivity stamped on events the adapter emits. Per-event override is permitted by the adapter's event constructor (e.g., a Cat-A messaging adapter can mark a specific message as `sensitive` based on conversation metadata). The category provides a default; the manifest can override; the runtime can per-event override.

  Defaults per category (recommended; manifest can deviate with justification):
  - Cat-A: `normal`
  - Cat-B: `normal`
  - Cat-C non-health: `sensitive` (Plaid)
  - Cat-C health: `privileged` (Stelo, Apple Health) — per [§13.6]
  - Cat-D: `sensitive`
  - Cat-E: `normal`

- **`observation_mode_required`** — `true` | `false`. When `true`, the adapter integrates with observation mode at the same final-outbound-filter point that messaging outbound does (per DIAGRAMS.md §9). Service calls / outbound actions emit `observation.suppressed@v1` with the full would-have-sent payload when observation mode is active, and the principal can review the suppressed log to see exactly what *would* have happened.

  Defaults per category:
  - Cat-A: `false` for inbound; `true` for outbound (existing convention via the channel-bridge plugin path).
  - Cat-B: `false` (the principal is maintaining state in the external system either way; CoS-side writes are participatory, not action-in-the-world).
  - Cat-C: `false` (read-only).
  - Cat-D: `false` for read; `true` for cross-cat writeback (Notes-checklists writeback is observation-mode-suppressed).
  - Cat-E: `true` (default; high-stakes Cat-E like brokerage cannot ship without it).

These two fields are independent — a Cat-D adapter with cross-cat writeback can be `sensitivity_default: sensitive` AND `observation_mode_required: true` simultaneously. A Cat-A inbound adapter is `normal` AND `false`. The validator (§3) checks coherence (a Cat-E adapter declaring `observation_mode_required: false` requires explicit justification in a manifest comment field; the install-time validator emits a warning and requires `--allow-unobserved-action` flag to proceed in non-developer-mode installs).

### 2.5 Sharing-model discriminator on Cat-B projection rows

Cat-B adapters mirror state from external systems that have varying sharing models. The `lists` projection (§5), the `calendars` projection (existing), and `parties.identifiers` (existing) all gain a `sharing_model` discriminator column on Cat-B-sourced rows so the system can correctly handle cross-member visibility.

**Sharing-model values:**

- `solo` — single-user, no sharing surface in upstream. Google Tasks lists are solo.
- `apple_icloud_shared_list` — Apple Reminders + Apple Calendar + Apple Contacts shared via iCloud Shared Lists / Family Sharing. Multi-participant; participation roster is observed via the upstream API.
- `google_workspace_shared` — Google Tasks / Calendar / Contacts shared via Workspace permissions. Multi-participant; roster from the API.
- `caldav_shared_calendar` — generic CalDAV calendar with multi-user ACLs (deferred to v2 per D25, but discriminator is reserved).
- `notion_workspace_shared` — Notion blocks shared via workspace membership (community pack).
- `trello_board_membership` — Trello board with member-level access (community pack).

**Why this matters at projection time.** The `lists` projection's row for an Apple iCloud Shared "Family Groceries" list needs to know that participation is multi-member for invitation-acceptance flow (UT-20) and for cross-member visibility queries. The `lists` row for "James's Personal" Google Tasks list needs to know it is solo and stays in `private:james` regardless of Family Sharing state.

Sharing-model is set at adapter ingest time, persisted in the projection row, and used by downstream pipelines and surfaces. It is not user-editable post-ingest — re-ingest from the source updates it.

### 2.6 Owner-scope-overridability at install

Most adapters allow operator override of the default owner-scope at install / pairing time. This is the existing pattern: the operator pairs Plaid for the household checking account at `shared:household` and pairs the operator's personal brokerage at `private:james`.

A new manifest field makes this explicit: **`owner_scope_overridable_at_install`** — `true` | `false`.

- `true` — bootstrap §5/§8 prompts the operator for the owner-scope at pairing time, defaulting to the category's default. This is the norm.
- `false` — the owner-scope is fixed by the adapter design and cannot be overridden. Cat-D adapters (bridge runtime) are universally `false` because the Tailscale-identity-binds-owner-scope rule per memo 04 §2.2 is structural privacy, not configuration. A hypothetical "household weather" adapter (Cat-C, scope-fixed at `shared:household`) would also be `false` — there's no member-scoped weather.

The validator (§3) checks coherence: an adapter declaring `runtime: bridge` AND `owner_scope_overridable_at_install: true` fails install — bridge runtime always ties owner-scope to Tailscale identity, the override would be meaningless.

---

## 3. Adapter framework deliverables

Per D20 and PM-34, Amendment-2 delivers the adapter framework as Phase A scope. The framework is built on the existing BUILD.md §PACK REGISTRY infrastructure (the six pack kinds, the seven-stage install lifecycle per DIAGRAMS.md §8, the `adminme pack` CLI). The five-category taxonomy refines pack manifests and adds category-specific base classes; it does **not** replace the pack-registry mechanism.

### 3.1 Manifest format extensions

The existing `pack.yaml` format for `kind: adapter` packs (per BUILD.md §PACK REGISTRY) is extended with the following fields. All extensions are additive — existing v1 adapters that haven't been migrated yet validate as single-capability adapters with category inferred from the existing fields, with a deprecation warning at install.

```yaml
# Existing fields (unchanged):
id: <slug>
kind: adapter
version: <semver>
min_platform: <semver>
description: <one-line>

# NEW: Amendment-2 fields
runtime: central | bridge | dual           # per §2.1
capabilities:                              # per §2.2; one or more entries
  - kind: cat_a_communication |
          cat_b_external_state_mirror |
          cat_c_inbound_data |
          cat_d_personal_knowledge |
          cat_e_outbound_action
    event_families: [<event-type-prefix>, ...]
    write_capabilities: [<operation-name>, ...]
    sensitivity_default: normal | sensitive | privileged
    observation_mode_required: true | false
    dedup_strategy:                        # optional, Cat-B-typical per PM-31
      kind: external_id_unique             # or: composite_key | content_hash | last_seen_wins
      key_fields: [<field-name>, ...]
owner_scope_default: shared:household | private:per_member | shared:org:<id>
owner_scope_overridable_at_install: true | false
sharing_model_discriminator: <discriminator-name>  # optional, Cat-B per §2.4
```

For dual-deployment runtimes, two deployment configurations follow:

```yaml
deployments:
  central:
    capabilities: [<list of capability ids that are active in central runtime>]
    config_schema: <path-to-config-schema>
  bridge:
    capabilities: [<list of capability ids that are active in bridge runtime>]
    config_schema: <path-to-config-schema>
```

The `dedup_strategy` field formalizes PM-31: Cat-B adapters using the same upstream entity (e.g., an iCloud reminder visible from both the central CoS Apple ID *and* a member bridge's Apple ID via shared list) deduplicate at projection time on `(kind, external_id)`. Pack-side declaration lets the projection layer apply the right strategy without per-projection special-casing.

### 3.2 Five abstract base classes

Per PM-34, five Python abstract base classes ship in `adminme/lib/adapters/`. Each base class corresponds to one category and provides the contract shape every adapter of that category implements.

**`adminme/lib/adapters/communication_base.py`** — `CommunicationAdapter` ABC.

Abstract methods:
- `_handle_inbound_message(raw_payload: dict) -> AdapterEmitResult` — translate adapter-specific inbound payload to a normalized event emission.
- `_send_outbound_message(content: OutboundMessage) -> SendResult` — execute outbound send through the adapter's transport.

Concrete methods provided:
- Observation-mode integration for outbound (default `observation_mode_required: true` for outbound side).
- Standard error handling and retry policy.
- Standard `messaging.received` / `messaging.sent` event-shape helpers.

**`adminme/lib/adapters/external_state_mirror_base.py`** — `ExternalStateMirrorAdapter` ABC.

Abstract methods:
- `_pull_state(cursor: StateCursor) -> Iterator[ExternalStateChange]` — scan upstream for changes since the last cursor; emit one StateChange per observed entity update.
- `_push_state(write_request: StateWriteRequest) -> WriteResult` — execute a write-back request, gated by the adapter's declared `write_capabilities`.
- `_resolve_conflict(local_state: dict, upstream_state: dict, last_known: dict) -> ConflictResolution` — handle the case where both AdministrateMe and the external system have changed since last sync.
- `_dedup_key(entity: dict) -> str` — produce the deduplication key per the manifest's `dedup_strategy`.

Concrete methods provided:
- Structured `write_capabilities` enforcement (refuse operations not in the manifest list).
- Standard cursor management.
- Standard `list.added` / `list_item.completed` / `calendar_event.updated` event-shape helpers.

**`adminme/lib/adapters/inbound_data_base.py`** — `InboundDataAdapter` ABC.

Abstract methods:
- `_fetch_data(since: datetime | None) -> Iterator[ExternalRecord]` — pull data from the upstream API.
- `_normalize_to_events(record: ExternalRecord) -> list[EventEnvelope]` — translate one upstream record into one or more typed events.

Concrete methods provided:
- Refusal of any write-back attempt (no `_push_state` exists in this base).
- Standard sensitivity-defaulting per manifest.
- Standard cursor management.

**`adminme/lib/adapters/personal_knowledge_base.py`** — `PersonalKnowledgeAdapter` ABC.

Abstract methods:
- `_watch_source() -> Iterator[KnowledgeSourceChange]` — long-running watcher for new / changed / deleted notes / voice notes / pages.
- `_emit_knowledge_events(change: KnowledgeSourceChange) -> list[EventEnvelope]` — translate one change into `note.*` / `voice_note.*` events.
- `_apply_writeback(request: WritebackRequest) -> WriteResult` — optional, for cross-cat extensions like Notes-checklists. Default raises `NotImplementedError`; cross-cat adapters override.

Concrete methods provided:
- Bridge-runtime owner-scope handling per memo 04 (the bridge stamps owner_scope from Tailscale identity at the `:3337` ingest endpoint; the adapter doesn't claim it).
- Standard `note.added` / `note.updated` / `voice_note.added` event-shape helpers.

**`adminme/lib/adapters/outbound_action_base.py`** — `OutboundActionAdapter` ABC.

Abstract methods:
- `_execute_action(request: ActionRequest) -> ActionResult` — perform the action in the world.
- `_handle_action_response(response: ExternalActionResponse) -> EventEnvelope` — translate the upstream confirmation into an `action.executed` or `action.failed` event.

Concrete methods provided:
- Observation-mode suppression with `observation.suppressed@v1` event emission carrying the full would-have-sent payload.
- Standard `action.requested` / `action.executed` / `action.failed` event-shape helpers.
- `write_capabilities` enforcement at the action-verb level.

### 3.3 Discovery and registration CLI

The existing `adminme pack` CLI gains a sibling `adminme adapters` subcommand group, focused on adapter-specific operations. Per PM-34:

- `adminme adapters list` — list installed adapters with their categories, runtimes, and capability summaries.
- `adminme adapters inspect <id>` — full manifest dump + base-class confirmation + capability table for one adapter.
- `adminme adapters disable <id>` — stop subscriptions / runners for an installed adapter without uninstalling. Useful for triage.
- `adminme adapters enable <id>` — re-enable a disabled adapter.
- `adminme adapters scaffold --category <cat-letter> --name <slug>` — generate a new adapter pack scaffold in the developer-mode directory: `pack.yaml` populated with the category's defaults, base-class subclass stubbed with all abstract methods declared, `tests/` directory with empty test scaffolds, `schemas/` directory with the relevant event schemas referenced.

Discovery uses the existing pack-registry / pack-install lifecycle (DIAGRAMS.md §8). `adminme adapters install` is an alias for `adminme pack install` filtered to `kind: adapter`. The framework leans on existing infrastructure rather than building parallel machinery.

### 3.4 Install-time validation extensions

The pack-install-validate stage (stage 1 of DIAGRAMS.md §8) gains adapter-specific checks per PM-34. These augment the existing `kind ∈ {adapter, pipeline, skill, projection, profile, persona}` and `id unique` checks; they do not replace them.

**Capability-runtime coherence check.** For each declared capability, verify the runtime is plausible. Cat-D + central is permitted but warns. Cat-A bridge is permitted but warns. Cat-D + bridge + `owner_scope_overridable_at_install: true` is a hard refusal (incompatible with the Tailscale-stamps-owner-scope rule).

**Event-schema registration check.** For every event-family declared in any capability, verify that a corresponding Pydantic schema is registered in `adminme.events.schemas.*` at v1+. An adapter declaring `event_families: [list.added]` requires `ListAddedV1` to be registered. Refusal on missing schema.

**Projection-subscription compatibility check.** For Cat-B / Cat-D adapters, verify that at least one projection subscribes to the declared event-families. An adapter emitting `note.added` events that no projection consumes is a dead end; the validator emits a warning (not refusal — it might be a forward-compatible install ahead of a projection prompt).

**Base-class conformance check.** Verify the adapter's primary class subclasses the correct ABC for its primary capability. An adapter declaring `cat_a_communication` capability whose class doesn't subclass `CommunicationAdapter` is a hard refusal.

**`write_capabilities` method-presence check.** For every declared write-capability, verify the adapter class implements (overrides) the corresponding method on the base class. A Cat-B adapter declaring `write_capabilities: [create_item, toggle_completion]` whose class only overrides `_create_item` (not `_toggle_completion`) fails install.

**Signature check (in verified mode).** Per D20 layer 2, verified third-party adapters must carry a signed manifest. The validator verifies the signature against the registry's public-key list before proceeding to other checks.

### 3.5 Three-layer developer mode

Per D20, three layers of adapter origination are supported, with different validation discipline at each layer:

**Layer 1 — Bundled adapters.** Default-on. Maintained in the AdministrateMe repo. Subject to the standard build-prompt + QC discipline. No developer-mode flag required to install. Examples: every adapter in the §1.3 inventory table that ships v1.

**Layer 2 — Verified third-party adapters.** Developer mode flag required (`adminme config set developer_mode true` or env `ADMINME_DEV_MODE=1`). Signed manifest required. Installed via `adminme adapters install --source pip|local|git <spec>` — not the public pack registry until verified-status mechanism is fleshed out post-Phase-A.

**Layer 3 — User-authored adapters.** Developer mode flag required. Scaffolded via `adminme adapters scaffold --category <X> --name <Y>`. Installed from local-path only; not redistributable without graduating to Layer 2.

The validator emits a warning at install time identifying which layer the adapter is being installed at, and the install event (`pack.installed`) records the layer in its payload for audit.

### 3.6 Authoring guide

A new file `docs/adapter-authoring-guide.md` ships with the adapter framework. Estimated 8–12 pages, structured as a walkthrough:

1. Pick the right category. Decision tree from epistemic role.
2. Single-capability vs multi-capability decision.
3. Run `adminme adapters scaffold` to generate the pack skeleton.
4. Implement the base class's abstract methods. Worked examples per category, citing the §1.3 reference adapters.
5. Declare capabilities and write-capabilities in the manifest.
6. Set sensitivity defaults and observation-mode-required correctly for the category.
7. Run the local test harness (`pytest packs/adapters/<your-adapter>/tests/`).
8. Install in developer mode and verify with `adminme adapters inspect`.
9. Common pitfalls (declaring a capability whose event-family no projection consumes; mis-declaring `owner_scope_overridable_at_install` for a bridge runtime; forgetting observation-mode integration on a Cat-E capability).
10. Promotion path: from Layer 3 user-authored to Layer 2 verified, then potentially to Layer 1 bundled via PR to the AdministrateMe repo.

The authoring guide cites memo 04 and memo 05 (this memo) as binding architectural references; cites BUILD.md §PACK REGISTRY for the install lifecycle; cites SYSTEM_INVARIANTS for the cross-cutting rules every adapter must respect.

---

## 4. L1 adapter inventory cleanup

The 2026-04-29-B rubric surfaced eleven specific drifts in the L1 adapter inventory across `ADMINISTRATEME_BUILD.md`, `docs/architecture-summary.md`, `docs/SYSTEM_INVARIANTS.md`, and `docs/DECISIONS.md`. Each drift had a different shape: some were stale (mentioned in inventory but never planned for build); some were under-specified (mentioned but no prompt assigned); some were category-mismatches under the prior runtime-substrate framing; some were credential-orphans per PM-32. UT-24 (CLOSED 2026-04-29-B by D21+D22+D23+D24+D25) is the umbrella for the cleanup.

The dispositions below are the result of the 2026-04-29-B consultation and are binding per D21–D25.

### 4.1 SMS / Twilio — recategorize as Cat-E

**Prior framing.** SMS via Twilio listed alongside iMessage/Telegram/Discord as a Cat-A communication channel.

**Disposition (per D21).** Twilio is **Cat-E (Outbound-Action)**, not Cat-A. The household's primary text-messaging surface is iMessage via BlueBubbles per existing v1 plans. Twilio's role is **outbound fallback** when iMessage delivery fails (e.g., the recipient is on Android and out-of-network for iMessage's SMS-failback). Inbound SMS is deferred to v2.

**Implementation impact.** `pack.yaml` for the Twilio adapter declares `runtime: central`, `capabilities: [{kind: cat_e_outbound_action, write_capabilities: [sms_send], observation_mode_required: true}]`. Existing `messaging.send_requested` event payload gains a `delivery_channel_preference` field (default `imessage`, fallback `sms`); the channel-bridge plugin path picks Twilio when iMessage delivery fails. No new prompt — modifies the existing OpenClaw-plugin-adapters prompt 12 scope.

### 4.2 CalDAV (separate adapter) — defer to v2

**Prior framing.** CalDAV listed as a v1 calendaring adapter alongside Apple Calendar (presumed iCloud-via-CalDAV) and Google Calendar.

**Disposition (per D25).** Apple Calendar (per D22, dual-deployment) covers iCloud calendaring natively via EventKit on the bridge and the Apple Calendar adapter on the central. Google Calendar covers Google calendaring natively via the Google Calendar API. A separate CalDAV adapter is needed only for calendar servers that are neither iCloud nor Google (e.g., Fastmail, NextCloud, employer CalDAV) — this is a v2 community-pack scenario.

**Implementation impact.** Remove CalDAV from L1 inventory in arch-summary §1, BUILD.md §L1, and SYSTEM_INVARIANTS.md §8.9. Remove CalDAV-specific credential intake from bootstrap §5 per PM-32. The `caldav_shared_calendar` sharing-model value (§2.4) is reserved for the v2 community pack; the discriminator column accepts it but no v1 adapter emits it.

### 4.3 Google Contacts — build in v1

**Prior framing.** Google Contacts mentioned in passing in the L1 inventory but no prompt assigned. CRM spine empty on day 1 — `parties.identifiers` would have no Google-side feed.

**Disposition (per D23).** Google Contacts adapter ships v1, central runtime, via the Google People API. Cat-B (External-State-Mirror), full bidirectional where the API allows (full CRUD on contacts owned by the authenticated user; observation-only on contacts shared via Workspace).

**Implementation impact.** New prompt 11e (Apple Contacts + Google Contacts together; closes UT-29). `pack.yaml` declares `runtime: central`, `capabilities: [{kind: cat_b_external_state_mirror, write_capabilities: [create_contact, update_contact_field, delete_contact, add_to_group], observation_mode_required: false, dedup_strategy: {kind: external_id_unique, key_fields: [google_resource_name]}}]`. Feeds `parties.identifiers` with `source_kind=google_people` discriminator. Sharing-model: `solo` for personal contacts, `google_workspace_shared` for Workspace-shared.

### 4.4 Apple Contacts — build in v1, bridge per-member

**Prior framing.** Apple Contacts via CardDAV mentioned in the L1 inventory but no prompt assigned. Same CRM-spine-empty problem on day 1 for Apple-using households.

**Disposition (per D23).** Apple Contacts adapter ships v1 on **bridge runtime per-member** via the Contacts.framework on each member's Mac Mini. This parallels the Apple Notes / Voice Memos pattern from memo 04 — each family member's iCloud-synced Contacts are read by their own bridge, never by the central CoS Mac Mini.

**Why bridge rather than central.** Contacts are per-member personal data. The household-shared "Family" contact group is the exception, not the rule, and it is observable through any member's bridge (since Family Sharing replicates it to each member's local Contacts). Centralizing Apple Contacts on the CoS Mac Mini would require the CoS Apple ID to be in every member's Family, which is a privacy regression (cf. memo 04 §2.1, the Apple-ID-isolation principle).

**Implementation impact.** Same prompt 11e as §4.3. `pack.yaml` declares `runtime: bridge`, `capabilities: [{kind: cat_b_external_state_mirror, write_capabilities: [create_contact, update_contact_field], observation_mode_required: false, dedup_strategy: {kind: external_id_unique, key_fields: [apple_contact_identifier]}}]`. Note: `delete_contact` is not in v1 write-capabilities — Apple Contacts deletion via Contacts.framework is irreversible and the round-trip-conflict surface is too wide for v1; deferred to a future minor revision. Feeds `parties.identifiers` with `source_kind=apple_contacts` discriminator. Sharing-model: `solo` for personal, `apple_icloud_shared_list` (reused term) for Family Sharing.

### 4.5 Google Drive — defer to v2

**Prior framing.** Google Drive listed in the L1 inventory under "documents" alongside Plaid for "financial" and Apple Reminders for "reminders."

**Disposition (per D25).** Google Drive document ingestion is v2 / community pack. The v1 build does not have a projection that consumes Google Drive documents; the personal-knowledge category is satisfied by Apple Notes / Voice Memos / Obsidian per memo 04.

**Implementation impact.** Remove Google Drive from L1 inventory in arch-summary §1, BUILD.md §L1, DIAGRAMS.md §1 ASCII (the "documents (Drive)" line under CENTRAL adapters becomes a deferred v2 line in a v2 footnote, or is removed depending on PR-α-2's editorial choice). Remove Google Drive credential intake from bootstrap §5 per PM-32.

### 4.6 iOS Shortcuts webhooks — remove from L1

**Prior framing.** iOS Shortcuts webhooks listed in the L1 inventory as a generic webhook ingest path, motivated by the pre-Conception-C plan to capture quick-thoughts via Shortcuts.

**Disposition (per D25, building on D17).** iOS Shortcuts webhooks are functionally retired by D17 (knowledge-source ingestion via bridges supersedes the Shortcuts webhook pattern). A family member who wants to jot a quick thought captures it in Apple Notes or Voice Memos, and the bridge-side adapter ingests it. There is no remaining role for a Shortcuts-driven webhook in v1.

**Implementation impact.** Remove iOS Shortcuts from L1 inventory across constitutional docs. No credential-intake removal needed (Shortcuts webhooks were operator-configured per-instance, not a credentialed third-party API).

### 4.7 iCloud as inventoried — clarify, not separate

**Prior framing.** "iCloud" appeared in some inventory passes as if it were its own adapter alongside Apple Reminders / Apple Notes / iMessage / Apple Calendar / Apple Contacts.

**Disposition.** iCloud is **not** a separate adapter. It is the cloud surface that Apple's per-domain APIs (EventKit for Reminders / Calendar, NoteStore for Notes, Contacts.framework for Contacts, BlueBubbles for iMessage) project. AdministrateMe never speaks to iCloud directly; AdministrateMe speaks to Apple's per-domain APIs, and those APIs are what synchronize through iCloud on the user's Mac.

**Implementation impact.** Inventory text in arch-summary §1, BUILD.md §L1, and DIAGRAMS.md §1 ASCII rewrites to remove any standalone "iCloud" line and clarifies that the Apple-side adapters (Reminders, Notes-prose, Notes-checklists, Voice Memos, Calendar, Contacts) collectively cover the iCloud surface through their respective Apple frameworks. No PR to credential intake — there is no iCloud credential to remove (the relevant credential is the Apple ID, which is per-runtime: central CoS Apple ID for central-runtime adapters, member Apple ID for bridge-runtime adapters).

### 4.8 Home Assistant — build in v1 as Cat-C+E reference

**Prior framing.** Home Assistant mentioned in BUILD.md §L5 / capture-product / automation-product surfaces as a planned v1 integration, with `/api/automation/ha/*` routers referenced — but no L1 adapter prompt assigned. Routers without a backing adapter — a stronger version of credential orphans per PM-32 ADDENDUM 2026-04-29-B.

**Disposition (per D24).** Home Assistant adapter ships v1, central runtime, as the **Cat-C+E reference implementation** for multi-capability adapters. Both seams ship in Phase A: the Cat-C state-read seam observes entity states via the HA REST/WebSocket API (entity types: lights, switches, climate, presence sensors, locks, scenes, etc.); the Cat-E service-call seam executes service calls (turn_on, turn_off, scene activation, climate.set_temperature, etc.).

**Observation-mode integration.** Per D24, the service-call seam is observation-mode-suppressed: when `observation_mode = on`, service calls emit `observation.suppressed@v1` with the full would-have-sent payload (entity_id, service, service_data, area_id), exactly the way messaging outbound suppresses today. The state-read seam is unaffected by observation mode (reading is not a side-effect).

**Implementation impact.** New prompt 11g (Home Assistant adapter; closes UT-30). `pack.yaml` declares `runtime: central`, `capabilities: [{kind: cat_c_inbound_data, event_families: [ha.state_observed, ha.event_observed], write_capabilities: [], sensitivity_default: normal, observation_mode_required: false}, {kind: cat_e_outbound_action, event_families: [ha.service_call_requested, ha.service_call_executed, ha.service_call_failed], write_capabilities: [service_call, scene_activation], sensitivity_default: normal, observation_mode_required: true}]`. Feeds `vector_search` (entity-name embedding for "turn on the kitchen light" recognition); does not require a new dedicated projection in v1.

**PM-32 ADDENDUM disposition.** The `/api/automation/ha/*` routers in the existing prompt 13b draft are now valid surfaces — they get **real backing** in 11g rather than being removed. PM-32's strengthened addendum (routers without backing adapters are stronger than credential orphans) is satisfied because 11g lands the adapter that those routers proxy to. The capture-product and automation-product surfaces in 13b reference the HA capabilities through the established product-API → adapter pattern, not directly.

### 4.9 Privacy.com — defer to v2 community pack

**Prior framing.** Privacy.com mentioned in BUILD.md §AUTHORITY governance examples (virtual-card creation as a high-stakes outbound action). Bootstrap §5 had a Privacy.com credential intake line.

**Disposition (per D25).** Privacy.com is deferred to a v2 community pack. The Cat-C transaction-notification seam is in practice covered by Plaid (which sees Privacy.com card transactions as merchant charges on the underlying funding account); the Cat-E issue-card seam is the only Privacy.com-specific need, and it is high-stakes-outbound that v1 doesn't require.

**Implementation impact.** Per PM-32, remove Privacy.com from bootstrap §5 credential intake. Remove Privacy.com from BUILD.md §AUTHORITY governance examples (or convert to v2 reference). The `cat_e_outbound_action` capability + observation-mode-required pattern is documented in §3 base classes as a generic shape; Privacy.com being a v2 community pack does not block v1.

### 4.10 Lob (postal mail) — defer to v2 community pack

**Prior framing.** Lob mentioned in BUILD.md §AUTHORITY governance examples (postal-letter dispatch as a high-stakes outbound action). Bootstrap §5 had a Lob credential intake line.

**Disposition (per D25).** Lob is deferred to a v2 community pack for the same reason as Privacy.com — it is high-stakes-outbound (Cat-E) that v1 does not need. Households that use postal-mail dispatch as a CoS capability install the community pack post-Phase-A.

**Implementation impact.** Per PM-32, remove Lob from bootstrap §5 credential intake. Remove Lob from BUILD.md §AUTHORITY governance examples (or convert to v2 reference, parallel to Privacy.com).

### 4.11 Connector-pack pattern — document as universal extension mechanism

**Prior framing.** Memo 04 §3.1 introduced `subkind: knowledge-source` as the connector-pack slot for Cat-D (Apple Notes / Voice Memos / Obsidian as bundled; Notion / Logseq / Roam / Day One / Bear / Readwise as community packs).

**Disposition.** Generalize the connector-pack pattern to **all five categories**. Every category supports community-authored adapter packs via the developer-mode tier (D20 layer 2 or 3). The §1.3 inventory is the v1-bundled set; everything else lives as community packs.

**Implementation impact.** §3 of this memo (the framework) is the documentation of the universal mechanism. The authoring guide (`docs/adapter-authoring-guide.md` per §3.6) is the operator-facing entry point. The `subkind: knowledge-source` tag from memo 04 is retained for backwards compatibility and remains a valid Cat-D-bridge-runtime synonym; the canonical declaration going forward is the explicit `capabilities: [{kind: cat_d_personal_knowledge}]` + `runtime: bridge` form.

### 4.12 Cumulative impact

After these eleven dispositions, the v1 L1 adapter inventory is:

- **Cat-A (Communication):** Gmail (central); BlueBubbles, Telegram, Discord (central, OpenClaw plugins).
- **Cat-B (External-State-Mirror):** Apple Reminders (dual), Google Tasks (central), Apple Calendar (dual, NEW), Google Calendar (central), Apple Notes-checklists (bridge, NEW, cross-cat with D), Apple Contacts (bridge, NEW), Google Contacts (central, NEW).
- **Cat-C (Inbound-Only Data):** Plaid (central); Home Assistant state-read half (central, NEW, multi-cap with E).
- **Cat-D (Personal-Knowledge):** Apple Notes-prose (bridge), Voice Memos (bridge), Obsidian (bridge opt-in), Apple Notes-checklists (bridge, NEW, cross-cat with B).
- **Cat-E (Outbound-Action):** Twilio SMS (central, recategorized); Home Assistant service-call half (central, NEW, multi-cap with C).

Phase A scope impact summary (carried in `partner_handoff.md`'s "Phase A scope impact" block, but worth stating here for the memo's record):

- New v1 prompts: 11e (Contacts), 11f (lists projection — see §5), 11g (Home Assistant).
- Modified v1 prompts: 11 (framework expansion: five base classes + manifest spec + validator + CLI + authoring guide), 11b (Apple Calendar dual-deployment added per D22), 11c-i / 11c-ii (Notes-checklist B-half emitting `list.*` + AppleScript write-back for toggle+add per D18 / UT-19), 13b (capture lists router; automation HA routers gain real backing per §4.8 PM-32 ADDENDUM), 14b (lists view added; CRM view populated by Apple Contacts feed per §4.4), 16 (§8 expansions for lists auto-seed + Apple Calendar pairing + Contacts pairing + HA pairing; §10 expansions for new bridge adapters), 17 (new `adminme adapters` CLI subcommand group per §3.3).
- Phase A duration estimate increase: from 93–123 hours to ~105–141 hours (~12–18 hour increase from the additions above).

The §4 cleanup is the predicate for the scoped sub-amendments in §5–§9 (lists projection, Apple Calendar v1, Contacts v1, HA v1, PR plan), which Session A-2 drafts.

## 5. Cat-B sub-amendment — external-state-mirror lists (D18)

### 5.1 The problem D18 closes

The pre-Amendment-2 inventory had Apple Reminders feeding `task.*` events and the `tasks` projection subscribing to `reminder.*` (per `docs/architecture-summary.md` §4 row 3.5 as it currently stands on main). That conflation has three failure modes.

**(a) Tasks and list items are different things.** A grocery item is not a task. A todo on a household honey-do list shared with a spouse is structurally a list item; some of those items get promoted into tasks (e.g. "fix the screen door — Saturday morning, needs the drill"), but most don't. Modeling them all as tasks corrupts the inbox: the ADHD neuroprosthetic surfaces every grocery line as a paralysis candidate, the `tasks` projection's domain enum bloats with `groceries` / `errands` / etc., and the `crm_surface` proactive pipeline gets noise it can't filter.

**(b) External lists have round-trip semantics that tasks don't.** Apple Reminders, Google Tasks, and Apple Notes checklists are mirrors of state the principal directly maintains in those upstream UIs. A list item created on iPhone in the kitchen needs to round-trip back through the bridge into the central event log AND back out to other family members' Reminders.app instances via iCloud Shared Lists — without going through the `tasks` projection's task-shaped fields (energy / effort / micro_script / waiting_on / goal_ref / life_event), which would all be NULL noise on a "milk" line.

**(c) Sharing models differ.** Tasks have an assignee party; lists have an owner and an invited-shares set. iCloud Shared Lists, Google Tasks shared task lists, and Apple Notes shared checklists each carry their own sharing semantics that don't fit a `task.assignee_party` field. Modeling shared lists as a multi-assignee task is a category error.

D18 (2026-04-29-B) closes all three by making **lists a 13th first-class projection**, alongside `member_knowledge` as the 12th from Conception-C. List items are the projection's grain. Tasks remain the obligation-and-work projection they always were, with `reminder.*` retired from its subscription list (UT-22 closure).

### 5.2 The new `lists` projection

The 13th projection sits in `adminme/projections/lists/` per the standard projection layout. Three tables, modeled in parallel to BUILD.md §3.4 (`commitments`) and §3.5 (`tasks`):

```sql
CREATE TABLE lists (
  list_id              TEXT PRIMARY KEY,             -- adminme-internal opaque id (Crockford ULID)
  external_id_kind     TEXT,                          -- 'apple_reminders' | 'google_tasks' |
                                                      -- 'apple_notes_checklist' | NULL (CoS-native)
  external_list_id     TEXT,                          -- upstream list id (per dedup_strategy in
                                                      -- pack manifest, PM-31)
  source_kind          TEXT NOT NULL,                 -- discriminator: 'apple_reminders' |
                                                      -- 'google_tasks' | 'apple_notes_checklist' |
                                                      -- 'cos_native'
  title                TEXT NOT NULL,
  list_kind            TEXT NOT NULL,                 -- 'shopping' | 'todo' | 'errands' |
                                                      -- 'travel_prep' | 'project' | 'other'
  owner_party          TEXT REFERENCES parties(party_id),  -- creator; NULL for upstream-discovered
  owner_scope          TEXT NOT NULL,                  -- 'private:<member>' | 'shared:household' |
                                                      -- 'shared:org:<id>'
  visibility_scope     TEXT NOT NULL,
  sharing_model        TEXT NOT NULL,                 -- 'private' | 'shared_household' |
                                                      -- 'icloud_shared_list' | 'google_shared_list'
                                                      -- (per §2.4 sharing-model discriminator)
  created_at           TEXT NOT NULL,
  archived_at          TEXT,                           -- non-null = archived (soft delete)
  last_event_id        BLOB NOT NULL,
  UNIQUE (external_id_kind, external_list_id)         -- Cat-B dedup per PM-31 (NULL/NULL allowed
                                                      -- for cos_native rows; SQLite NULLs distinct)
);

CREATE TABLE list_items (
  item_id              TEXT PRIMARY KEY,
  list_id              TEXT NOT NULL REFERENCES lists(list_id),
  external_item_id     TEXT,                           -- upstream item id when known
  parent_item_id       TEXT REFERENCES list_items(item_id),  -- subtask hierarchy (Google Tasks,
                                                              -- Reminders subtasks)
  body                 TEXT NOT NULL,
  status               TEXT NOT NULL,                  -- 'open' | 'completed' | 'removed'
  position             INTEGER,                         -- ordering within list (NULL allowed for
                                                       -- adapters that don't expose order)
  added_by_party       TEXT REFERENCES parties(party_id),
  added_at             TEXT NOT NULL,
  completed_at         TEXT,
  completed_by_party   TEXT REFERENCES parties(party_id),
  removed_at           TEXT,
  promoted_task_id     TEXT REFERENCES tasks(task_id), -- non-null when this list item has been
                                                       -- promoted to a task per D18 (the list item
                                                       -- itself is unchanged; the task is its own
                                                       -- row in `tasks`)
  notes                TEXT,
  owner_scope          TEXT NOT NULL,                   -- inherited from parent list at apply time
  visibility_scope     TEXT NOT NULL,
  last_event_id        BLOB NOT NULL,
  UNIQUE (list_id, external_item_id)
);

CREATE TABLE list_shares (
  share_id             TEXT PRIMARY KEY,
  list_id              TEXT NOT NULL REFERENCES lists(list_id),
  invited_party        TEXT NOT NULL REFERENCES parties(party_id),
  invitation_status    TEXT NOT NULL,                   -- 'pending' | 'accepted' | 'declined' |
                                                       -- 'expired'
  invited_at           TEXT NOT NULL,
  accepted_at          TEXT,
  external_invitation_ref TEXT,                         -- opaque upstream invitation handle
                                                       -- (iCloud share URL, Google sharing token)
  last_event_id        BLOB NOT NULL,
  UNIQUE (list_id, invited_party)
);
```

The `promoted_task_id` column is the sole structural link between the `lists` and `tasks` projections. Per D18, promoting a list item to a task does not modify the list item — the item retains its open/completed state in upstream-mirror semantics; the resulting task is a new row in `tasks` with `created_by = '<source-list-item-promotion>'` and a `source_list_item_id` payload field on the originating `list_item.promoted_to_task` event. This is the cleanest model for the case where a household member buys a tool to fix the screen door (groceries side) and *also* needs the screen-door-fix tracked as a task with energy / effort / micro_script.

### 5.3 New event family — list and list-item events (~13 types at v1)

Per [D7] all new event types register at v1 in `adminme.events.schemas.domain`. Per the partner_handoff inventory of "~13 event types at v1," the canonical set is:

- `list.created@v1` — a new list appears (CoS-native creation, or first observation by a Cat-B adapter of an upstream list).
- `list.updated@v1` — title / list_kind / sharing_model / archive-status change.
- `list.deleted@v1` — upstream list deleted (or CoS-side soft-delete via `archived_at`).
- `list.shared@v1` — invitation accepted; share row transitions to `accepted`. Emitted by the bridge that observes the list appearing in the invited member's Reminders.app post-acceptance, or by the central adapter for Google-side acceptance.
- `list.share_invited@v1` — invitation extended; share row transitions to `pending`. Carries `external_invitation_ref` payload.
- `list_item.added@v1` — new item observed (upstream creation or CoS-native add).
- `list_item.updated@v1` — body or notes or position change.
- `list_item.checked@v1` — status transitions to `completed`.
- `list_item.unchecked@v1` — status transitions back to `open` (Reminders supports this; Google Tasks does too; Notes-checklist via AppleScript supports toggle).
- `list_item.removed@v1` — item deleted upstream (or CoS-native remove for sources that allow it).
- `list_item.promoted_to_task@v1` — the item is being promoted to a task per §5.2. Carries `source_list_item_id` provenance.

Plus three Notes-write-back observability events, scoped to the cross-cat Notes-checklist adapter:

- `note.write_attempted@v1` — bridge attempted an AppleScript write-back to a Notes checklist (toggle or add). Carries `note_id`, `operation` (`toggle_completion` | `add_item`), `requested_by_event_id`.
- `note.write_succeeded@v1` — write-back confirmed by AppleScript return. Carries the same keys plus `confirmed_at`.
- `note.write_failed@v1` — write-back failed (note moved, note locked, AppleScript error, last-writer-wins iCloud sync race per UT-19). Carries the same keys plus `failure_reason`.

These three `note.write_*` events are explicitly **observability events**, not state-changing events. The state change is whatever the upstream Notes app records via iCloud sync; the bridge merely announces its write attempts so the operator can audit the Notes-checklist write path during the observation period and afterward. They go in `adminme.events.schemas.system` (alongside `xlsx.regenerated` per the system-event category Partner introduced in 07b), not in `adminme.events.schemas.domain`.

### 5.4 Apple Reminders — dual-deployment per D18

The Apple Reminders adapter (already in prompt 11b scope) becomes dual-deployment, with the two-axis manifest per §2.1 + §2.6:

```yaml
id: apple-reminders
kind: adapter
runtime: dual
capabilities:
  - kind: cat_b_external_state_mirror
    event_families: [list, list_item]
    write_capabilities: [create_list, archive_list, add_item, toggle_completion,
                         update_item, remove_item, invite_share]
    sensitivity_default: normal
    observation_mode_required: false  # state observation is read-only;
                                      # the write side is gated by the central
                                      # outbound() seam where it emits commands,
                                      # not at the adapter
    dedup_strategy:
      kind: external_id_unique
      key_fields: [external_id_kind, external_list_id]
owner_scope_default: shared:household
owner_scope_overridable_at_install: true
sharing_model_discriminator: sharing_model
deployments:
  central:
    capabilities: [cat_b_external_state_mirror]
    config_schema: schemas/central-config.yaml
  bridge:
    capabilities: [cat_b_external_state_mirror]
    config_schema: schemas/bridge-config.yaml
```

The central variant runs on the CoS Mac Mini against the assistant's Apple ID. It owns the four CoS-seeded shared lists (§5.6) and any other lists the principal explicitly assigns to the central account. The bridge variant runs on each member bridge against that member's Apple ID; it owns lists private to that member plus their side of any iCloud-shared lists they've accepted.

**Deduplication on `(external_id_kind, external_list_id)`** matters because a single iCloud Shared List shows up in both the central account's Reminders.app (the assistant is invited as a participant on each shared list) AND in the inviter's bridge Reminders.app. Both adapters observe the same list and emit `list.created` events with the same `external_list_id`. The projection's UNIQUE constraint on `(external_id_kind, external_list_id)` makes the second observation an idempotent no-op.

### 5.5 Google Tasks — Cat-B central-only

Net-new in 11b. REST API + OAuth 2 (the assistant's Google Workspace credentials per BOOTSTRAP §5). Subtask hierarchy via `parent_item_id`. Manifest:

```yaml
id: google-tasks
kind: adapter
runtime: central
capabilities:
  - kind: cat_b_external_state_mirror
    event_families: [list, list_item]
    write_capabilities: [create_list, archive_list, add_item, toggle_completion,
                         update_item, remove_item]
    sensitivity_default: normal
    observation_mode_required: false
    dedup_strategy:
      kind: external_id_unique
      key_fields: [external_id_kind, external_list_id]
owner_scope_default: shared:household
owner_scope_overridable_at_install: true
sharing_model_discriminator: sharing_model
```

No `invite_share` write-capability — Google Tasks shared lists are managed via the Google Workspace admin / Tasks API sharing endpoints which are out of v1 scope. v1 ships read-only observation of already-shared lists; sharing-creation is deferred.

### 5.6 Apple Notes-checklists — cross-cat B+D extension to 11c-ii

This is the canonical cross-cat / multi-capability adapter precedent (§2.2, PM-35). The Apple Notes adapter on a member bridge already (per memo 04 and PR-α landed 2026-04-29) emits `note.added` / `note.updated` / `note.deleted` events for whole-note ingestion into `member_knowledge`. Amendment-2 extends it with a Cat-B capability.

Manifest, post-extension:

```yaml
id: apple-notes
kind: adapter
runtime: bridge
capabilities:
  - kind: cat_d_personal_knowledge        # existing — whole-note ingestion
    event_families: [note]
    write_capabilities: []
    sensitivity_default: normal           # operator can set per-vault override
    observation_mode_required: false
  - kind: cat_b_external_state_mirror     # NEW — checklist detection + write-back
    event_families: [list, list_item]
    write_capabilities: [add_item, toggle_completion]   # toggle+add only per D18 / UT-19;
                                                        # explicitly NOT remove, in_place_text_edit,
                                                        # or reorder
    sensitivity_default: normal
    observation_mode_required: false
    dedup_strategy:
      kind: composite_key
      key_fields: [external_id_kind, external_list_id]   # external_list_id = "apple_notes_checklist:<note_id>"
owner_scope_default: private:per_member  # bridge runtime — Tailscale identity stamps owner_scope
                                         # at the :3337 ingest endpoint per memo 04
owner_scope_overridable_at_install: false  # hard refusal per §3.4 (Cat-D bridge incompatibility
                                           # with overridability); inherited because adapter
                                           # has a Cat-D capability
sharing_model_discriminator: sharing_model
```

The detection path: when the existing Cat-D capability ingests a note, a new sub-pipeline (lives in 11c-ii) parses the note body for Apple Notes checklist syntax (the encoded Unicode markers Apple Notes uses for unchecked/checked items; format documented in `docs/reference/apple-notes/checklist-format.md` to be added by 11c-ii). If the note contains at least one checklist item, the adapter emits parallel `list.created` and `list_item.added` events with `source_kind = 'apple_notes_checklist'` and `external_list_id = 'apple_notes_checklist:<note_id>'`.

The write-back path: when a `list_item.create_requested` or `list_item.toggle_completion_requested` event arrives at the adapter (gated by the `write_capabilities` enforcement in `ExternalStateMirrorAdapter._push_state`), the adapter invokes AppleScript on the bridge Mac Mini to drive Notes.app directly — open the target note by id, find the checklist item by content match, toggle or insert. Each attempt emits `note.write_attempted`; success emits `note.write_succeeded`; failure (note locked, note moved between vaults, AppleScript timeout, iCloud sync race) emits `note.write_failed`.

**Why no remove / no in-place text edit / no reorder.** Per D18 and UT-19. Apple Notes checklists are conversation artifacts — a child crosses out "milk" by checking it; a parent adds "eggs" before going to the store. AdministrateMe writes back the toggles (via the system, e.g. when a CoS skill confirms a household member said "got the milk") and adds new items (via the system when the principal says "add eggs to the grocery list"). The remove / in-place edit / reorder operations are reserved for the human author at the iPhone or Mac UI. AdministrateMe never deletes a child's "purple pencils" line off a Notes checklist; AdministrateMe never reorders someone else's mental shopping flow. Three operations is the entire write surface.

### 5.7 Bootstrap §8 auto-seed — four CoS-owned shared lists

At first run of bootstrap §8 (after Apple Reminders pairing succeeds and the central variant is connected to the assistant's Apple ID), the wizard auto-seeds four shared lists on the central account:

- **Family Groceries** — `list_kind: shopping`. The default grocery list. The system writes additions to this list when household members say things like "we're out of milk" in iMessage; the system observes checks when family members shop.
- **Family Errands** — `list_kind: errands`. Pickup/dropoff/return items the assistant should track.
- **Family Honey-Do** — `list_kind: todo`. Household maintenance and ad-hoc tasks where the request enters the list before being promoted (or not) to a `tasks` row.
- **Family Travel Prep** — `list_kind: travel_prep`. Pre-trip checklists; cleared after each trip.

Each list is created on the central Reminders account, then iCloud Shared List invitations are emitted to every adult and capable-teen family-member Apple ID enumerated in bootstrap §3. The wizard emits one `list.share_invited` event per invitation (carrying the iCloud share URL as `external_invitation_ref`) and surfaces a clear instruction to the operator: "Each invited member must accept the invitation on their device. Pending invitations appear in the Inbox until accepted."

Bridges then observe these lists appearing in their assigned member's Reminders.app post-acceptance (an iCloud sync delay of seconds-to-minutes). Each bridge emits a `list.shared` event scoped to its own member when the list first becomes visible. The `list_shares` projection-table row transitions from `pending` to `accepted` on the matching share_id.

**Acceptance failures degrade gracefully.** If a member never accepts an invitation (out-of-band reasons: declined, lost device, etc.), the share row stays `pending` and the inbox surfaces the pending share. The list itself functions on the central account; the central observes all writes; missing-bridge members simply don't see those lists in their personal Reminders.app until they accept.

### 5.8 `tasks` projection's `reminder.*` subscription is retired

Per D18 and UT-22 closure. The `tasks` projection on main today subscribes to `task.*` and `reminder.*` (per `docs/architecture-summary.md` §4 row 3.5 as it currently stands). Post-Amendment-2, the subscription drops `reminder.*`:

| Projection (post-Amendment-2) | Subscription |
|---|---|
| `tasks` | `task.*` |
| `lists` (NEW row 3.13) | `list.*`, `list_item.*` |

The `reminder.*` event family becomes deprecated. Any existing code that emits `reminder.*` (none on main today — Apple Reminders adapter is unbuilt; the events were forecast in BUILD.md §APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC line 1422 but not implemented) is replaced with `list.*` / `list_item.*` emissions. The forecast spec lines in BUILD.md get rewritten in PR-α-2 per §9.

### 5.9 New prompt 11f — `lists` projection build

The 11f addition to the build sequence is small: ship the `lists` / `list_items` / `list_shares` schema, the apply method for the 11 domain events + the 3 system observability events, the standard cursor + rebuild contract, and the queries module. Estimated 3–4 hours, single-prompt session. PROMPT_SEQUENCE row in §9.

### 5.10 Modifications to 11c-i / 11c-ii

The Apple Notes-checklist B-half lives in 11c-ii (the Apple Notes adapter prompt) per the existing primary-split in memo 04 §5.2. 11c-i (bridge daemon + ingest endpoint + connector-pack interface) is unchanged. 11c-ii's prompt body extends to add: the checklist parser, the second capability declaration in the Apple Notes pack manifest, the AppleScript write-back code path, the `list.created` / `list_item.added` emission alongside `note.added`, and the three `note.write_*` system observability events.

Whether this extension fits inside 11c-ii's existing budget (per `D-prompt-tier-and-pattern-index.md`'s entry on 11c-ii) is a question for 11c-ii's orientation session. The watch-flag discipline per PM-23 applies: if the extension pushes 11c-ii over §Per-prompt size budgets, propose a 11c-ii-α / 11c-ii-β split at orientation.

---

## 6. Cat-B sub-amendment — external-state-mirror Apple Calendar (D22)

### 6.1 Why Apple Calendar v1, why dual-deployment

The pre-Amendment-2 plan had Google Calendar as the only v1 calendar adapter (prompt 11b). For Apple-using households (Apple Calendar / iCal native, iCloud Family Calendar, member personal calendars on iCloud), this leaves the v1 calendar inventory empty on day one. The CoS Mac Mini's `calendars` projection is empty until either (a) the principal manually duplicates every calendar into Google Calendar, or (b) a v2 Apple Calendar adapter ships post-Phase-A.

Both choices are wrong. (a) discards the principal's existing calendar tooling and adds an indefinite duplicate-data-entry tax. (b) leaves Phase B without a working calendar surface for households whose calendar life is on iCloud, which is most households.

D22 closes the gap by making Apple Calendar v1 with the same dual-deployment shape as Apple Reminders. EventKit (`EKEventStore`, `EKEvent`, `EKCalendar`) on macOS is the access mechanism. Modifies prompt 11b scope.

### 6.2 Adapter shape

Manifest:

```yaml
id: apple-calendar
kind: adapter
runtime: dual
capabilities:
  - kind: cat_b_external_state_mirror
    event_families: [calendar.event]
    write_capabilities: [create_event, update_event, cancel_event,
                         add_attendee, change_attendee_response, add_reminder]
    sensitivity_default: normal           # operator can override per-calendar at install
    observation_mode_required: false      # state observation read-only;
                                          # write side gated by central outbound() per pipeline
                                          # that emits the create/update commands
    dedup_strategy:
      kind: composite_key
      key_fields: [external_calendar_id, external_event_id]
owner_scope_default: shared:household     # iCloud Family calendar = household;
                                          # individual member calendars override per §2.6
owner_scope_overridable_at_install: true
sharing_model_discriminator: sharing_model
deployments:
  central:
    capabilities: [cat_b_external_state_mirror]
    config_schema: schemas/central-config.yaml
  bridge:
    capabilities: [cat_b_external_state_mirror]
    config_schema: schemas/bridge-config.yaml
```

Central variant: assistant's Apple ID, observes CoS-owned calendars (e.g. "Family" calendar via iCloud Family Sharing, "Work-Life Logistics" if the principal puts coordinative work events on the assistant's account, etc.).

Bridge variant: each member bridge's Apple ID, observes that member's personal calendars plus their side of any shared calendars. Same code, different deployment configuration.

### 6.3 Deduplication

Per `dedup_strategy.composite_key` on `(external_calendar_id, external_event_id)`, where `external_calendar_id = EKCalendar.calendarIdentifier` and `external_event_id = EKEvent.eventIdentifier`. iCloud-shared calendars surface in multiple Apple ID accounts; the calendar identifier and event identifier are stable across observers, so the projection deduplicates the same calendar event observed from both the central account and a member bridge.

### 6.4 New event family — `calendar.event.*` (5 types at v1)

Per [D7]:

- `calendar.event.created@v1` — new event observed (upstream creation or CoS-native add).
- `calendar.event.updated@v1` — title / time / location / attendees / description change.
- `calendar.event.deleted@v1` — event deleted upstream (or CoS-native cancel).
- `calendar.event.attendee_response_changed@v1` — an attendee's response (accepted / declined / tentative / no_response) changed.
- `calendar.event.reminder_added@v1` — a calendar-side reminder/alert was added to the event (distinct from a `task.reminded` — this is the calendar app's native alarm).

The pre-existing `calendar.*` event family on main (per `docs/architecture-summary.md` §4 row 3.7) carries higher-level events emitted by other layers (`calendar.event.concluded` etc.). The Apple Calendar adapter's emissions slot in alongside without renaming.

### 6.5 Feeds existing `calendars` projection

No new projection. The `calendars` projection on main subscribes to `calendar.*` and writes to `calendar_events` + `availability_blocks`. Post-Amendment-2 it gains `calendar.event.*` in its subscription pattern (which is a strict subset of `calendar.*` in glob terms — already covered, but explicit broadening worthwhile for clarity in the PR-α-2 update to `docs/architecture-summary.md` §4 row 3.7).

The `sharing_model` discriminator from §2.4 is added as a new column on `calendar_events` (alongside the existing `privacy` column). This matters because the "Family" iCloud calendar (sharing_model = `icloud_family`) needs a different read-time visibility filter than the assistant's private "Work-Life Logistics" calendar (sharing_model = `private`). PR-α-2 adds the column via a migration.

### 6.6 Modifies prompt 11b scope

Pre-Amendment-2 11b ships Apple Reminders + Google Calendar + CalDAV. Post-Amendment-2 11b ships Apple Reminders (dual-deployment per §5.4) + Google Calendar (existing) + Apple Calendar (NEW dual-deployment) + Google Tasks (NEW central per §5.5). CalDAV is deferred to v2 per D25 (§4.2 of this memo).

Whether 11b fits a single Claude Code session post-Amendment-2 — four bidirectional adapters with three write-back surfaces — is unlikely. Per the watch-flag discipline (PM-23), 11b orientation in a future session must propose a split. Reasonable forecast: 11b-i (Apple Reminders + Apple Calendar dual-deployment, since they share the EventKit / iCloud / dual-deployment scaffolding) and 11b-ii (Google Calendar + Google Tasks central, which share OAuth 2 + Google API scaffolding). PR-β-2 records 11b as a pre-split candidate; the actual split memo lands at 11b orientation.

---

## 7. Cat-B sub-amendment — Contacts (D23)

### 7.1 The CRM-spine-empty-on-day-1 gap

The CRM is the spine of the system per BUILD.md §THE CRM IS THE SPINE OF THIS SYSTEM. The `parties` projection's `parties.identifiers` table is the keystone — every inbound message, every commitment, every relationship is anchored on a party identified by some external identifier (phone number, email, iMessage handle, etc.).

On day 1 of Phase B post-bootstrap, this table is nearly empty. The principal has filled in their family in bootstrap §3 (`party.created` + `membership.added` for each household member). Beyond that, the CRM populates only from inbound messaging: as iMessage / Telegram / Discord / Gmail messages arrive, the messaging adapters discover identifiers and emit `party.identifier_observed` events.

This is structurally too slow. A household has hundreds of contacts the principal already knows about — friends, family beyond the household, doctors, schools, vendors, neighbors. Without seeding from the principal's existing contacts store, the `crm_surface` proactive pipeline (10c-iii) has nothing to surface for weeks. The "CRM is the spine" claim in BUILD.md falls flat in practice during the observation period and the first weeks of live operation.

D23 closes the gap with two adapters:

- **Apple Contacts** — bridge runtime, per-member iCloud, via `Contacts.framework`. Reads each member's iCloud contacts and emits `contact.*` events scoped `private:<member>`.
- **Google Contacts** — central runtime, via Google People API. Reads the assistant's Google Workspace contacts (or, per owner-scope override at install, a household-shared Google account if the household runs one).

Both adapters feed the existing `parties.identifiers` table. New prompt **11e** ships both, co-located because they share the contacts-domain shape.

### 7.2 Adapter shapes

**Apple Contacts manifest:**

```yaml
id: apple-contacts
kind: adapter
runtime: bridge                           # per-member only
capabilities:
  - kind: cat_b_external_state_mirror
    event_families: [contact, party.identifier]
    write_capabilities: []                # v1 read-only;
                                          # write-back is Cat-B-conformant but deferred
    sensitivity_default: normal
    observation_mode_required: false
    dedup_strategy:
      kind: external_id_unique
      key_fields: [external_id_kind, external_contact_id]
owner_scope_default: private:per_member   # bridge runtime; Tailscale stamps at :3337
owner_scope_overridable_at_install: false
sharing_model_discriminator: sharing_model  # 'private' | 'icloud_shared_group'
```

**Google Contacts manifest:**

```yaml
id: google-contacts
kind: adapter
runtime: central
capabilities:
  - kind: cat_b_external_state_mirror
    event_families: [contact, party.identifier]
    write_capabilities: []                # v1 read-only
    sensitivity_default: normal
    observation_mode_required: false
    dedup_strategy:
      kind: external_id_unique
      key_fields: [external_id_kind, external_contact_id]
owner_scope_default: shared:household     # default for the assistant's Workspace contacts
owner_scope_overridable_at_install: true  # per §2.6 — operator can scope to private:<member>
                                          # if the underlying Google account is member-personal
sharing_model_discriminator: sharing_model  # 'private' | 'shared_household' | 'shared_org:<id>'
```

Both adapters emit two parallel event families on every observation:

- `contact.*` — the contact-domain events (canonical members emitted: `contact.added@v1`, `contact.updated@v1`, `contact.deleted@v1`, `contact.merged@v1` for upstream merge operations).
- `party.identifier_observed@v1` — the cross-projection feed into `parties.identifiers`. The same identifier (phone number, email, iMessage handle) may already exist in `parties.identifiers` from prior message observation; the dedup is on `(party_id, identifier_kind, identifier_value)` already in the projection schema.

The `contact.merged@v1` event handles the case where the principal merges two contacts in Apple Contacts or Google Contacts. The adapter detects the merge (the upstream API exposes it) and emits `contact.merged` with the surviving and absorbed external IDs; the `parties` projection's identity-resolution pipeline (10b-i) consumes this and proposes a corresponding `identity.merge_suggested` event for the corresponding party rows. Confirmation goes through the standard `identity.merge_confirmed` flow already on main.

### 7.3 Owner-scope semantics

**Apple Contacts.** Bridge per member; Tailscale identity at the `:3337` ingest endpoint stamps `owner_scope = private:<member_id>`. Per memo 04 / D17 / [§6.19], this is the correct stamping mechanism. iCloud Shared Groups (a feature where a member shares a contact group with other family-member Apple IDs) emit `sharing_model = icloud_shared_group` on their rows; the projection layer treats those rows as visible to all members in the share but owned by the originating member.

**Google Contacts.** Central runtime; the assistant's Workspace account (or whatever Google account is configured at install) is the single Google Contacts source. Default `owner_scope = shared:household` because the assistant's Workspace is conceived as household-shared infrastructure. Operator can override at install per §2.6 — for example, a household where each adult has their own Google Workspace would install separate `google-contacts` adapter instances, each scoped `private:<member>`, against each member's Google account.

### 7.4 Feeds `parties.identifiers` (closes the gap)

The `parties` projection (BUILD.md §3.1) writes to `parties` + `identifiers` + `memberships` + `relationships`. The `identifiers` table is what matters for the CRM-spine-empty-on-day-1 gap. Post-Amendment-2, the projection's subscription pattern adds `contact.*` and `party.identifier_observed` (the latter overlaps with the messaging adapters' existing emission and dedup is already handled).

After bootstrap §8 finishes, the household-shared Google Contacts and each member's Apple Contacts bulk-load into `parties.identifiers`. The `crm_surface` proactive pipeline (10c-iii) immediately has rows to surface against. The CRM is the spine on day 1, not week 4.

### 7.5 New prompt 11e

Net-new per PR-β-2. Estimated 3–4 hours. Ships:

- Apple Contacts adapter under `bridge/adapters/apple_contacts/` (bridge-side per memo 04 PM-29).
- Google Contacts adapter under `adminme/adapters/google_contacts/` (central).
- `contact.*` event schemas at v1 in `adminme.events.schemas.domain` (4 types).
- `party.identifier_observed@v1` schema (or version bump if it already exists; verify at refactor).
- Apple Contacts uses `Contacts.framework` via PyObjC binding, parallel to how 11c-ii uses Apple Notes. Google Contacts uses `googleapiclient`'s People API client.
- Tests: contract tests for both adapters; integration test that exercises bulk-load → `party.identifier_observed` → `parties.identifiers` round-trip.

---

## 8. Cat-C+E sub-amendment — Home Assistant (D24)

### 8.1 Why Home Assistant, why multi-capability, why Phase A

The Cat-E (Outbound-Action) category needs at least one v1 reference implementation per PM-34. Without it, the framework's Cat-E base class (`OutboundActionAdapter`) is theoretical and the observation-mode integration for outbound actions has no test surface. PM-32's strengthened addendum (2026-04-29-B) makes this concrete: the Capture/Automation routers in BUILD.md §L5 already reference HA — `/api/automation/ha/*` — and per the PM-32 rule, those routers either have a real backing adapter in Phase A scope or they get removed. Removing them is a regression in the surface layer; building them is the better path.

Home Assistant is the right v1 Cat-E reference for three reasons. (i) It exposes a clean REST + WebSocket API with explicit service semantics (every "do X" action is a service call with a structured request/response shape), matching the Cat-E base class's shape exactly. (ii) Many AdministrateMe target households already run HA — it's the open-source home-automation hub of choice. (iii) It naturally pairs with a Cat-C state-read seam (HA exposes the entire entity state stream as a separate WebSocket subscription), making it a natural multi-capability adapter and the canonical PM-35 multi-capability example.

D24 makes Home Assistant Cat-C+E reference, multi-capability, full bidirectional, in Phase A. New prompt **11g**.

### 8.2 Adapter shape — multi-capability, two seams

```yaml
id: home-assistant
kind: adapter
runtime: central
capabilities:
  - kind: cat_c_inbound_data              # state-read seam
    event_families: [ha.state_changed]
    write_capabilities: []                # Cat-C is inbound-only by definition
    sensitivity_default: normal           # operator can override per-domain
                                          # at install (e.g. mark `medical.*` privileged)
    observation_mode_required: false      # reading is not a side-effect
  - kind: cat_e_outbound_action           # service-call seam
    event_families: [ha.service_call]     # carries action.requested / action.executed /
                                          # action.failed shape per OutboundActionAdapter
    write_capabilities: [call_service]    # the single Cat-E verb
    sensitivity_default: normal
    observation_mode_required: true       # service calls are external side-effects
                                          # subject to observation mode
owner_scope_default: shared:household     # household-level default
owner_scope_overridable_at_install: true
sharing_model_discriminator: ~            # not applicable for HA;
                                          # the single HA instance is the canonical source
```

The Cat-C seam subscribes to HA's WebSocket `subscribe_events` channel for state changes (or polls the REST `/api/states` endpoint as fallback). For each state transition, it emits one `ha.state_changed@v1` event with `entity_id`, `from_state`, `to_state`, `attributes_changed`, and a timestamp. These events feed into a (future, post-v1) `home_state` projection if one is built; v1 sets up the event stream so projections / pipelines / surfaces can consume it without further adapter work.

The Cat-E seam consumes `ha.service_call_requested@v1` events emitted from elsewhere in the system — typically from a slash command (`/lights off`), a pipeline, or a console action. The adapter receives the requested event, calls HA's REST `POST /api/services/<domain>/<service>` with the requested payload, and emits `action.executed@v1` (Cat-E base class shape) on success or `action.failed@v1` on error.

### 8.3 Observation-mode integration

The single most important Phase-A test of `OutboundActionAdapter`'s observation-mode integration is HA. When `observation_mode = active`, the Cat-E seam:

- Receives `ha.service_call_requested@v1`.
- Does NOT call HA's REST endpoint.
- Emits `observation.suppressed@v1` with the full would-have-sent payload (the service domain, service name, request body, target entity, requesting actor, the event_id of the source request).
- Does NOT emit `action.executed@v1` (because nothing was executed).
- The Settings → Observation pane in the console shows the suppressed call in the operator's review log, just like a suppressed iMessage.

This is the canonical PR-γ-2 test case for the framework's observation-mode integration. The Cat-A messaging-outbound side already has `outbound()` per [§6.14] / `adminme/lib/observation.py`; the Cat-E HA service-call seam exercises the same `outbound()` seam at the action level instead of the message level. Same enforcement point, different verb.

State-read (Cat-C) is unaffected by observation mode. Reading "is the porch light on" is observation, not action; the principle in [§6.13] ("internal logic runs normally; only the external side effect is suppressed") clarifies that reading external state is internal logic, not external side effect. PR-α-2's [§6] update makes this explicit so future readers don't misinterpret `observation_mode_required: false` on a Cat-C capability.

### 8.4 Sensitivity defaulting

`sensitivity_default: normal` for the Cat-C seam is correct for most domains: knowing the porch light is on is not privileged information. For domains that ARE privileged (e.g. an HA blueprint that integrates with a medical device, an HA `sensor` exposing whether the bathroom is occupied), the operator overrides per-domain at install. The override mechanism is in the Cat-C base class config schema; the install-time validator (§3.4) checks that any privileged domain has a corresponding entry in the override table.

The Cat-E seam's `sensitivity_default: normal` matters less — Cat-E events carry the action that was attempted, not state. A privileged action (e.g. unlocking the front door) gets the privileged tag from the requesting actor / context, not from the adapter's manifest default.

### 8.5 Closes UT-23, closes the `/api/automation/ha/*` orphan

UT-23 (Cat-E reference implementation) is closed by HA being the v1 Cat-E reference. The `/api/automation/ha/*` routers in BUILD.md §L5 (currently described as forecast surface in 13b's prompt body) gain real backing per PM-32's strengthened addendum: the routers post `ha.service_call_requested` events into the event log, which the Cat-E seam consumes; the routers GET state for their respective views from a state-cache populated by the Cat-C seam (or directly from HA via the adapter's pass-through query method, in the v1 simplest implementation).

### 8.6 New prompt 11g

Net-new per PR-β-2. Estimated 3–4 hours. Ships:

- Adapter under `adminme/adapters/home_assistant/` with two capability classes (one inheriting `InboundDataAdapter`, one inheriting `OutboundActionAdapter`).
- `ha.state_changed@v1`, `ha.service_call_requested@v1`, plus the `OutboundActionAdapter` standard `action.executed@v1` / `action.failed@v1` event schemas.
- Pack manifest with both capabilities, validated against §3.4 install-time checks.
- Tests: a state-read contract test (mock HA WebSocket), a service-call contract test (mock HA REST), an observation-mode integration test (Cat-E seam with `observation_mode = active`, asserts `observation.suppressed` emitted and HA REST endpoint NOT called).
- Wires the `/api/automation/ha/*` routers (in 13b post-Amendment-2) to the adapter via the standard event-emission pattern.

---

## 9. PR plan

Four single-purpose PRs. Per `docs/partner_handoff.md` 2026-04-29-B and PM-22.

### 9.1 Memo PR — `arch-amendment-2-memo-personal-data-layer`

**Single new file:** `docs/05-architecture-amendment-personal-data-layer.md` (this memo, §1–§9 combined).

**Single commit, doc-only, no tests, no BUILD_LOG entry per PM-22.**

Drafted by Sessions A-1 + A-2. Session A-2 (this session) closes the drafting work. James commits the memo PR as the gate before A-3 begins.

### 9.2 PR-α-2 — `arch-amendment-2-pr-alpha-2`

**Constitutional doc updates per the file-by-file landing below.** Per PM-24 hybrid two-commit pattern (precedent: PR-α landed 2026-04-29 cleanly using this pattern):

- **Commit 1** (Claude Code, `str_replace`): updates to `docs/architecture-summary.md`, `docs/SYSTEM_INVARIANTS.md`, `docs/DECISIONS.md`, `ADMINISTRATEME_DIAGRAMS.md`, `docs/openclaw-cheatsheet.md`. These are character-precise edits that Claude Code's `str_replace` handles well.
- **Commit 2** (GitHub web UI): substantive additions to `ADMINISTRATEME_BUILD.md`. The new §ADAPTER TAXONOMY and §ADAPTER FRAMEWORK sections plus the §LISTS section are large enough to land via web UI per PM-24.

Drafted by Session A-3.

#### 9.2.1 `ADMINISTRATEME_BUILD.md` (Commit 2 — GitHub web UI)

- **§THE ARCHITECTURE — FIVE LAYERS, L1 box.** Rewrite the L1 paragraph to reflect the five-category taxonomy. The current text frames L1 as "channel-family-specific translators that read from external sources" with a runtime-substrate split (channel adapters / data adapters / knowledge-source adapters per the Conception-C amendment). New text frames L1 as "five categories by epistemic role with runtime as orthogonal axis," cross-references the new §ADAPTER TAXONOMY and §ADAPTER FRAMEWORK sections.
- **NEW §ADAPTER TAXONOMY section** (after §L1, before §PLAID — DETAILED SPEC). Imports memo §1 and §2 as the canonical taxonomy reference. Subsections: §1.1 The framing change, §1.2 The five categories (A–E with one-paragraph defining property each), §2.1 Two-axis model, §2.2 Capabilities-as-list, §2.3 Asymmetric write-capabilities, §2.4 Sensitivity-default and observation-mode-required, §2.5 Sharing-model discriminator, §2.6 Owner-scope-overridability.
- **NEW §ADAPTER FRAMEWORK section** (after §ADAPTER TAXONOMY). Imports memo §3. Subsections: §3.1 Manifest format extensions (the YAML schema), §3.2 Five abstract base classes (one paragraph per ABC + abstract methods listed), §3.3 Discovery and registration CLI (the `adminme adapters` subcommand list), §3.4 Install-time validation extensions (capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence, signature check), §3.5 Three-layer developer mode, §3.6 Authoring guide reference.
- **§L3 PROJECTIONS preamble** — bump count from "11 projections" to "13 projections" (or whatever the current count is post-Conception-C — verify at refactor; arch-summary §4 already shows 12 with `member_knowledge`).
- **§3.5 `tasks` projection.** Drop "& reminder.*" from the subscription list (UT-22 closure).
- **NEW §3.13 `lists` projection — household-mirrored lists** (after §3.12 `member_knowledge`). Imports memo §5.2 SQL schema and §5.3 event family. Three tables (`lists`, `list_items`, `list_shares`), 11 domain events + 3 observability events, the `promoted_task_id` cross-link to `tasks`.
- **§APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC.** Rewrite to match Cat-B + dual-deployment + `list.*` event family (replaces the existing `reminder.*` forecast). Per memo §5.4. Manifest example updated to the post-Amendment-2 shape.
- **NEW §APPLE CALENDAR BIDIRECTIONAL — DETAILED SPEC** (after §APPLE REMINDERS BIDIRECTIONAL). Per memo §6. EventKit + dual-deployment + `calendar.event.*` event family.
- **NEW §GOOGLE TASKS — DETAILED SPEC** (after §APPLE CALENDAR). Per memo §5.5. Central + REST API + OAuth 2.
- **NEW §APPLE CONTACTS — DETAILED SPEC** (after §GOOGLE TASKS). Per memo §7. Bridge per-member + Contacts.framework. Plus parallel **NEW §GOOGLE CONTACTS — DETAILED SPEC**.
- **NEW §APPLE NOTES-CHECKLISTS CROSS-CAT EXTENSION** (after §APPLE CONTACTS, or appended to existing §APPLE NOTES section). Per memo §5.6. The B-half of the Apple Notes adapter; AppleScript write-back; toggle+add only.
- **NEW §HOME ASSISTANT — DETAILED SPEC** (after the Notes section). Per memo §8. Multi-capability Cat-C + Cat-E; observation-mode integration on the Cat-E half.
- **§L5 — Capture (Product C) router list.** Add `/api/capture/lists` — read surface for lists (parallel to `/api/capture/parties`). Read-only on the surface; writes go through the standard event-emission path on the inbound side.
- **§L5 — Automation (Product D) router list.** Add `/api/automation/ha/state` (GET, returns latest cached state per entity), `/api/automation/ha/services` (POST `ha.service_call_requested` event with the body the router exposes — same shape as HA's REST `services` endpoint to keep pass-through simple). Per memo §8.5 + PM-32 ADDENDUM.
- **§BOOTSTRAP WIZARD §5 — Assistant credentials.** Existing entry already lists Home Assistant token (optional). Per PM-32 ADDENDUM, this is now non-optional in installs that enable HA; the optional flag remains for installs that don't run HA. No structural change; clarifying language.
- **§BOOTSTRAP WIZARD §8 — Channel pairing.** Add four sub-steps:
  - Lists auto-seed (per memo §5.7) — four CoS-owned shared lists created on the assistant's Apple ID + iCloud Shared List invitations to all adult+capable-teen family-member Apple IDs.
  - Apple Calendar pairing (per memo §6) — central variant on assistant's Apple ID; observation set up; first calendar pull tested.
  - Apple Contacts pairing prep — at the central level there is no Apple Contacts pairing (Apple Contacts is bridge-only per §7); §10 expansions cover the bridge side. But §8 records the Google Contacts central pairing here: OAuth flow with assistant's Workspace, first contacts pull tested.
  - Home Assistant pairing — long-lived access token, REST + WebSocket connection tested, `ha.state_changed` events flowing into the event log.
- **§BOOTSTRAP WIZARD §10 — Bridge enrollment.** Extend the per-bridge enrollment package to include the new adapters: Apple Calendar (bridge variant), Apple Contacts. The kid-bridge variant excludes Apple Contacts of adult contact lists (per §6.19 / D17 kid-bridge restriction principle — kids' bridges only ingest the kid's own Notes + Voice Notes; contacts that are not the kid's own get filtered).

#### 9.2.2 `docs/architecture-summary.md` (Commit 1 — `str_replace`)

- **§1 The five-layer model.** Rewrite the L1 paragraph to reflect the five-category taxonomy (mirrors the BUILD.md §L1 rewrite at compact-summary scale).
- **§4 The 12 projections table** — change to 13 projections; add **row 3.13 `lists`** with subscription `list.*`, `list_item.*` and tables `lists`, `list_items`, `list_shares`. Caption per memo §5.
- **§4 row 3.5** — drop `reminder.*` from the `tasks` projection's subscription column.
- **§4 row 3.12** — annotate that note checklists also feed the new `lists` projection (cross-reference to row 3.13).
- **§9 Products preamble.** Brief addition: Capture's router list now includes `/api/capture/lists`; Automation's router list now includes `/api/automation/ha/*` with real backing.
- **§10 Security and privacy / observation mode.** Brief addition: Cat-E adapters integrate observation mode at the action verb level via the same `outbound()` seam Cat-A messaging adapters use; the seam is unified by the `OutboundActionAdapter` base class.
- **§11 Open questions / item 4.** Already closed per D17 (Capture is a read surface). Status: keep the historical record.

#### 9.2.3 `docs/SYSTEM_INVARIANTS.md` (Commit 1 — `str_replace`)

- **NEW §6.20 (or next available §6 sub-clause)** — observation-mode integration applies at the action verb for Cat-E adapters, same as it applies at the message verb for Cat-A messaging adapters; the `outbound()` seam in `adminme/lib/observation.py` is the single enforcement point for both. (This codifies the principle that Cat-E ⊕ observation_mode_required = true is the framework default, per memo §8.3.)
- **NEW §8.10 (or next available §8 sub-clause)** — adapter categorization is by epistemic role (five categories), not by runtime substrate; runtime is an orthogonal secondary axis (per D19); multi-capability adapters declare each capability as its own seam (per PM-35).
- **NEW §LISTS SEMANTICS (parallel to §4 commitments / tasks / recurrences)** — list items are not tasks; promotion creates a task without modifying the list item; `(external_id_kind, external_list_id)` deduplication for shared lists; sharing-model discriminator on every `lists` row.

#### 9.2.4 `docs/DECISIONS.md` (Commit 1 — `str_replace` append)

Append D18 through D25 entries verbatim per the partner_handoff text recorded 2026-04-29-B. Each entry follows the standard format (Decided / Status: CONFIRMED / Resolves / body / Corollaries if applicable).

#### 9.2.5 `ADMINISTRATEME_DIAGRAMS.md` (Commit 1 — `str_replace`)

- **§1 Five-layer architecture, L1 box.** Update the ASCII art to represent five categories rather than three runtime variants. The five-category groupings stack vertically inside L1; the runtime-axis annotation appears as a side-label per category indicating which runtime each occupies.
- **§2 Event flow.** Add **two new canonical examples** (memo §5/§8 motivated):
  - "One new Family Groceries item added by Laura on iPhone → bridge → ingest → `lists` projection → console refresh on every connected family member's Today view." Mirrors the existing iMessage-to-confirmed-commitment example, illustrates Cat-B round-trip on the inbound side.
  - "Slash command `/lights off` typed in iMessage → OpenClaw routes to AdministrateMe slash handler → emits `ha.service_call_requested` → HA Cat-E seam → `outbound()` → either HA REST POST + `action.executed`, OR `observation.suppressed` if observation_mode active." Illustrates Cat-E with observation-mode integration.
- **§10 Bootstrap wizard state machine.** Update the §8 sub-step boxes to add the four new pairings (lists auto-seed, Apple Calendar, Contacts, Home Assistant). The §10 bridge-enrollment box gains "Apple Calendar bridge variant" and "Apple Contacts" line items.

#### 9.2.6 `docs/openclaw-cheatsheet.md` (Commit 1 — `str_replace`)

Mostly unaffected by Amendment-2. Two clarifications worth landing:

- **Q1 / Q4 footer.** Add a one-line note: AdministrateMe adapters with `kind: adapter` install through the same pack-registry path the cheatsheet describes; the five-category taxonomy refines what the manifest declares but does not change the install lifecycle.

### 9.3 PR-β-2 — `arch-amendment-2-sequence-updates`

**Single file:** `prompts/PROMPT_SEQUENCE.md`. Single commit, doc-only.

Drafted by Session A-4.

#### 9.3.1 Modified rows

- **Row 11** — `11-standalone-adapters.md`. Body extended: "(framework expansion: five base classes per §3.2 + manifest spec per §3.1 + install-time validator per §3.4 + `adminme adapters` CLI per §3.3 + authoring guide per §3.6)." Estimate updated. Tier flips to **Introduction** if it wasn't already (this is the framework prompt — the most important pattern-introduction in the entire L1 sequence). Pre-split disposition: pre-split candidate.
- **Row 11a** (if it exists in the current sequence — verify at refactor) — Gmail / Plaid central adapters become the first two reference implementations of `CommunicationAdapter` and `InboundDataAdapter` respectively. Body adds "first reference implementation of base class X." No structural change.
- **Row 11b** — Apple Reminders + Google Calendar + CalDAV → Apple Reminders (dual-deployment per §5.4) + Apple Calendar (NEW dual-deployment per §6) + Google Calendar (existing, central) + Google Tasks (NEW central per §5.5); CalDAV deferred per D25. Body rewritten. Pre-split candidate per memo §6.6 watch (Apple-side dual-deployment + Google-side central likely too much for one session).
- **Row 11c-i** — unchanged.
- **Row 11c-ii** — body extended for Apple Notes-checklist B-half per memo §5.6. Watch flag added per memo §5.10. Pre-split disposition: re-evaluate at orientation; secondary-split candidate.

#### 9.3.2 New rows

- **Row 11e** — `11e-contacts-adapters.md`. Apple Contacts (bridge per-member, Contacts.framework) + Google Contacts (central, People API). Per memo §7. 3–4 hours.
- **Row 11f** — `11f-lists-projection.md`. The 13th projection: `lists`. Tables `lists` / `list_items` / `list_shares`; 11 domain events + 3 system observability events; standard cursor / rebuild / queries. Per memo §5.9. 3–4 hours.
- **Row 11g** — `11g-home-assistant-adapter.md`. HA adapter, multi-capability Cat-C + Cat-E, full bidirectional with observation-mode integration on the Cat-E seam. Per memo §8.6. 3–4 hours.

#### 9.3.3 Dependency-graph ASCII

Update the dependency-graph block to add the new edges:

- 11 → 11e, 11 → 11f, 11 → 11g (each new prompt depends on the framework being in place).
- 11f after 11b (Cat-B adapters need the projection to write to; though strictly 11b emits events the projection consumes after rebuild, so the order is interchangeable — keep 11f after 11b for readability).
- 11g after 11 (only depends on framework, can run in parallel with the other 11x prompts in principle).

#### 9.3.4 Hard sequential dependencies

- 11 → 11a, 11b, 11c-i, 11c-ii, 11d, 11e, 11f, 11g (framework first; everything else flows from it).
- 11f either before or after 11b (interchangeable per dependency-graph note above; recommend 11f after 11b for incremental visibility).
- 11d depends on 11c (unchanged).

#### 9.3.5 Pre-split candidates

- 11 — pre-split candidate (framework + 5 base classes + manifest + validator + CLI + authoring guide is too much for one session).
- 11b — pre-split candidate (Apple-side dual-deployment + Google-side central; secondary forecast per memo §6.6).
- 11c-ii — secondary-split candidate (knowledge-source adapter + checklist B-half + AppleScript write-back; watch per memo §5.10).

#### 9.3.6 Total estimate

Update from "93–123 hours" to "~105–141 hours" per the partner_handoff scope-impact block. Net new prompts add ~9–12 hours; modified prompts add ~3–6 hours.

### 9.4 PR-γ-2 — `arch-amendment-2-handoff-snapshot`

**Single file:** `docs/partner_handoff.md`. Single commit, doc-only.

Drafted by Session A-4 alongside PR-β-2.

Roll-forward of all standing sections:

- **Current build state.** Move from "Amendment-2 cycle initiated" to "Amendment-2 cycle complete; next refactor target is 10c-ii." All A-1 / A-2 / A-3 / A-4 stage history compressed into one paragraph per the partner_handoff "compress historical entries" convention.
- **Prompt-writing decisions (PMs).** Standing list updated with PM-30 through PM-35 per the partner_handoff 2026-04-29-B entries (already drafted in the partner_handoff during the consultation; PR-γ-2 just rolls them into the standing list).
- **Open tensions (UTs).** UT-19 through UT-30 entered as tracked items; UT-22, UT-23, UT-24 marked RESOLVED with their D-decision references; UT-25 marked DEFERRED; the rest OPEN with their resolution-at-prompt-X annotations.
- **Phase A scope impact.** Updated from 93–123 hours to 105–141 hours; net new prompts and modified prompts enumerated (already in 2026-04-29-B entry).
- **Next-task queue.** Becomes:
  1. Build cadence resumes.
  2. Partner Type 1/2 session: 10c-ii orientation + refactor (proactive pipelines `morning_digest` + `paralysis_detection`).
  3. … (10c-iii, 10d, 11, 11a, 11b, 11c-i, 11c-ii, 11d, 11e, 11f, 11g, 12, 13a, 13b, 14a, 14b, 14c, 14d, 14e, 15, 15.5, 16, 17, 18, 19).

The partner_handoff already encodes this trajectory in its 2026-04-29-B entry; PR-γ-2 just promotes it from "in-flight" to "standing state."

---

## 10. Self-check

Per E-session-protocol §2.9 delivery-gate self-check, applied to a Type 0 architecture-amendment session. The literal item-by-item checklist is built for refactored prompts targeting Claude Code execution; the spirit applies here.

1. **Total memo size.** Combined memo §1–§9 is ~1,250 lines / ~110 KB. Above the 350-line / 25-KB refactored-prompt budget, but appropriate for a Tier C architecture-amendment memo (memo 04 was 480 lines; the increased scope of Amendment-2 — five categories + framework + four sub-amendments + four-PR plan — justifies the size). The downstream PR-α-2, PR-β-2, PR-γ-2 sessions are bounded; the memo is the reference for them.

2. **Per-PR estimate at landing time.**
   - Memo PR: 1 file, ~1,250 lines added. Single commit. Within Claude Code's per-commit 600-line budget for new doc content (this is doc-only, but at the edge — and PM-22 carves out infrastructure PRs from the four-commit discipline anyway).
   - PR-α-2 Commit 1: 5 files, character-precise `str_replace` edits. Within budget.
   - PR-α-2 Commit 2: 1 file (`ADMINISTRATEME_BUILD.md`) with substantive additions; per PM-24 lands via GitHub web UI. Out of Claude Code's str_replace budget by design.
   - PR-β-2: 1 file, 1 commit. Within budget.
   - PR-γ-2: 1 file, 1 commit. Within budget.

3. **Files to read before each PR.** Memo PR has zero deps (just commits the memo). PR-α-2 reads the five constitutional docs + this memo (per file-by-file landing). PR-β-2 reads `prompts/PROMPT_SEQUENCE.md` + `D-prompt-tier-and-pattern-index.md` + this memo. PR-γ-2 reads `docs/partner_handoff.md` + this memo. All within the 6-files-per-commit budget.

4. **Inline implementation code.** Zero. The memo cites the manifest YAML (in §3 from Session A-1; in §5/§6/§7/§8 here), the SQL schema (§5.2 only), and the event family (§5.3, §6.4, §7.2, §8.2). All are spec-level prose; PM-8 inline-code-cap of 40 lines does not apply to memo declarative content.

5. **Single-cycle judgment.** PR-α-2 Commit 1 is the highest-risk session: 5 files of `str_replace` edits, each with multiple distinct edits. Memo 04's PR-α landed cleanly with 4 files of `str_replace` edits per the 2026-04-29 partner_handoff record; PR-α-2's 5 files is a small extension. Should fit one Claude Code session.

6. **Symbol-name verification.** Symbols cited:
   - `outbound()` in `adminme/lib/observation.py` — confirmed at line 229 in the codebase zip.
   - `OutboundActionAdapter`, `CommunicationAdapter`, `ExternalStateMirrorAdapter`, `InboundDataAdapter`, `PersonalKnowledgeAdapter` — these ABCs do NOT yet exist on main; they ship in prompt 11 per memo §3.2. The memo cites them as the framework deliverable, not as already-shipped symbols. No drift.
   - `parties.identifiers` table — exists per `docs/architecture-summary.md` §4 row 3.1. Confirmed.
   - `tasks` projection's `reminder.*` subscription — exists per `docs/architecture-summary.md` §4 row 3.5. Confirmed (this is what PR-α-2 retires).
   - `:3337 bridge` ingest endpoint — exists per `docs/architecture-summary.md` §4 + memo 04 PR-α landing. Confirmed.
   - `member_knowledge` projection (12th) — exists per `docs/architecture-summary.md` §4 row 3.12 post-PR-α landing. Confirmed.

7. **Autolinker defense.** Mandatory grep pass (per E-session-protocol §2.10):

   ```
   grep -nE '[^`]([a-zA-Z0-9_./-]+\.(md|sh|py|yaml|yml|json|toml|io|co))[^`]' memo
   ```

   Run by Partner during artifact production; any unbacked tokens in prose are corrected before this memo ships as the closing artifact. The closing artifact is delivered as a downloadable file via the file-creation tool, not inline chat content, per PM-25.

The memo is ready for James to commit as the Memo PR. Sessions A-3 and A-4 follow.

---

[END OF MEMO. Sections §1–§9 complete. Ready for the Memo PR per §9.1.]
