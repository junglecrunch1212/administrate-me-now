# ADR-0013 — Simple GitHub delivery

Status: accepted; amended by owner direction on 2026-10-22 and 2026-10-23

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
6. Deploy only after merge with explicit owner authorization. One ordinary-language request may authorize both operations; do not require another confirmation when deployment is already covered.

The repository removes the Outcome Harness code, schemas, repository skills, generated current-state file, legacy executor instructions, retired prompt sequence, and their artifact-only tests. Product navigation tests remain as ordinary unit and production-composition tests. The required GitHub check keeps its existing `deterministic-consistency` name so branch protection continues without a transition gap.

The ordinary privacy scan rejects machine-specific instance paths throughout the Git-eligible tree and non-synthetic email, phone, and street-address literals in platform, bootstrap, bridge, pack, script, test, and OpenClaw files. An owner may optionally add exact private-name literals from an owner-only file outside Git; arbitrary personal names cannot be reliably identified without such a list, so the repository policy and review still prohibit them. The optional list is not a merge or deployment prerequisite.

## Fast delivery amendment — 2026-10-22

The owner accepts reversible runtime breakage to reduce shipping latency and
token spend. Apply [AGENTS.md](../../AGENTS.md) and the current scope rules in
[the acceptance contract](ACCEPTANCE-CONTRACT.md): focused checks and a small
smoke test can be final release evidence for an ordinary reversible change.
Ship when authorized, observe the affected outcome, roll back on regression,
and add the regression coverage exposed by that failure before redeployment.
Full Linux/macOS runs, global permission/privacy/recovery sweeps, new impact
maps, draft/ready cycles and repeated review/reporting are not default steps.

Prevention remains necessary for the affected irreversible contract: private
data, ownership, credentials, durable history and external sends. Keep those
runtime controls enforced and test them when changed. The retained predecessor,
compatible state, supported rollback and bounded health/use checks make
reversible failures tolerable; rollback is included in the release's deployment
authorization. Provider enrollment, permissions and write enablement still need
explicit authority, which may already be present in the owner's request.

Shape each request into the smallest complete usable outcome before choosing
work: diagnose existing authentication/configuration first, preserve requested
capabilities, test the failing production boundary and verify the actual result.
Reuse existing authorization; ask only for a real missing decision or permission.
A known in-scope blocker calls for implementation, not another offer to help.
Record a failed release and its recovery briefly in the same task. Completing
one outcome does not make later user-added work a reason to reopen it.

This is a policy redraft, not a CI or updater implementation. Read the current
selector/workflows and GitHub protection for enforced scopes and gates; the
acceptance contract records the remaining updater limitations. Narrower paths
need protected changes with focused gate/contract tests. Never bypass controls
or treat a prose amendment as a passing result.

## Small deliverables amendment — 2026-10-23

The three repair tasks demonstrate useful scope correction, not measured token
or time savings. See the acceptance contract's
[observed lessons](ACCEPTANCE-CONTRACT.md#small-deliverables-and-observed-lessons).
Policy should turn a short owner request into the next usable outcome, proof
and recovery path without asking the owner to approve ordinary implementation
choices. Preserve the whole ambition as a short sequence; ship each independent
outcome through its authorized stages before adding unrelated scope. One branch
and PR apply per deliverable, not to every future feature added to one chat.

A failed attempt changes the next reproduction and fix; it does not automatically
expand every check. Keep irreversible contracts together with the outcome,
recover promptly, and retain schema-aware history when an older binary is unsafe.
Verify the effective running tools, grants and instructions, not merely the
merged source. Replace obsolete guidance instead of accumulating process layers.

## Supersession

This ADR supersedes ADR-0009 and all active G-001 process clauses. It retires the Outcome Harness and the proposed, unmerged G-002 and ADR-0012 recovery processes. The 2026-10-22 and 2026-10-23 amendments also supersede conflicting blanket validation, readiness and evidence obligations in earlier standing guidance, this ADR, acceptance amendments and runbooks. Product behavior and runtime authorization contracts remain intact. Historical Git commits, closed pull requests, and past deployment receipts remain factual records but are not instructions for current work.

## Consequences

- Product work can advance through one branch and one pull request without owner copy-paste between reviewer tasks.
- Ordinary reversible changes use focused verification; broader validation needs an affected behavior or concrete risk. Advisory review remains optional.
- A failed selected required check blocks merge but does not create a new governance transaction; unrelated improvements do not block shipping.
- Live-family protections remain independent of development-process ceremony.
- The older executor and staged-control-plane files remain available in Git history only and must not be restored as active authority.

## Classification

- **PRESERVE:** V2 runtime architecture, product behavior, privacy, observation, backup, rollback, and provider-write safety.
- **MODERNIZE:** protected pull-request delivery, scoped checks, evidence reuse, short reports and prompt rollback after an authorized release.
- **SUPERSEDE:** G-001, Outcome Harness, proposed G-002/ADR-0012, exact-head artifacts, exact authorization phrases, staged labels, separate review tasks, evidence-only follow-up pull requests, blanket full-release validation, mandatory readiness paperwork and repeated promotion runs.
- **DEFER:** no required product function or safety invariant.
