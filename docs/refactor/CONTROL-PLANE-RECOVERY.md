# Control-plane recovery front door

Status: candidate workflow; no merge, deployment, or live repair is authorized.

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

The optional `ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE` is an absolute, owner-only
`0600` text file outside the repository with one literal per line. It is never
committed, uploaded, quoted in evidence, or copied into a task prompt. Hosted CI
runs the content-free structural scanner without this private file.
