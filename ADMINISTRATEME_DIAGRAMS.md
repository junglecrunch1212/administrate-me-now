# DIAGRAMS.md

**Ten ASCII diagrams for the AdministrateMe build.**

Companion to BUILD.md. Each diagram answers a question the prose alone doesn't answer well. Read in conjunction with the referenced BUILD.md section.

Not exhaustive by design — these are the ten diagrams that earn their place because a diagram conveys the relationship structure better than prose or code. Things that are fine as prose (event schemas, YAML examples, handler code, step lists) stay in BUILD.md; they don't get diagrammed here.

**Note on names used in examples.** Diagrams 2 and 4 use Stice-family names (James, Laura, Kate) as concrete examples to make the flows readable — parallel to how BUILD.md's CRM exposition and REFERENCE_EXAMPLES.md's worked packs use them. These names **must not appear in platform code** (the identity scan enforces this). The diagrams illustrate what the platform does for a populated instance; the platform itself stays tenant-agnostic.

---

## Index

1. [Five-layer architecture](#1-five-layer-architecture)
2. [Event flow: incoming iMessage through to confirmed commitment](#2-event-flow-incoming-imessage-through-to-confirmed-commitment)
3. [guardedWrite: the three-layer check](#3-guardedwrite-the-three-layer-check)
4. [authMember / viewMember split](#4-authmember--viewmember-split)
5. [Session and scope enforcement](#5-session-and-scope-enforcement)
6. [xlsx round-trip: bidirectional human-editable projection](#6-xlsx-round-trip-bidirectional-human-editable-projection)
7. [Machine topology and the tailnet](#7-machine-topology-and-the-tailnet)
8. [Pack installation flow](#8-pack-installation-flow)
9. [Observation mode: what fires, what gets suppressed](#9-observation-mode-what-fires-what-gets-suppressed)
10. [Bootstrap wizard state machine](#10-bootstrap-wizard-state-machine)

---

## 1. Five-layer architecture

**Canonical in BUILD.md:** "OPENCLAW IS THE ASSISTANT SUBSTRATE" + "THE ARCHITECTURE — FIVE LAYERS" sections.

**What to notice.** OpenClaw is the **assistant substrate** — the gateway the household's people actually talk to via iMessage/Telegram/Discord/chat, and the runtime that executes skills and dispatches slash commands. The five layers of AdministrateMe compose on top of it. The event log at L2 is AdministrateMe's source of truth; OpenClaw has its own memory separately, bridged into L2 via the `openclaw-memory-bridge` plugin. Adapters produce events; projections consume them; pipelines do both; surfaces read projections and write via the HTTP bridge, never directly to the event log.

```
   ┌────────────────────────────────────────────────────────────────────┐
   │              OPENCLAW GATEWAY   (loopback :18789)                  │
   │                                                                    │
   │   Agent loop · Channels (iMessage/Telegram/Discord/web) · SOUL.md  │
   │   Skill runner · Slash-command dispatcher · Session (dmScope)      │
   │   Plugins · Standing orders · Cron · Hooks · Approvals · Nodes     │
   │                                                                    │
   │   AdministrateMe installs skills, plugins, slash commands,         │
   │   standing orders INTO OpenClaw. OpenClaw is where the assistant   │
   │   actually "lives" from a principal's experience standpoint.       │
   └────────────────────────────────────────────────────────────────────┘
            │                      │                       ▲
            │ invokes skills,      │ dispatches slash      │ receives
            │ registers standing   │ commands to Admin.Me  │ outbound
            │ orders, fans out     │ handlers              │ drafts
            │ inbound channel      │                       │ for chan.
            │ messages via plugin  ▼                       │ delivery
            ▼
                ┌──────────────────────────────────────────────────────────┐
                │                     L5 : SURFACES                        │
                │  Node console @ :3330  ·  Python product APIs            │
                │  :3333 core   :3334 comms   :3335 capture   :3336 autom. │
                │  Chat (proxies to OpenClaw :18789) · Inbox · CRM ·       │
                │  Capture · Finance · Calendar · Scoreboard · Settings ·  │
                │  xlsx workbooks · morning digest · scoreboard TV         │
                │  (iMessage/SMS/Telegram/Discord surfaces = OpenClaw)     │
                └──────────────────────────────────────────────────────────┘
                                 ▲            │
                    read         │            │   write (via HTTP bridge)
                                 │            ▼
                ┌──────────────────────────────────────────────────────────┐
                │                     L4 : PIPELINES                       │
                │  identity_resolution · noise_filtering ·                 │
                │  commitment_extraction · thank_you ·                     │
                │  recurrence_extraction · artifact_classification ·       │
                │  relationship_summarization · closeness_scoring ·        │
                │  reminder_dispatch · morning_digest · reward_dispatch ·  │
                │  paralysis_detection · whatnow_ranking ·                 │
                │  scoreboard_projection · custody_brief · crm_surface ·   │
                │  graph_miner                                             │
                │                                                          │
                │  Event-subscription pipelines run inside AdministrateMe  │
                │  runner. Scheduled/proactive pipelines register as       │
                │  OpenClaw standing orders. Skills invoked via wrapper    │
                │  around OpenClaw's skill runner.                         │
                └──────────────────────────────────────────────────────────┘
                                 ▲            │
                    subscribe    │            │   emit
                                 │            ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │                    L2 : EVENT LOG    (source of truth)             │
   │    SQLCipher · append-only · partitioned by owner_scope            │
   │    parties · interactions · commitments · tasks · recurrences ·    │
   │    artifacts · money_flows · calendar · skill_calls · observation  │
   │    ════════════════════════════════════════════════════════════    │
   │                    L2 : EVENT BUS                                  │
   │    In-process pub/sub · checkpoint per subscriber · replayable     │
   │    (Separate from OpenClaw's memory; bridged via plugin)           │
   └────────────────────────────────────────────────────────────────────┘
                  ▲            │                 ▲
      emit        │            │  derive         │  read
                  │            ▼                 │
   ┌─────────────────────┐  ┌────────────────────┴──────────────────┐
   │   L1 : ADAPTERS     │  │            L3 : PROJECTIONS           │
   │   (5 categories ×   │  │                                       │
   │   3 runtimes)       │  │  parties     interactions   artifacts │
   │                     │  │  commitments tasks          recurrences│
   │  Cat-A COMMUNICATION│  │  calendars   places/assets/accounts   │
   │   Gmail · BlueBubbl │  │  money       vector_search            │
   │   · Telegram · Disc │  │  xlsx_workbooks  (bidirectional)      │
   │   [central]         │  │  member_knowledge  (per-member)       │
   │                     │  │  lists  (NEW per D18 — household-     │
   │  Cat-B EXT-MIRROR   │  │     mirrored lists, 13th projection)  │
   │   AppleReminders·   │  │                                       │
   │   GoogleTasks·      │  │  Each projection: handlers.py +       │
   │   AppleCalendar·    │  │  queries.py + schema.sql. Rebuild     │
   │   GoogleCalendar·   │  │  from event log at any time.          │
   │   AppleContacts·    │  └───────────────────────────────────────┘
   │   GoogleContacts·   │
   │   AppleNotes-cklist │   runtime axis is orthogonal:
   │   [dual / central /  │   ┌─────────────────────────────┐
   │    bridge per cap]  │   │  CENTRAL : on CoS Mac Mini  │
   │                     │   │  · Cat-A all                │
   │  Cat-C INBOUND-DATA │   │  · Cat-B Google* + central  │
   │   Plaid             │   │    half of dual-deployment  │
   │   [central]         │   │  · Cat-C all                │
   │                     │   │  · Cat-E HA + Twilio        │
   │  Cat-D PERS-KNOWL'GE│   │                             │
   │   AppleNotes(prose)·│   │  BRIDGE : per-member Mac    │
   │   VoiceMemos·       │   │  · Cat-D all                │
   │   Obsidian          │   │  · Cat-B bridge half of     │
   │   [bridge]          │   │    dual-deployment (Apple   │
   │                     │   │    Reminders, Apple Calend, │
   │  Cat-E OUTBOUND-ACT │   │    Apple Contacts, Notes-   │
   │   Twilio (out only)·│   │    checklists)              │
   │   HomeAssistant     │   │                             │
   │   [central]         │   │  emits to :3337 bridge      │
   │                     │   │   ingest endpoint over the  │
   │  Multi-capability   │   │   tailnet                   │
   │   AppleNotes = D+B  │   └─────────────────────────────┘
   │   (prose + cklist)  │
   │   HA = C+E          │   Adapter base classes: each
   │   (state + service) │   capability inherits from one
   │                     │   of the five ABCs per
   │                     │   BUILD.md §ADAPTER FRAMEWORK
   └─────────────────────┘
      external world
      (read only into)
```

The arrows to internalize: **OpenClaw runs the conversation. AdministrateMe runs the data + CoS behaviors. They meet at four seams: skills (OpenClaw invokes AdministrateMe skill packs), slash commands (AdministrateMe handlers, OpenClaw dispatcher), standing orders (AdministrateMe registers, OpenClaw schedules and fires), and channels (AdministrateMe's `openclaw-memory-bridge` plugin ingests conversation into the event log).**

---

## 2. Event flow: incoming iMessage through to confirmed commitment

**Canonical in BUILD.md:** "Cross-product event flow (illustration)" section. Also connects to REFERENCE_EXAMPLES.md §2 (pipeline) and §3 (skill).

**What to notice.** One inbound message touches seven distinct components. Each step emits an event; each event is replayable; each has a correlation ID. The principal's click at the very end emits exactly one event (`commitment.confirmed`) referencing everything upstream.

```
  Kate's iPhone                Mac Mini (BlueBubbles server)
       │                              │
       │  blue bubble                 │  WebSocket
       ▼                              ▼
  ┌────────────────────────────────────────────┐
  │  [1]  ADAPTER                              │
  │  messaging:bluebubbles_adminme             │
  │  WebSocket frame → _normalize()            │
  │  emit: messaging.received                  │      correlation_id
  │  ev_recv_001                               │      assigned here:
  └────────────────────────────────────────────┘      c_m1_abc123
                  │
                  │ event bus dispatch (in-process pub/sub)
                  ▼
  ┌────────────────────────────────────────────┐
  │  [2]  PIPELINE  commitment_extraction      │
  │  subscribed to: messaging.received         │
  │                                            │
  │   (a) resolve sender party                 │─── reads parties projection
  │   (b) skip rules (privileged? child?)      │     ↓
  │   (c) call skill: classify_candidate       │───► [3a] SKILL
  │        returns {is_candidate, confidence}  │     classify_commitment_candidate@3.2.1
  │   (d) if confidence ≥ 0.55 →               │     model call · LLM · recorded
  │       call skill: extract_fields           │     emit: skill.call.recorded
  │        returns {kind, summary, due, ...}   │───► [3b] SKILL
  │   (e) dedupe vs open commitments           │     extract_commitment_fields@2.1.0
  │   (f) emit: commitment.proposed            │     model call · LLM · recorded
  │  ev_prop_002                               │     emit: skill.call.recorded
  └────────────────────────────────────────────┘
                  │
                  │ event bus dispatch
                  ▼
  ┌────────────────────────────────────────────┐
  │  [4]  PROJECTION  commitments              │
  │  handler on_commitment_proposed:           │
  │    INSERT row with status='pending'        │
  └────────────────────────────────────────────┘
                  │
                  │ event bus dispatch (same event, different subscriber)
                  ▼
  ┌────────────────────────────────────────────┐
  │  [5]  PROJECTION  inbox_surface            │
  │  handler: append proposal card to member's │
  │  inbox list with UI hints + actions        │
  └────────────────────────────────────────────┘
                  │
       ┌──────────┴──────────────────────────────┐
       │   (meanwhile: xlsx_forward daemon       │
       │    debounces 5s then regenerates        │
       │    adminme-ops.xlsx Commitments sheet)  │
       └─────────────────────────────────────────┘
                  │
                  │ principal opens console, sees the proposal card
                  ▼
  ┌────────────────────────────────────────────┐
  │  [6]  CONSOLE  POST /api/commitments/      │     ← authMember = stice-james
  │        <id>/confirm                        │        (from Tailscale header)
  │   passes guardedWrite:                     │
  │     layer 1: allowlist — user allowed?     │
  │     layer 2: gate — 'allow'                │
  │     layer 3: rate limit — OK               │
  │   forwards to Python core API              │
  └────────────────────────────────────────────┘
                  │
                  ▼
  ┌────────────────────────────────────────────┐
  │  [7]  EMIT  commitment.confirmed           │
  │  ev_conf_003                               │     same correlation_id
  │    payload.proposal_event_id = ev_prop_002 │     carries through
  │    payload.approved_by = stice-james       │
  └────────────────────────────────────────────┘
                  │
          ┌───────┴─────────────────────────────┐
          ▼                                     ▼
   [4'] projection                      [5'] inbox_surface
   status: pending → confirmed          proposal card → commitment row
          │                                     │
          ▼                                     ▼
   reward_dispatch pipeline              morning_digest picks it up tomorrow
   rolls tier, emits reward.ready
   SSE fans out to James's open tabs

  ───────────────────────────────────────────────────────────────

  Total events emitted from this one iMessage:
    1  messaging.received
    2  skill.call.recorded  (one per skill)
    1  commitment.proposed
    1  commitment.confirmed
    1  reward.ready
   ─── 
    6 events · 1 correlation_id · traceable end to end
```

Every row in the log carries `correlation_id = c_m1_abc123`. One grep, full audit trail.

---

### Second canonical example: a new note in James's Apple Notes through to a confirmed commitment

**Why this is parallel.** Same shape as the iMessage example: an external source emits, an adapter normalizes, the event lands in the log, pipelines and projections derive. The only structural difference is L1's two-place shape — the adapter runs on James's bridge Mac Mini, not on the central CoS Mac Mini, and emits via the bridge ingest endpoint over the tailnet.

```text
James writes a note in Apple Notes on his iPhone
│
│  iCloud sync to James's Mac (the bridge)
▼
┌────────────────────────────────────────────┐
│  [1]  ADAPTER  (runs on james-bridge)      │
│  knowledge-source:apple_notes              │
│  watches NoteStore.sqlite changes          │
│  emit: note.added@v1                       │     correlation_id
│  ev_note_001                               │     assigned here:
│  payload.owner_scope = private:james       │     c_n1_def456
└────────────────────────────────────────────┘
│
│  HTTP POST over tailnet
│  james-bridge → :3337 bridge ingest
│  authenticated by Tailscale identity
▼
┌────────────────────────────────────────────┐
│  [2]  BRIDGE INGEST  (CoS Mac Mini)        │
│  validates payload + Tailscale identity    │
│  appends to event log via EventStore       │
│  (preserves owner_scope from payload)      │
└────────────────────────────────────────────┘
│
│ event bus dispatch (in-process pub/sub)
▼
┌────────────────────────────────────────────┐
│  [3]  PROJECTION  member_knowledge         │
│  handler on_note_added:                    │
│    INSERT row in notes (owner_scope=       │
│    private:james); body indexed for        │
│    later vector_search consumption         │
└────────────────────────────────────────────┘
│
│ event bus dispatch (same event, different subscriber)
▼
┌────────────────────────────────────────────┐
│  [4]  PROJECTION  vector_search            │
│  handler embeds note body (excluded if     │
│  sensitivity=privileged per [§6.9]) and    │
│  upserts the index row                     │
└────────────────────────────────────────────┘
│
│ event bus dispatch (same event, different subscriber)
▼
┌────────────────────────────────────────────┐
│  [5]  PIPELINE  commitment_extraction      │
│  subscribed to: messaging.received,        │
│   note.added, voice_note.added (post-D17)  │
│   (a) skip rules (privileged? other        │
│       member's owner_scope? not James's    │
│       case here)                           │
│   (b) call skill: classify_candidate       │───► skill.call.recorded
│   (c) call skill: extract_fields           │───► skill.call.recorded
│   (d) emit: commitment.proposed            │
│  ev_prop_002                               │
└────────────────────────────────────────────┘
│
(continues identically to the iMessage example
from this point — proposal lands in
commitments projection + inbox surface,
James confirms, reward dispatch fires.)
Total events emitted from this one Apple Note:
1  note.added
2  skill.call.recorded
1  commitment.proposed
1  commitment.confirmed   (after James clicks confirm)
1  reward.ready
───
6 events · 1 correlation_id · traceable end to end
physical knowledge segregation: the note's audio source
never left James's bridge Mac Mini's iCloud account.
```

The arrows to internalize: bridges are L1's two-place shape — central adapters at process scope on the CoS Mac Mini, bridge adapters at member-bridge scope. Both emit into the same event log; neither writes projections or calls pipelines directly. The bridge is the privacy boundary at the physical layer.

### Third canonical example: Cat-B round-trip — a new Family Groceries item

**Why this is parallel.** Cat-B (External-State-Mirror) adapters are the round-trip shape: an item added on iPhone in the kitchen flows through the bridge into the central event log, then fans out via iCloud Shared List back to every other family member's Reminders.app. The `lists` projection materializes the row; surfaces refresh.

```text
Laura adds "milk" to "Family Groceries" in Reminders.app on iPhone
│
│  iCloud Shared List sync to Laura's Mac (her bridge)
▼
┌────────────────────────────────────────────┐
│  [1]  ADAPTER  (runs on laura-bridge)      │
│  cat_b_external_state_mirror:              │
│    apple_reminders                         │
│  watches EventKit reminders changes        │
│  emit: list_item.added@v1                  │     correlation_id
│  ev_li_004                                 │     assigned here:
│  payload.list = "Family Groceries"         │     c_li_ghi789
│  payload.body = "milk"                     │
│  payload.added_by_party = laura_id         │
│  payload.owner_scope = shared:household    │
│  payload.external_id_kind =                │
│    'apple_reminders'                       │
│  payload.external_list_id = <icloud-id>    │
│  payload.sharing_model =                   │
│    'icloud_shared_list'                    │
└────────────────────────────────────────────┘
│
│  HTTP POST over tailnet
│  laura-bridge → :3337 bridge ingest
│  Tailscale identity binds owner_scope check
▼
┌────────────────────────────────────────────┐
│  [2]  BRIDGE INGEST  (CoS Mac Mini)        │
│  validates payload + Tailscale identity    │
│  appends to event log via EventStore       │
└────────────────────────────────────────────┘
│
│ event bus dispatch (in-process pub/sub)
▼
┌────────────────────────────────────────────┐
│  [3]  PROJECTION  lists                    │
│  handler on_list_item_added:               │
│    UPSERT lists row by                     │
│      (external_id_kind, external_list_id)  │
│      — Cat-B dedup invariant               │
│    INSERT list_items row                   │
│      with status='open',                   │
│           added_by_party=laura_id          │
└────────────────────────────────────────────┘
│
│ event bus dispatch (same event,
│                    different subscriber)
▼
┌────────────────────────────────────────────┐
│  [4]  XLSX FORWARD DAEMON                  │
│  rewrites the Lists sheet of               │
│  ~/.adminme/projections/adminme-ops.xlsx   │
│  (debounced 5s per [§3.11])                │
└────────────────────────────────────────────┘
│
│ console SSE refresh fan-out
▼
┌────────────────────────────────────────────┐
│  [5]  CONSOLE  (every connected family     │
│        member's Today view)                │
│  Family Groceries section refreshes        │
│  showing "milk" with Laura attribution     │
└────────────────────────────────────────────┘
                                              │
                                              │  meanwhile, on the
                                              │  iCloud-Shared-List side:
                                              ▼
                              ┌────────────────────────────────────┐
                              │  iCloud propagates "milk" to every │
                              │  family member's Reminders.app —   │
                              │  AdministrateMe never wrote back   │
                              │  upstream because the upstream     │
                              │  IS the source of truth (per D18). │
                              │  AdministrateMe is the mirror.     │
                              └────────────────────────────────────┘

Total events emitted from this one Reminders add:
1  list_item.added
───
1 event · 1 correlation_id · zero round-trip churn.
The deduplication invariant guarantees that even if a
second bridge (e.g. James's Mac Mini, also subscribed
to the iCloud Shared List) observes the same item and
emits its own list_item.added, the lists projection's
UPSERT keyed on (external_id_kind, external_list_id,
external_item_id) collapses both observations to one row.
```

The arrows to internalize: Cat-B is a one-way ingest path on the AdministrateMe side, even though the upstream surface is bidirectional from the human's perspective. AdministrateMe writes back ONLY when a CoS-side action (slash command, console click, pipeline action) requests a write; the Cat-B adapter then emits a `list_item.toggle_completion_requested` or similar request event which the *same* adapter's handler consumes to perform the upstream write. Inbound and outbound are two distinct paths through the same adapter, both routed through the event log.

### Fourth canonical example: Cat-E with observation-mode integration — `/lights off`

**Why this is the framework canary.** Cat-E (Outbound-Action) is the framework's most consequential category — the system actually *does* something in the world. Observation mode is the safety belt. This example shows both the active-mode and observation-mode paths through a Cat-E adapter, with Home Assistant as the v1 reference implementation per [D24].

```text
James types `/lights off` in iMessage at 9:37 PM
│
▼
┌────────────────────────────────────────────┐
│  [1]  OPENCLAW                              │
│  routes slash to AdministrateMe handler    │
│  POST :3336/api/automation/ha/services      │
│   body: {domain: "light", service: "turn_  │
│         off", target: "all"}                │
└────────────────────────────────────────────┘
│
▼
┌────────────────────────────────────────────┐
│  [2]  AUTOMATION PRODUCT (:3336)           │
│  /api/automation/ha/services router        │
│  guardedWrite three-layer check (allowlist │
│   / governance / rate_limit) per [§6.5]    │
│  emit: ha.service_call_requested@v1        │     correlation_id
│  ev_ha_005                                 │     assigned here:
│  payload.domain = "light"                  │     c_ha_jkl012
│  payload.service = "turn_off"              │
│  payload.target.entity_id = "all"          │
│  payload.requested_by = james_id           │
└────────────────────────────────────────────┘
│
│ event bus dispatch
▼
┌────────────────────────────────────────────┐
│  [3]  HA ADAPTER, Cat-E SEAM                │
│  subscribed to: ha.service_call_requested  │
│  consumes payload                           │
│                                             │
│  CHECK: outbound() seam per [§6.20]         │
│   ┌─────────────────────────────────────┐   │
│   │ if observation_mode == active:       │   │
│   │   emit observation.suppressed@v1     │   │
│   │   payload = original request +       │   │
│   │     would_have_called_endpoint =     │   │
│   │     "POST /api/services/light/turn_  │   │
│   │      off"                             │   │
│   │   STOP — do NOT call HA REST          │   │
│   │                                       │   │
│   │ else (observation off):               │   │
│   │   POST http://ha-host:8123/api/       │   │
│   │     services/light/turn_off           │   │
│   │     auth: long_lived_access_token     │   │
│   │     body: {entity_id: "all"}          │   │
│   │   on success: emit action.executed    │   │
│   │   on error: emit action.failed        │   │
│   └─────────────────────────────────────┘   │
└────────────────────────────────────────────┘
│
▼
( in active mode: lights actually turn off; James's chat
  receives "Done — turned off all lights." )

( in observation mode: lights stay on; James's chat
  receives "Observation mode active — would have turned
  off all lights. View suppressed actions in Settings →
  Observation." Settings → Observation pane shows the
  ev_ha_005 → observation.suppressed entry with full
  payload for review. )

Total events emitted in observation mode:
1  ha.service_call_requested
1  observation.suppressed
───
2 events · 1 correlation_id · zero side effect on
external world. The exact same enforcement point
([§6.20] / `adminme/lib/observation.py`) that gates
Cat-A messaging outbound also gates Cat-E action
outbound. Same seam, different verb.
```

The arrows to internalize: Cat-E adapters integrate observation mode at the **action verb** the same way Cat-A messaging adapters integrate it at the **message verb**. The `outbound()` seam in `adminme/lib/observation.py` is the single enforcement point for both routes per [§6.14] / [§6.20]. Cat-C (state-read) and the inbound half of any multi-capability adapter (e.g. HA's Cat-C state-read seam) are unaffected by observation mode — reading is internal logic, not external side effect.

---

## 3. guardedWrite: the three-layer check

**Canonical in CONSOLE_PATTERNS.md §3.** Also governs every write from a pipeline or a user action.

**What to notice.** The layers are ordered; denials short-circuit. `review` is a fourth exit — not a denial, a hold. Each exit emits a distinct audit event.

```
               incoming write request
                      │
                      ▼
          ┌─────────────────────────┐
          │  LAYER 1 : ALLOWLIST    │
          │                         │
          │  is this agent even     │
          │  permitted to attempt   │   pattern match:
          │  this action?           │   'message.send'
          │                         │   matches 'message.*'
          │                         │   or exact 'message.send'
          └────────────┬────────────┘
                       │
              no       │       yes
          ┌────────────┤
          ▼            │
     ╭─────────╮       ▼
     │  DENY   │  ┌─────────────────────────┐
     │  403    │  │  LAYER 2 : ACTION GATE  │
     │         │  │                         │
     │ emit:   │  │  governance.yaml says   │  values:
     │ denied  │  │  what this action is    │   allow
     │ .allow- │  │  gated to               │   review
     │ list    │  │                         │   deny
     ╰─────────╯  │                         │   hard_refuse
                  └─┬─┬─┬─┬───────────────┬─┘
                    │ │ │ │               │
           hard     │ │ │ │               │  allow
           refuse   │ │ │ │               │
                ╭───┘ │ │ │               │
                │     │ │ │               │
                ▼     │ │ │               │
         ╭──────────╮ │ │ │               │
         │  DENY    │ │ │ │               │
         │  403     │ │ │ │               │
         │ emit:    │ │ │ │               │
         │ denied.  │ │ │ │               │
         │ hard_    │ │ │ │               │
         │ refuse   │ │ │ │               │
         ╰──────────╯ │ │ │               │
                   deny │ │               │
                      ╭─┘ │               │
                      │   │               │
                      ▼   │               │
               ╭──────────╮│               │
               │   DENY   ││               │
               │   403    ││               │
               │  emit:   ││               │
               │  denied  ││               │
               │  .policy ││               │
               ╰──────────╯│               │
                     review │              │
                         ╭──┘              │
                         │                 │
                         ▼                 │
                  ╭──────────────╮         │
                  │  HOLD · 202  │         │
                  │              │         │
                  │  emit:       │         │
                  │  review.     │         │
                  │  requested   │         │
                  │              │         │
                  │  queue for   │         │
                  │  principal   │         │
                  │  approval    │         │
                  ╰──────────────╯         │
                                           │
                                           ▼
                             ┌─────────────────────────┐
                             │  LAYER 3 : RATE LIMIT   │
                             │                         │
                             │  sliding window         │
                             │  (scope, action) key    │
                             └─────────────┬───────────┘
                                           │
                              exceeded     │     within limit
                          ┌────────────────┤
                          ▼                │
                   ╭──────────────╮        ▼
                   │  DENY · 429  │  ┌─────────────────────┐
                   │              │  │  EXECUTE writeFn    │
                   │  include:    │  │                     │
                   │  retry_      │  │  stamped with       │
                   │  after_s     │  │  correlation_id     │
                   │              │  │                     │
                   │  emit:       │  │  on success emit:   │
                   │  denied.     │  │  write.succeeded    │
                   │  rate_limit  │  │                     │
                   ╰──────────────╯  │  on throw emit:     │
                                     │  write.failed       │
                                     │  then re-raise      │
                                     └─────────────────────┘

  Audit invariant: every request produces exactly one terminal
  event (denied.*, review.requested, write.succeeded, or write.failed).
  No path is silent.
```

---

## 4. authMember / viewMember split

**Canonical in CONSOLE_PATTERNS.md §2.**

**What to notice.** **Writes always use authMember.** **Reads use viewMember.** The "viewing as Laura" dropdown changes what James sees, not what he can do. If James clicks a task complete while viewing Laura's surface, the task.completed event's `actor_member_id` is James — correct, because James performed the action, even if the task is Laura's.

```
  Tailscale identity          ←── SOURCE OF TRUTH FOR AUTH
       │
       ▼
  ┌─────────────────────┐
  │  authMemberId       │
  │  authRole           │   set once per request; cannot be overridden
  │  tailscaleLogin     │
  └──────────┬──────────┘
             │
             │   ?view_as=stice-laura  (from query or header)
             │
             ▼
  ┌───────────────────────────────────┐
  │  can authMember view-as?          │
  │                                   │
  │  authRole == 'principal'?         │──no──► 403
  │  target in same household?        │──no──► 403
  │  target role != 'ambient'?        │──no──► 403
  │  authMember == target?            │──yes─► viewMember = authMember
  │                                   │         (fast path · skip cross-member ACL)
  └─────────────┬─────────────────────┘
                │  all yes
                ▼
  ┌─────────────────────┐
  │  viewMemberId       │   may differ from authMemberId;
  │  viewRole           │   may be any non-ambient member
  │  profileId          │   in the same household
  └─────────────────────┘

  Session object carries both.

  ┌───────────────────────────────────────────────────────────┐
  │                                                           │
  │                      REQUEST ROUTING                      │
  │                                                           │
  │   ┌──────────────────────┐    ┌─────────────────────────┐ │
  │   │   READ path          │    │   WRITE path            │ │
  │   │                      │    │                         │ │
  │   │   uses:              │    │   uses:                 │ │
  │   │     viewMemberId     │    │     authMemberId        │ │
  │   │                      │    │                         │ │
  │   │   + privacy filter   │    │   + guardedWrite:       │ │
  │   │     applied with     │    │     allowlist keyed on  │ │
  │   │     (authMember,     │    │     'user:{authMember}' │ │
  │   │      viewMember)     │    │                         │ │
  │   │                      │    │   + events record:      │ │
  │   │   → "James sees      │    │     actor = authMember  │ │
  │   │     Laura's view     │    │     target = whatever   │ │
  │   │     with privileged  │    │              the action │ │
  │   │     events as        │    │              was on     │ │
  │   │     [busy]"          │    │                         │ │
  │   └──────────────────────┘    └─────────────────────────┘ │
  │                                                           │
  └───────────────────────────────────────────────────────────┘

  Examples:

  james viewing james:   viewMember=james,  authMember=james
    fast path. Normal self-service.

  james viewing laura:   viewMember=laura,  authMember=james
    read: laura's data through james's privacy lens
          (her privileged work events → "[busy]")
    write: if james approves a commitment in her inbox view,
           commitment.confirmed.approved_by = james,
                                   .owner    = laura
           both IDs captured; never collapsed.

  charlie viewing laura:  403 at session build
    children cannot view-as regardless of target.
```

---

## 5. Session and scope enforcement

**Canonical in BUILD.md:** "L3 CONTINUED: THE SESSION & SCOPE ENFORCEMENT" section.

**What to notice.** The scope check is **multi-dimensional** — sensitivity × owner_scope × authRole × session_type. Not a simple ACL list. The matrix below shows the common cases; the full matrix has ~40 cells but 80% of traffic lives in these rows.

```
                                               QUERY ALLOWED?
                                               ───────────────
                                               READ    WRITE
  authRole    sensitivity   owner_scope        
  ────────    ───────────   ───────────
  principal   normal        household          YES     YES
  principal   normal        private:{self}     YES     YES
  principal   normal        private:{other}    NO      NO    ← other principal's private
  principal   sensitive     household          YES     YES
  principal   sensitive     private:{self}     YES     YES
  principal   sensitive     private:{other}    NO      NO
  principal   privileged    household          YES     NO    ← read for busy-block
                                                              rendering; no writes
  principal   privileged    private:{self}     YES     YES   ← owner of the
                                                              privileged content
  principal   privileged    private:{other}    NO*     NO    ← *except coarse
                                                              time/duration for
                                                              calendar busy blocks
                                                              (filtered by
                                                              redactToBusy at
                                                              projection read)

  child       normal        household          YES**   NO    ← **minus tags in
                                                              CHILD_FORBIDDEN_TAGS
                                                              (finance, health,
                                                              legal, adult_only)
  child       normal        private:{self}     YES     NO    ← scoreboard only
  child       sensitive     *                  NO      NO
  child       privileged    *                  NO      NO

  ambient     *             *                  NO      NO    ← no surface at all
                                                              (enforced at session
                                                              build: role='ambient'
                                                              returns 403)

  device      normal        household          YES     NO    ← scoreboard TV
                                                              (Tailscale device,
                                                              not mapped member)
  device      sensitive+    *                  NO      NO

  session_type = 'coach' (external-context assistant session):
    + all 'financial_*' and 'health_*' columns stripped before context build
    + even for the owner
    + enforced at Session construction; no bypass

  ─────────────────────────────────────────────────────────────────────

  Enforcement sites:
    (1) Session object construction     — rejects impossible combos up front
    (2) Projection query wrappers       — apply sensitivity+scope WHERE clauses
    (3) Privacy filter at read          — privileged → busy_block redaction
    (4) Console nav middleware          — HIDDEN_FOR_CHILD, ambient → 403
    (5) guardedWrite                    — write-side action gating per governance
    (6) Final outbound filter           — PII-leakage check before external call
    (7) Observation-mode wrapper        — suppresses external side effects

  All seven layers must be present. A single layer is not a privacy model;
  it's a bug waiting for a scenario where the layer doesn't fire.
```

---

## 6. xlsx round-trip: bidirectional human-editable projection

**Canonical in BUILD.md:** §3.11 `xlsx_workbooks` projection.

**What to notice.** Two daemons, two directions, one shared file lock. The sidecar state files are what make the reverse projection diffable without replaying the entire event log on every save. Conflicts (forward regen + human save racing) resolve in favor of forward — the human's in-progress edit is lost on that cycle, which is why debounce is short (5s) and why the reverse-skip event exists for visibility.

```
  ┌──────────────────────────────────────────────────────────────┐
  │                       EVENT LOG                              │
  │                   (source of truth)                          │
  └─────┬────────────────────────────────────────────────────▲───┘
        │                                                    │
        │ subscribe                            append events │
        │ trigger types                        through       │
        │                                      emit_event    │
        ▼                                                    │
  ┌──────────────────────┐                  ┌────────────────┴────────┐
  │  FORWARD DAEMON      │                  │   REVERSE DAEMON        │
  │  xlsx_sync/forward   │                  │   xlsx_sync/reverse     │
  │                      │                  │                         │
  │  • 5s debounce       │                  │  • watchdog on both     │
  │  • on burst: regen   │                  │    .xlsx files          │
  │    only affected     │                  │  • on modified event:   │
  │    sheets            │                  │    wait 2s (flush)      │
  │  • openpyxl write    │                  │    acquire lock         │
  │    to temp + rename  │                  │    load data_only=True  │
  │    atomically        │                  │                         │
  └───────┬──────────────┘                  └──────────┬──────────────┘
          │ acquire lock                               │ acquire lock
          │                                            │
          │        ┌──────────────────────────┐        │
          ├───────►│   FILE LOCK (.lock)      │◄───────┤
          │        │   flock sidecar          │        │
          │        └──────────────────────────┘        │
          │                                            │
          ▼                                            ▼
  ┌───────────────────────────┐       ┌──────────────────────────────┐
  │   adminme-ops.xlsx        │       │   Load sheet → DataFrame A   │
  │   adminme-finance.xlsx    │       │   Load sidecar → DataFrame B │
  │                           │       │   Diff by *_id columns:      │
  │   .lock          (flock)  │       │     • added rows             │
  │                           │       │     • deleted rows           │
  └───────────────────────────┘       │       (5s undo window)       │
          ▲                           │     • modified rows          │
          │ regenerate +              │                              │
          │ overwrite                 │   For each diff row:         │
          │                           │     emit_event(...)          │
          │                           │                              │
  ┌───────┴──────────────────┐        │   Write new state →          │
  │   .xlsx-state/           │◄───────┤   sidecar                    │
  │   (sidecar)              │        │                              │
  │                          │        │   Release lock               │
  │   per-sheet JSON         │        └──────────────────────────────┘
  │   snapshot of last       │
  │   forward-projected      │
  │   state. Basis for       │
  │   reverse diff.          │
  └──────────────────────────┘

  ─────────────────────────────────────────────────────────────────────
  PROTECTION MODEL

   Column header tag    Forward behavior        Reverse behavior
   ─────────────────    ────────────────        ─────────────────
   [derived]            cell.locked = True      edits silently ignored
   [plaid-authoritative] cell.locked = True      edits silently ignored
                         on those specific       (e.g. merchant_name on
                         columns                 a Plaid-sourced row)
   [bidirectional]      cell.locked = False     edits emit events
   id columns           cell.locked = True      edits silently ignored

  ─────────────────────────────────────────────────────────────────────
  CONFLICT: forward regen + human save at same time

   T=0     user saves xlsx
   T=0+1s  reverse daemon wakes, waits 2s for flush
   T=0+3s  reverse tries to acquire lock → HELD by forward
   T=0+3s  reverse waits up to 10s for lock
   T=0+5s  forward releases lock (regen complete)
           ─ xlsx now reflects post-forward state
           ─ user's edits OVERWRITTEN
   T=0+5s  reverse acquires lock, loads xlsx
           DataFrames match sidecar → no diff → no events
   T=0+5s  reverse emits xlsx.reverse_skipped_during_forward
           for visibility
   T=0+5s  user sees nothing happened to their edits

   Mitigation: 5s debounce → small window
   Mitigation: visibility event → principal can see it happened
   Accepted: rare in practice; fixing requires two-phase commit
             which is more complexity than the failure mode warrants
```

---

## 7. Machine topology and the tailnet

**Canonical in BUILD.md:** "MACHINE TOPOLOGY & NETWORK" section.

**What to notice.** The Mac Mini is the hub; everything household-side terminates on it. Tailscale is the only network surface. No port forwarding, no public DNS, no reverse proxy exposed to the internet. External services (Plaid, Google, Anthropic) are reached *outbound* from the Mac Mini; the tailnet never receives inbound internet traffic.

```
  ─────────────────────────────────────────────────────────────────────
                            HOUSEHOLD TAILNET
                         stice-family.ts.net (example)
  ─────────────────────────────────────────────────────────────────────

      ┌─────────────────┐       ┌─────────────────┐     ┌──────────────┐
      │ James's iPhone  │       │ Laura's iPhone  │     │  Scoreboard  │
      │ tailscale       │       │ tailscale       │     │  TV (Apple   │
      │ james@...       │       │ laura@...       │     │  TV + Web    │
      └────────┬────────┘       └────────┬────────┘     │  kiosk)      │
               │                         │              └──────┬───────┘
               │                         │                     │
      ┌────────┴────────┐       ┌────────┴────────┐     ┌──────┴───────┐
      │ James's Mac     │       │ Laura's Mac     │     │ Charlie's    │
      │ tailscale       │       │ tailscale       │     │ iPad (in     │
      │                 │       │                 │     │ kid mode;    │
      └────────┬────────┘       └────────┬────────┘     │ scoreboard)  │
               │                         │              └──────┬───────┘
               └─────────────────────────┴─────────────────────┘
                                      │
                                      │  all traffic over
                                      │  WireGuard (tailscale)
                                      │
       ─── Shelf at the household site ─────────────────────────
       │                                                       │
   ┌───┴───────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐  │
   │ james-    │  │ laura-    │  │ charlie-  │  │  CoS      │  │
   │  bridge   │  │  bridge   │  │  bridge   │  │ MAC MINI  │  │
   │ (Mac Mini)│  │ (Mac Mini)│  │ (kid var.)│  │  (hub)    │  │
   │           │  │           │  │           │  │           │  │
   │ iCloud:   │  │ iCloud:   │  │ iCloud:   │  │~/.adminme/│  │
   │  james    │  │  laura    │  │ charlie   │  │ lives here│  │
   │           │  │           │  │           │  │           │  │
   │ Apple     │  │ Apple     │  │ Apple     │  │ no member │  │
   │  Notes    │  │  Notes    │  │  Notes    │  │  iCloud   │  │
   │ Voice     │  │ Voice     │  │ Voice     │  │  signin   │  │
   │  Notes    │  │  Notes    │  │  Notes    │  │           │  │
   │ Obsidian* │  │ Obsidian* │  │ (kid: no  │  │           │  │
   │           │  │           │  │  Obsidian)│  │           │  │
   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  │
         │              │              │              │        │
         └──────────────┴──────────────┴──────────────┘        │
                              │                                │
                              │  bridge-ingest                 │
                              │  HTTP POST /api/bridge/        │
                              │  over tailnet                  │
                              │                                │
                              ▼                                │
                       (CoS Mac Mini :3337)                    │
                                                               │
       ─── (* Obsidian opt-in per member) ────────────────────┘
                                      │
                                      │  loopback (Node ↔ Python)
                                      │
   ┌──────────────────────────────────┴──────────────────────────────────┐
   │                                                                     │
   │                          MAC MINI PROCESSES                         │
   │                                                                     │
   │   ┌────────────────────────────────────────────────────────────┐    │
   │   │                Node console @ 127.0.0.1:3330               │    │
   │   │        (tailscale serve TLS terminated → local :3330)      │    │
   │   └────────────────────────────────────────────────────────────┘    │
   │                                │                                    │
   │                HTTP bridge     │                                    │
   │                                ▼                                    │
   │   ┌────────────────────────────────────────────────────────────┐    │
   │   │       Python product APIs (FastAPI, localhost only)        │    │
   │   │       :3333 core   :3334 comms   :3335 capture  :3336 auto │    │
   │   └────────────────────────────────────────────────────────────┘    │
   │                                │                                    │
   │                                ▼                                    │
   │   ┌────────────────────────────────────────────────────────────┐    │
   │   │   Daemons (non-HTTP): xlsx_sync_forward, xlsx_sync_reverse,│    │
   │   │   reminders_sync, event_dispatcher, adapter supervisors    │    │
   │   └────────────────────────────────────────────────────────────┘    │
   │                                │                                    │
   │                                ▼                                    │
   │   ┌────────────────────────────────────────────────────────────┐    │
   │   │        ~/.adminme/db/event_log.db   (SQLCipher)            │    │
   │   │        ~/.adminme/projections/*.db + *.xlsx                │    │
   │   └────────────────────────────────────────────────────────────┘    │
   │                                                                     │
   │   ┌────────────────────────────────────────────────────────────┐    │
   │   │   BlueBubbles server (separate process; also on Mac Mini)  │    │
   │   │   Signed in as assistant Apple ID. Port 1234 localhost.    │    │
   │   │   The messaging:bluebubbles_adminme adapter connects here. │    │
   │   └────────────────────────────────────────────────────────────┘    │
   │                                                                     │
   └──────────────────────────┬──────────────────────────────────────────┘
                              │
                              │  OUTBOUND ONLY
                              │  (nothing inbound from internet)
                              │
                              ▼
     ═══════════════════════════════════════════════════════════════
                           PUBLIC INTERNET
     ═══════════════════════════════════════════════════════════════
                              │
       ┌──────────────┬───────┴───────┬───────────────┬──────────────┐
       │              │               │               │              │
       ▼              ▼               ▼               ▼              ▼
   ┌──────┐     ┌─────────┐     ┌──────────┐    ┌─────────┐    ┌─────────┐
   │Plaid │     │ Google  │     │Anthropic │    │ Twilio  │    │ iCloud  │
   │ API  │     │Workspace│     │ API      │    │ SMS     │    │ (via    │
   │      │     │ OAuth + │     │ (LLM     │    │ (fallb- │    │ Apple   │
   │Webhook│    │ APIs    │     │ calls)   │    │  ack    │    │ ID on   │
   │ ←─── │     │         │     │          │    │  only)  │    │ Mac)    │
   │       │    │         │     │          │    │         │    │         │
   └───────┘    └─────────┘     └──────────┘    └─────────┘    └─────────┘

  Plaid webhook inbound: uses Tailscale Funnel (serve configured
  for one public path). Webhook signature verified; rate-limited;
  events land in event log. Only path where tailnet sees inbound internet.
```

---

## 8. Pack installation flow

**Canonical in BUILD.md:** "PACK REGISTRY" section; REFERENCE_EXAMPLES.md appendix.

**What to notice.** JSX compile happens at install time, not at run time. Tests run against the installed pack in an isolated fixture; failed tests roll back the registration. A `pack.installed` event is the atomic commit — either it's in the log or nothing happened.

```
  user: adminme pack install ./adhd-executive/
              │
              ▼
  ┌──────────────────────────────────────┐
  │  1. Validate manifest                │
  │     ─ pack.yaml parses?              │     on any failure:
  │     ─ required fields?               │     ─ log specific reason
  │     ─ kind ∈ {adapter, pipeline,     │     ─ exit non-zero
  │               skill, projection,     │     ─ no side effects
  │               profile, persona}      │
  │     ─ id unique?                     │
  └───────────┬──────────────────────────┘
              │ ok
              ▼
  ┌──────────────────────────────────────┐
  │  2. Platform compat check            │
  │     min_platform ≤ current?          │
  └───────────┬──────────────────────────┘
              │ ok
              ▼
  ┌──────────────────────────────────────┐
  │  3. Resolve dependencies             │
  │     ─ for pipelines:                 │
  │         all named skills installed?  │
  │         all named projections exist? │
  │     ─ for profiles:                  │
  │         skill_overrides refer to     │
  │         installed skills?            │
  │     ─ for personas:                  │
  │         theme tokens parseable?      │
  └───────────┬──────────────────────────┘
              │ ok
              ▼
  ┌──────────────────────────────────────┐
  │  4. Compile (if needed)              │
  │     profile packs:                   │
  │       for each views/*.jsx:          │
  │         esbuild → compiled/*.ssr.js  │
  │         esbuild → compiled/*.client  │
  │         extract CSS                  │
  │     adapters/pipelines/skills:       │
  │       no compile; Python runs direct │
  └───────────┬──────────────────────────┘
              │ ok
              ▼
  ┌──────────────────────────────────────┐
  │  5. STAGE into fixture instance      │
  │     tmpdir clone of instance         │
  │     install pack into tmpdir         │
  │     run pack's tests/ against it     │
  │                                      │
  │   all tests pass? ── no ─┬──► ROLLBACK:
  │                          │     delete tmpdir,
  │                          │     report failures,
  │                          │     no log entry,
  │                          │     exit non-zero
  │     yes                  │
  └───────────┬──────────────┘
              │
              ▼
  ┌──────────────────────────────────────┐
  │  6. COMMIT into live instance        │
  │     copy pack → ~/.adminme/packs/…   │
  │     INSERT into installed_packs      │
  │     register event subscriptions     │
  │       (pipelines subscribe now)      │
  │     register adapter capabilities    │
  │     profile becomes assignable       │
  │     persona becomes activatable      │
  │     skill becomes callable           │
  └───────────┬──────────────────────────┘
              │
              ▼
  ┌──────────────────────────────────────┐
  │  7. EMIT pack.installed event        │
  │     now in event log; atomic.        │
  │     payload: pack_id, version,       │
  │              installed_by,           │
  │              install_duration_ms     │
  └───────────┬──────────────────────────┘
              │
              ▼
           exit 0

  Uninstall reverses 6 → 5 → 4 with safety checks:
    profile: fail if assigned to any member (force flag bypasses;
             emits pack.force_uninstalled)
    persona: fail if active (force flag bypasses)
    skill:   fail if any pipeline depends on it
    pipeline/adapter: deactivate subscriptions first, then remove
```

---

## 9. Observation mode: what fires, what gets suppressed

**Canonical in BUILD.md:** operating rule #6 and "AUTHORITY, OBSERVATION, GOVERNANCE" section. CONSOLE_PATTERNS.md §11 for the enforcement code.

**What to notice.** The check is at the **final outbound filter** — not at the pipeline-decision layer, not at the policy layer, not at the skill-call layer. All of those run normally; the last step before leaving the Mac Mini is where observation intervenes. This matters because the principal reviewing the suppressed log sees exactly what *would* have happened, not a sanitized "we would have done something."

```
  observation mode = ON
  ─────────────────────

  ADAPTERS (inbound)
  ══════════════════                    PIPELINES                      SKILLS
                                        ═════════                      ══════

  messaging.received  ──────────►   [runs normally]   ───►  [LLM calls fire]
  calendar event                    commitment_extraction   classify, extract
  ingested                          thank_you                 recorded as usual
  webhook received                  morning_digest
  Plaid sync                        paralysis_detection
  reminders.pulled                  etc.

  all events                        all events
  appended                          appended
  to log                            to log
                                                               │
                                                               │ event bus
                                                               ▼
                                                     PROJECTIONS update
                                                     ═════════════════════

                                                     parties
                                                     commitments
                                                     inbox_surface
                                                     xlsx_workbooks
                                                     dashboard

                                                     (all derived state
                                                      stays coherent)
                                                               │
                                                               │
                                                               ▼
                                                     SURFACES
                                                     ════════
                                                     console renders normally
                                                     morning digest composed
                                                     reward preview returned
                                                     locally on click
                                                     inbox shows new items

                                                               │
                                                               │
                                                               ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │                                                                     │
  │                   FINAL OUTBOUND FILTER                             │
  │                                                                     │
  │   outbound(ctx, actionFn):                                          │
  │                                                                     │
  │      if observation_mode:                                           │
  │         emit observation.suppressed {                               │
  │           action: ctx.action                                        │
  │           would_have_sent: full_payload                             │
  │         }                                                           │
  │         return { suppressed: true }                                 │
  │      else:                                                          │
  │         return await actionFn()   ── external call fires            │
  │                                                                     │
  └─────────────────────────────────────────────────────────────────────┘
                                                               │
                                                               │ SUPPRESSED
                                                               │
             ┌─────────────────────────────────────────────────┼──────────────┐
             │                                                 │              │
             ▼                                                 ▼              ▼
   messaging.outbound.                                 push.send           email.send
   requested                                           (APNs)              (SMTP)

   BlueBubbles API call                                Apple Push          SMTP relay
   iMessage send                                       Notification
   SMS send                                            Service
   Telegram / Discord                                                    ── all three
                                                                            suppressed

   suppressed: would have sent                         suppressed          suppressed
   blue bubble to kate@...                             would have          would have sent
                                                       pushed "done"       "weekly digest"
                                                                           to james@...

           All routed to observation-mode log.
           Principal reviews in Settings → Observation.
           Each suppressed payload is the full would-have-sent object.
           Nothing reached the outside world.

  ─────────────────────────────────────────────────────────────────────

  What still happens in observation mode (not suppressed):

   ✓ Events appended to log
   ✓ Projections rebuild
   ✓ xlsx files regenerate
   ✓ Console UI renders
   ✓ LLM skills fire (they're internal; model-call cost is real but fine)
   ✓ Reward preview returns from task completion endpoint
   ✓ Reward toast shows locally in the clicking tab
   ✓ Chat (SSE) streams in the console (it's local)

  What is suppressed:

   ✗ Any outbound to external message channel (iMessage, SMS, email, push)
   ✗ Any write to Apple Reminders (outbound half of bidi)
   ✗ Any write to Plaid (rare; Plaid is mostly read)
   ✗ Any webhook firing to an external service
   ✗ Reward notifications via push (but local toast fires)
   ✗ Digest delivery to the configured channel (but digest appears in console)
   ✗ Paralysis nudges via the member's preferred channel
   ✗ CRM gap nudges out to anyone

  Default for new instance: ON.
  Typical flip-to-off: 5-7 days post-bootstrap, after principal reviews
  the observation log and sees nothing surprising.
```

---

## 10. Bootstrap wizard state machine

**Canonical in BUILD.md:** "BOOTSTRAP WIZARD" section.

**What to notice.** Each section is independently resumable. Skipping a section doesn't abort — it leaves an inbox task for later completion. The wizard writes every answer to an encrypted answers file; re-running the wizard jumps to the last incomplete section.

```
                              START
                                │
                                ▼
              ┌───────────────────────────────────────┐
              │  Load encrypted answers.yaml.enc if   │
              │  present.                             │
              │                                       │
              │  Resume at first incomplete section.  │
              └───────────────┬───────────────────────┘
                              │
                              ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §1  Environment preflight                        │
     │       macOS version, user, FileVault,              │
     │       Tailscale, Node 22+, Python 3.11+,           │
     │       Homebrew, git, gh, rclone, LibreOffice,      │
     │       1Password CLI                                │
     │                                                    │
     │   ─ fail:  ABORT (no partial state)                │
     │   ─ pass:  emit env.checked  →  next               │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §2  Name your assistant                          │
     │       name · emoji · voice preset · reward style   │
     │       · palette                                    │
     │                                                    │
     │   ─ writes config/persona.yaml                     │
     │   ─ emit persona.activated                         │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §3  Household composition                        │
     │       name · address · tz · adults · children ·    │
     │       expected arrivals · coparents · helpers      │
     │                                                    │
     │   ─ emits member.created, party.created,           │
     │     membership.added, relationship.added (many)    │
     │   ─ parties projection builds                      │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §4  Assign profiles                              │
     │       per adult · per child                        │
     │       tuning at defaults                           │
     │                                                    │
     │   ─ emit member.profile_assigned                   │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §5  Assistant credentials                        │
     │                                                    │
     │       PER CREDENTIAL:                              │
     │       ┌────────────────────────────────┐           │
     │       │ collect → test → store in      │           │
     │       │ 1Password → record reference   │           │
     │       │                                │           │
     │       │ on test fail: retry 3x         │           │
     │       │ still fail + credential        │           │
     │       │ optional: skip, add later      │           │
     │       │ still fail + required:         │           │
     │       │ pause wizard, fix, resume      │           │
     │       └────────────────────────────────┘           │
     │                                                    │
     │       Required:  Apple ID, Phone, Workspace,       │
     │                  1Password, Anthropic, Tailscale,  │
     │                  Backblaze B2, GitHub              │
     │       Optional:  OpenAI, Twilio, Telegram,         │
     │                  Discord, Tavily, ElevenLabs,      │
     │                  Deepgram, Privacy.com, Lob, HA    │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §6  Plaid                                        │
     │       client_id + sandbox_secret                   │
     │       first Link flow (sandbox)                    │
     │                                                    │
     │   ─ writes config/plaid.yaml (environment:sandbox) │
     │   ─ emit plaid.institution.linked                  │
     │                                                    │
     │   Go-live flip deferred to post-observation.       │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §7  Seed household data                          │
     │       address/properties · vehicles · mortgage ·   │
     │       recurring bills · healthcare providers ·     │
     │       schools · projects · vendors ·               │
     │       friends & family (CRM seed)                  │
     │                                                    │
     │   ─ many party.created, place.added, asset.added,  │
     │     account.added, recurrence.added events         │
     │   ─ skipped subsections: task.created "fill in     │
     │     later: <section>" for each, placed in inbox    │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §8  Channel pairing                              │
     │       for each selected channel:                   │
     │         ┌──────────────────────────────────┐       │
     │         │ verify · configure · test        │       │
     │         │ iMessage: Apple ID signed in +   │       │
     │         │    BlueBubbles server running    │       │
     │         │ Reminders: iCloud lists mapped   │       │
     │         │ Gmail: OAuth + Pub/Sub + Funnel  │       │
     │         │ Calendar: OAuth + watch channels │       │
     │         │ SMS (Twilio): webhook URL +      │       │
     │         │    signing secret                │       │
     │         └──────────────────────────────────┘       │
     │                                                    │
     │   PLUS Amendment-2 sub-steps (per D18/D22/D23/D24):│
     │         ┌──────────────────────────────────┐       │
     │         │ Lists auto-seed: 4 CoS-owned     │       │
     │         │   shared lists created on        │       │
     │         │   assistant Apple ID; iCloud     │       │
     │         │   Shared List invitations to     │       │
     │         │   adult+capable-teen members.    │       │
     │         │   emit list.created (×4) +       │       │
     │         │   list.share_invited per share.  │       │
     │         │                                  │       │
     │         │ Apple Calendar central variant:  │       │
     │         │   on assistant Apple ID;         │       │
     │         │   observation set up; first      │       │
     │         │   pull tested. emit              │       │
     │         │   calendar.paired{apple,central}.│       │
     │         │                                  │       │
     │         │ Google Contacts central:         │       │
     │         │   OAuth assistant Workspace;     │       │
     │         │   first contacts pull tested.    │       │
     │         │   emit contacts.paired{google}.  │       │
     │         │   (Apple Contacts pairing is     │       │
     │         │   bridge-only — handled in §10.) │       │
     │         │                                  │       │
     │         │ Home Assistant pairing:          │       │
     │         │   long-lived access token from   │       │
     │         │   §5; REST + WebSocket           │       │
     │         │   connection tested;             │       │
     │         │   ha.state_changed events        │       │
     │         │   flowing into log.              │       │
     │         │   emit ha.paired{tested:yes}.    │       │
     │         └──────────────────────────────────┘       │
     │                                                    │
     │   each pair emits channel.paired {adapter,         │
     │   channel_id, tested:yes}                          │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §9  Observation briefing                         │
     │       explain observation mode                     │
     │       show Settings → Observation path             │
     │       send FIRST outbound message                  │
     │       (this message is NOT suppressed —            │
     │        it's the briefing that proves the           │
     │        pipeline works end to end)                  │
     │                                                    │
     │   ─ emit observation.enabled {default_on: true}    │
     │   ─ emit bootstrap.completed                       │
     │   ─ generate ~/.adminme/bootstrap-report.md        │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
     ┌────────────────────────────────────────────────────┐
     │                                                    │
     │   §10  Bridge enrollment                           │
     │   (only if household has Apple-using members,      │
     │    per BUILD.md §MEMBER BRIDGES + D17)             │
     │                                                    │
     │   per member with an Apple ID:                     │
     │         ┌──────────────────────────────────┐       │
     │         │ central wizard generates an      │       │
     │         │ enrollment package per bridge:   │       │
     │         │  · per-member identity           │       │
     │         │  · tailnet auth key              │       │
     │         │  · adapter set:                  │       │
     │         │     - Apple Notes (always)       │       │
     │         │     - Voice Memos (always)       │       │
     │         │     - Apple Calendar             │       │
     │         │       bridge variant (NEW per    │       │
     │         │       D22)                       │       │
     │         │     - Apple Reminders bridge     │       │
     │         │       variant (NEW per D18       │       │
     │         │       dual-deployment)           │       │
     │         │     - Apple Contacts (NEW per    │       │
     │         │       D23)                       │       │
     │         │     - Apple Notes-checklists     │       │
     │         │       B-half (NEW per D18)       │       │
     │         │     - Obsidian (if member        │       │
     │         │       configures vault path;     │       │
     │         │       excluded for kid bridges)  │       │
     │         │  · bridge bootstrap mini-wizard  │       │
     │         │                                  │       │
     │         │ operator copies package to       │       │
     │         │ bridge Mac Mini (rsync over      │       │
     │         │ tailnet, or sneakernet at        │       │
     │         │ initial setup)                   │       │
     │         │                                  │       │
     │         │ mini-wizard runs on bridge:      │       │
     │         │  · verify macOS + iCloud signin  │       │
     │         │    (must match assigned member)  │       │
     │         │  · verify Tailscale auth         │       │
     │         │  · verify Apple Notes Full Disk  │       │
     │         │    Access; Calendar/Contacts     │       │
     │         │    permissions                   │       │
     │         │  · install bridge daemon under   │       │
     │         │    launchd                       │       │
     │         │  · configure active adapters     │       │
     │         │  · submit                        │       │
     │         │    bridge.enrollment_completed   │       │
     │         │    to central :3337 bridge       │       │
     │         │    ingest                        │       │
     │         │  · hand control back to central  │       │
     │         └──────────────────────────────────┘       │
     │                                                    │
     │   each bridge emits                                │
     │   bridge.enrolled {member_id, bridge_node_id,      │
     │     adapters_active}                               │
     │                                                    │
     │   kid bridges restricted: Apple Notes +            │
     │   Voice Memos only; no Obsidian, no Apple          │
     │   Contacts of adult contact lists, per             │
     │   §6.19 / D17 kid-bridge restriction principle.    │
     │                                                    │
     └──────────────────────────┬─────────────────────────┘
                                │
                                ▼
                          ╔════════════╗
                          ║   DONE     ║
                          ║            ║
                          ║  instance  ║
                          ║  is live   ║
                          ║  (observ.) ║
                          ╚════════════╝

                     ┌─────────────────────────────────┐
                     │  RESUMABILITY                   │
                     │                                 │
                     │  At any §, state written to     │
                     │  bootstrap-answers.yaml.enc     │
                     │  and the event log.             │
                     │                                 │
                     │  Re-running `adminme bootstrap` │
                     │  reads the answers file,        │
                     │  picks up at next incomplete §. │
                     │                                 │
                     │  Successfully completed §s are  │
                     │  idempotent — re-running is a   │
                     │  no-op (events already in log). │
                     │                                 │
                     │  Config files are never         │
                     │  rewritten from stale answers.  │
                     └─────────────────────────────────┘
```

---

## Reading order

For a cold read of the build, the diagrams are most useful in this order:

1. **§1 five-layer architecture** first — anchors everything.
2. **§2 event flow** second — shows how the layers actually compose in motion.
3. **§7 machine topology** third — contextualizes where all of this runs.
4. **§3 guardedWrite, §4 authMember/viewMember, §5 scope enforcement** together — the security model. Read them as a trio.
5. **§9 observation mode** — critical for early-instance safety.
6. **§6 xlsx round-trip** — the one projection worth its own diagram because the round-trip mechanics are non-obvious.
7. **§8 pack installation** — read when Claude Code reaches PHASE 11 (packs) or PHASE 15 (registry).
8. **§10 bootstrap wizard** — read when Claude Code reaches PHASE 13.

The diagrams are reference material, not a substitute for the prose. Whenever a diagram simplifies something consequential, the detail is in BUILD.md (referenced at the top of each section). Don't trust a diagram to capture every edge case — trust it to give you the shape.
