# AdministrateMe control-plane postmortem

Date: 2026-08-25

Status: resolved by the subtractive reboot defined in ADR-0013

## Executive finding

The V2 product did not stall because its event-sourced architecture or deployed
runtime became unsalvageable. It stalled because delivery governance became a
self-referential product of its own. Work that should have been one branch, one
pull request, and ordinary CI was split across user-created tasks, exact commit
identities, exact authorization phrases, imported artifacts, staged labels,
separate reviewers, repair agents, release agents, and evidence-only follow-up
pull requests. A normal correction changed the candidate head and invalidated
the proof, which restarted the same sequence.

The safe remedy is not a repository reset. The last main commit before G-001
still contained the earlier Claude executor loop, and the Outcome Harness merge
also contained useful product navigation and browser-test improvements. The
chosen recovery is a semi-greenfield control-plane reboot on current `main`:
retain the product and operational safety, remove both generations of delivery
orchestration, and make protected GitHub CI the only deterministic merge gate.

## Timeline and repository facts

| Event | Repository coordinate | Effect |
|---|---|---|
| Last main before G-001 and Outcome Harness | `5fe402d622ea101b7280e47c6cd272b79a751066` | Compact V2 rules, but older Claude executor/task-loop machinery still existed. This is a historical reference, not a safe rollback target. |
| G-001 introduced | feature `8d7ec87ed2e4b96cc19646915880d9af1df0a93f`, merged as `499d58e57c565d940ab3ac0ad03b4070a2a70c0f` in PR #332 | Added exact-head, separate-review-task, exact-phrase, guarded-release, and evidence-PR requirements. It changed governance and documentation, not product runtime. |
| Outcome Harness introduced | feature series beginning `2e90e5f0aefde4daa973ea84516fe83508a351fe`, merged as `cd08b50cb6295cfc1425a6a02c4165ea1a2126ce` in PR #340 | Added repository skills, schemas, artifact orchestration, generated current state, binding rules, and artifact-flow tests. The same merge also contained useful member-navigation and browser-test work. |
| G-002 proposed | `b9e9725d98d4d7528bc4c61a72b05bf62248eba3` through `2704d2d010fbb4d60612fd088759a97f984872f7`, closed PR #343 | Added more autonomous review and repair orchestration, but never merged to `main`; no runtime rollback is needed. |
| Recovery ADR-0012 proposed | closed PR #344 | Tried to repair the staged control plane while preserving its separate-task and exact-authorization assumptions. It improved parts of the mechanism but could not remove the loop. |
| Simple delivery reboot | branch `codex/v2-control-plane-reboot` from main `88bd571881bc0636ff90b12cb906626018978563` | Removes the orchestration layers while retaining every merged V2 product/runtime change. |

## What happened

### 1. Governance became recursive

G-001 required a separate, non-implementing, user-visible review task bound to
an exact head. Material changes required a fresh review. After merge, another
exact review and evidence-only transaction could be required. The system could
prove that a candidate matched a prior review, but it could not cheaply accept
the ordinary fact that fixing a reviewed problem creates a new commit.

### 2. The harness encoded the recursion

The Outcome Harness made the process executable through repository skills,
JSON schemas, artifact import and reconciliation code, current-state
generation, final-gate locks, and tests of the ceremony itself. Those checks
were internally coherent. That coherence made the loop harder to escape:
agents correctly followed the repository and therefore reproduced the failure
mode.

### 3. Actor assumptions did not match the available system

The policy treated a separate Codex task as an independent approval identity.
In reality, a subagent can provide useful independent analysis but is not a
separate GitHub account, and same-account GitHub approval is not independent.
The user became the message bus between agents because the policy disqualified
the implementation task's own subagents and required a user-visible task.

### 4. Deterministic checks and semantic review were conflated

GitHub Actions can deterministically test a commit. It cannot supply semantic
judgment. Conversely, semantic review can find bugs but should not require a
custom artifact protocol to be useful. Treating both as one exact-bound proof
system caused every code fix to invalidate both kinds of evidence.

### 5. Delivery safety and live safety were coupled

Valuable runtime protections—observation mode, encrypted backup, rollback,
tenant isolation, Doctor, and provider-write denial—became entangled with PR
ceremony. Removing the ceremony appeared to threaten deployment safety even
though the two concerns are separable.

### 6. Historical instructions remained searchable and active-looking

Before G-001, the repository already contained an executor-cycle generation:
prompt sequences, pause gates, split memos, partner instructions, review
schemas, and setup runbooks. Resetting to the pre-G-001 tree would therefore
restore an older version of the same organizational problem.

## Why progress appeared impossible

The loop had a stable failure cycle:

1. An implementation candidate was produced.
2. A separate exact-head review was requested through the user.
3. The review found or requested a correction.
4. The correction changed the head and invalidated the review binding.
5. Repair and release stages required new artifacts and another reviewer.
6. Evidence about the transaction created more follow-up work.
7. Repository instructions forced the next agent to restart at step 2.

This was not ordinary perfectionism. It was a liveness defect in the delivery
protocol: the process had many safe stopping rules but no cheap forward path
after a legitimate modification.

## How it could have been prevented

- Keep one current delivery authority. Historical procedures should live in
  Git history or be unmistakably frozen, never beside active instructions.
- Require stable GitHub checks, not custom exact-head evidence artifacts. A
  changed commit should rerun CI on the same pull request.
- Treat human or subagent review as advisory unless a genuinely independent
  GitHub identity and a clearly justified protection rule exist.
- Separate merge authority from deployment authority. Merging code does not
  authorize touching the family Mac, credentials, providers, or observation
  state.
- Preserve operational safety at the deployment boundary, where backup,
  rollback, Doctor, isolation, browser acceptance, and zero-effects evidence
  actually matter.
- Avoid mixing new governance machinery with product improvements in one
  commit series. A mixed merge made wholesale reversion unsafe.
- Add a liveness criterion to process design: a valid bug fix must have a
  bounded path to green without restarting the whole protocol.
- Prefer the smallest control plane that enforces a real threat. Each new role,
  artifact, label, phrase, or follow-up PR should have a demonstrated failure
  it prevents that ordinary protected GitHub delivery cannot prevent.

## Recovery decision

### Rejected: hard rollback

A reset to `5fe402d...` would discard later product/runtime work and revive the
older executor loop. Reverting PR #340 wholesale would also remove useful
member-navigation and browser-test changes. Both options create more product
risk than they remove.

### Rejected: greenfield product rebuild

The V2 runtime, tests, deployment safeguards, adapters, projections, products,
bridge, packs, and OpenClaw seam remain coherent and broadly verified. Rewriting
them would throw away validated behavior without addressing the actual cause.

### Chosen: semi-greenfield control-plane reboot

The reboot starts from current main, selectively removes delivery machinery,
retains product/runtime changes, and introduces one simple authority chain:

1. one current-main `codex/` branch;
2. one pull request;
3. protected `deterministic-consistency` CI;
4. optional advisory subagent or human review;
5. ordinary owner merge authorization;
6. a separate owner request before any guarded observation deployment.

## What the reboot removes

- G-001/ADR-0009 active authority and exact-phrase/exact-head requirements;
- Outcome Harness repository skills, schemas, artifact code, generated state,
  final-gate locks, and artifact-only tests;
- the unmerged G-002 and ADR-0012 recovery paths;
- legacy executor runbooks, prompt queues, pause gates, partner instructions,
  review schemas, and active-looking task-loop documents;
- nondeterministic dependency audits from the deterministic merge signal.

The retired material remains recoverable in Git history. Product navigation
coverage formerly colocated with harness tests is retained as ordinary tests.

## What the reboot preserves

- all merged V2 runtime and product behavior;
- the event-log, projection, pipeline, adapter, bridge, pack, product, and
  OpenClaw dependency boundaries;
- privacy and tenant isolation;
- provider allowlist/governance/rate-limit/final-guard ordering;
- observation mode and zero unintended provider effects;
- encrypted backup, guarded updater, rollback, bounded settlement, Doctor, and
  browser acceptance;
- private repository, protected main, merge commits, pinned GitHub Actions, no
  force push, no branch deletion, admin enforcement, and conversation
  resolution.

## Verification standard

The reboot is acceptable only if ordinary product checks remain green: the full
root suite, strict acceptance suite, bridge suite, Ruff, formatting, mypy,
Pyright, invariant and identity scans, OpenClaw contract tests, shell checks,
and the Gate-7 browser suite. GitHub reruns the required deterministic suite on
the pull request. The macOS rehearsal remains pre-deployment evidence because
it is path-selective and is not currently a branch-protection requirement.

## Residual risks

- Removing mandatory semantic review reduces one defense against requirements
  mistakes. Broad CI, optional advisory review, protected merge, and the
  separate deployment boundary mitigate this without recreating the loop.
- Generic scanning cannot infer every real personal name. Repository policy,
  generic contact/address rules, and an optional owner-only exact-pattern file
  cover the practical boundary without storing private canaries in Git.
- Historical commits and closed PRs still contain the retired instructions.
  They are immutable provenance, not current authority; branches, labels, and
  orphan workflow registrations should be cleaned after the replacement PR is
  established.
- A governance-only reboot changes no deployed runtime. It should not be
  deployed to the family Mac merely to make documentation match GitHub.

## Outcome

The project is fixable without a product restart. The central lesson is that
the repository was not lacking safeguards; it lacked a bounded path through
them. ADR-0013 restores that path while keeping the safeguards that protect
household data and live operations.
