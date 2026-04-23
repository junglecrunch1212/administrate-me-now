# AdministrateMe decisions log

_This file is read by Claude Code (or its successor) alongside `SYSTEM_INVARIANTS.md` before every subsequent prompt. Decisions here are as binding as invariants. When you read this, treat each CONFIRMED decision as a constraint. If a prompt asks you to violate one, stop and report. If you believe a decision is wrong, propose a change in a new entry; do not silently work around it._

Format: each decision is numbered, states the resolution of a specific ambiguity, cites where the ambiguity came from, and records the date decided. Decisions are append-only — superseding decisions create new numbered entries that reference the old one.

---

## D1 — Standing orders registration path

**Decided:** 2026-04-23. **Status:** CONFIRMED. **Resolves:** SYSTEM_INVARIANTS.md §16 item 1; architecture-summary.md §11 item 1.

Proactive pipelines register with OpenClaw via workspace-prose `AGENTS.md` entries paired with `openclaw cron add` invocations. There is no programmatic plugin-hook registration path — the OpenClaw docs do not expose one.

**Corollary:** AdministrateMe ships a `bootstrap/openclaw/programs/` directory containing one markdown file per standing order (scope / triggers / approval gate / escalation / execution steps / what-NOT-to-do). Bootstrap §8 concatenates these into `~/Chief/AGENTS.md` and runs `openclaw cron add` per a sidecar cron spec (`bootstrap/openclaw/cron.yaml`). The markdown files are version-controlled; the concatenated AGENTS.md is a generated artifact, not hand-edited.

## D2 — Console fan-out architecture

**Decided:** 2026-04-23. **Status:** REJECTS proposed §16 item 2 as stated; replaces with stricter formulation.

The Node console has **exactly one** event subscription to the Python event bus — via the HTTP bridge's SSE endpoint on Core `:3333/api/core/events/stream`. Internal console fan-out (chat stream proxy, reward toast dispatch, degraded-mode banner, general event notifications) is a separate concern handled by a `ClientFanOut` class that multiplexes that one upstream subscription to many browser tabs.

Two classes, two responsibilities: `UpstreamBusSubscriber` (one) and `ClientFanOut` (many). "One unified Bus" is explicitly rejected — that shape collapses into a god-object.

## D3 — View mode contract

**Decided:** 2026-04-23. **Status:** CONFIRMED with sharpening. **Resolves:** SYSTEM_INVARIANTS.md §16 item 3; architecture-summary.md §11 item 3.

Each profile pack exports a default React functional component accepting `{member, persona, data, api}` (typed via `@adminme/profile-types`) and renders the full surface for that member. "View mode" (`carousel` / `compressed` / `child`) is descriptive vocabulary for the three reference profile implementations — it is **not** an enum the shell dispatches on, and **not** a parameter any backend endpoint reads. Future profile authors can invent new visual treatments without touching the shell.

## D4 — CRM ownership

**Decided:** 2026-04-23. **Status:** REJECTS proposed §16 item 4 as stated; replaces with the correct decomposition. **Resolves:** architecture-summary.md §11 item 4.

The CRM (parties + identifiers + memberships + relationships + interactions + commitments + artifacts) is a **shared L3 projection concern, not a product concern**. Any Python product may read these projections directly via its local `Session` connection. Mutations flow through events, never through cross-product HTTP calls — e.g. when Core's `closeness_scoring` pipeline recomputes a tier, it emits `party.tier.recomputed`, not `POST /api/capture/parties/<id>`.

The Capture product owns the **human-facing CRM surfaces** (`/api/capture/parties`, the CRM view, triage queue). It does **not** own CRM data. Core, Comms, and Automation are peers of Capture for CRM reads.

This is the mental model: **products own surfaces, projections own data, events move state.**

## D5 — xlsx behavior during observation mode

**Decided:** 2026-04-23. **Status:** CONFIRMED with addition. **Resolves:** SYSTEM_INVARIANTS.md §16 item 5; architecture-summary.md §11 item 5.

The forward xlsx projector runs **unconditionally** regardless of observation mode, because the workbook is a purely local artifact and observation mode gates external side effects only.

**Addition:** Every forward write emits `xlsx.regenerated` with `observation_mode_active: true|false` on the payload. The observation-review pane queries these events so the tenant can see which workbook writes happened during the observation period.

## D6 — openclaw-memory-bridge event emission

**Decided:** 2026-04-23. **Status:** CONFIRMED with specificity. **Resolves:** SYSTEM_INVARIANTS.md §16 item 6; architecture-summary.md §11 item 6.

The `openclaw-memory-bridge` plugin emits events into AdministrateMe via `POST http://127.0.0.1:3334/api/comms/ingest/conversation-turn` (Comms product). The endpoint is loopback-only, authenticated with a shared secret written to `~/.adminme/config/plugin-secrets.yaml.enc` during bootstrap §8. The plugin does **not** open a SQLCipher connection and does **not** hold the AdministrateMe master key.

