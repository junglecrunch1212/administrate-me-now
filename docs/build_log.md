build_changelog.md not started until after Prompt 05
Prompt 05 Completed and Merged
Prompt 06 due for review and potential refactor
Permission for Claude Code Opus 4.7 Code Supervision Partner to take over this log now and keep up-to-date with details of each new prompt execution, merge, diff, future prompt refactors, and any other history important to build_changelog.md 

### Prompt 07a — ops spine projections (places_assets_accounts, money, vector_search)
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/07a-projections-ops-spine.md (~600 lines, quality bar = 06).
- **Session merged**: PR #<N>, commits 81290b0 / edd0c34 / 71731fb / <commit4>, merged <merge date>.
- **Outcome**: MERGED
- **Evidence**:
  - 3 projections: places_assets_accounts (3 entity tables + 2 association tables), money (1 table with is_manual + soft-delete), vector_search (vec0 virtual table + embeddings_meta sidecar).
  - 10 new event types registered at v1 per [D7] (place/asset/account × added/updated, money_flow × 3, embedding.generated).
  - 38 new unit tests (places_assets_accounts 15, money 13, vector_search 10) + integration rebuild extended to 10 projections + ~1200 events.
  - Runner gained `on_connection_opened` hook for projection-specific extension loading (vec0). Default no-op, backward compatible.
  - Privileged-filter at handler time on vector_search per [§13.8]. Envelope- or payload-level `sensitivity='privileged'` drops the write with INFO log.
  - CHECK constraints consistent on enum columns per CF-6 (places.kind, assets.kind, accounts.kind, accounts.status, money_flows.kind, sensitivity).
  - login_vault_ref CHECK accepts only `op://`, `1password://`, `vault://` — belt against broken adapter writes.
  - money soft-delete: `money_flow.manually_deleted` UPDATEs `deleted_at` rather than deleting the row. Rebuild correctness preserved. Queries exclude deleted.
  - BUILD_LOG updated as part of Commit 4 per new rule.
  - Ruff clean, mypy clean (95 source files), all inviolable greps OK.
- **Carry-forward for prompt 07b (xlsx forward daemon)**:
  - Forward daemon reads from all 10 projections' query functions. Query signatures stable.
  - `money.manual_flows` + `money_flow.manually_added` event type already wired for 07c's reverse path.
  - The 07b forward daemon subscribes to event types but MUST NOT emit — it's a projection per [§2.2].
- **Carry-forward for prompt 07c (xlsx reverse daemon)**:
  - `money_flow.manually_added` and `money_flow.manually_deleted` events registered; 07c emits these when principals edit the Raw Data sheet.
  - `task.updated`, `task.deleted`, `commitment.edited` (registered in 06) cover the Tasks/Commitments sheets' reverse path.
  - xlsx reverse is an adapter not a projection — it emits.
- **Carry-forward for prompt 08**:
  - 3 new projections × ~6 queries = ~18 more TODO(prompt-08) markers across queries.py files. Total now ~38 across 10 projections.
- **Carry-forward for future embedding daemon**:
  - `embedding.generated` schema requires pre-computed vector in payload. AdministrateMe does not import embedding SDKs. Daemon will call OpenClaw's embedding endpoint per [§8].

### Prompt 07b — xlsx_workbooks forward daemon
- **Refactored**: by Partner in Claude Chat. Prompt file: prompts/07b-xlsx-workbooks-forward.md.
- **Session merged**: PR #<N>, commits 05e13dd / 3c14625 / 2546061 / <commit4>, merged <merge date>.
- **Outcome**: MERGED
- **Evidence**:
  - xlsx_workbooks projection built as forward-only daemon consuming events from 10 projections; structurally a projection per [§2.2], emits only the categorized system event `xlsx.regenerated`.
  - 1 new event type at v1: `xlsx.regenerated` — in new `schemas/system.py` module to keep domain event files clean.
  - 2 workbook builders: adminme-ops.xlsx (Tasks, Recurrences, Commitments, People, Metadata) and adminme-finance.xlsx (Raw Data, Accounts, Metadata). Sheets requiring unregistered event types (Lists, Members, Assumptions, Dashboard, Balance Sheet, Pro Forma, Budget vs Actual) skipped with TODO markers.
  - File locking via `fcntl.flock` with 10s timeout; atomic write via temp+rename.
  - 5s debounce with cancel-on-new-event; `regenerate_now` bypasses for tests.
  - Derived-cell protection at cell level + sheet protection on read-only sheets (placeholder password `"adminme-placeholder"` — real secrets in prompt 16).
  - XlsxQueryContext holds handles to 7 projection connections (parties, tasks, commitments, recurrences, calendars, places_assets_accounts, money). Runner's register() unchanged; bootstrap constructs the projection with the context after other projections start.
  - 28 new unit tests (ops workbook 13, finance 8, emit 4, debounce 3) + 1 integration test + demo_xlsx_forward.py smoke.
  - §2.2 grep audit: every `log.append` in the xlsx projection is for `xlsx.regenerated` only.
  - Ruff clean, mypy clean (109 source files), all inviolable greps OK (one pre-existing false positive in pipelines/runner.py docstring noted, not introduced by 07b).
- **Carry-forward for prompt 07c (xlsx reverse daemon)**:
  - Sidecar state JSON (last-known sheet state) is NOT written in this prompt. 07c writes it after each forward regeneration OR reads from the just-written workbook at adapter startup. 07c decides the implementation path.
  - `xlsx.regenerated` emit provides a signal 07c's reverse adapter can subscribe to, to avoid re-diffing a workbook that forward just wrote.
  - Sheet protection passwords hardcoded to `"adminme-placeholder"` — 07c accepts this; real secrets flow lands in prompt 16.
  - Stub forward.py / reverse.py / schemas.py scaffolding files (from prompt 05) remain untouched — 07c may repurpose or delete.
- **Carry-forward for prompt 08**:
  - No query functions in xlsx projection; nothing for Session to wrap. Sheet builders read other projections' queries (which are the ones Session wraps).
- **Carry-forward for prompt 10+**:
  - Dashboard / Balance Sheet / Pro Forma / Budget vs Actual sheets skipped — derived-math pipelines build them later.
- **Carry-forward for future bootstrap (prompt 16)**:
  - Real xlsx daemon lifecycle (start-on-boot, stop-on-shutdown) lands in bootstrap. Phase A is code + tests only.

