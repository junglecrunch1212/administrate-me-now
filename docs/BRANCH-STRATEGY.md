# BRANCH-STRATEGY.md

**v4 — anchors the four-commit cycle to a concrete branch-and-PR shape.**

Read this before invoking any Executor session. Partner's refactored prompt MUST specify which model (stacked or parallel) applies; if it doesn't, the Executor defaults to **stacked** (safer).

---

## The two models

### Stacked (default, used for most prompts)

```
main
 └── claude/<PROMPT_ID>-c1                   (PR #N1: base=main)
       └── claude/<PROMPT_ID>-c2             (PR #N2: base=claude/<PROMPT_ID>-c1)
             └── claude/<PROMPT_ID>-c3       (PR #N3: base=claude/<PROMPT_ID>-c2)
                   └── claude/<PROMPT_ID>-c4 (PR #N4: base=claude/<PROMPT_ID>-c3)
```

Each PR's `base` is the prior c's branch, **not main**. The operator merges PRs in order: when c1 merges, GitHub auto-rebases c2 onto main and the c2 PR's diff collapses to just c2's changes. Same cascade for c3 and c4.

**Use stacked when:**
- c2's code imports types or functions defined in c1
- c3's tests reference fixtures defined in c1 or c2
- c4's integration tests exercise the full pipeline (c1+c2+c3 together)
- This is the AdministrateMe norm — almost every prompt is stacked

**v3 test-drive lesson:** `10c-iii` is stacked. v3 invocation branched c2, c3 off main individually, then c4 off c3. The c4 Executor noticed c1's content was missing from its branch and re-created c1's work as part of c4, mis-labeling the commit. v4 fixes this by making the stack explicit in the agents and the runbook.

### Parallel (rare, only for truly independent c-bands)

```
main
 ├── claude/<PROMPT_ID>-c1   (PR #N1: base=main)
 ├── claude/<PROMPT_ID>-c2   (PR #N2: base=main)
 ├── claude/<PROMPT_ID>-c3   (PR #N3: base=main)
 └── claude/<PROMPT_ID>-c4   (PR #N4: base=main)
```

Each PR independent. Merge order doesn't matter. All four branches are off main.

**Use parallel only when:**
- The prompt's c-bands are truly independent (e.g., four separate but related skill packs that don't cross-import)
- The Reviewer can QC each PR standalone without context from the others
- The operator wants to be able to revert any single c without affecting the others

**Risk of parallel when stacked is correct:** the c4 Executor will see missing dependencies and either fail with `<COMMIT_FAILED reason="dependencies missing"/>` or — worse — re-create earlier c-bands' work and ship bundled commits that pollute history.

---

## How the refactored prompt declares its strategy

Partner adds this section near the top of every refactored prompt:

```markdown
## Branch strategy

**Model:** STACKED  <!-- or PARALLEL -->

**Rationale:** <one sentence — why stacked or parallel for this prompt>

**Dependencies between c-bands:**
- c2 → c1: <what c2 imports/uses from c1>
- c3 → c2: <what c3 imports/uses from c2>
- c4 → c1+c2+c3: <what c4's integration tests touch>

**Operator merge order (stacked):** merge c1 first, then c2, c3, c4 in turn. After each merge, the next PR's diff auto-collapses against main.

**Operator merge order (parallel):** any order; PRs are independent.
```

If a refactored prompt has no `## Branch strategy` section, the Executor defaults to **stacked** and the Reviewer flags this as a `LOW` finding in Job 3 carry-forwards.

---

## What each Executor cN does

### c1 (always)

```bash
git checkout main
git pull origin main
git checkout -b claude/<PROMPT_ID>-c1
# claude --print ... runs work, commits, pushes, opens PR with base=main
```

### c2 (depends on c1)

**Stacked:**
```bash
git checkout claude/<PROMPT_ID>-c1
git checkout -b claude/<PROMPT_ID>-c2
# claude --print ... PR has base=claude/<PROMPT_ID>-c1
```

**Parallel:**
```bash
git checkout main
git pull origin main
git checkout -b claude/<PROMPT_ID>-c2
# claude --print ... PR has base=main
```

### c3 (depends on c2 in stacked; main in parallel)

**Stacked:** branch from `claude/<PROMPT_ID>-c2`. PR base = same.
**Parallel:** branch from `main`. PR base = main.

### c4 (depends on c3 in stacked; main in parallel)

**Stacked:** branch from `claude/<PROMPT_ID>-c3`. PR base = same.
**Parallel:** branch from `main`. PR base = main.

c4's pre-flight check in stacked mode:
```bash
# Verify stack integrity — c1, c2, c3 commits must all be visible
EXPECTED_PRIORS=3
ACTUAL_PRIORS=$(git log --oneline main..HEAD | wc -l)
if [ "$ACTUAL_PRIORS" -lt "$EXPECTED_PRIORS" ]; then
  echo "FAIL: c4 branch only has $ACTUAL_PRIORS prior commits; expected $EXPECTED_PRIORS"
  exit 1
fi
```

