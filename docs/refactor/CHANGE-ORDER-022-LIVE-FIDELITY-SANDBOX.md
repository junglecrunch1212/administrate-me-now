# CO-022 — Fail-Closed Live-Fidelity Sandbox

Status: **PHASE A IMPLEMENTED; NO MERGE, DEPLOYMENT, OR LIVE CHANGE AUTHORIZED**

Date: 2026-08-31

Governing sources: ADR-0000, ADR-0001, ADR-0002, ADR-0006, ADR-0008,
ADR-0013, D6, D15, D26, CO-014, CO-019, CO-020, CO-021, the bootstrap and
topology contract, the UX behavior contract, and the acceptance contract.

## Decision

The permanent sandbox is a reconciled encrypted baseline, not the earlier
nine-message demo and not a copy of a live database. Every sandbox test begins
by reading a fresh, content-free profile from the already-running active
release. The active process reuses its already-open event log, so candidate
code never opens a live database or applies a newer schema implementation to
it. The profiler persists only aggregates, generic category names, version
boundaries, and small-cell-suppression counts.

The candidate then verifies the HMAC-authenticated owner-only artifact against
`app/current-release.json`. A mismatch, stale timestamp, missing dimension,
privacy-comparison failure, bad signature, unsafe path, or under-complex
envelope refuses execution. The canonical baseline identity includes the
active commit, complete fidelity manifest, candidate source-tree digest,
runtime/schema/projection/pipeline boundaries, fidelity contract, and generator
version. Any change creates a transactionally staged new baseline.

## Lifecycle

```text
active runtime read-only aggregate endpoint
→ in-memory live/synthetic privacy comparison
→ owner-only signed fidelity artifact
→ deterministic fictional generator
→ production EventWriter and encrypted log
→ durable bus, 15 projections, and 8 pipelines
→ immutable instance fingerprint and dominance report
→ disposable per-test clone
→ candidate runtime or test command
→ canonical fingerprint verification
→ clone discard
```

No projection database is seeded directly. The current minimum generator emits
67,930 source facts: rich inbound/outbound messages, calendars, tasks,
reminder cues, lists, recurrences, finance observations, notes, contacts,
OpenClaw conversation/model observations, and typed historical adapter-cursor
advancement across all five adapter categories. Triage pressure uses durable
fictional message identities plus typed proposal, enrichment, decision,
feedback, policy-approval, policy-revocation, and supersession events. The
current floor emits 1,442 proposals across 594 initial matters. Production
projection evidence must retain at least 951 unresolved candidates across 522
matters after applying that history, including at least 591
incomplete/destination-required cases, grouping, pagination pressure, and
adversarial category coverage.

Reconciliation measures the emitted envelopes themselves before writing, then
settles the same workload through the production composition root. Both the
emitted-workload dominance report and settled runtime evidence must pass before
the transactionally staged baseline can become canonical. Projection files are
never accepted as fidelity evidence merely because a generator planned to
write enough rows.

Cardinality targets are `max(current floor, ceil(live × 1.10))`. Tail targets
meet or exceed live p50, p90, p95, p99, and maximum values, with the governing
floor applied. Category and topology targets are the union of live values, the
reviewed taxonomy, and an adversarial boundary case, making each a strict
superset. Adult-primary, adult-secondary, two dependent personas, and a CoS
service principal have isolated source, direction, workspace, account,
body-tail, and thread-tail coverage. Each persona class has independent
headroom targets in addition to the aggregate household envelope.

## Commands

Capture a fresh artifact from the authenticated active runtime:

```bash
PYTHONPATH=bridge poetry run adminme sandbox capture-profile \
  --tailscale-identity <authorized-adult-identity>
```

Reconcile or inspect without running a test:

```bash
PYTHONPATH=bridge poetry run adminme sandbox reconcile
PYTHONPATH=bridge poetry run adminme sandbox inspect
```

The only supported future-task test entry point is:

```bash
PYTHONPATH=bridge poetry run adminme sandbox test -- <test command>
```

For an exact-active-release comparison against a separate disposable clone,
select it explicitly; candidate mode remains the default:

```bash
PYTHONPATH=bridge poetry run adminme sandbox test \
  --runtime active -- <comparison command>
```

Tests needing current model behavior use the explicit bounded Gateway mode:

```bash
PYTHONPATH=bridge poetry run adminme sandbox test \
  --require-gateway -- <test command>
```

Serving is always loopback and always disposable:

```bash
PYTHONPATH=bridge poetry run adminme sandbox serve --fresh
```

Local artifacts, keys, baselines, and runs live outside Git in the owner-only
Sandbox application-support directory. The deterministic full-volume mode is
evidence of the OpenClaw seam contract, not current model semantics. Gateway
mode requires injected credentials and permits only the pinned loopback
Gateway; no provider SDK or second model router is added.

## Safety and privacy

- The sandbox has its own tenant and random 32-byte encryption key.
- Synthetic mode instantiates zero real provider adapters and stays in
  observation mode.
- Test environments are stripped of credential-shaped variables; only the two
  OpenClaw values are restored for explicit Gateway-required tests. The wrapper
  also applies an inherited operating-system network policy to the complete
  descendant process tree: deny all network access for deterministic tests,
  permit only the pinned loopback Gateway when requested, and permit only the
  chosen loopback listener for browser serving. Unsupported enforcement fails
  closed.
- The active runtime compares fictional identifiers and eight-word/32-character
  phrases with live values in memory. Output contains counts and pass/fail only;
  no content or reversible hash is persisted.
- Canonical state is never served or passed to a child command. Every run gets
  a private clone and key. The wrapper verifies the canonical fingerprint
  before and after execution.
- Hosted CI and subagents cannot produce the owner-signed active artifact and
  therefore fail closed rather than accessing live state.

## Acceptance evidence

`tests/acceptance/test_live_fidelity_sandbox.py` proves:

1. stale, incomplete, under-complex, or tampered inputs fail;
2. the full generator emits exactly 67,930 schema-valid source facts with all
   required source shapes and rich message tails;
3. the active endpoint is bound to its immutable release and never returns
   fixture content;
4. the in-memory comparator returns counts only and rejects exact/phrase
   overlap;
5. the full encrypted baseline settles through all 15 projections and 8
   pipelines with deterministic OpenClaw calls, no skill failures, no real
   adapters, and no external effects;
6. measured emitted and settled Triage volume includes at least 951 unresolved
   candidates, 522 matters, every candidate kind, required history, and the
   incomplete/destination cases;
7. rebuilding every derived projection in a disposable clone from the full
   encrypted log reproduces the logical rows of all 15 projections;
8. two disposable clones cannot observe one another; and
9. the canonical instance remains byte-identical after clone mutation and a
   repeated reconciliation reuses only the exact matching baseline.

Desktop and narrow-viewport visual checks remain separate authenticated
browser acceptance invoked through `sandbox serve --fresh`; a static unit test
is not treated as rendered-browser evidence. Gateway-sampled evidence likewise
requires the explicit command and a reachable pinned Gateway.

## Constitutional disposition

- **PRESERVE:** encrypted append-only truth, durable delivery, deterministic
  projections, production pipelines, observation/governance ordering, tenant
  isolation, the pinned OpenClaw `llm-task` seam, and zero unintended provider
  effects.
- **MODERNIZE:** the persistent nine-message development member into an
  always-reconciled, measured, disposable full-volume sandbox.
- **SUPERSEDE:** static fixture fidelity claims, copied databases, candidate
  access to live SQLCipher state, periodic best-effort refresh, direct
  projection seeding, and mutable shared test instances.
- **DEFER:** none of the required safety or fidelity invariants. Actual
  Gateway semantics and rendered-browser evidence are intentionally explicit
  execution modes because they require the pinned local services; they fail
  rather than silently substituting deterministic evidence.
