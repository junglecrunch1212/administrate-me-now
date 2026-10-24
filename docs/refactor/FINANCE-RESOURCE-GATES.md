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
| `ee43a0df` | 872 Python, 100 native/static/browser passed, including queued-revocation ordering | Before 4.036, after 3.570 → mixed 4.698 ms (1.163851) | Fail; planning also fails at 30.69% |
| `fce14a57` | 872 Python, 100 native/static/browser and deep-query work checks passed | Before 4.414, after 4.388 → mixed 5.433 ms (1.231012) | Fail; planning also fails at 20.74% |
| `b32c4252` | 877 Python, 100 native/static/browser, query-work and reader-memory checks passed | Before 4.323, after 4.164 → mixed 5.431 ms (1.256279) | Fail; planning also fails at 21.49% |
| `9814dbac` | 877 Python, 100 native/static/browser, query-work, reader-memory and diagnostic validity checks passed | Before 4.249, after 3.495 → mixed 5.423 ms (1.276111) | Fail; planning also fails at 10.19% |
| `d2115dd9` | 922 Python, 100 native/static/browser, query-work, reader-memory and diagnostic validity checks passed | Before 4.267, after 4.250 → mixed 5.349 ms (1.253548) | Fail; planning also fails at 29.23% |
| `d616844e` | 922 Python, 100 native/static/browser, query-work and reader-memory checks passed; all four diagnostics failed on an incorrect response-render assertion | Before 3.630, after 3.539 → mixed 4.407 ms (1.214193) | Fail; planning also fails at 22.69% |
| `7318f4d0` | 922 Python, 100 native/static/browser, query-work, reader-memory and all four repaired diagnostics passed | Before 3.308, after 3.052 → mixed 4.137 ms (1.250530) | Fail; planning also fails at 20.88% |
| `f8e56dba` (removed) | 924 Python passed; five new audit assertions used the wrong event type; 100 native/static/browser, query-work, reader-memory and all four diagnostics passed | Before 3.401, after 3.391 → mixed 4.382 ms (1.288282) | Fail; planning passed at 1.23% with baseline drift |

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
The queued-revocation fence and its regression passed at `ee43a0df`; neither
latency gate passed. The light run had 251 finance calls, 17.171/16.909 ms
status/calculation p95. Planning had 251 calls, before/after core p95
3.999/3.988 ms and mixed 5.226 ms (30.69%); summary/detail p95 was
19.903/26.704 ms. Server p95 also rose, from 1.668 to 1.923 ms for light and
1.657 to 2.087 ms for planning. These are application-side regressions too.

An independent source review identified a diagnostic defect: wrapped methods
were all named `timed`, combining core snapshots and event application in queue
statistics. The next candidate preserves original method identities and retains
raw per-operation queue/start/end and per-route start/end spans in a synthetic
artifact. Instrumented timings remain separate from both raw gates.

The same review identified deep event-page scans on the general projection
worker; measured planning query p95 was 11.185 ms. The candidate full-horizon
query seeks by the existing contiguous ordinal index before joining bodies.
Ingress and replay already require every event to be undated or overlap its
context's 365-day horizon, so this removes redundant filtering and prior-body
reads without changing row membership/order, source dates or public offsets.
Shorter horizons still filter before pagination. A hosted diagnostic compares
identical returned rows against the prior SQL at offsets 0, 1409, 2816, 2817
and 2818, records both query plans and operation counts, and requires deep-page
VM work to fall. Full-context pagination/replay and boundary-window checks
remain the semantic proof; both unchanged mixed-load gates still must pass.
No additional worker, cache, schema, grant or workload change is introduced.

