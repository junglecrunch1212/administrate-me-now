# Prompt 11-γ: Adapter framework — CLI subcommand group + authoring guide

**Phase:** Phase A — adapter framework expansion (third and final sub-prompt of the prompt-11 split per `docs/09-split-memo-11-framework.md`).

**Branch:** `claude/11-gamma-<auto>` (harness-assigned). PR base = `main` for all four c-bands per PR #106 sidecar. STACKED across c1→c4 per §8.5.

**Depends on:** Prompts 11-α and 11-β merged to main (HEAD `7b33ca46` as of authorship). The 5 ABCs at `adminme/lib/adapters/`, the `AdapterManifest` schema at `adminme/lib/pack_registry/`, and the 6-check validator at `adminme/lib/pack_registry/validator/` are the surfaces this prompt's CLI wraps + the authoring guide documents.

**Quality bar:** introduction-tier prompt per `D-prompt-tier-and-pattern-index.md`. The CLI is the **operator-facing seam** for the framework — `adminme adapters list` makes installed packs visible, `adminme adapters validate <pack>` runs the 6 checks before install, `adminme adapters scaffold <kind> <runtime>` generates a validator-clean skeleton for an authoring developer. The authoring guide is the **developer-facing seam** documenting the manifest extensions + the 6 validator rules + the [D20] three-layer developer-mode gate. Quality bar correspondingly high — UX coherent, gate ironclad, guide concrete with worked examples.

**Stop condition:** `adminme/cli/commands/adapters.py` exporting a Typer subcommand group with three subcommands (`list`, `validate`, `scaffold`) wired into `adminme/cli/main.py:app`. A scaffold-template tree at `adminme/cli/commands/adapters_scaffold/templates/` with five entries (one per epistemic-role category at its canonical runtime). `docs/adapter-authoring-guide.md`. ≥20 new tests across the four commits. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~60-80 minutes wall clock, 4 commits. ~$40-80 API spend per split memo §2.

---

## §1 Behavioral grounding

You are building the **operator + developer surface** for the adapter framework. Per memo 05 §3 + [D20] (three-layer developer mode: bundled / verified third-party / user-authored) + [PM-34], the framework's job is not done until authors can scaffold a clean pack, validate it without leaving their terminal, and read a single guide that documents every rule the validator enforces. 11-α defined the schema; 11-β enforced it; this prompt makes both accessible.

**Three subcommands, one Typer group** [memo 05 §3, [D20]]:

- **`adminme adapters list`** — discovery. Walks `packs/adapters/<family>/<name>/` looking for `pack.yaml` files, prints a table (rich.table) of each pack's name, family, version, declared capabilities (Cat-A through Cat-E), and runtime placement. Output goes to stdout; non-zero exit only on filesystem errors (a malformed pack.yaml that doesn't parse surfaces in the table as `<unparseable>` with a `<!>` marker rather than aborting the whole listing).

- **`adminme adapters validate <pack-dir>`** — wraps `adminme.lib.pack_registry.validator.validate_adapter_pack`. Prints each finding with severity color (red for error, yellow for warning, blue for advisory) via rich. Exit codes: `0` on no errors (warnings + advisories don't fail), `1` on any error finding, `2` on `ManifestParseError` (the manifest itself can't be parsed — a different failure mode from "manifest parses but fails validation"). The `validate` subcommand does NOT install the pack; install is a separate flow that runs validate first.

- **`adminme adapters scaffold <kind> <runtime>`** — generates a validator-clean pack skeleton from a template. `<kind>` is one of `Cat-A` through `Cat-E`; `<runtime>` is one of `central` / `bridge` / `dual` / `plugin`. The subcommand writes a new directory at `--output` (default `./adapter-skeleton-<kind>-<runtime>`) containing `pack.yaml`, `adapter.py` (subclass of the appropriate ABC), `config.schema.json`, and `tests/test_<name>_adapter_pack_internal.py` (F-3 unique-basename rule per 10c-iii precedent).

**Three-layer developer-mode gate on scaffold** [D20]. The scaffold subcommand is the only one of the three that creates files; per [D20]'s "bundled / verified third-party / user-authored" taxonomy, only user-authored mode can scaffold new packs. The gate has three independent layers, ALL of which must pass before scaffold proceeds:

1. **Environment variable**: `ADMINME_DEVELOPER_MODE=1` must be set in the process environment. Missing or any other value ⇒ refuse with "developer mode not enabled".
2. **Explicit flag**: `--allow-scaffold` must be passed on the command line. The flag is intentionally awkward to type so it doesn't become muscle memory; the subcommand refuses without it even when `ADMINME_DEVELOPER_MODE=1` is set.
3. **Interactive confirmation**: `typer.confirm("Scaffold a new adapter pack at <output>?")` returns False ⇒ abort. Default is False (Enter without input is refuse). A `--yes` flag skips the prompt but only after layers 1+2 pass; this is for automation, not convenience.