### Prompt 07c-α — xlsx round-trip foundations (schema + sidecar + diff core)
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/07c-alpha-foundations.md.
- **Session merged**: PR #20, commits aa395dd / 7305acd / fcdb592 / 1d770ec, merged 2026-04-24.
- **Outcome**: MERGED
- **Note**: Part 1 of a two-prompt split. 07c-β consumes what this prompt lands and ships the reverse daemon + integration round-trip. Split exists per partner_handoff PM-2 / PM-15: full reverse daemon plus its test pyramid does not fit one Claude Code session window.
- **Evidence**:
  - Two new system events at v1 in `adminme/events/schemas/system.py`: `xlsx.reverse_projected` (workbook_name, detected_at, sheets_affected, events_emitted, duration_ms) and `xlsx.reverse_skipped_during_forward` (workbook_name, detected_at, skip_reason: forward_lock_held). Schema-registry test asserts both register at v1.
  - `scripts/verify_invariants.sh` `ALLOWED_EMITS` regex extended; `ALLOWED_EMIT_FILES` left alone with maintenance comment explaining why (07c-β's reverse daemon lives outside `adminme/projections/` so the projection-emit auditor doesn't apply).
  - PM-10 disposition: deleted `adminme/projections/xlsx_workbooks/forward.py`, `reverse.py`, `schemas.py` — three 22-line scaffolding stubs from prompt 02 with no callers. Pre-delete grep confirmed zero imports.
  - `InstanceConfig.xlsx_workbooks_dir` flipped from `projections/.xlsx-state/` to `projections/xlsx_workbooks/`. Sibling `.xlsx-state/` resolves via `xlsx_workbooks_dir.parent / ".xlsx-state"`. Sibling pathway is required so 07c-β's watchdog scoped to the workbooks dir cannot self-trigger on sidecar writes. Existing 29-test xlsx suite unchanged — all callers use `config.xlsx_workbooks_dir` directly so the path change is transparent.
  - Sidecar I/O module at `adminme/projections/xlsx_workbooks/sidecar.py`: `sidecar_dir`, `sidecar_path`, `write_sheet_state`, `write_readonly_state`, `read_sheet_state`, `read_readonly_state`, `hash_readonly_sheet`. Atomic writes via `.tmp.<pid>` + `os.replace`. Bidirectional sheets persist `{"rows": [...]}`; read-only sheets persist `{"content_hash": "<sha256-hex>"}` only (no row data — hash-only is sufficient for the WARN signal 07c-β needs on read-only edits).
  - Forward daemon extended: `XlsxWorkbooksProjection._regenerate` now calls a new `_write_sidecar_for(workbook, xlsx_path)` helper as the LAST step inside the workbook lock. Helper opens the just-written xlsx with `openpyxl.load_workbook(data_only=True)` and writes per-sheet sidecar JSON. Reading back from the xlsx (rather than re-querying projections) ensures byte-alignment between sidecar and workbook on disk.
  - Two module-level constants `_BIDIRECTIONAL_SHEETS` and `_READONLY_SHEETS` are now the single source of truth for which sheets each workbook contains; both the `xlsx.regenerated` payload's `sheets_regenerated` list and the sidecar writer derive from them.
  - New `adminme/daemons/xlsx_sync/` package with binding placement note: "this daemon is L1-adjacent, NOT a projection per [§2.2] — it emits events." Per-sheet diff descriptors at `sheet_schemas.py` (Tasks/Commitments/Recurrences/Raw Data) declaring id_column, editable_columns (frozenset OR per-row callable for Raw Data), always_derived, add/update/delete event mappings, undo-window flag, new_id_prefix, and drop dispositions for non-emitting cases. Pure-functional diff core at `diff.py` with type normalization (float tol 1e-9, datetime/date → isoformat, None ≡ "", int↔float as floats) and id-column-edit-as-delete-plus-add semantics. Diff core has zero I/O — no openpyxl, no watchdog, no event log.
  - 28 new unit tests across four sites: 1 schema-registry extension, 8 sidecar I/O, 4 forward-writes-sidecar, 15 diff core (the diff site exceeds the ≥10 requirement; sidecar site exceeds ≥6). [QC correction: PR description and earlier draft of this entry said "23 new" — actual tally is 1+8+4+15 = 28.]
  - Ruff clean, mypy clean, `bash scripts/verify_invariants.sh` clean. Full test suite passes.
  - **QC note (descriptor visibility):** descriptor symbols in `sheet_schemas.py` are private (`_TASKS`, `_COMMITMENTS`, `_RECURRENCES`, `_RAW_DATA`); accessible only via `descriptor_for(workbook, sheet)` and `editable_columns_for(descriptor, row)` and the `BIDIRECTIONAL_DESCRIPTORS` tuple. Earlier 07c-β draft cited them by `TASKS_DESCRIPTOR`-style names that don't exist; 07c-β was patched in refactor to use the public accessor everywhere. See PM-16.
  - **QC note (read_only flag):** `_write_sidecar_for` opens the just-written xlsx with `read_only=False`. `read_only=True` would be cheaper for read-back. Not a violation; not blocking; flagging for awareness only.
  - **QC note (test fixture amendment):** during Commit 3 development, the `money_flow.manually_added` test fixture initially omitted `added_by_party_id` and used a `linked_account` field since removed from schema. Claude Code amended the fixture to include `added_by_party_id: "p1"` and re-ran; tests passed. Healthy debug loop. Surfaces UT-7 (actor attribution) as relevant for 07c-β's daemon emit path — daemon will use `actor_identity="xlsx_reverse"` per 07c-β draft until prompt 08 closes UT-7.
- **Carry-forward for prompt 07c-β (reverse daemon + integration round-trip)**:
  - Descriptors at `adminme/daemons/xlsx_sync/sheet_schemas.py` — bidirectional set is exactly Tasks / Commitments / Recurrences / Raw Data. People / Accounts / Metadata are read-only; 07c-β handles them via a separate "WARN if hash drifted" code path, NOT via descriptors here. **Reach descriptors via `descriptor_for(workbook, sheet)`; iterate via `BIDIRECTIONAL_DESCRIPTORS`. Private symbols (`_TASKS` etc.) are not exported.**
  - Diff core at `adminme/daemons/xlsx_sync/diff.py` — sync, pure-functional, returns `DiffResult(added, updated, deleted, dropped_edits)`. Daemon wraps it with watchdog → asyncio bridge, lock acquisition, undo window, sensitivity preservation, cold-start handling.
  - Sidecar I/O at `adminme/projections/xlsx_workbooks/sidecar.py` — forward already writes inside its lock. Reverse must rewrite at the end of each cycle (per BUILD.md §3.11 line 1015) so two principal saves diff against each other. Path: `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json`, sibling of workbooks dir.
  - The reverse daemon emits domain events on principal authority. The full UT-7 actor-attribution path (which member id authored the edit) does NOT resolve here; it resolves in prompt 08 when the event router knows about Session/principal_member_id. 07c-β stubs actor with `actor_identity="xlsx_reverse"` per the draft's envelope template.
  - The `xlsx.regenerated` system event provides a signal 07c-β's reverse can use to skip diffing a workbook the forward daemon just wrote.
  - **Recurrences sheet shape:** the 07b-built Recurrences sheet has columns `recurrence_id, title, cadence, next_due, assigned_member, notes, active, last_completed_at`. There is no `rrule` column on the sheet — the projection's underlying `rrule` is rendered into the human-readable `cadence` column at forward time. 07c-β's Recurrences ADD must map sheet `cadence` → `RecurrenceAddedV1.rrule` directly. The schema accepts free-text (`str`, `min_length=1`) — no RFC 5545 validation. Whatever the principal typed in `cadence` passes through unchanged. (Earlier 07c-β draft assumed both columns existed; patched in refactor.)
