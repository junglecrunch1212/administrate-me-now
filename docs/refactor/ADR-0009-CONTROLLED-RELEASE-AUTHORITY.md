# ADR-0009 — Controlled release authority

Status: **ACCEPTED BY OWNER; EFFECTIVE ONLY AFTER MANUAL GOVERNANCE MERGE**

Date: 2026-08-10

Decision owners: household principals and AdministrateMe product/engineering

Governing approval: G-001 — Controlled Release Authority

## Context

AdministrateMe correctly separates disposable Phase A engineering from Phase B
activation on the family Mac. The repository instruction “Do not merge” kept an
implementation task from silently turning green tests into a production
change. It also created a governance deadlock: a fully reviewed candidate could
not enter `main`, while Phase B was constitutionally forbidden before merge.

A blanket instruction to merge and deploy is not an acceptable repair. It is
not bound to a PR, immutable commit, current base, reviewer, test result, live
baseline, or rollback plan. It can also outlive the circumstances under which
it was given. Household-scale operation needs fewer people, not fewer release
controls.

## Decision

Release authority remains deny-by-default. A repository owner may activate one
controlled release with the exact current-task instruction:

```text
Authorize guarded release of PR #<number> at head <40-character-commit>.
```

The authorization is a capability bound to one PR, immutable head, and release
transaction. It is not inherited by another task, branch, SHA, pull request,
runtime migration, provider enrollment, write enablement, or unrelated repair.
The G-001 governance PR cannot use G-001 to merge itself; the owner performs
that one-time bootstrap merge manually. The exception exists only after this
ADR and the matching `AGENTS.md` rule are on `main`.
A controlled-release task starts only after that merge so the effective
instructions are loaded at task creation. A task that began under the former
absolute no-merge rule cannot acquire authority merely by rereading a changed
worktree.

### Phase A preconditions

All conditions are conjunctive:

1. A human other than the implementation author, or a separate user-visible
   Codex task that did not implement the candidate, reviews the exact head. An
   implementation subagent is not independent review. Human approval is a
   GitHub review. A separate Codex task leaves a content-free PR comment naming
   its task/thread, SHA, reviewed scope, commands, findings, and conclusion.
2. The candidate contains current `origin/main`; the PR is clean and mergeable;
   every actionable review thread is resolved; and no unrelated or household-
   identifying change is present.
3. Every contractually required local, production-composition, browser,
   package, privacy/isolation, backup/restore, and hosted gate passes on the
   final head. A passing narrow test never substitutes for the release suite.
4. If `main` advances, it is merged into the candidate. Rebase, force-push, and
   history rewriting are forbidden. The resulting new head receives fresh
   independent review and a complete gate rerun.
5. The release operator re-verifies the authorization's PR number and exact
   head immediately before a GitHub merge commit. Direct pushes to `main`,
   branch-protection bypasses, and a different merge target are forbidden.

Any material head change invalidates prior review, checks, and authorization.
The owner must issue a new exact-head instruction after the replacement head is
ready; a model cannot infer that the old approval still applies.

### Phase B transaction

Phase B begins only after the authorized PR's merge commit is present on
`origin/main`. The release transaction then:

1. checks out and attests the immutable merged SHA;
2. records the current release and health baseline without exposing content;
3. keeps observation mode active and refuses provider-write enablement;
4. creates and verifies the encrypted pre-switch backup;
5. uses only the reviewed guarded updater and its automatic rollback path;
6. performs bounded settlement and full Doctor through the selected release;
7. checks member/privacy isolation, backup and rollback evidence, and zero
   unintended provider effects; and
8. performs authenticated read-only browser acceptance for the changed member
   behavior before calling the deployment complete.

Liveness is necessary but insufficient. A new degradation, stale or ambiguous
proof, mismatched controller/SHA, failed backup, loss of observation mode,
unexpected permission prompt, settlement failure, Doctor failure, privacy
failure, browser mismatch, or provider effect stops the transaction. The
guarded updater rolls back where activation has begun. The operator preserves
content-free diagnostics and does not edit the installed release, active venv,
LaunchAgents, encrypted state, permissions, or provider configuration.

Runtime or dependency migration, Keychain migration, provider enrollment,
external-write activation, and unrelated repair always require their own named
review and approval. They cannot be smuggled into a product release to make a
gate pass.

### Release evidence closure

One follow-up evidence PR is part of the authorized transaction. Its diff is
restricted to governing documentation and content-free observed results. It
must name the reviewed head, merge commit, deployed release, backup receipt,
Doctor/settlement result, browser acceptance, rollback state, and provider-
effect result. Hosted checks and an exact docs-only diff must pass before it is
merged, and a non-author human or separate Codex review task must review that
exact evidence head. Any code, configuration, dependency, lock, test, or
runtime change requires a new ordinary PR and authorization.

## State model

```text
DENY (default)
  -> INDEPENDENT REVIEW + FINAL-HEAD GATES
  -> OWNER AUTHORIZATION (exact PR + exact head)
  -> GITHUB MERGE COMMIT ON MAIN
  -> GUARDED OBSERVATION ACTIVATION
  -> SETTLEMENT + DOCTOR + LIVE ACCEPTANCE
  -> DOCS-ONLY EVIDENCE MERGE
  -> COMPLETE
```

At any mismatch the state becomes **STOPPED** before merge or **ROLLED BACK**
after activation. Neither state automatically returns to an authorized state.

## Consequences

- An implementation task can no longer self-authorize a merge from a vague or
  historical instruction.
- A household principal can complete a release without maintaining a permanent
  second operator, while independent review remains real and auditable.
- Base drift and candidate edits invalidate approval instead of silently
  changing the reviewed object.
- Product release authority cannot mutate runtimes, credentials, permissions,
  providers, or observation policy opportunistically.
- The repository contains the release receipt needed to distinguish “code is
  green,” “code is merged,” “release is active,” and “release is verified.”

## Classification

- **PRESERVE:** deny-by-default merge posture, Phase A/Phase B separation,
  immutable reviewed releases, tests, privacy, observation, backup, Doctor,
  rollback, and provider-write governance.
- **MODERNIZE:** replace an absolute deadlock with a one-PR, one-SHA,
  owner-authorized release capability and auditable independent Codex review.
- **HARDEN:** bind base drift, exact head, hosted gates, merge method, updater,
  live proof, and evidence closure into one fail-closed transaction.
- **SUPERSEDE:** blanket chat-level merge/deploy overrides and inferred release
  authority.
- **DEFER:** nothing required by G-001. Each product release still supplies its
  own semantic and live acceptance evidence.
