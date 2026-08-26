# ADR-0013 — Simple GitHub delivery

Status: accepted

Date: 2026-08-25

Postmortem: `docs/refactor/CONTROL-PLANE-POSTMORTEM.md`

## Context

AdministrateMe successfully reached a working V2 runtime, but its delivery process accumulated two generations of orchestration: the earlier Claude executor loop, followed by G-001 exact-head authority and the Outcome Harness. The later controls required separate user-created review tasks, exact phrases and commit identities, artifact imports, staged labels, repeated proof invalidation, and evidence-only follow-up work. In practice, maintaining proof about the process displaced work on the product and made ordinary corrections restart the loop.

There is no safe historical commit to restore wholesale. The last main commit before G-001 still contains the older executor machinery, while the Outcome Harness merge also contains useful member-navigation and browser-test improvements. The reboot is therefore subtractive and begins from current `main`.

## Decision

GitHub is the only delivery control plane:

1. Start one `codex/` branch from current protected `main`.
2. Implement, test, and update one pull request.
3. Use required GitHub Actions checks as deterministic gates. Fix failures in the same branch and let GitHub rerun them normally.
4. Allow the implementation task to use bounded subagents or ordinary human review as advisory help. Neither requires a separate user-visible task or artifact import.
5. Accept an owner merge decision in ordinary language. Merge only through the protected pull request; never push directly, force-push, or rewrite published history.
6. Treat deployment as a separate operation requiring a clear owner request after merge.

The repository removes the Outcome Harness code, schemas, repository skills, generated current-state file, legacy executor instructions, retired prompt sequence, and their artifact-only tests. Product navigation tests remain as ordinary unit and production-composition tests. The required GitHub check keeps its existing `deterministic-consistency` name so branch protection continues without a transition gap.

The ordinary privacy scan rejects machine-specific instance paths throughout the Git-eligible tree and non-synthetic email, phone, and street-address literals in platform, bootstrap, bridge, pack, script, test, and OpenClaw files. An owner may optionally add exact private-name literals from an owner-only file outside Git; arbitrary personal names cannot be reliably identified without such a list, so the repository policy and review still prohibit them. The optional list is not a merge or deployment prerequisite.

## Operational safety retained

This ADR simplifies delivery but does not weaken the product or live-operation boundary. A deployment still uses an immutable commit from protected `main`, the guarded updater, observation mode, a verified encrypted backup, automatic rollback, bounded settlement, full Doctor, privacy and tenant-isolation checks, authenticated read-only browser acceptance, and zero unintended provider effects. Provider enrollment or writes, credentials, permissions, Keychain changes, and runtime/dependency migrations require separate explicit authorization. Hosted CI never receives live household credentials or deployment authority.

## Supersession

This ADR supersedes ADR-0009 and all active G-001 process clauses. It retires the Outcome Harness and the proposed, unmerged G-002 and ADR-0012 recovery processes. Historical Git commits, closed pull requests, and past deployment receipts remain factual records but are not instructions for current work.

## Consequences

- Product work can advance through one branch and one pull request without owner copy-paste between reviewer tasks.
- Code changes still receive broad automated verification and optional advisory review.
- A failed check blocks merge but does not create a new governance transaction.
- Live-family protections remain independent of development-process ceremony.
- The older executor and staged-control-plane files remain available in Git history only and must not be restored as active authority.

## Classification

- **PRESERVE:** V2 runtime architecture, product behavior, privacy, observation, backup, rollback, and provider-write safety.
- **MODERNIZE:** protected pull-request delivery with ordinary CI and optional bounded review.
- **SUPERSEDE:** G-001, Outcome Harness, proposed G-002/ADR-0012, exact-head artifacts, exact authorization phrases, staged labels, separate review tasks, and evidence-only follow-up pull requests.
- **DEFER:** no required product function or safety invariant.
