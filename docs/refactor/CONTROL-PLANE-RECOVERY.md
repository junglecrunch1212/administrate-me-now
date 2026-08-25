# Control-plane recovery front door

Status: candidate workflow; no merge, deployment, or live repair is authorized.

Active status surface: GitHub draft PR #344. PRs #342 and #343 are closed as
superseded recovery evidence and must not be resumed.

GitHub delivery permits merge commits only, binds the required consistency
status to GitHub Actions, restricts Actions to immutable-pinned GitHub-owned
actions, and applies `main` protection to administrators.

## For the owner

Use one Codex task for implementation and one separate Codex task only when an
independent exact-head review is requested. Treat the active recovery pull
request as the only status page. Do not resume the older Builder, Reviewer,
Repairs, Guarded Release, exact-head, or G-002 tasks.

Codex owns repository inspection, branch/worktree setup, implementation,
commands, commits, pushes, PR creation, hosted-check observation, review
artifact preparation, and safe GitHub configuration. The owner supplies only:

1. a separate user-visible review task when prompted;
2. the exact G-001 authorization phrase for the final PR/head after every gate
   and review is current; and
3. any macOS permission, Keychain, Tailscale, or physical-device action that
   cannot be performed in the disposable Phase A environment.

## Candidate stages

| Stage | Required proof | May proceed to |
|---|---|---|
| Workspace | Current GitHub `main` is contained by one saved `codex/` worktree | Early gates |
| Early gates | Static, focused, and production journey pass on one binding | Independent review |
| Review | Separate task approves exact binding with no actionable/unresolved finding | Final gates |
| Final gates | Codex applies the `final-gates` PR label; breadth, browser, release, hosted macOS, and clean-copy proof pass on unchanged head | Owner authorization |
| Authorization | `Authorize guarded release of PR #N at head <40-character-commit>.` | GitHub merge commit |
| Phase B | Merged immutable SHA, backup, observation, updater, settlement, Doctor, isolation, browser, zero effects | Evidence closure |

Any head/base/repository-state change returns the candidate to early gates. A
known live degradation requires its own exact typed repair contract; it cannot
be hidden, waived, or folded into unrelated release work.

## Private identity patterns

Hosted CI runs the content-free structural scanner without household values.
After exact-head review approves and before local final gates, Codex asks the
owner to run:

```bash
poetry run python -m scripts.configure_private_identity_patterns
```

The hidden-input configurator creates the canonical owner-only `0600` file
outside the repository. It requires one or more entries in each categorized
form—`name:<literal>`, `email:<literal>`, and `address:<literal>`—and accepts
additional entries in each category. Values never belong in a task prompt,
command argument, Git, hosted CI, logs, receipts, or PR comments. The local
aggregate release tier fails closed when the profile is missing, empty,
uncategorized, symlinked, repository-contained, mispermissioned, or not owned
by the current user. `ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE` is only an expert
manual override and does not weaken those checks.
