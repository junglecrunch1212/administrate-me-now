# Prompt 11: Adapter framework expansion

**Phase:** Phase A — L1 framework introduction (the pattern that all reference-implementation prompts 11a / 11b / 11c-i / 11e / 11g flow from).

**Depends on:** Prompt 10c-iii. The adapter framework introduces machinery (5 ABCs + manifest extensions + validator + CLI) that the existing simple `Adapter` Protocol from prompt 02 / 11a c1 extends rather than replaces.

**Estimated duration:** 5-6 hours (split — see split-memo footnote). **Pre-split candidate** per `D-prompt-tier-and-pattern-index.md`: five base classes + manifest spec + validator + CLI + authoring guide is too much for a single Claude Code session.

**Stop condition:** Adapter framework in place. Subsequent reference-implementation prompts (11a Gmail+Plaid, 11b Cat-B cohort, 11c-i bridge daemon, 11e Contacts, 11g Home Assistant) can be authored against typed contracts. Existing shipped adapters (Gmail, Plaid, Reminders — pre-canon work salvaged per memo 08) get retrofitted into the framework via Cohort C retrofit prompts.

---

## Read first

1. `docs/05-architecture-amendment-personal-data-layer.md` — full read (122 KB; section navigation, not full-page-read). Especially:
   - §1 (five-category adapter taxonomy + Cat-A through Cat-E definitions)
   - §2 (two-axis category × runtime model; capabilities-as-list per [PM-35]; asymmetric write-capabilities per UT-27; sensitivity_default + observation_mode_required as orthogonal manifest fields; sharing-model discriminator; owner-scope-overridability)
   - §3.1 (manifest format extensions)
   - §3.2 (five abstract base classes — `CommunicationAdapter`, `ExternalStateMirrorAdapter`, `InboundDataAdapter`, `PersonalKnowledgeAdapter`, `OutboundActionAdapter`)
   - §3.3 (discovery and registration CLI — `adminme adapters` subcommand group)
   - §3.4 (install-time validation extensions)
2. `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md` — full read (44 KB). Establishes Cat-D personal-knowledge adapters + bridge runtime + owner-scope-from-Tailscale-identity. The PersonalKnowledgeAdapter ABC's contract depends on the bridge model.
3. `docs/DECISIONS.md` — read [D17] through [D25] entries. The constitutional record for the five-category model + dual-deployment + multi-capability adapters.
4. `docs/08-architecture-amendment-adapter-rebase.md` — full read. Explains what is on main pre-framework (the simple `Adapter` Protocol) and what gets retrofitted in Cohort C.
5. `ADMINISTRATEME_BUILD.md` — sections:
   - "L1: ADAPTERS" — the two-runtime distinction (plugin vs. standalone) before Amendment-2
   - "L1-continued" (post-Amendment-2 expansion — capability-driven contracts)
   - "PACK REGISTRY" — the six pack kinds + seven-stage install lifecycle that the framework's manifest extensions plug into
6. `adminme/adapters/base.py` (on main) — the simple `Adapter` Protocol shipped via prompt 11a c1 (PR #91). The five new ABCs in `adminme/lib/adapters/` extend this Protocol; they do not replace it.
7. `adminme/events/schemas/adapters.py` (on main) — the three cross-cutting schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) at v1. Framework-level; no change needed.
8. `adminme/daemons/adapter_supervisor.py` (on main) — the supervisor. Will evolve in Cohort B's validator commit to handle capability-driven adapter discovery; the core spawn-and-restart logic carries forward unchanged.

## Operating context

Per Amendment-2 ([D19]–[D25] + [PM-33]–[PM-35]), L1 adapters are reorganized along an **epistemic-role taxonomy** with **runtime as an orthogonal secondary axis** (central / bridge / dual-deployment / plugin). Each adapter declares a list of **capabilities**, each capability having its own category. The framework's role is to provide:

- A typed base class per category, so adapter authors fill in a `start()` / `stop()` / `status()` shape that matches their category's contract (Cat-A vs Cat-B vs Cat-C vs Cat-D vs Cat-E).
- Manifest format extensions that capture the per-capability declarations (kind, runtime, capabilities-list, sensitivity_default, observation_mode_required, owner_scope_overridability, sharing_model).
- An install-time validator that catches mismatches between declared capabilities and the runtime / projection-subscription / base-class shape.
- A CLI subcommand group (`adminme adapters list / validate / scaffold`) for adapter discovery + developer-mode-gated scaffolding.
- An authoring guide explaining the framework to operator-developers writing new adapter packs.

The framework is **introduction-tier** per `D-prompt-tier-and-pattern-index.md`: the pattern landed here is reused by every subsequent reference-implementation prompt and every future community-pack adapter. Quality bar is correspondingly high.

## Objective

Build the framework that all subsequent L1 adapter work flows from: five abstract base classes in `adminme/lib/adapters/`, manifest format extensions in the pack-registry layer, install-time validator extensions, `adminme adapters` CLI subcommand group, and the authoring guide at `docs/adapter-authoring-guide.md`.

