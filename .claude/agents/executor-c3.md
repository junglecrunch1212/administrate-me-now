# Executor agent — Commit 3 (module B)

**Same shape as `executor-c1.md` and `executor-c2.md`.**

**v4 invocation** (per RUNBOOK-EXECUTOR.md):
```bash
git checkout claude/<PROMPT_ID>-c2     # branch from c2 per stacked BRANCH-STRATEGY.md
git checkout -b claude/<PROMPT_ID>-c3

claude --print --model "${ADMINME_MODEL_EXECUTOR:-opus}" --max-budget-usd 50 \
       --permission-mode bypassPermissions \
       '<c3 work instruction>' < /dev/null 2>&1 | tee /tmp/c3.log
```

**Output:** one git commit + push + PR (`base` = `claude/<PROMPT_ID>-c2`) — OR sentinel.

**Commit message format (v4 mandatory):** `phase <PROMPT_ID> commit 3: <imperative summary, <72 chars>`. Example: `phase 10c-iii commit 3: 3 pipeline packs + handler-direct unit tests`.

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
- Cross-module integrati