Refuse on ANY layer's miss. Each refusal exits with a distinct exit code (3 for env-missing, 4 for flag-missing, 5 for confirm-declined) and a one-line stderr message naming the missing layer.

**Authoring guide** [memo 05 §3]. `docs/adapter-authoring-guide.md` documents the full pack-author workflow: choose a category, choose a runtime, scaffold the skeleton, fill in the per-method bodies, validate, install. The guide is concrete — every rule the 6-check validator enforces gets a one-paragraph section + one worked example showing both the violation and the fix. The Cat-D + Cat-B multi-capability shape from the 11-α / 11-β integration tests (Apple-Notes-checklists per memo 05 §2.2) is the canonical worked example for the manifest extensions; the developer-mode gate gets its own §"Scaffolding new packs (developer mode)" section.

**Why `adminme/cli/commands/adapters.py` and not somewhere else** [Gate 2 verification]. The on-main CLI lives at `adminme/cli/main.py` exposing `app = typer.Typer(...)` (per prompt 02 scaffolding, console_script `adminme = "adminme.cli.main:app"` in `pyproject.toml`). The `adminme/cli/commands/` subdirectory does NOT exist on main — c1 creates it as a new sibling to `main.py`. The new module follows Typer's standard subcommand-group pattern: `adapters_app = typer.Typer()` defined in `adapters.py`, then `app.add_typer(adapters_app, name="adapters")` wired into `main.py`.

**Anti-vision halt** [VISION §"Small enough to inspect", DRIFT-CONTROL Job 4]. Each subcommand body MUST be ≤120 lines (including docstring + decorators). The scaffold templates themselves are not Python modules in the inspectability sense — they are text content — but the template loader function MUST be ≤80 lines and the template files themselves ≤60 lines each. The authoring guide is prose-heavy and not subject to line caps, but MUST be one inspectable scroll — target ~400 lines.