Rationale: keeps key material inside AdministrateMe; keeps the plugin thin; gives one well-defined ingest endpoint for future similar plugins.

## D7 — schema_version semantics and upcasters

**Decided:** 2026-04-23. **Status:** CONFIRMED with teeth. **Resolves:** SYSTEM_INVARIANTS.md §16 item 7.

`schema_version` is a **monotonically increasing integer per `event_type`** (not semver, not a string). Upcasters are pure functions named `upcast_v{N}_to_v{N+1}(payload: dict) -> dict` registered in `adminme/lib/event_types/<namespace>/<type>/upcasters.py`. Reading an old event composes upcasters in order up to the current version. Downcasting is explicitly not supported — the system only moves forward.

## D8 — correlation_id and causation_id discipline

**Decided:** 2026-04-23. **Status:** CONFIRMED with two additions. **Resolves:** SYSTEM_INVARIANTS.md §16 item 8.

Base rule: the originating adapter or L5 surface sets `correlation_id`; it is preserved unchanged through every derived event. `causation_id` is set to the event_id of the immediate parent. Neither is ever overwritten downstream.

**Addition 1:** Every adapter and every L5 surface endpoint generates a `correlation_id` on entry if the inbound request does not carry one. The originating event always has a correlation_id.

**Addition 2:** Static analysis rule — every `EventStore.append()` call site must pass both `correlation_id` and `causation_id` as explicit keyword arguments. Passing `None` is allowed when genuinely unknown; defaulting them via function signature is not. A lint rule (ruff custom check or grep-based test) enforces this.

---

## Additional decisions not from SYSTEM_INVARIANTS.md §16

### D9 — Hearth relationship

**Decided:** 2026-04-23. **Status:** CONFIRMED.

For v1, "Hearth" refers to a **conceptual base layer**, not a separate installable package. The `hearth.event_types` entry point is vocabulary for "core event types that live in the AdministrateMe repo under `adminme/lib/event_types/hearth/`," distinguishing them from AdministrateMe-specific types under `adminme/lib/event_types/adminme/`. There is no `pip install hearth-core`; there is no separate repo.

If Hearth is ever extracted to a separate package (post-v1), that extraction happens via a dedicated phase prompt with a migration plan; it does not happen opportunistically.

### D10 — Platform version lock

**Decided:** 2026-04-23. **Status:** CONFIRMED.

Platform versions for v1: **Python 3.11**, **Node 22 LTS**. These match what prompt 00's sandbox-downgrade established. Version upgrades happen via a dedicated phase prompt with a migration plan; they do not happen opportunistically, and no prompt adds a dependency that requires a newer minimum version without the upgrade prompt having run first.

### D11 — Skill pack directory shape

**Decided:** 2026-04-23. **Status:** CONFIRMED.

Every skill pack has exactly this shape:

```
~/.adminme/packs/skills/<namespace>/<name>/
├── SKILL.md                   # OpenClaw-format frontmatter + prompt body
├── input.schema.json          # AdministrateMe convention; wrapper enforces
├── output.schema.json         # AdministrateMe convention; wrapper enforces
├── handler.py                 # optional: Python post-processing
├── tests/
│   ├── fixtures/*.json        # input/output pairs for replay tests
│   └── test_skill.py          # contract test: schema compliance + one happy path per fixture
```

No variations. A skill pack that adds or omits files outside this contract fails `adminme pack install` at the validate-manifest stage.

### D12 — Documentation mirror versioning

**Decided:** 2026-04-23. **Status:** CONFIRMED (mechanism below).

`docs/reference/` contents are version-pinned via `docs/reference/_manifest.yaml`, which records the git commit SHA of every upstream source mirrored by prompt 00.5. When a referenced upstream doc changes in a way that affects an AdministrateMe invariant, a dedicated phase prompt updates the mirror AND the affected invariants together.

Without this pin, "per OpenClaw docs" becomes "per whatever version of the OpenClaw docs was in the mirror the day the code was written" — unacceptable for a 10-year system.

---

## How to use this file

- **Claude Code / future AI agents:** read this file before every prompt alongside `SYSTEM_INVARIANTS.md`. Treat CONFIRMED decisions as constraints. Do not silently violate them. If a new prompt requires revisiting a decision, the prompt itself must say so explicitly and add a superseding entry here.
- **Operator (human):** when a new ambiguity surfaces mid-build, add a numbered decision here before the prompt that depends on it runs. Decisions are cheap; undoing wrong decisions embedded in code is expensive.
- **Superseding a decision:** add a new numbered entry with `**Supersedes:** D<N>` at the top and explain the change. Do not edit historical entries except to add a `**Superseded by:** D<N>` note at the bottom.
