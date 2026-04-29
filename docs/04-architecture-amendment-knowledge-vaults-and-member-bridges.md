# Tier C architecture amendment — Knowledge vaults and member bridges

**Author:** AdministrateMe Build Supervision Partner
**Date:** 2026-04-29
**Disposition:** Architecture amendment (Conception C correction)
**Verdict:** **AMEND.** Capture-as-task-input-pipeline framing is replaced with Capture-as-knowledge-surface-over-per-member-bridge-ingested-knowledge-vaults. Three single-purpose PRs land the correction.
**Authority:** James, 2026-04-29 (this session) — confirmed Conception (A) was binding intent and (B) was translation drift.

---

## 1. Context

### 1.1 What was wrong

Across `ADMINISTRATEME_BUILD.md` §L5-continued, `docs/architecture-summary.md` §9, `prompts/13b-product-apis-capture-auto.md` (unrefactored), and the four console-views prompts (14a–14d), the Capture product (`:3335`) was framed as a **task-input pipeline with triage**:

> Owns quick-capture (natural-language prefix routing: `grocery:`, `call:`, `idea:`, `recipe:`), voice-note ingest, triage queue, recipes, CRM Parties/Places/Assets/Accounts views, semantic + structured search…

This framing — confirmed by James as drift from binding intent — treats Capture as the **place where input happens** and tasks/commitments/people/events as **derivatives** that pipelines extract from a triage queue. The mental model is: humans dump unstructured stuff into Capture; pipelines pull tasks out.

The binding intent is the inverse: Capture is **a read surface over a knowledge layer**. The knowledge layer is per-family-member, bridges into household-shared knowledge, and is fed by the family member's *own* tools-of-choice for capturing knowledge — Apple Notes, Voice Notes, optionally Obsidian or other knowledge systems. Tasks, commitments, people, and events are **events derived by reactive pipelines** from the knowledge events flowing in, not items polled from Capture's queue.

### 1.2 Why nothing on main is wrong yet