At `fce14a57`, identical deep-page rows were returned using the existing ordinal
index. At offset 2816, the diagnostic counted at most 300 VM steps for the full
candidate query versus at least 67,800 for the prior page SQL. Mean diagnostic
times were 1.691 ms (full candidate) and 7.597 ms (prior page only); these are
not the raw acceptance measurement. The matched query plans confirm an indexed
ordinal seek followed by lookups for the selected bodies. Planning query p95
in the separate profile fell from 11.185 to 3.159 ms, yet both raw gates failed.
Light status/calculation p95 was 18.346/19.106 ms with 250 calls. Planning core
before/after/mixed p95 was 4.488/4.434/5.419 ms (20.74%), with 251 calls and
21.052/21.388 ms summary/detail p95. No isolated favorable number clears either
gate.

Corrected operation labels showed small instrumented core queue waits: What Now
p95 0.195 ms (light) and 0.174 ms (planning), versus finance session-resolution
p95 1.251/1.401 ms occupying the general worker while waiting on the authority
reader. The next candidate dispatches each complete finance session read onto
that existing reader. It adds no thread or grant cache and retains the queued
Parties-mutation fence. Reader callbacks cannot submit writer work, avoiding
publication/close deadlocks; cancelled callbacks settle without blocking the
HTTP loop, including repeated cancellation, before cancellation propagates.
Final authority SQL and transport/disclosure checks remain fresh and unchanged.
Dedicated tests cover nonblocking unrelated-writer contention, write rejection,
no native-handle escape, queued revocation/publication and cancellation/close.

The next separate profiles warm both paths before recording before/mixed/after
phases (100 core calls each, 50 finance calls in the mixed phase). Profile
serialization occurs only after all measured responses, removing a diagnostic
end-of-run perturbation found in the raw spans. They retain per-route and
per-operation spans and phase-specific statistics; neither acceptance workload
is changed. Three isolated Linux processes also measure resident/high-water
and virtual memory before the reader, after its first read, after 1,000 repeated
reads, after rebuild and after shutdown. This measures the existing reader's
synthetic lifecycle; it does not establish deployment-hardware or heavy-worker
memory, and the page-cache target remains distinct from an RSS limit.

At `b32c4252`, the reader callback, repeated cancellation/failure, publication,
queued revocation and close checks passed (877 Python cases in the selected
functional suite). Both raw gates still failed: light had 251 finance calls,
17.750/19.454 ms status/calculation p95 and 25.63% core regression. Planning had
250 calls, before/after/mixed core p95 4.100/4.078/4.981 ms (21.49%), with
19.334/20.045 ms summary/detail p95. All native/static/browser checks passed.

Three isolated Linux processes measured an initial reader RSS increase of
7,004,160–7,024,640 bytes (about 6.7 MiB), unchanged after 1,000 additional reads.
Rebuild/reopen increased the delta to 7,467,008–7,487,488 bytes. The reader thread
count returned to zero at shutdown, but RSS remained 7,446,528–7,467,008 bytes
above the initial baseline. Allocator-retained memory is not reported as freed,
and this single lifecycle is not a long-run leak test. Virtual address space
reservation is recorded separately. These observations neither impose a 256 KiB
RSS bound nor qualify the household Mac Mini or a heavy worker.

Matched instrumented profiles showed little change in core queue time: light
What Now p95 before/mixed/after 0.160/0.164/0.157 ms; planning
0.169/0.172/0.177 ms. Whole-session resolution now runs on the authority reader
(p95 0.697/0.765 ms); final authority still blocks the HTTP loop synchronously
(p95 0.745/0.759 ms). The profiles' shared client event loop and heavy cProfile
instrumentation differ from the independently driven raw gate. Their favorable
core timing cannot explain or waive the raw failures.

The `9814dbac` diagnostic matches the independent finance client and
uses wall timing without cProfile by default. It runs four controlled client
start offsets (0, 12.5, 25 and 37.5 ms) across the 50 ms core cadence, each with
100 core and 50 finance calls, plus before/after phases. Every offset is
reported; none is selected to replace acceptance. An optional explicit
`ADMINME_FINANCE_CPU_DETAIL=1` preserves the heavier CPU trace when needed.
Separate disposable control servers omit only final authority SQL after receipt
persistence, retaining the initial session reads, owner/digest checks and audits.
This deliberately unsafe-for-authorization counterfactual is confined to the
synthetic test module, requires profile mode, is labeled
`final_authority_sql_stubbed=true` and `acceptance_eligible=false`, and cannot run
as either real acceptance server. It isolates the final loop-blocking cost;
it does not justify caching or removing production revocation checks. No runtime
code, raw workload, threshold, source contract or activation setting changes in
this diagnostic revision.


