# AdministrateMe engineering instructions

This repository is a household-scale, local-first, event-sourced platform. Treat documentation and comments as evidence; executable behavior and tests decide whether a change works. The v2 reconciliation in `docs/refactor/` governs product architecture. The owner-directed fast-delivery policy of 2026-10-22, refined on 2026-10-23, supersedes conflicting release, testing, readiness and reporting requirements in older instructions, change orders and runbooks. Historical delivery procedures are not current authority.

## Working rules

- Start each independently deliverable change from current protected `main`, using one `codex/` branch and one pull request. Continue an existing open repair PR instead of duplicating it. An ambitious request may need a sequence of small PRs in the same task. Preserve unrelated changes; never rewrite published history, force-push, or push directly to `main`.
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

## Shape tasks around usable outcomes

Treat the CoS as staff with its own internal workspace. Within current grants,
it may combine authorized sources for routine coordination and keep private
notes, memory and scratchpad work with source provenance, without asking again
for work the standing grant already covers. Derived work retains each client's
confidentiality and source restrictions; recheck authority when reusing it.
Access to an account, device or work computer is not authority to disclose its
contents. Cross-client or external disclosure requires authority for the source,
content and audience and must honor explicit client confidentiality. This does
not enroll accounts, widen grants or bypass independent sending approvals.