The Capture product (`:3335`) is **prompt 13b**, which has not yet been refactored or executed. The codebase merged through 10c-i (PR #44, 2026-04-29) is event log, projections, pipeline runner, skill runner, identity/noise/commitment/thank-you reactive pipelines, and reward_dispatch. None of that code is wrong under either conception. The drift lives entirely in the constitutional documents and the unrefactored prompt drafts.

This is the cheapest possible moment to correct the architecture: before any Capture-product code, console-views code, or knowledge-source adapter code has been written.

### 1.3 What James clarified that the prior spec missed entirely

The prior spec models family-member machines (`James's Mac`, `Laura's Mac`) as Tailscale endpoints — they sit on the household tailnet and access the central console at `:3330`. They do not run AdministrateMe code.

James clarified the actual physical topology: **each Apple-using family member has their own Mac Mini, physically present on the shelf next to the central CoS Mac Mini, signed into that family member's iCloud account.** These are **bridge devices** that should run AdministrateMe-side adapter code, reading the member's local Apple Notes / Voice Notes / (optional) Obsidian vault and emitting owner-scoped events to the central CoS Mac Mini.

This solves the "Apple Notes has no public API" problem entirely: the bridge is *that family member's Mac, with full local-OS access to that member's iCloud-synced Notes data via the on-disk SQLite store and AppleScript bridge.* The central CoS Mac Mini never signs into a family member's iCloud account — preserving identity-first privacy ([§13.12]) at the physical layer.

---

## 2. The corrected architecture

### 2.1 Member bridges — a new architectural unit

Each Apple-using family member runs a **member bridge**: a dedicated Mac Mini (physically on the shelf next to the central CoS Mac Mini), signed into that member's iCloud account, running an `adminme-bridge` daemon.

**What the bridge runs:**

- `adminme-bridge` daemon (`~/.adminme-bridge/`) — the supervisor process.
- **Apple Notes adapter** — reads the local `NoteStore.sqlite` database and watches for changes; emits `note.added@v1` / `note.updated@v1` / `note.deleted@v1` events.
- **Voice Notes adapter** — watches the Voice Memos recordings folder; emits `voice_note.added@v1` events with the audio artifact reference.
- **Obsidian adapter** — filesystem watcher on a configured vault path; emits `note.added@v1` events with `source_kind=obsidian`. Built-in but only active if the member has configured a vault path.
- **Connector-pack slot** — the extension point for additional knowledge-source adapters (Notion, Logseq, Roam, etc.) shipped as packs.
- **Tailscale client** — the bridge is a tailnet device with its own identity (e.g. `james-bridge@<tailnet>`).

**What the bridge does NOT run:**

- No event log. Events are emitted over the tailnet to the central CoS Mac Mini's bridge-ingest endpoint.
- No projections.
- No console.
- No pipelines, no skill runner, no OpenClaw.
- No iCloud credentials for any other member. James's bridge has James's iCloud, only James's. **Hard.**

### 2.2 The bridge-ingest endpoint

A new Python product on the central CoS Mac Mini at **`:3337`**, named **`bridge`**. Unlike the other four products (core/comms/capture/automation), which are loopback-only, `:3337` is the **only Python product exposed to the household tailnet** (via `tailscale serve` TLS-terminated → loopback `:3337`).

**Endpoints:**

- `POST /api/bridge/ingest` — accepts a single event from a member bridge.
- `POST /api/bridge/ingest/batch` — accepts a batch of events from a member bridge (cold start, catch-up).
- `GET /api/bridge/health` — bridge liveness check.

**Authentication:**

- `Tailscale-User-Login` header is the source of truth for **which bridge** is calling. Same pattern as console authentication (CONSOLE_PATTERNS §1).
- The `party_tailscale_binding` projection resolves the tailnet identity to a `member_id`.
- The ingest endpoint **stamps `owner_scope = private:<member_id>` on every event** based on the resolved member identity. The bridge cannot lie about whose data it is submitting — its tailscale identity is the source of truth for owner_scope.
- A submitted event whose claimed owner_scope disagrees with the resolved-from-tailnet member_id is a **hard refusal**. Logged to the privileged-access log as an attempted scope violation.

**Rationale for a dedicated `:3337 bridge` product (not folded into `:3334 comms`):**

1. It is the only Python product with non-loopback exposure. Different blast-radius posture than the rest.
2. It has different authentication needs (Tailscale-identity-binds-owner-scope is unique to bridges).
3. It is a small, well-scoped surface (3 endpoints) that benefits from being its own deployable unit.
4. Future bridges (e.g., a Plaid-mobile-bridge for some non-Apple integration) would naturally land here.

### 2.3 The central system, after the amendment

The CoS Mac Mini continues to do everything it does today (event log, projections, pipelines, skill runner, OpenClaw integration, central-side L1 adapters like Plaid, Gmail, BlueBubbles plugin), with these additions:

- **Bridge-ingest endpoint at `:3337`** as described in §2.2.
- **New event schemas** for note and voice-note ingestion (§3.2).
- **One projection extension or addition** to receive note events into a queryable store (§3.3).
- **Subscription-list extensions** on existing reactive pipelines so notes feed the same commitment / recurrence / relationship machinery as messages do (§3.4).

The CoS Mac Mini does **not** sign into family-member iCloud accounts, does not run Apple Notes adapters, and does not have local access to any family member's personal knowledge vault. All knowledge ingestion is bridge-side.

### 2.4 The Capture product, after the amendment

The Capture product (`:3335`) is **demoted from input pipeline to read surface**. Its responsibilities narrow to:

- **Knowledge view (NEW)** — semantic + structured search over the member's own knowledge events (notes, voice notes, obsidian); recent captures; cross-source aggregation.
- **Household knowledge view (NEW)** — same, but over the household-shared subset of knowledge.
- **Recipes view (KEPT)** — recipes as a semi-structured artifact kind, derivable from notes tagged or classified as recipes.
- **CRM Parties/Places/Assets/Accounts views (KEPT)** — unchanged. Per [D4], Capture owns the human-facing CRM surfaces, not the data.
- **Search (KEPT)** — `/api/capture/search` semantic + structured search over Interactions/Artifacts/Parties + the new knowledge events.

**Removed:**

- Quick-capture prefix routing (`grocery:`, `call:`, `idea:`, `recipe:`). Captures happen in Apple Notes / Voice Notes / Obsidian on the member's own device.
- Voice-note ingest endpoint. Ingested via L1 bridge adapter.
- Triage queue. Knowledge does not need triaging — it gets indexed and made searchable; tasks/commitments/etc. emerge from the same reactive pipelines that already process messages.
- The slash commands `/capture` and `/triage`. Possibly retained as no-op aliases for one release cycle to avoid breaking docs, then retired.

The router list at `/api/capture/{capture,voice,triage,recipes,parties,parties/:id,places,assets,accounts,search}` becomes `/api/capture/{recipes,parties,parties/:id,places,assets,accounts,search,knowledge,household-knowledge}`.

### 2.5 The kid-bridge variant

Per James's confirmation (2026-04-29 widening), kid bridges are **optional but functional.**

- Charlie's iPad continues to function as today (kid-mode tailnet device, scoreboard view). The iPad is not a bridge.
- A kid bridge Mac Mini is optional infrastructure — added if the family wants the child's knowledge-capture habits (school notes, voice memos) ingested into the household system at all.
- A kid bridge runs **the Apple Notes adapter and the Voice Notes adapter.** Obsidian is excluded from the kid-bridge default adapter set — kids are not the audience for that tool. Connector packs are also excluded from kid bridges by default; if a family installs one, it is an explicit per-bridge configuration, not an inheritance from the parent connector-pack set.
- Events from a kid bridge stamp `owner_scope = private:<child_id>`, just like any other member's events. The kid's events flow into the kid's own `member_knowledge` projection rows (so the kid can search their own notes via the kid-mode console surfaces) and into scoreboard projections (so school-related captures can feed school-related rewards / streaks / etc., if a future scoreboard pipeline adds that).
- The kid's events are **explicitly excluded from cross-member knowledge-graph derivation.** Kid-stamped events do not enter `vector_search`, `graph_miner`, `commitment_extraction`, `recurrence_extraction`, `relationship_summarization`, or `closeness_scoring`. The reasoning is twofold:
  - **Privacy / safety:** a child's school notes should not surface in a parent's CRM tier scoring, household commitment proposals, or graph-mined relationship suggestions. The household's adult knowledge graph stays adult.
  - **Signal quality:** kid notes are unlikely to contain reliable commitment / recurrence / relationship signals at the same quality as adult notes; including them adds noise without value.
- The kid's notes ARE searchable by the kid themselves (via the kid-mode Capture surface), and they ARE indexed into the kid's own `member_knowledge` projection rows. Within-owner-scope, the kid has the same knowledge tools the adults have. Cross-owner-scope, the kid is sandboxed.
- **Enforcement mechanism — not yet decided in this memo.** Two viable shapes:
  - **(a)** Each pipeline / projection that should *not* consume kid events lists `owner_scope_excludes: ["private:<child_id>"]` in its manifest. Pro: explicit at the consumer side. Con: requires updating every excluded pipeline's manifest when a new child member is added.
  - **(b)** The bridge-ingest endpoint flags kid events with a `kid_bridge: true` payload field (or, equivalently, a `derived_from_kid_bridge` envelope flag), and consumers that should *not* process kid events check this flag in their `apply()` / handler. Pro: child-set-membership is a runtime question, not a manifest question; new children inherit the rule automatically. Con: every excluded consumer has to remember to check.
  - The trade-off is "manifest discipline" vs "code discipline." Decision deferred to 11c orientation as **UT-16** (per §5.3 below).

### 2.6 The flow, end to end

```
James writes a note in Apple Notes on his iPhone.

  iCloud sync                                James's Mac Mini bridge
        │                                    (signed into James's iCloud)
        ▼                                            │
  ~/Library/Group Containers/                        │
  group.com.apple.notes/                             │
  NoteStore.sqlite                                   │
        │                                            │
        │  (the bridge's Notes adapter watches)      │
        │                                            │
        └──── change detected ────────────────────►  │
                                                     │
                                                     │  emits note.updated@v1
                                                     │  payload: {note_id, title, body, ...}
                                                     │  claimed owner_scope: private:james
                                                     │
                                                     ▼
                            ┌──────────────────────────────────┐
                            │  POST https://cos.<tailnet>/     │
                            │       api/bridge/ingest          │
                            │  Tailscale-User-Login:           │
                            │       james-bridge@<tailnet>     │
                            └──────────────┬───────────────────┘
                                           │
                          tailscale serve  │  TLS terminated
                                           ▼
                            ┌──────────────────────────────────┐
                            │  Central CoS Mac Mini :3337      │
                            │  bridge product                  │
                            │                                  │
                            │  (a) resolve tailnet identity →  │
                            │       member_id = james          │
                            │  (b) verify claimed owner_scope  │
                            │       matches resolved member    │
                            │  (c) stamp owner_scope =         │
                            │       private:james              │
                            │  (d) sensitivity defaults to     │
                            │       'sensitive' for notes      │
                            │  (e) EventStore.append(envelope) │
                            └──────────────┬───────────────────┘
                                           │
                                           │  (event in log; bus dispatch)
                                           │
                ┌──────────────────────────┼──────────────────────────┐
                │                          │                          │
                ▼                          ▼                          ▼
         vector_search             commitment_extraction      recurrence_extraction
         projection                pipeline                   pipeline
         (subscribes to            (subscribes to             (subscribes to
          note.* events;            note.* events;             note.* events;
          embeds non-               classifies if a             extracts birthdays,
          privileged note           commitment is               anniversaries,
          bodies)                   implied)                    renewals)
                                           │                          │
                                           ▼                          ▼
                                    commitment.proposed       recurrence.proposed
                                    (event)                   (event)
                                           │                          │
                                           ▼                          ▼
                                    commitments               recurrences
                                    projection                projection
                                           │                          │
                                           ▼                          ▼
                                    Capture / Today /         Today / Calendar /
                                    Inbox surfaces            Scoreboard surfaces
                                    show the proposed         show the proposed
                                    commitment for human      recurrence for human
                                    confirmation              confirmation
```

The same flow applies to voice notes (with an additional transcription step on the bridge before emit, OR transcription as a central skill call after the audio artifact is uploaded — depth-read at refactor time).

---

## 3. Concrete additions

### 3.1 Pack-kind extension

Per BUILD.md §PROFILE PACKS / §PERSONA PACKS, the existing six pack kinds are: `adapter`, `pipeline`, `skill`, `projection`, `profile`, `persona`.

This memo introduces a **subkind tag** on `adapter` packs: `subkind: knowledge-source`. A knowledge-source-subkind adapter is one that runs on a member bridge (not on the central CoS Mac Mini) and emits note / voice-note / etc. events.

The pack-loading code on the bridge filters for `kind: adapter, subkind: knowledge-source`; the pack-loading code on the central system filters those out (they are bridge-side only).

Apple Notes, Voice Notes, and Obsidian ship as built-in knowledge-source adapters. Notion / Logseq / Roam are deferred to community packs.

### 3.2 New event schemas

Five new event types, all registered at v1 per [D7]:

**`note.added@v1`** — a note was created in a knowledge source.

```python
class NoteAddedV1(BaseModel):
    note_id: str                          # source-system-stable identifier
    source_kind: Literal["apple_notes", "obsidian", "<connector-defined>"]
    title: str
    body: str
    folder_path: str | None               # for Apple Notes folders / Obsidian directory
    tags: list[str]                       # source-defined tags if any
    created_at: datetime                  # source's reported creation time
    body_word_count: int                  # cheap to compute; helps downstream sizing
    has_attachments: bool                 # if true, follow-up event(s) reference artifacts
```

**`note.updated@v1`** — a note's body or metadata changed. Same shape plus:

```python
    previous_event_id: str | None         # last note.* event for this note_id, if known
```

**`note.deleted@v1`** — a note was removed from the source. Sparse:

```python
    note_id: str
    source_kind: Literal[...]
    deleted_at: datetime
```

**`voice_note.added@v1`** — a voice memo was recorded.

```python
    voice_note_id: str
    source_kind: Literal["apple_voice_memos", "<connector-defined>"]
    audio_artifact_ref: str               # artifact:// pointer to uploaded audio
    duration_seconds: float
    created_at: datetime
    title: str | None                     # source's auto-title if any
```

**`voice_note.transcribed@v1`** — emitted by a central pipeline that ran a transcription skill against an `voice_note.added` event.

```python
    voice_note_id: str
    transcript: str
    transcript_word_count: int
    skill_id: str                         # which transcription skill was used
    skill_version: str
    confidence: float | None
```

The first four are emitted by bridges. The fifth is emitted by a central pipeline. Owner scope flows through naturally.

### 3.3 Projection — `member_knowledge` (new) OR extend `artifacts`

**Decision deferred to the architecture-amendment doc-update PR.** Two viable shapes:

**(a) New projection `member_knowledge`** — a dedicated 12th projection. Tables: `notes`, `note_versions`, `voice_notes`. Subscribes to `note.*` and `voice_note.*` events. Owner-scope-partitioned per [§1.6]. Per-member SQLite database files? Or one database with `owner_scope` column? Per the rest of the system, the latter (one DB, owner_scope column).

**(b) Extend the existing `artifacts` projection** — `artifacts` already handles documents, OCR text, and structured extraction per kind. Add `note` and `voice_note` as artifact kinds. Pro: fewer projections, reuses existing queryability. Con: notes are not really "artifacts" in the user-facing sense — they are first-class knowledge units, not derived attachments.

Recommendation: **(a) new projection `member_knowledge`**. Notes deserve their own first-class projection. Their query patterns (search, recent, by-folder, by-tag) are different enough from artifact queries (by-kind, by-classification, by-source-document) to warrant separation.

### 3.4 Subscription-list extensions on existing reactive pipelines

These pipelines, already on main as of 10b-ii-β:

- **`commitment_extraction`** — currently subscribes to `messaging.received`, `messaging.sent`, `telephony.voicemail_transcribed`. Add `note.added`, `note.updated`, `voice_note.transcribed`.
- **`recurrence_extraction`** — currently scheduled, scans contacts/notes/manuals. Future reactive variant: subscribe to `note.added`, `note.updated`, `voice_note.transcribed` for incremental extraction.
- **`relationship_summarization`** — proactive nightly. Future enhancement: include note events from the rolling 90-day window in the summary corpus.
- **`graph_miner`** — currently nightly on `adminme-vault if present`. After the amendment: `graph_miner` runs over the union of all members' note events (respecting owner_scope at projection-query time, so the household-shared subset is what surfaces; private notes are not graph-mined cross-member).

The first one (`commitment_extraction`) is a small, mechanical change that lands in the same prompt as the new event schemas. The other three are deferred enhancements that land in their respective pipelines' next iteration prompts (or later).

### 3.5 `vector_search` projection — subscription extension

Currently subscribes to `interactions.*`, `artifacts.*`, `parties.*` (non-privileged only) per [§3.10] / [§13.9].

**Adds:** `note.added`, `note.updated`, `voice_note.transcribed` (non-privileged only).

Privileged-content filter at `apply()` time continues to work as today — sensitivity-flagged events refuse embedding. The bridge defaults `sensitivity = sensitive` on note events (not `privileged`), so note bodies enter `vector_search` and are searchable cross-member-with-owner-scope-respect. Members can elevate per-source to `privileged` during bridge configuration if they have, say, a privileged-medical Obsidian vault.

### 3.6 Bridge daemon — new project under `bridge/`

Lives at `bridge/` in the repo. Separate from `adminme/` (which is central-system code). Shares the event-schema models from `adminme/lib/event_types/` via an editable install (so the bridge knows the exact wire format).

```
bridge/
├── pyproject.toml             # separate Poetry project; depends on adminme-events package
├── adminme_bridge/
│   ├── __init__.py
│   ├── main.py                # bridge daemon entrypoint
│   ├── config.py              # ~/.adminme-bridge/config.yaml schema
│   ├── ingest_client.py       # HTTP client to central :3337 endpoint
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py            # KnowledgeAdapter Protocol + BridgeContext
│   │   ├── apple_notes.py     # Apple Notes SQLite + AppleScript hybrid
│   │   ├── voice_notes.py     # Voice Memos folder watcher
│   │   └── obsidian.py        # Filesystem watcher for an Obsidian vault
│   └── packs/                 # connector-pack loader (mirrors adminme/pipelines pack pattern)
└── tests/
    ├── unit/
    └── integration/
```

The bridge daemon is supervised via `launchd` on macOS. Bootstrap on a bridge Mac Mini installs the daemon, configures it (which adapters are enabled, which iCloud account, which Obsidian path), pairs with the central system (exchanges Tailscale identity), and starts the daemon.

### 3.7 Bridge bootstrap — new section in prompt 16 OR new section §10 of the bootstrap wizard

The central bootstrap wizard adds a section (call it **§10 Bridge enrollment**, between §9 Observation briefing and the bootstrap.completed event):

- Prompts the operator for bridge Mac Mini count (one per Apple-using member).
- For each declared bridge: emits `bridge.enrollment_pending` events.
- Output: an enrollment-package URL or rsync target the operator copies to each bridge.

A separate **bridge bootstrap mini-wizard** runs on each bridge Mac Mini:

- Verifies macOS version, iCloud signin status (must be the right member's account), Tailscale auth, Apple Notes accessibility (Full Disk Access permission).
- Installs the bridge daemon under `launchd`.
- Configures which adapters are active (Apple Notes always; Voice Notes by default; Obsidian if the member configures a vault path).
- Tests the bridge-ingest endpoint roundtrip (submits a `bridge.enrollment_completed` event).
- Hands control back to the central wizard to continue.

This is a meaningful addition to prompt 16's scope. Prompt 16 is already a pre-split candidate (highest-priority split per `D-prompt-tier-and-pattern-index.md`); the bridge-bootstrap addition reinforces the split — it likely becomes its own sub-prompt.

---

## 4. Constitutional document changes

These are the precise locations the amendment-PR (§5 PR-α) modifies. Line-level replacement text is produced during PR-α drafting, which is the **next** Type 0 session after this memo lands.

### 4.1 `ADMINISTRATEME_BUILD.md`

- **§MACHINE TOPOLOGY & NETWORK section** — extend to acknowledge bridge Mac Minis. Document the on-the-shelf physical arrangement, per-member iCloud isolation, Tailscale identities for bridges.
- **§THE ARCHITECTURE / L1 Adapters section** — extend to describe the bridge-side adapter family. Make the central/bridge split explicit. Apple Notes / Voice Notes / Obsidian listed as built-in bridge adapters.
- **§L5-continued / Capture (`:3335`) section** — the paragraph beginning *"Capture — :3335 — Working Memory + CRM Surfaces"* gets replaced. New name: **"Capture — :3335 — Knowledge Surfaces + CRM Surfaces."** Quick-capture prefix routing removed; voice-note ingest removed; triage queue removed. Knowledge view + household knowledge view added. Recipe view, CRM views, search retained.
- **NEW SECTION: §MEMBER BRIDGES** (top-level, between §MACHINE TOPOLOGY and §THE ARCHITECTURE, OR as a §L1-bridge subsection — depth-read decides). Specifies the bridge model: physical arrangement, per-member iCloud, daemon shape, default adapters, connector-pack model, bridge-ingest endpoint, kid-bridge variant.
- **§L5-continued — `:3337 bridge` product** — new fifth Python product. Routers: `/api/bridge/{ingest,ingest/batch,health}`. Slash commands: none (bridge is non-interactive). No proactive jobs.
- **§BOOTSTRAP WIZARD** — extend to include §10 Bridge enrollment OR document the per-bridge bootstrap mini-wizard.

### 4.2 `docs/architecture-summary.md`

- **§1 The five-layer model / L1** — extend to describe the two-place adapter family (central adapters at `:333x`-process scope; bridge adapters at member-bridge scope). This is the sentence-level addition that makes the architecture two-physical-host instead of one-physical-host.
- **§4 The 11 projections** → **The 12 projections** — add `member_knowledge` row. Subscribes to `note.*`, `voice_note.*`. Tables: `notes`, `note_versions`, `voice_notes`. Owner-scope partitioned. Privileged content excluded per [§13.9].
- **§9 Python product APIs** — add `:3337 bridge` as a fifth product. Modify the Capture (`:3335`) paragraph to reflect demotion from input pipeline to read surface (per §2.4 above).
- **§10 Bootstrap wizard** — extend §10 of the wizard to include bridge enrollment.
- **§11 Open questions** — open question #4 (cross-product event-flow ownership vs CRM spine assertion) reframes around the new D17 (§4.4 below). The question was about "where does a CRM-tier-change endpoint belong" — D17 doesn't directly answer it but it does close the broader question of "what is Capture for."

### 4.3 `docs/SYSTEM_INVARIANTS.md`

- **§13 Privacy + privileged + observation** — add a new sub-section or invariant:

  > **§13.18 (proposed):** Bridge sovereignty — a member bridge runs only its assigned member's adapters; a bridge Mac Mini that runs more than one member's adapter set is a misconfiguration. Cross-member knowledge access happens only through projection queries on the central CoS Mac Mini, never through bridge-to-bridge access. [BUILD.md §MEMBER BRIDGES]

- **§3 Adapters and the boundary** — modest extension to acknowledge the bridge-side adapter family and the bridge-ingest endpoint as L1's two-place shape.

### 4.4 `docs/DECISIONS.md`

**New: D17 — Personal knowledge ingestion is L1-bridge-shaped, not L5-product-shaped.**

> **Decided:** 2026-04-29. **Status:** CONFIRMED. **Resolves:** drift between BUILD.md §L5-continued / architecture-summary.md §9 framing of Capture as a quick-capture / triage / voice-ingest input pipeline and the binding intent that personal knowledge is ingested per-member from each member's own knowledge tools (Apple Notes by default; Voice Notes; Obsidian opt-in; connector packs for other systems).
>
> Personal knowledge captures live in each family member's own tooling on their own device. AdministrateMe ingests this knowledge via **member bridges**: a Mac Mini per Apple-using family member, on the household tailnet, signed into that member's iCloud account, running an `adminme-bridge` daemon with knowledge-source adapters (Apple Notes, Voice Notes, optionally Obsidian). Bridges emit owner-scoped `note.*` and `voice_note.*` events to the central CoS Mac Mini's `:3337 bridge` ingest endpoint, where Tailscale identity binds the owner_scope.
>
> The Capture product (`:3335`) is a **read surface** over the resulting knowledge layer + the CRM projections — not an input pipeline. Quick-capture prefix routing, triage queues, and central voice-note ingest are explicitly retired.
>
> Tasks, commitments, recurrences, and relationships flow through the existing reactive pipelines (`commitment_extraction`, `recurrence_extraction`, `relationship_summarization`), with `note.*` and `voice_note.*` added to their subscription lists. There is no "polling" of Capture; pipelines react to events as they always have.
>
> Connector packs for non-Apple knowledge sources (Notion, Logseq, Roam, etc.) install on bridges as `kind: adapter, subkind: knowledge-source` packs.
>
> **Corollary 1:** D4 ("products own surfaces, projections own data, events move state") still applies. Knowledge events are just one more event family flowing into the projection layer.
>
> **Corollary 2:** [§13.12] (identity-first privacy) is **strengthened** by this decision. Each member's private knowledge is physically segregated on their own bridge's iCloud account; the central system never holds member iCloud key material.

### 4.5 `ADMINISTRATEME_DIAGRAMS.md`

- **§7 Machine topology and the tailnet** — diagram updates to show the row of bridge Mac Minis on the shelf next to the CoS Mac Mini. Family-member personal Macs (separate from bridges) remain as ordinary tailnet endpoints.
- **§1 The five-layer model** — L1 ASCII art extends to show bridge-side adapters as a distinct family.
- **§2 Event flow** — a second canonical example added: "one new note in James's Apple Notes → bridge → ingest → vector_search + commitment_extraction" — mirroring the existing iMessage-to-confirmed-commitment example.

### 4.6 `prompts/PROMPT_SEQUENCE.md` and `D-prompt-tier-and-pattern-index.md`

See §5.2 below — prompt-sequence changes are landed in PR-β.

---

## 5. The PR plan

Three single-purpose PRs, in order. All three are infrastructure / planning-artifact PRs per PM-22 — no four-commit discipline, no BUILD_LOG entries, no tests.

### 5.1 PR-α: constitutional doc amendments

**Branch:** `arch-amendment-knowledge-vaults-and-member-bridges`

**Files modified:**

- `ADMINISTRATEME_BUILD.md` — sections per §4.1 above.
- `docs/architecture-summary.md` — sections per §4.2 above.
- `docs/SYSTEM_INVARIANTS.md` — new §13.18 (or sub-clause) per §4.3.
- `docs/DECISIONS.md` — new D17 entry per §4.4.
- `ADMINISTRATEME_DIAGRAMS.md` — §1, §2, §7 updates per §4.5.

**Files NOT modified in this PR:**

- Any code. This is pure documentation.
- Any prompt files. Those land in PR-β.
- `docs/partner_handoff.md`. That lands in PR-γ.

**Drafting next session:** the line-by-line replacement text for each modified document. The drafting session is a Type 0 session; output is the PR-α body + the per-file replacement blocks James pastes into Claude Code.

### 5.2 PR-β: prompt-sequence update

**Branch:** `sequence-update-knowledge-amendment`

**Files modified:**

- `prompts/PROMPT_SEQUENCE.md` — sequence table updates:
  - **NEW: prompt 11c** (`11c-bridge-and-knowledge-adapters.md`) — bridge daemon + ingest endpoint + 3 default adapters + 5 new event schemas + connector-pack interface. Inserted after 11b (or after the 11 split if 11 is split first). Pre-split candidate: split into 11c-i (bridge daemon + ingest endpoint + connector-pack interface) and 11c-ii (3 default adapters + event schemas + tests).
  - **NEW: prompt 07d** (`07d-member-knowledge-projection.md`) — the `member_knowledge` projection. Could land between 07c-β and 07.5 (extending the projection cohort) OR between 11c and 12 (after the bridge ships). Recommendation: **between 11c and 12**, because the projection has nothing to consume until note events exist. So: 11c → 07d (renumbered as 11d?) → 12.
  - **MODIFIED: prompt 13b** (`13b-product-apis-capture-auto.md`) — Capture product description narrows per §2.4. Routers list updates. Voice-ingest and triage endpoints removed; knowledge / household-knowledge endpoints added.
  - **MODIFIED: prompt 14b** (`14b-console-views-primary.md`) — Capture view description narrows per §2.4.
  - **MODIFIED: prompt 16** (`16-bootstrap-wizard.md`) — adds §10 Bridge enrollment to the wizard flow + per-bridge bootstrap mini-wizard. Likely makes prompt 16 even more split-bound; the existing 16a/16b/16c forecast extends to 16d (bridge enrollment) or folds into 16c.
- `D-prompt-tier-and-pattern-index.md` — corresponding table updates: new rows for 11c, 07d (or 11d); modified rows for 13b, 14b, 16; new pre-split disposition entries.

**Files NOT modified:**

- Any code.
- The constitutional docs (those landed in PR-α).
- `docs/partner_handoff.md` (lands in PR-γ).

**Drafting next session:** the line-by-line PROMPT_SEQUENCE.md replacement blocks.

### 5.3 PR-γ: partner-state snapshot

**Branch:** `update-partner-handoff-knowledge-amendment`

**Files modified:**

- `docs/partner_handoff.md` — updates:
  - **Current build state** updated to reflect that the Conception-C amendment cycle has landed.
  - **Next task queue** updated: **10c-ii orientation + refactor** stays as the next build-prompt session (the amendment does not affect 10c-ii).
  - **New PMs introduced**:
    - **PM-28 (HARD):** When constitutional documents drift from binding architectural intent, Partner pauses the build, flags the drift, and runs an architecture-amendment cycle (Tier C memo + 3 single-purpose PRs) before resuming. The Conception-C amendment of 2026-04-29 is the canonical example.
    - **PM-29 (SOFT):** Knowledge-source adapters live on member bridges, not on the central CoS Mac Mini. Future prompts that add knowledge-source adapters land in `bridge/` not `adminme/`.
  - **New UTs introduced** (open):
    - **UT-15 (OPEN):** Bridge daemon and central system share event-schema models via editable install or vendored copy. Decide at 11c orientation.
    - **UT-16 (OPEN):** Kid-event routing-restriction enforcement mechanism — `owner_scope_excludes` in pipeline manifests vs `kid_bridge: true` payload flag honored by consumers. Decide at 11c orientation OR earlier if a downstream prompt needs to know.
    - **UT-17 (OPEN):** `member_knowledge` as a new (12th) projection vs extending `artifacts`. Recommendation in §3.3 is the new projection; final decision deferred to the projection-prompt orientation.
    - **UT-18 (OPEN):** Apple Notes read mechanism — SQLite direct vs AppleScript vs hybrid. Recommendation in §1.3 is hybrid (SQLite for bulk, AppleScript fallback). Final decision at 11c orientation.

**Drafting next session:** the partner_handoff.md update block.

---

## 6. What the build sequence looks like after the amendment

Pre-amendment next-task queue:

1. James: drive partner-state snapshot prep PR for 10c-i QC results.
2. Partner session: 10c-ii orientation + refactor.
3. … (10c-ii through 19)

Post-amendment next-task queue:

1. **James reviews this memo.** Approves or revises.
2. **James: prep PR-α** (constitutional doc amendments). Single-purpose PR per PM-22.
3. **Partner session: PR-α drafting.** Type 0 session. Output: line-by-line replacement blocks for the five modified constitutional docs, packaged as a Claude Code micro-prompt.
4. **Claude Code session: execute PR-α.** Lands the doc amendments.
5. **Partner session: PR-β drafting.** Type 0 session. Output: PROMPT_SEQUENCE.md and D-index replacement blocks.
6. **Claude Code session: execute PR-β.** Lands the sequence updates.
7. **Partner session: PR-γ drafting.** Type 0 session, brief. Output: partner_handoff.md update block.
8. **Claude Code session: execute PR-γ.** Lands the partner-state snapshot.
9. **James: prep PR-* for 10c-i QC results.** (This was item 1 in the pre-amendment queue. It still needs to happen, but it can land alongside or after the amendment cycle — the QC PR doesn't conflict with the amendment PRs.)
10. **Partner session: 10c-ii orientation + refactor.** The first build-prompt session post-amendment.
11. **(unchanged from pre-amendment queue)** 10c-iii, 10d, 11, 11c, 11d, 12, 13a, 13b, 14a, 14b, 14c, 14d, 14e, 15, 15.5, 16, 17, 18, 19.

The amendment adds **three Partner sessions and three Claude Code sessions** — call it ~6-8 hours of build time across one or two Saturday slots. Compared to building Capture-as-input-pipeline in 13b and then ripping it out in a sidecar after Phase B reveals it doesn't match how the family actually captures knowledge, this is a steep discount.

---

## 7. Risk register

Things that could go wrong with the amendment, ranked.

### High

**(a) Bridge bootstrap is harder than it looks.** macOS Full Disk Access for the Notes SQLite read. iCloud signin gymnastics. Tailscale identity per device. Each bridge needs the central system to know about it (party_tailscale_binding registration). The bridge bootstrap mini-wizard absorbs this complexity, but writing it well is not trivial. **Mitigation:** prompt 16 (already a pre-split candidate) absorbs bridge enrollment as its own sub-prompt. Plenty of Saturday slots remain.

**(b) NoteStore.sqlite schema breaks on macOS update.** Apple has changed the Notes database schema in past major-version updates. **Mitigation:** the hybrid read mechanism (per §1.3 recommendation) falls back to AppleScript when the SQLite schema fingerprint doesn't match a known version. The bridge logs the unknown-schema-version event and continues operating in degraded mode (AppleScript-only is slower but functional). A diagnostic prompt (d09 or similar) handles "Apple updated macOS and the Notes adapter is degraded."

### Medium

**(c) Bridge-ingest endpoint becomes a chokepoint.** If a bridge has 10,000 historical notes to ingest on first run, the cold-start surge could overwhelm `:3337`. **Mitigation:** the `/api/bridge/ingest/batch` endpoint and a bridge-side rate limiter on cold-start. The first-run import is a planned event, not a steady-state load; documented in the bridge bootstrap.

**(d) Owner_scope binding via Tailscale identity fails open if Tailscale is misconfigured.** The CONSOLE_PATTERNS §1 console pattern handles this for tenant requests; the bridge pattern reuses it. But a bridge-ingest request without `Tailscale-User-Login` must be a hard refusal, not a fallback. **Mitigation:** invariant test in the test suite for prompt 11c verifies that an unauthenticated bridge-ingest request returns 403. The invariant gets added to `scripts/verify_invariants.sh` as a grep canary.

### Low

**(e) Connector packs become a sprawl point.** If every family wants their own knowledge system supported, the connector-pack ecosystem could grow unbounded. **Mitigation:** ship Apple Notes, Voice Notes, Obsidian. That's enough for the foreseeable family-of-four scope. Community packs are community problems.

**(f) The Capture product's demotion confuses someone reading the architecture summary mid-build.** **Mitigation:** D17 is explicit about the demotion. PR-α's diff to architecture-summary.md §9 makes the change visible.

---

## 8. What this memo does NOT decide

Carried forward to subsequent sessions:

- **UT-15 through UT-18** (per §5.3 above) — bridge-event-schema sharing, kid-event routing, projection vs artifacts-extension, Apple Notes read mechanism. All deferred to 11c orientation, with recommendations on file.
- **The exact §10 Bridge enrollment flow in the bootstrap wizard.** Sketched in §3.7; precise wizard sections drafted at prompt 16 refactor time.
- **Whether the bridge daemon's transcription of voice notes happens bridge-side or central-side.** Bridge-side keeps audio files local (privacy); central-side reuses the existing skill runner (consistency). Recommendation: **central-side**, because (a) it reuses skill-runner provenance, (b) bridge Mac Minis may not have Whisper or equivalent installed, (c) audio-as-artifact is the existing pattern and bridges can upload to the central artifact store. Confirmed at 11c orientation.
- **The exact connector-pack interface.** Mirrors the existing pack pattern (`pack.yaml`, `handler.py`, optional `tests/`); precise schema specified at 11c refactor time.

---

## 9. Approval

This memo is the architectural decision. The three PRs (α, β, γ) execute it.

James reviews. If approved, the next Partner session is PR-α drafting (Type 0). If revised, this memo is updated and re-reviewed.

**Approval status:** APPROVED 2026-04-29 with one widening — kid bridges run Apple Notes + Voice Notes adapters (Obsidian excluded), cross-member graph derivation still sandboxed (§2.5).

---

## End of memo
