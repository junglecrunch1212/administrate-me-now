# Prompt 11r-a: Retrofit Gmail to CommunicationAdapter

**Phase:** Phase A — Cohort C retrofit (first of three) per `docs/08-architecture-amendment-adapter-rebase.md` §3.3.

**Branch:** `claude/11r-a-<auto>` (harness-assigned). PR base = `main` for all four c-bands per PR #106 sidecar. STACKED across c1→c4 per §8.5.

**Depends on:** Cohort B closed at main HEAD `fcb9d2ae` (PRs #118-#121 / #123-#126 / #128-#131). The 5 ABCs at `adminme/lib/adapters/`, the manifest schema at `adminme/lib/pack_registry/`, the 6-check validator at `adminme/lib/pack_registry/validator/`, and the CLI at `adminme/cli/commands/adapters.py` are all on main.

**Quality bar:** retrofit-tier prompt per `D-prompt-tier-and-pattern-index.md`. This prompt closes the FIRST of three `advisory_pending_retrofit` findings the 11-β validator surfaces on shipped v1 packs. The Gmail pack is the lightest retrofit (Cat-A, inbound-only today) and sets the **precedent** for 11r-b (Plaid) and 11r-c (Reminders — the heavy one with event-taxonomy migration). Pattern fidelity matters: the inheritance flip, the manifest capabilities block, and the `send` / `_on_inbound` stub pattern will be reused twice more.

**Stop condition:** `packs/adapters/messaging/gmail/pack.yaml` declares canonical `capabilities` block with Cat-A entry; `packs/adapters/messaging/gmail/adapter.py`'s `GmailAdapter` class inherits from `CommunicationAdapter`; `send()` and `_on_inbound()` abstract methods are implemented; `adminme adapters validate packs/adapters/messaging/gmail` exits 0 with zero findings (advisory_pending_retrofit gone). All existing Gmail tests still pass. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~60-80 minutes wall clock, 4 commits. ~$50-80 API spend (lighter than 11-α/β/γ; retrofit scope is narrow).

---

## §1 Behavioral grounding

You are closing the **first** of three `advisory_pending_retrofit` findings. Per `docs/08-architecture-amendment-adapter-rebase.md` §3.3, the shipped Gmail pack predates the framework and currently inherits nothing — `class GmailAdapter:` with no base class, implementing the simple `Adapter` Protocol via duck typing. The 11-β validator surfaces this as `advisory_pending_retrofit` on the `base_class_conformance` check; this prompt makes that finding go away by retrofitting Gmail onto `CommunicationAdapter`.

**Three surface changes** [memo 08 §3.3]:

- **Inheritance flip**: `class GmailAdapter:` → `class GmailAdapter(CommunicationAdapter):`. Import the ABC + the auxiliary dataclasses (`OutboundMessage`, `SendResult`, `InboundEnvelope`) from `adminme.lib.adapters`. The `CommunicationAdapter`'s `family: Literal["messaging"] = "messaging"` ClassVar default is preserved (Gmail is a messaging adapter); `sensitivity_floor` stays `"normal"` (existing default).

- **Manifest extension**: add to `pack.yaml`:
  ```yaml
  capabilities:
    - kind: Cat-A
      runtime: central
      event_families: [messaging.received]
  observation_mode_required: true
  sharing_model_discriminator: owner_member_id
  dedup_strategy: external_id
  ```
  The legacy top-level fields (`pack:`, `runtime:`, `family:`, `triggers:`, `depends_on:`, `events_emitted:`, `config:`) stay unchanged — the manifest parser ignores them; the new extension fields layer on top.

  Note: `event_families` declares ONLY `messaging.received` (Gmail's inbound-only surface today); `messaging.sent` is deferred to a Phase B prompt that ships real Gmail outbound. The framework supports declaring partial capabilities per [PM-35]; a Cat-A adapter that only ingests is legal as long as the manifest is honest about its surface.

- **Abstract method satisfaction**: `CommunicationAdapter` declares `send()` and `_on_inbound()` as `@abstractmethod`. The retrofit MUST implement both for `GmailAdapter()` to instantiate at all. Per memo 08 §3.3's "small surface change" framing, both methods ship as **honest stubs** that satisfy the framework contract WITHOUT pretending to do work the pack doesn't yet support:
  - `send(message: OutboundMessage) -> SendResult` routes through `adminme.lib.observation.outbound` (satisfies the 11-β `observation_mode_wrapping` check) with an `action_fn` that raises `NotImplementedError("Gmail outbound deferred to Phase B")`. The validator's AST walk finds the `outbound(...)` call and accepts the wiring; runtime callers get a clear error if they try to actually send.
  - `_on_inbound(envelope: InboundEnvelope) -> None` is a hook the polling loop calls per inbound message; the existing `_run_poll_cycle` already constructs equivalent envelope data and calls `_emit_messaging_received` directly. The hook adapts that flow: build an `InboundEnvelope`, call `self._on_inbound(envelope)`, then emit. The hook's default body in this retrofit is a no-op (the existing emit-the-event path stays where it is); subclasses overriding the hook can add behavior like in-memory dedup. The hook MUST exist as a method to satisfy the ABC's abstract surface.

**What does NOT change** [memo 08 §5]:

- The existing inbound polling logic — `_poll_loop`, `_run_poll_cycle`, the Gmail history-list incremental sync, the cursor advance milestones — all preserved. This pack works today; the retrofit is **inheritance + manifest only**, not a rewrite.
- The 3 cross-cutting `adapter.*` event schemas — unchanged.
- The `messaging.received` event schema — unchanged.
- The pack's existing tests — pass through the retrofit unchanged. New tests are additive.
- The supervisor's adapter-loading code path — unchanged (the simple `Adapter` Protocol that `CommunicationAdapter` extends is preserved per 11-α §1 design).

**Why this is the "lightest" of the three retrofits** [memo 08 §3.3]:

Gmail is Cat-A. Cat-A's manifest doesn't have `write_capabilities` (Cat-A's write surface is `send`, not a per-operation list per [UT-27]), so the retrofit avoids the per-operation method-presence enforcement that 11r-c (Reminders) will need. Gmail is `runtime: central` (the easy placement; no bridge or dual-deployment shape). Gmail's event schemas are already registered at v1 (`messaging.received` shipped with the pack); no schema migration. 11r-b (Plaid) is similar light scope (Cat-C, no write surface, no schema migration). 11r-c (Reminders) is the heavy one — schema taxonomy migration from `reminder.list_item.synced` → `list.*` / `list_item.*` per [D18], dual-deployment per [D22], asymmetric write_capabilities per memo 05 §2.3.

**Anti-vision halt** [VISION §"Small enough to inspect", DRIFT-CONTROL Job 4]: this is a retrofit, not a rewrite. The adapter.py diff MUST be ≤200 lines of additions (inheritance flip ~20 lines + send() stub ~40 lines + _on_inbound() stub ~30 lines + class attribute declarations ~20 lines + import lines ~5 lines = ~115 lines; reasonable). pack.yaml diff ≤30 lines. If the executor finds itself rewriting the existing _poll_loop or _emit_* helpers, halt and surface — the retrofit's scope is contract layer compliance, not internal refactoring.

**Mode:** Phase A. The retrofit preserves the live polling-loop semantics; existing integration tests with `requires_live_services` continue to gate the network-touching paths.

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for memo 05 / memo 08 ranges. Targeted reads only.

1. **`docs/08-architecture-amendment-adapter-rebase.md`** — full read (~15 KB). §3.3 (Cohort C retrofit scope for Gmail specifically), §5 (what's preserved across the retrofit).
2. **`prompts/_active/11-alpha-adapter-framework-abcs-and-manifest.md`** — full read (~36 KB). §7 (the 5 ABCs the retrofit inherits from; the manifest extension fields the new pack.yaml declares). Particularly the `CommunicationAdapter` contract spec.
3. **`prompts/_active/11-beta-adapter-framework-validator.md`** — full read (~36 KB). §7 (the 6 checks that must pass post-retrofit). Pay particular attention to `base_class_conformance` (the check this retrofit closes) and `observation_mode_wrapping` (which the `send` stub must satisfy via AST walk).
4. **`prompts/_active/11-gamma-adapter-framework-cli-and-guide.md`** — full read (~36 KB). §"Backward-compat with v1 manifests" — explains the current advisory_pending_retrofit shape and how the retrofit closes it.
5. **`docs/05-architecture-amendment-personal-data-layer.md`** — targeted reads:
   - §1.2 (Cat-A contract — what `CommunicationAdapter` declares)
   - §2.1 (five-category taxonomy; reaffirm Gmail = Cat-A central)
   - §2.4 (sensitivity_default + observation_mode_required as orthogonal fields)
6. **`adminme/lib/adapters/communication_base.py`** — full read. The ABC `GmailAdapter` will inherit from. `CommunicationAdapter` declares `family: Literal["messaging"] = "messaging"`, `sensitivity_floor: SensitivityFloor = "normal"`, and abstract `send` + `_on_inbound`. The dataclasses `OutboundMessage`, `SendResult`, `InboundEnvelope` live in this file.
7. **`adminme/lib/adapters/__init__.py`** — confirm the public re-export shape; the retrofit imports from `adminme.lib.adapters` (not `adminme.lib.adapters.communication_base`).
8. **`adminme/lib/pack_registry/models.py`** — confirm `AdapterManifest` field shape so the new pack.yaml extensions round-trip cleanly through `parse_adapter_manifest`.
9. **`adminme/lib/pack_registry/validator/__init__.py`** — confirm the `validate_adapter_pack(pack_dir, *, registry=None, projections_dir=None) -> ValidationResult` signature; the retrofit's tests assert `validate_adapter_pack(Path("packs/adapters/messaging/gmail"))` returns `ValidationResult` with `not result.has_errors()` AND `not any(f.check == "base_class_conformance" and "advisory_pending_retrofit" in f.message for f in result.advisories())` (the advisory is GONE post-retrofit).
10. **`adminme/lib/observation.py`** — full read. The `outbound` function the `send` stub routes through. Confirm the signature: `outbound(session, action, payload, action_fn, manager, event_log)` (or whatever it actually is on main — the c2 implementation must match).
11. **`adminme/adapters/base.py`** — full read. The simple `Adapter` Protocol that `CommunicationAdapter` extends; `AdapterContext` + `AdapterStatus` + `SensitivityFloor` are imported here. The existing GmailAdapter already uses these; the retrofit doesn't change their imports.
12. **`packs/adapters/messaging/gmail/pack.yaml`** — full read (small). The current legacy v1 manifest. The retrofit ADDS the extension fields below the existing block; nothing is removed.
13. **`packs/adapters/messaging/gmail/adapter.py`** — targeted reads:
    - Lines 1-100 (module docstring + imports + module-level constants — for context on what the retrofit changes vs preserves)
    - Lines 123-160 (the `GmailAdapter` class declaration + class attributes — what the retrofit replaces)
    - Lines 209-235 (`async start` — the retrofit doesn't change this but the `_on_inbound` hook integrates with the start flow)
    - Lines 235-280 (`_poll_loop` + `_run_poll_cycle` — the inbound flow the `_on_inbound` hook plugs into)
    - Lines 454-497 (`_emit_authenticated`, `_emit_messaging_received` — the existing emit path the retrofit hooks)
14. **`packs/adapters/messaging/gmail/tests/`** — list the existing tests; the retrofit's new tests are additive at `tests/unit/adapters/messaging/test_gmail_retrofit.py` (NOT in the pack-internal tests directory — those use the F-3 unique-basename rule for pack-load shapes; the retrofit's tests verify the framework-level inheritance, which is a different concern).
15. **`tests/integration/test_adapter_supervisor_gmail.py`** — full read. Verify the existing integration test still passes against the retrofitted GmailAdapter (the supervisor's adapter-loading code uses the simple Adapter Protocol surface which is preserved; the retrofit is ADDITIVE).
16. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads: §6.20 (observation mode wraps outbound — what the `send` stub satisfies via wiring), §6 invariant 10 (sensitivity floor — Gmail's `normal` floor is preserved), §7.3 (no projection direct writes — Gmail emits events only; preserved), §8 (no LLM SDKs — preserved).
17. **`docs/DECISIONS.md`** — read [D6], [D7], [D8], [D17], [D19], [D20], [D24], plus [D26] (rebase decision, if landed) and [PM-34], [PM-35].

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad confirming: (a) the existing inbound polling logic stays unchanged; (b) the new `capabilities` block declares only `messaging.received` (matching Gmail's current surface); (c) `send` stub routes through `adminme.lib.observation.outbound` with an `action_fn` that raises `NotImplementedError` for Phase A; (d) `_on_inbound` is a no-op hook that satisfies the abstract method; (e) the pack's existing tests continue to pass (the inheritance change preserves the simple `Adapter` Protocol's `start` / `stop` / `status` / `name` / `family` / `version` / `sensitivity_floor` surface); (f) the c4 integration test asserts the validator returns NO `advisory_pending_retrofit` finding post-retrofit.

---

## §3 Reconciliation notes (carry-forwards from Cohort B)

- **Path correction (Gate 2 verified)**: the retrofit targets `packs/adapters/messaging/gmail/adapter.py` (on-main location, unchanged since shipping). No new directories created.
- **F-3 HARD (10c-iii heritage)**: new pack-internal tests (if any) use unique basenames. This retrofit adds NEW pack-internal tests for the `send` stub + `_on_inbound` hook + ABC inheritance assertions; basename = `test_gmail_communication_adapter_retrofit_pack_internal.py` per F-3.
- **PM-27 SOFT (convention)**: the retrofit does NOT change `GmailAdapter.__init__`'s constructor-injection pattern. The existing `credentials_resolver` + `service_factory` callable-injection stays.
- **Observation-mode wrapping (11-β c3)**: the AST-matcher is pinned to `adminme.lib.observation.outbound` per 11-β c3's verification. The `send` stub MUST use this exact symbol — `from adminme.lib.observation import outbound` + `await outbound(...)` in the method body.
- **Stacked-merge anti-pattern fix (PR #106 — on main)**: c2/c3/c4 PRs open with `base=main` automatically. Ghost conflicts resolve via the Chrome MCP JS-regex pattern proven on 11c + 11-α + 11-β + 11-γ.
- **Constitution-sync rule (PR #97)**: this PR modifies a shipped pack (`packs/adapters/messaging/gmail/`) but does NOT modify any constitutional document. No cascade exemption needed.
- **Rebase carry-forward (memo 08)**: this prompt closes the FIRST of three Cohort C retrofits. After 11r-a merges, 11r-b (Plaid → InboundDataAdapter) is the next target, then 11r-c (Reminders → ExternalStateMirrorAdapter with schema migration).
- **Cohort B carry-forward**: 11-γ's `adminme adapters validate packs/adapters/messaging/gmail` is the primary verification command for c4; it MUST exit 0 with NO `advisory_pending_retrofit` finding on `base_class_conformance` after the retrofit.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `packs/adapters/messaging/gmail/pack.yaml` does NOT have a `capabilities:` block currently. **Verified at authorship (2026-05-17).**

A2. `packs/adapters/messaging/gmail/adapter.py` declares `class GmailAdapter:` with no base class. **Verified at authorship.**

A3. `adminme/lib/adapters/__init__.py` re-exports `CommunicationAdapter`, `OutboundMessage`, `SendResult`, `InboundEnvelope`. **Verified at authorship.**

A4. `adminme/lib/observation.py` exports a callable named `outbound`. **Pinned at 11-β c3.** The retrofit imports `from adminme.lib.observation import outbound` and calls `await outbound(...)` inside the `send` stub. Confirm the EXACT signature at c2 start (`grep -n '^def outbound\|^async def outbound' adminme/lib/observation.py`); pass through whatever positional/keyword args the on-main signature requires.

A5. `packs/adapters/messaging/gmail/tests/test_*.py` existing test file count is non-zero. **Verified at authorship.** Running the existing tests via `pytest packs/adapters/messaging/gmail/tests/ -v` MUST continue to pass after the retrofit.

A6. `messaging.received` event schema is registered at v1 in `adminme/events/schemas/ingest.py` (or wherever messaging schemas live). **Verified at authorship.** The retrofit's new `event_families: [messaging.received]` declaration round-trips cleanly through the validator's `event_schema_registration` check.

A7. `adminme/lib/pack_registry/validator/__init__.py` exports `validate_adapter_pack`. **Verified at authorship.**

A8. `pyproject.toml` is unchanged. No new dependency adds; the retrofit uses only existing imports.

---

## §5 Objective

Retrofit the shipped Gmail pack to inherit `CommunicationAdapter`, declare a canonical Cat-A `capabilities` block in `pack.yaml`, and implement honest stubs for the `send` + `_on_inbound` abstract methods that satisfy the framework contract without claiming Gmail-outbound functionality the pack doesn't yet have. Close the first of three `advisory_pending_retrofit` findings the 11-β validator surfaces. ≥15 new tests floor.

---

## §6 Out of scope

- **Real Gmail outbound** (`users.messages.send` API call) — Phase B prompt. The `send` stub raises `NotImplementedError` via the action_fn passed to `outbound()`. A future prompt adds the real send path + `messaging.sent` event schema + the second capability block in the manifest.
- **`messaging.sent` event schema registration** — deferred to the Phase B Gmail-outbound prompt. The retrofit declares ONLY `messaging.received` in `event_families`.
- **Refactoring Gmail's existing inbound polling logic** — preserved as-is. The retrofit adds the `_on_inbound` hook ALONGSIDE the existing emit path; the hook is a no-op for now.
- **Changing the supervisor's adapter-loading code** — unchanged. The simple `Adapter` Protocol surface (`name` / `family` / `version` / `sensitivity_floor` / `start` / `stop` / `status`) is preserved through inheritance from `CommunicationAdapter`.
- **Retrofitting other packs (Plaid / Reminders)** — separate prompts (11r-b and 11r-c per memo 08 §3.3).
- **Adding new event schemas** — `messaging.received` is already registered; no new schemas needed for this retrofit.
- **`pack.yaml` `triggers:` / `depends_on:` / `events_emitted:` / `runtime:` blocks** — preserved as-is. The retrofit ADDS extension fields; nothing is removed.

---

## §7 Deliverables

### Manifest extension at `packs/adapters/messaging/gmail/pack.yaml`

Append below the existing `config:` block:

```yaml
# Framework extension fields per memo 05 §3.1 / [PM-33] / [PM-35].
# Added by prompt 11r-a (Cohort C retrofit) — closes the
# advisory_pending_retrofit finding the 11-β validator surfaces on
# base_class_conformance.
capabilities:
  - kind: Cat-A
    runtime: central
    event_families: [messaging.received]
observation_mode_required: true
sharing_model_discriminator: owner_member_id
dedup_strategy: external_id
```

Note: `sensitivity_default` is NOT declared (defaults to `"normal"` per `AdapterManifest`, matching Gmail's existing `sensitivity_floor: normal`). `owner_scope_overridability_at_install` is NOT declared (default `True` for Cat-A is correct; only Cat-D pins it to `False`). `write_capabilities` is NOT declared (empty default is correct for Cat-A — the write surface is `send`, not a per-operation list).

### Adapter class retrofit at `packs/adapters/messaging/gmail/adapter.py`

Changes (in order of file flow):

- **New imports** (after the existing `from adminme.adapters.base import ...` block):
  ```python
  from adminme.lib.adapters import (
      CommunicationAdapter,
      InboundEnvelope,
      OutboundMessage,
      SendResult,
  )
  from adminme.lib.observation import outbound
  ```

- **Class declaration**: `class GmailAdapter:` → `class GmailAdapter(CommunicationAdapter):`. The class docstring gets a one-paragraph addition naming the retrofit and the deferred Phase B outbound work.

- **Class attributes**: add `capabilities: ClassVar[list[Any]] = [...]` matching the manifest's `capabilities` block (the class attribute mirrors the manifest for runtime introspection per memo 05 §3.2). Existing `family`, `version`, `sensitivity_floor` class attributes are preserved — the ABC's `family: Literal["messaging"] = "messaging"` default matches Gmail's existing `family = "messaging"`.

- **`async def send(self, message: OutboundMessage) -> SendResult`** (NEW, ~40 lines). Routes through `await outbound(action="messaging.send", action_fn=_gmail_send_deferred, ...)` where `_gmail_send_deferred` is a closure raising `NotImplementedError("Gmail outbound deferred to Phase B")`. Returns `SendResult(delivered=False, external_id=None, suppressed=<observation-mode-result>)`. The exact `outbound(...)` keyword args MUST match the on-main signature in `adminme/lib/observation.py` — verify at c2 start. The framework contract is honored: callers see a clear `NotImplementedError` rather than a silent no-op or a stale return value; the validator's AST walk finds the `outbound(...)` call and accepts the wiring.

- **`async def _on_inbound(self, envelope: InboundEnvelope) -> None`** (NEW, ~20 lines). No-op hook (`return None`) that satisfies the abstract method. The existing `_poll_loop` → `_emit_messaging_received` path stays in place; a future internal refactor can route through the hook for in-memory dedup or metrics. The hook's existence-and-callability is what the ABC contract requires today.

### Tests at `tests/unit/adapters/messaging/test_gmail_retrofit.py`

NEW file at the framework-tests location (not pack-internal). Covers:

- ABC inheritance: `assert issubclass(GmailAdapter, CommunicationAdapter)`.
- Manifest validation: `parse_adapter_manifest(Path("packs/adapters/messaging/gmail")).capabilities[0].kind == "Cat-A"`.
- Validator: `result = validate_adapter_pack(Path("packs/adapters/messaging/gmail"))`; `not result.has_errors()`; advisory_pending_retrofit is GONE from `result.advisories()`.
- `send` stub: instantiates `GmailAdapter` (with the existing constructor-injection fakes), calls `await adapter.send(OutboundMessage(...))`, asserts `NotImplementedError` raised OR `suppressed=True` if observation mode is on.
- `_on_inbound` stub: instantiates the adapter, calls `await adapter._on_inbound(InboundEnvelope(...))`, asserts returns `None` without raising.
- Backward compat: existing `GmailAdapter.start` / `stop` / `status` / `name` / `family` / `version` / `sensitivity_floor` surface preserved (each accessor returns the expected shape).

### Pack-internal tests (additive)

Append to existing pack-internal test file (or NEW file `tests/test_gmail_communication_adapter_retrofit_pack_internal.py` per F-3 unique-basename):

- `capabilities` class attribute matches the manifest's `capabilities` block.
- `pack.yaml` round-trips through `parse_adapter_manifest` cleanly.
- The 6 validator checks each return zero error findings against the retrofitted pack.

Test floor: ≥15 across the four commits (~6 framework + ~6 pack-internal + ~3 integration).

---

## §8 Commit structure (exactly four commits; messages exact)

Every commit message follows `phase 11r-a commit <N>: <imperative summary>` exactly. Branch strategy STACKED per §8.5; PR base = `main` for ALL FOUR c-bands per PR #106 sidecar.

### Commit 1 — Manifest extension (`pack.yaml` only)

**Files:** `packs/adapters/messaging/gmail/pack.yaml` (modified — append the 4 extension fields below the existing `config:` block).

**Files-to-read:** 4 (`adminme/lib/pack_registry/models.py` for the `AdapterManifest` field shape, memo 05 §3.1 for the manifest extension rules, the existing pack.yaml for the legacy v1 baseline, the split memo 08 §3.3 for Gmail-specific scope).

**Verification:**
```
poetry run python -c "from pathlib import Path; from adminme.lib.pack_registry import parse_adapter_manifest; m = parse_adapter_manifest(Path('packs/adapters/messaging/gmail')); print('Cat-A' in {c.kind for c in m.capabilities})"
git add packs/adapters/messaging/gmail/pack.yaml
git commit -m "phase 11r-a commit 1: Gmail pack.yaml capabilities + sharing model + dedup_strategy"
```

Expected: prints `True`. Manifest parses cleanly with the new `capabilities` block.

### Commit 2 — Class inheritance + abstract method stubs

**Files:** `packs/adapters/messaging/gmail/adapter.py` (modified — imports, class declaration, class attributes, `send` stub, `_on_inbound` stub).

**Files-to-read:** 5 (`adminme/lib/adapters/communication_base.py` for the ABC + dataclass surfaces, `adminme/lib/observation.py` for the `outbound` signature, existing `packs/adapters/messaging/gmail/adapter.py` for the methods being preserved, an existing pack-internal test for the inheritance assertion pattern, the existing tests in c1).

**Critical step at c2 start:** verify the actual signature of `adminme.lib.observation.outbound` via `grep -n '^def outbound\|^async def outbound' adminme/lib/observation.py | head -3`. The `send` stub's call to `outbound(...)` MUST match the on-main signature exactly; pass through whatever positional/keyword args the live signature requires. If `outbound` returns a different shape than the prompt's pseudo-code assumes (e.g. an object with `.suppressed` attribute vs a dict), adapt the stub's SendResult construction accordingly.

**Verification:**
```
poetry run python -c "from packs.adapters.messaging.gmail.adapter import GmailAdapter; from adminme.lib.adapters import CommunicationAdapter; assert issubclass(GmailAdapter, CommunicationAdapter); print('inheritance OK')"
poetry run mypy packs/adapters/messaging/gmail/ 2>&1 | tail -10
poetry run pytest packs/adapters/messaging/gmail/tests/ -v
git add packs/adapters/messaging/gmail/adapter.py
git commit -m "phase 11r-a commit 2: GmailAdapter inheritance + send/_on_inbound stubs"
```

Expected: mypy clean; existing pack-internal tests still pass; inheritance assertion passes.

### Commit 3 — Framework-level retrofit tests

**Files:** `tests/unit/adapters/__init__.py` (NEW if missing), `tests/unit/adapters/messaging/__init__.py` (NEW), `tests/unit/adapters/messaging/test_gmail_retrofit.py` (NEW).

**Files-to-read:** 4 (`adminme/lib/pack_registry/validator/__init__.py` for the `validate_adapter_pack` signature, existing pack-internal tests for the constructor-injection pattern (so the new tests can instantiate `GmailAdapter` without 1Password CLI), `tests/integration/test_adapter_supervisor_gmail.py` for the integration test pattern (the new framework tests are unit-level; integration verification lands in c4), the existing tests in c1+c2).

**Verification:**
```
poetry run mypy tests/unit/adapters/messaging/ 2>&1 | tail -10
poetry run pytest tests/unit/adapters/messaging/test_gmail_retrofit.py -v
# Validator check: advisory_pending_retrofit is GONE
poetry run python -c "from pathlib import Path; from adminme.lib.pack_registry.validator import validate_adapter_pack; r = validate_adapter_pack(Path('packs/adapters/messaging/gmail')); pending = [f for f in r.advisories() if f.check == 'base_class_conformance' and 'advisory_pending_retrofit' in f.message]; assert len(pending) == 0, f'still has advisory: {pending}'; assert not r.has_errors(), f'has errors: {r.errors()}'; print('validator clean')"
git add tests/unit/adapters/
git commit -m "phase 11r-a commit 3: framework-level Gmail retrofit tests"
```

Expected: mypy clean; ≥6 new framework tests pass; validator returns zero errors AND advisory_pending_retrofit is gone.

### Commit 4 — Integration tests + BUILD_LOG

**Files:** Update `tests/integration/test_adapter_supervisor_gmail.py` if needed to assert the retrofitted class shape (the supervisor still loads via simple Adapter Protocol, so likely no change needed; verify). `docs/build_log.md` (append the 11r-a entry).

**Files-to-read:** 4 (`tests/integration/test_adapter_supervisor_gmail.py` for the existing integration test surface, `docs/build_log.md` tail for the append point, memo 08 §3.3 for the canonical Gmail retrofit summary the BUILD_LOG cites, `adminme/cli/commands/adapters.py` for the `validate` CLI command (the BUILD_LOG mentions running `adminme adapters validate` against Gmail to confirm advisory cleared)).

**Build_log Evidence template:** Append AFTER the most recent prior entry (11-γ). Header: `### Prompt 11r-a — Retrofit Gmail to CommunicationAdapter`. Cite: the `pack.yaml` capabilities block; the inheritance flip; the `send` + `_on_inbound` stubs; the AST-walk-compatible `outbound` wiring; the validator-clean check; the deferred Phase B outbound work; carry-forward into 11r-b (Plaid retrofit) and 11r-c (Reminders retrofit with schema migration).

**Verification:**
```
poetry run ruff check packs/adapters/messaging/gmail/ tests/unit/adapters/messaging/ 2>&1 | tail -10
poetry run mypy adminme/ packs/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
# CLI smoke test: validate via the adminme adapters CLI
poetry run adminme adapters validate packs/adapters/messaging/gmail
git add docs/build_log.md tests/integration/test_adapter_supervisor_gmail.py
git commit -m "phase 11r-a commit 4: integration test refresh + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+15; `verify_invariants.sh` exit 0; `adminme adapters validate` exits 0 with no errors AND no advisory_pending_retrofit findings.

**PR open:** Title `phase 11r-a: Retrofit Gmail to CommunicationAdapter (closes 1 of 3 advisory_pending_retrofit findings)`. Body: top line "First of three Cohort C retrofit prompts per `docs/08-architecture-amendment-adapter-rebase.md` §3.3. Retrofits the shipped Gmail pack to inherit `CommunicationAdapter`, declare a canonical Cat-A `capabilities` block, and ship honest stubs for `send` + `_on_inbound` that satisfy the framework contract. Real Gmail outbound deferred to a Phase B prompt." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` is `main` per PR #106 sidecar. Pre-flight stack-integrity check on c4 expects 3 prior commits above `origin/main` (c1+c2+c3).

Ghost conflicts on c2/c3/c4 (squash-merge artifacts) — resolve via the Chrome MCP JS-regex pattern proven on 11c + 11-α + 11-β + 11-γ (Partner drives per the precedent).

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥15 new tests passing; ruff clean on `packs/adapters/messaging/gmail/` / `tests/unit/adapters/messaging/`; existing pack-internal tests still pass; `scripts/verify_invariants.sh` exit 0; PR opens against `main`; `adminme adapters validate packs/adapters/messaging/gmail` exits 0 with NO errors AND NO `advisory_pending_retrofit` finding.

Cross-cutting [§N] / [DN] checks:

- [§7.3, D17]: zero projection writes from the retrofitted adapter (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' packs/adapters/messaging/gmail/` returns 0). Gmail emits events only; unchanged from pre-retrofit.
- [§8.2, D6]: no LLM SDK imports under `packs/adapters/messaging/gmail/`. Unchanged from pre-retrofit.
- [§6 invariant 10]: `sensitivity_floor: "normal"` preserved on `GmailAdapter`; `sensitivity_default: "normal"` (default) on the manifest; `resolve_effective_sensitivity` returns `"normal"` for the Cat-A capability.
- [§6.20]: the `send` stub routes through `adminme.lib.observation.outbound` (the validator's `observation_mode_wrapping` AST walk finds the call); `observation_mode_required: true` declared in the manifest.
- [D7]: `messaging.received` event schema at v1 unchanged; no new schemas registered.
- [D8]: `correlation_id` propagation discipline unchanged — Gmail's existing `_emit_messaging_received` continues to set `correlation_id` per envelope.
- [PM-34]: GmailAdapter inherits from `CommunicationAdapter` (one of the 5 ABCs at `adminme/lib/adapters/`); the validator's `base_class_conformance` check passes without the advisory.
- [PM-35]: `capabilities` is declared as a list (one entry today; future Phase B prompts can add `messaging.sent` capability for the outbound surface).
- [UT-27]: `write_capabilities` is empty for Cat-A (the write surface is `send`, not per-operation); the validator's `write_capabilities` check passes.

---

## §10 Stop

Gmail pack retrofitted onto `CommunicationAdapter`. Canonical Cat-A `capabilities` block declared in `pack.yaml`. Honest stubs for `send` + `_on_inbound` shipped. `adminme adapters validate` returns zero errors with NO `advisory_pending_retrofit` finding. First of three Cohort C retrofits closed.

Ready for **prompt 11r-b** (Plaid → InboundDataAdapter — second Cohort C retrofit per memo 08 §3.3). Plaid is Cat-C (read-only, no write surface, no `send` method to stub) — even lighter scope than Gmail. After 11r-b: **prompt 11r-c** (Reminders → ExternalStateMirrorAdapter — the heavy one with `list.*` / `list_item.*` schema taxonomy migration per [D18] and dual-deployment per [D22]).
