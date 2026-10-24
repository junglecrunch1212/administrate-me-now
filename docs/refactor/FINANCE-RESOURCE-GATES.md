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
at headers so long chat streams cannot block their own tools. An admitted
finance request yields again after tool audit and disclosure-receipt persistence
when ordinary work has arrived meanwhile. Each yield uses the same bounded
quiet interval and wait cap, retains the finance FIFO lane, and occurs before
a fresh authorization read. A timeout returns no private values. This is bounded
HTTP admission, not preemption of synchronous Python or proof of reserved
Gateway/model capacity.

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

## Accepted-planning resource coverage

A separate hosted workload imports the synthetic accepted accounting publication
and shared planning inventory through the production composition root: 3,307
bodies, 38,382 context references, 141 original issues and a 2,818-row selected
context. It alternates native balance-summary and single-event-page queries,
checking the returned source value/row, projection pin, receipt and zero provider
effects on every call. It uses the same independent clients, 500 samples per
phase, 20 core/10 finance requests per second, both bracketing baselines and raw
10% core regression ceiling. Both planning operations use the existing report
p95 target of 500 ms. The original status/calculator workload retains its
100/500 ms targets and runs independently. The longer 120-second synthetic
startup allowance covers fixture import only, not measured request latency.

At `e5483133`, this additional gate failed: before core p95 3.483 ms, after
3.189 ms, mixed 4.182 ms (20.07%), with 251 admitted finance calls. Summary/detail
p95 was 20.113/21.027 ms. The inventory cardinalities and exact returned rows
passed, but that does not clear the resource gate. It measures accepted planning
retrieval, not a full raw accounting ledger, heavy computation, reminder delivery
or deployment hardware. Passing the simpler status/calculator workload cannot
substitute for it. Run only in hosted CI or an authorized disposable lab with
`ADMINME_FINANCE_PLANNING_BENCHMARK=1` and the
`test_accepted_planning_mixed_load_p95_gate` test.

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

The v3 harness uses independent server, core-client and finance-client
processes, explicit TCP_NODELAY, 500 samples per phase, bracketing baselines,
20 core requests/second and 10 finance calls/second. Server timings are
diagnostic and never replace the raw client assertion. Earlier shared-client
and delayed-ACK measurements are retained in Git history as failed or rejected
evidence, not passing acceptance.

| Candidate | Hosted functional evidence | Raw core baseline → mixed p95 | Result |
|---|---|---|---|
| `42dafaa2` | One invalid role-removal fixture failed; corrected later | 5.913 → 5.476 ms (0.926090); after baseline 4.490 ms | One bounded pass, not stability proof |
| `e8c57926` | Two planning defects failed; corrected later | 4.735 → 5.615 ms (1.185864) | Fail |
| `b9466312` | 779 Python, 96 native and synthetic desktop checks passed | 3.524 → 4.305 ms (1.221600) | Fail |
| `de8a6fe1` | 782 Python, 96 native and synthetic desktop checks passed | 4.389 → 5.440 ms (1.239571) | Fail |
| `7d120a86` | 793 Python, 96 native and synthetic desktop checks passed | 2.725 → 3.059 ms (1.122426); after baseline 1.932 ms | Fail |
| `63691cc7` | 814 Python passed; one summary-size failure. 100 native/static passed; browser label failed | 4.595 → 5.620 ms (1.223142); after baseline 4.480 ms | Fail |
| `cc159d8a` | 816 Python, 100 native/static and synthetic desktop checks passed | 3.857 → 4.838 ms (1.254260); after baseline 3.695 ms | Fail |
| `34d2bfe8` | 847 Python, 100 native/static and synthetic desktop checks passed | Before 1.885, after 2.767 → mixed 3.007 ms (1.086922) | Bounded pass; baseline drift limits generalization |
| `1323abd5` | 867 Python passed; two new raw-render fixtures lacked `answer_ref`. 100 native/static/browser passed | Before 4.395, after 4.270 → mixed 5.191 ms (1.181163) | Fail; earlier pass does not establish stable isolation |
| `e5483133` | 871 Python, 100 native/static/browser passed | Before 3.426, after 3.491 → mixed 4.356 ms (1.247873) | Fail; dedicated-reader performance remains insufficient |

