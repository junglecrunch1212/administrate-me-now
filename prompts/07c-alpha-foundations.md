# Prompt 07c-α: xlsx round-trip foundations (schema + sidecar + diff core)

**Phase:** BUILD.md PHASE 2. First half of the round-trip 07b opened.
**Depends on:** 01–07b merged. Slim universal preamble + `scripts/verify_invariants.sh` on main with `ALLOWED_EMITS='xlsx\.regenerated'`.
**Stop condition:** Two new system events registered at v1; three dead stubs (PM-10) deleted; sidecar JSON I/O module shipped with tests; forward daemon (07b's `XlsxWorkbooksProjection`) extended to write per-sheet sidecar inside its lock; descriptor registry + pure-functional diff core at `adminme/daemons/xlsx_sync/`. `bash scripts/verify_invariants.sh` clean. Reverse daemon itself ships in 07c-β; no daemon code in this prompt.

This is part 1 of a two-prompt split. 07c-β consumes what this prompt lands and ships the reverse daemon + integration round-trip. The split exists because the full reverse daemon plus its test pyramid does not fit one Claude Code session window — see partner_handoff PM-2 / PM-15.

---

## Read first

The slim universal preamble (`prompts/PROMPT_SEQUENCE.md`) governs cross-cutting discipline. Do not re-derive it.

**Spec — read in order:**

- `ADMINISTRATEME_BUILD.md` lines 993–1080 — §3.11 reverse-projection algorithm. Lines 1009 (read sidecar), 1015 (write sidecar), 1054 (rebuild deletes both files plus sidecar) are the load-bearing bits for this prompt's sidecar pathway.
- `ADMINISTRATEME_DIAGRAMS.md` §6 — xlsx round-trip diagram (two daemons, shared lock, sidecar state).
- `docs/SYSTEM_INVARIANTS.md` §2 (projections derived; the reverse daemon is NOT a projection — it lives in 07c-β at `adminme/daemons/xlsx_sync/`), §10 (xlsx bidirectional + lock contention), §13 item 5.
- `docs/DECISIONS.md` D5, D7.

**Codebase context — load only what you reference:**

- `adminme/projections/xlsx_workbooks/__init__.py`, `builders.py`, `lockfile.py`, `query_context.py` — the forward daemon you extend in Commit 3.
- `adminme/projections/xlsx_workbooks/sheets/{tasks,commitments,recurrences,raw_data}.py` — `HEADERS` and `DERIVED_COLUMNS` are the source of truth for descriptors. Read once each; do not re-summarize them in the descriptor module beyond field/event mappings.
- `adminme/projections/xlsx_workbooks/sheets/{people,accounts,metadata_ops,metadata_finance}.py` — read-only sheets; for descriptor module's awareness of sheet inventory only.
- `adminme/projections/xlsx_workbooks/{forward,reverse,schemas}.py` — confirm these are the 22-line stubs from prompt 02. Delete all three in Commit 1 (PM-10).
- `adminme/events/schemas/system.py` — pattern for the two new system events.
- `adminme/lib/instance_config.py` — confirm `xlsx_workbooks_dir`. **InstanceConfig has no `principal_member_id` field**; do not add one — UT-7 (actor attribution for reverse emits) resolves in prompt 08.
- `scripts/verify_invariants.sh` — `ALLOWED_EMITS` regex.

**Do NOT load** during this session: BUILD.md outside §3.11, L4/pipelines/console specs.

---

## Architectural placement (binding)

This prompt does not ship the reverse daemon — only its supporting infrastructure. But the placement is set now so 07c-β has nothing to debate:

The reverse daemon at `adminme/daemons/xlsx_sync/reverse.py` (07c-β) is an **L1-adjacent adapter, not a projection**. It does NOT register with `ProjectionRunner`; it emits domain events on principal authority. PM-14 is now HARD: daemons live in `adminme/daemons/`, projections in `adminme/projections/`. The forward xlsx daemon is the documented exception (it IS a projection per [§2.2]).

`scripts/verify_invariants.sh`'s `ALLOWED_EMIT_FILES` audits `adminme/projections/` only, so it does NOT extend in this prompt. `ALLOWED_EMITS` regex DOES extend — hygiene says the allowlist names every system type a projection MAY emit.

---

## Sidecar JSON pathway (resolves UT-6)

BUILD.md §3.11 line 1009 + 1015 + 1054 imply both daemons touch a sidecar:

- **Forward writes the sidecar after each regeneration, inside the same lock as the xlsx write.** Gives reverse a stable read baseline; resolves a forward-vs-human race to "forward wins, reverse comes back to find sidecar matches workbook, no diff."
- **Reverse rewrites the sidecar at the end of each cycle.** Captures post-emit state so two principal saves in quick succession diff against each other.

**Path:** `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json`. Resolve via `InstanceConfig.xlsx_workbooks_dir.parent / ".xlsx-state"` — sibling to the workbooks dir, not inside it. Critical: a future watchdog over the workbooks dir (07c-β) cannot self-trigger on sidecar writes. **InstanceConfig.xlsx_workbooks_dir must point to `projections/xlsx_workbooks/`** — if the existing value is something else (e.g. `.xlsx-state/`), update it in Commit 1 with a one-line comment citing the sibling pathway.

**Per-sheet contents:**
- Bidirectional sheets store `{"rows": [{...row dict keyed by header...}, ...]}`.
- Read-only sheets store `{"content_hash": "<sha256-hex>"}` so principal edits to read-only sheets are detectable for WARN logging without persisting full row data.

**Forward integration:** the sidecar writer reads the just-written xlsx file (not re-queries projections). Guarantees byte-identical content between sidecar and what reverse will load. Eliminates "wrote rows from current projection state but sidecar derived from a slightly different snapshot" failure mode.

---

## Sheet descriptors (07c-β consumes)

Build a registry at `adminme/daemons/xlsx_sync/sheet_schemas.py` describing per-sheet diff behavior. Each `SheetDescriptor` declares: `workbook`, `sheet`, `id_column`, `editable_columns` (frozenset OR callable for per-row determination), `always_derived` (frozenset), `adds_emit_event`, `updates_emit_event`, `deletes_emit_event` (str | None — None means "drop"), `deletes_use_undo_window` (bool), `new_id_prefix` (str | None for backend-minted ids), `add_drop_log_level` / `update_drop_log_level` / `delete_drop_log_level` (str — for non-emitting cases).

**Source of truth:** the sheet builder in `adminme/projections/xlsx_workbooks/sheets/<sheet>.py`. Its `HEADERS` enumerates columns; its `DERIVED_COLUMNS` is your `always_derived` set. Match exactly — drift breaks round-trip silently. (07.5 will audit this equivalence.)

**Per-sheet event mapping:**

| Sheet | ADD | UPDATE | DELETE |
|---|---|---|---|
| Tasks | `task.created` | `task.updated` | `task.deleted` (5s undo, `tsk_<8hex>` prefix) |
| Commitments | drop INFO ("commitments are pipeline-proposed only per [§4.2]") | `commitment.edited` | drop INFO ("commitments cancel via API") |
| Recurrences | `recurrence.added` (`rec_<8hex>` prefix) | `recurrence.updated` | drop INFO ("recurrences not deletable in v1") |
| Raw Data — manual row (`is_manual==TRUE`) | `money_flow.manually_added` (`flow_<8hex>` prefix) | drop INFO (`money_flow.recategorized` not registered yet) | `money_flow.manually_deleted` (5s undo) |
| Raw Data — Plaid row (`is_manual==FALSE`) | drop WARN ("non-manual row added via xlsx; rejecting per [§13.4]") | drop INFO ("Plaid-authoritative; assigned_category/notes/memo edits deferred") | drop WARN ("Plaid row deletion via xlsx ignored") |

People / Accounts / Metadata are read-only — handled in 07c-β by the daemon's read-only-sheet code path, not in `BIDIRECTIONAL_DESCRIPTORS`.

For Raw Data, `editable_columns` is a callable returning `{"date", "account_last4", "merchant_name", "merchant_category", "amount", "memo", "assigned_category", "notes"}` for manual rows and `{"assigned_category", "notes", "memo"}` for Plaid rows. `is_manual` itself is in `always_derived`.

---

## Diff core (07c-β consumes)

Pure-functional, sync, no I/O, no openpyxl imports. Lives at `adminme/daemons/xlsx_sync/diff.py`.

**Signature:** `diff_sheet(current_rows, sidecar_rows, descriptor) -> DiffResult` where `DiffResult` has `added`, `updated` (list of `(row_dict, changed_fields_dict)` tuples), `deleted`, and `dropped_edits` (rows where only non-editable columns changed; for INFO log).

**Type normalization:**
- floats compared at abs-tolerance 1e-9
- `datetime`/`date` → `.isoformat()` for comparison
- `None` and `""` treated as equal
- int↔float compared as floats with the tolerance

**ID column edits** (principal blanks then re-types `task_id`): diff sees the original id missing in current and a new id present → reports as delete-plus-add. The daemon (in 07c-β) emits the delete with undo window AND the add immediately.

**Blank id on a new row:** diff sees an added row with no id. The daemon (in 07c-β) generates the new id; the diff core does NOT.

---

## Out of scope

- The reverse daemon class itself, watchdog→asyncio bridge, lock acquisition, per-cycle algorithm, undo-window machinery, sensitivity preservation on emit, cold-start handling — **prompt 07c-β**.
- Integration round-trip test, smoke script — **prompt 07c-β**.
- `adminme projection rebuild xlsx_workbooks` CLI — **prompt 17**.
- Adding `observation_mode_active` to forward emit payload per D5 — **prompt 16**.
- Daemon lifecycle (start-on-boot, stop-on-shutdown) — **prompt 16**.
- Routing reverse-emitted events through Session/guardedWrite — **prompt 08** (UT-7 opens here, resolves there).
- Touching read-only sheet builders.

---

## Commit discipline (four commits per slim preamble)

### Commit 1 — Schemas + verify_invariants + stub deletion + InstanceConfig fix

- Append two system event classes to `adminme/events/schemas/system.py`:
  - `XlsxReverseProjectedV1` — fields: `workbook_name: Literal["adminme-ops.xlsx", "adminme-finance.xlsx"]`, `detected_at: str` (min_length=1), `sheets_affected: list[str]`, `events_emitted: list[str]`, `duration_ms: int` (ge=0). `model_config = {"extra": "forbid"}`.
  - `XlsxReverseSkippedDuringForwardV1` — fields: `workbook_name`, `detected_at`, `skip_reason: Literal["forward_lock_held"]`. `model_config = {"extra": "forbid"}`.
  - Both register at v1.
- Extend `scripts/verify_invariants.sh` `ALLOWED_EMITS` regex to `'xlsx\.regenerated|xlsx\.reverse_projected|xlsx\.reverse_skipped_during_forward'`. Add a maintenance comment explaining `ALLOWED_EMIT_FILES` does NOT extend (daemon is outside `adminme/projections/`).
- `git rm` the three dead stubs (`forward.py`, `reverse.py`, `schemas.py` in `adminme/projections/xlsx_workbooks/`) — PM-10 disposition. Grep for any imports first; if hits, those imports were also dead — prune.
- If `InstanceConfig.xlsx_workbooks_dir` does not currently resolve to `<instance_dir>/projections/xlsx_workbooks/`, fix it. Add a brief docstring/comment that the sibling `.xlsx-state/` tree resolves via `xlsx_workbooks_dir.parent / ".xlsx-state"`. **Run the existing xlsx test suite after this change** — any test that expected the old path fails fast and gets fixed in this commit.

**Tests (Commit 1):**

- Extend `tests/unit/test_schema_registry.py` with one test asserting both new event types are in `registry.known_types()` at v1.
- (No new test file in this commit; the schema additions are smoke-tested by registry; the InstanceConfig change is smoke-tested by the existing xlsx tests.)

### Commit 2 — Sidecar I/O module + tests

- New module `adminme/projections/xlsx_workbooks/sidecar.py`: pure-functional sidecar I/O.
  - `sidecar_dir(xlsx_workbooks_dir: Path) -> Path` — returns `xlsx_workbooks_dir.parent / ".xlsx-state"`.
  - `sidecar_path(xlsx_workbooks_dir, workbook_name, sheet_name) -> Path`.
  - `write_sheet_state(xlsx_workbooks_dir, workbook_name, sheet_name, rows: list[dict]) -> Path` — atomic via `.tmp.<pid>` + `os.replace`. JSON with `sort_keys=True, separators=(",", ":")`.
  - `write_readonly_state(xlsx_workbooks_dir, workbook_name, sheet_name, rows: list[list]) -> Path` — stores `{"content_hash": <sha256-hex>}`.
  - `read_sheet_state(...) -> list[dict] | None` — returns rows from `data["rows"]`, None if file missing or shape unexpected.
  - `read_readonly_state(...) -> str | None` — returns the hash, None if missing.
  - `hash_readonly_sheet(rows: list[list]) -> str` — `json.dumps(rows, default=str, sort_keys=True, separators=...)` then sha256 hex.

**Tests (Commit 2) — `tests/unit/test_xlsx_sidecar.py` (≥6 tests):**

- `sidecar_dir` is sibling-of-workbooks-dir (not inside).
- `sidecar_path` includes workbook name and sheet name in the right shape.
- write-then-read sheet state roundtrip.
- read-missing returns None for both shapes.
- readonly state stores `content_hash` only (no `rows` key); hash is 64-char sha256 hex.
- readonly hash determinism — equal inputs → equal hashes; different inputs → different hashes.
- atomic write — no `.tmp.*` leftovers after success.
- write-overwrites — second write replaces first cleanly.

### Commit 3 — Forward daemon writes sidecar + tests

- Extend `XlsxWorkbooksProjection._regenerate()` in `adminme/projections/xlsx_workbooks/__init__.py` to call a new `_write_sidecar_for(workbook, xlsx_path)` helper as the LAST step **inside the lock**, after the xlsx write and before lock release. The helper opens the just-written xlsx with `openpyxl.load_workbook(..., data_only=True)` and writes per-sheet sidecar JSON (bidirectional sheets via `write_sheet_state`, read-only via `write_readonly_state`).
- Add two module-level constants: `_BIDIRECTIONAL_SHEETS: dict[str, list[str]]` (workbook → list of bidirectional sheet names) and `_READONLY_SHEETS: dict[str, list[str]]`. Use them to drive both the existing `sheets_regenerated` payload list AND the new sidecar writer (single source of truth).
- The `xlsx.regenerated` payload's `sheets_regenerated` list now derives from `_BIDIRECTIONAL_SHEETS[workbook] + _READONLY_SHEETS[workbook]` rather than hard-coded literal lists.

**Tests (Commit 3) — `tests/unit/test_xlsx_forward_writes_sidecar.py` (≥4 tests):**

- Bidirectional sheets get JSON sidecar after `regenerate_now` — for each of Tasks/Recurrences/Commitments, file exists, has `"rows"` key, value is a list.
- Read-only sheets get hash sidecar — for each of People/Metadata, file exists, has `"content_hash"` key, no `"rows"` key, hash is 64 hex.
- Finance Raw Data sidecar matches workbook rows — open the just-written workbook with openpyxl, extract rows-as-dicts, compare equal to `sidecar["rows"]`. This is the round-trip canary.
- Sidecar exists when `regenerate_now` returns — by the time `await regenerate_now(...)` completes, the sidecar tree is on disk (not pending).

### Commit 4 — Descriptors + diff core + tests + BUILD_LOG + push

- New empty packages: `adminme/daemons/__init__.py`, `adminme/daemons/xlsx_sync/__init__.py`. Top-of-package docstring on `xlsx_sync/__init__.py` notes "this daemon is L1-adjacent, NOT a projection per [§2.2] — it emits events" so future readers understand the placement.
- `adminme/daemons/xlsx_sync/sheet_schemas.py` — `SheetDescriptor` dataclass + four bidirectional descriptors per the per-sheet event-mapping table. Helper `descriptor_for(workbook, sheet) -> SheetDescriptor | None`. Helper `editable_columns_for(descriptor, row) -> frozenset[str]` resolving the static-or-callable shape.
- `adminme/daemons/xlsx_sync/diff.py` — `DiffResult` dataclass + `diff_sheet()` per spec. Pure sync; no openpyxl/watchdog imports.
- Append BUILD_LOG entry for 07c-α per slim preamble template. Note explicitly that this is part 1 of a two-prompt split. Carry-forward to 07c-β: descriptors at `adminme/daemons/xlsx_sync/sheet_schemas.py`, diff core at `adminme/daemons/xlsx_sync/diff.py`, sidecar I/O at `adminme/projections/xlsx_workbooks/sidecar.py`, forward already writes sidecar.

**Tests (Commit 4) — `tests/unit/test_xlsx_diff.py` (≥10 tests):**

no-changes-empty; added-only; deleted-only; editable-change-as-update; derived-only-change-as-dropped-edit; id-column-change-as-delete-plus-add; blank-id-on-new-row-stays-in-added (daemon mints id later); float tolerance equivalence; None ≡ ""; datetime ≡ ISO string; manual-row full editable set yields update on amount; Plaid-row amount edit lands in dropped_edits; callable editable evaluated per-row (one manual + one Plaid in same diff: manual yields update, Plaid yields drop).

**Verification block** (one block, end of Commit 4):

```
poetry run pytest -q
poetry run ruff check adminme/ tests/
poetry run mypy adminme/
bash scripts/verify_invariants.sh
```

Full suite expected: prior baseline + ≥21 new tests (1 schema + ≥6 sidecar + ≥4 forward-writes-sidecar + ≥10 diff). All must pass.

**Push + open PR** per slim preamble (`gh pr create` first; MCP fallback). PR title: `Phase 07c-α: xlsx round-trip foundations (schema + sidecar + diff core)`. Body notes this is part 1 of 2 — 07c-β follows after merge with the daemon and integration round-trip. Single-purpose; no sidecar fixes.

---

## Stop

Per slim preamble's stop discipline: post-PR, ONE round of `mcp__github__pull_request_read` (`get_status`, `get_reviews`, `get_comments`); report; STOP. Do not poll. Do not merge.

Stop report names: branch, PR URL, four commit SHAs, full-suite count, ≥21 new unit tests confirmed across the four new test sites (sidecar / forward-writes-sidecar / diff / schema registry extension), `bash scripts/verify_invariants.sh` clean. Note PM-10 disposition (3 stubs deleted) and UT-6 resolution (sidecar pathway implemented forward-side; reverse-side lands in 07c-β). Confirm `adminme/daemons/xlsx_sync/` packages created and ready for 07c-β to drop the daemon module in.
