# G-001 staged control-plane recovery evidence

Status: published as draft PR #344; independent exact-head review and final
gates pending.

## Read-only post-mortem findings

- GitHub `main` remained the proven V2 line while the active family release
  stayed on its prior reviewed immutable selector; G-002 was never merged,
  enrolled, or deployed.
- PR #342 was a 23-file product repair repeatedly revised after semantic
  findings. PR #343 was a 43-file, roughly 6,900-line governance expansion with
  nine exact-head priority findings despite green deterministic suites.
- Current-main independently passed the broad root and strict acceptance
  suites. The G-002 head independently passed broader root, acceptance, static,
  invariant, and OpenClaw checks. Green tests therefore did not distinguish a
  safe release-authority design from a flawed one.
- The latest large repair had no durable commit and its temp-bound harness
  directory was later empty. Task count, names, and reported effort were not
  recoverable evidence.
- The original local project workspace was behind GitHub `main` and contained
  protected untracked user state. Recovery therefore began in a new clean saved
  worktree without modifying or deleting that state.
- GitHub `main` had no branch protection. The repository had no forks, stars,
  subscribers, or Pages site and was made private on 2026-08-25 to contain
  tracked household-identity canaries without rewriting history.

## Recovery publication and repository controls

- Draft PR #344 is the only active non-dependency recovery PR. PRs #342 and
  #343 were closed and labeled `superseded-recovery-evidence`; their commits,
  discussion, and exact-head findings remain recoverable.
- GitHub `main` now requires the strict `deterministic-consistency` status on
  the current base, bound specifically to GitHub Actions app ID 15368, and
  PR-based delivery, enforces the rule for administrators,
  dismisses stale reviews, requires conversation resolution, forbids force
  pushes and deletion, and retains merge commits for G-001.
- Squash, rebase, and auto-merge are disabled. Repository Actions are limited
  to GitHub-owned `actions/*`; every workflow action is pinned to an immutable
  commit before repository SHA-pinning enforcement is enabled.
- The `final-gates` label exists but is not applied. The final hosted macOS job
  therefore skips before independent review while cheap consistency runs.

## Candidate controls

| Failure mode | Candidate control |
|---|---|
| Stale or ambiguous base | Read-only `workspace-status --check` compares branch, HEAD, local `origin/main`, remote GitHub `main`, and ancestry. |
| Temp evidence loss | `create-run` defaults to a private durable host-local root and still rejects repository-contained runs and symlink run paths. |
| Review occurs after full cost | `record-review` requires exact passing static/focused/journey receipts; CLI breadth/browser/release gates and the labeled final hosted macOS job require exact-bound approval. |
| Green tests imply safe authority | Independent review remains a separate G-001 identity and covers all eight existing scopes. |
| Generic degraded-baseline waiver | ADR-0012 permits only domain-specific, target-bound, monotonic, observation-safe, rollback-capable repair contracts. |
| Identity canary leaks its subject | Repository source contains no household literal list; optional owner-private patterns are `0600`, and results are content-free coordinates. |
| Multi-task loop becomes state | One recovery PR is the status surface; stalled tasks and G-002 are superseded evidence only. |

The first durable self-run also caught and repaired a pre-review CLI defect:
`run-tier focused` had ignored the bound changed-file inventory unless the
operator repeated every path, causing fallback selection. It now defaults to
the immutable verification scope; an explicit path list remains an intentional
override. The pre-repair receipts are historical only and cannot be imported
after the changed binding.

No release, deployment, live Doctor, provider action, observation change,
runtime migration, history rewrite, or family-Mac experiment was performed by
this candidate.
