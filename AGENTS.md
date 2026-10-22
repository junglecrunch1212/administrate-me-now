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
- For member dashboard inspection and browser acceptance, follow `docs/refactor/MEMBER-DEVICE-TESTING.md`. The browser must run on the authorized member device; the central Host's identity and principal view-as are not substitutes. Test the control and result-return paths before claiming remote acceptance.

## Deployment safety

A deployment operates on the immutable merge commit from protected `main`. A central runtime deployment uses the existing guarded updater, observation mode, verified encrypted backup, and automatic rollback on a new degradation or failed prerequisite. After central activation, require bounded settlement, full Doctor, privacy and tenant-isolation checks, authenticated read-only browser acceptance for changed behavior, backup/rollback evidence, and zero unintended provider effects. A supported compatible component release follows its bounded installation, health and rollback contract without manufacturing a central deployment or maintenance cycle.

Runtime or dependency migration, provider enrollment, write enablement, Keychain or permission changes, and unrelated live repairs always require their own explicit owner request. Never edit an installed release, active virtual environment, LaunchAgent, encrypted state, or provider configuration to make activation pass. Hosted CI and subagents must not access the live family instance or credentials.

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

During development, select targeted behavioral checks by the changed behavior
and its dependencies, with appropriate formatting, types, invariants, and
privacy/security checks. Include a production-composition regression when a
change crosses application boundaries. Run behavioral checks only in hosted CI
or an authorized disposable lab; targeted testing does not authorize experiments
on the family Mac, member acceptance devices, or the live instance.

Keep the pull request in draft while iterating. Select development checks by
affected behavior and dependencies; a development pass is not automatically
final release evidence. After a failure, reproduce and fix the concrete cause,
batch related corrections, and pass relevant regressions before requesting
another release run. Return an invalidated candidate to draft.

When the candidate is settled, mark the same PR ready for its selected release
scope. The stable protected jobs evaluate that scope and require its actual
passing checks. Missing, failed, cancelled, skipped, stale or incomplete
required evidence cannot approve release. No label, separate review artifact,
prescribed phrase or additional owner approval is needed to select the
authorized scope. Preserve useful running checks; do not restart them solely
to adopt this policy. Retire an unnecessary broad run only after a legitimate
scoped gate exists and its required evidence passes. Report development,
scoped release and full-system results separately, with concrete failures and
coverage gaps.

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

These commands are the full-system inventory, not a requirement to run every
suite for every change. Full-system deployment validation also includes
unscoped lint, all pack/package validators, identity and instance-path scans,
replay/isolation/backup tests, and the clean macOS lab rehearsal in
`docs/refactor/ACCEPTANCE-CONTRACT.md`. Record environment-specific skips and
failures verbatim.

## Risk-based release scope

Owner-directed policy (2026-10-21) supersedes the earlier blanket requirement
for complete Linux and macOS suites on every release. Optimize delivery for
this household with bounded validation and recoverable changes:

- Documentation, copy and other non-executable changes require relevant
  document/static checks, not an application release or behavioral suite.
- An isolated reversible component may release after its final scoped checks
  cover the changed behavior, affected dependencies/interfaces, privacy and
  security boundaries, and realistic installation/use on the affected platform.
  File size alone does not establish isolation.
- Broaden coverage for shared runtime/composition, encrypted storage or event
  schema/migrations, cross-member identity/authorization, external-write
  governance, or widely shared dependencies. Require full-system validation
  when effects are system-wide or cannot be credibly bounded, and explain the
  concrete reason. A localized sensitive fix still needs its complete affected
  security/data contract; a bootstrap path or new commit alone is not a reason
  to run every subsystem.
- A bridge-helper-only installer/SDK/diagnostics release normally qualifies as
  a component: prove actual packaged installation on the affected toolchain,
  helper integrity, interrupted recovery, diagnostic/secret cleanup and deployed
  enrollment-interface compatibility. Reuse the unchanged central runtime
  through the supported compatible component path. Identify actual coupling
  rather than silently rebuilding or redeploying the central application.
- Retain the known-working predecessor and supported rollback. Perform bounded
  component health/use checks after authorized deployment and roll back on a
  regression. Verify backup/restore compatibility before persistent-state
  changes; reuse valid unchanged evidence where its bindings permit.
- Preserve ownership/isolation, encryption, append-only history, recipient
  restrictions, independent sending approvals, credential handling and audited
  external effects. Rollback cannot undo a disclosure or send. No candidate
  experiments on family/member systems, installed-release edits or invented
  permissions are permitted.
- Keep protected PR delivery. The gate must evaluate the selected scope and
  actual passing evidence; never remove protection, forge a status or bypass
  the current gate while adding a narrower path. Policy/CI changes need focused
  selection and validation tests, not inherently unrelated application suites.
- Bind evidence to the component/artifact, relevant toolchain, locks,
  dependencies and interfaces. Reuse unchanged component evidence only when
  those inputs demonstrably match. Changed behavior and new interactions need
  fresh checks; unrelated prose alone does not invalidate all prior evidence.

## Readiness before release validation

Owner-directed policy (2026-10-21): before declaring a behavioral candidate
ready, trace the affected user workflow through its real production boundaries.
Keep a compact impact map in the existing PR/task: affected contract, concrete
test or evidence, and unresolved gap. Consider identity and permission,
acquisition policy, events and derived state, historical ownership, readiness,
restart, revocation, and upgrade/restore/rollback where affected. Bound this
review to the change and its dependencies; justify exclusions rather than
retesting every subsystem.

Use production composition and representative installed-release conditions in
isolated CI: packaged code without Git metadata, existing configuration and
retained events, disabled/revoked sources, and restart/recovery where relevant.
Assert user-visible outcomes and preserved invariants. A successful setup call
alone does not prove continued usability or maintainability.

When a late defect exposes a missing assumption, review the finite equivalent
paths before correcting just that example: other new intake routes for an
ownership gap, related loaders and installed entrypoints for source identity or
cache gaps. Extend shared regressions and batch related corrections without an
unrelated broad refactor. Check discoverable deployment/device prerequisites
with supported read-only diagnostics before promising readiness or entering
maintenance; candidate behavior still runs only in isolated CI or an authorized
disposable lab. Preserve consent, permission, evidence-expiry and operational
guards.

Complete this bounded review and the relevant lifecycle checks before marking
a draft ready or requesting final validation for its selected scope. Resolve known blockers and
actionable findings, pass focused checks, and settle the candidate first. A new
invalidating defect returns the PR to draft and relevant focused validation;
explain the concrete reason for restarting. Preserve useful unchanged evidence
and every required final-candidate gate. Report development checks passed,
scoped/full release checks passed, deployed, and verified usable separately. Remaining dependencies
are distinct from CI duration; test estimates are not completion promises.
Summarize late defects and added coverage in the existing PR without another
tracking/review task or permission ceremony.

Carry this policy into applicable focused checks through normal PR review.
Instruction-only changes need document checks, not invented behavioral tests or
a fresh release cycle merely to synchronize prose. Do not interrupt otherwise
valid running release checks for an instruction-only update.

## Definition of done

A claim is complete only when a process-level test demonstrates it through the production composition root. Unit tests for isolated layers are necessary but insufficient. Update governing documentation when behavior or scope changes, but do not create evidence-only follow-up work. Classify semantic changes as PRESERVE, MODERNIZE, SUPERSEDE, or DEFER and cite the governing source. DEFER is never valid for a required product function, projection capability, privacy/governance invariant, workbook round trip, or bootstrap safety.
