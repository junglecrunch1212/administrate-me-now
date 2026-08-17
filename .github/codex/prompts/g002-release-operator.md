# AdministrateMe G-002 scheduled release operator

Operate one queued low-risk AdministrateMe candidate. This is a fail-closed
local scheduled task, not general permission to change or release the system.
If any precondition is missing, record a content-free terminal reason and stop
without asking the owner to relay evidence between tasks.

## Fixed boundaries

1. Read the effective `AGENTS.md`, ADR-0011, the repository skills, and the one
   oldest queue entry in the configured absolute G-002 queue directory. If the
   policy is not on `origin/main`, enrollment is not enabled, the queue is
   absent, or no entry is waiting, return `NOT_CONFIGURED` or `NO_WORK`.
2. Process at most one PR and one immutable head per scheduled run. Ignore PRs
   without a schema-valid queue entry. Never infer authority from a branch,
   title, green check, prior owner message, or another PR.
3. Never read or alter `.codex/`, `FILE_INDEX.md`, `docs/map/`,
   `docs/refactor/harness-research/`, household records, credentials, provider
   payloads, Keychain, permissions, provider configuration, or unrelated work.
4. Never enable writes, disable observation, change a runtime/dependency,
   enroll a provider, broaden a target, push `main`, force-push, rebase, bypass
   protection, rewrite history, or edit an installed release. Any manual path
   or high-risk impact routes to G-001 and ends this transaction.
5. Use only content-free capability IDs, Doctor check IDs, counts, SHAs,
   digests, paths, and check conclusions in queue, admission, and terminal
   receipts. Retain every local and hosted attempt immutably.
6. Require `ADMINME_G002_QUEUE_ROOT` to name the enrolled owner-only directory.
   Use `next-g002-queue` for selection. Queue entries live under `entries/`,
   exact Harness snapshots under `runs/`, and immutable scheduled-run results
   under `attempts/`. Never depend on a purgeable system-temp run after handoff.

## Exact state machine

### A. Revalidate the handoff

- Validate the queue entry with
  `python -m scripts.harness.cli validate-g002-queue` and validate its exact
  Harness run. Require the queue base, head, full changed-path list, diff
  digest, outcome-bound release impacts, and PR head to agree.
- Require a clean committed candidate checkout. Fetch current `origin/main`.
  If main is not contained, merge it into the candidate without rebasing or
  rewriting, create a fresh Harness run without modifying the old snapshot,
  rerun the cheap checkpoint, push the new head, create a new queue entry, use
  `record-g002-attempt` to close the old entry as `SUPERSEDED`, and stop this
  scheduled run.
- Run `review-ready`. If it is not `READY`, repair only missing deterministic
  candidate proof; never manufacture a pass.

### B. Resolve early independent review before breadth

- Locate the exact-head `AdministrateMe early adversarial review` run. If it is
  pending, stop as `WAITING_REVIEW`. Download its immutable result and receipt
  and import them with `record-hosted-review` before running any breadth or
  release tier.
- If review reports actionable findings, use `adminme-change` in an isolated
  disposable worktree on the PR branch. Add a failing regression first, repair
  only the evidenced behavior, create a fresh Harness run without modifying the
  rejected snapshot, run static/focused/production and applicable browser proof,
  refresh the binding after the intentional commit, push normally, and create a
  replacement exact-head queue entry. Use `record-g002-attempt` to close the
  rejected entry as `SUPERSEDED`. Do one bounded repair batch per scheduled run.
  If repair or proof fails, retain its immutable logs, append `WAITING_CHECKS`
  with the exact content-free reason code, and remove only that scheduled run's
  disposable worktree; never leave a dirty shared checkout. A scope expansion
  or manual boundary closes it as `G001_REQUIRED`.
- If the imported review is not exact, approving, and free of unresolved
  required evidence, do not run breadth, merge, or activate.
- If the exact approved head is still draft, mark only that PR ready for review
  and recheck that its head is unchanged. Readiness does not authorize merge;
  it merely allows the already reviewed head to enter required checks.

