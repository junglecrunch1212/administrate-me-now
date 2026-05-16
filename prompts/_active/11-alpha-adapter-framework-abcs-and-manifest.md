# Prompt 11-α: Adapter framework — 5 ABCs + manifest extensions

**Phase:** Phase A — adapter framework expansion (first sub-prompt of the prompt-11 split per `docs/09-split-memo-11-framework.md`).

**Branch:** `claude/11-alpha-<auto>` (harness-assigned). PR base = `main` for all four c-bands per PR #106 sidecar. STACKED across c1→c4 per §8.5.

**Depends on:** Prompt 10c-iii merged to main. The simple `Adapter` Protocol from prompt 11a c1 (PR #91, on main) is the lowest common contract; the 5 ABCs introduced here EXTEND it, do not replace it. Cohort C retrofit prompts will subsequently bring shipped Gmail / Plaid / Reminders adapters under the appropriate ABC.

**Quality bar:** introduction-tier prompt per `D-prompt-tier-and-pattern-index.md`. The contracts shipped here are reused by every subsequent reference-implementation prompt (11-β validator, 11-γ CLI, then 11a/b/c-i/c-ii/d/e/f/g per the resumed canon). Quality bar correspondingly high — typed contracts must be coherent, well-documented, and lay a clean foundation for the install-time validator.

**Stop condition:** Five abstract base classes at `adminme/lib/adapters/` ({communication,external_state_mirror,inbound_data,personal_knowledge,outbound_action}_base.py + `__init__.py` re-export). Manifest format extensions at `adminme/lib/pack_registry/` (NEW sibling to `adminme/lib/skill_runner/` where the existing skill-pack loader lives — parallel structure, not a fork). `bootstrap/pack_install_order.yaml` unchanged (no new install ordering needed — ABCs are imported by adapter packs at adapter-class definition time, not at install time). ≥60 new tests across the four commits. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~80-100 minutes wall clock, 4 commits. ~$80-120 API spend per split memo §2.

---

## §1 Behavioral grounding

You are building the **contract layer** of the adapter framework. Per memo 05 §3 + [D19] / [PM-34], the framework's job is to give adapter authors a typed base class per epistemic-role category, plus manifest-format extensions that capture per-capability declarations. The actual install-time validator that checks these contracts ships in 11-β; the CLI + authoring guide that wraps the validator ships in 11-γ. This prompt builds what 11-β and 11-γ depend on.

**Five categories, five ABCs** [memo 05 §3.2, [PM-34]]. Each base class lives in `adminme/lib/adapters/` and extends the simple `Adapter` Protocol from `adminme/adapters/base.py` (shipped via 11a c1, PR #91). The Protocol is the lowest common contract — `name`, `family`, `version`, `sensitivity_floor`, `start(ctx)`, `stop()`, `status()`. The ABCs add category-specific shape:

- **`CommunicationAdapter`** (Cat-A) — bidirectional messaging. Adds `send(message)` (outbound, observation-mode-suppressed via `adminme/lib/observation.py` per [§6.20]) and `_on_inbound(envelope)` hook. Required class attributes: `capabilities` list with at least one Cat-A entry.
- **`ExternalStateMirrorAdapter`** (Cat-B) — bidirectional mirror of external state into projections. Adds `apply_write(operation)` (gated by `write_capabilities`) and `_on_external_change(envelope)` hook. Required class attributes: `write_capabilities` (list of per-operation capability strings per UT-27); `sharing_model_discriminator` (string naming the row-level discriminator column).
- **`InboundDataAdapter`** (Cat-C) — read-only ingestion. No write method (an adapter that writes is Cat-B or Cat-E, not Cat-C). Simplest base class.
- **`PersonalKnowledgeAdapter`** (Cat-D) — per-member knowledge via bridge runtime. Required class attribute: `runtime: Literal["bridge"]`. The Tailscale-identity-binds-owner-scope rule from memo 04 §2.2 is enforced via this base class (owner_scope is stamped by the bridge-ingest endpoint, not by the adapter — the base class makes this contract explicit via a `final` decorator on the owner_scope setter).
- **`OutboundActionAdapter`** (Cat-E) — consume `action.requested` events; execute external side effects. Adds `execute_action(request)`. Required class attribute: `observation_mode_required = True` (default). Wrapped through `adminme/lib/observation.py` for observation-mode suppression.

Each ABC inherits from `typing.Protocol` (runtime-checkable) and uses `abc.abstractmethod` for required methods. The intersection of "Protocol with abstractmethods" is intentional: adapters can be duck-typed (Protocol) at the type-checker level AND get explicit abstract-method enforcement at runtime via `abc.ABC`. The 5 ABCs use `abc.ABCMeta` + Protocol pattern documented in memo 05 §3.2.

**Manifest format extensions** [memo 05 §3.1, [PM-33] / [PM-35]]. The current pack manifest (`pack.yaml`) has flat fields like `kind`, `family`, `version`, `triggers`, `events_emitted`, `depends_on`. The framework extends it with:

- `capabilities` — a LIST of capability blocks, each with `kind` (Cat-A through Cat-E), `runtime` (central / bridge / dual / plugin), `event_families` (event-type prefixes), `sensitivity_default` (per-capability override), `observation_mode_required` (per-capability override). Multi-capability adapters declare multiple blocks per [PM-35].
- `write_capabilities` — flat list of per-operation write-capability strings (e.g., `["create_list", "delete_item", "toggle_completion"]` for a Cat-B list adapter per UT-27). Cat-A / Cat-C / Cat-D ignore this; Cat-B / Cat-E require it.
- `sensitivity_default` — `"normal" | "sensitive" | "privileged"`, top-level default. Per-capability override in `capabilities[].sensitivity_default` takes precedence.
- `observation_mode_required` — boolean. Top-level default; per-capability override allowed.
- `owner_scope_overridability_at_install` — boolean. `false` for Cat-D bridge adapters (memo 04 §2.2 invariant); validator enforces this in 11-β.
- `sharing_model_discriminator` — string naming the row-level discriminator column on Cat-B projections per memo 05 §2.4.
- `dedup_strategy` — Cat-B-typical per [PM-31]; declares `(kind, external_id)` shape for upstream-entity deduplication across dual-deployment variants.

The new fields are PARSED in this prompt; they are not yet ENFORCED — enforcement lands in 11-β's install-time validator. This is the key architectural separation: 11-α defines the schema; 11-β checks compliance against the schema.

**Why `adminme/lib/pack_registry/` and not `bootstrap/pack_registry/`** [Gate 2 path correction]. The existing pack-loader at `adminme/lib/skill_runner/pack_loader.py` lives under `adminme/lib/`, not `bootstrap/`. The split memo §4 said "bootstrap/pack_registry/" with the qualifier "or wherever pack-load lives on main — verify at prompt-authoring time". Pre-authorship gate confirmed the actual location is `adminme/lib/`. The framework's manifest-extension code lives at `adminme/lib/pack_registry/` as a new sibling to `skill_runner/`; the existing `adminme/lib/skill_runner/pack_loader.py` stays where it is (skill packs only; adapter packs get parallel infrastructure that the framework's loader handles).

**Anti-vision halt** [VISION §"Small enough to inspect", DRIFT-CONTROL Job 4]. Each ABC file MUST be ≤200 lines (including docstring). If the contract for any single category exceeds this, halt and surface — the discriminator between a "contract" and an "implementation" is that contracts are inspectable in one screen. Cat-A is the simplest (~120 lines); Cat-B is the most complex (~140 lines including the write_capabilities surface); none should crack 200 without a justified deviation.

**Mode:** Phase A. No live adapter instantiation; ABCs are pure contracts tested via fake subclasses. The manifest extensions are pure schema definitions tested via round-trip serialize + validate.

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md` / `memo 05` ranges. Memo 05 is 122 KB; targeted reads only.

1. **`docs/09-split-memo-11-framework.md`** — full read (~12 KB). §2 (11-α row), §3 (dependency on this prompt by 11-β + 11-γ), §4 (deliverables breakdown for 11-α).
2. **`prompts/11-adapter-framework.md`** — full read (15 KB). Parent draft; §"Deliverables" + §"Read first" specifically.
3. **`docs/05-architecture-amendment-personal-data-layer.md`** — targeted reads:
   - §1 (five-category taxonomy + each category's contract) — lines 12 to ~250
   - §2.2 (capabilities-as-list per [PM-35], multi-capability shape) — ~205-240
   - §2.3 (asymmetric write-capabilities per UT-27) — ~238-260
   - §2.4 (sensitivity_default + observation_mode_required as orthogonal fields) — ~259-285
   - §2.5 (sharing_model_discriminator on Cat-B projections)
   - §2.6 (owner_scope_overridability_at_install)
   - §3.1 (manifest format extensions) — targeted via `grep -n '^### 3.1'`
   - §3.2 (five abstract base classes) — targeted via `grep -n '^### 3.2'`
4. **`docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`** — §2.2 (Tailscale-identity-binds-owner-scope invariant — this is what `PersonalKnowledgeAdapter` enforces).
5. **`adminme/adapters/base.py`** — full read (5 KB). The simple `Adapter` Protocol from 11a c1. The 5 ABCs extend this Protocol; confirm the `name`, `family`, `version`, `sensitivity_floor`, `start`, `stop`, `status` shape before subclassing.
6. **`adminme/events/schemas/adapters.py`** — full read. The 3 cross-cutting schemas; ABCs reference these for the `_emit_authenticated` / `_emit_error` / `_emit_cursor_advanced` helper methods that subclasses inherit.
7. **`adminme/lib/observation.py`** — full read. The observation-mode suppression infrastructure that `CommunicationAdapter.send()` and `OutboundActionAdapter.execute_action()` wrap through.
8. **`adminme/lib/scope.py`** — full read. The `OwnerScope` type + helpers; `PersonalKnowledgeAdapter` uses this to surface the "Tailscale-stamps-not-adapter" invariant.
9. **`adminme/lib/skill_runner/pack_loader.py`** — full read. **Precedent for pack-load logic.** The new `adminme/lib/pack_registry/` borrows the loading pattern (yaml.safe_load + Pydantic validation) but is structurally separate (adapter packs are not skill packs).
10. **`tests/unit/test_pack_loader.py`** — full read. **Precedent for testing pack-load logic.** The new tests at `tests/unit/lib/pack_registry/` use the same fixture pattern.
11. **`packs/adapters/messaging/gmail/pack.yaml`**, **`packs/adapters/financial/plaid/pack.yaml`**, **`packs/adapters/reminders/apple-eventkit/pack.yaml`** — read each. Current pack-manifest shape; the new `capabilities`/`write_capabilities`/etc. fields will eventually replace the flat fields when Cohort C retrofit prompts run. For this prompt, the new fields are OPTIONAL additions to the manifest schema — existing flat manifests still parse cleanly.
12. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads: §1 (event log append-only), §6 invariant 10 (sensitivity floor), §6.20 (observation mode wraps outbound), §7.3 (no projection direct writes from adapters), §8 (no LLM SDKs).
13. **`docs/DECISIONS.md`** — read [D6], [D7], [D8], [D17], [D18], [D19], [D20], [D24]. Particularly [D19] for the 5-category taxonomy + [PM-34] for the 5 ABCs in `adminme/lib/adapters/`.

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad confirming: (a) each ABC extends the simple `Adapter` Protocol with category-specific methods; (b) each ABC's docstring cites the constitutional source for its contract decisions; (c) `PersonalKnowledgeAdapter`'s owner_scope-from-Tailscale invariant is enforced via type contract, not runtime check; (d) manifest extensions are PARSED here but ENFORCED in 11-β; (e) `adminme/lib/pack_registry/` is a NEW sibling, parallel to (not extending) `adminme/lib/skill_runner/`.

---

## §3 Reconciliation notes (carry-forwards from rebase + Cohort A/B step 1+2)

- **F-3 HARD (10c-iii heritage):** any new pack-internal tests use unique basenames. This prompt does NOT add pack-internal tests (it adds framework-level tests); F-3 carries forward for Cohort C retrofit prompts where the existing pack tests get extended.
- **PM-27 SOFT (convention):** the ABCs do not mandate constructor injection by themselves, but reference implementations (Cohort C / D) inherit the pattern from Gmail / Plaid / Reminders precedent.
- **Stacked-merge anti-pattern fix (PR #106 — on main):** c2/c3/c4 PRs from this cycle open with `base=main` automatically. Ghost conflicts resolve via the regex pattern proven on 11c.
- **Constitution-sync rule (PR #97):** this PR adds NEW infrastructure (5 ABCs, pack-registry module) but does NOT modify constitutional docs. No cascade exemption needed.
- **Rebase carry-forward (memo 08):** the simple `Adapter` Protocol from 11a c1 is the parent of the 5 ABCs; the protocol stays unchanged. Existing pack-class definitions (Gmail/Plaid/Reminders) currently implement the Protocol directly; Cohort C retrofits them to inherit from the appropriate ABC.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `adminme/adapters/base.py` has the simple `Adapter` Protocol (5186 bytes per gate). The 5 ABCs import `SensitivityFloor`, `AdapterContext`, `AdapterStatus`, and the `Adapter` Protocol from this file. **Verified at authorship time (2026-05-16).**

A2. `adminme/lib/__init__.py` exists (empty); the new `adminme/lib/adapters/__init__.py` and `adminme/lib/pack_registry/__init__.py` are new subdirectories under it. **Verified at authorship.**

A3. `adminme/lib/observation.py` (11 KB) provides the observation-mode helpers `CommunicationAdapter.send()` and `OutboundActionAdapter.execute_action()` wrap through. Confirm the function signatures at c1 start.

A4. `adminme/lib/scope.py` (10 KB) defines `OwnerScope` + the bridge-vs-central scope-resolution helpers. `PersonalKnowledgeAdapter` references the type.

A5. `adminme/lib/skill_runner/pack_loader.py` exists and parses skill pack `pack.yaml` files. The new `adminme/lib/pack_registry/` mirrors the loader pattern but for adapter packs. Confirm the yaml-load + Pydantic pattern at c3 start.

A6. No prior `adminme/lib/adapters/` directory exists. `mcp__github__get_file_contents(path="adminme/lib/adapters/", ref="main")` returns 404. **Verified at authorship.**

A7. No prior `adminme/lib/pack_registry/` directory exists. **Verified at authorship.**

A8. `pyproject.toml` already has `pydantic` (used by event schemas + observation module). No new dependency adds.

---

## §5 Objective

Ship the 5 abstract base classes for L1 adapters at `adminme/lib/adapters/` and the manifest format extensions for capability-driven adapter declarations at `adminme/lib/pack_registry/`. Establishes the contract layer that 11-β (install-time validator) and 11-γ (CLI + authoring guide) build on; subsequently consumed by Cohort C retrofit prompts (Gmail → CommunicationAdapter; Plaid → InboundDataAdapter; Reminders → ExternalStateMirrorAdapter) and Cohort D reference-implementation prompts. ≥60 new tests floor.

---

## §6 Out of scope

- **Install-time validator** — 11-β. The validator that ENFORCES the manifest extensions ships in the next sub-prompt; this prompt only PARSES them.
- **`adminme adapters` CLI subcommand group** — 11-γ. Discovery + scaffold + validate-as-CLI ships in the third sub-prompt.
- **Authoring guide** (`docs/adapter-authoring-guide.md`) — 11-γ.
- **Retrofitting existing Gmail / Plaid / Reminders packs to the new ABCs** — Cohort C retrofit prompts per memo 08 §3.3.
- **Reference implementations using the new ABCs** — Cohort D (resumed canon: 11a Gmail+Plaid using the ABCs, 11b Cat-B cohort, 11c-i bridge daemon, 11c-ii Cat-D adapters, 11d projection, 11e Contacts, 11f lists projection, 11g HA).
- **Member-bridge daemon + `:3337 bridge` ingest endpoint** — 11c-i (Cohort D).
- **`member_knowledge` projection (the 12th projection)** — 11d (Cohort D).
- **`lists` projection (the 13th projection)** — 11f (Cohort D).
- **Changes to the existing simple `Adapter` Protocol** at `adminme/adapters/base.py` — explicitly preserved unchanged; the 5 ABCs extend it via subclassing, not replacement. (The Cohort C retrofit prompts will not change this either — they only update adapter classes' inheritance to use the ABCs instead of the Protocol directly.)
- **Live multi-capability adapter integration** — the framework's multi-capability shape is tested via fake subclasses here; real multi-capability adapters (Apple Notes-checklists, Home Assistant) ship in Cohort D's 11c-ii and 11g.

---

## §7 Deliverables

### Five abstract base classes at `adminme/lib/adapters/`

- **`__init__.py`** — re-exports the 5 ABCs from a single import point: `from adminme.lib.adapters import CommunicationAdapter, ExternalStateMirrorAdapter, InboundDataAdapter, PersonalKnowledgeAdapter, OutboundActionAdapter`.

- **`communication_base.py`** (~120 lines) — `CommunicationAdapter`. Required methods (in addition to the simple Protocol's `start` / `stop` / `status`): `async def send(self, message: "OutboundMessage") -> "SendResult"` (observation-mode-suppressed via `adminme.lib.observation.suppress_if_observed`), `async def _on_inbound(self, envelope: "InboundEnvelope") -> None` (hook for subclasses). Class attributes: `family: Literal["messaging"]`; `capabilities: list[Capability]` with at least one Cat-A entry.

- **`external_state_mirror_base.py`** (~140 lines) — `ExternalStateMirrorAdapter`. Required methods: `async def apply_write(self, operation: "WriteOperation") -> "WriteResult"` (gated by `write_capabilities` membership check), `async def _on_external_change(self, envelope: "ChangeEnvelope") -> None` (hook). Class attributes: `family: Literal["reminders", "calendaring", "contacts"]`; `capabilities: list[Capability]`; `write_capabilities: list[str]` (per-operation, UT-27); `sharing_model_discriminator: str` (column name for Cat-B projections).

- **`inbound_data_base.py`** (~100 lines) — `InboundDataAdapter`. NO write method (Cat-C is inbound-only by definition; the discriminator from Cat-B). Class attributes: `family: Literal["financial", "iot", "telemetry"]`; `capabilities: list[Capability]`.

- **`personal_knowledge_base.py`** (~120 lines) — `PersonalKnowledgeAdapter`. Class attributes: `family: Literal["knowledge"]`; `runtime: Literal["bridge"]` (Cat-D is bridge-only per memo 04); `capabilities: list[Capability]`; `_owner_scope_immutable: Final[bool] = True` (the type contract that the bridge-ingest endpoint stamps owner_scope, not the adapter — concrete enforcement lives at the bridge endpoint, but the type contract makes the rule legible in code).

- **`outbound_action_base.py`** (~130 lines) — `OutboundActionAdapter`. Required method: `async def execute_action(self, request: "ActionRequest") -> "ActionResult"` (wrapped through `adminme.lib.observation.suppress_if_observed` per [§6.20]). Class attributes: `family: Literal["home_automation", "telephony", "external_action"]`; `capabilities: list[Capability]`; `observation_mode_required: bool = True` (default for Cat-E).

### Manifest format extensions at `adminme/lib/pack_registry/`

- **`__init__.py`** — exports `Capability`, `AdapterManifest`, `parse_adapter_manifest`.

- **`models.py`** (~150 lines) — Pydantic models:
  - `Capability` — fields: `kind: Literal["Cat-A", "Cat-B", "Cat-C", "Cat-D", "Cat-E"]`, `runtime: Literal["central", "bridge", "dual", "plugin"]`, `event_families: list[str]`, `sensitivity_default: Literal["normal", "sensitive", "privileged"] | None = None` (per-capability override), `observation_mode_required: bool | None = None`.
  - `AdapterManifest` — extends the existing flat pack.yaml model with: `capabilities: list[Capability]`, `write_capabilities: list[str] = []`, `sensitivity_default: Literal["normal", "sensitive", "privileged"] = "normal"`, `observation_mode_required: bool = False`, `owner_scope_overridability_at_install: bool = True`, `sharing_model_discriminator: str | None = None`, `dedup_strategy: dict[str, Any] | None = None`.
  - All fields `extra="forbid"` per Pydantic discipline; per-capability overrides resolved via `resolve_effective_sensitivity(capability_index: int) -> SensitivityLevel` helper.

- **`parser.py`** (~80 lines) — `parse_adapter_manifest(pack_dir: Path) -> AdapterManifest`. Reads `pack.yaml`, yaml.safe_load, validates against `AdapterManifest` model, returns the parsed structure. Mirrors the `pack_loader.py` pattern from `skill_runner/` but for adapter packs. Backward-compatible: existing flat manifests (without `capabilities` field) parse with `capabilities=[]` default.

### Tests at `tests/unit/lib/adapters/` + `tests/unit/lib/pack_registry/`

- `test_communication_adapter_base.py` (~10 tests): Protocol contract via fake subclass; `send` invokes observation suppression; `_on_inbound` hook callable; class attributes required (`family`, `capabilities`); rejects subclass missing `send`.
- `test_external_state_mirror_adapter_base.py` (~12 tests): Protocol contract; `apply_write` gated by `write_capabilities` membership; `_on_external_change` hook; rejects subclass with `write_capabilities` empty for Cat-B; `sharing_model_discriminator` required.
- `test_inbound_data_adapter_base.py` (~8 tests): Protocol contract; no write method on the class; rejects subclass that declares `write_capabilities`; happy path for Cat-C-shape subclass.
- `test_personal_knowledge_adapter_base.py` (~10 tests): Protocol contract; `runtime` literal-enforced to `"bridge"`; `_owner_scope_immutable` Final flag visible to type-checker; rejects subclass with `runtime="central"`.
- `test_outbound_action_adapter_base.py` (~10 tests): Protocol contract; `execute_action` invokes observation suppression; `observation_mode_required` defaults `True`; happy-path Cat-E subclass.
- `test_manifest_extensions.py` (~12 tests): `AdapterManifest` parses minimal + maximal manifests; capabilities-as-list validates each entry; `write_capabilities` empty allowed for Cat-A/Cat-C/Cat-D; sensitivity per-capability override resolves correctly; rejects unknown `kind`; rejects unknown `runtime`; backward-compat empty-capabilities default.
- `test_parser.py` (~8 tests): round-trip parse of each existing pack.yaml (Gmail / Plaid / Reminders) with `capabilities=[]` default; new manifest with multi-capability block parses; malformed yaml raises; missing pack.yaml raises.

Floor: ≥60 across the four commits.

---

## §8 Commit structure (exactly four commits; messages exact)

Every commit message follows `phase 11-alpha commit <N>: <imperative summary>` exactly. Branch strategy STACKED per §8.5; PR base = `main` for ALL FOUR c-bands per PR #106 sidecar.

### Commit 1 — Three ABCs (Communication, ExternalStateMirror, InboundData) + tests

**Files:** `adminme/lib/adapters/__init__.py` (NEW), `adminme/lib/adapters/communication_base.py` (NEW), `adminme/lib/adapters/external_state_mirror_base.py` (NEW), `adminme/lib/adapters/inbound_data_base.py` (NEW), `tests/unit/lib/adapters/__init__.py` (NEW), `tests/unit/lib/adapters/test_communication_adapter_base.py` (NEW), `tests/unit/lib/adapters/test_external_state_mirror_adapter_base.py` (NEW), `tests/unit/lib/adapters/test_inbound_data_adapter_base.py` (NEW).

**Files-to-read:** 6 (`adminme/adapters/base.py` for the parent Protocol, `observation.py` for `suppress_if_observed`, memo 05 §1.2 Cat-A through Cat-C contracts, memo 05 §3.2 ABC shape, `events/schemas/adapters.py` for the cross-cutting schemas the ABCs reference, the split memo §4 for the deliverable breakdown).

**Verification:**
```
poetry run python -c "from adminme.lib.adapters import CommunicationAdapter, ExternalStateMirrorAdapter, InboundDataAdapter; print('OK')"
poetry run mypy adminme/lib/adapters/ 2>&1 | tail -10
poetry run pytest tests/unit/lib/adapters/test_communication_adapter_base.py tests/unit/lib/adapters/test_external_state_mirror_adapter_base.py tests/unit/lib/adapters/test_inbound_data_adapter_base.py -v
git add adminme/lib/adapters/__init__.py adminme/lib/adapters/communication_base.py adminme/lib/adapters/external_state_mirror_base.py adminme/lib/adapters/inbound_data_base.py tests/unit/lib/adapters/
git commit -m "phase 11-alpha commit 1: CommunicationAdapter + ExternalStateMirrorAdapter + InboundDataAdapter ABCs"
```

Expected: mypy clean; ≥30 tests pass; the simple `Adapter` Protocol at `adminme/adapters/base.py` is the parent of all three.

### Commit 2 — Two ABCs (PersonalKnowledge, OutboundAction) + tests

**Files:** `adminme/lib/adapters/personal_knowledge_base.py` (NEW), `adminme/lib/adapters/outbound_action_base.py` (NEW), `tests/unit/lib/adapters/test_personal_knowledge_adapter_base.py` (NEW), `tests/unit/lib/adapters/test_outbound_action_adapter_base.py` (NEW). Update `adminme/lib/adapters/__init__.py` to re-export the new ABCs.

**Files-to-read:** 5 (memo 04 §2.2 for the Tailscale-binds-owner-scope invariant that `PersonalKnowledgeAdapter` surfaces, memo 05 §1.2 Cat-D + Cat-E contracts, memo 05 §2.6 owner_scope_overridability, `scope.py` for the `OwnerScope` type, `observation.py` for the Cat-E suppression wrapper).

**Verification:**
```
poetry run mypy adminme/lib/adapters/ 2>&1 | tail -10
poetry run pytest tests/unit/lib/adapters/ -v
git add adminme/lib/adapters/personal_knowledge_base.py adminme/lib/adapters/outbound_action_base.py adminme/lib/adapters/__init__.py tests/unit/lib/adapters/
git commit -m "phase 11-alpha commit 2: PersonalKnowledgeAdapter + OutboundActionAdapter ABCs"
```

Expected: mypy clean; ≥20 additional tests pass (running total ≥50 across the 5 ABCs); the `__init__.py` re-exports all 5 ABCs.

### Commit 3 — Manifest format extensions at `adminme/lib/pack_registry/`

**Files:** `adminme/lib/pack_registry/__init__.py` (NEW), `adminme/lib/pack_registry/models.py` (NEW), `adminme/lib/pack_registry/parser.py` (NEW), `tests/unit/lib/pack_registry/__init__.py` (NEW), `tests/unit/lib/pack_registry/test_manifest_extensions.py` (NEW), `tests/unit/lib/pack_registry/test_parser.py` (NEW).

**Files-to-read:** 6 (memo 05 §3.1 manifest extensions, `skill_runner/pack_loader.py` for the loader pattern, the 3 existing pack.yaml files (Gmail, Plaid, Reminders) for backward-compat validation, the existing `tests/unit/test_pack_loader.py` for the testing pattern).

**Verification:**
```
poetry run python -c "from adminme.lib.pack_registry import AdapterManifest, parse_adapter_manifest; print('OK')"
poetry run mypy adminme/lib/pack_registry/ 2>&1 | tail -10
poetry run pytest tests/unit/lib/pack_registry/ -v
# Backward-compat: parse the three existing pack.yaml files without error
poetry run python -c "from pathlib import Path; from adminme.lib.pack_registry import parse_adapter_manifest; [parse_adapter_manifest(Path('packs/adapters') / p) for p in ['messaging/gmail', 'financial/plaid', 'reminders/apple-eventkit']]; print('Backward-compat: OK')"
git add adminme/lib/pack_registry/ tests/unit/lib/pack_registry/
git commit -m "phase 11-alpha commit 3: adapter pack manifest extensions (capabilities-as-list, write_capabilities, sharing_model_discriminator)"
```

Expected: mypy clean; ≥20 manifest + parser tests pass; the 3 existing pack.yaml files parse cleanly with empty `capabilities=[]` defaults.

### Commit 4 — Integration tests + BUILD_LOG

**Files:** `tests/integration/test_adapter_framework_contracts.py` (NEW; integration test that instantiates a synthetic multi-capability adapter class — Cat-D + Cat-B per [PM-35] — and verifies the contract enforcement end-to-end), `docs/build_log.md` (append the 11-α entry).

**Files-to-read:** 3 (existing `tests/integration/test_adapter_supervisor_gmail.py` for the integration test template, `docs/build_log.md` tail for the append point, memo 05 §2.2 for the multi-capability shape the integration test exercises).

**Build_log Evidence template:** Append AFTER the most recent prior entry. Header: `### Prompt 11-α — Adapter framework contracts (5 ABCs + manifest extensions)`. Cite: the 5 ABCs at `adminme/lib/adapters/`; the manifest extensions at `adminme/lib/pack_registry/`; backward-compatibility with existing pack.yaml manifests (Gmail, Plaid, Reminders all parse with `capabilities=[]` default); the contract-vs-implementation split (parse here, validate in 11-β); carry-forwards into 11-β (validator builds on these contracts) and 11-γ (CLI builds on validator + scaffold uses ABCs as templates).

**Verification:**
```
poetry run ruff check adminme/lib/adapters/ adminme/lib/pack_registry/ tests/unit/lib/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_adapter_framework_contracts.py
git commit -m "phase 11-alpha commit 4: framework integration tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+60; `verify_invariants.sh` exit 0.

**PR open:** Title `phase 11-alpha: adapter framework contracts (5 ABCs + manifest extensions)`. Body: top line "First sub-prompt of the prompt-11 framework split per `docs/09-split-memo-11-framework.md`. Ships the contract layer (5 ABCs at `adminme/lib/adapters/`) and manifest extensions (capabilities-as-list, write_capabilities, sharing_model_discriminator, dedup_strategy at `adminme/lib/pack_registry/`) that 11-β (validator) and 11-γ (CLI + guide) build on. Backward-compatible — existing pack.yaml manifests parse cleanly with `capabilities=[]` default." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` is `main` per PR #106 sidecar. Pre-flight stack-integrity check on c4 expects 3 prior commits above `origin/main` (c1+c2+c3).

Ghost conflicts on c2/c3/c4 (squash-merge artifacts) — resolve via the JS regex pattern proven on 11c (one execution per file).

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥60 new tests passing; ruff clean on `adminme/lib/adapters/` / `adminme/lib/pack_registry/` / `tests/unit/lib/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`.

Cross-cutting [§N] / [DN] checks:

- [§7.3, D17]: zero projection writes from the new ABCs or manifest models (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' adminme/lib/adapters/ adminme/lib/pack_registry/` returns 0 matches).
- [§8.2, D6]: no LLM SDK imports under `adminme/lib/adapters/` or `adminme/lib/pack_registry/`.
- [§6 invariant 10]: `sensitivity_default` cannot be lowered by per-capability override below the top-level pack default. `AdapterManifest.resolve_effective_sensitivity` enforces the "cannot lower below pack-default" rule; test asserts.
- [§6.20]: `CommunicationAdapter.send` and `OutboundActionAdapter.execute_action` wrap through `adminme.lib.observation.suppress_if_observed`. Tests inject a fake suppression function and verify the wrapper invokes it.
- [D8]: `correlation_id` discipline is unchanged from the simple `Adapter` Protocol (the ABCs do not override the propagation behavior; subclass adapters call `ctx.correlation_id_factory()` per emission, same as Gmail/Plaid/Reminders do today).
- [PM-34]: 5 ABCs land at `adminme/lib/adapters/` exactly. Naming + class shape matches memo 05 §3.2 verbatim.
- [PM-35]: `capabilities` is a list, not a single field. Multi-capability adapters declare multiple blocks. Test asserts.
- [UT-27]: `write_capabilities` is a per-operation list, not a boolean. Test asserts.
- [memo 04 §2.2]: `PersonalKnowledgeAdapter` surfaces the owner_scope-from-Tailscale invariant via type contract (`_owner_scope_immutable: Final[bool] = True`); test asserts the attribute is Final.

---

## §10 Stop

Five abstract base classes at `adminme/lib/adapters/` shipped. Manifest format extensions at `adminme/lib/pack_registry/` shipped. Backward-compatible with existing pack.yaml manifests. The contract layer that 11-β (install-time validator) and 11-γ (CLI + authoring guide) build on is in place.

Ready for **prompt 11-β** (install-time validator extensions — capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence). The validator consumes the manifest extensions shipped here + the ABCs to check adapter-pack compliance at install time.