- **Carry-forward for prompt 08 (Session + scope + governance)**:
  - UT-7 opens here, resolves there: route reverse-emitted events through Session/guardedWrite so the actor (`added_by_party_id`, `principal_member_id`, etc.) is principal-attributed.
- **Carry-forward for prompt 16 (bootstrap wizard)**:
  - Daemon lifecycle (start-on-boot, stop-on-shutdown) for the reverse daemon — shipped in 07c-β, wired into bootstrap in 16.
  - Adding `observation_mode_active` to forward emit payload per D5 — deferred to 16 alongside observation-mode wiring.
- **Carry-forward for prompt 17 (CLI)**:
  - `adminme projection rebuild xlsx_workbooks` CLI per BUILD.md §3.11 line 1054 (rebuild deletes both workbook files plus the sidecar tree, then regenerates).

### Prompt 07c-β — xlsx reverse daemon + integration round-trip
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/07c-beta-reverse-daemon.md.
- **Session merged**: PR #21, commits ffa6d9c / bf649ed / 00bff7d / 2788761, merged 2026-04-25.
- **Outcome**: MERGED
- **Note**: Part 2 of a two-prompt split. 07c-α (PR #20, merged 2026-04-24) landed schema/descriptors/diff/sidecar; together with this prompt it closes the xlsx round-trip and resolves UT-6.
- **Evidence**:
  - `XlsxReverseDaemon` lands at `adminme/daemons/xlsx_sync/reverse.py`. Public API: `start()` (begins watchdog observer; idempotent), `stop()` (cancels pending cycles + undo-window tasks + observer; idempotent), `run_cycle_now(workbook)` (bypasses the watchdog and flush wait; used by tests). Constructor signature: `(config, query_context, *, event_log, flush_wait_s=2.0, forward_lock_timeout_s=10.0, delete_undo_window_s=5.0)` per spec.
  - Per-cycle algorithm follows BUILD.md §3.11 lines 993–1080 verbatim: flush wait → forward lock acquisition (timeout → emit `xlsx.reverse_skipped_during_forward` and return — NO `xlsx.reverse_projected`) → load workbook with `openpyxl.load_workbook(data_only=True)` → for each bidirectional sheet, diff live rows vs sidecar (cold-start sheets emit nothing) → for each read-only sheet, hash live rows + WARN on drift → rewrite ALL sidecar JSON to current state (so subsequent cycles diff cleanly) → release lock → emit `xlsx.reverse_projected` with `events_emitted` (envelope ids) + `sheets_affected` + `duration_ms`.
  - Watchdog→asyncio bridge: `watchdog.observers.Observer` schedules a `FileSystemEventHandler` whose callbacks run on watchdog's thread; the daemon hops to its asyncio loop via `loop.call_soon_threadsafe(self._schedule_cycle, workbook_name)`. The on-loop `_schedule_cycle` cancels any pending debounce for the workbook and schedules a fresh one after `flush_wait_s`. Per-workbook serialization is enforced by an internal `asyncio.Lock` keyed per workbook; tests assert `_max_concurrent_observed <= 1`.
  - Undo window: deletes on sheets where `descriptor.deletes_use_undo_window=True` queue an `asyncio.Task` via `_schedule_undo_delete` that sleeps `delete_undo_window_s` then emits the delete event. Subsequent cycles observing the row return cancel the task via `cancel_pending_delete`. Note: emits scheduled inside the undo window do NOT count toward the cycle's `events_emitted` list (they fire after the cycle's terminal `xlsx.reverse_projected`). Acceptable observability gap, documented in the daemon's module docstring.
  - All four bidirectional sheet pathways wired: Tasks (3 emit functions) + Commitments (3) + Recurrences (3) + Raw Data (2 — money_flow.manually_added + money_flow.manually_deleted; updates not allowed per descriptor). Each emit derives `actor_identity="xlsx_reverse"` (UT-7 placeholder).
  - Sensitivity preservation: every emit copies sensitivity from the sidecar's prior state (or defaults `normal` for ADDs). Privileged → privileged; sensitive → sensitive.
  - 22 new tests: 6 unit (schema registry, descriptor bridge, daemon shape, undo-window cancel, sidecar rewrite at end of cycle, lock-contention skip path) + 6 integration (round-trip per pathway, plus cold-start, multi-sheet, read-only WARN drift) + 10 algorithm-cell coverage (each emit path + the lock-contention emit + the WARN emit).
  - `scripts/demo_xlsx_roundtrip.py` smoke script demonstrates user edit → cycle → emit → rebuild → workbook restored.
  - Ruff clean, mypy clean, `bash scripts/verify_invariants.sh` clean. Full test suite 411 passed / 1 skipped.
- **Carry-forward for prompt 08 (Session + scope + governance — opens UT-7)**:
  - Reverse daemon emits use `actor_identity="xlsx_reverse"` literal at line 91 (placeholder). Prompt 08 routes through Session/guardedWrite so emits are principal-attributed.
- **Carry-forward for prompt 16 (bootstrap)**:
  - Daemon lifecycle (start-on-boot, stop-on-shutdown) wires into bootstrap §7.
  - Observation-mode pass-through: reverse daemon does not check observation mode; forward daemon emits `xlsx.regenerated` regardless. Bootstrap wires the observation_mode_active payload field per D5.
- **Carry-forward for prompt 17 (CLI)**:
  - `adminme projection rebuild xlsx_workbooks` per BUILD.md §3.11 line 1054 (rebuild deletes both workbook files + sidecar tree, then regenerates).

