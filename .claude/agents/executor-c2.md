# Executor agent — Commit 2 (module A)

**Same shape as `executor-c1.md`. Read that first; only the C2-specific differences are below.**

**Output:** exactly one git commit on the working branch — OR `<NO_OP/>` / `<COMMIT_FAILED/>` / `<NEEDS_AMENDMENT/>`.

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
8. Verify per §9 of the refactored prompt — typically: ruff clean, pytest on the new tests passes, `verify_invariants.sh` exits 0.
9. Commit with message `<NN>.2: <short description>`. Amend if verification fails after a fix attempt.
10. Stop. Do not advance to C3.

## Same hard rules apply

See `executor-c1.md` § Hard rules. They apply identically here.

## Same citation discipline

See `executor-c1.md` § Citation discipline. Apply throughout.

## Output

Same as C1 — exactly one commit, or one of the no-op/failed/amendment-needed sentinels. No push, no PR, no advance to C3.
