# Executor agent — Commit 4 (integration + BUILD_LOG + PR open)

**Same shape as `executor-c1.md` through `executor-c3.md`.**

**Output:** one git commit, then `git push` and `gh pr create`. The end of the four-commit cycle.

---

## What commit 4 is

Commit 4 closes the prompt:

- Cross-module **integration tests** (`tests/integration/test_<prompt_id>.py`) that exercise modules A + B together end-to-end
- **BUILD_LOG entry** at `docs/build_log.md` per the format of existing entries — Refactored, Session merged (placeholder for PR number; you can leave as TBD or update after PR opens), Outcome, Evidence (bulleted; one bullet per delivered artifact), Carry-forwards (bulleted; one per future-prompt impact)
- Any **final tying-together code** — registration entries, package init updates, etc. — that wasn't part of A or B
- The **PR**

## What commit 4 is NOT

- Module A or B logic (already in C2 and C3)
- Schema/plumbing (already in C1)
- Skipping pre-existing commits and merging straight to main

## Pre-flight check

```bash
git log --oneline -5     # all three earlier commits present?
git status               # clean tree?
gh auth status           # gh ready for PR creation?
```

If any fails, stop and report.

## What you do

1. Read `prompts/_active/<NN>-*.md` § C4.
2. Write the integration tests per the prompt's §7 (test floor for integration is typically ≥3-5 tests).
3. Author the BUILD_LOG entry per the format of the most recent entry in `docs/build_log.md`. Match shape exactly:
   ```
   ### Prompt <NN> — <short title>
   - **Refactored**: by Partner in Cowork, <date>. Prompt file: `prompts/_active/<NN>-<slug>.md` (~XXX lines, quality bar = <prior prompt>).
   - **Session merged**: PR #<TBD>, commits <hash1>/<hash2>/<hash3>/<hash4>, merged <date>.
   - **Outcome**: MERGED.
   - **Evidence**:
     - <bullet per delivered artifact, with file paths and constitutional citations>
   - **Carry-forward for prompt <next>**:
     - <bullet per carry-forward>
   ```
4. Apply citation discipline.
5. Run final verification per §9 of the refactored prompt. Typically:
   ```bash
   poetry run ruff check adminme/ packs/ tests/
   poetry run mypy adminme/ 2>&1 | tail -3
   poetry run pytest tests/ -v --tb=short
   bash scripts/verify_invariants.sh
   ```
   All four should succeed. If pytest fails or ruff has unfixable errors, amend C4. If still failing, emit `<COMMIT_FAILED/>` and stop.
6. Commit `<NN>.4: <description> + BUILD_LOG entry + integration tests`.
7. Push to the working branch:
   ```bash
   git push -u origin <working-branch>
   ```
8. Open the PR:
   ```bash
   gh pr create --base main --title "Prompt <NN>: <one-line description>" --body "$(cat <<'EOF'
   Refactored prompt: prompts/_active/<NN>-<slug>.md
   Four commits per the four-commit discipline (PM-4):
   - <hash1>: schema/plumbing
   - <hash2>: module A
   - <hash3>: module B
   - <hash4>: integration + BUILD_LOG entry + this PR

   Test floor met: ≥XX tests (shipped YY).
   verify_invariants.sh: exit 0
   ruff: clean
   mypy: <note any acceptable issues>
   pytest: passing

   Carry-forwards (see BUILD_LOG entry for full list):
   - <one-line summaries>
   EOF
   )"
   ```
9. Tell the operator the PR number, then stop.

## After the PR opens

You stop. The operator reviews and merges. The Reviewer Routine then fires on the merge.

## Hard rules and citations

Per `executor-c1.md`. Apply identically.

## Output

- Exactly one git commit
- One `git push`
- One `gh pr create`
- A stdout report naming the PR number and reporting verification results
- Then stop

Sentinels (`<NO_OP/>`, `<COMMIT_FAILED/>`, `<NEEDS_AMENDMENT/>`) apply if relevant.

## Vision pre-commit check (true-north sanity)

Before staging commit 4, ask one question: **does what I've built actually serve a real family using AdministrateMe?**

This is a 30-second check, not a deep audit. Specifically:

- If this prompt shipped a pipeline: would a family member actually benefit from the pipeline firing, or did I just satisfy the test floor?
- If this prompt shipped a projection: would the projection's data be queryable in a useful way by the surfaces the prompt or upstream prompts define?
- If this prompt shipped console code: would a family member be able to use this surface, or is it correct-but-unusable?
-