### Prompt 09a — skill runner wrapper (around OpenClaw)
- **Refactored**: by Partner in Claude Chat, 2026-04-26. Prompt file: prompts/09a-skill-runner.md (~250 lines, quality bar = 08b).
- **Session merged**: PR #29, commits ff0b319 / 2ba917e / 9c92b33 / a55277e, merged 2026-04-26.
- **Outcome**: MERGED.
- **Evidence**:
  - `adminme/lib/skill_runner/wrapper.py` — `run_skill()` 9-step flow per [BUILD.md L4-continued] + [ADR-0002]. Provider-preference fallback iterates inside the wrapper; one POST per provider per attempt; deterministic 4xx and malformed 200 short-circuit the loop. Module-level event-log DI via `set_default_event_log()` mirrors 08b's `outbound()` pattern.
  - `adminme/lib/skill_runner/pack_loader.py` — parses `pack.yaml` + `SKILL.md` frontmatter + `schemas/input.schema.json` + `schemas/output.schema.json` + optional `handler.py`. Schemas validated with `Draft202012Validator.check_schema()`; cache by `(pack_id, version)`; `invalidate_cache()` test hook.
  - `packs/skills/classify_test/` — full pack scaffold for tests; trivial `(text) -> (is_thing, confidence)` classifier.
  - `adminme/events/schemas/domain.py` — `SkillCallRecordedV2.input_tokens`, `output_tokens`, `cost_usd`, and `openclaw_invocation_id` relaxed to Optional per `[ADR-0002]` graceful-degradation clause.
  - `adminme/events/schemas/system.py` — `SkillCallFailedV1` (closed-enum `failure_class`) and `SkillCallSuppressedV1` (closed-enum `reason`) registered at v1.
  - `scripts/verify_invariants.sh` — extended with a single-seam check that `skill.call.recorded`, `skill.call.failed`, `skill.call.suppressed` are emitted only from `adminme/lib/skill_runner/wrapper.py`. Same single-seam pattern as the xlsx forward projector.
  - `pyproject.toml` — `jsonschema >=4.21` (runtime, used by wrapper + pack_loader) and `respx` (dev) added; mypy `ignore_missing_imports` overrides for both. `markers = ["requires_live_services: ..."]` declared so the integration stub doesn't warn.
  - 14 unit tests (`tests/unit/test_skill_wrapper.py`) covering the full failure-mode pyramid: happy path with body-shape assertion, input invalid, sensitivity refused, scope insufficient, provider fallback (5xx → next provider), all providers 5xx, malformed 200 envelope, timeout, handler raises (defensive default returned + raw response saved), output validation fails, observation-mode short-circuit, dry-run short-circuit, large-input spillover, token/cost graceful degradation. All HTTP routed through `httpx.MockTransport` (the AdministrateMe sandbox has no live OpenClaw gateway).
  - 10 pack-loader tests (`tests/unit/test_pack_loader.py`) — manifest fields, schema validation samples, no-handler / handler-loaded / handler-without-`post_process`, cache hit, cache invalidation, malformed yaml, invalid JSON Schema.
  - 6 schema tests (`tests/unit/test_event_schemas.py`) — `SkillCallRecordedV2` accepts `None` tokens and cost; `SkillCallFailedV1` round-trip + rejects bad `failure_class`; `SkillCallSuppressedV1` round-trip + rejects bad `reason`.
  - `tests/integration/test_skill_wrapper_live.py` — live integration stub marked `requires_live_services`, skipped in Phase A.
  - `[§7]`/`[D7]`: `skill.call.failed` and `skill.call.suppressed` registered at v1.
  - `[§8]`/`[D6]`: zero new SDK imports; `verify_invariants.sh` clean.
  - `[ADR-0002]`: wrapper POSTs `/tools/invoke` with `tool: "llm-task"`; provider iteration in wrapper, not in OpenClaw.
  - **QC overshoot (Partner, 2026-04-26):** Commit 1 also relaxed `SkillCallRecordedV2.openclaw_invocation_id` to Optional (in addition to the three token/cost fields the prompt named). Defensible per [ADR-0002] provenance table — `openclaw_invocation_id` is documented as "from `/tools/invoke` response if present in response envelope." Logged as quality signal, not drift.
- **Carry-forward for prompt 09b**:
  - `pack_loader` accepts the canonical pack shape; `classify_thank_you_candidate` will be the second pack to load through it.
  - `run_skill` is stable; 09b just supplies a pack and asserts the round-trip.
- **Carry-forward for prompt 10a**:
  - Pipelines call `await run_skill(skill_id, inputs, SkillContext(session=..., correlation_id=...))`. Causation wiring (set causation_id on `skill.call.recorded` to the triggering domain event) lands in pipeline runner.
  - The wrapper's `_resolve_pack_root` accepts absolute paths, repo-relative slugs, and `namespace:name` forms; production pipelines should pass the absolute path resolved via `InstanceConfig.packs_dir`.
- **Carry-forward for prompt 16 (bootstrap)**:
  - Bootstrap §6 wires `OPENCLAW_GATEWAY_TOKEN` from 1Password; 09a falls back to env for now.
  - Bootstrap §7 calls `set_default_event_log(...)` at service start so production callers don't need to construct `_Runtime` themselves.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - `tests/integration/test_skill_wrapper_live.py` is the test that activates against the live OpenClaw gateway. The skip in 09a is its Phase-A placeholder.

### Prompt 09b — first canonical skill pack (classify_thank_you_candidate)
- **Refactored**: by Partner in Claude Chat, 2026-04-27. Prompt file: prompts/09b-first-skill-pack.md (~290 lines, quality bar = 09a).
- **Session merged**: PR #<PR-09b>, commits bae1681 / 585d9bf / 36fd872 / <commit4-09b>, merged <merge-date-09b>.
- **Outcome**: MERGED.
- **Evidence**:
  - `packs/skills/classify_thank_you_candidate/` — full pack at version 1.3.0 per [REFERENCE_EXAMPLES.md §3]; pack.yaml + SKILL.md (frontmatter + §3 body) + schemas/{input,output}.schema.json + prompt.jinja2 + handler.py.
  - `handler.py` — top-level `post_process(raw, inputs, ctx)`; only logic is the urgency-coercion safety net per [REFERENCE_EXAMPLES.md §3 lines 1389-1395]. Zero `adminme_platform`-style imports.
  - `tests/test_skill.py` — pack-loads-cleanly canary + handler-direct unit cases; 4 tests (loads + well-formed pass-through + missing-urgency coercion + non-dict defensive).
  - **QC overshoot (Partner, 2026-04-27 post-merge):** prompt floor for `tests/test_skill.py` was 1 test (loader canary only); shipped 4 (loader canary + 3 handler-direct unit cases). Adds the non-dict defensive case that mirrors the handler's first guard. Logged as quality signal, not drift. Prompt-floor for `tests/integration/test_classify_thank_you_pack.py` was 4 (3 fixtures + 1 handler-direct safety net) — shipped exactly 4. Total 09b new tests: **8** (4 unit + 4 integration), vs. floor 5.
  - `tests/integration/test_classify_thank_you_pack.py` — three fixture tests (kleins_hosted_us, reciprocal_coffee, coparent_pickup) + handler-direct safety-net test; 4 tests; all HTTP via `httpx.MockTransport`.
  - `bootstrap/pack_install_order.yaml` — NEW; single-entry list queued for prompt 15 / 16 install path.
  - `[§8]`/`[D6]`: zero LLM/embedding SDK imports; `verify_invariants.sh` clean.
  - `[§12.4]`: tenant-identity strings (stice-james, Klein, Mike) confined to `packs/skills/classify_thank_you_candidate/tests/fixtures/` and integration test fixture-construction sites with `# fixture:tenant_data:ok`.
  - `[ADR-0002]`: pack consumes `run_skill()` which POSTs to `/tools/invoke` with `tool: "llm-task"`. No new HTTP seams.
