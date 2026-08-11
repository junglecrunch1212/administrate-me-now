# G-001 controlled release authority evidence

Status: owner-approved governance candidate; activation requires the owner's
manual merge because G-001 cannot authorize itself.

## Requirement matrix

| Requirement | Governing evidence |
|---|---|
| Default remains no merge | `AGENTS.md` denies merge, direct `main` push, and Phase B unless every G-001 condition is satisfied. |
| Exact user authority | One current-task phrase binds a PR number and 40-character immutable head; branch, change-order, inferred, prior, or blanket approval is invalid. |
| Independent review | ADR-0009 requires a non-author human GitHub approval or a separate user-visible, non-implementing Codex task's content-free exact-head review receipt; an implementation subagent is insufficient. |
| Base and head integrity | Current `origin/main` must be contained; base drift requires a merge commit into the candidate, fresh review, fresh gates, and new exact-head authorization. |
| Complete gates | Narrow tests cannot substitute for the repository release suite, production composition, browser, privacy/isolation, backup/restore, package, and hosted checks. |
| Safe merge | Only the named GitHub PR and authorized head may create a merge commit; direct push, history rewrite, force-push, protection bypass, and combined authority are forbidden. |
| Guarded Phase B | Only a merged `main` SHA may enter the reviewed updater after baseline evidence, observation retention, and a verified encrypted backup. |
| Fail-closed activation | Any new degradation or mismatch stops or rolls back; installed releases, active environments, LaunchAgents, encrypted state, permissions, and provider config are never hand-edited. |
| Live completion | Settlement, full Doctor, isolation/privacy, rollback/backup, authenticated read-only browser acceptance, and zero unintended provider effects are mandatory. |
| Evidence closure | One independently reviewed, hosted-green, exact docs-only follow-up may record the release; any executable or configuration change needs a new PR and authorization. |
| Bootstrap boundary | The governance PR cannot merge itself. G-001 becomes effective only after the owner manually places it on `main`. |
| Fresh-task boundary | A controlled release begins in a task created after G-001 is on `main`; a task governed by the former rule cannot promote itself by rereading the file. |

## Candidate validation boundary

The governance PR must change only `AGENTS.md` and `docs/refactor/` governance
records. It must contain no application, bootstrap, bridge, pack, plugin,
workflow, configuration, dependency, lock, test, fixture, or household data.
Validation records the exact diff, `git diff --check`, invariant scan, and
hosted constitutional/macOS checks. Those checks prove the governance artifact
is internally consistent; they do not authorize its own merge or a product
release.

## Candidate validation

The isolated candidate based on `origin/main` changes exactly seven allowlisted
governance files: `AGENTS.md`, ADR-0009, AC-21, C081, the release handoff, the
migration map, and this evidence record. Semantic assertions cover every owner-
approved G-001 clause, including the self-bootstrap and fresh-task boundaries.

```text
git diff --check
PASS

governance scope allowlist and semantic requirement assertions
PASS — 7/7 files governance-only; no unexpected path

bash scripts/verify_invariants.sh
PASS
```

The final candidate head still requires the two ordinary hosted PR checks. A
green hosted result is review evidence only; the owner remains the sole actor
permitted to perform this policy's one-time manual bootstrap merge.
