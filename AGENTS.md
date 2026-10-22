# AdministrateMe engineering instructions

This repository is a household-scale, local-first, event-sourced platform. Treat documentation and comments as evidence; executable behavior and tests decide whether a change works. The v2 reconciliation in `docs/refactor/` governs product architecture. The fast-delivery policy below, directed by the owner on 2026-10-22, supersedes conflicting release, testing, readiness and reporting requirements in older instructions, change orders and runbooks. Historical delivery procedures are not current authority.

## Working rules

- Start each change from current protected `main`, use one `codex/` branch and one pull request, and preserve unrelated changes. Never rewrite published history, force-push, or push directly to `main`.
- The implementation task may use bounded subagents for implementation, testing, research, or advisory review. A separate user-created review task, exact-head review artifact, staged label, or prescribed authorization phrase is not required.
- GitHub is the delivery control plane: fix blocking failures on the same branch, pass the applicable protected checks, and merge through the pull request. Resolve findings that affect the requested outcome or a protected invariant; unrelated improvements do not block delivery. Normal commits do not start a new review ceremony.
- Accept owner authorization in ordinary language. One request may authorize both merge and deployment; do not ask again when it already does. A request only to merge does not authorize deployment.
- Never place household names, emails, phone numbers, addresses, credentials, or tokens in platform code, bootstrap code, built-ins, or tests.
- Keep the dependency direction: adapters -> encrypted append-only event log/durable bus -> deterministic projections -> pipelines -> family surfaces. Sources emit events; projections are never a source of truth.
- OpenClaw owns channels, conversations, generic memory, sessions, model routing, cron/background execution, agent orchestration, generic tool approval, device nodes, and runtime administration. AdministrateMe owns typed household truth, domain authorization, scope/privacy, projections, workbooks, provenance, correction, observation, and outcome feedback.
- All LLM work goes through the pinned OpenClaw `llm-task` seam. Never add a provider SDK or a second model router.
- Do not collapse event facts, calendar events, tasks, commitments, list items, recurrences, or scheduler jobs into one model.
- External writes require the documented allowlist -> governance -> rate-limit ordering, independent OpenClaw and domain approval, a final content/privacy guard, and observation-mode handling. Audit every terminal result.
- Preserve all six product functions, all thirteen projection capabilities, all five adapter categories, all six pack kinds, the five built-in profiles, and four built-in personas. A missing implementation must be reported, not silently omitted.
- A central v2 deployment is one long-lived AdministrateMe Python runtime behind Tailscale Serve plus one exactly pinned OpenClaw Gateway. Member bridge daemons remain separate, per-member edge processes. Node may build browser assets but is not another central application runtime.
- Candidate tests run only in hosted CI or an authorized disposable lab. Deploy to the household only after protected merge and owner authorization. Observing a scoped release and rolling it back on regression is an intended delivery step; arbitrary candidate experiments and installed-release edits remain prohibited.
- For member dashboard inspection and browser acceptance, follow `docs/refactor/MEMBER-DEVICE-TESTING.md`. The browser must run on the authorized member device; the central Host's identity and principal view-as are not substitutes. Test the control and result-return paths before claiming remote acceptance.

## Deployment safety

A deployment operates on an immutable merge commit from protected `main`, using the supported updater or component installer, a known-working predecessor, compatible retained state, and a concrete rollback command. Keep the existing observation and external-write controls. Reuse valid backup/recovery evidence under its existing freshness and compatibility rules; require new restore testing when the change affects persistence or recovery. Do not manufacture a central deployment for an independently installable component.

After activation, check liveness/readiness and the changed user outcome within a bounded observation window. Check actual member rendering when UI behavior changed. Roll back promptly on a new runtime or workflow regression, verify predecessor health, then fix and retest the affected path in isolation. Rollback of that release is included in deployment authorization. Full Doctor, whole-household isolation sweeps and recovery rehearsals are not universal policy prerequisites for each narrow change; run them when the change or a concrete failure makes them relevant.