- **Carry-forward for prompt 10a (pipeline runner)**:
  - `bootstrap/pack_install_order.yaml` exists with one entry. Pipeline runner does not consume this file directly — that's prompt 15/16. 10a should reference packs by absolute path resolved via `InstanceConfig.packs_dir` (per UT-9 SOFT note).
- **Carry-forward for prompt 10b-ii (thank_you_detection pipeline)**:
  - The pipeline calls `await run_skill("classify_thank_you_candidate", inputs, ctx)` against this pack. Inputs match `schemas/input.schema.json`; outputs decoded per `schemas/output.schema.json`. The pipeline emits `commitment.proposed` (or equivalent) when `is_candidate=true`; that emit happens in pipeline code, not in skill code (skills never emit events directly per [REFERENCE_EXAMPLES.md §3 line 1527]).
- **Carry-forward for prompt 15 (OpenClaw integration)**:
  - `bootstrap/pack_install_order.yaml` is the source-of-truth list of built-in packs to register with OpenClaw. Prompt 15's persona-compiler / pack-registration path reads this file.
- **Carry-forward for prompt 16 (bootstrap wizard)**:
  - Bootstrap §6 / §7 walks `bootstrap/pack_install_order.yaml` and calls `openclaw skill install` per entry. Phase B only.

### Prompt 10a — pipeline runner
- **Refactored**: by Partner in Claude Chat, 2026-04-26. Prompt file: prompts/10a-pipeline-runner.md (~340 lines, quality bar = 09a).
- **Session merged**: PR #33, commits edb3920 / 1fa335a / f30a6f2 / c96cf55, merged 2026-04-26.
- **Outcome**: MERGED.
- **Evidence**:
  - `adminme/pipelines/base.py` — `Pipeline` Protocol + `PipelineContext` frozen dataclass + `Triggers` TypedDict + `PipelinePackLoadError`.
  - `adminme/pipelines/pack_loader.py` — `load_pipeline_pack()` parses `pipeline.yaml` + imports `handler.py` + instantiates `runtime.class`; cache by `(pack_id, version)`.
  - `adminme/pipelines/runner.py` — `PipelineRunner` with `register()` / `discover(builtin_root, installed_root)` / `start()` / `stop()` / `status()`. Discovery walks two roots: in-tree `adminme/pipelines/` and `instance_config.packs_dir / "pipelines"`. Per UT-9, callers pass absolute paths.
  - `tests/fixtures/pipelines/echo_logger/` — trivial fixture pack (`pipeline.yaml` + `handler.py` with `EchoLoggerPipeline`); used by runner tests. NOT a production pipeline.
  - `tests/fixtures/pipelines/echo_emitter/` — sibling fixture pack used by the integration causation test; emits `messaging.sent` with `causation_id=ctx.triggering_event_id` (no new schema registration, reuses `messaging.sent` v1).
  - 8 unit tests (`tests/unit/test_pipeline_pack_loader.py`) + 5 unit tests (`tests/unit/test_pipeline_runner.py`) + 4 integration tests (`tests/integration/test_pipeline_runner_integration.py`) — total 17 new tests (vs. floor 14).
  - **QC overshoot (Partner, 2026-04-26 post-merge):** prompt floor 14 tests; shipped **17** (overshoot from 3 extra `pack_loader` cases — wrong-kind manifest, missing-entrypoint-file, class-without-handle protocol-check). Logged as quality signal, not drift. Full repo: 429 passed / 1 skipped per Claude Code's transcript.
  - **QC silent-architecture decision (Partner, 2026-04-26 post-merge):** the prompt's Commit 1 plan named test #6 as "runtime.class not present in `handler.py`" combined with "instance does not implement handle"; Claude Code factored these into TWO separate tests (`test_missing_runtime_class_raises` and `test_class_missing_handle_raises`), which is why the unit-test count overshot 5→8. Cleaner separation. Accept and note.
  - `[§7.3]` (no projection direct writes): pipeline → projection canary in `verify_invariants.sh` is armed and clean.
  - `[§7.4]`/`[§8]`/`[D6]`: zero new SDK imports; `verify_invariants.sh` clean.
  - `[§7.7]` (one failure does not halt the bus): bus checkpoint advancement on success, non-advancement on raise — both asserted in tests.
  - `[ADR-0002]`: `PipelineContext.run_skill_fn` is the one seam pipelines use to call skills; no direct OpenClaw HTTP calls from pipeline code.
