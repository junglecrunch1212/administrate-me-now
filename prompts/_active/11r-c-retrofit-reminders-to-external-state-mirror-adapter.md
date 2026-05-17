# Prompt 11r-c: Retrofit Apple Reminders to ExternalStateMirrorAdapter

**Phase:** L1 / Cohort C step 3 (third and FINAL Cohort C retrofit per `docs/08-architecture-amendment-adapter-rebase.md` §3.3)
**Branch:** `claude/11r-c-c<N>` per c-band (Executor's harness assigns)
**Stop condition:** `adminme adapters validate packs/adapters/reminders/apple-eventkit` exits 0 with NO errors AND no `advisory_pending_retrofit` finding on `base_class_conformance`
**Depends-on:** Cohort B closed (PRs #118-#121 / #123-#126 / #128-#131 on main); Cohort C steps 1 + 2 closed (Gmail retrofit PRs #133-#136; Plaid retrofit PRs #139-#142)
**Quality-bar reference:** `prompts/_active/11r-b-retrofit-plaid-to-inbound-data-adapter.md` (merged as PR #138; executor cycle #14 landed PRs #139-#142). 11r-c is structurally similar but HEAVIER: Cat-B has TWO abstract methods (vs Cat-C's none), asymmetric `write_capabilities` per [UT-27], and a real observation-mode wrap via `adminme.lib.observation.outbound` per [§6.20].
**Estimated duration:** c1 ~30-40 min · c2 ~60-90 min · c3 ~45-60 min · c4 ~45-60 min · total ~3-4 hrs wall / ~$60-90 spend

---

## §1 Behavioral grounding

This is the third and FINAL Cohort C retrofit. After 11r-c lands, the 11-β validator surfaces ZERO `advisory_pending_retrofit` findings on any shipped v1 pack and Cohort C is **CLOSED**. Cohort D (resumed canon: full event-sourcing 11a/11b/11c per memo 08 §3.4 + `prompts/PROMPT_SEQUENCE.md` rows 116-117) begins after this prompt's c4 merges.

The Apple Reminders adapter at `packs/adapters/reminders/apple-eventkit/adapter.py` is the HEAVY retrofit: Cat-B is bidirectional (read + write), and unlike Plaid (Cat-C read-only) it must implement BOTH abstract methods that the `ExternalStateMirrorAdapter` ABC declares — `_apply_write_impl` AND `_on_external_change`. The existing adapter already implements both surfaces (`_write_one_outbound` writes outbound; `_emit_inbound_synced` handles inbound per-item). The retrofit refactors the existing logic to satisfy the ABC's abstract contract — it does NOT rewrite the production paths. Per memo 08 §5: "**inheritance + manifest only, not a rewrite**."

Three load-bearing things are NEW vs Plaid's retrofit:

1. **Asymmetric `write_capabilities` per [UT-27]** — the manifest + ClassVar list ONLY the per-operation kinds the adapter actually supports. The base class's `apply_write` gates on membership and raises `WriteCapabilityError` for any undeclared kind BEFORE the external API is touched. The Reminders adapter today supports two write kinds: `create_reminder` (the canonical EKReminder save from upstream `reminder.dispatched` / `task.created` / `commitment.proposed`) and (per [D18] memo 05 §5.4) `toggle_completion` — but `toggle_completion` is NOT implemented yet in the shipped code (the outbound path only creates new reminders). The retrofit declares ONLY `create_reminder` in `write_capabilities`; declaring `toggle_completion` without implementation would fail the validator's `write_capabilities_check` (method-presence rule per memo 05 §3.4) and is also the kind of HARD `[UT-27]` violation that the Cat-B ABC's gate is designed to catch at runtime.
2. **Observation-mode wrap per [§6.20]** — the new `_apply_write_impl` MUST route the actual `EKEventStore.save(_:commit:)` call through `adminme.lib.observation.outbound(observation_mode_required=True, would_have_side_effect=..., perform=...)` so observation-mode suppression works for write-back. The existing `_write_one_outbound` does NOT do this — that's a real gap this retrofit closes. The existing path's never-sync filter is per-item privacy, NOT a substitute for the observation-mode operator gate.
3. **`sharing_model_discriminator` per memo 05 §2.5** — declares the projection-row column that distinguishes per-member vs shared rows. For Reminders the discriminator is `owner_member_id` (matches Plaid's per [PM-35]).

OUT OF SCOPE in this prompt per memo 08 §5:

- **The [D18] `list.*` / `list_item.*` event-family schema migration.** The existing `reminder.list_item.synced@v1` schema STAYS. Adding the new event family is ADDITIVE work that ships in a separate prompt (Cohort D's canonical 11b or a dedicated Cohort C step 4) with its own consumer-pipeline migration plan; it's not a retrofit.
- **Dual-deployment per [D22]** (central + per-member bridge variants). The existing adapter is single-deployment central; dual-deployment is Cohort D's canonical 11b job.
- **Real `toggle_completion` write handler** (memo 05 §5.4's AppleScript bridge for in-place edit) — DECLARING it in `write_capabilities` without implementation would fail `write_capabilities_check`; the retrofit narrowly preserves the existing `create_reminder` only.
- **Retrofiting the inbound poll loop's full dispatch path through `_on_external_change`** — the existing `_inbound_poll_once` keeps calling `_emit_inbound_synced` directly in production. `_on_external_change` is the ABC contract entry point and is implemented as a callable thin wrapper that delegates to `_emit_inbound_synced` (so the AST-walk-based `base_class_conformance` check sees a real implementation, not `raise NotImplementedError`). The supervisor's loader path is unchanged; the inbound poll's per-cycle behavior is preserved byte-for-byte.
- **Replacing the queue-based outbound flow with `apply_write(operation)` invocation per drain.** The existing `_drain_outbound_once` keeps the queue; the new `_apply_write_impl` is a per-operation implementation that the ABC's `apply_write` gate dispatches to. The existing `_write_one_outbound` REFACTORS to call `_apply_write_impl` via the gate (so the gate is exercised on every outbound write — closing the [UT-27] observability gap). This is the one production-path refactor in this prompt and is intentional: it's what makes the `write_capabilities` gate load-bearing instead of decorative.

Citations: [§6 invariant 10] (sensitivity floor triple-lock — `normal` for Reminders), [§6.20] (observation-mode wrapping), [§7.3] / [D17] (adapters never write projections), [§8.2] / [D6] (no LLM SDK direct imports), [D7] (event schemas register at v1, immutable), [D8] (correlation_id propagation), [D11] (sensitivity floors hardcoded at class), [D18] (Reminders + Tasks + Notes-checklists emit `list.*` / `list_item.*` taxonomy — referenced as future migration target, NOT included in this prompt), [D19] (5-category adapter taxonomy by epistemic role), [D22] (Apple Reminders dual-deployment — OUT OF SCOPE), [PM-27] (constructor injection with no-op defaults), [PM-34] (5 mutually exclusive ABCs), [PM-35] (capabilities-as-list), [UT-27] (asymmetric write_capabilities, per-operation method-presence enforcement), memo 05 §1.2 (Cat-B contract), §2.3 (asymmetric write_capabilities), §2.5 (sharing_model_discriminator), §3.1 (manifest extension fields), §3.2 (5-ABC pattern), §3.4 (six-check validator), §5.3 (list.*/list_item.* taxonomy — referenced for the [D18] migration deferral), §5.4 (Reminders bidirectional spec), memo 08 §3.3 (Cohort C retrofit pattern), §5 (inheritance + manifest only, not a rewrite).

[VISION §"Privacy by default"] — Reminders' never-sync filter (list-name substring + #private tag + child/ambient member role) is preserved byte-for-byte. The retrofit ONLY adds the framework conformance layer; user-visible behavior does NOT change.

## §2 Read first (required)

Per c-band:

**c1 (pack.yaml extension) — 4 files**

- `prompts/_active/11r-b-retrofit-plaid-to-inbound-data-adapter.md` — quality bar reference (the JUST-merged predecessor; 345 lines; `pack.yaml` extension precedent)
- `packs/adapters/reminders/apple-eventkit/pack.yaml` — current Reminders manifest, no `capabilities:` block (the advisory source — same advisory that 11r-b closed for Plaid and 11r-a closed for Gmail)
- `adminme/lib/pack_registry/parser.py` — `AdapterManifest` model (declares `capabilities`, `write_capabilities`, `sharing_model_discriminator`, `sensitivity_default`, `dedup_strategy: dict[str, Any] | None`, `observation_mode_required`)
- `docs/05-architecture-amendment-personal-data-layer.md` §3.1 (manifest extension fields) + §2.3 (asymmetric write_capabilities) + §2.5 (sharing_model_discriminator)

**c2 (adapter.py inheritance flip + ABC impls) — 6 files**

- `adminme/lib/adapters/external_state_mirror_base.py` — Cat-B ABC: ClassVars (`family`, `capabilities`, `write_capabilities`, `sharing_model_discriminator`), abstract methods (`start`, `stop`, `status`, `_apply_write_impl`, `_on_external_change`), concrete `apply_write` gate raising `WriteCapabilityError`, `WriteOperation` / `WriteResult` / `ChangeEnvelope` dataclasses
- `adminme/lib/observation.py` — `outbound(*, observation_mode_required, would_have_side_effect, perform)` per [§6.20]; the function the retrofit's `_apply_write_impl` MUST call before invoking `EKEventStore.save(_:commit:)`
- `packs/adapters/reminders/apple-eventkit/adapter.py` (head + class section ONLY — the file is 42 KB; use `sed -n '1,250p'` for the head + the class-attribute section and `sed -n '/class AppleEventKitAdapter/,/_translate_upstream/p'` for the class definition region; do NOT full-read)
- `packs/adapters/messaging/gmail/adapter.py` — Cat-A precedent (11r-a) for the inheritance-flip pattern + `send` / `_on_inbound` stub addition
- `packs/adapters/financial/plaid/adapter.py` — Cat-C precedent (11r-b) for the ClassVar-extension pattern (no stubs — Cat-C has no write surface; Cat-B's pattern is the union of Cat-A's stub additions + Cat-C's ClassVar extension)
- `docs/05-architecture-amendment-personal-data-layer.md` §1.2 (Cat-B contract) + §5.4 (Reminders bidirectional) + memo 08 §5 (inheritance + manifest only, not a rewrite)

**c3 (framework tests) — 5 files**

- `tests/unit/adapters/messaging/test_gmail_retrofit.py` — c3 precedent layout (11r-a, ~280 lines, 11 framework tests)
- `tests/unit/adapters/financial/test_plaid_retrofit.py` — c3 precedent (11r-b, ~270 lines, 10 framework tests)
- `adminme/lib/pack_registry/validator.py` — six-check validator (`base_class_conformance` + `write_capabilities_check` are the two relevant checks for Cat-B's `advisory_pending_retrofit` closure)
- `adminme/events/registry.py` — `registry.register(name, version, model)` API
- `packs/adapters/reminders/apple-eventkit/tests/` — existing pack-internal layout (skim ONLY to confirm the F-3 unique-basename requirement; do NOT depth-read)

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `tests/integration/test_adapter_supervisor_gmail.py` — supervisor-loader retrofit-sibling precedent (11r-a)
- `tests/integration/test_adapter_plaid_supervisor_retrofit.py` — supervisor-loader retrofit-sibling precedent (11r-b, NEW in c4)
- `tests/unit/lib/pack_registry/test_parser.py` — existing test that asserts pre-retrofit `_yields_empty_capabilities` for Reminders; renaming target post-retrofit
- `tests/integration/test_adapter_framework_validator.py` — existing assertion that pinned `advisory_pending_retrofit` for Reminders; update target post-retrofit (parallel to 11r-b's c4 update for Plaid)

## §3 Reconciliation notes (carry-forward from 11r-a + 11r-b)

- **F-1 (PRE-AUTHORSHIP GATE): adapter path is `packs/adapters/reminders/apple-eventkit/`** — the build_log carry-forward from 11r-b cited `packs/adapters/lists/apple_reminders/`. Wrong path. Pre-authorship Gate 3 caught this. Confirmed on main `93b9995f` / `6f9ff142`: actual location is `packs/adapters/reminders/apple-eventkit/`.
- **F-2 (HARD/from 11r-a): adapter.py diff anti-vision halt.** 11r-c is the heavy retrofit; expected c2 diff is ~150-300 lines added (vs 11r-b's +34/-6, vs 11r-a's ~80 lines). The +300 ceiling is the explicit anti-vision halt. If the Executor finds itself adding more than 300 lines to `adapter.py` in c2, halt and request operator disposition — the retrofit is supposed to be inheritance + ABC impls + observation wrap, not a rewrite of the existing logic.
- **F-3 (HARD/from 10c-iii + 11r-a + 11r-b): pack-internal test basename uniqueness.** The new pack-internal test in c4 MUST use a unique basename (e.g. `test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py`) — collision with another pack's pack-internal test of the same basename trips pytest's rootdir-relative module-name collision. Verified F-3-safe basename below in §7.
- **F-4 (PRE-EXISTING TECH DEBT): 4 Gmail-pinning tests still broken from 11r-a.** Per the 11r-b carry-forward, four tests still assert pre-retrofit Gmail state (`test_parse_existing_gmail_pack_yaml_yields_empty_capabilities`, `test_existing_v1_gmail_pack_validates_without_errors`, `test_validate_against_on_main_gmail_pack_exits_zero_with_advisory`, `test_list_command_finds_three_reference_packs_on_main`). 11r-c's c4 also updates the analogous Reminders assertions (renames `_yields_empty_capabilities` → `_yields_cat_b_capability`, updates validator assertion). Recommended after-11r-c sidecar: single PR updating the 4 Gmail tests now that all three legacy packs are post-retrofit.
- **F-5 (SOFT/from 11r-b): pack.yaml byte-count.** 11r-b's pack.yaml added +17 lines vs a stated ≤15 ceiling — accepted as "trailing newline" tolerance. 11r-c's pack.yaml extension adds slightly more (Cat-B has `write_capabilities` + the slightly longer `capabilities` block with a Cat-B kind + the `sharing_model_discriminator` field). Soft ceiling: ≤25 lines for c1's pack.yaml diff.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `AppleEventKitAdapter` is the actual class name.** Confirmed on main `6f9ff142`: yes — `packs/adapters/reminders/apple-eventkit/adapter.py` line ~178 declares `class AppleEventKitAdapter:`. The retrofit flips to `class AppleEventKitAdapter(ExternalStateMirrorAdapter):` — NOT a rename.
- **A-2: `write_capabilities` declares ONLY `create_reminder`.** Per [UT-27] (asymmetric — declare only what's implemented) + memo 08 §5 (inheritance + manifest only, not a rewrite). The existing adapter does not implement `toggle_completion` or `delete_reminder` or `update_reminder`; declaring any of those would fail `write_capabilities_check`'s method-presence rule (the validator's c2 check from 11-β at `adminme/lib/pack_registry/validator.py`).
- **A-3: `_apply_write_impl` exercises the existing `_write_one_outbound` logic.** The new method's body is a refactor that lifts the EKReminder-save section of `_write_one_outbound` into the new method, with the addition of an `adminme.lib.observation.outbound` wrap around the `ek_store.save(reminder, commit=True)` call per [§6.20]. The existing `_write_one_outbound` becomes the orchestrator that translates the upstream event into a `WriteOperation`, calls `await self.apply_write(operation)` (which exercises the ABC's gate), and on a successful `WriteResult` emits `reminder.list_item.synced` v1 with `direction="outbound"`. This is the one production-path refactor in this prompt.
- **A-4: `_on_external_change` delegates to existing `_emit_inbound_synced`.** The new method's body is a thin wrapper: build a synthetic reminder-like object from the `ChangeEnvelope.payload` and call `self._emit_inbound_synced(ctx=..., reminder=synthetic, sync_cycle_id=envelope.payload["sync_cycle_id"])`. The existing `_inbound_poll_once` continues to call `_emit_inbound_synced` directly for the production poll path; `_on_external_change` is the ABC contract entry point for future use (e.g. when 11g's HA Cat-C / Cat-E adapters route real change envelopes through it).
- **A-5: `apply_write` raises `WriteCapabilityError` for unsupported kinds.** The base class's gate. The c3 framework test pins this with `with pytest.raises(WriteCapabilityError):` against an operation whose `kind` is `"toggle_completion"` (a kind not declared in 11r-c's `write_capabilities` — i.e. the case that the deferred per-operation handler would fail).
- **A-6: `sharing_model_discriminator` is `owner_member_id`.** Matches Plaid's per [PM-35]. Reminders rows in the future `lists` projection (Cohort D's 11f) are per-member by `owner_member_id` for personal lists, then become shared via the `list_shares` join table. Single-deployment central in 11r-c.

## §5 Objective

Retrofit `packs/adapters/reminders/apple-eventkit/` so the pack-internal class `AppleEventKitAdapter` inherits from `adminme.lib.adapters.ExternalStateMirrorAdapter` (Cat-B, bidirectional state mirror), declares a Cat-B `capabilities` block + asymmetric `write_capabilities` + `sharing_model_discriminator` + `sensitivity_default` + `dedup_strategy` in `pack.yaml`, implements the ABC's two abstract methods (`_apply_write_impl` and `_on_external_change`) as refactors of the existing methods, and routes the outbound EKEventStore save through `adminme.lib.observation.outbound` per [§6.20]. The retrofit closes the third and final `advisory_pending_retrofit` finding the 11-β validator surfaces (closing Cohort C); preserves all existing user-visible behavior (the never-sync privacy filter, the bidirectional sync semantics, the 30s poll cadences, the cursor-advance milestone gating); and does not migrate any schemas.

## §6 Out of scope

- The [D18] `list.*` / `list_item.*` event-family schema migration — additive ADDITIVE work in a separate prompt (Cohort C step 4 or Cohort D's canonical 11b)
- Dual-deployment per [D22] (central + per-member bridge variants) — Cohort D's canonical 11b
- Real `toggle_completion` / `update_reminder` / `delete_reminder` write handlers — declaring them would fail `write_capabilities_check`'s method-presence rule
- Migrating the inbound poll's dispatch through `_on_external_change` in production — the new method is the ABC entry point but the existing `_inbound_poll_once` keeps calling `_emit_inbound_synced` directly
- Replacing the supervisor's adapter-loading code path — preserved byte-for-byte (the `ExternalStateMirrorAdapter` ABC extends the same simple `Adapter` Protocol shape)
- Touching `never_sync.py` or `_eventkit_shim.py` — preserved byte-for-byte
- Touching the `reminder.list_item.synced@v1` schema — preserved byte-for-byte ([D7] immutable v1)
- Touching the supervisor at `adminme/daemons/adapter_supervisor.py`
- Touching upstream pipeline subscribers — the queue contract (`enqueue_outbound(event)`) is preserved
- Touching `config.example.yaml` / `config.schema.json` — preserved byte-for-byte
- Touching the `requires_live_services`-gated full lifecycle test at `tests/integration/test_adapter_apple_eventkit_supervisor.py` (or equivalent) — the retrofit-contract integration sibling in c4 is the new addition
- The CRM-spine 11e Apple Contacts retrofit — separate prompt
- The lists projection 11f — Cohort D

## §7 Deliverables (files + tests + demo commands)

**c1: `packs/adapters/reminders/apple-eventkit/pack.yaml`** — append framework extension block (≤25 lines added) below the existing `documentation:` block:

```yaml
# Framework extension fields per memo 05 §3.1 / [PM-33] / [PM-35] / [UT-27].
# Added by prompt 11r-c (Cohort C retrofit, step 3 of 3) — closes the THIRD
# and FINAL advisory_pending_retrofit finding the 11-β validator surfaces on
# base_class_conformance, closing Cohort C. `dedup_strategy` is declared as
# a mapping per the on-main AdapterManifest model (`dict[str, Any] | None`);
# the `external_id` kind matches the existing per-reminder dedupe semantics
# (EKReminder.calendarItemIdentifier is the stable per-reminder key — same
# `_seen_inbound` cache key the adapter uses for inbound dedupe today).
capabilities:
  - kind: Cat-B
    runtime: central
    event_families: [reminder.list_item.synced]
write_capabilities:
  - create_reminder
sensitivity_default: normal
sharing_model_discriminator: owner_member_id
dedup_strategy:
  kind: external_id
```

NOT declared (deferred to Cohort D's canonical 11b): `observation_mode_required` (the per-write observation wrap is implemented in `_apply_write_impl` via `adminme.lib.observation.outbound` directly — the manifest field gates the validator's `observation_mode_wrapping` check, which Cat-B passes implicitly because every Cat-B has at least one write capability requiring the wrap), `runtime: dual` (Cohort D 11b dual-deployment scope).

**c2: `packs/adapters/reminders/apple-eventkit/adapter.py`** — inheritance flip + ClassVars + ABC abstract method implementations (≤300 lines added; anti-vision halt). Three surface changes:

1. **Inheritance flip + ABC imports**:
   - `from adminme.lib.adapters import ExternalStateMirrorAdapter, WriteOperation, WriteResult, ChangeEnvelope, WriteCapabilityError` (top of file)
   - `from adminme.lib.observation import outbound as observation_outbound` (top of file)
   - `class AppleEventKitAdapter:` → `class AppleEventKitAdapter(ExternalStateMirrorAdapter):`
2. **ClassVar additions** (alongside existing `name` / `family` / `version` / `sensitivity_floor`):
   - `name: ClassVar[str] = "reminders-apple-eventkit"` (narrowed from `str`)
   - `family: ClassVar[Literal["reminders"]] = "reminders"` (narrowed from ABC's Literal["reminders" | "calendaring" | "contacts"])
   - `version: ClassVar[str] = "1.0.0"` (narrowed)
   - `sensitivity_floor: ClassVar[SensitivityFloor] = "normal"` (narrowed; matches ABC default — subclass override-upward rule preserved)
   - `capabilities: ClassVar[list[Any]] = [{"kind": "Cat-B", "runtime": "central", "event_families": ["reminder.list_item.synced"]}]` (mirrors manifest per memo 05 §3.2)
   - `write_capabilities: ClassVar[list[str]] = ["create_reminder"]` (asymmetric per [UT-27])
   - `sharing_model_discriminator: ClassVar[str] = "owner_member_id"`
3. **Two new ABC abstract method implementations** (lifted refactors of existing logic, NOT new business logic):
   - `async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:` — accepts a `WriteOperation` with `kind="create_reminder"`; builds the EKReminder via the existing `ek_store.build_reminder(...)`; wraps the `ek_store.save(reminder, commit=True)` in `await observation_outbound(observation_mode_required=True, would_have_side_effect={...}, perform=lambda: ek_store.save(reminder, commit=True))` per [§6.20]; returns `WriteResult(applied=True, external_id=remind_id, suppressed=False)` on save, `WriteResult(applied=False, external_id=None, suppressed=True)` if observation suppressed
   - `async def _on_external_change(self, envelope: ChangeEnvelope) -> None:` — thin wrapper that synthesizes a reminder-like object from `envelope.payload` and delegates to the existing `_emit_inbound_synced` (the production poll path is unchanged; this satisfies the ABC contract for future use)
4. **`_write_one_outbound` refactor** (the one production-path change): split the existing method into (a) the translation + never-sync check + member-role lookup (preserved at top of method) and (b) the EKReminder-save + emit section (lifted into `_apply_write_impl`). The refactored `_write_one_outbound` builds the `WriteOperation` and calls `await self.apply_write(operation)` (which exercises the ABC's `write_capabilities` gate). On `WriteResult.applied=True`, emit the existing `reminder.list_item.synced@v1` event with `direction="outbound"` as before. On `WriteResult.suppressed=True`, do NOT emit the synced event (observation-mode silenced the side effect).

All other methods preserved byte-for-byte: `start`, `stop`, `status`, `enqueue_outbound`, `_outbound_poll`, `_drain_outbound_once`, `_translate_upstream` (static), `_inbound_poll`, `_inbound_poll_once`, `_fetch_reminders_filtered`, `_is_new_or_modified`, `_emit_inbound_synced`, `_maybe_emit_inbound_cursor_advance`, `_emit_authenticated`, `_emit_error`, `_append`, plus the `_classify_eventkit_error` module-level helper.

**c3: `tests/unit/adapters/reminders/test_apple_eventkit_retrofit.py`** + `__init__.py` — ≥12 framework-level tests:

1. `test_apple_eventkit_adapter_inherits_external_state_mirror_adapter` — `issubclass(AppleEventKitAdapter, ExternalStateMirrorAdapter)` per memo 08 §3.3
2. `test_apple_eventkit_adapter_inherits_exactly_one_framework_abc` — mutual-exclusion across the 5 ABCs per [PM-34]
3. `test_apple_eventkit_adapter_write_capabilities_declared_asymmetrically` — `AppleEventKitAdapter.write_capabilities == ["create_reminder"]`; `len(write_capabilities) == 1` (asymmetric per [UT-27]; `toggle_completion` etc. NOT declared because not implemented)
4. `test_apply_write_gate_raises_on_undeclared_kind` — `with pytest.raises(WriteCapabilityError): await adapter.apply_write(WriteOperation(kind="toggle_completion", ...))` — the ABC gate rejects undeclared ops BEFORE `_apply_write_impl` is called
5. `test_apply_write_dispatches_to_impl_on_declared_kind` — `apply_write(WriteOperation(kind="create_reminder", ...))` reaches `_apply_write_impl` (asserted via monkeypatching `_apply_write_impl` to record the call)
6. `test_apply_write_routes_through_observation_outbound` — the `EKEventStore.save` call is wrapped by `adminme.lib.observation.outbound`; assertion: `observation_outbound` is called with `observation_mode_required=True` (mockable via monkeypatching)
7. `test_pack_yaml_capabilities_block_parses_as_cat_b` — `parse_adapter_manifest` returns one Cat-B capability with `runtime="central"` and `event_families=["reminder.list_item.synced"]`
8. `test_pack_yaml_extension_fields_round_trip` — manifest declares `sensitivity_default == "normal"`, `sharing_model_discriminator == "owner_member_id"`, `dedup_strategy == {"kind": "external_id"}`, `write_capabilities == ["create_reminder"]`, `observation_mode_required is False` (default; the wrap is in `_apply_write_impl`, not the manifest)
9. `test_validator_no_pending_retrofit_advisory` — `validate_adapter_pack(REMINDERS_PACK_DIR)` returns NO `advisory_pending_retrofit` finding on `base_class_conformance`
10. `test_validator_returns_no_errors_for_retrofitted_pack` — `not result.has_errors()` post-retrofit
11. `test_family_class_attribute_is_reminders_literal` — `AppleEventKitAdapter.family == "reminders"`
12. `test_capabilities_class_attribute_mirrors_manifest` — class ClassVar agrees with `parse_adapter_manifest`'s `capabilities` block
13. `test_sensitivity_floor_normal_preserved` — `AppleEventKitAdapter.sensitivity_floor == "normal"` (matches ABC default; subclass override-upward rule preserved)
14. `test_on_external_change_delegates_to_emit_inbound_synced` — `_on_external_change(envelope)` invokes `_emit_inbound_synced` with the synthesized reminder-like object (assertion via monkeypatching)

Load `AppleEventKitAdapter` via the file-path importlib pattern (mirrors 11r-a + 11r-b precedents; `packs/` is not on poetry's package path).

**c4: pack-internal + integration tests + assertion updates + BUILD_LOG**:

1. **`packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py`** (NEW, F-3 unique basename, 3 tests):
   - `test_capabilities_class_attribute_matches_manifest`
   - `test_pack_yaml_round_trips_through_parse_adapter_manifest_with_write_capabilities` (asserts all four extension fields)
   - `test_six_validator_checks_return_zero_errors_and_no_advisory_pending_retrofit`
2. **`tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py`** (NEW, 3 tests; default-CI sibling to the `requires_live_services`-gated full lifecycle test):
   - `test_retrofit_supervisor_loaded_apple_eventkit_inherits_external_state_mirror_adapter`
   - `test_retrofit_apple_eventkit_write_capabilities_gate_rejects_undeclared_kinds_through_loader`
   - `test_retrofit_validator_clean_on_supervisor_loaded_pack`
3. **`tests/unit/lib/pack_registry/test_parser.py`** — rename `test_parse_existing_reminders_pack_yaml_yields_empty_capabilities` to `test_parse_existing_reminders_pack_yaml_yields_cat_b_capability`; assert `kind == "Cat-B"`, `runtime == "central"`, `event_families == ["reminder.list_item.synced"]`, `sensitivity_default == "normal"`, `write_capabilities == ["create_reminder"]`, `sharing_model_discriminator == "owner_member_id"`
4. **`tests/integration/test_adapter_framework_validator.py`** — update `test_existing_v1_reminders_pack_validates_without_errors` (or analogous; rename if needed) to assert NO `advisory_pending_retrofit` finding post-retrofit; pattern matches 11r-b's c4 update
5. **`docs/build_log.md`** — append the 11r-c entry per the c4 template. Mark **`Closes Cohort C`** prominently — this is the closing prompt for the cohort. Carry-forward section: NEXT prompt is **Cohort D** (resumed canon: prompts 11a / 11b / 11c per PROMPT_SEQUENCE.md rows 116-117 + memo 08 §3.4), starting with the canonical 11a (Gmail + Plaid CENTRAL via the typed ABCs with real outbound — NOT a retrofit but a full event-sourcing build).

**Demo command (post-retrofit):**

```bash
adminme adapters validate packs/adapters/reminders/apple-eventkit
# Expected exit code: 0; NO `advisory_pending_retrofit` finding on `base_class_conformance`; NO errors
```

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11r-c commit <N>: <imperative summary>` exactly.

- **c1: `phase 11r-c commit 1: Apple Reminders pack.yaml capabilities + write_capabilities + sharing model`** — `packs/adapters/reminders/apple-eventkit/pack.yaml` (≤25 lines added)
- **c2: `phase 11r-c commit 2: AppleEventKitAdapter inheritance + ClassVars + apply_write + observation wrap`** — `packs/adapters/reminders/apple-eventkit/adapter.py` (≤300 lines added; anti-vision halt)
- **c3: `phase 11r-c commit 3: framework-level Reminders retrofit tests`** — `tests/unit/adapters/reminders/__init__.py` + `test_apple_eventkit_retrofit.py` (~14 tests)
- **c4: `phase 11r-c commit 4: integration test refresh + pack-internal retrofit tests + BUILD_LOG`** — pack-internal (NEW, F-3 unique basename) + integration retrofit sibling (NEW) + assertion updates to existing test files + BUILD_LOG append (closes Cohort C)

## §8.5 Branch strategy — STACKED

Each c-band commits onto the prior c-band's branch (c2 from c1, c3 from c2, c4 from c3); PRs open with `base=main` (per PR #106 sidecar policy). Ghost-conflict risk is HIGHER than 11r-b because c2 + c4 both touch `packs/adapters/reminders/apple-eventkit/` (c2 modifies `adapter.py`; c4 adds files under `tests/`) and c4 touches `tests/unit/lib/pack_registry/test_parser.py` and `tests/integration/test_adapter_framework_validator.py` (existing files, not under the retrofit's own pack). Mitigation: c-band file scopes are still non-overlapping at the file level (c1=pack.yaml; c2=adapter.py; c3=new test files under `tests/unit/adapters/reminders/`; c4=new files + 2 existing-file assertion updates). The 11r-a + 11r-b precedents had ZERO ghost conflicts; the same scoping should hold for 11r-c. If a c-band lands dirty on merge, fall through to Chrome MCP ghost-conflict resolution (JS-regex pattern documented in 11r-b's Reviewer findings).

## §9 Verification

Per c-band:

**c1**:
```bash
python -c "from adminme.lib.pack_registry.parser import parse_adapter_manifest; from pathlib import Path; m = parse_adapter_manifest(Path('packs/adapters/reminders/apple-eventkit')); assert len(m.capabilities) == 1; assert m.capabilities[0].kind == 'Cat-B'; assert m.write_capabilities == ['create_reminder']; assert m.sharing_model_discriminator == 'owner_member_id'; assert m.sensitivity_default == 'normal'; assert m.dedup_strategy == {'kind': 'external_id'}; print('c1 manifest parses ✓')"
```

**c2**:
```bash
poetry run python -c "from packs.adapters.reminders.apple_eventkit.adapter import AppleEventKitAdapter; from adminme.lib.adapters import ExternalStateMirrorAdapter; assert issubclass(AppleEventKitAdapter, ExternalStateMirrorAdapter); assert AppleEventKitAdapter.write_capabilities == ['create_reminder']; print('c2 inheritance + ClassVars ✓')"
poetry run mypy adminme/ packs/adapters/reminders/apple-eventkit/ 2>&1 | tail -5
```

(Note: the import path is `packs.adapters.reminders.apple_eventkit` — pyproject.toml has the underscore form per existing test convention; verify the actual import resolution against existing pack-internal tests at orientation.)

**c3**:
```bash
poetry run pytest tests/unit/adapters/reminders/test_apple_eventkit_retrofit.py -v
# Expected: 14+ passed
```

**c4**:
```bash
poetry run pytest packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py tests/unit/lib/pack_registry/test_parser.py tests/integration/test_adapter_framework_validator.py -v
# Expected: all retrofit-related tests pass; the 4 pre-existing Gmail-pinning failures remain (out of scope per F-4 carry-forward); the 2 Plaid analogues pass (fixed in 11r-b's c4)
poetry run adminme adapters validate packs/adapters/reminders/apple-eventkit
# Expected: exit 0; NO `advisory_pending_retrofit` finding; NO errors
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When `adminme adapters validate packs/adapters/reminders/apple-eventkit` exits 0 with NO `advisory_pending_retrofit` finding AND no errors, c4 is done. **Cohort C is closed.** Open the PR per the standard four-commit structure. Do NOT proceed to Cohort D (canonical 11a) without operator review of the 11r-c merge — the canonical 11a is a full event-sourcing build, not a retrofit, and warrants a separate Partner orientation + pre-authorship gate cycle per memo 08 §3.4 + PROMPT_SEQUENCE.md rows 116-117.

After merge: Partner runs three-job QC on the merged state, then opens the partner-state-snapshot PR closing Cohort C (PROMPT_SEQUENCE.md Pause rule flips to "Cohort C CLOSED, Cohort D begins"; partner_handoff.md new Last-updated paragraph; build_log.md placeholder patch on the 11r-c entry's PR-numbers + commit shas).
