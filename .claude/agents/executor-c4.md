# Executor agent — Commit 4 (integration + BUILD_LOG + PR open)

**Same shape as `executor-c1.md` through `executor-c3.md`.**

**v4 invocation** (per RUNBOOK-EXECUTOR.md):
```bash
git checkout claude/<PROMPT_ID>-c3     # branch from c3 per stacked BRANCH-STRATEGY.md
git checkout -b claude/<PROMPT_ID>-c4

claude --print --model "${ADMINME_MODEL_EXECUTOR:-opus}" --max-budget-usd 50 \
       --permission-mode bypassPermissions \
       '<c4 work instruction>' < /dev/null 2>&1 | tee /tmp/c4.log
```

**Output:** one git commit + push + PR (`base` = `claude/<PROMPT_ID>-c3`) — closing the four-commit cycle for this prompt.

**Commit message format (v4 mandatory):** `phase <PROMPT_ID> commit 4: <imperative summary, <72 chars>`. Example: `phase 10c-iii commit 4: integration tests + BUILD_LOG`.

**v3 test-drive lesson (POSTMORTEM §1.6):** if your c4 branch's history doesn't show c1/c2/c3 commits via `git log main..HEAD`, you're branched off the wrong base. STOP and report `<COMMIT_FAILED reason="c4 not stacked on c3"/>` rather than re-creating c1's work to compensate.

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

## Pre-flight check (v4 — mandatory)

```bash
# Verify stack integrity — c1, c2, c3 commits must all be visible from this branch.
EXPECTED_PRIORS=3
ACTUAL_PRIORS=$(git log --oneline main..HEAD | wc -l)
if [ "$ACTUAL_PRIORS" -lt "$EXPECTED_PRIORS" ]; then
  echo "FAIL: c4 branch only has $ACTUAL_PRIORS prior commits; expected $EXPECTED_PRIORS (c1+c2+c3)"
  echo "      Likely branched off wrong base. STOP and report <COMMIT_FAILED reason=\"c4 not stacked\"/>"
  exit 1
fi

git status               # clean tree?
[ -n "$GITHUB_TOKEN" ]   # token for explicit-URL push
```

If pre-flight fails, STOP and emit `<COMMIT_FAILED reason="c4 not stacked on c3"/>`. Do NOT re-create c1/c2/c3 work to compensate (per POSTMORTEM §1.6).

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
6. **Commit** with the v4-mandatory format:
   ```bash
   git -c user.email='claude+executor@anthropic.com' -c user.name='claude (executor-c4)' \
       commit -am 'phase <PROMPT_ID> commit 4: integration tests + BUILD_LOG'
   ```
7. **Push** via the explicit-URL token-injection pattern (handles VS Code credential helper interference + LFS hook absence per v4 §3.3):
   ```bash
   git push -u --no-verify \
     "https://x-access-token:${GITHUB_TOKEN}@github.com/junglecrunch1212/administrate-me-now" \
     "claude/<PROMPT_