- **Carry-forward for prompt 10b-i** (identity_resolution + noise_filtering — supersedes original 10b carry-forward per PR #37 split):
  - `PipelineRunner.discover(builtin_root, installed_root)` will pick up packs added under both roots. **10b-i must verify the path convention against shipped 10a runner.py before drafting** — the split memo at `docs/01-split-memo-10b.md` specifies `packs/pipelines/identity_resolution/` etc. (mirroring 09b's `packs/skills/`), which is consistent with `installed_root = instance_config.packs_dir / "pipelines"` if `packs_dir == "packs/"`. Open as UT-11 in partner_handoff.md.
  - Each 10b-i pipeline's `handle()` receives a `PipelineContext` with pre-built Session + run_skill_fn + outbound_fn + guarded_write. They do NOT construct these themselves.
  - Causation wiring: pipelines emitting derived events set `causation_id=ctx.triggering_event_id` on the emitted envelope. The integration test for `echo_emitter` is the canary.
- **Carry-forward for prompt 10b-ii** (commitment_extraction + thank_you_detection — supersedes original 10b carry-forward per PR #37 split):
  - Same `PipelineRunner.discover()` path convention as 10b-i; depends on 10b-i merging first so identity_resolution is live (commitment_extraction `find_party_by_identifier` consumes parties auto-created by identity_resolution).
  - `thank_you_detection` consumes the `classify_thank_you_candidate` pack from 09b end-to-end (already on main).
- **Carry-forward for prompt 10c** (proactive pipelines):
  - 10c registers proactive pipelines with OpenClaw via workspace-prose AGENTS.md + `openclaw cron add` per cheatsheet Q3. The runner's `discover()` reads `triggers.schedule` / `triggers.proactive` but does NOT subscribe them; 10c's bootstrap step picks up the registered packs and writes the AGENTS.md sections.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - `pipeline_supervisor.py` remains a docstring-only stub. Bootstrap §7 wires `PipelineRunner` lifecycle (start-on-boot via the supervisor, stop-on-shutdown).
  - Bootstrap §6 calls `set_default_event_log(...)` (already from 09a). Pipeline runner uses the same shared event log.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - Confirm at least one reactive pipeline fires end-to-end against a live bus + live skill-runner against live OpenClaw.

### Prompt 10b-i — reactive pipelines (identity_resolution + noise_filtering)
- **Refactored**: by Partner in Claude Chat, 2026-04-26. Prompt file: `prompts/10b-i-identity-and-noise.md` (320 lines, quality bar = 09b + 10a). Pre-split memo at `docs/01-split-memo-10b.md`. **The refactored prompt was committed to the repo this round** (a deviation from the historical paste-only convention; see PM-21 update in `partner_handoff.md`).
- **Session merged**: PR #38, commits 22c6195 / 73880d4 / 4c19c80 / 0a3250f, merged 2026-04-26.
- **Outcome**: MERGED.
- **Evidence**:
  - `packs/pipelines/identity_resolution/{pipeline.yaml,handler.py,tests/test_pack_load.py}` — `IdentityResolutionPipeline` heuristic-only resolver; emits `party.created` + `identifier.added` on miss; `identity.merge_suggested` above 0.85 threshold; never auto-merges per [BUILD.md §1130].
  - **Open-question disposition (silent architectural decision, accepted)**: `PipelineContext` does not currently expose a parties-projection connection. Per the prompt's "Open question for orientation", picked option (2): the production pipeline ships in degenerate-clean mode — `_default_candidate_loader` returns an empty list, so every miss creates a new party. The merge-threshold branch is exercised by unit tests injecting a custom candidate loader. The seam is fully factored on `IdentityResolutionPipeline.__init__(candidate_loader=...)`, so the future bridge from runner is one constructor-arg wiring, not a handler refactor. **This decision is load-bearing for 10b-ii — see "Carry-forward for prompt 10b-ii" below.**
  - `packs/pipelines/noise_filtering/{pipeline.yaml,handler.py,tests/test_pack_load.py}` — `NoiseFilteringPipeline` calls `classify_message_nature` once per inbound; emits `messaging.classified` with full skill provenance; defensive-default = "personal" / confidence 0.0 on skill failure (does NOT propagate exceptions per [§7.7]).
  - `packs/skills/classify_message_nature/` — full 09b-shape skill pack at v2.0.0 ([BUILD.md §1136] names it `classify_message_nature@v2`). 3 unit tests via pack-loader canary + handler-direct. **Minor undershoot vs 09b reference** (`classify_thank_you_candidate` ships 4 handler-direct cases; this pack folds the non-dict-input case into the `coerces_when_classification_missing` test). Cosmetic; coverage is the same.
  - `adminme/events/schemas/crm.py` — appended `IdentityMergeSuggestedV1` registered at v1; `candidate_kind` is closed `Literal["email", "phone", "imessage_handle"]`.
  - `adminme/events/schemas/ingest.py` — appended `MessagingClassifiedV1` registered at v1; `classification` is closed `Literal["noise", "transactional", "personal", "professional", "promotional"]`.
  - Integration tests at `tests/integration/test_pipeline_10b_i_integration.py` — 4 round-trip tests against the live runner.
  - **Total new tests: 22** (3 skill-pack handler-direct + 1 identity_resolution pack-load + 8 unit + 1 noise_filtering pack-load + 5 unit + 4 integration). Suite tally: 423 → 447 passed. **QC F-1 (cosmetic):** the original BUILD_LOG entry written in the merged Commit 4 claimed 24 tests; actual is 22 (the breakdown overcounted identity_resolution unit by 1 and noise_filtering unit by 1). Corrected here.
  - **QC F-3 (overshoot, positive signal):** extra unit test `test_exact_match_returns_without_emit` shipped beyond the prompt's Commit 2 plan — covers the case where an existing identifier already owns the inbound's `value_normalized` (return without emit; the parties projection already has the link). Smart addition; logged.
  - `[§7.3]` (no projection direct writes): pipelines emit only via `ctx.event_log.append`; pipeline→projection canary in `verify_invariants.sh` clean.
  - `[§7.4]` / `[§8]` / `[D6]`: zero new SDK imports (heuristics are pure-Python; the one skill call goes through `ctx.run_skill_fn` per [ADR-0002]).
  - `[§7.7]` (pipeline failure does not halt bus): `noise_filtering` catches `SkillInputInvalid` / `SkillOutputInvalid` / `OpenClawTimeout` / `OpenClawUnreachable` / `OpenClawResponseMalformed` and emits the defensive default. **QC F-2 (soft-watch for 10b-ii):** does NOT catch `SkillSensitivityRefused` or `SkillScopeInsufficient` (also exported from `adminme.lib.skill_runner`). For 10b-i these can't fire (`classify_message_nature` has `sensitivity_required: normal`, `context_scopes_required: []`), so it's clean today. 10b-ii's `classify_commitment_candidate` and `extract_commitment_fields` should either match the same sensitivity/scopes shape OR widen the `except` list. Verify during 10b-ii refactor.
  - `[D7]`: both new event types register at v1.
  - Causation-id wiring: every emit in both pipelines uses `causation_id=ctx.triggering_event_id` per the 10a echo_emitter canary contract.
  - `verify_invariants.sh` exit 0. PM-14 honored: pipelines live under `packs/pipelines/`, NOT `adminme/projections/`; `ALLOWED_EMIT_FILES` left untouched (correct — pipelines are not projections). UT-11 confirmed CLOSED by this merge: convention is `packs/pipelines/<name>/` mirroring 09b's `packs/skills/<name>/`.
- **Carry-forward for prompt 10b-ii** (commitment_extraction + thank_you_detection):
  - **Parties-DB seam decision is load-bearing.** `commitment_extraction` per REFERENCE_EXAMPLES.md §2 calls `find_party_by_identifier` to resolve the sender before classification. 10b-i punted (degenerate-clean), but 10b-ii cannot punt the same way without making `commitment_extraction` always fail sender-resolution. Three options to evaluate at 10b-ii orientation:
    - (a) Thread `parties_conn_factory` through `PipelineContext` as a Commit 1 of 10b-ii (one extra commit; sets the precedent for 10c+).
    - (b) Use 10b-i's injectable-loader pattern (`candidate_loader: Callable | None`) and ship `commitment_extraction` in degenerate mode too (no DB read; weaker behavior but cohesive).
    - (c) Split 10b-ii itself into 10b-ii-α (parties-DB seam wiring + `commitment_extraction`) and 10b-ii-β (`thank_you_detection`).
    The split memo flagged (c) as a watch; the partner_handoff entry UT-12 tracks this open. **Resolve at 10b-ii orientation.**
  - `find_party_by_identifier` is backed (in degenerate mode) by parties auto-created by `identity_resolution` — every unresolved sender from 10b-i merging onward becomes a fresh party. So even if 10b-ii also runs degenerate, hits will accumulate naturally over time.
  - `messaging.classified` events let `commitment_extraction` skip noise/transactional classifications cheaply (subscribe to the classification, not the raw inbound).
  - The pipeline-pack shape pattern (yaml + handler + test_pack_load) is now duplicated; 10b-ii continues this exact shape.
  - The defensive-default-on-skill-failure pattern from `noise_filtering` carries forward — but **widen the `except` list** if 10b-ii's skills declare a non-`normal` sensitivity or non-empty context scopes (see QC F-2 above).
  - `commitment.suppressed` event type registers at v1 in `domain.py` next to `commitment.proposed` (existing v1).
- **Carry-forward for prompt 10c** (proactive pipelines):
  - The `triggers.events: []` + `triggers.proactive: true` shape is the unfilled half of the manifest contract; 10b-i's two manifests use only `triggers.events`.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - `bootstrap §8` runs `PipelineRunner.discover(builtin_root=adminme/pipelines/pipeline_packs, installed_root=instance_config.packs_dir/"pipelines")`. The path layout is `packs/pipelines/<name>/pipeline.yaml`. Bootstrap copies builtin packs into the instance dir on first run. **UT-11 closed by 10b-i shipping under `packs/pipelines/`.**
- **Carry-forward for prompt 19** (Phase B smoke test):
  - Confirm `identity_resolution` correctly resolves a test sender against a seeded party (requires the parties-DB seam to be threaded by then), and `noise_filtering` calls real OpenClaw and classifies a transactional receipt as `transactional`.

### Prompt 10b-ii-α — reactive pipelines (parties-DB seam + commitment_extraction)
- **Refactored**: by Partner in Claude Chat, 2026-04-28. Prompt file: `prompts/10b-ii-alpha-commitment-extraction.md` (~370 lines, quality bar = 10b-i + parties-DB seam). Secondary-split memo at `docs/02-split-memo-10b-ii.md`.
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge-date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - `adminme/pipelines/base.py` — `PipelineContext` extended with `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None` per `docs/02-split-memo-10b-ii.md` §10b-ii-α. Closes UT-12 via option (a)+(c).
  - `adminme/pipelines/runner.py` — `PipelineRunner.__init__` gains optional `parties_conn_factory` kwarg; `_make_callback` threads into `PipelineContext`. Backward compatible (default `None`) — all 5 existing 10a runner-integration construction sites stay green without modification.
  - `adminme/events/schemas/domain.py` — appended `CommitmentSuppressedV1` registered at v1 per `[D7]`; `reason` is closed `Literal["below_confidence_threshold", "dedupe_hit", "skill_failure_defensive_default"]`.
  - `packs/skills/classify_commitment_candidate/` — full 09b-shape skill pack at v3.0.0 (`BUILD.md §L4` names it `@^3.0.0`). 4 unit tests via pack-loader canary + handler-direct.
  - `packs/skills/extract_commitment_fields/` — full 09b-shape skill pack at v2.1.0 (`BUILD.md §L4` names it `@^2.1.0`). Output schema round-trips into `CommitmentProposedV1` without coercion drift (`kind` and `urgency` enums match the Literal exactly). 4 unit tests.
  - `packs/pipelines/commitment_extraction/{pipeline.yaml,config.example.yaml,config.schema.json,handler.py,tests/test_pack_load.py}` — `CommitmentExtractionPipeline` per `BUILD.md §L4` + `REFERENCE_EXAMPLES.md §2` architecture; resolves sender via `find_party_by_identifier` using `ctx.parties_conn_factory`; calls classify → extract; emits `commitment.proposed` (above review_threshold = confident; between min and review_threshold = weak) or `commitment.suppressed` (below or on skill failure / sender unresolvable / factory missing); per-member overrides config-driven; tenant-agnostic (placeholder member ids in `config.example.yaml`).
  - `tests/unit/test_pipeline_commitment_extraction.py` — 11 handler-direct unit tests covering threshold paths, suppression reasons, F-2 defensive widening (`SkillSensitivityRefused`), and per-member overrides (lower-threshold + impossibly-high disable per `REFERENCE_EXAMPLES.md §2 line 666`).
  - `tests/unit/test_pipeline_runner.py` — 3 new tests covering `parties_conn_factory` default-`None` backward-compat + threading + same-object-per-dispatch semantics.
  - `tests/integration/test_pipeline_10b_ii_alpha_integration.py` — 3 round-trip tests against the live runner with a seeded parties DB and monkeypatched skill runner.
  - **Total new tests: 26** (4 classify_commitment_candidate + 4 extract_commitment_fields + 1 commitment_extraction pack-load + 11 commitment_extraction unit + 3 runner unit + 3 integration). Suite tally on the `tests/` testpath: 447 → 464 passed, 2 skipped (the +9 pack-internal tests live under `packs/` and run via explicit path per the per-commit verification commands).
  - **F-2 carry-forward CLOSED**: both new skill packs declare `sensitivity_required: normal` and `context_scopes_required: []`; pipeline's `except` list nonetheless catches `SkillSensitivityRefused` and `SkillScopeInsufficient` defense-in-depth so that future skill-spec changes don't silently widen the failure surface.
  - `[§7.3]` (no projection direct writes): pipeline emits only via `ctx.event_log.append`; pipeline→projection canary in `verify_invariants.sh` clean.
  - `[§7.4]` / `[§8]` / `[D6]`: zero new SDK imports; the two skill calls go through `ctx.run_skill_fn` per `[ADR-0002]`.
  - `[§7.7]`: skill failure on either call does NOT raise — emits `commitment.suppressed` with `reason="skill_failure_defensive_default"`. Bus checkpoint advances normally.
  - `[D7]`: new event type `commitment.suppressed` registers at v1.
  - `[§12.4]`: per-member-override config uses placeholder member ids (`member_id_example_*`); verify script clean.
  - `[§15]`/`[D15]`: parties-DB path resolved through `InstanceConfig.projection_db_path("parties")` in the integration test harness; no hardcoded literal in handler or runner.
  - Causation-id wiring: every emit uses `causation_id=ctx.triggering_event_id` per the 10a echo_emitter canary contract.
  - `verify_invariants.sh` exit 0.
  - **Pre-existing ruff baseline (NOT introduced by this prompt)**: `poetry run ruff check .` reports 2 F401 errors in `docs/reference/plaid/python-sdk-plaid_api.py` (pre-existing on main since PR #17). `poetry run ruff check adminme/ packs/ tests/` is clean — all code shipped by this prompt is ruff-clean.
  - UT-12 CLOSED by this merge per `docs/02-split-memo-10b-ii.md` §"Self-check" — option (c)+(a) shipped: split is option (c), parties-DB seam wired through `PipelineContext` is option (a).
- **Carry-forward for prompt 10b-ii-β** (`thank_you_detection` + `extract_thank_you_fields`):
  - Parties-DB seam already in `PipelineContext` from this prompt. `thank_you_detection` constructs its handler the same way: `with ctx.parties_conn_factory() as conn:` for sender resolution.
  - The defensive-default-on-skill-failure pattern (suppress, do not raise) is now established for two-skill-chain pipelines; thank_you_detection's pipeline reuses the F-2-widened `except` list literally.
  - The per-member-overrides config shape is settled (`min_confidence` / `review_threshold` / `dedupe_window_hours` / `per_member_overrides` / `skip_party_tags`). thank_you_detection reuses the same skeleton.
  - `commitment.proposed` with `kind="other"` is the default thank-you path. If `BUILD.md §1150` implies thank_you should be its own kind in `CommitmentProposedV1.kind`'s Literal, that's a Literal-extension migration (forward-only per `[D7]`); flag as 10b-ii-β's open question — do NOT silently extend the enum.
  - 09b's `classify_thank_you_candidate@1.3.0` is already on main. 10b-ii-β only ships `extract_thank_you_fields` (smaller because the binary classify already happened upstream).
  - Sender-unresolvable + factory-missing currently emit `commitment.suppressed` with `reason="skill_failure_defensive_default"`. If 10b-ii-β finds this overloads the reason enum, the future migration path is to extend `CommitmentSuppressedV1.reason`'s Literal forward (per `[D7]`).
- **Carry-forward for prompt 10c** (proactive pipelines):
  - Per-member-overrides config shape shipped here is reusable for proactive pipelines that have member-specific cadence (e.g., `morning_digest` per-member quiet hours).
  - The parties-DB seam is now generally available to any pipeline that needs read access to the parties projection; pass `parties_conn_factory=...` at runner construction.
- **Carry-forward for prompt 14b** (inbox surface):
  - `commitment.suppressed` events with `reason="below_confidence_threshold"` and confidence between, e.g., 0.40 and 0.55 may surface in the inbox as "near-miss" entries for principal calibration. Decide at 14b refactor.
  - The `dedupe_hit` value of `commitment.suppressed.reason` is registered but currently unused; the projection-side dedupe prompt will start emitting it.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - Bootstrap §7 wires `PipelineRunner` lifecycle. The new `parties_conn_factory` is constructed by bootstrap from `instance_config.projection_db_path("parties")` + the encryption key (derived from secrets) and passed at runner construction.
  - The pipeline's `config.example.yaml` is copied to `<instance_dir>/packs/pipelines/commitment_extraction/config.yaml` on first run; bootstrap §3 collects per-member overrides during the wizard.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - Confirm `commitment_extraction` correctly resolves a real sender against the live parties projection and proposes a commitment from a seeded test message.
---

## Sidecar PRs (out-of-band, no four-commit discipline)

Sidecar PRs are single-purpose fixes against already-merged code, run
outside the build-prompt sequence. They have no carry-forwards and do
not appear in the `## Build prompts` ledger above. This section is a
chronological trace; the per-PR detail lives in the PR description on
GitHub. Tracked here so future Partner sessions reading the build_log
see the full PR landscape.

This section also captures **infrastructure PRs that are not sidecars in the PM-15 sense** — sequence updates, split-memo prep PRs, and similar single-purpose planning-artifact PRs. Per PM-22, these have no four-commit discipline, no BUILD_LOG entry by design, and are tagged here explicitly to keep the categorization clean.

### sidecar-raw-data-is-manual-derived (PR #35, merged 2026-04-26)
- **Surfaced by**: 07.5 audit finding C-1 (`docs/checkpoints/07.5-projection-consistency.md`).
- **Diff scope**: 2 files. `adminme/projections/xlsx_workbooks/sheets/raw_data.py` (1-line set-literal expansion + 4-line docstring comment); `tests/unit/test_xlsx_finance_workbook.py` (3 imports + 1 sync test).
- **Why**: Raw Data builder's `ALWAYS_DERIVED` was missing `is_manual` while the bidirectional descriptor's `always_derived` included it. Cosmetic protection drift, not a correctness bug — `is_manual` is set by the projection from event type and has no live workbook-side edit path, so the missing cell-level lock had nothing to gate. But the two declarations are meant to be equivalent in each direction (builder owns sheet-side cell protection; descriptor owns reverse-diff drop behavior); re-drift in either direction would silently change round-trip semantics.
- **Guard**: `test_raw_data_always_derived_matches_descriptor` asserts `ALWAYS_DERIVED == set(descriptor_for(FINANCE_WORKBOOK_NAME, "Raw Data").always_derived)`. Both-direction drift fails CI on next push.
- **Verification (per Claude Code transcript)**: ruff clean, mypy clean on `raw_data.py`, full unit suite 412 passed / 1 skipped (was 411/1; +1 from the new sync test), `scripts/verify_invariants.sh` exits 0 (no `ALLOWED_EMITS` change).
- **Closes**: 07.5 finding C-1. UT-1 was already CLOSED 2026-04-25 when the audit landed; this PR is the formal closure of the audit's queued sidecar.
- **Outcome**: MERGED.

### sequence-update-10b-split (PR #37, merged 2026-04-26) — INFRASTRUCTURE, NOT SIDECAR per PM-22
- **Category**: Sequence update / split-memo prep PR. No code touched. No BUILD_LOG entry by design — sequence updates are planning-artifact PRs, not build prompts. Recorded here so future Partner sessions see the full PR landscape.
- **Surfaced by**: Tier C split memo at `docs/01-split-memo-10b.md` (Partner, 2026-04-26) — the on-disk record of the split decision per the pre-split disposition for 10b in `D-prompt-tier-and-pattern-index.md`.
- **Branch**: `claude/sequence-update-10b-split-OFrFL` (harness-assigned; the requested `sequence-update-10b-split` was overridden, accepted without fight per preamble discipline).
- **Diff scope**: 2 files in a single commit (`5afa6b2`).
  - `prompts/PROMPT_SEQUENCE.md`: replaced the single 10b row in the sequence table with two rows (10b-i for `identity_resolution + noise_filtering`, 10b-ii for `commitment_extraction + thank_you_detection`); updated the dependency-graph ASCII to read `10a → 10b-i → 10b-ii → 10c → 10d`; updated the hard-sequential-dependency line to note "10b-ii consumes identity_resolution output" + "downstream prompts that depended on 10b now depend on 10b-ii." Row formatting matches existing table style (column widths, dashes, code-fences).
  - `prompts/10b-reactive-pipelines.md`: deleted (the unrefactored 26-line v1 draft is superseded by the upcoming 10b-i and 10b-ii sub-prompts).
- **Verification**: post-PR check returned status `pending` (no CI configured on this branch), zero reviews, zero comments. Stopped per the "Post-PR: one check, then stop" rule.
- **Closes**: Pre-condition for the 10b-i refactor session. The `D-prompt-tier-and-pattern-index.md` 10b row needs a separate update from "Pre-split candidate — Tier C memo first" to "Was split on arrival" with 10b-i and 10b-ii listed beneath; that file lives in Partner setup (NOT in repo per the split memo §10 and PM-22), so James updates it out of band.
- **Outcome**: MERGED.
