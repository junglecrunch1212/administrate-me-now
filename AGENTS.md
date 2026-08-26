# AdministrateMe engineering instructions

This repository is a household-scale, local-first, event-sourced platform. Treat documentation and comments as evidence; executable behavior and tests decide whether a change works. The v2 reconciliation in `docs/refactor/` governs product architecture. Historical delivery procedures are not current authority.

## Working rules

- Start each change from current protected `main`, use one `codex/` branch and one pull request, and preserve unrelated changes. Never rewrite published history, force-push, or push directly to `main`.
- The implementation task may use bounded subagents for implementation, testing, research, or advisory review. A separate user-created review task, exact-head review artifact, staged label, or prescribed authorization phrase is not required.
- GitHub is the delivery control plane: fix failures on the same branch, require the protected PR checks to pass, resolve actionable review comments, and merge through the pull request. Normal commits invalidate only the checks GitHub reruns; they do not start a new review ceremony.
- A repository owner may authorize merge in ordinary language. A merge request does not authorize deployment or another operational change.
- Never place household names, emails, phone numbers, addresses, credentials, or tokens in platform code, bootstrap code, built-ins, or tests.
- Keep the dependency direction: adapters -> encrypted append-only event log/durable bus -> deterministic projections -> pipelines -> family surfaces. Sources emit events; projections are never a source of truth.
- OpenClaw owns channels, conversations, generic memory, sessions, model routing, cron/background execution, agent orchestration, generic tool approval, device nodes, and runtime administration. AdministrateMe owns typed household truth, domain authorization, scope/privacy, projections, workbooks, provenance, correction, observation, and outcome feedback.
- All LLM work goes through the pinned OpenClaw `llm-task` seam. Never add a provider SDK or a second model router.
- Do not collapse event facts, calendar events, tasks, commitments, list items, recurrences, or scheduler jobs into one model.
- External writes require the documented allowlist -> governance -> rate-limit ordering, independent OpenClaw and domain approval, a final content/privacy guard, and observation-mode handling. Audit every terminal result.
- Preserve all six product functions, all thirteen projection capabilities, all five adapter categories, all six pack kinds, the five built-in profiles, and four built-in personas. A missing implementation must be reported, not silently omitted.
- A central v2 deployment is one long-lived AdministrateMe Python runtime behind Tailscale Serve plus one exactly pinned OpenClaw Gateway. Member bridge daemons remain separate, per-member edge processes. Node may build browser assets but is not another central application runtime.
- Phase A runs only in Codex/cloud or a disposable lab. Never experiment on the family Mac. Phase B begins only after merge and a clear, separate owner request to deploy.

## Deployment safety

A deployment operates on the immutable merge commit from protected `main`. Use the existing guarded updater; keep observation mode active; verify an encrypted backup before switching; and roll back automatically on a new degradation or failed prerequisite. After activation, require bounded settlement, full Doctor, privacy and tenant-isolation checks, authenticated read-only browser acceptance for changed behavior, backup/rollback evidence, and zero unintended provider effects.

Runtime or dependency migration, provider enrollment, write enablement, Keychain or permission changes, and unrelated live repairs always require their own explicit owner request. Never edit an installed release, active virtual environment, LaunchAgent, encrypted state, or provider configuration to make activation pass. Hosted CI and subagents must not access the live family instance or credentials.

## G-002 autonomous observation release train

G-002 is a standing release capability for ordinary observation-only releases
and bounded repairs. It supersedes G-001's per-head owner phrase and separate
user-visible review-task requirement only inside the deterministic G-002
envelope. G-001 remains the only route for a candidate that changes provider-
write authority, observation mode, runtimes or dependencies, provider
enrollment, credentials, Keychain, permissions, privacy or authorization,
destructive data behavior, release controls, or any path classified manual by
`scripts/harness/autonomous_release.py`.

The governance change that introduces G-002 cannot authorize its own merge or
enrollment. The repository owner manually merges that governance-only PR, adds
an `OPENAI_API_KEY` repository secret used only by the read-only hosted job, sets
`ADMINME_CODEX_REVIEW_ENABLED=true`, and configures the resulting hosted review
as required. A task created before that merge continues under G-001.

- Run exact-bound static, focused, production-composition, and applicable
  browser checks first. Then run the independent exhaustive adversarial review
  before breadth, release-candidate, and macOS suites. A review completed after
  breadth is not G-002 evidence.
- An eligible owner-requested build may publish a draft and enqueue one exact
  committed head after `review-ready`; it does not gain merge or live authority.
  The versioned scheduled operator in
  `.github/codex/prompts/g002-release-operator.md` is the only standing
  consumer of that immutable local queue.