At `9814dbac`, the raw accepted-planning gate recorded before/after/mixed core
p95 3.967/3.645/4.372 ms (10.19% regression), 251 admitted calls and
17.966/17.819 ms summary/detail p95. The light gate recorded 251 calls and
18.565/18.840 ms status/calculation p95. Both remain failed under the unchanged
10% raw threshold. Three reader-memory trials measured 7,020,544–7,024,640 bytes
initial RSS growth, unchanged after 1,000 further reads; rebuild/reopen raised
that to 7,483,392–7,487,488, with 7,462,912–7,467,008 retained after shutdown.
The reader thread terminated in all three trials; retained RSS is not freed
memory or a deployment-hardware qualification.

Independent-client wall profiles and the final-SQL control both showed their
largest core tails at the 12.5 ms offset. Real/control What Now p95 was
3.229/2.949 ms for light finance and 3.338/3.282 ms for planning. Thus final
authority SQL alone does not explain the shared cost. Slow core spans overlapped
event-log cursor validation, page reads and lag counts, followed by audit
projection application. These overlaps identify work to investigate, not causal
proof or an alternate acceptance threshold.

The `d2115dd9` candidate combines event-log cursor validation and the first
page query into one native-worker turn. It preserves existence checks even for
empty filtered results, exclusive cursors, type/owner filters, watermark and
paging semantics; it changes neither bus delivery nor authority. The affected
EventLog suite joins hosted finance checks, including a multi-page filtered read
that proves one worker turn per page. The same raw gates and independent-client
profiles determine whether this reduction helps; no latency success is assumed.
At this head, both independent-client profiles confirm event-log submissions
fell from 2,600 to 2,000 for 200 finance calls: 600 checked page reads replace
600 cursor and 600 page jobs; the 600 lag counts remain. Correctness passed
(922 Python cases), but raw light/planning p95 still failed at 25.35%/29.23%.
Light had 251 calls and 17.610/17.997 ms status/calculation p95. Planning had
250 calls, before/after/mixed core p95 4.335/4.330/5.602 ms and
20.598/20.987 ms summary/detail p95. There is no demonstrated end-to-end win.
Three reader-memory trials measured 7,020,544–7,045,120 bytes initial growth,
flat after 1,000 further reads, 7,483,392–7,507,968 after rebuild/reopen and
7,462,912–7,487,488 retained after shutdown; all reader threads exited.


The prior independent-client planning profile submitted 2,600 event-log jobs
across 200 finance calls, including 600 cursor checks (23.08% of log submissions).
The cursor checks consumed 73.626 ms worker wall time and 65.263 ms queue time
in aggregate. Batching removes their separate dispatches, not their SQL work;
these totals cannot establish the core tail improvement.

The follow-up diagnostic records bounded loop-callback spans (excluding selector
sleep and suspended coroutines) and per-worker thread CPU alongside wall time.
It separately times JSON decoding/encoding, event-envelope decoding, response
serialization and JSON rendering on the executing thread, attributes lag counts
to code locations (without arguments or retained frames), and pins planning
cardinalities in each artifact. Nested function timings overlap and are not
additive. Thread CPU excludes waits; wall minus CPU includes SQL I/O,
descheduling and possible GIL contention and must not be described as GIL time
alone. The extra instrumentation is confined to separate synthetic profile
servers, never either raw gate, and does not qualify release or private access.


The first occupancy-diagnostic revision (`d616844e`) incorrectly required
`JSONResponse.render` to execute. Pinned FastAPI 0.136.0 sends typed endpoints
through `serialize_response(..., dump_json=True)` and constructs `Response`
from Pydantic JSON bytes, so that rendering hook is optional. The correction
requires the already-instrumented serializer, also times the synchronous
Pydantic JSON-byte serializer, and retains raw trace artifacts before validity
assertions. This repairs diagnostic coverage only; it changes
neither production response serialization nor the raw performance gate.

