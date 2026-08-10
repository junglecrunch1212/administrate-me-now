# CO-012 contextual message reasoning evidence

Date: 2026-08-10

Environment: clean candidate branch based on `main`, Python 3.14, deterministic
OpenClaw fixture, private disposable runtime instances, no household content,
credentials, canonical materialization, or provider effects.

## Implemented proof

- `tests/unit/test_message_context.py` proves bounded authored-text selection,
  same-thread ordering, related signing-service discovery, relationship reason
  codes, cross-member/workspace/sensitivity exclusion, quoted-history removal,
  privacy-coordinate-bound candidate identity, exact aliases/quotes, and
  rejection of completed, other-owned, or related-only output.
- `tests/unit/test_pipeline_noise_filtering.py` proves reviewed replay,
  duplicate-source idempotency, contextual proposal v3, and replacement of an
  untouched older thread interpretation.
- `test_multi_message_legal_context_yields_distinct_explainable_candidates`
  exercises the production composition root with six primary messages, one
  outbound promise, several asks, a completed step, an ownership-uncertain
  decision, and one deterministically related third-party service notice. It
  produces four distinct Triage proposals and proves exact evidence, compact
  explanation, expanded source chain, mandatory human gating, and zero
  external effects.
- Triage API composition re-authorizes every source in an interpretation before
  returning its source chain. The browser uses text-only DOM construction and
  visibly separates `Context summary · review aid` from `Exact source evidence`.

## Candidate release evidence

The complete local candidate matrix passes on the reviewed branch:

- 1,745 root and pack tests;
- 60 isolated bridge tests with the required bridge-only import path;
- 47 strict acceptance tests with `--runxfail`;
- Ruff lint and format, mypy, Pyright with zero errors, dependency audits, and
  invariant/architecture-negative checks;
- process performance budgets, package builds, clean install, tenant isolation,
  and encrypted backup/restore rehearsal; and
- the exact pinned native OpenClaw contract, build, package, and all 17 plugin
  tests under Node 24.19.0.

Representative measured budgets were 16.91 ms p95 for append-to-projection,
885.86 ms cold start, 13.48 ms rebuild, 15.09 ms p95 temporary view, 18.66 ms
p95 Today, and 14.85 MiB peak memory. The release runner ended with
`ADMINISTRATEME RELEASE CANDIDATE SUITE: PASS`. Non-blocking warnings were the
existing FastAPI TestClient/httpx deprecation, Poetry project-metadata
deprecations, the expected inability to find the local AdministrateMe package
on PyPI during dependency audit, and a newer-Pyright notice. No waiver was
used.

The credential-free rendered console also passes 223 desktop, tablet, narrow,
accessibility, responsive, state, and failure-mode assertions with zero browser
console errors, page errors, or failed requests. Its stale pre-CO-011 Settings
count was corrected to the actual nine control areas on desktop and phone.

PR review and merge receipt, immutable deployment/backup/rollback receipt,
message replay settlement, exact source-assurance counts, Doctor output, and
authenticated live desktop plus 390-pixel Triage review remain pending. No live
candidate may be confirmed, dismissed, enriched, routed, or sent as part of
visual proof.
