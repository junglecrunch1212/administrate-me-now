# Executor agent — Commit 2 (module A)

**Same shape as `executor-c1.md`. Read that first; only the C2-specific differences are below.**

**v4 invocation** (per RUNBOOK-EXECUTOR.md):
```bash
git checkout claude/<PROMPT_ID>-c1     # branch from c1 per stacked BRANCH-STRATEGY.md
git checkout -b claude/<PROMPT_ID>-c2

claude --print --model "${ADMINME_MODEL_EXECUTOR:-opus}" --max-budget-usd 40 \
       --permission-mode bypassPermissions \
       '<c2 work instruction>' < /dev/null 2>&1 | tee /tmp/c2.log
```

**Output:** one git commit + push + PR (PR's `base` = `claude/<PROMPT_ID>-c1`) — OR `<NO_OP/>` / `<COMMIT_FAILED/>` / `<NEEDS_AMENDMENT/>`.

**Commit message format (v4 mandatory):** `phase <PROMPT_ID> commit 2: <imperative summary, <72 chars>`. Example: `phase 10c-iii commit 2: 3 compose_* skill packs at v1.0.0`.

---

## What commit 2 is

Commit 2 implements **module A** of the prompt's work — the first of two implementation modules. The pattern across the supervised build:

- The first major module's handler/runner/projection-builder/pipeline code
- Its public interface (the contracts other modules use)
- Its unit tests (≥30% of the prompt's test floor)

## What commit 2 is NOT

- Anything from C1's scope (schema/plumbing); that's already committed in C1
- Module B logic (commit 3)
- Cross-module integration (commit 4)

## Pre-flight check

Before starting, run:

```bash
git log --oneline -5     # confirm C1 commit is present on this branch
git status               # confirm clean working tree
```

If C1 is missing, the operator either didn't run C1 yet or it failed silently. Stop and report.

## What you do

1. Read `prompts/_active/<NN>-*.md`. Locate §8 C2 block. Read it.
2. Read the constitutional docs cited in §2 Read first that apply specifically to C2's scope.
3. If C2 says `<NO_OP/>`, emit `<NO_OP/>` and stop.
4. Otherwise, implement module A. Files typically under `adminme/<area>/<module>.py`, `packs/pipelines/<pipeline>/handler.py`, `packs/skills/<skill>/handler.py`, etc., per the prompt's §7 Deliverables.
5. Write the tests for module A under the corresponding `tests/unit/` or `packs/<kind>/<name>/tests/` directory.
6. Match the test floor from §7.
7. Apply citation discipline as in C1 (one-line `# [§N, DN]` comments on code touching invariants).
8. Verify per §9 