# G-002 / ADR-0011 autonomous observation release train evidence

Status: candidate only; not merged, enrolled, or released.

## Problem evidence

The repository history after G-001 shows repeated late review invalidation, not
an absence of useful findings. PR #336 required five independent exact-head
reviews before release. PR #340 required several Harness false-green repairs.
PR #342 produced four rejected heads after declared suites were green; the
findings covered provider-record validity, durable circuit truth, lease
contention, cancellation, stale-authority append, and retained Harness attempts.
Those are safety defects. The repeated ordering put their discovery after the
highest-cost proof.

The executable Harness on `main` also retains its navigation-pilot default:
`create_run` manufactures navigation-specific outcome and preservation claims,
and browser proof was required even when no member-visible behavior changed.
The candidate replaces those claims with an explicit generic `UNDETERMINED`
scaffold that `review-ready` refuses until `adminme-outcome`/`adminme-change`
fill it. G-002 does not discard navigation characterization; it makes browser
applicability explicit and adds an exact cheap-review checkpoint.

## Requirement matrix

| Requirement | Candidate evidence |
|---|---|
| Review before breadth | `harness review-ready` requires exact static, focused, journey, and applicable browser proof; the release evaluator compares review completion with breadth start. |
| Independent without owner task switching | The hosted workflow checks out the exact head and runs a fresh ephemeral Codex job read-only with dropped sudo and a schema-bound output. Codex is the final step in the secret-bearing job; the conclusion job uses an isolated base-controlled binding helper, runs no candidate script under its posting token, and publishes only the content-free receipt. |
| No silent review green | Exact head/base, conclusion, actionable count, and unresolved evidence are validated before the hosted check can pass. |
| Preserve high-risk G-001 | Manual paths and eight declared high-risk impacts deterministically deny G-002. |
| Healthy ordinary release | The observation lane denies any unhealthy required baseline capability. |
| Repair a degraded baseline | The bounded-repair lane requires exact baseline targets and completes only when `T ∩ P = ∅` and `P ⊆ B − T`. |
| Repair Doctor-visible failure | Capability and Doctor target sets are independently bound; both must clear monotonically while unrelated baseline failures may remain but cannot expand. |
| No owner evidence shuttle | A schema-bound immutable local queue transfers the exact committed head, paths, digest, lane, targets, and outcome-bound impacts to the versioned scheduled operator. The validated run is copied to the enrolled owner-only durable queue root, so a reboot or system-temp purge cannot erase the handoff. |
| Deterministic queue lifecycle | `next-g002-queue` selects the oldest non-closed entry. Every selected scheduled run appends a schema-bound content-free attempt; waiting remains selectable, while supersession, escalation, denial, completion, rollback, incomplete deployment, and exhausted repair close exactly one queue without editing it. A complete attempt retains and hashes the exact settlement manifest; selection revalidates that it still evaluates `COMPLETE`. |
| No hand-authored admission | `prepare-release-manifest` derives proof and one coherent, at-most-five-minute-old content-free baseline from the validated queue, Harness, imported hosted receipt, Doctor, and runtime diagnostics; `autonomous-release --receipt` records only deterministic `ADMIT`. |
| Both updater paths are exact | The admission receipt and actual GitHub two-parent merge derive a schema/Pydantic-valid policy for the healthy or bounded-repair lane. The guarded updater revalidates baseline before and after backup; repair accepts only the named monotonic improvement. |
| Backup is proved before switch | The updater authenticates and decrypts the encrypted backup into a temporary staging area, validates its manifest and every file digest, rebuilds all fifteen projections and retained workbooks, and only then permits selector mutation. |
| Preserve live safety | Observation, Doctor, backup, rollback, exact selector, an exact-merge authenticated live privacy receipt, applicable browser proof, and zero provider effects are conjunctive settlement gates. Doctor, diagnostics, privacy, and browser evidence must postdate guarded activation; the settlement builder cannot infer a privacy pass from pre-release tests or reuse pre-switch files. |
| Reduce browser bloat | Browser proof is mandatory for member/capability-health paths and declared visible behavior; other changes record `NOT_APPLICABLE` instead of manufacturing screenshots. |
| No navigation-pilot fiction | A new run starts with a generic `UNDETERMINED` outcome/evidence/preservation scaffold; review cannot begin until the applicable skill replaces every placeholder with task-specific cases and proof. |
| No self-authorization | This candidate changes manual governance/Harness/workflow paths, so its own evaluator must return `DENY`; owner manual merge and one-time enrollment are required. |
| No hand-declared green | Release impacts are bound in the original outcome contract. Admission validates the exact Harness run, required release receipt, canonical imported review, immutable hosted result/receipt, full path/diff binding, and observed review-before-breadth timestamps. |
| No hand-declared hosted green | Admission reads the named PR through GitHub, requires the exact non-draft clean/mergeable head, current-main containment, zero unresolved review threads, and successful exact-head review, consistency, and applicable macOS check runs. |
| Immutable attempts | Every local command attempt has a unique retained receipt/log pair and canonical validation re-hashes all retained attempts; hosted attempts remain workflow artifacts. |

## Enrollment boundary

The owner performs one enrollment cycle after independent G-001 review of this
governance candidate: manually merge it, store `OPENAI_API_KEY` as a repository
secret, set `ADMINME_CODEX_REVIEW_ENABLED=true`, make the hosted review and its
dependent consistency/applicable-macOS jobs required checks, create the local
queue directory, and install the versioned release-operator prompt as a Codex
scheduled task. No secret is stored in the repository. Missing setup is
`NOT_CONFIGURED`, never approval.

No current PR, including PR #342, inherits G-002. It must be repaired and
reviewed under the policy effective for its task, or be superseded by a new
current-main candidate created after G-002 activation.