- The hosted reviewer is a fresh, ephemeral, read-only Codex job with no
  implementation context or write permission. It reviews the exact candidate
  head, records a content-free receipt, and fails on actionable or unresolved
  evidence. A head change triggers a new review; it does not require an owner
  prompt.
- Every hosted result/receipt and every local command attempt remains immutable.
  Canonical Harness validation re-hashes retained failed and invalidated
  attempts; only the latest exact-bound attempt may determine current status.
- An admitted ordinary observation release requires a healthy baseline. An
  admitted bounded repair may start from a degraded baseline only when it names
  one or more exact unhealthy required capabilities. Non-target degradation may
  remain, but every target must recover and no new or worsened degradation may
  appear. Otherwise the updater rolls back.
- Both lanes require current `origin/main` containment, clean mergeability,
  zero actionable threads, exact-head deterministic/production/browser-as-
  applicable/breadth/release/hosted proof, an attested updater, observation
  active, zero live provider claims, and backup capability. The deterministic
  decision is `python -m scripts.harness.cli autonomous-release <manifest>`.
  The evaluator rejects declared green fields unless the outcome-bound impact
  classification, validated Harness run,
  exact release-tier receipts, downloaded hosted review result/receipt, and
  actual review-before-breadth timestamps agree.
- After a passing G-002 decision, the release operator may merge that exact
  PR/head through GitHub with a merge commit and
  run the ordinary guarded Phase B transaction without another owner message.
  It may not push to `main`, bypass protection, rewrite history, enable writes,
  or broaden a repair target.
- Settlement requires the exact selected merge, observation retained, verified
  encrypted backup, a fresh complete Doctor report, an exact-merge live
  authenticated privacy/isolation receipt, applicable authenticated browser
  proof, rollback readiness, zero provider effects, and monotonic required-
  capability and named Doctor-check health. The ordinary lane requires every
  Doctor check healthy; bounded repair may retain only admitted non-target
  failures after all named capability and Doctor targets clear. Any ambiguity
  returns `ROLLBACK`, never success. A bounded repair is passed to the updater
  only by an exact admission receipt and generated two-parent-merge activation
  policy.
- The operator posts one content-free release receipt. G-002 does not require a
  second docs-only PR for an otherwise complete release; governing documents
  are updated in a later ordinary documentation batch. Failed or incomplete
  attempts remain immutable evidence and do not consume owner attention.

## Code Review Rules

### Event-sourced concurrency and authority

- Flag any path that can append, advance a cursor, close health/circuit state,
  or report readiness after its lease/authority expired or transferred. The
  safe path revalidates authority at the durable mutation boundary.
- Flag cancellation that is swallowed, translated into a durable provider
  error, advances state, or waits on a polling interval. Cancellation must
  propagate unchanged and leave no false durable result.

### Harness evidence

- Flag a check rerun that overwrites, drops, or excludes a prior attempt from
  canonical validation. Every retained receipt and hashed log must validate;
  the current conclusion may derive from the latest attempt only.
- Flag review performed only after breadth/release work. The exhaustive
  adversarial review belongs at the exact-bound pre-breadth checkpoint.

## Repository map

- `adminme/events/`: envelopes, schema registry, SQLCipher log, durable in-process bus.
- `adminme/projections/`: deterministic read models and runner.
- `adminme/pipelines/`, `packs/pipelines/`, `packs/skills/`: reactive domain logic and typed OpenClaw skill calls.
- `adminme/adapters/`, `adminme/lib/adapters/`, `packs/adapters/`: adapter protocol, five-category contracts, implementations.
- `adminme/products/`: product router namespaces served by the one Python runtime.
- `adminme/lib/`: session/scope, governance, observation, pack registry, OpenClaw skill seam.
- `bridge/`: per-member bridge package and tests.
- Browser behavior remains governed by `docs/refactor/UX-BEHAVIOR-CONTRACT.md`; the false Node application scaffold was removed at Gate 6.
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

Before a deployment, also run the full unscoped lint suite, all pack/package validators, identity and instance-path scans, replay/isolation/backup tests, and the clean macOS lab rehearsal in `docs/refactor/ACCEPTANCE-CONTRACT.md`. Record environment-specific skips and failures verbatim.

## Definition of done

A claim is complete only when a process-level test demonstrates it through the production composition root. Unit tests for isolated layers are necessary but insufficient. Update governing documentation when behavior or scope changes, but do not create evidence-only follow-up work. Classify semantic changes as PRESERVE, MODERNIZE, SUPERSEDE, or DEFER and cite the governing source. DEFER is never valid for a required product function, projection capability, privacy/governance invariant, workbook round trip, or bootstrap safety.