**Mode:** Phase A. No live pack instantiation; the CLI commands run against on-disk pack directories. The `list` subcommand walks `packs/adapters/`; the `validate` subcommand wraps the existing aggregator (no new validation logic); the `scaffold` subcommand writes files but does NOT call the validator on the generated pack (the operator runs `adminme adapters validate <new-pack-dir>` manually as the workflow's natural next step).

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md` / `memo 05` ranges. Memo 05 is 122 KB; targeted reads only.

1. **`docs/09-split-memo-11-framework.md`** — full read (~12 KB). §2 (11-γ row + budget), §3 (depends on 11-α + 11-β), §4 (deliverables breakdown for 11-γ; the split memo's path `adminme/cli/commands/adapters.py` is correct + matches on-main CLI layout).
2. **`prompts/_active/11-alpha-adapter-framework-abcs-and-manifest.md`** — full read (~36 KB). §7 (the 5 ABCs the `scaffold` templates inherit from; the manifest schema the `validate` subcommand consumes via the validator).
3. **`prompts/_active/11-beta-adapter-framework-validator.md`** — full read (~36 KB). §7 (the validator surface the `validate` subcommand wraps; the 6 checks' diagnostic shapes that the authoring guide documents). §9 (the cross-cutting [§N]/[DN] checks that the guide cites for each rule).
4. **`prompts/11-adapter-framework.md`** — full read (15 KB). §"Authoring guide" outline. The authoring guide written in c4 follows this outline; expand it with the concrete worked examples per each of the 6 validator rules.
5. **`docs/05-architecture-amendment-personal-data-layer.md`** — targeted reads:
   - §2.1 (five-category taxonomy) — lines ~12-200
   - §2.2 (multi-capability shape — Cat-D + Cat-B Apple-Notes-checklists; the canonical worked example for the guide)
   - §2.6 (owner_scope_overridability_at_install — for the Cat-D scaffold template)
   - §3 (the framework's manifest schema + validator design; everything in this section informs the guide)
6. **`docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`** — §2.2 (Tailscale-binds-owner-scope — for the Cat-D scaffold template + guide section).
7. **`adminme/cli/main.py`** — full read. Confirm the `app = typer.Typer(...)` shape; the `commands/adapters.py` module wires into this app via `app.add_typer(adapters_app, name="adapters")` from c1.
8. **`pyproject.toml`** — confirm `typer >= 0.12` + `rich` are already dependencies. No new dependency adds. The `[tool.poetry.scripts]` entry `adminme = "adminme.cli.main:app"` is the console-script entry point; no change.
9. **`adminme/lib/adapters/__init__.py`** + each ABC file — full read. The 5 ABCs the scaffold templates' `adapter.py` skeletons inherit from. Each scaffold template's `adapter.py` MUST inherit from the correct ABC for its category so the generated pack passes the 11-β `base_class_conformance` check on first run.
10. **`adminme/lib/pack_registry/models.py`** — full read. `Capability` + `AdapterManifest` shapes; the scaffold templates' `pack.yaml` MUST emit fields that round-trip through `parse_adapter_manifest` cleanly so the generated pack passes parser validation.
11. **`adminme/lib/pack_registry/validator/__init__.py`** — full read. The `validate_adapter_pack(pack_dir, *, registry=None, projections_dir=None) -> ValidationResult` signature the `validate` subcommand wraps. Also read `result.py` for the `ValidationResult` accessors (`has_errors()` / `errors()` / `warnings()` / `advisories()`) the `validate` subcommand renders.
12. **`adminme/lib/observation.py`** — full read. Confirm the exact symbol name (`outbound` per 11-β c3's AST-walk pin) so the scaffold templates' Cat-A `send` and Cat-E `execute_action` bodies use the canonical symbol; otherwise `validate` on a freshly-scaffolded pack will fail observation-mode-wrapping.
13. **`packs/adapters/messaging/gmail/`**, **`packs/adapters/financial/plaid/`**, **`packs/adapters/reminders/apple-eventkit/`** — read each pack's `pack.yaml` + `adapter.py`. The scaffold templates mirror the on-disk layout of these existing packs (file presence + naming conventions) so a developer who has read one of the existing packs recognizes the scaffold output. Cohort C will retrofit these existing packs onto the new ABCs; the scaffold templates are written as if the retrofit has already happened (target shape, not current shape).
14. **`tests/unit/lib/pack_registry/validator/test_validate_adapter_pack.py`** — full read. **Precedent for the integration-style test pattern** the c4 integration test for the CLI uses (writes pack.yaml + adapter.py on tmp_path, runs the CLI, asserts findings shape).
15. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads: §1 (event log append-only — N/A for CLI, asserted no-write), §6.20 (observation mode wraps outbound — the guide's §"Observation mode" section documents this), §7.3 (no projection direct writes from adapters — guide's §"No projection writes from adapters" section), §8 (no LLM SDKs from adapter packs — guide's §"No LLM SDK imports" section).
16. **`docs/DECISIONS.md`** — read [D6], [D7], [D8], [D17], [D18], [D19], [D20], [D24]. Particularly [D20] for the three-layer developer-mode gate — the gate's exact rule set is documented in the guide's §"Scaffolding new packs (developer mode)" section.

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad confirming: (a) the CLI wires into `adminme/cli/main.py` via `app.add_typer`; no changes to the console-script entry point; (b) the `list` subcommand walks `packs/adapters/<family>/<name>/` (depth-2 scan from `packs/adapters/`, not the whole `packs/` tree); (c) the `validate` subcommand calls `validate_adapter_pack` with no flag overrides (registry + projections_dir default to on-main); the `--registry-stub` and `--projections-dir` flags are for advanced testing and out-of-scope for this prompt; (d) the `scaffold` subcommand's three-layer gate is enforced top-down (env, then flag, then confirm) with distinct exit codes; (e) the scaffold templates inherit from the correct ABC + emit a `pack.yaml` that round-trips through `parse_adapter_manifest` + pass `validate_adapter_pack` with no errors (advisory_pending_retrofit is acceptable per the same reasoning that applies to existing Gmail/Plaid/Reminders packs).

---

## §3 Reconciliation notes (carry-forwards from 11-α + 11-β merges)

- **Path correction (Gate 2 verified)**: split memo §4 11-γ deliverables say `adminme/cli/commands/adapters.py` + `adminme/cli/commands/adapters_scaffold/templates/`. The on-main `adminme/cli/main.py` is the Typer entry point per prompt 02; `commands/` subdirectory is new under `adminme/cli/`. No drift from 11-α / 11-β's `adminme/lib/pack_registry/` pattern — both prompts created NEW subdirectories.
- **F-3 HARD (10c-iii heritage)**: every pack-internal test scaffold template uses a unique basename — `tests/test_<name>_adapter_pack_internal.py` with `<name>` substituted from the scaffold's `--name` flag. Carry-forward holds for Cohort C retrofit + Cohort D reference implementations.
- **Observation-mode wrapping (11-β c3)**: the c3 validator pinned the AST-matcher to `adminme.lib.observation.outbound` (verified at 11-β authorship). Cat-A + Cat-E scaffold templates' adapter.py bodies MUST emit a call to this exact symbol; the templates ship with comments naming the import line so developers don't accidentally re-name it.
- **Stacked-merge anti-pattern fix (PR #106 — on main)**: c2/c3/c4 PRs from this cycle open with `base=main` automatically. Ghost conflicts resolve via the Chrome MCP JS-regex pattern proven on 11c + 11-α + 11-β (Partner drives).
- **Constitution-sync rule (PR #97)**: this PR adds NEW infrastructure (CLI subcommand group + scaffold templates + authoring guide) but does NOT modify constitutional docs. The new `docs/adapter-authoring-guide.md` is NOT a constitutional document — it documents what the framework already enforces; the framework is canonical, the guide is descriptive. No cascade exemption needed.
- **Rebase carry-forward (memo 08)**: this prompt closes Cohort B. After 11-γ merges, the framework expansion is complete and Cohort C (retrofit Gmail/Plaid/Reminders onto the new ABCs) becomes the next-prompt target per memo 08 §3.3.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `adminme/cli/main.py` exists and exposes `app: typer.Typer`. **Verified at authorship (2026-05-16).** The `commands/adapters.py` module is registered via `app.add_typer(adapters_app, name="adapters")` from c1.

A2. `adminme/cli/commands/` directory does NOT exist on main. **Verified at authorship.** c1 creates it; the `__init__.py` is the package marker.

A3. `adminme/lib/pack_registry/validator/__init__.py` exposes `validate_adapter_pack(pack_dir, *, registry=None, projections_dir=None) -> ValidationResult`. **Verified at authorship.**

A4. `adminme/lib/adapters/__init__.py` re-exports all 5 ABCs. **Verified at authorship.**

A5. `pyproject.toml` has `typer >= 0.12` + `rich`. No new dependency adds. **Verified at authorship.**

A6. `adminme/lib/observation.py` exports a callable named `outbound` (the 11-β AST-matcher target). Confirm at c3 start by `grep -n 'def outbound' adminme/lib/observation.py`; scaffold templates' Cat-A + Cat-E `adapter.py` bodies use this exact symbol.

A7. The three existing packs (Gmail / Plaid / Reminders) parse cleanly through `parse_adapter_manifest`. **Verified by 11-β c4 integration tests on main.**

A8. `docs/adapter-authoring-guide.md` does NOT exist on main. **Verified at authorship.** c4 creates it.

---

## §5 Objective

Ship the `adminme adapters` CLI subcommand group at `adminme/cli/commands/adapters.py` with three subcommands (`list` / `validate` / `scaffold`). Add a scaffold-template tree at `adminme/cli/commands/adapters_scaffold/templates/` with five per-category entries. Document the framework end-to-end in `docs/adapter-authoring-guide.md`. The `scaffold` subcommand enforces the [D20] three-layer developer-mode gate; the `validate` subcommand wraps the 11-β validator's `validate_adapter_pack` aggregator and renders findings via rich. ≥20 new tests floor.

---

## §6 Out of scope

- **New ABCs or new validator checks** — the 5 ABCs from 11-α and the 6 checks from 11-β are the canonical surface. Extensions ship via constitutional amendment, not via this prompt.
- **Installing the scaffolded pack** — `scaffold` writes files; it does NOT run `validate` on them, it does NOT register the pack with the supervisor, and it does NOT add anything to `bootstrap/pack_install_order.yaml`. The developer runs validate + install separately as the next workflow step.
- **CLI subcommand groups for the other framework surfaces** (`adminme events`, `adminme projections`, etc.) — those ship under separate prompts (15 + 17 per the existing build sequence).
- **Live pack-discovery beyond `packs/adapters/<family>/<name>/`** — third-party packs installed via PyPI or git-clone live outside this prompt's discovery scope. Cohort B's framework is for the bundled + verified-third-party tiers per [D20]; user-authored packs scaffold via `adminme adapters scaffold` and then install via the same supervisor flow once retrofitted.
- **Retrofitting existing Gmail / Plaid / Reminders packs to the new ABCs** — Cohort C retrofit prompts per memo 08 §3.3. The `validate` subcommand surfaces these packs' current state as advisory_pending_retrofit; Cohort C closes the gap.
- **Cross-pack capability collision check in the CLI** — out of scope; install-order discipline + supervisor-level capability registry handle those at install time per 11-β §6.
- **Auto-detection of developer mode from a `.developer-mode` file** — explicitly rejected; the [D20] three-layer gate is env + flag + confirm. A file-based override would weaken the gate (a stale file on disk could enable scaffolding the developer didn't intend).
- **Authoring guide translations** — English-only for Phase A. i18n out of scope until a non-English operator is identified.

---

## §7 Deliverables

### CLI subcommand group at `adminme/cli/commands/`

- **`__init__.py`** (NEW) — empty package marker.

- **`adapters.py`** (~280 lines including the three subcommand bodies + the developer-mode gate helper + the rich-table renderer + the scaffold-template loader). Defines:
  - `adapters_app: typer.Typer` — the subcommand group.
  - `@adapters_app.command("list")` — walks `packs/adapters/<family>/<name>/`, parses each `pack.yaml` via `parse_adapter_manifest` (catches `ManifestParseError` per-pack and surfaces as `<unparseable>` row), renders a rich.table with columns: name, family, version, kinds (joined Cat-A/Cat-B/Cat-C/Cat-D/Cat-E), runtime, sensitivity_default.
  - `@adapters_app.command("validate")` — accepts `pack_dir: pathlib.Path` positional arg, calls `validate_adapter_pack(pack_dir)`, renders each finding with severity color via rich.console (red = error, yellow = warning, blue = advisory). Exit codes: 0 (no errors), 1 (errors), 2 (ManifestParseError).
  - `@adapters_app.command("scaffold")` — accepts `kind: CapabilityKindArg` + `runtime: CapabilityRuntimeArg` positional args + `--output: pathlib.Path = "./adapter-skeleton-<kind>-<runtime>"` + `--name: str = "<lowercase-kind>-<runtime>-skeleton"` + `--allow-scaffold: bool` + `--yes: bool`. Enforces the three-layer gate top-down; on PASS, loads the matching template from `adapters_scaffold/templates/<kind-lower>-<runtime>/`, copies + substitutes `<name>` into pack.yaml + adapter.py + test_<name>_adapter_pack_internal.py, writes to `<output>`.

- **`adapters_scaffold/__init__.py`** (NEW) — empty package marker (so the templates directory's contents ship with the package).

- **`adapters_scaffold/templates/`** — five template subdirectories, each with `pack.yaml.j2` + `adapter.py.j2` + `config.schema.json` + `tests/test_<NAME>_adapter_pack_internal.py.j2`:
  - **`cat-a-central/`** — CommunicationAdapter subclass; manifest declares `capabilities: [{kind: Cat-A, runtime: central, event_families: [messaging.received, messaging.sent]}]`; adapter.py routes `send` through `adminme.lib.observation.outbound`.
  - **`cat-b-central/`** — ExternalStateMirrorAdapter subclass; manifest declares Cat-B with `write_capabilities: [example_op_one, example_op_two]` + `sharing_model_discriminator: owner_member_id`; adapter.py implements `_handle_example_op_one` + `_handle_example_op_two`.
  - **`cat-b-dual-deployment/`** — Cat-B with `runtime: dual`; manifest declares both central + bridge deployment markers per memo 05 §2.5.
  - **`cat-c-central/`** — InboundDataAdapter subclass; manifest with empty `write_capabilities: []` (Cat-C has no write surface).
  - **`cat-d-bridge/`** — PersonalKnowledgeAdapter subclass; manifest with `runtime: bridge` + `owner_scope_overridability_at_install: false` per memo 04 §2.2.
  - **`cat-e-central/`** — OutboundActionAdapter subclass; manifest with `observation_mode_required: true`; adapter.py routes `execute_action` through `adminme.lib.observation.outbound`.

  Templates use `.j2` extension for files that need `<NAME>` substitution; the loader uses `str.replace` (not Jinja2) for simplicity — the substitutions are name + class-name capitalization only, no logic.

### Wire-up in `adminme/cli/main.py`

- Add `from adminme.cli.commands.adapters import adapters_app` import.
- Add `app.add_typer(adapters_app, name="adapters")` after the existing `app = typer.Typer(...)` declaration.

### Authoring guide at `docs/adapter-authoring-guide.md`

- ~400 lines. Sections:
  1. Introduction (why adapter packs; the 5-category taxonomy at a glance with one-sentence summaries).
  2. Quickstart (`adminme adapters scaffold Cat-C central` → fill in `adapter.py` → `adminme adapters validate ./<output>` → install). Worked end-to-end.
  3. Choosing a category (decision tree: read-only? Cat-C. Bidirectional state mirror? Cat-B. Bidirectional messaging? Cat-A. Personal knowledge ingest? Cat-D. Outbound action? Cat-E).
  4. Choosing a runtime (central vs bridge vs dual vs plugin).
  5. The manifest extensions (`capabilities`, `write_capabilities`, `sensitivity_default`, `observation_mode_required`, `owner_scope_overridability_at_install`, `sharing_model_discriminator`, `dedup_strategy`) — one subsection per field with the rule + the example.
  6. The 6 validator rules — one subsection per check (capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence, observation-mode wrapping). Each subsection: the rule, the failure example, the diagnostic message shape, the fix.
  7. Multi-capability adapters (Cat-D + Cat-B Apple Notes-checklists per memo 05 §2.2; Cat-C + Cat-E Home Assistant per [D24]).
  8. Scaffolding new packs (developer mode) — the [D20] three-layer gate, exit codes, env var, flag, confirm.
  9. Backward-compat with v1 manifests (Gmail / Plaid / Reminders — advisory_pending_retrofit until Cohort C lands).
  10. Where to look in the codebase (5 ABC files, validator modules, the 3 reference packs).

### Tests at `tests/unit/cli/commands/`

- `test_adapters_list.py` (~6 tests): walks a synthetic `packs/adapters/` on tmp_path; renders table; handles `<unparseable>` row on malformed pack.yaml; exit code 0 on filesystem error.
- `test_adapters_validate.py` (~6 tests): wraps `validate_adapter_pack`; exits 0 on no-error result; exits 1 on error finding; exits 2 on `ManifestParseError`; renders red/yellow/blue findings.
- `test_adapters_scaffold.py` (~8 tests): refuses without `ADMINME_DEVELOPER_MODE=1` (exit 3); refuses without `--allow-scaffold` (exit 4); refuses on confirm-decline (exit 5); accepts with all three layers + `--yes`; generates one happy-path Cat-C central skeleton on tmp_path and asserts `validate_adapter_pack` returns no errors on the output.

### Integration test at `tests/integration/test_adapters_cli_end_to_end.py`

- ~4 tests: scaffold Cat-A central → validate → no errors; scaffold Cat-B central → validate → no errors; scaffold Cat-D bridge → validate → no errors; scaffold Cat-E central → validate → no errors. End-to-end exercise of the framework: scaffold writes a pack the validator immediately accepts.

Test floor: ≥20 across the four commits (~6 list + ~6 validate + ~8 scaffold + ~4 integration = 24).

---

## §8 Commit structure (exactly four commits; messages exact)

Every commit message follows `phase 11-gamma commit <N>: <imperative summary>` exactly. Branch strategy STACKED per §8.5; PR base = `main` for ALL FOUR c-bands per PR #106 sidecar.

### Commit 1 — `adminme adapters list` subcommand + wire-up

**Files:** `adminme/cli/commands/__init__.py` (NEW), `adminme/cli/commands/adapters.py` (NEW; only the `list` subcommand body + `adapters_app` declaration), update `adminme/cli/main.py` to wire the subcommand group, `tests/unit/cli/__init__.py` (NEW if missing), `tests/unit/cli/commands/__init__.py` (NEW), `tests/unit/cli/commands/test_adapters_list.py` (NEW).

**Files-to-read:** 5 (`adminme/cli/main.py` for the Typer app surface, `adminme/lib/pack_registry/parser.py` for `parse_adapter_manifest` + `ManifestParseError`, `adminme/lib/pack_registry/models.py` for the field shape the table renders, an existing pack's pack.yaml for the expected table content, the split memo §4 11-γ breakdown).

**Verification:**
```
poetry run python -c "from adminme.cli.commands.adapters import adapters_app; print('OK')"
poetry run adminme adapters list  # exercises the subcommand against on-main packs
poetry run mypy adminme/cli/commands/ 2>&1 | tail -10
poetry run pytest tests/unit/cli/commands/test_adapters_list.py -v
git add adminme/cli/commands/__init__.py adminme/cli/commands/adapters.py adminme/cli/main.py tests/unit/cli/
git commit -m "phase 11-gamma commit 1: adminme adapters list subcommand + wire-up"
```

Expected: mypy clean; ≥6 tests pass; `adminme adapters list` shows three rows (messaging-gmail-api / financial-plaid / reminders-apple-eventkit) on main packs.

### Commit 2 — `adminme adapters validate <pack-dir>` subcommand

**Files:** Update `adminme/cli/commands/adapters.py` to add the `validate` subcommand body, `tests/unit/cli/commands/test_adapters_validate.py` (NEW).

**Files-to-read:** 4 (`adminme/lib/pack_registry/validator/__init__.py` for the `validate_adapter_pack` signature, `adminme/lib/pack_registry/validator/result.py` for `ValidationResult` accessors, an existing pack to use as the happy-path test fixture, the existing tests in c1).

**Verification:**
```
poetry run mypy adminme/cli/commands/ 2>&1 | tail -10
poetry run pytest tests/unit/cli/commands/ -v
poetry run adminme adapters validate packs/adapters/messaging/gmail  # smoke test
git add adminme/cli/commands/adapters.py tests/unit/cli/commands/
git commit -m "phase 11-gamma commit 2: adminme adapters validate subcommand"
```

Expected: mypy clean; ≥6 additional tests pass (running total ≥12); smoke test exits 0 + surfaces the advisory_pending_retrofit finding for Gmail.

### Commit 3 — `adminme adapters scaffold` subcommand + 5 scaffold templates

**Files:** Update `adminme/cli/commands/adapters.py` to add the `scaffold` subcommand body + the three-layer gate helper + the template loader. `adminme/cli/commands/adapters_scaffold/__init__.py` (NEW). `adminme/cli/commands/adapters_scaffold/templates/cat-a-central/` (NEW, 4 files: pack.yaml.j2, adapter.py.j2, config.schema.json, tests/test_<NAME>_adapter_pack_internal.py.j2). Similarly for `cat-b-central/`, `cat-b-dual-deployment/`, `cat-c-central/`, `cat-d-bridge/`, `cat-e-central/`. `tests/unit/cli/commands/test_adapters_scaffold.py` (NEW).

**Files-to-read:** 6 (each of the 5 ABCs at `adminme/lib/adapters/` so the templates emit the correct base-class names + abstract-method signatures, `adminme/lib/observation.py` to confirm the exact `outbound` symbol name for the Cat-A + Cat-E templates).

**Critical step at c3 start:** verify `grep -n '^def outbound' adminme/lib/observation.py` returns a hit; if the symbol is named differently, update the Cat-A + Cat-E template bodies to use the actual on-main name before writing.

**Verification:**
```
poetry run mypy adminme/cli/commands/ 2>&1 | tail -10
poetry run pytest tests/unit/cli/commands/ -v
# Refuse without env var
ADMINME_DEVELOPER_MODE=0 poetry run adminme adapters scaffold Cat-C central --allow-scaffold --yes --output /tmp/x
# Refuse without flag
ADMINME_DEVELOPER_MODE=1 poetry run adminme adapters scaffold Cat-C central --yes --output /tmp/x
# Accept with all three layers
ADMINME_DEVELOPER_MODE=1 poetry run adminme adapters scaffold Cat-C central --allow-scaffold --yes --output /tmp/scaffold-test
# Generated pack passes validate
ADMINME_DEVELOPER_MODE=1 poetry run adminme adapters validate /tmp/scaffold-test
git add adminme/cli/commands/adapters.py adminme/cli/commands/adapters_scaffold/ tests/unit/cli/commands/
git commit -m "phase 11-gamma commit 3: adminme adapters scaffold subcommand + 5 templates"
```

Expected: mypy clean; ≥8 additional scaffold tests pass (running total ≥20); the env-missing and flag-missing refusals exit with codes 3 and 4; the happy-path generates a pack that `validate` accepts without errors.

### Commit 4 — Authoring guide + integration tests + BUILD_LOG

**Files:** `docs/adapter-authoring-guide.md` (NEW; ~400 lines). `tests/integration/test_adapters_cli_end_to_end.py` (NEW; 4 end-to-end tests scaffolding + validating one pack per category). `docs/build_log.md` (append the 11-γ entry).

**Files-to-read:** 4 (`tests/integration/test_adapter_framework_validator.py` for the integration test pattern from 11-β c4, `docs/build_log.md` tail for the append point, memo 05 §2.2 for the multi-capability worked example the guide §"Multi-capability adapters" section uses, the existing reference packs (Gmail/Plaid/Reminders) for the guide §"Where to look" section's pointers).

**Build_log Evidence template:** Append AFTER the 11-β entry. Header: `### Prompt 11-γ — Adapter framework CLI + authoring guide (closes Cohort B)`. Cite: the 3 subcommands at `adminme/cli/commands/adapters.py`; the 5 scaffold templates; the authoring guide; the three-layer developer-mode gate per [D20] with distinct exit codes (3/4/5); end-to-end integration tests scaffolding 4 packs and validating each cleanly; carry-forwards into Cohort C (retrofit prompts close advisory_pending_retrofit) and Cohort D (resumed canon adopts the ABCs directly).

**Verification:**
```
poetry run ruff check adminme/cli/commands/ tests/unit/cli/commands/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
# Full end-to-end: scaffold + validate clean for each of the 4 categories tested
ADMINME_DEVELOPER_MODE=1 poetry run adminme adapters scaffold Cat-A central --allow-scaffold --yes --output /tmp/test-a && poetry run adminme adapters validate /tmp/test-a
ADMINME_DEVELOPER_MODE=1 poetry run adminme adapters scaffold Cat-B central --allow-scaffold --yes --output /tmp/test-b && poetry run adminme adapters validate /tmp/test-b
ADMINME_DEVELOPER_MODE=1 poetry run adminme adapters scaffold Cat-D bridge --allow-scaffold --yes --output /tmp/test-d && poetry run adminme adapters validate /tmp/test-d
ADMINME_DEVELOPER_MODE=1 poetry run adminme adapters scaffold Cat-E central --allow-scaffold --yes --output /tmp/test-e && poetry run adminme adapters validate /tmp/test-e
git add docs/build_log.md docs/adapter-authoring-guide.md tests/integration/test_adapters_cli_end_to_end.py
git commit -m "phase 11-gamma commit 4: authoring guide + CLI integration tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+20; `verify_invariants.sh` exit 0; each end-to-end scaffold+validate pair exits 0.

**PR open:** Title `phase 11-gamma: adapter framework CLI + authoring guide (closes Cohort B)`. Body: top line "Third and final sub-prompt of the prompt-11 framework split per `docs/09-split-memo-11-framework.md`. Ships the `adminme adapters` Typer subcommand group at `adminme/cli/commands/adapters.py` (list / validate / scaffold), five per-category scaffold templates at `adminme/cli/commands/adapters_scaffold/templates/`, and the authoring guide at `docs/adapter-authoring-guide.md`. Three-layer developer-mode gate per [D20] (env var + --allow-scaffold flag + interactive confirm) with distinct exit codes. End-to-end: scaffold a Cat-X pack, validate it, no errors. Closes Cohort B per `docs/08-architecture-amendment-adapter-rebase.md` §3.2." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` is `main` per PR #106 sidecar. Pre-flight stack-integrity check on c4 expects 3 prior commits above `origin/main` (c1+c2+c3).

Ghost conflicts on c2/c3/c4 (squash-merge artifacts) — resolve via the JS regex pattern proven on 11c + 11-α + 11-β (Partner drives Chrome MCP per the precedent).

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥20 new tests passing; ruff clean on `adminme/cli/commands/` / `tests/unit/cli/commands/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`; end-to-end check (4 categories scaffold + validate cleanly) passes.

Cross-cutting [§N] / [DN] checks:

- [§7.3, D17]: zero projection writes from CLI code (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' adminme/cli/commands/` returns 0). The CLI is pure-functional over filesystem + validator.
- [§8.2, D6]: no LLM SDK imports under `adminme/cli/commands/`. The CLI does not call OpenClaw either.
- [§6.20]: scaffold templates' Cat-A and Cat-E adapter.py bodies emit calls to `adminme.lib.observation.outbound`. The c4 end-to-end test validates this by running the 11-β observation-mode-wrapping check on each scaffolded pack and asserting no findings.
- [D20]: three-layer gate enforced — env var THEN flag THEN confirm. All three layers required for scaffold to proceed. Each refusal exits with a distinct code (3 / 4 / 5) per the gate's diagnostic discipline.
- [PM-34]: 5 scaffold templates correspond to the 5 ABCs at `adminme/lib/adapters/`. Each template's adapter.py inherits from the matching ABC name.
- [PM-35]: capabilities-as-list shape preserved in each scaffold template's pack.yaml; multi-capability examples documented in the authoring guide.
- [UT-27]: scaffold templates emit `write_capabilities` only for Cat-B + Cat-E; Cat-A / Cat-C / Cat-D templates emit `write_capabilities: []`.
- **memo 04 §2.2**: Cat-D bridge scaffold template emits `runtime: bridge` + `owner_scope_overridability_at_install: false`.

---

## §10 Stop

`adminme adapters` CLI subcommand group with three subcommands (`list` / `validate` / `scaffold`) shipped. Five per-category scaffold templates ship at `adminme/cli/commands/adapters_scaffold/templates/`. Authoring guide at `docs/adapter-authoring-guide.md` documents the framework end-to-end. Three-layer developer-mode gate per [D20] enforced on `scaffold`. End-to-end workflow (scaffold → validate → no errors) verified for 4 categories in the integration test.

**Closes Cohort B** per `docs/08-architecture-amendment-adapter-rebase.md` §3.2.

Ready for **Cohort C retrofit prompts** per `docs/08-architecture-amendment-adapter-rebase.md` §3.3: Gmail → CommunicationAdapter, Plaid → InboundDataAdapter, Reminders → ExternalStateMirrorAdapter. Each retrofit closes one of the three `advisory_pending_retrofit` findings the 11-β validator currently surfaces on the existing v1 packs.