Use policy to improve the working prompt without making the owner rewrite it.
In one to three sentences, state the first complete usable outcome, how to prove
it, and how to recover. Preserve the full request and existing authorization;
state reasonable assumptions and proceed. For a larger ambition, name a short
sequence of outcomes and detail only the next one. This is not a form, planning
round, new chat, or approval gate. Examples and observed lessons are in
[the acceptance contract](docs/refactor/ACCEPTANCE-CONTRACT.md#small-deliverables-and-observed-lessons).

- Slice by usable behavior and recoverability. Prefer one connected account,
  one working reply path, or one service's complete approved action through
  read-back. Keep the UI, permission, audit and recovery needed for that outcome
  together. A schema, API or permission toggle alone is not a usable feature;
  a necessary enabling change must name the outcome it unlocks.
- When a proposed fix adds another service, device, runtime, permission class,
  persistent format or deployment component, split it into a later deliverable
  unless it is essential to the current outcome. Keep requested later work
  visible; do not silently drop it, call partial work complete, or build a
  general workflow framework for one bounded need. Continue authorized slices
  without asking for permission again at each milestone.
- Diagnose before rebuilding. For a broken connection, inspect authentication,
  granted scope, enabled policy, acquisition and visible data using supported
  read-only diagnostics. Prefer an existing reconnect/configuration operation
  when it achieves the request. Check complete permitted tool compositions
  before declaring a missing named tool fatal. Introduce code only for an
  established defect or missing capability; explain that boundary briefly.
- Use permission already given. A request to reconnect a named account authorizes
  its supported reconnection within the requested scope; it does not require a
  second permission just to choose that narrow path. Preserve authorized read
  and write capabilities. Ask only for unresolved account/scope choices,
  unavoidable OAuth/device consent, or an operation not yet authorized. If new
  code/deployment authority is unclear, prepare a concrete bounded repair and
  ask once for the remaining authority, including its recovery. A diagnostic
  question alone is not deployment or send authorization. Another adult owns
  their own account/device consent. Choosing a narrow implementation within an
  authorized outcome needs no additional permission.
- Verify the user's outcome: authenticated sync and visible permitted data for
  connections; a real authorized response path for replies; resumed acquisition
  for a bridge. Login success, a saved setting, passing CI or generic health is
  not proof of those outcomes. Test the actual failing production boundary in
  isolation before the next authorized attempt. Do not send test messages or
  perform provider writes without permission.
- Keep independent work independent. Identify a concrete shared interface or
  deployment conflict before making one task wait for another. Prepare code and
  checks concurrently, serialize conflicting live mutations, and reuse unchanged
  component evidence. Do not modify another task's component to clear unrelated
  CI. Combine already-ready compatible changes only when this avoids a real
  shared maintenance cycle; do not wait for unrelated features or device consent.
  Use one monitor and one operator for a shared release, without duplicate
  polling. Read progress as needed; messaging another chat needs authorization.
- Fix necessary in-scope blockers instead of ending with an offer to do so.
  Preserve the full requested result when slicing work; do not silently reduce
  it to read-only access or a code-only deliverable. Close each completed outcome
  clearly. Later user-added work becomes the current bounded outcome without
  reopening completed milestones or requiring a new chat. When a failure or
  request changes the plan, update these few outcome sentences rather than
  expanding a permanent checklist. Read only the affected contract and relevant
  evidence; replace obsolete policy instead of accumulating overlapping rules.

## Deployment safety

A deployment operates on an immutable merge commit from protected `main`, using the supported updater or component installer, a known-working predecessor, compatible retained state, and a concrete rollback command. Keep the existing observation and external-write controls. Reuse valid backup/recovery evidence under its existing freshness and compatibility rules; require new restore testing when the change affects persistence or recovery. Do not manufacture a central deployment for an independently installable component.

After activation, check liveness/readiness and the changed user outcome within a bounded observation window. Check actual member rendering when UI behavior changed. Roll back promptly on a new runtime or workflow regression, verify predecessor health, then fix and retest the affected path in isolation. Rollback of that release is included in deployment authorization. Full Doctor, whole-household isolation sweeps and recovery rehearsals are not universal policy prerequisites for each narrow change; run them when the change or a concrete failure makes them relevant.

Record the candidate/configuration identity, expected versus observed result,
a redacted error or existing log reference, and recovery outcome in the same
PR/task. Capture only what is needed and do not delay recovery for a report.
If the original fault remains without a new regression, the repair is incomplete;
returning to an already-broken predecessor cannot establish success. Preserve
the healthiest supported state and use the failure to target the next fix.
Prepare artifacts and checks before starting short-lived handoffs or asking for
manual device consent; honor actual expiry without repeatedly wasting its window.
For a state or sending change, decide before enablement whether recovery is a
binary rollback or feature revocation on the new schema-aware runtime. Include
that boundary in the slice; retained history may make an older binary unsafe.
Use a bounded affected-workflow check with a concrete failure signal. On failure,
recover first and reproduce the observed cause in isolation; do not retry the
same release, refresh consent or restart broad suites without new evidence.
Verify effective runtime tools, hooks, grants and installed instructions where
the change depends on them. A merged template or saved configuration does not
prove that the running agent loaded it. Restore already-authorized operation
after maintenance through the supported activation path, preserving exact grants.

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

Read `scripts/release_scope.py` and current GitHub protection for implemented
scopes and required statuses; do not maintain a competing list in prompts.
Unknown inputs still use the protected fallback. A missing scope is an
implementation gap to fix through a protected PR with positive and negative
selection tests, not an excuse to bypass protection or add manual full suites.
Each selected result must actually pass; missing, failed, cancelled, stale or
unexpectedly skipped evidence is not success. Preserve useful unrequired runs,
but do not wait for unrelated aggregates after required and affected checks pass.
Policy-only edits need static checks and no central deployment. Executable
updater and CI requirements remain enforced until legitimately changed.

## Definition of done

Deliver the requested behavior with the selected checks and state remaining
limitations honestly. Distinguish checked, merged, deployed and verified usable;
claim only the stages actually completed. For a small change, a concise result,
validation and any deployment blocker are enough. Update governing contracts
when behavior changes. Classify material contract changes as PRESERVE,
MODERNIZE, SUPERSEDE or DEFER once in the existing PR; no classification
paperwork is needed for routine fixes. Required product functions and privacy,
governance, history and bootstrap invariants cannot be silently dropped.
