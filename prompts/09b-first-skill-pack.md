# Prompt 09b: First canonical skill pack — `classify_thank_you_candidate`

**Phase:** BUILD.md "L4 CONTINUED: THE SKILL RUNNER (LAYERED ON OPENCLAW)" + ADR-0002. First *real* (non-test) skill pack landing through the wrapper.
**Depends on:** 09a merged. `adminme.lib.skill_runner.pack_loader.load_pack` accepts the canonical pack shape (pack.yaml + SKILL.md frontmatter + schemas/{input,output}.schema.json + prompt.jinja2 + optional handler.py exposing `post_process(raw, inputs, ctx)`). `adminme.lib.skill_runner.wrapper.run_skill(skill_id, inputs, ctx)` is stable. `packs/skills/classify_test/` is the dummy pack used by the wrapper's own tests; this prompt ships the first *non-dummy* pack against the same loader contract. Pattern: **Extension** of 09a per `D-prompt-tier-and-pattern-index.md`.
**Estimated duration:** 2 hours.
**Stop condition:** `await run_skill("classify_thank_you_candidate", <fixture_input>, ctx)` against a mocked `/tools/invoke` returns the expected output for each of three named fixtures (`kleins_hosted_us`, `reciprocal_coffee`, `coparent_pickup`); pack-loader accepts the pack without error; pack id appears in `bootstrap/pack_install_order.yaml`; `bash scripts/verify_invariants.sh` exits 0.

---

## Read first (required)

Read these in order. Do NOT proceed without them.

1. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` lines **1030–1530** — the canonical `classify_thank_you_candidate` reference, in full. (`sed -n '1030,1530p' ADMINISTRATEME_REFERENCE_EXAMPLES.md`) **Critical reading note: §3 is the *content* spec — what the pack does, what its prompt says, what its schemas require, what the three fixtures look like. The §3 *file layout* is illustrative and DIVERGES from what 09a's `pack_loader.py` actually accepts. The actual loader contract wins. See "Operating context — pack shape" below.**
2. `adminme/lib/skill_runner/pack_loader.py` — full file (~210 lines). Confirm what the loader requires: top-level `pack:` block in `pack.yaml` carrying `id` + `version`; SKILL.md *frontmatter* (YAML between `---` fences) carrying the runtime contract; schemas/{input,output}.schema.json validated via `Draft202012Validator.check_schema()`; `prompt.jinja2` mandatory; `handler.py` optional and (if present) MUST expose a top-level `post_process(raw, inputs, ctx) -> dict` callable.
3. `adminme/lib/skill_runner/wrapper.py` — three specific ranges:
   - lines **95–125** for `SkillContext` and `SkillResult` shape (only fields exposed to your tests).
   - lines **365–400** for `_build_llm_task_args` — confirms which frontmatter keys the wrapper consumes (`provider_preferences`, `max_tokens`, `temperature`, `timeout_seconds`, `thinking`).
   - lines **520–595** for the input-validation, sensitivity, scope, and observation/dry-run gates.
4. `packs/skills/classify_test/` — full directory. **This is your reference shape.** Mirror it exactly: `pack.yaml`, `SKILL.md` (with frontmatter), `prompt.jinja2`, `schemas/input.schema.json`, `schemas/output.schema.json`. Note `classify_test` ships *without* `handler.py` because trivial passthrough suffices; `classify_thank_you_candidate` ships *with* `handler.py` for the urgency-coercion safety net (per §3 lines 1389–1395).
5. `tests/unit/test_skill_wrapper.py` lines **1–80** — confirms how tests construct `Session` and `SkillContext`, mock `/tools/invoke` via `httpx.MockTransport`, and import `_set_runtime_for_tests` + `invalidate_cache`. Mirror this pattern for the new tests; do NOT invent a different harness.
6. `tests/unit/fixtures/handler_raises_pack/handler.py` — confirms the canonical `post_process(raw, inputs, ctx)` signature (three positional args). Your handler matches this signature exactly.
7. `docs/openclaw-cheatsheet.md` Q1 (skill install) and Q5 (SKILL.md shape) — reference only; this prompt does NOT install into a live OpenClaw (Phase A).
8. SYSTEM_INVARIANTS `[§8]`/`[D6]` (no LLM/embedding SDK imports in `adminme/`; same for `packs/`), `[§12.4]` (no tenant-identity in platform code; the §3 fixtures use "stice-james" / "Stice household" — those names live in `packs/skills/classify_thank_you_candidate/tests/fixtures/` only and in `tests/integration/test_classify_thank_you_pack.py` fixture-construction sites, NOT in production `adminme/` code).

If any of files 1–6 are missing, are smaller than expected, or differ structurally from the above, **stop and report**. Do not paper over drift.

---

## Operating context

### What this prompt establishes

`classify_thank_you_candidate` is the first canonical (production-shape) skill pack. Every later skill pack — `classify_commitment_candidate`, `extract_commitment_fields`, `compose_morning_digest`, etc. — copies this layout. Get it right; it sets the pattern.

The pack is *not* installed into a live OpenClaw in Phase A. There is no OpenClaw gateway in the sandbox. Validation in Phase A is structural (loader accepts it) + behavioral (post-handler coercion logic does what it should against mocked gateway responses) + queued for Phase B install via `bootstrap/pack_install_order.yaml` (a new file this prompt creates; consumed in prompt 15 / 16).

### Pack shape — what the loader actually accepts

The §3 spec puts the runtime contract in `pack.yaml` (`runtime:`, `model: preferred/fallback`, `inputs.schema:`, `outputs.schema:`) and treats SKILL.md as plain markdown documentation. **09a's loader does NOT honor that shape.** The merged `pack_loader.py` reads:

- `pack.yaml` → must contain top-level `pack:` block with at minimum `id: str` (non-empty) and `version: str` (non-empty). Other keys parsed but not enforced; they're available via `loaded.manifest`.
- `SKILL.md` → `---`-fenced YAML frontmatter (parsed into `loaded.skill_frontmatter: dict`) followed by markdown body (`loaded.skill_body: str`). The wrapper consumes the *frontmatter* keys: `provider_preferences` (list of `provider/model` strings), `sensitivity_required` ("normal" | "sensitive" | "privileged"), `context_scopes_required` (list of scope strings), `outbound_affecting` (bool), `timeout_seconds` (int), `max_tokens` (int), `temperature` (float), optional `thinking`, optional `on_failure` (defensive default dict returned if handler raises and the pack declares one).
- `schemas/input.schema.json` and `schemas/output.schema.json` → required; validated as JSON Schemas via `Draft202012Validator.check_schema()`.
- `prompt.jinja2` → required; passed verbatim to OpenClaw as the `prompt` arg per ADR-0002. (Rendering happens inside OpenClaw — the wrapper does not pre-render.)
- `handler.py` → optional. If present, must expose a callable `post_process(raw_response: dict, inputs: dict, ctx: SkillContext) -> dict`. Loader fails the pack with `PackLoadError` if `handler.py` exists but lacks `post_process`.

This means the §3 reference text needs **translation**, not transcription:

- §3's `pack.yaml` `runtime:`, `model:`, `inputs:`, `outputs:`, `documentation:` blocks → keep `pack:` (id, version, kind, author, license, min_platform); the rest is metadata-only and the loader ignores it. Keep `model.preferred` / `model.fallback` in `pack.yaml` for documentation, but the *active* preferences live in SKILL.md frontmatter as `provider_preferences`.
- §3's `SKILL.md` → keep the markdown body verbatim (with the documentation, criteria, tier reference, change log). Add `---`-fenced frontmatter at the top per the `classify_test` template, populated for thank-you classification.
- §3's `handler.py` (`SkillBase` subclass, `adminme_platform.skills.SkillBase`, `adminme_platform.models.call_model`, `adminme_platform.projections.parties`) → **none of those imports exist or will exist.** `adminme_platform` is illustrative naming for a future top-level package layout that AdministrateMe never actually adopted (AdministrateMe is `adminme/`, and the wrapper handles model calls + correlation threading + replay archiving itself). **Replace §3's handler entirely** with a top-level `post_process(raw, inputs, ctx)` function whose only job is the one safety-net coercion §3 lines 1389–1395 describe: if `raw["is_candidate"] is True` and `raw.get("urgency")` is missing, coerce to `is_candidate=False` and append `"missing_urgency"` to reasons. The TIER_LABELS dict and the prompt-rendering machinery from §3 are NOT needed in the handler — the wrapper passes the prompt template to OpenClaw as-is, and the prompt itself can describe the tier interpretation in plain language for the model.
- §3's `tests/fixtures/*.yaml` → keep the three fixtures verbatim in content. They live at `packs/skills/classify_thank_you_candidate/tests/fixtures/` and are consumed by the unit tests under `tests/integration/test_classify_thank_you_pack.py` (Test layout below). The `# fixture:tenant_data:ok` discipline applies — the fixtures contain "stice-james", "Stice household", "Klein", "Mike (coparent)", which are tenant-identifiable names. Per `[§12.4]` these live in fixtures only.

### Test housing

The pack's tests live at:

- `packs/skills/classify_thank_you_candidate/tests/fixtures/{kleins_hosted_us,reciprocal_coffee,coparent_pickup}.yaml` — input + expected_output yaml per §3 (verbatim content).
- `tests/integration/test_classify_thank_you_pack.py` — pytest module that loads each fixture, mocks `/tools/invoke` to return what a well-behaved gateway would for that input, calls `await run_skill("classify_thank_you_candidate", inputs, ctx)`, and asserts the output matches `expected_output` per fixture's relaxed-match conventions (`confidence_min`, `reasons_must_include_any_of`).
- `packs/skills/classify_thank_you_candidate/tests/test_skill.py` — minimal pytest module that calls the loader on the pack and asserts it parses (manifest, frontmatter, schemas all valid). One test. Catches structural drift in the pack itself.

The pack's tests live at the integration tier (not unit) because they exercise the wrapper end-to-end including the handler post-processing step. The unit-tier `tests/unit/test_skill_wrapper.py` from 09a remains the wrapper-internals coverage; nothing there needs editing.

---

## Objective

Ship:

1. `packs/skills/classify_thank_you_candidate/` — the canonical pack at version `1.3.0` (matching §3's manifest version). Layout:
   ```
   pack.yaml
   SKILL.md                           (frontmatter + body)
   prompt.jinja2
   handler.py                         (post_process safety-net coercion)
   schemas/input.schema.json
   schemas/output.schema.json
   tests/fixtures/kleins_hosted_us.yaml
   tests/fixtures/reciprocal_coffee.yaml
   tests/fixtures/coparent_pickup.yaml
   tests/test_skill.py                (loader-validates-this-pack canary)
   ```
2. `tests/integration/test_classify_thank_you_pack.py` — three fixture-driven tests (one per named fixture) plus one handler-direct test for the urgency-coercion safety net. Four tests total. All HTTP mocked via `httpx.MockTransport`; no live OpenClaw.
3. `bootstrap/pack_install_order.yaml` — NEW file. Single top-level `built_in_skill_packs:` list containing one entry: `- skill:classify_thank_you_candidate`. Order matters for later prompts (15 install path); preserve the list shape. Two-line header comment cites this is consumed by prompt 15 + bootstrap §6 / prompt 16.

---

## Out of scope

- Do NOT implement the `thank_you` pipeline that consumes this skill. That's prompt 10b.
- Do NOT install the pack into a live OpenClaw, run the OpenClaw CLI, or call any non-mocked HTTP endpoint. There is no live gateway in the sandbox.
- Do NOT add new event types. The wrapper already emits `skill.call.recorded` / `skill.call.failed` / `skill.call.suppressed` from the single seam at `adminme/lib/skill_runner/wrapper.py`. This pack adds zero emit sites; it's purely a *consumer* of the wrapper. **Do not edit `scripts/verify_invariants.sh` — the `SKILL_EMITS` block from 09a already covers this pack and every future pack, since every pack routes through the same wrapper.**
- Do NOT add other skill packs (`classify_commitment_candidate`, `extract_commitment_fields`, etc.) — those ship with their owning pipelines (10b/10c).
- Do NOT add provider SDK imports (`anthropic`, `openai`, `sentence_transformers`) anywhere under `adminme/` or `packs/`. `[§8]`/`[D6]`. `verify_invariants.sh` enforces.
- Do NOT add a `replay archive` / `replays/` directory. §3's manifest references `tests/fixtures/replays/`; that's a future feature. Phase A skips it.
- Do NOT consume tenant-identity strings in `adminme/` production code. The "Stice household" / "stice-james" names are confined to fixtures (`packs/skills/classify_thank_you_candidate/tests/fixtures/` and the test module) per `[§12.4]`. Add `# fixture:tenant_data:ok` to any line in `tests/integration/test_classify_thank_you_pack.py` where the fixture-id strings appear inline if the verify-script's tenant-identity grep flags them.

---

## Four-commit plan

### Commit 1 — pack scaffold + structural-validation test

**Files:**
- `packs/skills/classify_thank_you_candidate/pack.yaml` — top-level `pack:` block:
  ```yaml
  pack:
    id: skill:classify_thank_you_candidate
    name: Classify thank-you candidate
    version: 1.3.0
    kind: skill
    author: built-in
    license: Apache-2.0
    min_platform: 0.4.0

  # Documentation-only metadata (the loader ignores everything below
  # `pack:`; the active runtime contract lives in SKILL.md frontmatter).
  model:
    preferred: anthropic/claude-haiku-4-5
    fallback: anthropic/claude-opus-4-7
  ```
- `packs/skills/classify_thank_you_candidate/SKILL.md` — `---`-fenced frontmatter (declared shape that the wrapper actually reads), then the markdown body verbatim from REFERENCE_EXAMPLES.md §3 lines 1083–1151 (`# classify_thank_you_candidate` heading through `## Change log`):
  ```yaml
  ---
  name: classify_thank_you_candidate
  namespace: adminme
  version: 1.3.0
  description: Decide whether a principal should send a thank-you note to a party after a recent interaction or favor.
  input_schema: ./schemas/input.schema.json
  output_schema: ./schemas/output.schema.json
  provider_preferences:
    - anthropic/claude-haiku-4-5
    - anthropic/claude-opus-4-7
  max_tokens: 400
  temperature: 0.2
  sensitivity_required: normal
  context_scopes_required: []
  timeout_seconds: 12
  outbound_affecting: false
  on_failure:
    is_candidate: false
    confidence: 0.0
    reasons: ["skill_failure_defensive_default"]
  ---

  # classify_thank_you_candidate
  <... rest of §3's body verbatim ...>
  ```
- `packs/skills/classify_thank_you_candidate/schemas/input.schema.json` — verbatim from REFERENCE_EXAMPLES.md §3 lines 1155–1213.
- `packs/skills/classify_thank_you_candidate/schemas/output.schema.json` — verbatim from REFERENCE_EXAMPLES.md §3 lines 1218–1244. (The schema's `if` / `then` clause requiring `urgency` + `suggested_medium` when `is_candidate: true` is exactly what the handler's safety net protects against.)
- `packs/skills/classify_thank_you_candidate/prompt.jinja2` — verbatim from REFERENCE_EXAMPLES.md §3 lines 1248–1298.
- `packs/skills/classify_thank_you_candidate/tests/test_skill.py` — single test:
  ```python
  """Loader-validates-this-pack canary. Catches structural drift in
  pack.yaml, SKILL.md frontmatter, or either schema."""
  from pathlib import Path

  from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack


  def test_pack_loads_cleanly() -> None:
      invalidate_cache()
      pack_root = Path(__file__).resolve().parents[1]
      loaded = load_pack(pack_root)
      assert loaded.pack_id == "skill:classify_thank_you_candidate"
      assert loaded.version == "1.3.0"
      assert loaded.skill_frontmatter["sensitivity_required"] == "normal"
      assert "anthropic/claude-haiku-4-5" in loaded.skill_frontmatter["provider_preferences"]
      assert loaded.handler_post_process is None  # Commit 2 adds handler.py
      assert "is_candidate" in loaded.output_schema["properties"]
  ```

**Verification (this commit):**
```bash
poetry run pytest packs/skills/classify_thank_you_candidate/tests/test_skill.py -v
poetry run ruff check packs/skills/classify_thank_you_candidate/tests/test_skill.py
```

`git add packs/skills/classify_thank_you_candidate/pack.yaml packs/skills/classify_thank_you_candidate/SKILL.md packs/skills/classify_thank_you_candidate/schemas/ packs/skills/classify_thank_you_candidate/prompt.jinja2 packs/skills/classify_thank_you_candidate/tests/test_skill.py`
`git commit -m "phase 09b/1: classify_thank_you_candidate pack scaffold"`

### Commit 2 — handler.py + structural-test update

**Files:**
- `packs/skills/classify_thank_you_candidate/handler.py` — top-level `post_process(raw, inputs, ctx)` function only. No imports from `adminme_platform`, no `SkillBase`, no jinja rendering, no `call_model`. The wrapper handles all of that. The handler's only job is the urgency-coercion safety net per §3 lines 1389–1395:
  ```python
  """Post-processing for classify_thank_you_candidate.

  The wrapper validates input, calls OpenClaw, parses the response envelope,
  then passes raw model output to this function. The function returns a dict
  the wrapper validates against output.schema.json.

  Safety net: if the model returns is_candidate=true without urgency or
  suggested_medium (which the schema requires when is_candidate=true), coerce
  to is_candidate=false rather than letting output_invalid fire downstream
  (the wrapper's `on_failure` defensive-default lookup is broader than this
  case; the handler narrows it to the specific known-flaky path).

  Per [REFERENCE_EXAMPLES.md §3 lines 1389-1395].
  """

  from typing import Any


  def post_process(raw: dict, inputs: dict, ctx: Any) -> dict:
      if not isinstance(raw, dict):
          # Should never happen — wrapper hands us a dict. Defensive only.
          return {
              "is_candidate": False,
              "confidence": 0.0,
              "reasons": ["skill_post_process_non_dict_input"],
          }
      if raw.get("is_candidate") is True and not raw.get("urgency"):
          return {
              "is_candidate": False,
              "confidence": float(raw.get("confidence", 0.0)),
              "reasons": list(raw.get("reasons", [])) + ["missing_urgency"],
          }
      return raw
  ```
- `packs/skills/classify_thank_you_candidate/tests/test_skill.py` — extend with a second test that asserts `loaded.handler_post_process is not None` after handler.py exists and exercises the coercion logic directly (no wrapper, no HTTP) on three inputs: well-formed, missing-urgency, non-dict.

**Verification (this commit):**
```bash
poetry run pytest packs/skills/classify_thank_you_candidate/tests/ -v
poetry run mypy packs/skills/classify_thank_you_candidate/handler.py 2>&1 | tail -10
poetry run ruff check packs/skills/classify_thank_you_candidate/
```

`git add packs/skills/classify_thank_you_candidate/handler.py packs/skills/classify_thank_you_candidate/tests/test_skill.py`
`git commit -m "phase 09b/2: classify_thank_you_candidate handler post_process"`

### Commit 3 — fixtures + integration tests against mocked gateway

**Files:**
- `packs/skills/classify_thank_you_candidate/tests/fixtures/kleins_hosted_us.yaml` — verbatim from REFERENCE_EXAMPLES.md §3 lines 1414–1451. The file is a yaml document with `name`, `description`, `input`, `expected_output` blocks. `expected_output` uses `confidence_min` (a floor, not exact) and `reasons_must_include_any_of` (a substring-OR check); the test code below honors those semantics.
- `packs/skills/classify_thank_you_candidate/tests/fixtures/reciprocal_coffee.yaml` — verbatim from REFERENCE_EXAMPLES.md §3 lines 1455–1486. Filename per the prompt's name shorthand (`reciprocal_coffee`); the §3 file's `name:` field reads `reciprocal_coffee_with_close_friend_not_candidate` — keep that as the document's `name:` value.
- `packs/skills/classify_thank_you_candidate/tests/fixtures/coparent_pickup.yaml` — verbatim from REFERENCE_EXAMPLES.md §3 lines 1490–1517.
- `tests/integration/test_classify_thank_you_pack.py` — three fixture tests + one handler-direct test:

  Pattern for each fixture test: load fixture yaml; build `Session` + `SkillContext`; configure `httpx.MockTransport` to return a `200 {ok:true, result:{details:{json: <expected_output_with_concrete_confidence>}, openclawInvocationId: "test-..."}}` envelope keyed off the route — the response body is a *plausible* model output that satisfies the fixture's `expected_output` constraints with a concrete confidence value (≥ `confidence_min`); call `await run_skill("classify_thank_you_candidate", fixture["input"], ctx)`; assert:
  - `result.output["is_candidate"] == fixture["expected_output"]["is_candidate"]`
  - `result.output["confidence"] >= fixture["expected_output"]["confidence_min"]`
  - if `expected_output` declares `urgency` / `suggested_medium`, those equal the result's
  - any string in `expected_output["reasons_must_include_any_of"]` appears as substring of any element of `result.output["reasons"]` (case-insensitive)
  - exactly one `skill.call.recorded` event appended to the in-memory event log
  - `[§12.4]` discipline: tag the line constructing the `Session(tenant_id="stice-test", ...)` with `# fixture:tenant_data:ok`

  Use the existing `tests/unit/test_skill_wrapper.py` lines 1–80 as the harness reference: `_set_runtime_for_tests(...)` to inject a test runtime, `invalidate_cache()` autouse fixture, `httpx.MockTransport` for HTTP mocking, `await event_log.close()` in fixture teardown.

  The fourth test exercises `handler.post_process` directly (no wrapper, no HTTP) against the kleins_hosted_us fixture's `expected_output` modified to drop `urgency` — asserts the safety net coerces to `is_candidate=false` with `"missing_urgency"` in reasons. Mirrors `tests/unit/fixtures/handler_raises_pack`'s direct-call discipline.

**Verification (this commit):**
```bash
poetry run pytest tests/integration/test_classify_thank_you_pack.py -v
poetry run ruff check tests/integration/test_classify_thank_you_pack.py
poetry run mypy tests/integration/test_classify_thank_you_pack.py 2>&1 | tail -10
```

`git add packs/skills/classify_thank_you_candidate/tests/fixtures/ tests/integration/test_classify_thank_you_pack.py`
`git commit -m "phase 09b/3: three fixture tests + handler-direct safety-net test"`

### Commit 4 — pack_install_order.yaml + verify + BUILD_LOG + push

**Files:**
- `bootstrap/pack_install_order.yaml` — NEW file:
  ```yaml
  # Ordered list of built-in skill packs the bootstrap wizard installs into
  # OpenClaw during Phase B (per prompt 16 §6). Consumers: prompt 15
  # (OpenClaw integration) and prompt 16 (bootstrap). Order matters when
  # later packs depend on earlier ones; for now the list has one entry.

  built_in_skill_packs:
    - skill:classify_thank_you_candidate
  ```
- `docs/build_log.md` — append the entry per the canonical template in `docs/qc_rubric.md`. Placeholders `PR #<N>`, `<commit4>`, `<merge date>`, `Outcome: IN FLIGHT (PR open)` — Partner fills these in post-merge during the next session's Job 1 housekeeping (UT-5).

**BUILD_LOG entry template (paste verbatim, fill bracketed evidence lines):**
```markdown
### Prompt 09b — first canonical skill pack (classify_thank_you_candidate)
- **Refactored**: by Partner in Claude Chat, 2026-04-27. Prompt file: prompts/09b-first-skill-pack.md (~290 lines, quality bar = 09a).
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - `packs/skills/classify_thank_you_candidate/` — full pack at version 1.3.0 per [REFERENCE_EXAMPLES.md §3]; pack.yaml + SKILL.md (frontmatter + §3 body) + schemas/{input,output}.schema.json + prompt.jinja2 + handler.py.
  - `handler.py` — top-level `post_process(raw, inputs, ctx)`; only logic is the urgency-coercion safety net per [REFERENCE_EXAMPLES.md §3 lines 1389-1395]. Zero `adminme_platform`-style imports.
  - `tests/test_skill.py` — pack-loads-cleanly canary + handler-direct unit cases; 2 tests.
  - `tests/integration/test_classify_thank_you_pack.py` — three fixture tests (kleins_hosted_us, reciprocal_coffee, coparent_pickup) + handler-direct safety-net test; 4 tests; all HTTP via `httpx.MockTransport`.
  - `bootstrap/pack_install_order.yaml` — NEW; single-entry list queued for prompt 15 / 16 install path.
  - `[§8]`/`[D6]`: zero LLM/embedding SDK imports; `verify_invariants.sh` clean.
  - `[§12.4]`: tenant-identity strings (stice-james, Klein, Mike) confined to `packs/skills/classify_thank_you_candidate/tests/fixtures/` and integration test fixture-construction sites with `# fixture:tenant_data:ok`.
  - `[ADR-0002]`: pack consumes `run_skill()` which POSTs to `/tools/invoke` with `tool: "llm-task"`. No new HTTP seams.
- **Carry-forward for prompt 10a (pipeline runner)**:
  - `bootstrap/pack_install_order.yaml` exists with one entry. Pipeline runner does not consume this file directly — that's prompt 15/16. 10a should reference packs by absolute path resolved via `InstanceConfig.packs_dir` (per UT-9 SOFT note).
- **Carry-forward for prompt 10b (thank_you pipeline)**:
  - The pipeline calls `await run_skill("classify_thank_you_candidate", inputs, ctx)` against this pack. Inputs match `schemas/input.schema.json`; outputs decoded per `schemas/output.schema.json`. The pipeline emits `commitment.proposed` (or equivalent) when `is_candidate=true`; that emit happens in pipeline code, not in skill code (skills never emit events directly per [REFERENCE_EXAMPLES.md §3 line 1527]).
- **Carry-forward for prompt 15 (OpenClaw integration)**:
  - `bootstrap/pack_install_order.yaml` is the source-of-truth list of built-in packs to register with OpenClaw. Prompt 15's persona-compiler / pack-registration path reads this file.
- **Carry-forward for prompt 16 (bootstrap wizard)**:
  - Bootstrap §6 / §7 walks `bootstrap/pack_install_order.yaml` and calls `openclaw skill install` per entry. Phase B only.
```

**Verification (Commit 4):**
```bash
poetry run ruff check packs/skills/classify_thank_you_candidate/ tests/integration/test_classify_thank_you_pack.py
poetry run mypy adminme/ packs/skills/classify_thank_you_candidate/handler.py tests/integration/test_classify_thank_you_pack.py 2>&1 | tail -10
poetry run pytest packs/skills/classify_thank_you_candidate/tests/ tests/integration/test_classify_thank_you_pack.py -v
poetry run python -c "
from pathlib import Path
from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack
invalidate_cache()
p = load_pack(Path('packs/skills/classify_thank_you_candidate'))
print(f'Pack: {p.pack_id} v{p.version}; handler={\"yes\" if p.handler_post_process else \"no\"}; provider_preferences={p.skill_frontmatter[\"provider_preferences\"]}')
"
grep "skill:classify_thank_you_candidate" bootstrap/pack_install_order.yaml
bash scripts/verify_invariants.sh
```

Expected: ruff clean, mypy clean (or only pre-existing notes), all 6 new tests pass (1 loader canary + 1 handler-direct in pack tests; 3 fixture + 1 handler-direct in integration), pack-loader python one-liner prints the manifest fields, grep matches one line, `verify_invariants.sh` exits 0.

`git add bootstrap/pack_install_order.yaml docs/build_log.md`
`git commit -m "phase 09b/4: pack_install_order + BUILD_LOG"`

`git push origin HEAD`

Then open the PR. Try `gh pr create` first; MCP fallback if `gh` is unavailable. PR title: `phase 09b: first canonical skill pack (classify_thank_you_candidate)`. PR body: copy the Commit 4 BUILD_LOG entry up through the Carry-forward bullets, leaving placeholders.

Then **stop** per the universal preamble's "Post-PR: one check, then stop" rule.

---

## Stop

> First canonical skill pack live. The `classify_thank_you_candidate` pack at v1.3.0 loads through `pack_loader.load_pack`, invokes via `run_skill`, post-processes through the urgency-coercion safety net, and validates against three fixture-driven integration tests using `httpx.MockTransport`. The pack is queued for Phase B install via `bootstrap/pack_install_order.yaml`. The pattern is established: every later skill pack — `classify_commitment_candidate` (10b), `extract_commitment_fields` (10b), `compose_morning_digest` (10c) — follows the same shape. Ready for prompt 10a (pipeline runner).