## Out of scope

- **Reference implementations** — Gmail (Cat-A), Plaid (Cat-C), Apple Reminders dual (Cat-B), Apple Calendar dual (Cat-B), Google Calendar (Cat-B), Google Tasks (Cat-B), bridge daemon + ingest endpoint, Apple Notes / Voice Notes / Obsidian (Cat-D), Apple Notes-checklists writeback (Cat-B + Cat-D multi-capability), Apple Contacts (Cat-B), Google Contacts (Cat-B), Home Assistant (Cat-C + Cat-E multi-capability). These land in prompts 11a / 11b / 11c-i / 11c-ii / 11d (projection) / 11e / 11g.
- **Retrofit of shipped pre-canon adapters** (Gmail, Plaid, Reminders) — Cohort C per memo 08 §3.3.
- **`member_knowledge` projection** — 11d (per canon).
- **`lists` projection** — 11f.
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12 (the Cat-A plugin runtime variants).
- **Live external API integration** — this prompt builds contracts, not adapter implementations. Reference implementations integrate live.

## Deliverables

### Five abstract base classes

Per `docs/05-architecture-amendment-personal-data-layer.md` §3.2 + [PM-34]. Each lives in `adminme/lib/adapters/`:

- `communication_base.py` — `CommunicationAdapter` ABC. Contract: bidirectional messaging. Required methods: `start(ctx)`, `stop()`, `status()`, `send(message)` (outbound; observation-mode-suppressed via [§6.20]), `_on_inbound(envelope)` (hook). Class attributes: `name`, `family: Literal["messaging"]`, `version`, `capabilities` (list with at least one Cat-A capability).
- `external_state_mirror_base.py` — `ExternalStateMirrorAdapter` ABC. Contract: mirror external state into projections bidirectionally. Required methods: `start(ctx)`, `stop()`, `status()`, `apply_write(operation)` (consumes outbound writes; gated by `write_capabilities`), `_on_external_change(envelope)` (hook for inbound). Class attributes: `name`, `family: Literal["reminders","calendaring","contacts"]`, `version`, `capabilities`, `write_capabilities` (list of per-operation write-capability strings per UT-27).
- `inbound_data_base.py` — `InboundDataAdapter` ABC. Contract: read-only ingestion. Required methods: `start(ctx)`, `stop()`, `status()`. No write method (Cat-C is inbound-only by definition; an adapter that writes is Cat-B or Cat-E).
- `personal_knowledge_base.py` — `PersonalKnowledgeAdapter` ABC. Contract: per-member knowledge ingestion via bridge runtime. Required methods: `start(ctx)`, `stop()`, `status()`. Class attributes include `runtime: Literal["bridge"]` (Cat-D is bridge-only by default per memo 04). Cross-capability extension (Cat-D + Cat-B for Notes-checklists) declared via `capabilities` list per [PM-35].
- `outbound_action_base.py` — `OutboundActionAdapter` ABC. Contract: consume `action.requested` events; execute external side effects; emit `action.executed` or `action.failed`. Required methods: `start(ctx)`, `stop()`, `status()`, `execute_action(request)`. Observation-mode-required by default (`observation_mode_required: true`).

Each ABC wraps the simple `Adapter` Protocol from `adminme/adapters/base.py` — the Protocol stays as the lowest common contract; the ABCs add category-specific shape.

### Manifest format extensions

Per `docs/05-architecture-amendment-personal-data-layer.md` §3.1 + [PM-33]. Extend `bootstrap/pack_registry/` (or wherever pack-load logic lives on main — verify at prompt-authoring time) with:

- `capabilities` field as a list of capability blocks, each declaring `kind: "Cat-A" | "Cat-B" | "Cat-C" | "Cat-D" | "Cat-E"`, `runtime: "central" | "bridge" | "dual" | "plugin"`, `event_families` (list of event-type prefixes the capability emits or consumes), `sensitivity_default`, `observation_mode_required`.
- `write_capabilities` field (top-level) listing per-operation write capabilities per UT-27. Cat-B and Cat-E adapters use this; Cat-A / Cat-C / Cat-D ignore it.
- `sensitivity_default` field — `"normal" | "sensitive" | "privileged"`, defaulting to category-typical per §3.1.
- `observation_mode_required` — boolean.
- `owner_scope_overridability_at_install` — boolean.
- `sharing_model_discriminator` — string naming the row-level discriminator column for Cat-B projections.
- `dedup_strategy` — Cat-B-typical per [PM-31]; declares how the upstream entity's `(kind, external_id)` deduplicates across deployment variants.

### Install-time validator extensions

Per `docs/05-architecture-amendment-personal-data-layer.md` §3.4 + [PM-34]. Extend the pack-install-validate stage (stage 1 of `DIAGRAMS.md` §8) with:

- **Capability-runtime coherence** — a bridge-runtime adapter can't declare Cat-A messaging capabilities (those belong to central or plugin runtimes); Cat-D + bridge + `owner_scope_overridability_at_install: true` is a hard refusal.
- **Event-schema registration** — every event family declared in `capabilities[].event_families` has a registered schema in `adminme/events/schemas/`.
- **Projection-subscription compatibility** — Cat-B / Cat-D adapters declare event families that at least one projection subscribes to.
- **Base-class conformance** — the adapter's class inherits from one of the five ABCs; the category in the inheritance matches the manifest's category declarations.
- **Write-capabilities method-presence + signature check** — for each entry in `write_capabilities`, the adapter class exposes a corresponding `apply_<capability_name>` method with the canonical signature.

### `adminme adapters` CLI subcommand group

Per `docs/05-architecture-amendment-personal-data-layer.md` §3.3 + [D20]:

- `adminme adapters list` — lists installed adapters by category + runtime + capabilities. Default human-readable; `--json` flag for machine-readable.
- `adminme adapters validate <pack-path>` — runs the install-time validator standalone against a pack directory without installing.
- `adminme adapters scaffold <kind> <runtime>` — developer-mode-gated user-authored adapter scaffolding under the three-layer developer-mode gate per [D20] (`ADMINME_DEVELOPER_MODE=1` env + `--allow-scaffold` flag + interactive confirmation). Generates a starter pack at `packs/adapters/<kind>/<name>/` with `pack.yaml`, `adapter.py` (skeleton inheriting from the appropriate ABC), `config.schema.json` (template), `tests/test_<name>_adapter_pack_internal.py` (F-3 rename pattern).

### Authoring guide

`docs/adapter-authoring-guide.md` — operator/developer-facing tutorial. Sections:

1. Picking your category (decision tree: Cat-A through Cat-E).
2. Filling in the manifest (per-category templates + capability examples).
3. Implementing the ABC (per-category code skeletons; observation-mode hooks).
4. Testing (pack-internal pack-load canary + handler-direct tests + integration test pattern).
5. Multi-capability adapters (when to declare multiple capabilities; how the validator handles them).
6. Dual-deployment adapters (central + bridge configs; sharing-model discriminator).
7. Common pitfalls (no LLM SDK imports under `packs/adapters/`; no projection writes from adapters; correlation_id discipline).

### Tests

- Unit tests for each ABC: instantiate a fake subclass, verify the Protocol contract holds.
- Unit tests for the manifest extensions: round-trip serialize + validate; reject malformed manifests (missing required fields; unknown category; capability-runtime mismatch).
- Unit tests for each validator check: capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write-capabilities method-presence.
- Unit tests for `adminme adapters` CLI: `list` returns installed adapters; `validate` catches each known-bad manifest shape; `scaffold` writes the expected file tree under developer-mode-gated invocation.
- Integration test: install a synthetic multi-capability adapter pack (Cat-D + Cat-B, mirroring the Apple Notes-checklists shape from prompt 11c-ii); verify the supervisor spawns it; verify both capabilities' event-families register.

Floor: ≥40 new tests across the framework prompt (5 ABCs + 8 manifest fields + 5 validator checks + 3 CLI subcommands → roughly 20 contract tests + 20 validator tests).

## Verification

```bash
poetry run pytest adminme/lib/adapters/ tests/unit/adapters/ tests/integration/test_adapter_framework_*.py -v
poetry run pytest -v
poetry run mypy adminme/ 2>&1 | tail -10
poetry run ruff check adminme/ 2>&1 | tail -10
bash scripts/verify_invariants.sh

# CLI smoke
poetry run adminme adapters list
poetry run adminme adapters validate packs/adapters/messaging/gmail/

git commit -m "phase 11: adapter framework expansion"
```

## Stop

> Adapter framework in place. Five ABCs at `adminme/lib/adapters/`. Manifest extensions in pack registry. Install-time validator extensions. `adminme adapters` CLI subcommand group. Authoring guide at `docs/adapter-authoring-guide.md`. Ready for: Cohort C retrofit prompts (Gmail, Plaid, Reminders → typed bases) AND Cohort D reference implementations (11a Gmail+Plaid, 11b Cat-B cohort, 11c-i bridge daemon, 11c-ii Cat-D + Notes-checklists, 11d projection, 11e Contacts, 11f lists projection, 11g Home Assistant).

---

## Split-memo footnote

This prompt is a **pre-split candidate** per `D-prompt-tier-and-pattern-index.md` + PROMPT_SEQUENCE.md row 115. The framework's five deliverables (5 ABCs / manifest extensions / validator / CLI / authoring guide) plausibly decompose into three sub-prompts:

- **11-α** — ABCs + manifest extensions (the contract layer everything downstream depends on). Roughly 2-3 hrs.
- **11-β** — Install-time validator extensions (depends on the manifest contract). Roughly 2-3 hrs.
- **11-γ** — `adminme adapters` CLI subcommand group + authoring guide (depends on the validator). Roughly 1-2 hrs.

The split memo is the next Cohort B PR after this parent draft merges. It will live at `docs/09-split-memo-11-framework.md` (number 09 because 08 is already taken by the rebase memo).
