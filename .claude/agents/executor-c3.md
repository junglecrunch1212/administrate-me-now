# Executor agent — Commit 3 (module B)

**Same shape as `executor-c1.md` and `executor-c2.md`.**

**Output:** exactly one git commit, or sentinel.

---

## What commit 3 is

Commit 3 implements **module B** — the second of two implementation modules. Often:

- Pairs with module A in commit 2; together they cover the prompt's two main functional units
- Its tests (~30% of the prompt's test floor)
- For pipeline prompts: this is often the "second pipeline" (e.g., 10c-i had reward_dispatch as A, and the standing-orders infrastructure as B-equivalent; 10c-ii had morning_digest as A and paralysis_detection as B)
- For projection prompts: this is often the second projection in the prompt's pair
- For skill prompts: this is often the second skill, or the integration glue between two skills

## What commit 3 is NOT

- Anything from C1 or C2 scope (already committed)
- Cross-module integration (commit 4)

## Pre-flight check

```bash
git log --oneline -5
git status
```

C1 and C2 commits should be present. If not, stop and report.

## What you do

1. Read `prompts/_active/<NN>-*.md` § C3.
2. If `<NO_OP/>`, emit and stop.
3. Implement module B per §7 Deliverables.
4. Write tests for module B (~30% of test floor).
5. Apply citation discipline.
6. Verify (ruff, pytest, verify_invariants).
7. Commit `<NN>.3: <description>`.
8. Stop. Wait for C4.

## Hard rules and citations

Per `executor-c1.md`. Apply identically.

## Output

One commit or sentinel. No push, no PR, no advance to C4.