### C. Finish proof only for the approved unchanged head

- Run the exact Harness breadth and release tiers. Wait for and verify the
  named exact-head hosted consistency and applicable macOS checks. Require the
  PR to be open, non-draft, clean, mergeable, current-main-containing, and free
  of actionable threads. A changed head returns to A in a later run.
- Export a fresh content-free baseline from the active release with
  `bootstrap.cli doctor --no-synthetic-proof --export` and
  `bootstrap.cli diagnostics --export`. Do not restart or repair live state to
  make admission pass.
- Derive—not hand-author—the release manifest with
  `harness prepare-release-manifest`. Evaluate it with
  `harness autonomous-release --receipt`. `DENY`, missing proof, ambiguity, or
  a nonzero provider claim ends before merge.

### D. Merge the exact admitted candidate

- Merge only the queued PR through GitHub using a merge commit. Verify the merge
  commit has exactly the former base and admitted head as its two parents and
  is present on `origin/main`. Never merge a different SHA.
- Create a clean immutable checkout of that merge SHA. Derive the exact
  activation policy with `harness prepare-bounded-repair` or
  `harness prepare-observation-release` according to the admitted lane. The
  updater must receive exactly one of those policies. Retain the unchanged,
  clean admitted-head checkout for final Harness derivation; the bound local
  Harness run is intentionally validated there, while activation runs only
  from the immutable merge checkout.

### E. Guarded activation and settlement

- Invoke only the reviewed `bootstrap/update.sh` from the immutable checkout.
  Pass the exact generated policy for the admitted lane. The updater
  must retain observation, revalidate the baseline after backup, create and
  verify the encrypted pre-switch backup, switch immutably, and automatically
  restore the predecessor on any exception.
- Require bounded settlement, a fresh complete Doctor report, exact selector,
  privacy/isolation, applicable authenticated read-only desktop/narrow browser
  acceptance, backup/rollback evidence, and zero provider-effect delta. The
  ordinary lane requires every Doctor check healthy. For a repair, every named
  capability and Doctor target must clear; only admitted non-target baseline
  failures may remain, and any new or disallowed failure requires rollback even
  when liveness is green.
- Run the versioned authenticated read-only privacy probes against the active
  merge: self-scope, cross-member denial, non-enumerating denial, session scope,
  replay isolation, observation retention, and zero provider effects. Record
  only their content-free exact-merge receipt. For a visible candidate, use
  `adminme-frontend-qa` for the corresponding desktop/narrow receipt.
- From the retained admitted-head checkout, derive settlement with
  `harness complete-release` using the exact live privacy receipt and applicable
  browser receipt; do not add green fields by hand. Only `COMPLETE` is released.
  Any other result is `ROLLED_BACK` or `DEPLOYMENT_INCOMPLETE`; never improvise
  a second activation.

## Terminal handling

Use `record-g002-attempt` once at the end of every scheduled run that selected a
queue entry; never edit the source entry. `WAITING_REVIEW` and `WAITING_CHECKS`
leave it selectable. `SUPERSEDED`, `G001_REQUIRED`, `ADMISSION_DENIED`,
`COMPLETE`, `ROLLED_BACK`, `DEPLOYMENT_INCOMPLETE`, and
`REPAIR_LIMIT_REACHED` close it. The schema-bound receipt derives queue ID, PR,
base/head, Harness run and digest, while recording exact merge/deployed SHA when
applicable, content-free remaining capability/Doctor IDs, rollback result,
reason codes, and elapsed time. A `COMPLETE` attempt must pass the exact derived
settlement manifest with `--settlement-manifest`; the command copies and hashes
that manifest under the enrolled owner-only queue root, and canonical queue
selection revalidates it. Do not open a second evidence PR. Do not notify the
owner for `NO_WORK`, waiting, or safe supersession; surface only
`G001_REQUIRED`, three consecutive repair failures on the same evidenced
condition, rollback failure, or an ambiguous live safety boundary.
