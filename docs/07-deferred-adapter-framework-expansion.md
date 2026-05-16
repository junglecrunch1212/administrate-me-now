# Deferred — expanded 5-base-class adapter framework

**Status:** DEFERRED, 2026-05-16. Operator decision: ship simple Adapter Protocol + 5 standalone adapters as v1 (prompts 11a/b/c/d). The expanded framework described in this memo is preserved as future work, not blocked on.

**Surfaced by:** the 2026-05-16 prompt-11 drift discovery. The workspace pinned `PROMPT_SEQUENCE.md` described an expanded 5-abstract-base-class adapter framework architecture, citing amendments [D19]–[D25] from memos 04 (`docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`) and 05 (`docs/05-architecture-amendment-personal-data-layer.md`) which had landed on main. The expanded version was never pushed to main's `PROMPT_SEQUENCE.md`. Partner discovered the drift while you (operator) noticed the file looked simpler than it should.

**Why we deferred** (operator's choice, three options were on the table):
1. Halt 11a; verify which version is canon first.
2. Treat workspace as aspirational; main is canon.
3. **Accept current 11a as v1; expanded framework comes later.** ← chosen.

The simple `Adapter` Protocol shipped in 11a c1 (PR #91, merged) is structurally compatible with the expanded framework: the 5 base classes would EXTEND the simple protocol, not replace it. So the c1 work isn't wasted.

---

## What the deferred framework would have looked like

Per the workspace `PROMPT_SEQUENCE.md` (and the amendment memos 04 and 05):

### Five abstract base classes (one per epistemic-role category)

- **`CommunicationAdapter`** (Cat-A) — adapters that handle bidirectional messaging across a channel. Reference impl: Gmail (central runtime; OAuth 2 service account + history API). Also covers Telegram / Discord plugin-runtime variants in prompt 12.
- **`ExternalStateMirrorAdapter`** (Cat-B) — adapters that mirror external state (lists, calendars, tasks) bidirectionally into AdministrateMe projections. Reference impls per memo §5/§6: Apple Reminders **dual-deployment** (central on assistant Apple ID + bridge variant per member Apple ID per [D18]), Apple Calendar **dual-deployment** (per [D22]), Google Calendar (central, OAuth 2 + watch channels), Google Tasks (central, REST + OAuth 2). Emits `list.*` / `list_item.*` (Reminders, Tasks, Notes-checklists) and `calendar_event.*` (Calendars).
- **`InboundDataAdapter`** (Cat-C) — adapters that ingest external data into the event log. Reference impl: Plaid (central, cursor-based `/transactions/sync` + webhook handler). Also covers Home Assistant state observation (`ha.state_changed@v1`).
- **`PersonalKnowledgeAdapter`** (Cat-D) — adapters that ingest per-member knowledge (notes, voice notes, Obsidian vaults) into the `member_knowledge` projection. Reference impls: Apple Notes (prose half — emits `note.*`), Voice Notes (emits `voice_note.*`), Obsidian (opt-in vault).
- **`OutboundActionAdapter`** (Cat-E) — adapters that consume `action.requested` events and execute external side effects. Reference impl: Home Assistant service-call seam (`ha.service_call_requested@v1` → POST `/api/services/<domain>/<service>` → `action.executed@v1` or `action.failed@v1`). `observation_mode_required: true`; wrapped through `adminme/lib/observation.py` per `[§6.20]`.

### Manifest format extensions

Per [D19] / [D20] / [PM-33] – [PM-35]:

- `capabilities` as a list (not a single field), so an adapter can declare multiple cross-category capabilities (e.g., Apple Notes is both Cat-D and Cat-B).
- `write_capabilities` separate from read capabilities; install-time validator checks method-presence + signature.
- `sensitivity_default` (defaults `normal`; `privileged` cannot be lowered by member-config per `[§6 invariant 10]`).
- `observation_mode_required` (boolean; outbound-affecting adapters set `true`).
- `owner_scope_overridability` (whether a member can override the default scope).
- `sharing_model` discriminator (`per_member`, `household_shared`, `cohort_of_one`, etc.).
- `kind` (Cat-A through Cat-E) and `runtime` (central, bridge, dual, plugin).

### Install-time validator

Catches at install/registration time:

- Capability-runtime coherence (a bridge-runtime adapter can't declare Cat-A messaging capabilities — those belong to central or plugin runtimes).
- Event-schema registration (every emitted event type has a registered schema).
- Projection-subscription compatibility (no Cat-B adapter writes to a projection it doesn't depend on).
- Base-class conformance (the adapter's class inherits from one of the 5 base classes).
- `write_capabilities` method-presence + signature check.

### `adminme adapters` CLI subcommand group

Per [D20]:

- `adminme adapters list` — lists installed adapters by category + runtime + capabilities.
- `adminme adapters validate <pack>` — runs the install-time validator standalone.
- `adminme adapters scaffold <kind>` — developer-mode-gated user-authored adapter scaffolding (three-layer developer-mode gate per [D20]).

### Authoring guide

`docs/adapter-authoring-guide.md` — operator/developer-facing tutorial for writing a new adapter pack.

### Sub-prompt structure (deferred)

The workspace `PROMPT_SEQUENCE.md` decomposed this as:

- **11** — framework expansion (5 base classes + manifest + validator + CLI + authoring guide). Tier: introduction. 5-6 hrs.
- **11a** — Gmail (Cat-A) + Plaid (Cat-C). 3-4 hrs.
- **11b** — Cat-B reference impls (Apple Reminders dual + Apple Calendar dual + Google Calendar + Google Tasks). 5-7 hrs.
- **11c-i** — Bridge daemon + central `:3337 bridge` ingest. 3-4 hrs.
- **11c-ii** — Personal-knowledge adapters (Cat-D) + Apple Notes-checklists B-half. 4-5 hrs.
- **11d** — `member_knowledge` projection (12th projection). 3-4 hrs.
- **11e** — Contacts adapters (Apple + Google) per [D23]. 3-4 hrs.
- **11f** — `lists` projection (13th projection) per [D18]. 3-4 hrs.
- **11g** — Home Assistant adapter (Cat-C + Cat-E multi-capability) per [D24]. 3-4 hrs.

Total: ~33–43 hours across 9 sub-prompts. Compared to the v1 simple-Adapter approach (4 sub-prompts, ~12-16 hours).

---

## What's shipped vs deferred

| Component | v1 (shipped/in-flight) | Deferred to expanded framework |
|---|---|---|
| Simple `Adapter` Protocol with `name`/`family`/`version`/`sensitivity_floor`/`start`/`stop`/`status` | ✓ shipped (PR #91 c1) | extends to 5 base classes |
| 3 cross-cutting schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) | ✓ shipped (PR #91 c1) | unchanged; 5-base-class framework reuses these |
| Adapter supervisor (`adminme/daemons/adapter_supervisor.py`) | ✓ shipped (PR #91 c1) | unchanged; supervisor is base-class-agnostic |
| Gmail adapter | in flight (11a c2 / PR pending) | becomes a `CommunicationAdapter` (Cat-A) reference impl |
| Plaid adapter | future 11b | becomes an `InboundDataAdapter` (Cat-C) reference impl |
| Apple Reminders | future 11c (simple v1) | dual-deployment per [D18]; `ExternalStateMirrorAdapter` (Cat-B) |
| Google Calendar + CalDAV | future 11d (simple v1) | `ExternalStateMirrorAdapter`; CalDAV removed per [D25] in expanded version |
| `member_knowledge` projection | not in v1 scope | needed for Cat-D adapters |
| `lists` projection | not in v1 scope | needed for Cat-B lists adapters |
| Home Assistant adapter | not in v1 scope | Cat-C state + Cat-E action seams |
| `adminme adapters` CLI | not in v1 scope | per [D20] |
| Authoring guide | not in v1 scope | per [D19] |

---

## When to revisit

Recommended trigger points to consider returning to the expanded framework:

1. **After 11d ships** — once the four simple adapters are merged, evaluate whether the simple Adapter Protocol has accumulated enough domain-specific extensions that a 5-base-class refactor would consolidate them cleanly.
2. **Before adding contacts / lists / member-knowledge** — these adapters were the original motivation for Cat-B / Cat-D categories. If they're scheduled for Phase A, they likely want the typed base classes from the start.
3. **Before prompt 12** (OpenClaw plugin adapters) — plugin runtime is its own category in the expanded framework. If prompt 12's draft already implies a typed plugin-adapter base, the framework expansion is a natural pre-requisite.
4. **Before Phase B deploys** — if the adapter-pack discovery surface needs the `adminme adapters` CLI for installation, the framework expansion has to ship first.

---

## What to do if you decide to revisit

1. Re-read this memo + memos 04 / 05 / the workspace `PROMPT_SEQUENCE.md` for the design context.
2. Treat the expanded `PROMPT_SEQUENCE.md` table rows (workspace lines 115-123) as the authoritative scope target.
3. Open an architecture-amendment cycle if any of the Cat-A through Cat-E definitions need refinement.
4. The simple `Adapter` Protocol already on main becomes the parent of the 5 base classes — no breaking change to v1 adapters.
5. Per `cowork-partner/PROJECT-INSTRUCTIONS.md` (post PR #97 merge), the amendment-cascade rule applies: pushing the expanded sequence-file rows to main requires touching `prompts/PROMPT_SEQUENCE.md` in the same PR, which is the natural place anyway.

---

## Constitutional citations preserved here for future reference

- [D17] proactive pipelines do not write projections directly
- [D18] Apple Reminders dual-deployment + `list.*` event family
- [D19] adapter framework (5 base classes + manifest extensions)
- [D20] `adminme adapters scaffold` developer-mode gate
- [D22] Apple Calendar dual-deployment per Amendment-2
- [D23] Contacts adapters (Apple + Google) per Amendment-2
- [D24] Home Assistant multi-capability adapter
- [D25] CalDAV removal (Apple Calendar covers iCloud; Google Calendar covers Google)
- Amendment-2 memo: `docs/05-architecture-amendment-personal-data-layer.md`
- Knowledge vaults / member bridges memo: `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`
- `BUILD.md` sections: §MEMBER BRIDGES (bridge runtime), §L1-continued (adapter framework expansion)