Existing automated updater safeguards remain enforced until changed and tested through a protected PR. Prose does not implement a shorter updater path or authorize bypassing its checks. Identify any such coupling once as an implementation gap instead of inventing repeated manual validation around it.

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

## Fast delivery and checks

The owner accepts occasional reversible runtime and workflow failures in return
for faster delivery. Default to build, check the changed behavior, ship when
authorized, observe, roll back if needed, and fix. Exhaustive pre-release
assurance is not the objective. Scope follows behavior and consequences, not
line count or a filename.

- **Prose only:** document/static checks. No application release or behavioral
  suite just to change instructions.
- **Ordinary reversible change:** a focused regression or existing relevant
  check, affected syntax/type/build checks, and a small realistic smoke test.
  Use production composition when the defect or interface needs it; do not
  require a new process test for every local edit. Those passing checks can be
  final release evidence without a second promotion run.
- **Sensitive change:** test the affected permission, identity, privacy,
  credential, external-write, persistence or recovery contract before release.
  Unchanged contracts do not require blanket reruns. Preserve ownership,
  encryption, append-only history, recipient restrictions, independent sending
  approvals and audited effects: rollback cannot undo disclosure or sending.
- **System-wide change:** use full-system validation only for concrete broad
  effects or an irreversible risk that cannot be bounded by focused checks.
  State the reason briefly. A shared file, bootstrap edit, missing path mapping,
  or unrelated commit is not by itself that reason.

For narrow work, one short PR paragraph naming the change, checks and
rollback is sufficient. No mandatory impact map, subsystem-exclusion essay,
lifecycle matrix, separate review chat, evidence-only PR, readiness approval
or draft/ready cycle. Use draft status when it actually avoids waste under
current CI. Fix a failure and run its regression plus affected dependencies;
broaden only when the failure reveals a wider shared assumption. Do not rerun
successful unrelated suites after each correction. Record nonblocking gaps
briefly rather than turning them into new release prerequisites.

Reuse passing results when the relevant artifact/source, toolchain, lockfile,
dependencies and interfaces match. New behavior needs fresh relevant evidence;
an unrelated prose change does not invalidate it. Parallelize independent
checks. Preserve useful running work and avoid duplicate local/hosted runs.
Select tests in the existing PR without asking for another owner approval.

Use Python 3.14 and Node 24 with the candidate manifests and ADR-0006. Existing
deployments keep their runtime until an explicitly authorized migration. Full
command inventory is in `docs/refactor/ACCEPTANCE-CONTRACT.md`; run only the
selected scope in hosted CI or an authorized disposable lab.

## CI implementation boundary

The intended default is scoped validation for ordinary reversible changes.
Current `scripts/release_scope.py` implements only `documentation`,
`bridge-helper` and `system-wide`; most other code still falls back to the full
workers. The protected `deterministic-consistency` and
`credential-free-rehearsal` checks remain required until the implementation is
updated. Do not report that this policy alone made those releases faster.

Implement narrower gates through ordinary protected PRs with focused positive
and negative selection tests. Each selected required check must actually pass;
missing, failed, cancelled, stale or unexpectedly skipped evidence cannot
become success. Keep branch protection and truthful results. A broad fallback
caused solely by a missing scoped path is a CI limitation to fix, not a reason
to demand extra manual suites. Policy-only edits need static checks.

## Definition of done

Deliver the requested behavior with the selected checks and state remaining
limitations honestly. Distinguish checked, merged, deployed and verified usable;
claim only the stages actually completed. For a small change, a concise result,
validation and any deployment blocker are enough. Update governing contracts
when behavior changes. Classify material contract changes as PRESERVE,
MODERNIZE, SUPERSEDE or DEFER once in the existing PR; no classification
paperwork is needed for routine fixes. Required product functions and privacy,
governance, history and bootstrap invariants cannot be silently dropped.
