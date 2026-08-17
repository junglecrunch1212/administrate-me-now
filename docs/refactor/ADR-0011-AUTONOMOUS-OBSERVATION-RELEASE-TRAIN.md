# ADR-0011 — Autonomous observation release train

Status: **OWNER-DIRECTED CANDIDATE; MANUAL GOVERNANCE MERGE AND ENROLLMENT REQUIRED**

Date: 2026-08-16

Governing approval: G-002 — Autonomous Observation Release Train

## Context

G-001 removed the former no-merge deadlock and prevented several real release
defects. It also coupled three different concerns into one late, repeated
transaction: defect discovery, final evidence attestation, and owner release
authority. The implementation task commonly ran every expensive local and
hosted gate before a separate Codex review performed the first exhaustive
adversarial pass. Each legitimate finding changed the head and invalidated all
evidence, so the cycle restarted. PR #336 required five exact-head review
cycles. PR #342 reached four rejected heads while all declared suites stayed
green; its findings involved malformed records, durable circuit truth, lease
ownership, cancellation, and Harness attempt preservation.

The review was valuable. Its placement and authorization ceremony were not.
Reverting to unreviewed release would increase version count by concealing
defects. The required result is earlier discovery, automatic independence, and
standing low-risk release authority.

G-001 also treated every degraded baseline as a stop. That is safe for an
ordinary feature release, but it creates a repair deadlock: a candidate whose
only purpose is to repair one unhealthy required capability cannot activate
until the capability is already healthy. When multiple independent
degradations exist, insisting on an all-at-once repair increases scope and
risk.

## Decision

G-001 remains the manual lane. G-002 adds two standing-authority lanes after a
one-time manual governance merge and repository enrollment:

1. **Ordinary observation release.** The live baseline is healthy. The
   candidate cannot change any high-risk boundary and activation retains
   observation with zero provider effects.
2. **Bounded repair.** The live baseline is degraded. The candidate names exact
   unhealthy required capability IDs and exact failed Doctor-check IDs.
   Activation is allowed only to clear those targets without introducing or
   worsening any other capability or Doctor failure.

The deterministic evaluator is
`python -m scripts.harness.cli autonomous-release <manifest>`. It returns
`ADMIT`, `DENY`, `COMPLETE`, or `ROLLBACK`; prose cannot override it.

### What remains manual G-001

A candidate is never G-002 eligible when it changes provider-write enablement,
observation mode, runtime or dependency versions, provider enrollment,
credentials, Keychain, permissions, privacy or authorization, destructive data
behavior, release governance/control code, or a path on the evaluator's
versioned manual list. The list includes this ADR, every `AGENTS.md`, Harness and
workflow control code/tests, updater/selector, Doctor, service, backup and secret
paths, event machinery and migrations, auth/scope/governance/observation seams,
release scripts, and dependency manifests.

G-002 cannot introduce, alter, or authorize itself. The owner manually merges
the governance candidate, configures the isolated hosted reviewer, and starts
new tasks under the effective instructions. Until then, every product release
continues under G-001.

### Early adversarial review

The change task runs exact-bound static, focused, production-composition, and
applicable browser proof first. `harness review-ready` rejects missing or failed
proof and any task-neutral `UNDETERMINED` scaffold placeholder. A fresh hosted
Codex job then performs the exhaustive read-only review
of that exact head before breadth, release-candidate, or macOS work begins.

The hosted job:

- checks out the immutable PR head with no persisted Git credentials;
- runs with a read-only Codex sandbox and dropped sudo;
- receives an API key only through the reviewed action proxy;
- ends the secret-bearing job immediately after Codex, then validates, uploads,
  and posts the content-free receipt from a fresh job with no API secret;
- follows repository review rules for event authority, cancellation, leases,
  durable state, replay, observation, provider effects, and Harness evidence;
- emits schema-bound JSON and a content-free PR receipt; and
- fails on an actionable finding, unresolved required evidence, or head/base
  mismatch.

