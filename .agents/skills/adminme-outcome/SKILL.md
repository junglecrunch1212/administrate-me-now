---
name: adminme-outcome
description: Translate an AdministrateMe owner's ordinary-language product or frontend request into a bounded, schema-valid outcome contract and owner journey. Use before implementation when a request needs acceptance cases, visible non-goals, privacy boundaries, side-effect classification, or a decision about whether owner input is genuinely required.
---

# AdministrateMe outcome

Turn owner intent into observable product behavior without delegating engineering choices.

## Workflow

1. Read the applicable `AGENTS.md` chain and `docs/refactor/CURRENT-STATE.md`.
2. Inspect current executable behavior, production-composition tests, and governing `docs/refactor/` sources before inferring a new promise.
3. Rephrase the request as one primary frontend or household journey.
4. Define concrete happy, empty, loading, stale, degraded, denied, retry, and terminal-failure cases. Mark non-applicable states with evidence.
5. Name visible non-goals and privacy, external-effect, irreversibility, cost, and release boundaries. Classify every `release_impacts` field as `YES` or `NO`: provider writes, runtime/dependency migration, provider enrollment, credentials/Keychain/permissions, privacy/authorization, destructive data, observation mode, release controls, and member-visible behavior. Never leave a low-risk assumption implicit.
6. Ask no backend question. Ask at most one batched owner question only when the unresolved answer changes visible behavior, wording, click burden, privacy, cost, irreversibility, or release intent; recommend one answer and show the frontend consequence.
7. Replace every task-neutral `UNDETERMINED` placeholder in `outcome-contract.json` in the system-temp `administrateme-harness-*` run. Leave no state applicability, privacy, effect, journey, or owner-decision placeholder for engineering to guess, then validate the whole run.

## Boundaries

- Treat events, projections, provider commands, and constitutional status as evidence, not design prompts for the owner.
- Preserve contradictory paths and label missing proof `UNDETERMINED`.
- Classify operations as `READ`, `LOCAL_WRITE`, `REPOSITORY_WRITE`, `EXTERNAL_WRITE`, `RELEASE`, or `LIVE_HOUSEHOLD`.
- Stop before an external write, release, live-household action, privacy expansion, new cost, or irreversible effect without exact authority.
- Do not implement code, choose a model provider, store household data, or grant release permission in this skill.

<!-- BEGIN GENERATED:ADMINME-HARNESS -->
## Generated harness reference

Primary artifact: `outcome-contract.json`.
Validate artifacts with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli validate-run <run-dir>`.
Inspect current inventories with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli inventory`.
Use `UNDETERMINED` for UNKNOWN, TRACE_BREAK, missing, or unproved behavior.
<!-- END GENERATED:ADMINME-HARNESS -->
