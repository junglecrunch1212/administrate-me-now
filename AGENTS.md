# AdministrateMe engineering instructions

This repository is a household-scale, local-first, event-sourced platform. Treat documentation and comments as evidence; prompt-era prose is not executable proof. The v2 constitutional reconciliation lives in `docs/refactor/` and governs new work. Historical detail remains in `WHAT-IS-ADMINISTRATEME.md`, `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_CONSOLE_REFERENCE.html`, `ADMINISTRATEME_DIAGRAMS.md`, `ADMINISTRATEME_FIELD_MANUAL.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `prompts/PROMPT_SEQUENCE.md`, `docs/00-mental-model.md`, `docs/openclaw-cheatsheet.md`, and `docs/architecture-summary.md`.

## Working rules

- v2 integration work belongs on `codex/administrateme-v2-runtime`, based on `main`. Never rewrite published history or weaken tests.
- Release authority is deny-by-default: do not merge a pull request, push directly to `main`, or begin Phase B unless the exact G-001 controlled-release procedure below is active and satisfied.
- Preserve unrelated changes. Never place household names, emails, phone numbers, addresses, credentials, or tokens in platform code, bootstrap code, built-ins, or tests.
- Keep the dependency direction: adapters -> encrypted append-only event log/durable bus -> deterministic projections -> pipelines -> family surfaces. Sources emit events; projections are never a source of truth.
- OpenClaw owns channels, conversations, generic memory, sessions, model routing, cron/background execution, agent orchestration, generic tool approval, device nodes, and runtime administration. AdministrateMe owns typed household truth, domain authorization, scope/privacy, projections, workbooks, provenance, correction, observation, and outcome feedback.
- All LLM work goes through the pinned OpenClaw `llm-task` seam. Never add a provider SDK or a second model router.
- Do not collapse event facts, calendar events, tasks, commitments, list items, recurrences, or scheduler jobs into one model.
- External writes require the documented allowlist -> governance -> rate-limit ordering, independent OpenClaw and domain approval, a final content/privacy guard, and observation-mode handling. Audit every terminal result.
- Preserve all six product functions, all thirteen projection capabilities, all five adapter categories, all six pack kinds, the five built-in profiles, and four built-in personas. A missing implementation must be reported, not silently omitted.
- A central v2 deployment is one long-lived AdministrateMe Python runtime behind Tailscale Serve plus one exactly pinned OpenClaw Gateway. Member bridge daemons remain separate, per-member edge processes. Node may build browser assets but is not another central application runtime.
- Phase A runs only in Codex/cloud or a disposable lab. Never experiment on the family Mac. Phase B starts only after review and merge.

## G-001 controlled release authority

G-001 is a narrow exception to the default no-merge rule, not blanket merge or
deployment authority. The governance change that introduces G-001 cannot
authorize its own merge; a repository owner must merge that governance-only PR
manually. G-001 applies only after the merged policy is present on `main`.
A controlled-release task must start after that merge so it loads the effective
instructions; a task started under the former absolute rule cannot promote
itself by observing that the file changed.

- A release authorization must be an explicit current-task user instruction in
  the form **“Authorize guarded release of PR #<number> at head
  <40-character-commit>.”** It authorizes one named PR, one immutable head, and
  one release transaction. A change order, branch name, earlier blanket
  instruction, inferred intent, or approval of a different SHA is insufficient.
- Before merge, an independent human or a separate user-visible Codex review
  task that did not implement the candidate must review the exact head. An
  implementation subagent is not independent review. A human records an
  approving GitHub review. A separate Codex task records a content-free PR
  comment naming its task/thread, exact SHA, review scope, commands, findings,
  and conclusion. Any material head change invalidates the review.
- The authorized head must contain current `origin/main`, the PR must be clean
  and mergeable, every required local and hosted gate must pass on that head,
  and every actionable review thread must be resolved. If `main` advances,
  merge current `main` into the candidate without rebasing, force-pushing, or
  rewriting history; resolve only the actual conflicts, then repeat independent
  review and every required gate.
- Merge only through the named GitHub PR using a merge commit bound to the
  authorized head. Never push directly to `main`, merge a different SHA, bypass
  branch protection, weaken a check, or combine another PR into the authority.
- Phase B may begin only after the resulting merge commit exists on
  `origin/main`. Deploy an immutable checkout of that merged SHA through the
  reviewed guarded updater only. Never edit an installed release, active venv,
  LaunchAgent, encrypted state, or provider configuration to make activation
  pass.
- Record the live baseline, retain observation mode, create and verify the
  encrypted pre-switch backup, and require automatic rollback on any new
  degradation or failed prerequisite. Runtime/dependency migrations, provider
  enrollment, write enablement, Keychain/permission changes, and unrelated
  repairs require separate named approval and are never opportunistic parts of
  a guarded release.
- After activation, require bounded settlement, full Doctor, privacy/isolation
  checks, authenticated read-only browser acceptance for the changed behavior,
  backup/rollback evidence, and zero unintended provider effects. A failed or
  ambiguous check makes the deployment incomplete even if liveness is green.
- The same authorization permits one follow-up PR containing only release
  evidence in governing documentation. Its exact head must receive independent
  docs review and hosted checks before merge; it may not change code,
  configuration, dependencies, locks, tests, runtime state, or the release
  result it records.
- Stop without improvising when the PR/SHA/base, independent review, test
  result, backup, observation state, updater provenance, health, settlement,
  privacy, rollback, or live acceptance differs from the authorized plan.
  Preserve diagnostics and the last healthy release; do not broaden authority
  to finish the task.

## Repository map

- `adminme/events/`: envelopes, schema registry, SQLCipher log, durable in-process bus.
- `adminme/projections/`: deterministic read models and runner.
- `adminme/pipelines/`, `packs/pipelines/`, `packs/skills/`: reactive domain logic and typed OpenClaw skill calls.
- `adminme/adapters/`, `adminme/lib/adapters/`, `packs/adapters/`: adapter protocol, five-category contracts, implementations.
- `adminme/products/`: product router namespaces served by the one Python runtime.
- `adminme/lib/`: session/scope, governance, observation, pack registry, OpenClaw skill seam.
- `bridge/`: per-member bridge package and tests.
- Browser behavior remains governed by `docs/refactor/UX-BEHAVIOR-CONTRACT.md`;
  the false Node application scaffold was removed at Gate 6.
- `bootstrap/`: baseline prose/config scaffolding. Its target is `docs/refactor/BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md`.
- `tests/acceptance/`: golden process gates. Run with `--runxfail` for release gating.

## Supported checks

Use Python 3.14 and Node 24, matching the candidate manifests and ADR-0006. Existing Python 3.11/Node 22 deployments remain untouched until the separately approved runtime-migration gate. The bridge package must be importable when running the root suite.

```bash
poetry sync --with dev
PYTHONPATH=bridge poetry run pytest -q
PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q
poetry run ruff check adminme bridge/adminme_bridge
poetry run mypy adminme bridge/adminme_bridge
bash scripts/verify_invariants.sh
npm --prefix openclaw/administrateme ci
npm --prefix openclaw/administrateme run check
npm --prefix openclaw/administrateme test
```

Before a release, also run the full unscoped lint suite, all pack/package validators, identity and instance-path scans, replay/isolation/backup tests, and the clean macOS lab rehearsal in `docs/refactor/ACCEPTANCE-CONTRACT.md`. Record environment-specific skips and failures verbatim.

## Definition of done

A claim is complete only when a process-level test demonstrates it through the production composition root. Unit tests for isolated layers are necessary but insufficient. Update `docs/refactor/HANDOFF.md`, the crosswalk, migration map, and acceptance evidence when behavior or scope changes. Classify semantic changes as PRESERVE, MODERNIZE, SUPERSEDE, or DEFER and cite the governing source. DEFER is never valid for a required product function, projection capability, privacy/governance invariant, workbook round trip, or bootstrap safety.