At `d616844e`, planning raw before/after/mixed core p95 was
3.491/3.494/4.288 ms, with 251 finance calls and 16.680/16.881 ms
summary/detail p95. Light had 250 calls and 14.952/14.974 ms
status/calculation p95. The three reader trials were again flat after
1,000 reads (initial RSS delta 7,020,544–7,024,640 bytes), grew to
7,483,392–7,487,488 after rebuild and retained 7,462,912–7,467,008 after
shutdown with zero reader threads. None of these observations clears either
raw gate, and the failed occupancy profiles supply no qualified timing result.


At `7318f4d0`, planning raw before/after/mixed core p95 was
3.603/3.576/4.355 ms, with 251 finance calls and 16.739/17.884 ms
summary/detail p95. Light had 250 calls and 14.068/14.893 ms
status/calculation p95. Reader RSS grew 7,008,256–7,020,544 bytes initially,
remained flat over 1,000 further reads, grew to 7,471,104–7,483,392 on rebuild
and retained 7,450,624–7,462,912 at shutdown; all three reader threads exited.

The valid profiles cover all four offsets. At the 12.5 ms overlap, planning
query worker wall/CPU totals were 104.244/104.248 ms over 50 calls; nested JSON
decode totals were 76.547/76.562 ms. Loop envelope decoding p95 was 0.039 ms,
Pydantic JSON encoding 0.026 ms and response serialization 0.043 ms. These small
loop operations do not justify moving them individually. Core worker wall/CPU
p95 was 1.328/1.006 ms (planning) and 1.073/0.871 ms (light), with audit drain
callbacks overlapping execution. At offset zero, extra pre-query HTTP work also
remains visible; offsets 25/37.5 mostly avoid the overlap, with occasional long
background outliers retained in the traces. These are instrumented observations,
not proof of GIL causality or replacement acceptance. The 600 steady-state lag
counts come from `_drain_once`; snapshot waiters account only for a few startup
counts in light profiles, not the measured hot path.

