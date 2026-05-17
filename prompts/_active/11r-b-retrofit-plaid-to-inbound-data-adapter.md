# Prompt 11r-b: Retrofit Plaid to InboundDataAdapter

**Phase:** Phase A — Cohort C retrofit (second of three) per `docs/08-architecture-amendment-adapter-rebase.md` §3.3.

**Branch:** `claude/11r-b-<auto>` (harness-assigned). PR base = `main` for all four c-bands per PR #106 sidecar. STACKED across c1→c4 per §8.5.

**Depends on:** 11r-a merged (PRs #133-#136). Main HEAD `d7b2d4cb` at authorship (post partner-state-snapshot PR #137). The 5 ABCs at `adminme/lib/adapters/`, the manifest schema + 6-check validator at `adminme/lib/pack_registry/`, the CLI at `adminme/cli/commands/adapters.py`, and the Cohort C retrofit pattern established by 11r-a are all on main.

**Quality bar:** retrofit-tier prompt per `D-prompt-tier-and-pattern-index.md`. This prompt closes the SECOND of three `advisory_pending_retrofit` findings the 11-β validator surfaces. **Even lighter scope than 11r-a:** Plaid is Cat-C (read-only); `InboundDataAdapter` has NO `send` and NO `_on_inbound` abstract methods — just `start` / `stop` / `status` which Plaid already implements. The retrofit is **pure inheritance + manifest extension**, no stub methods.

**Stop condition:** `packs/adapters/financial/plaid/pack.yaml` declares canonical Cat-C `capabilities` block; `packs/adapters/financial/plaid/adapter.py`'s `PlaidAdapter` class inherits from `InboundDataAdapter` with `family: ClassVar[Literal["financial"]] = "financial"` and a `capabilities` ClassVar mirroring the manifest; `adminme adapters validate packs/adapters/financial/plaid` exits 0 with zero findings (advisory_pending_retrofit gone). All existing Plaid tests still pass. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~45-60 minutes wall clock, 4 commits. ~$40-60 API spend (lightest of the three retrofits; no stub methods to author).

---

## §1 Behavioral grounding

You are closing the **second** of three `advisory_pending_retrofit` findings. Per `docs/08-architecture-amendment-adapter-rebase.md` §3.3, the shipped Plaid pack predates the framework — `class PlaidAdapter:` with no base class, implementing the simple `Adapter` Protocol via duck typing. The 11-β validator surfaces this as `advisory_pending_retrofit` on the `base_class_conformance` check; this prompt makes that finding go away by retrofitting Plaid onto `InboundDataAdapter`.

**Why Plaid is the lightest of the three retrofits** [memo 08 §3.3]: Plaid is **Cat-C — read-only ingestion**. The `InboundDataAdapter` ABC was designed to be **the discriminator from Cat-B**: it has NO write surface. The abstract method set is literally `start` / `stop` / `status` — the same three that the simple `Adapter` Protocol already declares. No `send` (Cat-A surface). No `_on_inbound` hook (also Cat-A surface). No `apply_write` (Cat-B surface). No `execute_action` (Cat-E surface). Plaid's existing methods cover the entire abstract surface; the retrofit adds NO stub methods.

**Two surface changes** [memo 08 §3.3]:

- **Inheritance flip**: `class PlaidAdapter:` → `class PlaidAdapter(InboundDataAdapter):`. Import the ABC from `adminme.lib.adapters`. Add class attributes the ABC requires:
  - `family: ClassVar[Literal["financial"]] = "financial"` (the ABC's literal-constrained family — Plaid is financial; the legacy `family: financial` in `pack.yaml`'s `pack:` block mirrors this but is informational at the manifest layer; the ClassVar at the adapter-class layer is what the ABC enforces).
  - `capabilities: ClassVar[list[Any]] = [...]` (mirrors the manifest's `capabilities` block at the class layer for runtime introspection per memo 05 §3.2).
  - Existing `sensitivity_floor: SensitivityFloor = "privileged"` preserved as-is — the ABC's default is `"normal"` but the subclass override to `"privileged"` is legal and the [§6 invariant 10] floor-cannot-be-lowered rule applies.
  - Existing `name`, `version` class attrs preserved.

- **Manifest extension**: add to `pack.yaml`:
  ```yaml
  capabilities:
    - kind: Cat-C
      runtime: central
      event_families: [money_flow.plaid_synced]
  sensitivity_default: privileged
  sharing_model_discriminator: owner_member_id
  dedup_strategy:
    kind: external_id
  ```
  Legacy top-level + nested fields (`pack:` block, `sensitivity_floor`, `triggers`, `depends_on`, `config`, `documentation`) stay unchanged. The new extension fields layer on top per memo 05 §3.1.

  Note: `write_capabilities` is NOT declared — Cat-C has no write surface; declaring it would fail the 11-β validator's `write_capabilities_check` per [UT-27]. `observation_mode_required` is NOT declared — Cat-C is read-only with no outbound side effect, so the Cat-E observation rule doesn't apply (default `False` is correct). `owner_scope_overridability_at_install` is NOT declared (default `True` is correct — only Cat-D pins it to `False`).

**What does NOT change** [memo 08 §5]:

- Plaid's existing ingestion logic — `start` / `stop` / `status` / `webhook_handler` / `_sync_transactions` / `_fetch_institution_health` / the error classification hierarchy (`PlaidSDKError` + subclasses) — all preserved unchanged.
- The 3 cross-cutting `adapter.*` event schemas — unchanged.
- The `money_flow.plaid_synced` event schema at v1 — unchanged.
- The pack's existing tests — pass through the retrofit unchanged. New tests are additive.
- The supervisor's adapter-loading code path — unchanged (simple `Adapter` Protocol preserved through inheritance from `InboundDataAdapter`).
- The `sensitivity_floor: privileged` triple-lock (class attribute + pack.yaml hardcoded + `config.schema.json` const) — unchanged.

**Why this is even lighter than 11r-a** [memo 08 §3.3 + the InboundDataAdapter docstring]:

- 11r-a added `send` + `_on_inbound` stubs because `CommunicationAdapter` has those abstract methods. Plaid does NOT need any stub methods — `InboundDataAdapter`'s abstract surface is `start` / `stop` / `status`, which Plaid already implements.
- 11r-a's `send` stub had to route through `adminme.lib.observation.outbound` to satisfy the validator's `observation_mode_wrapping` check. Plaid is Cat-C; the validator's observation-mode-wrapping check **skips Cat-C** (no outbound surface to wrap).
- 11r-a's manifest declared `observation_mode_required: true`. Plaid's does NOT — Cat-C is read-only.

**Anti-vision halt** [VISION §"Small enough to inspect", DRIFT-CONTROL Job 4]: this is the LIGHTEST retrofit. adapter.py diff MUST be ≤50 lines of additions (3-5 import lines + class declaration + 2 ClassVars + comment block ~30 lines). pack.yaml diff ≤15 lines. If the executor finds itself adding ANY stub methods, refactoring `_sync_transactions`, or touching the webhook handler, halt and surface — that's out-of-scope drift.

**Mode:** Phase A. The retrofit preserves all live ingestion semantics; existing integration tests with `requires_live_services` continue to gate the Plaid-API-touching paths.

---

## §2 Read first (required, in order)

**Source-of-truth discipline:** every file below MUST be fetched fresh via `mcp__github__get_file_contents(ref="main")` before consulting any pinned version. Pinned content is a snapshot; live main is authoritative. Use `sed -n '<start>,<end>p'` for memo 05 / memo 08 ranges (targeted reads only).

1. **`docs/08-architecture-amendment-adapter-rebase.md`** — full read (~15 KB). §3.3 (Cohort C Plaid retrofit specifically), §5 (what's preserved).
2. **`prompts/_active/11r-a-retrofit-gmail-to-communication-adapter.md`** — full read (~33 KB). **The Cohort C precedent.** This is the v4 refactor template. Note where it diverges from 11r-b: 11r-a added `send` + `_on_inbound` stubs; 11r-b does not. 11r-a declared `observation_mode_required: true`; 11r-b does not. Otherwise the structure is identical.
3. **`prompts/_active/11-alpha-adapter-framework-abcs-and-manifest.md`** — full read (~36 KB). §7 — the manifest extension fields the new pack.yaml declares.
4. **`prompts/_active/11-beta-adapter-framework-validator.md`** — full read (~36 KB). §7 — the 6 checks. Pay particular attention to `base_class_conformance` (the check this retrofit closes) and `event_schema_registration` (verifies `money_flow.plaid_synced` is registered, which it already is).
5. **`docs/05-architecture-amendment-personal-data-layer.md`** — targeted reads:
   - §1.2 (Cat-C contract — what `InboundDataAdapter` declares)
   - §2.1 (five-category taxonomy; reaffirm Plaid = Cat-C central)
   - §2.3 (`write_capabilities` per-operation per [UT-27]; this is what Cat-C does NOT have)
6. **`adminme/lib/adapters/inbound_data_base.py`** — full read. The ABC `PlaidAdapter` will inherit from. Note: `family: ClassVar[Literal["financial", "iot", "telemetry"]]`, abstract methods are `start` / `stop` / `status` ONLY (no write surface, no observation wrap), NO `write_capabilities` ClassVar on Cat-C by design.
7. **`adminme/lib/adapters/__init__.py`** — confirm `InboundDataAdapter` is re-exported (it is, per 11-α c1).
8. **`adminme/lib/pack_registry/models.py`** — `AdapterManifest` field shape; the new pack.yaml extensions round-trip through `parse_adapter_manifest`.
9. **`adminme/lib/pack_registry/validator/__init__.py`** — `validate_adapter_pack(pack_dir, *, registry=None, projections_dir=None) -> ValidationResult` signature. The retrofit's tests assert validator-clean post-retrofit (no errors AND no `advisory_pending_retrofit` finding on `base_class_conformance`).
10. **`adminme/adapters/base.py`** — full read. The simple `Adapter` Protocol that `InboundDataAdapter` extends; `AdapterContext` + `AdapterStatus` + `SensitivityFloor` imports preserved.
11. **`packs/adapters/financial/plaid/pack.yaml`** — full read (~2.5 KB). The current legacy v1 manifest. The retrofit ADDS the extension fields below the existing block; nothing is removed.
12. **`packs/adapters/financial/plaid/adapter.py`** — targeted reads:
    - Lines 1-80 (module docstring + imports + module-level constants)
    - Lines 122-150 (the `PlaidSDKError` hierarchy — context only, NOT modified)
    - Lines 206-250 (the `PlaidAdapter` class declaration + class attributes — what the retrofit modifies)
    - Lines 248-340 (`async start` / `async stop` / `status` — the methods that satisfy the InboundDataAdapter contract; verify signatures match)
13. **`packs/adapters/financial/plaid/tests/`** — list existing tests; the retrofit's new tests are additive at `tests/unit/adapters/financial/test_plaid_retrofit.py` (framework-level, NOT pack-internal).
14. **`tests/integration/test_adapter_plaid_supervisor.py`** — full read. Verify the existing integration test still passes against the retrofitted PlaidAdapter (the supervisor's adapter-loading code uses the simple `Adapter` Protocol surface which is preserved; the retrofit is ADDITIVE).
15. **`tests/unit/adapters/messaging/test_gmail_retrofit.py`** — full read. **Test pattern precedent from 11r-a.** The new framework tests at `tests/unit/adapters/financial/test_plaid_retrofit.py` mirror this shape (ABC inheritance assertion, manifest validation, validator-clean, backward-compat surface preservation) — but Plaid skips the `send` stub test + `_on_inbound` hook test (those methods don't exist on InboundDataAdapter).
16. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads: §6 invariant 10 (sensitivity floor; Plaid's `privileged` floor preserved), §7.3 (no projection direct writes), §8 (no LLM SDKs).
17. **`docs/DECISIONS.md`** — read [D6], [D7], [D8], [D17], [D19], [D26] (rebase), [PM-34], [PM-35].

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad confirming: (a) Plaid's existing ingestion logic stays unchanged; (b) the new `capabilities` block declares only `money_flow.plaid_synced` (Plaid's single emit family today); (c) no stub methods are added (InboundDataAdapter's abstract surface is `start`/`stop`/`status` which Plaid already implements); (d) the `sensitivity_floor: privileged` triple-lock is preserved across all three layers; (e) the c4 integration test asserts the validator returns NO `advisory_pending_retrofit` finding post-retrofit; (f) NO `observation_mode_required: true` in the manifest (Cat-C is read-only); (g) NO `write_capabilities` in the manifest (Cat-C has no write surface per UT-27).

---

## §3 Reconciliation notes (carry-forwards from 11r-a)

- **Path correction (Gate 2 verified)**: the retrofit targets `packs/adapters/financial/plaid/adapter.py` and `packs/adapters/financial/plaid/pack.yaml` (on-main locations, unchanged since shipping). No new directories created.
- **F-3 HARD (10c-iii heritage)**: any new pack-internal tests use unique basenames. This retrofit's pack-internal tests use `tests/test_plaid_inbound_data_adapter_retrofit_pack_internal.py` (basename distinct from 11r-a's `test_gmail_communication_adapter_retrofit_pack_internal.py`).
- **PM-27 SOFT (convention)**: the retrofit does NOT change `PlaidAdapter.__init__`'s constructor-injection pattern. Existing dependency-injection callables (webhook signature verifier, Plaid client factory) stay.
- **11r-a precedent — manifest dict shape**: `dedup_strategy` uses the dict shape `{kind: external_id}` per the on-main `AdapterManifest` model (`dict[str, Any] | None`). The Gate 2 verification at c1 start confirms this; do not use a flat-string shape.
- **Stacked-merge anti-pattern fix (PR #106 — on main)**: c2/c3/c4 PRs open with `base=main` automatically.
- **11r-a precedent — no ghost conflicts**: 11r-a merged with ZERO ghost conflicts because c-bands had non-overlapping file scopes (c1=pack.yaml, c2=adapter.py, c3=new tests, c4=integration test refresh + BUILD_LOG). 11r-b mirrors this; expect clean MCP-direct merges. Only drive Chrome MCP if `mergeable_state` reports "dirty".
- **Constitution-sync rule (PR #97)**: this PR modifies a shipped pack but does NOT modify any constitutional document. No cascade exemption needed.
- **Rebase carry-forward (memo 08)**: this prompt closes the SECOND of three Cohort C retrofits. After 11r-b merges, 11r-c (Reminders → ExternalStateMirrorAdapter, the heavy one with `list.*` / `list_item.*` schema migration) is the final target.
- **Cohort B carry-forward**: 11-γ's `adminme adapters validate packs/adapters/financial/plaid` is the primary verification command for c4; it MUST exit 0 with NO `advisory_pending_retrofit` finding on `base_class_conformance` post-retrofit.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `packs/adapters/financial/plaid/pack.yaml` does NOT have a `capabilities:` block currently. **Verified at authorship (2026-05-17, main HEAD `d7b2d4cb`).**

A2. `packs/adapters/financial/plaid/adapter.py` declares `class PlaidAdapter:` with no base class (line 206). **Verified at authorship.**

A3. `adminme/lib/adapters/__init__.py` re-exports `InboundDataAdapter`. **Verified at authorship.**

A4. `adminme/lib/adapters/inbound_data_base.py` declares `family: ClassVar[Literal["financial", "iot", "telemetry"]]` and abstract methods are `start` / `stop` / `status` ONLY (no `send`, no `_on_inbound`, no write surface). **Verified at authorship.**

A5. `packs/adapters/financial/plaid/tests/test_*.py` existing test file count is non-zero. **Verified at authorship.** Running existing tests via `pytest packs/adapters/financial/plaid/tests/ -v` MUST continue to pass after the retrofit.

A6. `money_flow.plaid_synced` event schema is registered at v1 in `adminme/events/schemas/financial.py`. **Verified at authorship.** The retrofit's `event_families: [money_flow.plaid_synced]` round-trips cleanly through the 11-β validator's `event_schema_registration` check.

A7. `adminme/lib/pack_registry/validator/__init__.py` exports `validate_adapter_pack`. **Verified at authorship.**

A8. `pyproject.toml` is unchanged. No new dependency adds; the retrofit uses only existing imports.

---

## §5 Objective

Retrofit the shipped Plaid pack to inherit `InboundDataAdapter`, declare a canonical Cat-C `capabilities` block in `pack.yaml`, and add the required ABC class attributes (`family: Literal["financial"]`, `capabilities`). Close the second of three `advisory_pending_retrofit` findings the 11-β validator surfaces. NO stub methods (Cat-C's abstract surface is `start`/`stop`/`status` which Plaid already implements). ≥12 new tests floor (lower than 11r-a's 15 because no stub-method tests needed).

---

## §6 Out of scope

- **New methods on PlaidAdapter** — Cat-C requires no stubs; the existing `start`/`stop`/`status` cover the abstract surface. Do NOT add a `send` method (that's Cat-A) or `apply_write` (Cat-B) or `execute_action` (Cat-E).
- **Refactoring Plaid's existing ingestion logic** — preserved as-is. The retrofit adds ABC inheritance ALONGSIDE the existing methods; no internal changes.
- **Webhook handler changes** — `webhook_handler` stays as-is. The InboundDataAdapter ABC does NOT specify a webhook handler shape (webhooks are a pack-internal concern, not a framework contract).
- **Adding `messaging.sent` or any new event schema** — Plaid emits ONE family (`money_flow.plaid_synced`); the manifest declares it. No new schemas.
- **Changing the supervisor's adapter-loading code** — unchanged.
- **`pack.yaml` `triggers:` / `depends_on:` / `config:` / `documentation:` blocks** — preserved as-is. The retrofit ADDS extension fields; nothing is removed.
- **Retrofitting other packs (Gmail, Reminders)** — separate prompts (Gmail done via 11r-a; Reminders awaiting 11r-c).
- **Cohort D's canonical 11a** (Gmail + Plaid with the typed ABCs + real Gmail outbound) — different prompt, deferred per memo 08 §3.4.

---

## §7 Deliverables

### Manifest extension at `packs/adapters/financial/plaid/pack.yaml`

Append below the existing `documentation:` block:

```yaml
# Framework extension fields per memo 05 §3.1 / [PM-33] / [PM-35].
# Added by prompt 11r-b (Cohort C retrofit) — closes the
# advisory_pending_retrofit finding the 11-β validator surfaces on
# base_class_conformance. `dedup_strategy` is declared as a mapping per
# the on-main AdapterManifest model (`dict[str, Any] | None`); the
# `external_id` kind matches Plaid's `(plaid_item_id, sync_cursor)`
# stability semantics — same /transactions/sync cycle produces the same
# external_id, so re-delivery on supervisor restart is dedup-safe.
capabilities:
  - kind: Cat-C
    runtime: central
    event_families: [money_flow.plaid_synced]
sensitivity_default: privileged
sharing_model_discriminator: owner_member_id
dedup_strategy:
  kind: external_id
```

Note: `write_capabilities` NOT declared (empty default is correct for Cat-C — no write surface per [UT-27]; declaring would fail the validator's `write_capabilities_check`). `observation_mode_required` NOT declared (default `False` is correct for Cat-C — read-only, no outbound side effect). `owner_scope_overridability_at_install` NOT declared (default `True` is correct — only Cat-D pins to `False`).

### Adapter class retrofit at `packs/adapters/financial/plaid/adapter.py`

Changes (in order of file flow):

- **New imports** (after the existing `from adminme.adapters.base import ...` block):
  ```python
  from typing import ClassVar, Literal  # if not already imported
  from adminme.lib.adapters import InboundDataAdapter
  ```

- **Class declaration**: `class PlaidAdapter:` → `class PlaidAdapter(InboundDataAdapter):`. Add a one-paragraph docstring addition naming the retrofit (closes Cohort C step 2; inherits the Cat-C contract; existing ingestion logic preserved).

- **Class attributes** (add to the class body, near the existing `sensitivity_floor`):
  ```python
  family: ClassVar[Literal["financial"]] = "financial"
  
  capabilities: ClassVar[list[Any]] = [
      {
          "kind": "Cat-C",
          "runtime": "central",
          "event_families": ["money_flow.plaid_synced"],
      }
  ]
  ```
  Existing `sensitivity_floor: SensitivityFloor = "privileged"` preserved (the InboundDataAdapter ABC's default is `"normal"` but subclasses can override upward per [§6 invariant 10]). Existing `name`, `version` class attrs preserved if present.

- **No method changes** — `start`, `stop`, `status`, `webhook_handler`, `_sync_transactions`, `_fetch_institution_health`, error-classification helpers all stay byte-for-byte identical.

### Tests at `tests/unit/adapters/financial/test_plaid_retrofit.py`

NEW file at the framework-tests location (not pack-internal). Tests directory `tests/unit/adapters/financial/` is NEW (sibling to `tests/unit/adapters/messaging/` created by 11r-a). Covers:

- ABC inheritance: `assert issubclass(PlaidAdapter, InboundDataAdapter)`.
- Manifest validation: `parse_adapter_manifest(Path("packs/adapters/financial/plaid")).capabilities[0].kind == "Cat-C"`.
- Validator: `result = validate_adapter_pack(Path("packs/adapters/financial/plaid"))`; `not result.has_errors()`; advisory_pending_retrofit is GONE from `result.advisories()`.
- Class attribute literal: `assert PlaidAdapter.family == "financial"`; `assert isinstance(PlaidAdapter.capabilities, list)`; `assert PlaidAdapter.capabilities[0]["kind"] == "Cat-C"`.
- No write surface: `assert not hasattr(PlaidAdapter, "send")`; `assert not hasattr(PlaidAdapter, "apply_write")`; `assert not hasattr(PlaidAdapter, "execute_action")` (Cat-C discriminator from Cat-A/B/E).
- Sensitivity floor preserved: `assert PlaidAdapter.sensitivity_floor == "privileged"` (triple-lock at class layer; matches `sensitivity_default: privileged` in manifest).
- Backward compat: existing `start` / `stop` / `status` callable surface preserved (each accessor returns the expected shape with the existing constructor-injection fakes).

### Pack-internal tests (additive)

NEW file at `packs/adapters/financial/plaid/tests/test_plaid_inbound_data_adapter_retrofit_pack_internal.py` per F-3 unique-basename:

- `capabilities` class attribute matches the manifest's `capabilities` block.
- `pack.yaml` round-trips through `parse_adapter_manifest` cleanly with `capabilities[0].kind == "Cat-C"`.
- The 6 validator checks each return zero error findings against the retrofitted pack.

Test floor: ≥12 across the four commits (~6 framework + ~3 pack-internal + ~3 integration).

---

## §8 Commit structure (exactly four commits; messages exact)

Every commit message follows `phase 11r-b commit <N>: <imperative summary>` exactly. Branch strategy STACKED per §8.5; PR base = `main` for ALL FOUR c-bands per PR #106 sidecar.

### Commit 1 — Manifest extension (`pack.yaml` only)

**Files:** `packs/adapters/financial/plaid/pack.yaml` (modified — append the 4 extension fields below the existing `documentation:` block).

**Files-to-read:** 4 (`adminme/lib/pack_registry/models.py` for the `AdapterManifest` field shape, memo 05 §3.1 for the manifest extension rules, the existing pack.yaml for the legacy v1 baseline, the 11r-a pack.yaml on main as a precedent for the dict-shaped `dedup_strategy`).

**Verification:**
```
poetry run python -c "from pathlib import Path; from adminme.lib.pack_registry import parse_adapter_manifest; m = parse_adapter_manifest(Path('packs/adapters/financial/plaid')); print('Cat-C' in {c.kind for c in m.capabilities})"
git add packs/adapters/financial/plaid/pack.yaml
git commit -m "phase 11r-b commit 1: Plaid pack.yaml capabilities + sharing model + dedup_strategy"
```

Expected: prints `True`. Manifest parses cleanly with the new `capabilities` block.

### Commit 2 — Class inheritance + ABC class attributes

**Files:** `packs/adapters/financial/plaid/adapter.py` (modified — imports, class declaration, class attributes only; NO method changes).

**Files-to-read:** 5 (`adminme/lib/adapters/inbound_data_base.py` for the ABC contract, existing `packs/adapters/financial/plaid/adapter.py` for the methods being preserved, 11r-a's `packs/adapters/messaging/gmail/adapter.py` on main as the inheritance-flip precedent, an existing pack-internal test for the inheritance assertion pattern, the existing tests in c1).

**Anti-vision check at c2 start:** the diff MUST be ≤50 lines of additions to `adapter.py`. If you find yourself adding ANY methods (stubs or otherwise), HALT — Cat-C requires no stubs; the existing `start`/`stop`/`status` cover the abstract surface.

**Verification:**
```
poetry run python -c "from packs.adapters.financial.plaid.adapter import PlaidAdapter; from adminme.lib.adapters import InboundDataAdapter; assert issubclass(PlaidAdapter, InboundDataAdapter); assert PlaidAdapter.family == 'financial'; assert PlaidAdapter.capabilities[0]['kind'] == 'Cat-C'; print('inheritance + class attrs OK')"
poetry run mypy packs/adapters/financial/plaid/ 2>&1 | tail -10
poetry run pytest packs/adapters/financial/plaid/tests/ -v
git add packs/adapters/financial/plaid/adapter.py
git commit -m "phase 11r-b commit 2: PlaidAdapter inheritance + family + capabilities ClassVars"
```

Expected: mypy clean; existing pack-internal tests still pass; inheritance assertion passes; class attribute assertions pass.

### Commit 3 — Framework-level retrofit tests

**Files:** `tests/unit/adapters/financial/__init__.py` (NEW), `tests/unit/adapters/financial/test_plaid_retrofit.py` (NEW).

**Files-to-read:** 4 (`adminme/lib/pack_registry/validator/__init__.py` for the `validate_adapter_pack` signature, `tests/unit/adapters/messaging/test_gmail_retrofit.py` for the 11r-a test pattern precedent, existing pack-internal tests for the constructor-injection pattern (so the new tests can instantiate `PlaidAdapter` without live Plaid SDK), the existing tests in c1+c2).

**Verification:**
```
poetry run mypy tests/unit/adapters/financial/ 2>&1 | tail -10
poetry run pytest tests/unit/adapters/financial/test_plaid_retrofit.py -v
# Validator check: advisory_pending_retrofit is GONE
poetry run python -c "from pathlib import Path; from adminme.lib.pack_registry.validator import validate_adapter_pack; r = validate_adapter_pack(Path('packs/adapters/financial/plaid')); pending = [f for f in r.advisories() if f.check == 'base_class_conformance' and 'advisory_pending_retrofit' in f.message]; assert len(pending) == 0, f'still has advisory: {pending}'; assert not r.has_errors(), f'has errors: {r.errors()}'; print('validator clean')"
git add tests/unit/adapters/financial/
git commit -m "phase 11r-b commit 3: framework-level Plaid retrofit tests"
```

Expected: mypy clean; ≥6 new framework tests pass; validator returns zero errors AND advisory_pending_retrofit is gone.

### Commit 4 — Integration tests + BUILD_LOG

**Files:** Update `tests/integration/test_adapter_plaid_supervisor.py` if needed to assert the retrofitted class shape (the supervisor still loads via simple Adapter Protocol, so likely no change needed; verify). NEW `packs/adapters/financial/plaid/tests/test_plaid_inbound_data_adapter_retrofit_pack_internal.py` (per F-3 unique-basename, additive pack-internal tests). `docs/build_log.md` (append the 11r-b entry).

**Files-to-read:** 4 (`tests/integration/test_adapter_plaid_supervisor.py` for the existing integration test surface, `docs/build_log.md` tail for the append point, memo 08 §3.3 for the canonical Plaid retrofit summary, `adminme/cli/commands/adapters.py` for the `validate` CLI command).

**Build_log Evidence template:** Append AFTER the most recent prior entry (11r-a). Header: `### Prompt 11r-b — Retrofit Plaid to InboundDataAdapter (Cohort C, 2 of 3)`. Cite: the `pack.yaml` capabilities block (Cat-C, NO `write_capabilities`, NO `observation_mode_required`); the inheritance flip with `family`/`capabilities` ClassVars; the no-stub-methods note (Cat-C discriminator from Cat-A/B/E); the validator-clean check; carry-forward into 11r-c (Reminders retrofit with schema taxonomy migration — the heavy one).

**Verification:**
```
poetry run ruff check packs/adapters/financial/plaid/ tests/unit/adapters/financial/ 2>&1 | tail -10
poetry run mypy adminme/ packs/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
# CLI smoke test: validate via the adminme adapters CLI
poetry run adminme adapters validate packs/adapters/financial/plaid
git add docs/build_log.md tests/integration/test_adapter_plaid_supervisor.py packs/adapters/financial/plaid/tests/test_plaid_inbound_data_adapter_retrofit_pack_internal.py
git commit -m "phase 11r-b commit 4: integration test refresh + pack-internal retrofit tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+12; `verify_invariants.sh` exit 0; `adminme adapters validate` exits 0 with no errors AND no advisory_pending_retrofit findings.

**PR open:** Title `phase 11r-b: Retrofit Plaid to InboundDataAdapter (closes 2 of 3 advisory_pending_retrofit findings)`. Body: top line "Second of three Cohort C retrofit prompts per `docs/08-architecture-amendment-adapter-rebase.md` §3.3. Retrofits the shipped Plaid pack to inherit `InboundDataAdapter`, declare a canonical Cat-C `capabilities` block, add `family` + `capabilities` ClassVars. No stub methods (Cat-C has no `send`/`_on_inbound`/`apply_write`/`execute_action` abstract surface — Plaid's existing `start`/`stop`/`status` cover the contract). The lightest of the three retrofits." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` is `main` per PR #106 sidecar. Pre-flight stack-integrity check on c4 expects 3 prior commits above `origin/main` (c1+c2+c3).

Per the 11r-a precedent, expect ZERO ghost conflicts (non-overlapping file scopes per c-band: c1=pack.yaml, c2=adapter.py, c3=new test directory, c4=existing integration test + pack-internal test + BUILD_LOG). Try MCP-direct merge first for each c-band; only drive Chrome MCP if `mergeable_state` reports "dirty".

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥12 new tests passing; ruff clean on `packs/adapters/financial/plaid/` / `tests/unit/adapters/financial/`; existing pack-internal tests still pass; `scripts/verify_invariants.sh` exit 0; PR opens against `main`; `adminme adapters validate packs/adapters/financial/plaid` exits 0 with NO errors AND NO `advisory_pending_retrofit` finding.

Cross-cutting [§N] / [DN] checks:

- [§7.3, D17]: zero projection writes from the retrofitted adapter (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' packs/adapters/financial/plaid/` returns 0). Plaid emits events only; unchanged from pre-retrofit.
- [§8.2, D6]: no LLM SDK imports under `packs/adapters/financial/plaid/`. Unchanged.
- [§6 invariant 10]: `sensitivity_floor: "privileged"` preserved on `PlaidAdapter` class attribute; `sensitivity_default: "privileged"` declared in manifest. Triple-lock (class attr + pack.yaml + config.schema.json) intact.
- [§6.20]: Cat-C has no outbound surface; `observation_mode_wrapping` validator check skips Plaid (no `send` / `execute_action` method to AST-walk).
- [D7]: `money_flow.plaid_synced` event schema at v1 unchanged; no new schemas.
- [D8]: `correlation_id` propagation discipline unchanged.
- [PM-34]: PlaidAdapter inherits from `InboundDataAdapter` (one of the 5 ABCs); validator's `base_class_conformance` check passes without the advisory.
- [PM-35]: `capabilities` declared as list (one Cat-C entry today).
- [UT-27]: `write_capabilities` empty / NOT declared (Cat-C has no write surface); validator's `write_capabilities_check` passes (Cat-A/C/D adapters with non-empty write_capabilities would fail, but Plaid's is correctly empty).

---

## §10 Stop

Plaid pack retrofitted onto `InboundDataAdapter`. Canonical Cat-C `capabilities` block declared in `pack.yaml`. `family` + `capabilities` ClassVars added; NO stub methods (Cat-C's abstract surface is the same `start`/`stop`/`status` as the simple Adapter Protocol that Plaid already satisfied). `adminme adapters validate` returns zero errors with NO `advisory_pending_retrofit` finding. Second of three Cohort C retrofits closed.

Ready for **prompt 11r-c** (Reminders → ExternalStateMirrorAdapter — third and final Cohort C retrofit per memo 08 §3.3). 11r-c is the HEAVY one: schema taxonomy migration from `reminder.list_item.synced` v1 (single event with `direction` field) to `list.*` + `list_item.*` per [D18]; dual-deployment per [D22] (central variant on assistant Apple ID + bridge variant per member Apple ID); asymmetric `write_capabilities` per memo 05 §2.3 (per-operation write_capabilities list for the bidirectional surface). After 11r-c lands, the 11-β validator surfaces zero `advisory_pending_retrofit` findings on any pack, and Cohort D (resumed canon) begins per memo 08 §3.4.
