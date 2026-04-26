# Prompt 09a: Skill runner wrapper (around OpenClaw)

**Phase:** BUILD.md "L4 CONTINUED: THE SKILL RUNNER (LAYERED ON OPENCLAW)" + ADR-0002.
**Depends on:** 08a (Session, scope) + 08b (governance, observation.outbound, system event registration patterns) merged. The skill-runner stub at `adminme/lib/skill_runner/wrapper.py` and the v2 `skill.call.recorded` schema stub at `adminme/events/schemas/domain.py` exist (landed in 02 / 04). This prompt fills them in. Pattern: **Introduction** — first AdministrateMe → OpenClaw HTTP seam.
**Estimated duration:** 2–3 hours.
**Stop condition:** `await run_skill("classify_test", {"text": "hi"}, ctx)` against a mocked OpenClaw `/tools/invoke` endpoint validates inputs, POSTs the correct `llm-task` body per ADR-0002, parses the response, and emits exactly one `skill.call.recorded` event. The full failure-mode test set (input invalid, sensitivity refused, scope insufficient, malformed response, timeout, handler raises, output validation fails, observation-mode short-circuit, large-input spillover) all pass. `bash scripts/verify_invariants.sh` exits 0.

---

## Read first (required)

Read these in order. Do NOT proceed without them.

1. `ADMINISTRATEME_BUILD.md` lines **1267–1290** — "L4 CONTINUED: THE SKILL RUNNER" section. The nine-step wrapper flow is the contract. (`sed -n '1267,1290p' ADMINISTRATEME_BUILD.md`)
2. `docs/adrs/0002-skill-runner-endpoint-correction.md` — full file. The endpoint-and-payload contract. ADR-0002 supersedes any other reference to `/skills/invoke` you encounter elsewhere.
3. `docs/reference/openclaw/gateway/tools-invoke-http-api.md` — full file. The HTTP envelope shape: 200 → `{ok: true, result}`, 400/401/404/429/500 → `{ok: false, error: {type, message}}`. Auth is `Authorization: Bearer <token>`.
4. `docs/reference/openclaw/tools/llm-task.md` — full file. The `llm-task` tool's params (`prompt`, `input`, `schema`, `provider`, `model`, `thinking`, `temperature`, `maxTokens`, `timeoutMs`) and that the result is `details.json` containing the parsed JSON.
5. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` lines **1030–1100** — pack shape (`pack.yaml`, `SKILL.md`, `schemas/`, `handler.py`). Read this much only; the file is 2938 lines. (`sed -n '1030,1100p' ADMINISTRATEME_REFERENCE_EXAMPLES.md`)
6. `adminme/lib/session.py` — Session dataclass (frozen; `tenant_id`, `auth_member_id`, `auth_role`, `view_member_id`, `correlation_id`, `allowed_scopes` property). The wrapper consumes a Session via `SkillContext`; do not invent fields it doesn't have.
7. `adminme/events/schemas/domain.py` lines **174–207** — existing `SkillCallRecordedV2` registration. The schema declares `input_tokens`/`output_tokens`/`cost_usd` as required non-negative numbers; ADR-0002 says these are `None` when llm-task doesn't expose them. **Resolution: this prompt's Commit 1 makes those three fields `int | None` / `float | None`. See Commit 1 below.**
8. `adminme/events/schemas/system.py` — pattern for system-event registration (see `xlsx.regenerated` v1). New events `skill.call.failed` v1 and `skill.call.suppressed` v1 follow the same shape.
9. `scripts/verify_invariants.sh` — the `ALLOWED_EMITS` regex. Commit 4 extends it to allow `skill.call.recorded`, `skill.call.failed`, `skill.call.suppressed` from the new emit file.
10. SYSTEM_INVARIANTS `[§7]` (event types register at v1 — exception path documented in `domain.py` module docstring), `[§8]` (no LLM/embedding SDKs in `adminme/`), `[D6]` (same).

If any of files 1–9 are missing or differ in shape from the above, **stop and report**. Do not paper over drift.

---

## Operating context

The skill-runner wrapper is the single seam between AdministrateMe Python code and OpenClaw's LLM substrate. Pipelines, surfaces, and (later) the bootstrap wizard call `await run_skill(skill_id, inputs, ctx)`. The wrapper:

- Validates inputs against the skill's `input.schema.json`.
- Enforces sensitivity policy — refuses privileged inputs unless the skill declares `sensitivity_required: privileged`.
- Enforces scope — `context_scopes_required` must be a subset of `ctx.session.allowed_scopes`.
- POSTs to OpenClaw's gateway (`POST http://127.0.0.1:18789/tools/invoke` with `tool: "llm-task"`) per ADR-0002. Iterates `provider_preferences` on transient failure (one POST per provider, in order).
- Runs optional `handler.py` `post_process` for Python-side shaping.
- Validates output against `output.schema.json`.
- Emits `skill.call.recorded` (or `skill.call.failed` / `skill.call.suppressed` on the corresponding paths) with full provenance.
- Returns the validated output.