---

## How the Reviewer interacts with stacked vs parallel

**Reviewer fires on every merge to main.** For stacked prompts, that means Reviewer fires 4 times (once per c-band merge). Each verdict is local to that c-band; the operator sees four verdicts in sequence as they merge through the stack.

**Reviewer's Job 1 (contract check)** is harder for stacked prompts because the c-band's PR diff alone doesn't tell the full story. Reviewer reads the prompt's §"Branch strategy" section to understand what's expected per c-band, then validates the PR's diff against that expectation specifically. If a c4 PR contains content that should have been in c1, Reviewer flags it as `silent_scope_change` per Job 1's rubric.

**Reviewer's Job 3 (next-prompt calibration)** triggers only after c4 merges in stacked prompts. The "next prompt" is the next row in `PROMPT_SEQUENCE.md`, not the next c-band of the current prompt.

---

## Edge cases

### What if the operator merges c2 before c1?

GitHub allows this. The c2 PR's base is `claude/<PROMPT_ID>-c1`, so the operator must merge c1 first OR change c2's base to main and resolve the diff manually.

**Recommendation:** don't bypass the order. Merge c1, let the Reviewer fire, then merge c2.

### What if c1's Reviewer verdict is FAIL_CRITICAL?

Stop the stack. Don't merge c2, c3, c4 yet. The operator opens an Issue (or follows `04-HALTS.md`) to resolve c1's findings via sidecar PR. Once c1 is clean (Reviewer PASS), merge c1 then proceed with the rest of the stack.

### What if c4 fails its integration tests in stacked mode?

c4 emits `<COMMIT_FAILED reason="integration tests fail"/>` and stops. Operator inspects, decides whether the failure is in c4's integration code (fix in c4) or in c1/c2/c3 (fix via sidecar PR before re-running c4).

### What if Partner wants to mix the models within a single prompt (e.g., c1 parallel, c2-c4 stacked)?

Don't. Pick one strategy per prompt. Mixed strategies confuse the Reviewer and the operator. If the prompt genuinely has both independent and dependent parts, split it into two prompts via the Splitter agent.

---

## Cleanup after the four-PR stack merges

After all four PRs merge:

```bash
# Local cleanup
git checkout main
git pull origin main
git branch -D claude/<PROMPT_ID>-c1 claude/<PROMPT_ID>-c2 claude/<PROMPT_ID>-c3 claude/<PROMPT_ID>-c4

# Remote cleanup (optional; GitHub keeps branches until you delete them)
git push origin --delete claude/<PROMPT_ID>-c1 claude/<PROMPT_ID>-c2 claude/<PROMPT_ID>-c3 claude/<PROMPT_ID>-c4
```

Or use the GitHub UI's "Delete branch" button on each merged PR (default).

---

## Branch-name convention (v4)

| Role | Branch prefix | Example |
|---|---|---|
| Executor c1-c4 | `claude/<PROMPT_ID>-c<N>` | `claude/10c-iii-c1` |
| Partner refactor | `partner/refactor-<PROMPT_ID>` | `partner/refactor-10c-iii` |
| Setup / infrastructure | `setup-<topic>` | `setup-claude-config-and-hooks` |
| Sidecar fix | `sidecar-<topic>` | `sidecar-fix-event-schema-typing` |
| Architecture amendment | `arch-amendment-<topic>` | `arch-amendment-bridge-sovereignty` |
| Partner handoff snapshot | `update-partner-handoff` | `update-partner-handoff` |
| Sequence update | `sequence-update-<topic>` | `sequence-update-10b-ii-split` |

Reviewer's exclude regex (`^(refactor-|sidecar-|arch-amendment-|update-partner-handoff|sequence-update-|setup-)`) does NOT match `claude/` branches. Reviewer fires on every `claude/` PR merge. Reviewer does NOT fire on Partner / sidecar / amendment merges (those are infrastructure).

Reviewer DOES fire on `partner/refactor-<PROMPT_ID>` merges — but the verdict is short because the diff is just a prompt-file addition. The Reviewer's Job 1 simply checks "is this a prompt file under prompts/_active/" and exits PASS. v5 may add a `partner/` exclude.

---

## Why this is the way it is

The v3 bundle was implicit about branch strategy. Partner's prompts said "create a branch and ship four commits" but didn't specify the topology. The v3 test drive surfaced the cost: c4 mis-labeled its commit, the diff was bundled, and the operator has to mentally untangle which content belongs to which c-band.

v4 makes the strategy explicit at three layers:
1. **Partner's refactored prompt** declares `STACKED` or `PARALLEL` in a `## Branch strategy` section
2. **Executor agents** branch and base PRs accordingly
3. **Reviewer** reads the strategy section and tunes Job 1 to the declared topology

Three layers of explicitness because branch topology is one of those things where ambiguity has cascading effects — a wrong choice in c1 leaks into every later c-band's diff.