The conclusion job materializes its binding helper from the effective base and
executes validation with isolated Python. Candidate-controlled scripts never
run in the job that can post the content-free GitHub receipt.

Each hosted attempt retains its full schema-bound result and content-free
receipt as an immutable workflow artifact. Each local command attempt uses a
unique receipt/log directory. Canonical validation re-hashes every retained
attempt, including failed or binding-invalidated attempts, while deriving the
current conclusion only from the latest exact-bound attempt.

If review finds a defect, the implementation adds a failing regression, repairs
the bounded behavior, refreshes the binding, and repeats the cheap checkpoint.
Only an approved unchanged head incurs breadth and release-candidate cost. A
head change still invalidates its review; the change is that review now happens
before the expensive work it can invalidate.

### G-002 admission

Every condition is conjunctive:

1. The exact head contains current `origin/main`, the PR is clean and mergeable,
   and zero actionable threads remain.
2. Static, focused, production journey, applicable desktop/narrow browser,
   breadth, release-candidate, and applicable hosted macOS checks pass.
3. The hosted adversarial review approved that exact head before breadth began;
   its downloaded result and receipt match the validated Harness run's base,
   head, complete path manifest, and diff digest.
4. The candidate declares no high-risk impact and changes no manual path.
5. The live baseline is a coherent, at-most-five-minute-old content-free Doctor
   and diagnostics observation, observation-active, backup-capable, and has zero
   live provider claims.
6. An ordinary release has no unhealthy required capability or Doctor check. A
   bounded repair names at least one exact unhealthy required capability and
   Doctor check, and every target is present in its corresponding baseline set.
7. The guarded updater is the reviewed attested controller.

The evaluator obtains PR metadata, current-main comparison, review-thread
state, and named check-run conclusions through authenticated read-only GitHub
API calls. Corresponding manifest fields are assertions to cross-check, not
standalone evidence.

A passing decision authorizes the release operator to merge the exact PR/head
through GitHub with a merge commit and execute Phase B without a new owner
message. Direct `main` push, force-push, history rewrite, protection bypass,
write enablement, target expansion, and opportunistic repair remain forbidden.

### Scheduled operator and exact handoff

After enrollment, an owner-requested low-risk build may publish a draft and
write one immutable local queue entry only after its committed exact-head cheap
checkpoint is `READY`. The queue entry binds the PR, base/head, complete paths,
diff digest, Harness run, lane, target IDs, and all nine release-impact
decisions from the original outcome contract. A high-risk impact or manual path
cannot enter the queue.

`queue-g002` snapshots the complete validated run under the enrolled private,
owner-only `ADMINME_G002_QUEUE_ROOT/runs` directory and writes its entry under
`entries`. The scheduled operator never depends on the purgeable system-temp
source after handoff, and a changed head receives a fresh snapshot rather than
mutating evidence bound to the superseded queue entry. `next-g002-queue` skips
only entries with a validated immutable closing attempt; waiting attempts remain
eligible for a later scheduled run.

The versioned prompt at
`.github/codex/prompts/g002-release-operator.md` is installed as a local Codex
scheduled task. It processes at most one queue entry per run. It imports the
hosted review before breadth, repairs at most one evidenced bounded batch per
run, derives the release manifest from the immutable queue plus fresh Doctor
and runtime diagnostics, and uses the deterministic evaluator for both
admission and settlement. `NO_WORK`, pending review, and safe supersession do
not create owner chores. G-001 scope, repeated same-condition failure, rollback
failure, or ambiguous live safety is surfaced.

### Monotonic degraded repair

For a bounded repair, let `B` be the set of unhealthy required capabilities at
baseline, `T` the declared non-empty capability target set, and `P` the
post-settlement set. Admission requires `T` to be a subset of `B`. Completion
requires:

```text
T ∩ P = ∅
P ⊆ B − T
```

Thus every target recovers, a pre-existing non-target degradation may remain,
and no target, new degradation, or previously healthy capability may be hidden
inside a merely “live” result.

