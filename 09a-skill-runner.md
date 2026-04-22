# Prompt 09a: Skill runner wrapper (around OpenClaw)

**Phase:** BUILD.md "L4 CONTINUED: THE SKILL RUNNER (LAYERED ON OPENCLAW)".
**Depends on:** Prompt 08. Session, scope, observation are in.
**Estimated duration:** 2-3 hours.
**Stop condition:** A `run_skill()` call successfully invokes OpenClaw's skill API, validates input/output, emits `skill.call.recorded`, and returns the parsed output. A dummy skill pack loads and runs end-to-end.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` **"L4 CONTINUED: THE SKILL RUNNER (LAYERED ON OPENCLAW)"** — the nine-step wrapper flow is the contract.
2. `docs/openclaw-cheatsheet.md` question 1 (skill install) and question 5 (SKILL.md shape) — your own from prompt 01.
3. `docs/reference/openclaw/` — specifically any file covering `/skills/invoke`, skill manifest shape, and the provider abstraction. These were mirrored in prompt 00.5. If a file you need is missing, see `docs/reference/_status.md` — the mirror may be incomplete and need operator clipping before proceeding.
4. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §3 (the full thank-you skill pack, including `pack.yaml`, `SKILL.md`, `handler.py`, schemas, tests). This is the shape the wrapper must support.

## Operating context

The wrapper is a thin Python module that stands between AdministrateMe pipelines (callers) and OpenClaw's skill runner (the LLM substrate). Its responsibilities:
1. Validate inputs before sending.
2. Enforce sensitivity policy (refuse privileged inputs for skills not declared privileged).
3. Enforce scope (skill's required scopes must be subset of caller's session scopes).
4. POST to OpenClaw `/skills/invoke`.
5. Run `handler.py` post-process if present.
6. Validate output.
7. Emit `skill.call.recorded` event with full provenance.
8. Return to caller.

The wrapper does NOT call LLM providers directly. Ever. OpenClaw owns that. If OpenClaw is unreachable, the wrapper fails loudly.

## Objective

Build `platform/lib/skill_runner/wrapper.py` + supporting modules. Plus a dummy skill pack `packs/skills/classify_test/` used for testing.

## Out of scope

- Do NOT write the canonical skill packs yet (classify_thank_you_candidate is prompt 09b; classify_commitment_candidate, extract_commitment_fields, compose_morning_digest, etc. come in their owning pipeline prompts, 10b/10c).
- Do NOT install skills into OpenClaw — that's prompt 15. For this prompt, either (a) mock the OpenClaw HTTP call for tests, or (b) expect the operator has a local OpenClaw reachable and a test skill already installed via manual command. Prefer (a) for CI-friendliness; (b) for lab verification.

## Deliverables

### `platform/lib/skill_runner/wrapper.py`

```python
async def run_skill(
    skill_id: str,
    inputs: dict,
    ctx: SkillContext,
) -> SkillResult:
    """Nine-step flow per BUILD.md L4 Skill Runner."""

@dataclass
class SkillContext:
    session: Session
    correlation_id: str
    tenant_id: str
    # extensions
    observation_mode_active: bool = False
    dry_run: bool = False

@dataclass
class SkillResult:
    output: BaseModel               # validated Pydantic instance
    openclaw_invocation_id: str
    provider: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int
```

Behavior:

1. Load the skill pack from `~/.adminme/packs/skills/<skill_id>/` or `packs/skills/<skill_id>/` for built-ins. Cache the manifest.
2. Validate `inputs` against the skill's input schema. Fail → raise `SkillInputInvalid`.
3. Sensitivity check. If any input value has metadata indicating privileged content and the skill doesn't declare `sensitivity_required: privileged`, raise `SkillSensitivityRefused`.
4. Scope check: for each scope in `context_scopes_required`, check `session.allowed_scopes`. Fail → raise `SkillScopeInsufficient`.
5. If `ctx.dry_run` or `ctx.observation_mode_active AND skill is outbound-affecting`: short-circuit and emit `skill.call.suppressed` instead. Return a stub result. (Most skills are read-only; this only applies when the skill's metadata declares outbound impact.)
6. POST to OpenClaw: `http://127.0.0.1:18789/skills/invoke` with JSON body `{skill_name, skill_version, inputs, correlation_id, session_context: {auth_member_id, dm_scope, tenant_id}}`. Use `httpx` async client. Timeout = manifest's `timeout_seconds`.
7. Response shape (verify against openclaw cheatsheet): `{invocation_id, raw_response, provider, tokens_in, tokens_out, cost_usd, duration_ms}`. If shape differs, raise `OpenClawResponseMalformed` with what was received (this is recoverable — consult diagnostic d02).
8. Load handler.py if present; call `post_process(raw_response, inputs, ctx)`. If handler raises, log full response to `~/.adminme/raw_events/skill_validation_failures/<ts>.json`, return a fallback value per the skill's `on_failure` policy, also emit `skill.call.failed`.
9. Validate output against output.schema.json. Same failure path as #8.
10. Emit `skill.call.recorded` event with all fields from `SkillResult` plus the full inputs (size-capped — if inputs >50KB, store inputs in `~/.adminme/raw_events/skill_large_inputs/<event_id>.json` and put the path in the event payload instead).
11. Return SkillResult.

### `platform/lib/skill_runner/pack_loader.py`

Parses `pack.yaml`, `SKILL.md` frontmatter, both JSON schemas, imports `handler.py` if present. Caches by `(pack_id, version)`.

### `packs/skills/classify_test/` (dummy pack for tests)

Full pack per REFERENCE_EXAMPLES.md §3 shape, but trivial logic — a classifier that returns `{is_thing: true, confidence: 0.7}` unconditionally. Input schema: `{text: str}`. Output schema: `{is_thing: bool, confidence: float}`. Used only for unit tests.

### Schema

Add `skill.call.recorded` v2 (per prompt 04 plan — stub exists; this prompt fills in fields) and `skill.call.failed` v1 and `skill.call.suppressed` v1 to `platform/events/schemas/`.

### Tests

**Live install is a Phase B concern.** In this prompt (Phase A), you validate that the pack structure matches what OpenClaw expects per `docs/reference/openclaw/` — correct directory layout, manifest fields present, schemas valid JSON, handler.py importable. You do NOT run `openclaw skill install` — there is no OpenClaw gateway in your sandbox. The live install happens during the Mac Mini bootstrap.

For Phase A integration tests, mock the OpenClaw HTTP call. Test fixture responses exercise the wrapper's parsing and error paths without needing a live gateway.

`tests/unit/test_skill_wrapper.py` — mocking OpenClaw HTTP responses:
- Happy path: valid input → mock response → validated output → event emitted.
- Input validation fails → `SkillInputInvalid` raised, no OpenClaw call made.
- Sensitivity refusal: privileged input to non-privileged skill → `SkillSensitivityRefused`, no OpenClaw call.
- Scope refusal: session missing required scope → `SkillScopeInsufficient`, no OpenClaw call.
- OpenClaw returns malformed shape → `OpenClawResponseMalformed` raised, failure event emitted.
- OpenClaw timeout → exception raised, failure event emitted with timeout classification.
- Handler raises → fallback value returned, failure event emitted, raw response saved.
- Output validation fails → fallback value returned, failure event emitted.
- Observation mode active + outbound-affecting skill → suppressed event emitted, fallback returned.
- Large inputs (>50KB) → stored to raw_events folder, event payload references the path.

`tests/integration/test_skill_wrapper_live.py` — marked `@pytest.mark.requires_live_services`, skipped by default in Phase A. Runs during Phase B after bootstrap to verify the real OpenClaw gateway responds correctly:
- Precondition: classify_test pack installed into OpenClaw (happens during bootstrap pack-install phase).
- Call `run_skill("classify_test", {"text": "hello"}, ctx)`.
- Assert output is `{is_thing: true, confidence: 0.7}`.
- Assert `skill.call.recorded` is in event log.

## Verification

```bash
poetry run pytest tests/unit/test_skill_wrapper.py -v
# Phase A skips live tests:
poetry run pytest tests/integration/test_skill_wrapper_live.py -v -m "not requires_live_services" || echo "(skipped; Phase B)"

git add platform/lib/skill_runner/ packs/skills/classify_test/ platform/events/schemas/skill_events.py tests/
git commit -m "phase 09a: skill runner wrapper"
git push
```

## Stop

**Explicit stop message:**

> Skill runner wrapper in. AdministrateMe can invoke OpenClaw skills with full validation, provenance, and event recording. Dummy pack proves the loop end-to-end. Ready for prompt 09b (canonical classify_thank_you_candidate pack).