The wrapper imports `httpx` (already in `pyproject.toml` per prompt 02) and `jsonschema` (add via Mypy preflight if not present). It does NOT import `anthropic`, `openai`, `sentence_transformers`, or any provider SDK — `[§8]`/`[D6]`. `verify_invariants.sh` enforces.

---

## Objective

Build:

- `adminme/lib/skill_runner/wrapper.py` — `run_skill()` + `SkillContext` + `SkillResult` + the four exceptions.
- `adminme/lib/skill_runner/pack_loader.py` — parses `pack.yaml` + `SKILL.md` frontmatter + both JSON schemas + imports `handler.py` if present. Caches by `(pack_id, version)`.
- `packs/skills/classify_test/` — dummy pack with trivial behavior, used only by the wrapper's tests. Full pack shape per REFERENCE_EXAMPLES.md §3.
- Schema additions to `adminme/events/schemas/system.py` (`skill.call.failed` v1, `skill.call.suppressed` v1) and field-type relaxation in `adminme/events/schemas/domain.py` (`SkillCallRecordedV2`) per ADR-0002's graceful-degradation clause.
- Test pyramid in `tests/unit/test_skill_wrapper.py` (mocked) + `tests/integration/test_skill_wrapper_live.py` (live, marked `requires_live_services`, skipped in Phase A).

---

## Out of scope

- Do NOT write canonical skill packs. `classify_thank_you_candidate` is 09b; `classify_commitment_candidate`, `extract_commitment_fields`, `compose_morning_digest` ship with their owning pipeline prompts (10b/10c).
- Do NOT install skills into a live OpenClaw — that's prompt 15. There is no OpenClaw gateway in the sandbox; tests mock the HTTP layer with `respx` (or `httpx.MockTransport`).
- Do NOT add provider SDK imports (`anthropic`, `openai`, etc.) anywhere under `adminme/`. `verify_invariants.sh` will fail Commit 4 if you do.
- Do NOT route `skill.call.recorded` through `guardedWrite` — the skill runner emits internal observability events, not actions. (Pipelines that consume skill output and then *write* domain state will route the write through `guardedWrite` separately.)
- Do NOT extend `ALLOWED_EMITS` in `verify_invariants.sh` to cover skill events emitted by anything other than `adminme/lib/skill_runner/wrapper.py`. The skill runner is the single seam — same single-seam pattern as xlsx daemon.

---

## Four-commit plan

### Commit 1 — schema relaxation + plumbing

**Files:**
- `adminme/events/schemas/domain.py` — change `SkillCallRecordedV2.input_tokens` and `output_tokens` to `int | None = None`, and `cost_usd` to `float | None = None`. Add a one-line comment citing `[ADR-0002]` and the graceful-degradation clause. The schema already lives at v2 (v1 reserved per existing module docstring); this is a field-relaxation, not a new version. Justification: `SkillCallRecordedV2` has never been emitted on main — it was registered as a stub awaiting 09a. No upcaster needed.
- `adminme/events/schemas/system.py` — add `SkillCallFailedV1` (fields: `skill_name`, `skill_version`, `failure_class` enum `{"input_invalid","sensitivity_refused","scope_insufficient","openclaw_unreachable","openclaw_timeout","openclaw_malformed_response","handler_raised","output_invalid"}`, `error_detail: str`, `correlation_id: str`, `provider_attempted: str | None`, `duration_ms_until_failure: int | None`) and `SkillCallSuppressedV1` (fields: `skill_name`, `skill_version`, `reason: Literal["observation_mode_active","dry_run"]`, `would_have_sent: dict`, `correlation_id: str`). Register both at v1.
- `adminme/lib/skill_runner/__init__.py` — export `run_skill`, `SkillContext`, `SkillResult`, the exception classes.
- `tests/unit/test_event_schemas.py` (extend) — three new tests: round-trip `SkillCallRecordedV2` with `None` token/cost fields; round-trip `SkillCallFailedV1`; round-trip `SkillCallSuppressedV1`.