The [de8 hosted run](https://github.com/junglecrunch1212/administrate-me-now/actions/runs/40042497072)
retains 251 admitted finance calls, 18.381/19.970 ms warm status/calculation
p95 and 1.881 → 2.247 ms diagnostic server p95. Its correctness tests prove
that a grant revoked while delivery yields is checked again before release;
they do not prove the required performance isolation.

Profiles show shared projection dispatch cost. Initial finance reads and
identity resolution already use whole-query worker callbacks with detached
results. Exact grant/quorum resolution uses one SQL snapshot; final private
checks remain fresh and synchronous. The two ordinary What Now/active-commitment
reads now run on that same existing projection worker as complete callbacks,
so their statement dispatch does not block HTTP I/O. Member identity is
rechecked after the await; delegated CoS state retains its own authenticated
domain boundary. Hosted blocked-read liveness and changed-role/login cases
passed at `7d120a86`. Selecting only consumed grant columns and copying immutable
Session/context fields without recursive copying did not clear the gate at
`cc159d8a`: 250 admitted calls retained a 25.43% raw p95 regression. The separate
profile measured final authority p95 0.710 ms, including 0.565 ms projection
dispatch. These values do not replace acceptance timing.

Source inspection found that each Parties read reset SQLite's authorizer twice.
[SQLite expires prepared statements on each authorizer change](https://raw.githubusercontent.com/sqlite/sqlite/master/src/auth.c).
Candidate `34d2bfe8` uses one additional, worker-owned, encrypted `mode=ro` Parties
handle with a permanent query-only authorizer, 64 prepared statements and a
256 KiB page-cache target. It caches SQL plans, never grant values. Every grant,
quorum, expiry and final revocation check still queries current committed WAL
state. Cursors close after detached rows are copied; rebuild publication closes
the reader before replacing the database, and shutdown closes it on its owner
worker. Other projections retain their existing query boundary. There is no new
executor, runtime, background task or changed response/load threshold.

The focused hosted checks exercise fresh revocation after plan reuse, extra
approvals, write/ATTACH/transaction rejection after writer-side preparation,
failed and successful rebuilds, handle ownership and close. Existing full
projection rebuild tests and Parties behavior checks are included because this
changes that database's reader lifecycle. These checks passed in the
[34d2bfe8 hosted run](https://github.com/junglecrunch1212/administrate-me-now/actions/runs/40048286538).
With 251 admitted finance calls, warm status/calculation p95 was 12.418/17.238 ms;
raw core p95 was 1.885 ms before, 2.767 ms after and 3.007 ms mixed. The unchanged
gate uses the larger bracketing baseline and passed at 8.69%. Before/after drift
prevents claiming stable performance or attributing the whole change to one fix.
The separate diagnostic final-authority p95 fell from 0.710 to 0.326 ms and
HTTP-loop projection dispatch from 0.565 to 0.235 ms; worker session resolution
was 0.356 ms and event application 0.475 ms. Profiles support the compilation-cost
diagnosis but never replace raw acceptance. Total memory remains unmeasured;
the cache target is not an RSS claim. No threshold or offered load changed.

At `1323abd5`, the original gate failed again at 18.12%, with 251 admitted calls
and 17.324/19.288 ms status/calculation p95. Separate diagnostics showed worker
statement p95 0.292 ms, but final HTTP-loop dispatch 0.787 ms and final authority
0.943 ms; projection event application p95 was 1.021 ms. This supports examining
queue contention beyond compilation, without proving a sole cause. The next
candidate moves the existing Parties read-only handle to one dedicated serialized
reader thread. Writers retain their existing owner. Final authority still reads
current committed SQL data synchronously and never caches a grant. Publication
excludes new reader submissions, drains/closes the old handle before replacing
the DB, and permits readers only after the replacement; shutdown joins both
workers. Source review also identified that a read must not overtake an already
queued Parties mutation. A pending-mutation fence preserves the original writer
FIFO for those reads, including queued revocations, role changes, maintenance
writes and publication. Only unrelated writer work is bypassed; no authority
result is cached. A dedicated regression queues a revocation behind a held
writer and requires the later read to wait and reject the revoked grant.
Focused hosted checks hold the writer busy while a fresh grant read
finishes, then verify committed revocation, and hold publication while a new read
must wait for the replacement. Existing lifecycle/identity tests remain included.
The additional thread's memory cost and its latency benefit are not yet measured.

Budget funding outlook, bank cash, debt payoff and noncash offsets remain
distinct. The separate dated-projection transport is a declared summary subset;
its exact Books producer mapping and private parity remain integration gates.
Accounting accepted-publication v1 is unchanged. Heavy-work memory, termination,
reminder delivery, large-ledger and deployment-hardware gates remain unproved.

The [7d120a86 run](https://github.com/junglecrunch1212/administrate-me-now/actions/runs/40044025770)
retains the failure despite the lower absolute latency. Its separate diagnostic
measured 50 final authorization calls on the HTTP loop: 18.079 ms total,
0.453 ms p95, 0.498 ms maximum. Main-loop projection dispatch p95 was 0.347 ms;
100 worker session resolutions had 0.853 ms p95. These instrumented values are
not acceptance timings and do not authorize removing fresh revocation checks.
No identical rerun is substituted for a fix or used to erase earlier failures.

At `e5483133`, the dedicated reader's held-writer/rebuild tests passed, but both
raw latency gates failed. Diagnostic final-authority p95 was 0.396 ms in the light
workload and 0.408 ms with planning; planning query p95 was 7.667 ms on the writer
worker. Lower final-authority timing is insufficient. The next diagnostic records
per-operation executor wait separately from execution, including core snapshot
queries, authority reads and event-log work. Instrumentation remains confined
to separate profile servers; no profile value substitutes for either raw gate.
The queued-revocation fence and its regression are pending their own hosted run.
