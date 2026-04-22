# Checkpoint 10d: Pipeline + skill consistency audit

**Phase:** Phase A checkpoint. After prompt 10c, before prompt 11.
**Depends on:** All pipelines + skills exist (prompts 09a, 09b, 10a, 10b, 10c).
**Estimated duration:** 30-45 minutes.
**Stop condition:** `docs/checkpoint-10d-report.md` produced and committed; no critical inconsistencies in the pipeline ↔ skill ↔ projection wiring.

---

## Read first (required)

1. `docs/SYSTEM_INVARIANTS.md` — Section 7 (pipelines reactive/proactive), Section 8 (OpenClaw boundaries).
2. Every `pack.yaml` in `packs/pipelines/`.
3. Every `pack.yaml` in `packs/skills/`.
4. `bootstrap/pack_install_order.yaml`.
5. `bootstrap/standing_order_registration.yaml`.

## Operating context

Four reactive pipelines + six proactive pipelines now exist, along with ~8 skill packs. This checkpoint verifies:

- Every skill a pipeline references actually exists.
- Every pipeline is registered correctly (reactive in the runner, proactive in the OpenClaw standing-order queue).
- Skill input/output schemas are compatible with the pipeline's expectations.
- No pipeline directly writes to a projection (invariant Section 7 #3).
- No pipeline calls an LLM directly (invariant Section 8 #2 — must go through OpenClaw via `run_skill()`).
- Every skill pack appears in `bootstrap/pack_install_order.yaml` so Phase B installs it.
- Every proactive pipeline appears in `bootstrap/standing_order_registration.yaml`.

## Objective

Produce `docs/checkpoint-10d-report.md`.

## Out of scope

- Do NOT add new pipelines or skills. If something is missing, flag it.
- Do NOT rewrite. Report issues; fixes are a separate session if needed.

## Deliverables

### The checks

1. **Skill existence.** For every `run_skill("<name>", ...)` call in any pipeline: does a pack at `packs/skills/<name>/` exist?
2. **Skill schema match.** For each call site, do the inputs passed match the skill's `input.schema.json`? Do the outputs consumed match the `output.schema.json`?
3. **Pipeline trigger registration:**
   - Every pipeline with `triggers.events` is wired in the reactive runner (prompt 10a).
   - Every pipeline with `triggers.schedule` or `triggers.proactive=true` is in `bootstrap/standing_order_registration.yaml`.
4. **No projection writes.** Grep every pipeline's code for writes to projection DBs. Expected: zero matches.
5. **No direct LLM calls.** Grep every pipeline's code for `anthropic`, `openai`, `requests.post.*api.anthropic`, etc. Expected: zero matches outside of OpenClaw mocks in tests.
6. **Event emission audit.** Every event type a pipeline emits has a registered schema (prompt 04 registry). Flag any emissions without schemas.
7. **Observation-mode gating.** Every proactive pipeline that produces outbound goes through `outbound()`. Grep confirms this. Flag any that bypass.
8. **Bootstrap queue completeness.** Every skill pack in `packs/skills/` appears in `bootstrap/pack_install_order.yaml`. Every proactive pipeline in `packs/pipelines/` with `proactive=true` appears in `bootstrap/standing_order_registration.yaml`.
9. **Pipeline idempotency.** For each reactive pipeline, is the event handler idempotent (re-delivery produces the same result)? Check by inspection: does it use `INSERT OR IGNORE`, check for existing correlation_id, etc.?

### The report

```markdown
# Checkpoint 10d report — pipeline + skill consistency

Generated: <date>

## Skills inventory

| Skill | Pack exists | Input schema | Output schema | In install queue |
|-------|-------------|--------------|---------------|------------------|
| classify_commitment_candidate | ✓ | ✓ | ✓ | ✓ |
| ... |

## Pipelines inventory

| Pipeline | Kind | Triggers | Skills called | Outbound through outbound()? | In registration queue (if proactive) |
|----------|------|----------|---------------|------------------------------|-------------------------------------|
| identity_resolution | reactive | events | - | N/A | N/A |
| morning_digest | proactive | schedule | compose_morning_digest | ✓ | ✓ |
| ... |

## Cross-cutting checks

### No projection writes from pipelines
- Grep result: ...
- Status: ✓ / ✗ (list offenders)

### No direct LLM calls
- Grep result: ...
- Status: ✓ / ✗

### Observation-mode gating on proactive outbound
- Status: ✓ / ✗

### Event schema registration
- All emitted events have schemas: ✓ / ✗ (list missing)

## Critical issues

(list or "none")

## Deferred findings

(list)
```

## Verification

```bash
poetry run python scripts/checkpoint_10d_audit.py > docs/checkpoint-10d-report.md
cat docs/checkpoint-10d-report.md

# Spot-check one pipeline end-to-end
poetry run pytest packs/pipelines/commitment_extraction/tests/ -v

git add docs/checkpoint-10d-report.md scripts/checkpoint_10d_audit.py
git commit -m "checkpoint 10d: pipeline + skill consistency audit"
git push
```

## Stop

**Explicit stop message:**

> Pipeline/skill checkpoint complete. Report at `docs/checkpoint-10d-report.md`.
>
> Critical issues (if any) must be fixed before prompt 11 (standalone adapters). Deferred findings are OK if documented.