**Verification (this commit):**
```bash
poetry run pytest tests/unit/test_event_schemas.py -v
poetry run mypy adminme/lib/skill_runner/ adminme/events/schemas/ 2>&1 | tail -10
```

`git commit -m "phase 09a/1: skill schemas + None-tolerant tokens per ADR-0002"`

### Commit 2 — pack_loader

**Files:**
- `adminme/lib/skill_runner/pack_loader.py` — parses `pack.yaml` (PyYAML — already a dep), `SKILL.md` frontmatter (single-line keys per cheatsheet Q5; reuse a small frontmatter parser or inline ~20 lines of YAML-block extraction), validates both JSON schemas with `jsonschema` library's `Draft202012Validator.check_schema()`, imports `handler.py` via `importlib` if present and asserts `post_process(raw, inputs, ctx)` exists. Returns a `LoadedPack` dataclass: `pack_id`, `version`, `manifest`, `input_schema`, `output_schema`, `prompt_template`, `handler_post_process` (callable or `None`), `pack_root` (Path). Cache by `(pack_id, version)` in module-level dict; `pack_loader.invalidate_cache()` for tests.
- `packs/skills/classify_test/pack.yaml` + `SKILL.md` + `schemas/input.schema.json` + `schemas/output.schema.json` + `prompt.jinja2` + (no `handler.py` — keep it minimal). Trivial behavior: input `{text: str}`, output `{is_thing: bool, confidence: float}`. The `prompt.jinja2` is a one-liner: `Classify whether '{{ input.text }}' is a thing. Return JSON with is_thing (bool) and confidence (0..1).`. The output schema enforces `is_thing: bool`, `confidence: number with minimum 0 and maximum 1`.
- `tests/unit/test_pack_loader.py` — load classify_test pack; assert manifest fields; assert schemas validate trivial sample objects; assert no handler is loaded; cache hit on second load returns the same `LoadedPack` instance; cache miss after `invalidate_cache()`. Also: a malformed-pack-yaml fixture (corrupted pack) → loader raises `PackLoadError`. Minimum 6 tests.

**Verification:**
```bash
poetry run pytest tests/unit/test_pack_loader.py -v
```

`git commit -m "phase 09a/2: skill pack_loader + classify_test pack"`

### Commit 3 — wrapper

