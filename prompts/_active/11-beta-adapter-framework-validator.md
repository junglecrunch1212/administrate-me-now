# Prompt 11-β: Adapter framework — install-time validator

**Phase:** Phase A — adapter framework expansion (second sub-prompt of the prompt-11 split per `docs/09-split-memo-11-framework.md`).

**Branch:** `claude/11-beta-<auto>` (harness-assigned). PR base = `main` for all four c-bands per PR #106 sidecar. STACKED across c1→c4 per §8.5.

**Depends on:** Prompt 11-α merged to main (HEAD `fdbdd556` as of authorship). The 5 ABCs at `adminme/lib/adapters/` and the `AdapterManifest` / `Capability` / `parse_adapter_manifest` schema at `adminme/lib/pack_registry/` are the contract surface this prompt builds the validator on. 11-α PARSES; 11-β ENFORCES.

**Quality bar:** introduction-tier prompt per `D-prompt-tier-and-pattern-index.md`. The validator output is the seam every Cohort C retrofit + Cohort D reference-implementation prompt relies on to surface "wrong-shape" pack errors at install time, not at runtime. Quality bar correspondingly high — checks must be coherent, narrowly scoped, and produce diagnostic messages that name the failing field.

**Stop condition:** A `validate_adapter_pack(pack_dir) -> ValidationResult` aggregator at `adminme/lib/pack_registry/validator/__init__.py` plus six per-check modules: capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence, observation-mode wrapping. ≥30 new tests across the four commits. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~70-90 minutes wall clock, 4 commits. ~$60-100 API spend per split memo §2.

---

## §1 Behavioral grounding

You are building the **enforcement layer** that the framework's contract layer (11-α) was designed for. Per memo 05 §3.4 + [D19] / [D20] / [PM-34], the validator runs at install time — BEFORE the supervisor instantiates the adapter — so wrong-shape pack manifests fail fast with a diagnostic that names the field, not opaquely at runtime when an adapter tries to call a non-existent method.

The contract-vs-implementation split was load-bearing in 11-α: that prompt declared the schema; this prompt verifies compliance against the schema. Every check here corresponds to a rule already documented in the ABCs' docstrings or the manifest models' field constraints — the validator catches divergences between declaration and actual code, not novel rules.

**Six checks, one aggregator** [memo 05 §3.4, [PM-34], 11-α BUILD_LOG carry-forward §11-β]:

- **Capability-runtime coherence**: Cat-D adapter MUST declare `runtime: "bridge"` (memo 04 §2.2); Cat-A `messaging` family MUST declare `runtime ∈ {"central", "plugin"}` (memo 05 §2.1); Cat-D's manifest MUST set `owner_scope_overridability_at_install: False` (memo 04 §2.2); Cat-E's manifest MUST set `observation_mode_required: True` (memo 05 §2.4, [§6.20]). These rules surface declarative invariants in the manifest that the ABC docstrings already pin; the validator catches manifest drift.

- **Event-schema registration**: every event family declared in `capabilities[].event_families` MUST resolve to a Pydantic schema registered with `adminme.events.registry.registry` at import-time. Adapter packs that declare emission of an unregistered event family fail validation. Walks `adminme/events/schemas/` to collect the registered set; checks each manifest's declared families against it.

- **Projection-subscription compatibility**: for each Cat-B or Cat-D capability (the two categories whose ingest events feed projections), at least one `event_families` entry MUST appear in some projection's `subscribes_to` list. Walks `adminme/projections/` for Projection subclasses, collects `subscribes_to` declarations. A Cat-D capability whose target projection ships in a later prompt (e.g. `member_knowledge` from 11d, `lists` from 11f) produces a `pending_projection` advisory, not a refusal — the validator is forward-compat.