The single bounded pre-audit-wait candidate `f8e56dba` was evaluated through
[the complete hosted run](https://github.com/junglecrunch1212/administrate-me-now/actions/runs/40060194879)
before removal. Light raw core p95 was 3.401/3.391/4.382 ms
(before/after/mixed), a 28.83% regression or 0.981 ms over the larger baseline,
with 251 actual finance calls and 13.964/13.992 ms status/calculation p95.
Planning was 4.007/3.236/4.057 ms, a bounded 1.23% pass with material baseline
drift, 250 actual finance calls and 18.286/15.613 ms summary/detail p95.
Both unchanged gates were required, so this candidate failed the stop criterion.
The planning pass does not establish stability or compensate for light failure.

Functional results were 924 passing Python checks and five failed new audit
assertions. Those assertions selected `tool.call_audited`, while the native
writer emits `openclaw.tool_call.recorded`; they do not establish missing audit
history. The two new receipt-wait/revocation cases passed. Remaining assertions
after each failure were not executed, so the candidate's audit, timeout and
cancellation coverage is not qualified. Native 100 checks, static checks,
synthetic browser smoke, query-work, reader-memory and all four diagnostics
passed. The rejected candidate and its seven added cases were removed together;
production sources and the disclosure test file match `7318f4d0` byte for byte.
That predecessor's functional evidence is reusable, but its two failed raw
resource gates remain failed. No unqualified test result is promoted to a pass.

The four diagnostic profiles remain unqualified. Real light What Now p95 at
0/12.5/25/37.5 ms offsets was 2.355/2.271/2.314/1.542 ms, versus
2.086/1.811 ms bracketing baselines. Real planning was
1.817/1.125/1.106/1.193 ms, versus 1.386/1.180 ms. Different phase behavior
reinforces that an audit-submission wait is not resource reservation. All three
reader trials were flat through 1,000 further reads, with initial RSS deltas
7,008,256–7,041,024 bytes, rebuild deltas 7,471,104–7,503,872 and retained
shutdown deltas 7,450,624–7,483,392; each reader thread exited. No deployment
hardware or heavy-worker claim follows.

The decision boundary is now structural, not another timing adjustment:

- Keep the existing 10% gate and investigate reducing shared audit/projection
  work with a bounded causal experiment before another production candidate.
  Any batching must preserve every terminal audit, append-only ordering,
  idempotency, cancellation, projection catch-up and fresh final authority.
  The light failure means planning-only decoding changes cannot be sufficient.
- A bounded calculation worker could isolate planning/model CPU, but still
  leaves parent-process audit and HTTP costs. It adds IPC validation, memory,
  cancellation/termination and credential-handling obligations; current traces
  do not establish that it would clear the light gate. No child-process access
  to household data or credentials is authorized by these measurements.
- Changing workload/rate or accepting an absolute latency budget in place of
  the existing relative gate requires an explicit owner decision and a new
  representative acceptance contract. The measured sub-millisecond absolute
  increase does not authorize weakening the current 10% requirement.

Heavy analysis and activation remain disabled. The full capability/workflow
backlog remains in the acceptance ledger; removal of this unsuccessful
optimization does not complete the original W0–W7 scope.

## Exact a26cada2 terminal evidence

[Finance run 40108819429](https://github.com/junglecrunch1212/administrate-me-now/actions/runs/40108819429)
completed with 975 functional checks passing and both raw resource gates
failing. No diagnostic result substitutes for these gates. Both workloads used
20 core requests/second plus 10 finance requests/second, 500 core samples per
phase and 251 actual finance calls, on hosted Linux with Python 3.14.7.

| Raw workload | Baseline before / after p95 | Mixed core p95 | Increase over larger baseline | Server before → mixed p95 |
|---|---|---|---|---|
| Light | 4.630 / 4.320 ms | 5.378 ms | 0.748 ms; 16.148% | 1.898 → 2.165 ms |
| Accepted planning | 4.393 / 4.259 ms | 5.209 ms | 0.816 ms; 18.564% | 1.841 → 2.151 ms |

The unchanged acceptance bound is 10%. Baseline drift is 0.310/0.134 ms;
the server-side increases are 0.267/0.310 ms. The effect is therefore not solely
client timing. The absolute added HTTP delay is small in these synthetic
workloads, but the run does not measure end-to-end CoS answer latency, household
Mini behavior, reminder delivery or a large ledger. It supplies no whole-process
CPU utilization percentage. Summing overlapping instrumented spans would not
produce one. Neither a saturated host nor harmless measurement noise is proven.

The authority-reader diagnostic measured first-read RSS deltas of
6,004,736–6,021,120 bytes, flat through another 1,000 reads; rebuild/reopen deltas
were 6,467,584–6,483,968 bytes. The reader thread exited, while allocator RSS was
retained. These are Linux synthetic reader costs, not a 256-KiB process bound or
production memory-capacity evidence.

The single matched Tasks no-op diagnostic removed 50 dispatches per mixed
phase without uniformly improving latency. Planning client p95 at offsets
0/12.5/25/37.5 ms was 5.581/8.114/5.467/4.350 ms in baseline and
5.593/7.381/4.344/4.405 ms in control. This establishes cadence sensitivity, not
a causal cure or a passing alternative phase. Its RSS includes trace buffers;
all diagnostic reports remain `acceptance_eligible=false`. No production
no-op optimization or delay tuning follows from this mixed result.

The decision remains explicit: either retain the 10% relative gate and develop
a causally supported structural reduction, or have the owner separately approve
a representative absolute-latency/rate contract with its tradeoff stated. The
latter could accept the observed sub-millisecond increase while weakening the
current relative-isolation guarantee; it has not been adopted. Metadata-only
reads and dated snapshot delivery must not be represented as resource-qualified
full finance activation. The separate daily Sheets refresh engine is not a
dependency of an accurately labeled accepted dated snapshot.

## Proposed acceptance revision — not adopted

The current 10% requirement remains failed. These are proposed product budgets
for explicit review, not thresholds fitted to make a failed run pass. Raw gates
retain p95 summaries, not raw samples/p99; p99 cannot be reconstructed from those
reports. Whole-process CPU and deployment-hardware headroom are unmeasured.
Instrumented diagnostic p99/CPU spans cannot replace those missing measurements.

| Measure | Candidate budget for review | Rationale and evidence gap |
|---|---|---|
| Core HTTP p95 | <=25 ms total and <=5 ms added over the larger bracketing baseline | Small explicit interaction-delay budget, independent of the ~4 ms denominator |
| Core HTTP p99 | <=75 ms total and <=15 ms added | Bounds tail harm; no current raw result establishes this |
| Finance latency | Preserve status p95 <100 ms and calculation/planning p95 <500 ms; add p99 <500 ms / <1 s respectively | Existing p95 obligations remain; p99 needs raw collection |
| Added process-tree CPU | <=0.25 logical-core equivalent averaged over normal load; <=1 core under stress | Sum process CPU seconds / elapsed seconds against an equally sampled baseline, not overlapping spans |
| Added RSS | <=64 MiB metadata/light, <=128 MiB planning; <=8 MiB retained growth after warm-up over 15 minutes | Sample parent and workers together; the measured ~6 MiB reader alone proves neither budget |
| Whole lab process tree | <=512 MiB RSS; existing lane/queue/timeout limits | Proposed safety stop, not measured production headroom |

Normal-load assumptions for review: 2 core requests/second plus one finance
retrieval every 5 seconds, sustained 15 minutes, preceded and followed by the
same core-only baseline. Include one fixed 30-second 20-core/10-finance burst.
These are conservative household-envelope assumptions, not measured household
usage. Keep the existing 20-core/10-finance sustained stress test separate.
Predeclare one complete run per workload; retain every phase, error, timeout,
cold/queued result and raw sample, with counts and quantile uncertainty. Do not
select favorable phases or retry to pass. The 180 normal-phase finance samples
cannot establish a confident rare-tail guarantee; review a longer fixed run or
a separate pre-agreed stress sample if that guarantee is required. Correct
values, audits, revocation, privacy and zero provider effects remain mandatory.

This trades strict relative isolation for explicit absolute interaction and
capacity budgets. It does not qualify LLM response time, background reminders,
heavy analysis, daily Sheets recalculation or live source freshness. Hosted
Linux and intended deployment hardware require separate evidence.

No local lab is already authorized here. A disposable VM is one proposed
isolation choice, not a repository requirement. A reviewable setup is a local VM with 2 vCPUs, 2 GiB memory,
loopback-only networking, no shared home/Keychain/service directories, a clean
immutable candidate checkout and the prepared pinned Python 3.14.7/Node 24.19.0
toolchain. Approve that VM, private scratch directory, resource ceiling and
cleanup window before execution. The existing probe below can run once inside
it with a 10-minute VM watchdog and no automatic retry:

    ADMINME_TEST_MODE=1 ADMINME_FINANCE_BENCHMARK=1 \
    ADMINME_FINANCE_PLANNING_BENCHMARK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=bridge \
    /absolute/approved/lab-venv/bin/python -m pytest -q -s \
      tests/acceptance/test_finance_mixed_load.py::test_light_finance_mixed_load_p95_gate \
      tests/acceptance/test_finance_mixed_load.py::test_accepted_planning_mixed_load_p95_gate \
      --basetemp=/absolute/approved/lab-scratch/finance-probe

That is an executable existing p95 probe, not complete proposed qualification.
The diagnostic harness below adds sustained-load, raw p99 and process-tree
CPU/RSS collection; its exact-head hosted evidence must be reviewed before any
hardware run. Proposed budget enforcement has not been adopted. A VM watchdog
stops only the lab; retain synthetic reports, stop its processes, then remove
only the approved VM/scratch path. No installed release, private state, Keychain,
TCC or provider connection is needed by the synthetic fixtures. Direct candidate
execution alongside the household service remains prohibited; shared resource
contention is an operational effect even without a file mutation. No local VM,
probe, new qualification or policy change has been run or approved here.

## Diagnostic sustained/resource harness (not acceptance)

The next-head diagnostic implements the proposed measurement method without
adopting any proposed budget. `test_finance_sustained_resources.py` spawns the
existing credential-free production HTTP composition, plus separate core and
finance load generators. Its fixed full sequence is cold (1 second), warm-up
(30 seconds at 2 core/1 finance requests per second), core-only baseline
(60 seconds at 2/s), normal mixed load (900 seconds at 2 core/0.2 finance/s),
matching core-only baseline (60 seconds), burst baseline (25 seconds at 20
core/s), mixed burst (30 seconds at 20 core/10 finance/s), and matching burst
baseline (25 seconds). Light and accepted-planning fixtures run once each on
separate hosted jobs. The existing two 10% mixed-load gates retain their exact
workloads, assertions and execution; this diagnostic cannot clear them.

Every scheduled request receives a terminal JSONL record. Fixed arrival times
continue during slow responses; bounded client queues retain overflow as a
rejection. Raw records retain operation, scheduled/start/terminal times, queue
wait, HTTP status, server timing, latency, errors, drain cancellation and
undispatched requests. No retry, successful-only phase selection or removal of
cold requests is permitted. Reports give nearest-rank p95/p99, sample counts,
all-attempt and successful-only distributions separately, and flag fewer than
1,000 observations as weak tail evidence. Fifteen minutes supplies only 180
normal finance observations. Neither its p99 nor a 120-sample baseline is a
confident rare-tail guarantee.

The resource observer reads Linux procfs only beneath the explicitly spawned
runtime PID and its OS-reported descendants, including children of every
thread. It excludes pytest and the independent load generators. CPU sums
process user/system time, OS-accounted waited-child time and live descendant
time, including all runtime threads. CPU seconds divided by sampled elapsed
seconds gives logical-core equivalents, not an overlapping-span percentage.
RSS is the sum of resident pages at 250-ms intervals. Raw PID/start-time,
per-process counters, collection errors and every resource sample are retained.
PID reuse, changed parentage, missing proc entries and decreasing aggregate
CPU are measurement gaps, not zero usage. Tree snapshots are not atomic;
short-lived unwaited/orphaned children and sub-sample RSS peaks remain limits.
Shared pages may be counted more than once, so this is neither PSS nor cgroup
peak accounting. Hosted results do not establish Mini capacity.

The summary records absolute measurements and differences from the larger
bracketing baseline for normal and burst phases, plus retained normal-phase
RSS growth. It never evaluates the proposed budgets as acceptance. Harness
correctness and production-composition smoke tests must pass before the full
diagnostic runs. Each job has a 35-minute outer timeout; request timeout is
10 seconds, phase drain is 15 seconds, and only its owned synthetic processes
are terminated during cleanup. Partial raw files and a prewritten manifest
remain available on failure. The hosted artifact retention is seven days.
No private import, live process inspection, provider operation or local lab is
involved. Hardware measurements remain separately authorized work.

Completed request-profile, final-authority-control and paired Tasks-no-op
experiments are preserved as explicit `workflow_dispatch` opt-ins, disabled by
default. Their fixed-corpus correctness check still runs. This avoids repeating
unchanged exploratory experiments automatically; all existing correctness,
browser and real-load gates remain in the normal workflow.

The terminal `b1ac7a25` light baseline was 4.498/4.486 ms and mixed p95 was
5.719 ms (+1.221 ms, +27.156%). Planning was 3.812/3.708 ms versus 4.811 ms
(+0.999 ms, +26.199%). Both unchanged 10% gates failed. These older summary-only
runs still provide neither raw p99 nor whole-process CPU evidence. The new
collector does not retroactively fill those gaps.
