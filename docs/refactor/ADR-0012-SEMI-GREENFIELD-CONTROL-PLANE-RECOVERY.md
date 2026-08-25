# ADR-0012 — Semi-greenfield control-plane recovery

Status: **CANDIDATE ONLY; NOT MERGED OR RELEASED**

Date: 2026-08-25

Decision owners: household principals and AdministrateMe product/engineering

## Context

The V2 product and runtime reached a real observation deployment, then delivery
stalled around dashboard refinement and a proposed G-002 governance expansion.
PRs #342 and #343 accumulated multiple implementation, review, repair, and
release tasks without reaching an authoritative final head. Exact-head checks
could pass while semantic review still found release-authority flaws. Harness
evidence lived under system temp and later disappeared. The long-lived local
workspace fell behind GitHub `main`, the live selector stayed on an older
reviewed release, and GitHub `main` lacked protection. The repository was also
public while tracked canaries reversibly encoded household identifiers.

The product architecture is not the failed component. Current-main and the
G-002 candidate both passed broad deterministic suites in independent clean
worktrees, and V2 already contains the production composition, event log,
projections, pipelines, products, adapters, packs, bootstrap, and guarded
runtime operations. Rewriting that product would discard the strongest proof
while preserving the failed delivery habits.

The identifier ADR-0011 remains reserved for the unmerged G-002 proposal. That
proposal never became `main`, release authority, or live behavior.

## Decision

Preserve V2 and replace only its engineering control plane.

1. **One current workspace.** Recovery uses one saved `codex/` worktree whose
   head contains the exact current GitHub `main`. A read-only command compares
   HEAD, branch, local tracking ref, and `git ls-remote` before work. Stale,
   detached, direct-`main`, or non-current workspaces fail closed.
2. **Durable evidence by default.** Harness runs live in a private host-local
   state directory unless an explicit disposable test path is supplied. They
   remain outside the repository and keep exact candidate/repository bindings.
3. **Review before expensive gates.** Static, focused, and production-journey
   gates make a candidate review-ready. Independent exact-bound review then
   approves or rejects it. Breadth, browser, release, final hosted macOS, and
   clean-copy gates remain locked until approval; cheap hosted consistency may
   run earlier. A changed binding clears all proof.
4. **G-001 remains release authority.** No autonomous merge/deploy engine,
   credential broker, orchestration graph, or second policy system is added.
   The owner still authorizes one PR and immutable head using ADR-0009. Phase B
   still begins only from merged `main` through the guarded updater.
5. **Degraded baselines are typed exceptions, not waivers.** A repair must use
   a domain-specific reviewed executor. It binds the immutable baseline,
   exact target/failure set, operation allowlist and ceilings; retains
   observation, privacy, backup, rollback, and zero-provider-effect checks;
   requires monotonic improvement and no new degradation; and stops on any
   mismatch. CO-017/CO-018 recovery is evidence of this shape, not generic
   authority to make a release pass.
6. **Privacy canaries remain private and release-critical.** Hosted code scans
   use content-free structural checks because hosted runners receive no
   household data. The local release tier fails closed unless a categorized
   owner-only `0600` file outside Git supplies name, email, and street-address
   canaries. The aggregate release tier is mandatory for a harness PASS.
   Scanner results contain coordinates and rule names, never matched content.
   The GitHub repository remains private; history is preserved rather than
   rewritten. The owner creates or replaces the profile interactively with
   `poetry run python -m scripts.configure_private_identity_patterns`; typed
   values are hidden and never enter command arguments or process output.
7. **One owner front door.** The active GitHub recovery PR is the status and
   decision surface. Separate Codex tasks are used only when G-001 requires
   independent review; old builder/reviewer/repair/release task chains are not
   resumed.

## Recovery sequence

```text
CURRENT GITHUB MAIN + SAVED CODEX WORKTREE
  -> STATIC + FOCUSED + PRODUCTION JOURNEY
  -> INDEPENDENT EXACT-BOUND REVIEW
  -> BREADTH + BROWSER + RELEASE + FINAL HOSTED MACOS + CLEAN COPY
  -> OWNER G-001 AUTHORIZATION FOR EXACT PR/HEAD
  -> GITHUB MERGE COMMIT
  -> GUARDED OBSERVATION ACTIVATION (only when separately in scope)
```

The control-plane recovery PR itself is a candidate only. This ADR does not
authorize its merge or any Phase B/live action.

## Consequences

- Proven V2 product/runtime behavior and the active release are unchanged.
- Failed or rejected candidates become inspectable history, not bases for more
  agents.
- Semantic review can stop a bad design before full-suite and browser cost.
- Evidence survives task and temp-directory churn without entering Git.
- GitHub protections and exact G-001 authorization become delivery controls;
  test greenness alone cannot become release authority.

## Classification

- **PRESERVE:** V2 runtime/product architecture, append-only truth, privacy,
  observation, guarded writes, G-001 exact-head authority, and live rollback.
- **MODERNIZE:** canonical-workspace attestation, durable harness evidence, and
  staged review/gate order.
- **HARDEN:** repository privacy/protection, content-free identity scanning,
  and exact degraded-baseline repair boundaries.
- **SUPERSEDE:** the unmerged G-002 control plane, temp-only evidence, stale
  workspace use, late semantic review, and multi-task delivery loops.
- **DEFER:** no V2 product function or required projection capability.