- **Base-class conformance**: the adapter class loaded from the pack (`<pack_root>/adapter.py`'s top-level `class <Name>`) MUST inherit from exactly one of the 5 ABCs at `adminme/lib/adapters/`. The inherited ABC's `family` Literal MUST overlap with the manifest's declared family. A pack manifest declaring a `Cat-B` capability whose adapter class inherits from `CommunicationAdapter` (Cat-A) is a hard refusal.

- **write_capabilities method-presence**: for each per-operation string in `manifest.write_capabilities`, the adapter class MUST expose a private `_handle_<operation>` async method (or a single `_apply_write_impl` whose body dispatches on `operation.kind` — both shapes pass; the validator checks the disjunction via AST walk). Cat-A / Cat-C / Cat-D manifests with non-empty `write_capabilities` fail (those categories have no write surface).

- **Observation-mode wrapping**: `CommunicationAdapter.send` and `OutboundActionAdapter.execute_action` (the two outbound surfaces) MUST route through `adminme.lib.observation` (the function name is `suppress_if_observed` or `outbound` per the on-main module; verify at c3 start). AST walks the subclass's `send` / `execute_action` body for the import + call. Per [§6.20]: the single outbound-suppression enforcement point lives in `adminme.lib.observation`; adapters wire through it; the validator catches adapters that bypass.

The aggregator (`validate_adapter_pack`) runs all six checks and returns a `ValidationResult` with `findings: list[ValidationFinding]`. Each finding has `severity: Literal["error", "warning", "advisory"]`, `check: str` (which of the six), `message: str`, `field_path: str | None` (the manifest field path, e.g. `"capabilities[0].kind"`). The aggregator is what the supervisor's install-stage 1 calls; a single `error` finding refuses the install, `warnings` are surfaced but install proceeds, `advisory` is informational (e.g. `pending_projection` for Cat-D capabilities whose projection ships later).

**Where the validator plugs into install** [memo 05 §3.4]. The exact integration point (which file calls `validate_adapter_pack` during pack install) is left to the executor's c4 wiring — verify at c4 start. Likely candidates: the supervisor's adapter-loading code OR a new `adminme/lib/pack_registry/install.py` shim. The validator MUST be importable + callable as a standalone function before the wiring; full integration test (load → validate → reject malformed pack) lands in c4.

**Anti-vision halt** [VISION §"Small enough to inspect", DRIFT-CONTROL Job 4]. Each per-check module MUST be ≤180 lines including docstring. If a single check exceeds this (capability-runtime coherence has the most rules at ~150 lines; observation-mode wrapping is AST-walk heavy at ~140 lines), halt and surface — checks must be inspectable independently. The aggregator (`__init__.py`) MUST be ≤120 lines.

**Mode:** Phase A. No live pack instantiation in validator code — the validator AST-walks adapter class definitions but does not import them. The base-class conformance + observation-mode wrapping checks use `ast.parse` on the adapter file content, not `importlib.import_module` — this is intentional to avoid side effects from adapter-class definition.

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md` / `memo 05` ranges. Memo 05 is 122 KB; targeted reads only.

1. **`docs/09-split-memo-11-framework.md`** — full read (~12 KB). §2 (11-β row + budget), §3 (depends on 11-α), §4 (deliverables breakdown for 11-β; note the path correction below).
2. **`prompts/_active/11-alpha-adapter-framework-abcs-and-manifest.md`** — full read (~28 KB). §7 (deliverables that shipped; what the validator checks against), §9 (cross-cutting invariant assertions the validator AST-walks to confirm).
3. **`prompts/11-adapter-framework.md`** — full read (15 KB). §"Authoring guide" outline; the validator surfaces what the guide documents.
4. **`docs/05-architecture-amendment-personal-data-layer.md`** — targeted reads:
   - §2.1 (five-category taxonomy + runtime placement rules) — lines ~12-200
   - §2.4 (sensitivity_default + observation_mode_required as orthogonal fields) — ~259-285
   - §2.6 (owner_scope_overridability_at_install) — search `grep -n '^### 2.6'`
   - §3.4 (install-time validator design) — search `grep -n '^### 3.4'`
5. **`docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`** — §2.2 (Tailscale-binds-owner-scope — surfaces as the Cat-D `owner_scope_overridability_at_install=False` rule).
6. **`adminme/lib/adapters/__init__.py`** + each ABC file (`communication_base.py`, `external_state_mirror_base.py`, `inbound_data_base.py`, `personal_knowledge_base.py`, `outbound_action_base.py`) — full read. The validator's base-class conformance check imports these to assert subclass inheritance.
7. **`adminme/lib/pack_registry/__init__.py`** + `models.py` + `parser.py` — full read. The validator builds on `AdapterManifest`'s parsed shape; `Capability.kind` / `Capability.runtime` / `AdapterManifest.write_capabilities` etc. are the input fields the checks consume.
8. **`adminme/lib/skill_runner/pack_loader.py`** — full read. **Precedent for pack-load logic.** The validator runs AFTER the existing pack loader succeeds; understand the load shape so the validator doesn't duplicate work.
9. **`adminme/adapters/base.py`** — full read. The simple `Adapter` Protocol that all 5 ABCs extend. The validator's `Adapter`-Protocol-satisfaction check runs via `isinstance` against the runtime-checkable Protocol.
10. **`adminme/events/registry.py`** — full read. The `SchemaRegistry` that `adminme/events/schemas/*` modules call `.register(...)` against. The event-schema-registration check queries `registry.registered_event_families()` (or equivalent — verify the actual method name at c1 start).
11. **`adminme/events/schemas/__init__.py`** + walk to confirm which families register. Particularly `adapters.py` (cross-cutting), `crm.py`, `domain.py`, `financial.py`, `governance.py`, `ingest.py`, `ops.py`, `reminders.py`, `system.py`.
12. **`adminme/projections/base.py`** — full read. `Projection.subscribes_to: list[str] | str` is the per-projection subscription declaration the projection-subscription-compatibility check walks. The 11 projection directories under `adminme/projections/` each contain a Projection subclass declaring its families.
13. **`adminme/lib/observation.py`** — full read. The single outbound-suppression enforcement point per [§6.20]. The observation-mode-wrapping check AST-walks subclass `send` / `execute_action` for an import + call into this module; confirm the exact function name (`suppress_if_observed` vs `outbound`) at c3 start before writing the AST matcher.
14. **`packs/adapters/messaging/gmail/pack.yaml`**, **`packs/adapters/financial/plaid/pack.yaml`**, **`packs/adapters/reminders/apple-eventkit/pack.yaml`** + each pack's `adapter.py` — read each. Existing v1 manifests (without `capabilities:`) MUST validate cleanly with the new validator (backward-compat); existing adapter classes currently implement the simple `Adapter` Protocol directly (not the new ABCs) so they will fail base-class conformance — this is expected, and the validator surfaces the failure with an `advisory_pending_retrofit` severity. (Cohort C retrofits the inheritance; 11-β surfaces the gap, doesn't fail-the-build over it.)
15. **`tests/unit/test_pack_loader.py`** — full read. **Precedent for testing pack-load logic.** New validator tests at `tests/unit/lib/pack_registry/validator/` follow the same fixture pattern.
16. **`tests/unit/lib/pack_registry/test_manifest_extensions.py`** + **`tests/unit/lib/pack_registry/test_parser.py`** — full read. The validator tests build on the same `pack.yaml`-on-disk fixture infrastructure these tests use.
17. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads: §1 (event log append-only), §6 invariant 10 (sensitivity floor), §6.20 (observation mode wraps outbound), §7.3 (no projection direct writes from adapters — the validator does NOT enforce this since the simple Protocol's `Adapter` baseline does, but the validator's `[§7.3]` self-check asserts the validator code itself doesn't write projections).
18. **`docs/DECISIONS.md`** — read [D6], [D7], [D8], [D17], [D18], [D19], [D20], [D24].

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad confirming: (a) the validator reads from manifests + AST-walks adapter source; it does NOT import adapter modules at validation time; (b) each check returns `ValidationFinding` entries with severity / check / message / field_path; (c) the aggregator combines findings without short-circuiting (collect all findings, return the full list); (d) backward-compat behavior — existing v1 manifests (Gmail/Plaid/Reminders) yield `advisory_pending_retrofit` findings on base-class conformance, not errors; (e) the on-main observation function name (`outbound` vs `suppress_if_observed`) is fixed at c3 start before writing the AST matcher.

---

## §3 Reconciliation notes (carry-forwards from 11-α merge + Cohort A/B step 1-4)

- **Path correction (Gate 2 precedent from 11-α)**: split memo §4 11-β deliverables say `bootstrap/pack_registry/validator/*` but the canonical location is `adminme/lib/pack_registry/validator/` — sibling to 11-α's `models.py` + `parser.py`. The split memo's "bootstrap/" path was speculative; 11-α verified at gate that the actual pack-registry parent is `adminme/lib/pack_registry/`. 11-β inherits the corrected path.
- **F-3 HARD (10c-iii heritage)**: any new pack-internal tests use unique basenames. This prompt does NOT add pack-internal tests (it adds framework-level tests at `tests/unit/lib/pack_registry/validator/`); F-3 carries forward for Cohort C retrofit prompts.
- **PM-27 SOFT (convention)**: validator does not mandate constructor injection by itself; reference implementations (Cohort C / D) inherit the pattern from existing precedent.
- **Stacked-merge anti-pattern fix (PR #106 — on main)**: c2/c3/c4 PRs from this cycle open with `base=main` automatically. Ghost conflicts resolve via the regex pattern proven on 11c + 11-α (one execution per file).
- **Constitution-sync rule (PR #97)**: this PR adds NEW infrastructure but does NOT modify constitutional docs. No cascade exemption needed.
- **Rebase carry-forward (memo 08)**: validator builds on the 5 ABCs + manifest schema shipped by 11-α; both are on main HEAD `fdbdd556` and form the validator's input surface. Cohort C retrofit prompts will subsequently consume the validator (via `adminme adapters validate <pack>` in 11-γ).
- **11-α BUILD_LOG carry-forward §11-β** (lines 590+ of `docs/build_log.md`): the validator surface listed there is the canonical scope — capability-runtime coherence (which absorbs the Cat-D `owner_scope_overridability_at_install=False` sub-rule + Cat-E `observation_mode_required=True` sub-rule), event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence, observation-mode wrapping. Six checks, distributed 2/1/3 across c1/c2/c3 per §8.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `adminme/lib/pack_registry/models.py` exports `Capability`, `AdapterManifest`, `CapabilityKind`, `CapabilityRuntime`, `SensitivityLevel`, plus `resolve_effective_sensitivity` method. **Verified at authorship (2026-05-16).**

A2. `adminme/lib/pack_registry/parser.py` exports `parse_adapter_manifest`, `ManifestParseError`. **Verified at authorship.**

A3. `adminme/lib/adapters/__init__.py` re-exports all 5 ABCs (CommunicationAdapter, ExternalStateMirrorAdapter, InboundDataAdapter, PersonalKnowledgeAdapter, OutboundActionAdapter). **Verified at authorship.**

A4. `adminme/events/registry.py` exists and exposes a way to query registered event-family names. The exact method name (`registered_event_families()`, `families()`, `event_types()`, etc.) must be verified at c1 start; the event-schema-registration check uses whichever surface is canonical.

A5. `adminme/projections/base.py` declares `Projection.subscribes_to: list[str] | str`. Each of the 11 projection directories under `adminme/projections/` contains a Projection subclass declaring its families. **Verified at authorship.**

A6. `adminme/lib/observation.py` exists and exposes the single outbound-suppression enforcement point. The exact callable name (`suppress_if_observed` per 11-α prompt §1, vs `outbound` per BUILD_LOG carry-forward) MUST be confirmed at c3 start by `grep -n 'def ' adminme/lib/observation.py`; the AST-matcher uses whichever name is on main.

A7. `packs/adapters/messaging/gmail/`, `packs/adapters/financial/plaid/`, `packs/adapters/reminders/apple-eventkit/` each have a `pack.yaml` + an `adapter.py` with a top-level class implementing the simple `Adapter` Protocol. These three are the backward-compat reference set; c4 integration test validates each and asserts the `advisory_pending_retrofit` finding (not error) on base-class conformance.

A8. `pyproject.toml` already has `pyyaml` + `pydantic`. No new dependency adds. The AST-walk uses Python's stdlib `ast` module.

---

## §5 Objective

Ship the install-time validator for adapter packs at `adminme/lib/pack_registry/validator/`. Six checks (capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence, observation-mode wrapping) plus a `validate_adapter_pack` aggregator. The validator surfaces wrong-shape pack errors at install time with field-named diagnostics. Backward-compat: existing v1 manifests (Gmail / Plaid / Reminders) yield `advisory_pending_retrofit` findings, not errors. ≥30 new tests floor.

---

## §6 Out of scope

- **`adminme adapters` CLI subcommand group + `validate` subcommand wiring** — 11-γ. The validator is callable from Python here; the CLI wrap ships in the third sub-prompt.
- **Authoring guide** (`docs/adapter-authoring-guide.md`) — 11-γ. The validator's error messages reference field paths; the guide documents the rules.
- **Scaffold templates** — 11-γ. The validator runs against existing packs and synthetic test fixtures here; templates that generate validator-clean packs from scratch ship in the third sub-prompt.
- **Retrofitting existing Gmail / Plaid / Reminders packs to the new ABCs** — Cohort C retrofit prompts per memo 08 §3.3. 11-β SURFACES the gap via `advisory_pending_retrofit`; Cohort C closes it.
- **New ABC additions** — the 5 ABCs from 11-α are the canonical category set. New categories require a constitutional amendment (Tier C memo + amendment cycle), not a validator extension.
- **Live import of adapter modules at validation time** — explicitly out of scope. Validator AST-walks `adapter.py` content; does NOT call `importlib.import_module`. This is intentional: a malformed adapter module's import-time side effects MUST NOT fire during pack validation.
- **Member-bridge daemon** — 11c-i (Cohort D).
- **`member_knowledge` projection (the 12th)** — 11d (Cohort D); validator's projection-subscription-compatibility check produces `advisory_pending_projection` for Cat-D capabilities until 11d ships.
- **`lists` projection (the 13th)** — 11f (Cohort D); same advisory pattern.
- **Cross-pack capability collision checks** (e.g. two Cat-B adapters claiming the same `sharing_model_discriminator`) — out of scope; install-order discipline + supervisor-level capability registry handles those concerns separately.

---

## §7 Deliverables

### Validator aggregator + result types at `adminme/lib/pack_registry/validator/`

- **`__init__.py`** (~110 lines) — re-exports `validate_adapter_pack`, `ValidationResult`, `ValidationFinding`, `Severity`. The `validate_adapter_pack(pack_dir: Path, *, registry=None) -> ValidationResult` aggregator: loads the manifest via `parse_adapter_manifest`; runs each of the six checks; collects findings; returns the aggregated `ValidationResult`. Accepts an optional `registry` parameter (defaults to `adminme.events.registry.registry`) so tests can inject a fake.
- **`result.py`** (~70 lines) — `Severity = Literal["error", "warning", "advisory"]`; `@dataclass(frozen=True) class ValidationFinding(severity, check, message, field_path)`; `@dataclass(frozen=True) class ValidationResult(pack_dir, findings)` with `has_errors() -> bool` / `errors() -> list[ValidationFinding]` / `warnings() -> list[ValidationFinding]` / `advisories() -> list[ValidationFinding]` accessors.

### Six per-check modules

- **`capability_runtime_coherence.py`** (~150 lines) — `check_capability_runtime_coherence(manifest: AdapterManifest) -> list[ValidationFinding]`. Rules: Cat-D ⇒ `runtime="bridge"`; Cat-D ⇒ `owner_scope_overridability_at_install=False`; Cat-E ⇒ `observation_mode_required=True`; Cat-A `family="messaging"` ⇒ `runtime ∈ {"central", "plugin"}`. Each violated rule yields one `error` finding with field-path `capabilities[N].kind` or `owner_scope_overridability_at_install` etc.

- **`event_schema_registration.py`** (~90 lines) — `check_event_schema_registration(manifest: AdapterManifest, *, registry) -> list[ValidationFinding]`. For each capability, walk `capability.event_families`. For each family, look up the registered set on the registry. Unknown family ⇒ `error` finding with field-path `capabilities[N].event_families[M]` + message naming the family and listing 3 registered families with the longest prefix overlap as suggestions.

- **`projection_subscription_compatibility.py`** (~120 lines) — `check_projection_subscription_compatibility(manifest: AdapterManifest, *, projections_dir: Path) -> list[ValidationFinding]`. Walks `projections_dir` for Projection subclasses (default: `adminme/projections/`). For each Cat-B or Cat-D capability, asserts at least one `event_families` entry appears in some projection's `subscribes_to` declaration. Known-pending projections (per `_PENDING_PROJECTIONS` const: `member_knowledge`, `lists`) yield `advisory_pending_projection` not error. Other unmatched families yield `error`.

- **`base_class_conformance.py`** (~140 lines) — `check_base_class_conformance(pack_dir: Path, manifest: AdapterManifest) -> list[ValidationFinding]`. AST-walks `<pack_dir>/adapter.py`; locates the top-level class with the most explicit bases that resolve to one of the 5 ABC names (`CommunicationAdapter`, etc.). Per category mapping (Cat-A ⇒ `CommunicationAdapter`, Cat-B ⇒ `ExternalStateMirrorAdapter`, etc.), asserts the inheritance matches the manifest's primary capability. Adapter class with NO ABC inheritance + manifest has `capabilities=[]` ⇒ `advisory_pending_retrofit` (this catches the 3 existing v1 packs). Adapter class with ABC inheritance + capability mismatch ⇒ `error`.

- **`write_capabilities_check.py`** (~130 lines) — `check_write_capabilities(pack_dir: Path, manifest: AdapterManifest) -> list[ValidationFinding]`. For each per-operation string in `manifest.write_capabilities`: AST-walks `<pack_dir>/adapter.py`; asserts EITHER an `async def _handle_<operation>(self, operation)` method exists OR an `async def _apply_write_impl(self, operation)` method exists whose body dispatches on `operation.kind`. Missing both shapes ⇒ `error`. Manifest's `write_capabilities` non-empty + capability is Cat-A/Cat-C/Cat-D ⇒ `error` (those categories have no write surface).

- **`observation_mode_wrapping.py`** (~140 lines) — `check_observation_mode_wrapping(pack_dir: Path, manifest: AdapterManifest) -> list[ValidationFinding]`. AST-walks `<pack_dir>/adapter.py`; locates the class's `send` (for Cat-A) or `execute_action` (for Cat-E) method. Asserts the method body contains a call to `adminme.lib.observation.<name>` where `<name>` is whichever symbol the on-main module exports (verified at c3 start). Missing ⇒ `error`. Cat-B / Cat-C / Cat-D adapters skip this check (no outbound surface).

### Tests at `tests/unit/lib/pack_registry/validator/`

- `test_result.py` (~5 tests): `ValidationResult.has_errors()`, `.errors()`, `.warnings()`, `.advisories()` accessors; frozen-dataclass canaries.
- `test_capability_runtime_coherence.py` (~7 tests): each of the four rules — happy path + violation. Field-path correctness.
- `test_event_schema_registration.py` (~5 tests): happy path; unknown family error; the 3-family-suggestion list shape; empty `event_families` skips; injected fake registry.
- `test_projection_subscription_compatibility.py` (~6 tests): happy path (Cat-B family in tasks projection's `subscribes_to`); unknown family error; pending projections (`member_knowledge`, `lists`) yield advisory not error; Cat-A / Cat-C / Cat-E capabilities skip the check.
- `test_base_class_conformance.py` (~7 tests): happy path each category; cross-category mismatch error; empty-capabilities + no-ABC inheritance ⇒ advisory; malformed adapter.py (syntax error) ⇒ specific error finding (not crash).
- `test_write_capabilities_check.py` (~6 tests): `_handle_<op>` shape; `_apply_write_impl` dispatch shape; missing both ⇒ error; non-write-category + non-empty `write_capabilities` ⇒ error.
- `test_observation_mode_wrapping.py` (~5 tests): Cat-A `send` calls observation function ⇒ pass; missing call ⇒ error; Cat-E `execute_action` calls observation function ⇒ pass; missing ⇒ error; Cat-B/C/D skip.
- `test_validate_adapter_pack.py` (~6 tests): aggregator runs all six checks; collects findings non-short-circuit; `has_errors()` correct on mixed-severity result; injected fake registry; missing pack.yaml ⇒ `ManifestParseError` propagates.

Floor: ≥30 across the four commits.

---

## §8 Commit structure (exactly four commits; messages exact)

Every commit message follows `phase 11-beta commit <N>: <imperative summary>` exactly. Branch strategy STACKED per §8.5; PR base = `main` for ALL FOUR c-bands per PR #106 sidecar.

### Commit 1 — Result types + capability-runtime coherence + event-schema registration

**Files:** `adminme/lib/pack_registry/validator/__init__.py` (NEW, stub aggregator that calls just these two checks), `adminme/lib/pack_registry/validator/result.py` (NEW), `adminme/lib/pack_registry/validator/capability_runtime_coherence.py` (NEW), `adminme/lib/pack_registry/validator/event_schema_registration.py` (NEW), `tests/unit/lib/pack_registry/validator/__init__.py` (NEW), `tests/unit/lib/pack_registry/validator/test_result.py` (NEW), `tests/unit/lib/pack_registry/validator/test_capability_runtime_coherence.py` (NEW), `tests/unit/lib/pack_registry/validator/test_event_schema_registration.py` (NEW).

**Files-to-read:** 6 (`adminme/lib/pack_registry/models.py` for AdapterManifest shape, `adminme/events/registry.py` for the registry surface, memo 05 §2.1 + §2.4 for the rules, memo 04 §2.2 for the Cat-D owner_scope rule, `adminme/events/schemas/__init__.py` for the family naming convention, the split memo §4 11-β breakdown).

**Verification:**
```
poetry run python -c "from adminme.lib.pack_registry.validator import ValidationResult, ValidationFinding; print('OK')"
poetry run mypy adminme/lib/pack_registry/validator/ 2>&1 | tail -10
poetry run pytest tests/unit/lib/pack_registry/validator/ -v
git add adminme/lib/pack_registry/validator/__init__.py adminme/lib/pack_registry/validator/result.py adminme/lib/pack_registry/validator/capability_runtime_coherence.py adminme/lib/pack_registry/validator/event_schema_registration.py tests/unit/lib/pack_registry/validator/
git commit -m "phase 11-beta commit 1: validator result types + capability-runtime coherence + event-schema registration checks"
```

Expected: mypy clean; ≥17 tests pass (5 result + 7 capability-runtime + 5 event-schema).

### Commit 2 — Projection-subscription compatibility check

**Files:** `adminme/lib/pack_registry/validator/projection_subscription_compatibility.py` (NEW), `tests/unit/lib/pack_registry/validator/test_projection_subscription_compatibility.py` (NEW). Update `adminme/lib/pack_registry/validator/__init__.py` to wire the new check into the aggregator.

**Files-to-read:** 4 (`adminme/projections/base.py` for the Projection ABC, sample projection's `subscribes_to` declaration from one of the 11 directories, memo 05 §3.4 for the projection-subscription rule, the existing tests in c1).

**Verification:**
```
poetry run mypy adminme/lib/pack_registry/validator/ 2>&1 | tail -10
poetry run pytest tests/unit/lib/pack_registry/validator/ -v
git add adminme/lib/pack_registry/validator/projection_subscription_compatibility.py adminme/lib/pack_registry/validator/__init__.py tests/unit/lib/pack_registry/validator/
git commit -m "phase 11-beta commit 2: projection-subscription compatibility check"
```

Expected: mypy clean; ≥6 additional tests pass (running total ≥23). The `_PENDING_PROJECTIONS` const lists `member_knowledge` + `lists`.

### Commit 3 — Base-class conformance + write_capabilities + observation-mode wrapping

**Files:** `adminme/lib/pack_registry/validator/base_class_conformance.py` (NEW), `adminme/lib/pack_registry/validator/write_capabilities_check.py` (NEW), `adminme/lib/pack_registry/validator/observation_mode_wrapping.py` (NEW), `tests/unit/lib/pack_registry/validator/test_base_class_conformance.py` (NEW), `tests/unit/lib/pack_registry/validator/test_write_capabilities_check.py` (NEW), `tests/unit/lib/pack_registry/validator/test_observation_mode_wrapping.py` (NEW). Update `__init__.py` to wire the three new checks.

**Files-to-read:** 5 (`adminme/lib/adapters/__init__.py` for the 5 ABC names, `adminme/lib/adapters/external_state_mirror_base.py` for the `_apply_write_impl` precedent, `adminme/lib/observation.py` to confirm the exact function name — `grep -n '^def ' adminme/lib/observation.py`, an existing pack's `adapter.py` (Gmail or Plaid) for the AST-walk fixture pattern, the existing tests in c1+c2).

**Critical step at c3 start:** verify the actual function name in `adminme/lib/observation.py` (`suppress_if_observed` per 11-α prompt §1, vs `outbound` per BUILD_LOG carry-forward). Pin the AST matcher to whichever name is canonical on main.

**Verification:**
```
poetry run mypy adminme/lib/pack_registry/validator/ 2>&1 | tail -10
poetry run pytest tests/unit/lib/pack_registry/validator/ -v
git add adminme/lib/pack_registry/validator/base_class_conformance.py adminme/lib/pack_registry/validator/write_capabilities_check.py adminme/lib/pack_registry/validator/observation_mode_wrapping.py adminme/lib/pack_registry/validator/__init__.py tests/unit/lib/pack_registry/validator/
git commit -m "phase 11-beta commit 3: base-class conformance + write_capabilities + observation-mode wrapping checks"
```

Expected: mypy clean; ≥18 additional tests pass (running total ≥41). `__init__.py` aggregator now calls all six checks.

### Commit 4 — Aggregator integration tests + BUILD_LOG

**Files:** `tests/integration/test_adapter_framework_validator.py` (NEW; integration test that constructs a real `pack_dir` on `tmp_path` with `pack.yaml` + `adapter.py` and exercises the full aggregator); `tests/unit/lib/pack_registry/validator/test_validate_adapter_pack.py` (NEW; aggregator-level unit tests); `docs/build_log.md` (append the 11-β entry).

**Files-to-read:** 4 (`tests/integration/test_adapter_framework_contracts.py` for the integration test template from 11-α c4, `docs/build_log.md` tail for the append point, the three existing pack.yaml files for the backward-compat scenarios, the §"Carry-forward for prompt 11-β" section of the 11-α BUILD_LOG entry for the canonical scope description).

**Build_log Evidence template:** Append AFTER the 11-α entry. Header: `### Prompt 11-β — Adapter framework validator (6 install-time checks)`. Cite: the 6 checks at `adminme/lib/pack_registry/validator/`; the aggregator + `ValidationResult` / `ValidationFinding` shape; backward-compat behavior with existing Gmail/Plaid/Reminders packs (`advisory_pending_retrofit` not error); the AST-walk-only stance (no live adapter import at validation time); carry-forwards into 11-γ (CLI's `validate` subcommand wraps the aggregator) and Cohort C retrofit (the validator becomes a passing-check once retrofit completes).

**Verification:**
```
poetry run ruff check adminme/lib/pack_registry/validator/ tests/unit/lib/pack_registry/validator/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
# Backward-compat: each of the three existing packs validates with advisory_pending_retrofit + no errors
poetry run python -c "from pathlib import Path; from adminme.lib.pack_registry.validator import validate_adapter_pack; results = [validate_adapter_pack(Path('packs/adapters') / p) for p in ['messaging/gmail', 'financial/plaid', 'reminders/apple-eventkit']]; assert all(not r.has_errors() for r in results), [r.errors() for r in results]; print('Backward-compat: OK')"
git add docs/build_log.md tests/integration/test_adapter_framework_validator.py tests/unit/lib/pack_registry/validator/test_validate_adapter_pack.py
git commit -m "phase 11-beta commit 4: validator integration tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+30; `verify_invariants.sh` exit 0; backward-compat check passes (existing packs have NO errors, only advisories).

**PR open:** Title `phase 11-beta: adapter framework validator (6 install-time checks)`. Body: top line "Second sub-prompt of the prompt-11 framework split per `docs/09-split-memo-11-framework.md`. Ships the install-time validator at `adminme/lib/pack_registry/validator/` — six checks (capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence, observation-mode wrapping) plus the `validate_adapter_pack` aggregator. AST-walk-only stance: validator does not live-import adapter modules. Backward-compat: existing Gmail/Plaid/Reminders packs yield `advisory_pending_retrofit` not error." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` is `main` per PR #106 sidecar. Pre-flight stack-integrity check on c4 expects 3 prior commits above `origin/main` (c1+c2+c3).

Ghost conflicts on c2/c3/c4 (squash-merge artifacts) — resolve via the JS regex pattern proven on 11c + 11-α (Partner drives Chrome MCP per the precedent).

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥30 new tests passing; ruff clean on `adminme/lib/pack_registry/validator/` / `tests/unit/lib/pack_registry/validator/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`; backward-compat check (existing 3 packs yield NO errors) passes.

Cross-cutting [§N] / [DN] checks:

- [§7.3, D17]: zero projection writes from validator code (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' adminme/lib/pack_registry/validator/` returns 0 matches). The validator is pure-functional over manifest + AST.
- [§8.2, D6]: no LLM SDK imports under `adminme/lib/pack_registry/validator/`.
- [§6 invariant 10]: the validator does NOT lower any sensitivity floor; the sensitivity-floor rule is enforced at `AdapterManifest.resolve_effective_sensitivity` (11-α). Validator's job is to surface manifest-vs-code drift on the OTHER fields.
- [§6.20]: the observation-mode-wrapping check IS the cross-cutting enforcement seam for [§6.20] at the pack level. Adapter packs that bypass observation get caught here.
- [D7]: the event-schema-registration check enforces "every emitted family is registered at v1+". Adapter packs that declare emission of an unregistered family fail validation.
- [D8]: correlation_id discipline unchanged — adapters propagate via `ctx.correlation_id_factory()`. Validator does not check this (correlation-id wiring is an at-runtime invariant the supervisor enforces).
- [PM-34]: 5 ABCs imported by name in the base-class conformance check. Naming + import path match the on-main 11-α surface.
- [PM-35]: `capabilities` walked as a list across all six checks; multi-capability shape exercised in the integration test.
- [UT-27]: `write_capabilities` per-operation membership enforced by `write_capabilities_check.py` end-to-end.
- [memo 04 §2.2]: Cat-D `owner_scope_overridability_at_install=False` rule enforced by `capability_runtime_coherence.py`.

---

## §10 Stop

Six install-time validator checks at `adminme/lib/pack_registry/validator/` shipped. Aggregator (`validate_adapter_pack`) callable as a standalone function. Backward-compatible with existing Gmail / Plaid / Reminders packs (advisory not error). The enforcement layer that 11-γ (CLI + authoring guide) wraps + Cohort C retrofit prompts close via inheritance updates is in place.

Ready for **prompt 11-γ** (CLI subcommand group: `adminme adapters list` / `validate` / `scaffold` with developer-mode gate per [D20] + authoring guide at `docs/adapter-authoring-guide.md`). The CLI's `validate` subcommand wraps `validate_adapter_pack` shipped here; the authoring guide documents the rules each check enforces.