The same equations apply independently to baseline failed Doctor checks `D`,
target Doctor checks `U`, and post-settlement failed checks `Q`: `U ⊆ D`,
`U ∩ Q = ∅`, and `Q ⊆ D − U`.

### Settlement

After activation, completion requires the exact selected merge, observation
retained, verified encrypted backup, rollback readiness, a fresh complete
Doctor report, an exact-merge authenticated live privacy receipt, applicable
authenticated browser acceptance, zero provider effects, and both health-set
relations above. The ordinary lane requires every Doctor check healthy; the
bounded-repair lane permits only admitted non-target baseline failures after
every named target clears. Post-activation Doctor, diagnostics, privacy, and
applicable browser timestamps must follow the guarded updater's recorded
completion. Admission produces an immutable receipt. Each lane
then produces a policy bound to the exact two-parent merge;
the updater revalidates the admitted healthy or degraded content-free baseline
before and after an authenticated, extracted, manifest-validated, replay-proven
encrypted backup. A bounded repair rolls back on any non-monotonic settlement.
Any missing,
failed, stale, or ambiguous proof returns `ROLLBACK`. The operator records one
content-free release receipt; a second docs-only evidence PR is no longer on
the critical path.

Every scheduled run that selects a queue entry appends one schema-valid,
content-free operator-attempt receipt. Waiting states do not close the queue;
supersession, G-001 escalation, denial, completion, rollback, incomplete
deployment, and exhausted bounded repair do. The source queue and every prior
attempt remain immutable. A completion attempt is valid only while its retained
owner-only settlement manifest still hashes to the receipt, matches the queue
identity and merge commit, and independently evaluates `COMPLETE`; queue
selection rejects altered, orphaned, contradictory, or non-canonical attempt
history. Failed scheduled repairs retain their logs, append a waiting receipt,
and discard only their own disposable worktree so a later run never inherits
dirty repair state.

## State model

```text
CHANGE
  -> STATIC + FOCUSED + PRODUCTION (+ BROWSER WHEN APPLICABLE)
  -> EARLY INDEPENDENT ADVERSARIAL REVIEW
       -> FINDING: REGRESSION TEST + REPAIR -> REPEAT CHEAP CHECKPOINT
       -> APPROVE: BREADTH + RELEASE + HOSTED GATES
  -> DETERMINISTIC LANE CLASSIFICATION
       -> MANUAL/HIGH RISK: G-001
       -> G-002 ORDINARY OR BOUNDED REPAIR: ADMIT
  -> PR MERGE COMMIT
  -> GUARDED OBSERVATION ACTIVATION
  -> SETTLEMENT
       -> COMPLETE
       -> ROLLBACK
```

## Consequences

- Independent review remains real, exact-head, and read-only, but no longer
  requires the owner to create and shuttle between tasks.
- The highest-value defect discovery happens before the most expensive checks.
- Ordinary releases still refuse a degraded baseline.
- A bounded repair can improve one exact capability without combining unrelated
  repairs or falsely declaring the whole runtime healthy.
- High-risk changes remain per-head, owner-authorized G-001 transactions.
- The repository secret, enablement variable, required-check configuration,
  local queue directory, and automatic release-task scheduling are one-time
  external setup;
  until configured, hosted review is not evidence and G-002 remains inactive.

## Classification

- **PRESERVE:** exact-head review, current-base containment, immutable merge,
  observation, provider-effect denial, backup, Doctor, privacy, rollback, and
  fail-closed uncertainty.
- **MODERNIZE:** move exhaustive review before breadth and automate it as an
  isolated hosted job; replace per-release owner prompts with a standing low-
  risk capability.
- **SUPERSEDE:** healthy-baseline-only admission for an exact bounded repair and
  the mandatory second evidence PR on every otherwise complete release.
- **HARDEN:** deterministic manual-path/impact classification and monotonic
  capability-set settlement.
- **DEFER:** live enrollment and activation of G-002 until its governance PR is
  manually merged and the hosted reviewer is configured.
