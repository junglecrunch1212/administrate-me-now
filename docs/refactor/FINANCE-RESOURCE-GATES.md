# Finance resource gates

Semantic classification: **MODERNIZE** the bounded domain interface under
ADR-0001; **PRESERVE** OpenClaw scheduling/orchestration and privileged scope.
The accepted-publication finance contract is disabled pending activation.

## Implemented boundaries

`adminme.finance.admission` gives ordinary HTTP work priority over light
finance requests. After authenticating and mapping the actor without projection reads, finance
waits for a five-millisecond foreground quiet interval, with one active
finance request and at most four FIFO waiters (one waiting slot per member).
Admission expires after 250 milliseconds with a typed busy result. Busy
responses still traverse the existing audit path; private data is not read
before admission, and current domain authority is resolved after waiting. Cancellation
and failures release the lane. Non-finance requests never acquire this lane.
Ordinary responses retain priority through their final body send; SSE releases
at headers so long chat streams cannot block their own tools. This is bounded HTTP admission, not preemption of synchronous
Python or proof of reserved Gateway/model capacity.

`adminme.finance.budgets` admits work using tenant, resolved actor, audience,
authenticated session and inbound message identity. Missing identity refuses
admission. A new correlation ID cannot reset the budget. The pinned transport
does not attest model round numbers, so each call conservatively consumes a
round: at most three calls until that capability is proven. With an attested
round identifier, the primitive allows six calls across three rounds.

The store retains at most 1,024 turn records for 24 hours and refuses new turns
at capacity; it does not evict active limits. Calls and returned evidence are
charged separately, a failed call still costs admission, and one receipt
cannot be delivered twice. A 30-second turn deadline and 2-second light-work
delivery deadline reject late success. These are delivery gates, not proof
that a blocking function can be preempted. Runtime query execution must remain
bounded independently; no heavy work uses this path.

Core, bundle and ordinary result budgets are 400, 1,200 and 2,000 serialized
UTF-8 bytes respectively. Byte count conservatively bounds tokens for the
byte-backed model tokenizers without adding an unpinned tokenizer. Total turn
evidence is 6,000 bytes, with at most two bundles. The conservative byte limit
may produce smaller answers than the proposed token budget. Required caveats
are counted and never silently truncated. Detail pagination permits 50 rows
by default, 100 maximum; large valid data still needs a smaller projection to
fit an ordinary result.

`adminme.finance.cache` is an optional, volatile result cache, currently not
required by the facade. Cache identity binds tenant, actor, audience, grant
and policy revisions, book/entity, publication, operation, calculator/model/
rules, canonical parameters, as-of, coverage, valuations, chosen inputs and
hypothetical identity. Retrieval and one-use delivery independently invoke
current authority. A policy-provider error refuses access and deletes the
affected entry. Different content cannot replace the same cache key.
Explicit invalidation removes affected pointers without changing accepted
history. Limits are 50 entries/10 MiB per member, 24-hour maximum TTL, 128
member buckets and 32 MiB globally. No private result is written to disk.

## Heavy-work admission remains blocked

`adminme.finance.jobs.finance_job_status` returns `temporarily_unavailable`
with `accepted=false`, no job identifier, and the exact prerequisites. No
worker, undurable queue, second cron, or EventLog executor task is started.
The proposed one worker/one per member/four queued, 120-second wall deadline,
256 MiB incremental memory, 2,000 paths/720 months are unverified limits, not
implemented compute guarantees. Completion requires immutable event-backed
job inputs/terminal receipts, safe cancellation and expiry, idempotency,
per-member fairness, tested process termination and proof of reserved general
CoS capacity. No source or runtime capability currently proves those gates.

## Verification and activation

The synthetic resource tests cover caps, long-turn admission, independent
actors, deadlines, receipt reuse, cache identity dimensions, revocation during
delivery, expiry, correction and hypothetical isolation. They must run in
hosted CI or an approved disposable lab, not against the family runtime.
They do not establish latency or incremental memory on the deployment host.

Mixed-load release acceptance must measure baseline and finance-loaded p95
for ordinary What Now/calendar/reminder work through the production
composition root, including cold/warm caches and long conversations. Require
no missed authorized reminders and at most 10% p95 regression. Starting warm
status/report targets are 100/500 ms. No measured result is asserted here;
heavy admission and live activation remain blocked until that evidence is
available. A caller/runtime restart must terminate any in-flight model turn;
the volatile budget store does not prove prior-turn continuity after restart.

An opt-in preliminary harness is
`tests/acceptance/test_finance_mixed_load.py`. In an authorized hosted runner:

```sh
ADMINME_FINANCE_BENCHMARK=1 PYTHONPATH=bridge poetry run pytest tests/acceptance/test_finance_mixed_load.py -q -s
```