**Files:**
- `adminme/lib/skill_runner/wrapper.py` — fully implements the nine-step flow per BUILD.md L4 + ADR-0002.

  Public surface:
  ```python
  @dataclass(frozen=True)
  class SkillContext:
      session: Session                  # carries tenant_id, auth_member, scopes, source
      correlation_id: str | None = None # overrides session.correlation_id when set
      observation_mode_active: bool = False  # caller passes 08b's ObservationManager.is_active()
      dry_run: bool = False             # test override; never set in production callers

  @dataclass(frozen=True)
  class SkillResult:
      output: dict                      # validated JSON output (Pydantic-equivalent dict, not BaseModel)
      openclaw_invocation_id: str | None  # may be absent in response envelope; ADR-0002 graceful
      provider: str                     # the provider_preferences[i] that succeeded
      input_tokens: int | None
      output_tokens: int | None
      cost_usd: float | None
      duration_ms: int

  class SkillInputInvalid(Exception): ...
  class SkillSensitivityRefused(Exception): ...
  class SkillScopeInsufficient(Exception): ...
  class OpenClawResponseMalformed(Exception): ...

  async def run_skill(skill_id: str, inputs: dict, ctx: SkillContext) -> SkillResult: ...
  ```

  Behavior (steps numbered to match BUILD.md L4 lines 1267–1290):
  1. Load pack via `pack_loader`.
  2. Validate `inputs` against `input.schema.json`. On failure → emit `skill.call.failed` v1 with `failure_class="input_invalid"`, raise `SkillInputInvalid`. Note: per the universal preamble's failure-mode-handler-direct discipline, the test that asserts this branch calls `run_skill` directly and asserts the exception + the emitted event; do not route through bus.
  3. Sensitivity check. If any input value, when paired with caller-provided sensitivity metadata (a per-input dict or absent → assume `normal`), is `privileged` and the manifest's `sensitivity_required` is not `privileged` → emit `skill.call.failed` v1 with `failure_class="sensitivity_refused"`, raise `SkillSensitivityRefused`. **Inputs do not auto-detect privileged content; the caller must annotate** (per `[§13]` privileged content discipline — only the source adapter knows). For 09a, the caller-side annotation API is: `inputs[<field>]` carries the value; an optional second argument `input_sensitivities: dict[str, str] | None = None` on `run_skill` carries the per-field sensitivity. Default `None` means all inputs are `normal`. Add this argument to the public API. (Update `SkillContext` no further; sensitivity is per-call, not per-context.)
  4. Scope check. For each scope in manifest's `context_scopes_required`, assert it appears in `ctx.session.allowed_scopes`. Failure → emit `skill.call.failed` v1 with `failure_class="scope_insufficient"`, raise `SkillScopeInsufficient`. Note: `context_scopes_required` in the manifest is a list of strings like `interactions:read`; for 09a's `classify_test` pack, leave the list empty so no scope check fires in the dummy pack. The wrapper's scope-check logic is exercised by a test pack fixture in `tests/unit/fixtures/scope_required_pack/` that requires `private:*`.
  5. **Observation / dry-run short-circuit.** If `ctx.dry_run` OR (`ctx.observation_mode_active` AND `manifest.outbound_affecting is True`) → emit `skill.call.suppressed` v1 with `reason="dry_run"` or `reason="observation_mode_active"`, return a `SkillResult` carrying a manifest-declared `defensive_default` output (per skill manifest's `on_failure` policy; if absent, return a result with `output={}`, all metric fields `None`, `provider="suppressed"`, `duration_ms=0`). The `classify_test` pack does NOT declare `outbound_affecting: true` and does NOT have a `defensive_default`, so dry-run from this pack returns `output={}`.
  6. **POST to OpenClaw.** Iterate `manifest.provider_preferences`. For each `provider/model` pair: build the body per ADR-0002 (verbatim shape from the ADR); `httpx.AsyncClient` POST to `http://127.0.0.1:18789/tools/invoke` with `Authorization: Bearer <token>` from `ctx.session` *or* env var `OPENCLAW_GATEWAY_TOKEN` (fall back to env in 09a; bootstrap wizard wires it from 1Password in prompt 16). Timeout = `manifest.timeout_seconds * 1000` ms passed as `args.timeoutMs`; the httpx client uses a slightly longer wall-clock timeout (`+ 2s`). If the call returns 200 → break loop with `response.json()["result"]`. If transient (5xx, network) → log and try next provider. If 4xx → no retry, this is a deterministic refusal. If list exhausted → emit `skill.call.failed` v1 with `failure_class="openclaw_unreachable"` or `"openclaw_timeout"`, raise the corresponding exception. Per ADR-0002, exact-shape mismatch (response is 200 but envelope is wrong) → emit `skill.call.failed` with `failure_class="openclaw_malformed_response"`, raise `OpenClawResponseMalformed` (recoverable per diagnostic d02).
  7. Optional `handler.post_process(raw_response, inputs, ctx)` if loaded. If raises → log raw response to `<instance>/raw_events/skill_validation_failures/<event_id>.json` (path via `InstanceConfig.raw_events_dir / "skill_validation_failures"` per `[§15]` — never hardcode the path), emit `skill.call.failed` v1 with `failure_class="handler_raised"`, return defensive default per manifest `on_failure` policy (raise if no policy).
  8. Validate output against `output.schema.json` with `jsonschema`. Failure → same path as #7 but `failure_class="output_invalid"`.
  9. **Emit `skill.call.recorded` v2** via the EventLog (`event_log.append(...)`). Inputs >50KB get spilled to `<instance>/raw_events/skill_large_inputs/<event_id>.json` and the event payload's `inputs` becomes `{"_spilled_to": "<path>"}`. Emit shape uses `EventEnvelope.now_utc_iso()` for timestamps; `actor_identity = ctx.session.auth_member_id`; `correlation_id = ctx.correlation_id or ctx.session.correlation_id`; `causation_id` is the caller's responsibility (None default — pipelines wire causation in 10a). Return `SkillResult`.

  The wrapper does NOT call `EventLog.append` directly for the `failed` and `suppressed` events — it builds a tiny internal helper `_emit_failed(...)` and `_emit_suppressed(...)` to keep the logic tight. The path through `event_log.append` is the same; just one helper per event type so each emit site is one line.

  **`event_log` source.** The wrapper does not construct an `EventLog`; it receives one via dependency injection at the module level (the same pattern 08b uses for `outbound`). Add a module-level `_DEFAULT_EVENT_LOG: EventLog | None = None`; expose `set_default_event_log(log)` for production wire-up. Tests inject a fixture `EventLog` into a per-test wrapper instance.

- `tests/unit/test_skill_wrapper.py` — minimum 14 tests:
  1. Happy path against mocked `/tools/invoke`: validates input, POSTs correct body shape (assert URL, headers, JSON body matches ADR-0002), parses response, emits exactly one `skill.call.recorded`, returns `SkillResult`.
  2. Input validation fails → `SkillInputInvalid` raised, no HTTP call made (assert `respx` recorded zero requests), one `skill.call.failed` with `failure_class="input_invalid"`.
  3. Sensitivity refusal: classify_test pack + privileged-annotated input → `SkillSensitivityRefused`, no HTTP call, `failure_class="sensitivity_refused"`.
  4. Scope refusal: scope-required test pack fixture + session lacking `private:*` → `SkillScopeInsufficient`, no HTTP call, `failure_class="scope_insufficient"`.
  5. OpenClaw 500 on first provider, 200 on second → succeeds, provider field reflects second provider.
  6. OpenClaw 500 on all providers → `failure_class="openclaw_unreachable"`.
  7. OpenClaw response shape malformed (200 but no `ok` field) → `OpenClawResponseMalformed`, `failure_class="openclaw_malformed_response"`.
  8. OpenClaw timeout → `failure_class="openclaw_timeout"`, exception raised.
  9. Handler raises → `failure_class="handler_raised"`, raw response saved to `raw_events/skill_validation_failures/`, defensive default returned (use a test pack with `on_failure: {is_thing: false, confidence: 0.0}`).
  10. Output schema validation fails → `failure_class="output_invalid"`, same path as #9.
  11. `ctx.observation_mode_active=True` + outbound-affecting test pack → `skill.call.suppressed` emitted, `output={}` returned (or defensive default if pack declares one), no HTTP call.
  12. `ctx.dry_run=True` → same as #11 with `reason="dry_run"`.
  13. Inputs >50KB → spilled to `raw_events/skill_large_inputs/`, event payload `inputs` field is `{"_spilled_to": "<abs path>"}`.
  14. Token/cost graceful degradation: response without `tokens_in` → `skill.call.recorded` emitted with `input_tokens=None`; ADR-0002 cited in test docstring.

  Use `respx` to mock `httpx.AsyncClient` — already standard in the codebase per 07c-β tests. If `respx` is missing from `pyproject.toml`, add it via Mypy preflight (universal preamble).

  Async-subscriber discipline (universal preamble): for the "exactly one event emitted" assertions, follow the failure-mode-handler-direct pattern — the wrapper calls `event_log.append()` synchronously from the test's perspective (asyncio.to_thread); tests assert on the in-memory log directly, not via a subscriber. No `notify` / `_wait_for_checkpoint` needed for 09a tests.

- `tests/integration/test_skill_wrapper_live.py` — single test, marked `@pytest.mark.requires_live_services`, skipped in Phase A. Documented contract: post-bootstrap (Phase B), invokes the real `classify_test` pack against a live OpenClaw gateway, asserts `skill.call.recorded` lands.

**Verification:**
```bash
poetry run pytest tests/unit/test_skill_wrapper.py -v
poetry run pytest tests/integration/test_skill_wrapper_live.py -v -m "not requires_live_services" || echo "(skipped; Phase B)"
```

`git commit -m "phase 09a/3: skill wrapper run_skill + 14 mocked tests"`

### Commit 4 — verify, BUILD_LOG, push

**Files:**
- `scripts/verify_invariants.sh` — extend `ALLOWED_EMITS` regex to include `skill\.call\.recorded|skill\.call\.failed|skill\.call\.suppressed`, **scoped to file `adminme/lib/skill_runner/wrapper.py` only** (same single-seam pattern as the xlsx allowlist). The script's existing structure already supports per-file allowlisting; mirror the pattern from the xlsx block. If the script's structure does not in fact support per-file allowlisting, document the gap in a one-line comment in the script and rely on the test in `test_skill_wrapper.py` to enforce the seam directly — same fallback 08b chose for `external.sent` per PM-17. Either path is acceptable; do not block on the script if the structure isn't there.
- `docs/build_log.md` — append the entry per the canonical template in `docs/qc_rubric.md`. Placeholders: `PR #<N>`, `<commit4>`, `<merge date>`, `Outcome: IN FLIGHT (PR open)`. Partner fills these in post-merge during the next session's Job 1 housekeeping (UT-5).

**BUILD_LOG entry template (paste verbatim, fill the bracketed evidence lines):**
```markdown
### Prompt 09a — skill runner wrapper (around OpenClaw)
- **Refactored**: by Partner in Claude Chat, 2026-04-26. Prompt file: prompts/09a-skill-runner.md (~250 lines, quality bar = 08b).
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - `adminme/lib/skill_runner/wrapper.py` — `run_skill()` 9-step flow per BUILD.md L4 + ADR-0002; provider-preference fallback iterates inside wrapper, single POST per provider attempt.
  - `adminme/lib/skill_runner/pack_loader.py` — parses pack.yaml + SKILL.md + JSON schemas; cache by `(pack_id, version)`.
  - `packs/skills/classify_test/` — full pack scaffold for tests; trivial classifier.
  - `adminme/events/schemas/domain.py` — `SkillCallRecordedV2` token/cost fields relaxed to Optional per `[ADR-0002]` graceful-degradation clause.
  - `adminme/events/schemas/system.py` — `SkillCallFailedV1` and `SkillCallSuppressedV1` registered at v1.
  - `scripts/verify_invariants.sh` — `ALLOWED_EMITS` extended for skill.call.* (or test-side enforcement noted if script structure required).
  - 14 unit tests (`tests/unit/test_skill_wrapper.py`) + 6 pack-loader tests (`tests/unit/test_pack_loader.py`) + 3 schema tests + 1 live test stub (skipped Phase A).
  - `[§7]`/`[D7]`: `skill.call.failed` and `skill.call.suppressed` registered at v1.
  - `[§8]`/`[D6]`: zero new SDK imports; `verify_invariants.sh` clean.
  - `[ADR-0002]`: wrapper POSTs `/tools/invoke` with `tool: "llm-task"`; provider iteration in wrapper, not in OpenClaw.
- **Carry-forward for prompt 09b**:
  - `pack_loader` accepts the canonical pack shape; `classify_thank_you_candidate` will be the second pack to load through it.
  - `run_skill` is stable; 09b just supplies a pack and asserts the round-trip.
- **Carry-forward for prompt 10a**:
  - Pipelines call `run_skill(skill_id, inputs, SkillContext(session=..., correlation_id=...))`. Causation wiring (set causation_id on `skill.call.recorded` to the triggering domain event) lands in pipeline runner.
- **Carry-forward for prompt 16 (bootstrap)**:
  - Bootstrap §6 wires `OPENCLAW_GATEWAY_TOKEN` from 1Password; 09a falls back to env for now.
  - Bootstrap §7 calls `set_default_event_log(...)` at service start.
```

**Verification (Commit 4):**
```bash
poetry run ruff check adminme/lib/skill_runner/ adminme/events/schemas/ tests/unit/test_skill_wrapper.py tests/unit/test_pack_loader.py
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/test_skill_wrapper.py tests/unit/test_pack_loader.py tests/unit/test_event_schemas.py -v
bash scripts/verify_invariants.sh
```

Expected: ruff clean, mypy clean (or only pre-existing notes), all new tests pass, `verify_invariants.sh` exits 0.

`git commit -m "phase 09a/4: verify_invariants extension + BUILD_LOG"`

`git push origin HEAD`

Then open the PR. Try `gh pr create` first; MCP fallback if `gh` is unavailable. PR title: `phase 09a: skill runner wrapper + ADR-0002 wiring`. PR body: copy the Commit 4 BUILD_LOG entry up through the Carry-forward bullets, leaving placeholders.

Then **stop** per the universal preamble's "Post-PR: one check, then stop" rule.

---

## Stop

> Skill runner wrapper landed. AdministrateMe can invoke OpenClaw skills via `/tools/invoke` with `tool: "llm-task"` (per ADR-0002), full validation, provenance recording, observation/dry-run short-circuit, and graceful degradation when the response envelope omits cost/token fields. The `classify_test` dummy pack proves the loop end-to-end against a mocked gateway. Ready for prompt 09b (canonical `classify_thank_you_candidate` pack as the first real consumer).