It seeds synthetic events through the real application, grants a synthetic
member's private read context, and measures What Now/commitment reads under
parallel authenticated finance status/reserve calculation calls. Bracketing
baseline and mixed p95 values are printed and saved only to the disposable
test directory. It asserts the 10% regression and 100/500 ms status/calculation
targets. This preliminary harness does not prove a large accepted ledger,
heavy-worker memory/termination, reminder delivery or deployment hardware.
Its default skip is explicit; a skipped benchmark is not a passed gate.

The first hosted measurement **failed** the unchanged 10% raw client latency
gate: baseline p95 1.380 ms, mixed core p95 2.418 ms, ratio 1.752. Warm finance
status/calculation p95 were 8.759/9.181 ms. The failure is retained; neither
those small absolute times nor an admission configuration proves isolation.
The next harness also reports a diagnostic matched-thread control with an
existing lightweight system HTTP request, plus server-only core timing, to
separate shared TestClient/portal contention from application work. Those
diagnostics do not replace the original baseline, relax its assertion or
establish live hardware performance. New admission behavior requires a fresh
hosted result before any passing claim.

The second v1 measurement at `684f12f1` also failed: core p95 1.907 ms
baseline versus 2.895 ms mixed (ratio 1.518). Its empty HTTP control alone
regressed 29.7%, exposing shared TestClient/portal overhead. The v2 harness
therefore runs the real application in an isolated spawned HTTP server and
uses independent asynchronous HTTP clients. It retains the raw 10% p95 gate,
100/500 ms finance targets, bracketing baseline and alternating concurrent
launch order. Five hundred requests per phase improve sampling. The earlier
failures remain evidence, not passing results; v2 still needs hosted results.

Budget funding outlook, dated bank-cash projections, debt payoff and noncash
offsets are different contracts. Neither the resource layer nor the reader
converts an offset into liquidity or debt principal into expense. The validated
Books funding-outlook publication and any required semantic schema extension
remain an integration-review dependency; the v1 interchange stays unchanged.

At `2f0ddbfa`, hosted functional/static/native checks passed (353 Python and
92 plugin tests). The v2 benchmark assertion also passed, but its sequential
baseline was 44.044 ms versus 4.113 ms loaded. This approximately 40 ms transport
stall is not accepted as performance-isolation evidence. The next harness sets
TCP_NODELAY explicitly on the supplied listener and both clients to remove
Nagle/delayed-ACK distortion; all original thresholds remain unchanged.

At `75c81d8c`, explicit TCP_NODELAY removed the inflated baseline: before/after
core p95 were 3.490/3.503 ms, loaded p95 7.219 ms (ratio 2.061), so the gate
failed. The v3 harness separates finance and core load generators into distinct
processes/event loops, preserving the independent HTTP server and socket
settings. It measures 500 core samples per phase at 20 requests/second with
10 finance calls/second, requires at least 100 successful finance calls, and
keeps the same 10% and 100/500 ms assertions. This is a specified synthetic
load, not household traffic or heavy-worker acceptance. The earlier failures
remain recorded; no passing performance claim follows from changing a harness.

At `0e3c17ee`, the v3 independent-client run measured before/after core p95
3.899/3.710 ms and mixed p95 4.311 ms (ratio 1.105731): **failed** the unchanged
1.10 gate. Its 251 admitted finance calls measured status/calculation p95
27.148/35.523 ms. The next candidate retains ordinary response priority through
the final body send (SSE releases after headers) and batches two exact durable
receipt reads to reduce shared event-log executor contention. These are runtime
changes requiring new evidence, not a waiver or a passing performance claim.

At `6b2930ef`, 389 functional Python and 96 native tests passed, while the
mixed-load gate again failed: before/after core p95 3.842/3.906 ms, mixed
4.916 ms (ratio 1.258718), with 251 finance calls. Status/calculation p95 were
22.646/22.881 ms. Source inspection found each committed finance audit waking
every durable subscriber, including unrelated projections. The next candidate
uses committed-type wake hints while retaining wildcard, legacy-hint and
startup recovery behavior, with focused delivery/restart regression coverage.
No thresholds are relaxed and the performance gate remains unmet.

At `a315afc4`, 696 Python and 96 native tests passed; the new purchase result
exceeded the conservative reply budget and requires shorter equivalent caveats.
The isolated Chrome 155 desktop save/pause/preference/reload flow passed and its
screenshot was inspected. Mixed load still failed: baseline 4.357 ms versus
5.581 ms loaded (1.280843); finance status/calculation p95 improved to
16.863/16.972 ms. The next diagnostic records server response time separately
from client latency to locate the remaining contention. The original client
10% assertion remains unchanged; instrumentation is not a performance fix.
