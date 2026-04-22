# Prompt 03: Event log + event bus (L2)

**Phase:** BUILD.md PHASE 1 ("Event log + bus + minimal event schemas"), minus the schemas part (which is prompt 04).
**Depends on:** Prompt 02 passed. Scaffolding + deps exist.
**Estimated duration:** 3-4 hours.
**Stop condition:** Event log accepts appends, supports read-since-cursor, survives process restart; event bus fans out to multiple subscribers with per-subscriber checkpoints; all unit tests pass; a demo script appends and replays 100 events.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` sections (use grep to locate by heading):
   - **"L2: THE EVENT LOG (SOURCE OF TRUTH)"** — invariants, partitioning, schema.
   - **"L2: THE EVENT BUS"** — pub/sub semantics, checkpoints, replay.
2. `ADMINISTRATEME_DIAGRAMS.md` §1 (five-layer architecture). Look at the arrows into and out of L2.
3. `docs/architecture-summary.md` §3 (event log invariants) — your own summary from prompt 01.

Do NOT read:
- The event schemas section (prompt 04).
- Projection details (prompts 05-07).

## Operating context

L2 is the source of truth. Everything above it (L3 projections, L4 pipelines) reads from it; everything below it (L1 adapters) writes to it. If the event log is wrong, the whole system is wrong — it's the one thing that cannot be rebuilt from something else. Treat it with appropriate respect.

Two modules this prompt implements:

- **`adminme/events/log.py`**: the `EventLog` class. Append-only, SQLCipher-encrypted, partitioned by `owner_scope`. Writes are atomic; reads are read-committed; replay is deterministic.
- **`adminme/events/bus.py`**: the `EventBus` class. In-process pub/sub on top of the log. Subscribers register for event types; the bus fans out new events to them and tracks a checkpoint per subscriber so they can resume after restart.

Events at this point are opaque dicts — typed-payload validation comes in prompt 04. Here we care about storage, ordering, retrieval, and fan-out only.

## Objective

Implement the event log and event bus, both with tests, such that:

1. Events can be appended and read back in insertion order.
2. Events are partitioned by `owner_scope` and can be filtered by it on read.
3. The log survives process restart — reading from it after a restart returns the same events.
4. The bus fans out to multiple subscribers in parallel.
5. Each subscriber has a persistent checkpoint; on restart, subscribers resume from their checkpoint.
6. Queries are fast enough: read-since-cursor for 10k events returns in under 100ms on lab hardware.
7. Encryption key is derived from 1Password secret reference (do not store plaintext keys).

## Out of scope

- Do NOT implement event schemas. Events are opaque dicts with a `type` field at minimum. Prompt 04 handles typed validation.
- Do NOT implement projections. The bus exists, but no projection consumes events in this prompt.
- Do NOT implement replay-from-cursor for projection rebuild. That's part of prompt 05.
- Do NOT integrate with OpenClaw yet.

## Deliverables

### `adminme/events/log.py`

```python
"""
L2 Event Log — append-only SQLCipher-backed event storage.

Per BUILD.md §"L2: THE EVENT LOG".

Schema (one table):
    events (
        event_id        TEXT PRIMARY KEY,     -- e.g., "ev_0k8m2n4p6q8r"
        event_at_ms     INTEGER NOT NULL,     -- insertion time, millis since epoch
        tenant_id       TEXT NOT NULL,
        owner_scope     TEXT NOT NULL,        -- partition key
        type            TEXT NOT NULL,        -- event type string
        version         INTEGER NOT NULL,     -- schema version
        correlation_id  TEXT,
        source          TEXT,                 -- json
        payload         TEXT NOT NULL         -- json
    )

Indexes:
    idx_owner_scope_time (owner_scope, event_at_ms)
    idx_type_time (type, event_at_ms)
    idx_correlation (correlation_id) WHERE correlation_id IS NOT NULL

Partitioning: at v1, the owner_scope is a column, not a physical partition.
A future migration may move to per-owner_scope files if query performance demands.
"""
```

Public API:

```python
class EventLog:
    def __init__(self, db_path: Path, encryption_key: bytes):
        """Opens or creates the SQLCipher-encrypted log. Migrates to latest schema."""

    async def append(self, event: dict) -> str:
        """
        Appends one event. Validates minimum fields (type, tenant_id, owner_scope, payload).
        Generates event_id if not provided. Returns event_id.
        Atomic: failure raises; partial-write impossible.
        """

    async def append_batch(self, events: list[dict]) -> list[str]:
        """Same as append, for many. Atomic over the batch."""

    async def read_since(
        self,
        cursor: str | None = None,
        *,
        limit: int = 1000,
        owner_scope: str | None = None,
        types: list[str] | None = None,
    ) -> AsyncIterator[dict]:
        """
        Yields events after the cursor (exclusive), in append order.
        If cursor is None, starts from the beginning.
        Filters by owner_scope and/or type set if provided.
        """

    async def get(self, event_id: str) -> dict | None:
        """Single event fetch by ID."""

    async def get_by_correlation(self, correlation_id: str) -> list[dict]:
        """All events with the given correlation_id, in append order."""

    async def latest_event_id(self) -> str | None:
        """Tip of the log."""

    async def count(self) -> int: ...
```

**Implementation notes:**

- Use `aiosqlite` with SQLCipher via `sqlcipher3-binary` (import as `sqlcipher3`). If async SQLCipher is hard in pure Python, you may wrap sync SQLCipher calls in `asyncio.to_thread()` — call that decision out in a module comment.
- Event ID generation: `ev_` prefix + 14-char ULID-like time-sortable suffix. Deterministic: two events inserted in the same millisecond get different IDs (use a monotonic counter within a millisecond).
- Encryption key: the `EventLog.__init__` receives raw bytes. A separate helper `adminme/lib/crypto.py::derive_event_log_key(op_ref: str) -> bytes` reads from 1Password. Stub that helper if 1Password CLI isn't wired up yet.
- Migrations: table `_schema_version` tracks which migration has run. On init, run any pending. Each migration is a SQL file in `adminme/events/migrations/0001_initial.sql`, `0002_add_correlation_index.sql`, etc.

### `adminme/events/bus.py`

```python
"""
L2 Event Bus — in-process pub/sub on top of the event log.

Per BUILD.md §"L2: THE EVENT BUS".

Design:
- One EventBus instance per process.
- Subscribers register with (subscriber_id, types_of_interest, callback).
- When events are appended to the log, the bus calls matching subscribers.
- Each subscriber has a checkpoint persisted in a separate sqlite db
  (adminme/events/bus_checkpoints.db, unencrypted — it's just cursor state).
- On bus startup, for each subscriber that's been registered before,
  it replays events from the checkpoint forward so the subscriber catches up.

Callbacks are async. The bus awaits them sequentially per subscriber
(within one subscriber, events arrive in order). Across subscribers,
they run in parallel.

If a subscriber callback raises, the exception is logged and the
checkpoint is NOT advanced — the event will be retried on the next
fan-out cycle. After N consecutive failures (default 5), the subscriber
is marked degraded and the operator is notified.
"""
```

Public API:

```python
class EventBus:
    def __init__(self, log: EventLog, checkpoint_db: Path):
        """Bus wraps the log. Manages its own checkpoint db."""

    async def start(self) -> None:
        """Start fan-out loop. For each registered subscriber, replay from checkpoint."""

    async def stop(self) -> None:
        """Graceful shutdown. Await in-flight callbacks to finish; persist checkpoints."""

    def subscribe(
        self,
        subscriber_id: str,
        types: list[str] | Literal["*"],
        callback: Callable[[dict], Awaitable[None]],
    ) -> None:
        """
        Register a subscriber. Must be called before start().
        types="*" means all events.
        Callback receives the full event dict.
        """

    async def notify(self, event_id: str) -> None:
        """
        Called immediately after EventLog.append(). Wakes the fan-out loop
        to process the new event. (If the bus is not running, the event
        stays in the log; on next start, the subscribers catch up via replay.)
        """

    async def subscriber_status(self, subscriber_id: str) -> dict:
        """
        Returns {checkpoint_event_id, lag_count, last_success_at, last_failure_at,
                 consecutive_failures, degraded: bool}.
        """
```

**Critical behaviors:**

1. **Durability:** Events are only considered delivered to a subscriber when the callback returns without raising AND the checkpoint is persisted. If the process crashes between callback return and checkpoint persist, the event will be re-delivered after restart. Subscribers must be idempotent.
2. **Ordering:** Per subscriber, events are delivered in append order. Across subscribers, no ordering guarantee.
3. **Backpressure:** If a subscriber is slow, its checkpoint lags. The bus does not drop events. If lag exceeds 10,000 events, log a warning.
4. **Degraded state:** After 5 consecutive callback failures for one subscriber, mark degraded, log loudly, stop delivering to that subscriber until cleared. A degraded subscriber can be cleared via `bus.reset_subscriber(subscriber_id)`.

### Tests

`tests/unit/test_event_log.py`:

- Append 1 event, read it back. IDs and fields match.
- Append 1000 events, read all back. Order matches insertion order.
- Append with explicit event_id, read back — ID is preserved.
- Append batch of 100, all present atomically.
- Append with correlation_id, get_by_correlation returns them in order.
- Read-since-cursor with cursor == event_id of first event yields events 2..N.
- Filter by type set: only matching events returned.
- Filter by owner_scope: only matching events returned.
- `read_since` with cursor not in log raises `CursorNotFound`.
- Persistence: close the log, reopen, events still there.
- Encryption: the raw .db file is not readable with a bad key.
- Event IDs within the same millisecond are unique and sortable.
- Latest_event_id returns the tip.

`tests/unit/test_event_bus.py`:

- Single subscriber receives an event appended after subscribe.
- Single subscriber on `types=["foo"]` does not receive events of type "bar".
- Wildcard subscriber receives all events.
- Multiple subscribers all receive the same event.
- Subscriber callback that raises: event is re-delivered on next cycle; checkpoint not advanced.
- After 5 consecutive failures, subscriber is marked degraded; no more deliveries; `reset_subscriber` restores.
- Checkpoint persists: stop bus, restart, subscriber resumes from last successful checkpoint.
- Subscriber registered AFTER events already exist: on `start()`, receives all prior events from its starting checkpoint.
- Concurrent appends are all delivered; none dropped.
- `subscriber_status` reflects lag correctly.

### Demo script

`scripts/demo_event_log.py`:

```python
"""Appends 100 synthetic events, replays them via a bus subscriber,
verifies count matches, prints timing. Uses a tmpdir; doesn't touch ~/.adminme/."""
```

Output should look like:

```
Appending 100 events...            elapsed: 12ms
Registering subscriber...
Starting bus...
Subscriber received 100 events.    elapsed: 35ms
Stopping bus; persisting checkpoints...
Re-opening log + bus...
Subscriber's checkpoint is at ev_xxxxx
Appending 10 more events...
Subscriber received 10 more.       elapsed: 8ms
Total events: 110. Checkpoint: ev_yyyyy.
```

## Verification

```bash
# Lint + type check
poetry run ruff check adminme/events/
poetry run mypy adminme/events/

# Tests
poetry run pytest tests/unit/test_event_log.py -v
poetry run pytest tests/unit/test_event_bus.py -v

# Timing sanity check (lab hardware)
poetry run python -c "
import asyncio, time, tempfile, os
from pathlib import Path
from adminme.events.log import EventLog

async def main():
    with tempfile.TemporaryDirectory() as td:
        log = EventLog(Path(td)/'events.db', b'x'*32)
        events = [{'type': 'test.event', 'tenant_id': 't', 'owner_scope': 'household',
                   'version': 1, 'payload': {'i': i}} for i in range(10_000)]
        t0 = time.perf_counter()
        await log.append_batch(events)
        t1 = time.perf_counter()
        count = 0
        async for ev in log.read_since():
            count += 1
        t2 = time.perf_counter()
        print(f'Appended 10k: {(t1-t0)*1000:.0f}ms. Read 10k: {(t2-t1)*1000:.0f}ms. Count: {count}')

asyncio.run(main())
"

# Demo
poetry run python scripts/demo_event_log.py

git add adminme/events/ tests/unit/test_event_log.py tests/unit/test_event_bus.py scripts/demo_event_log.py
git commit -m "phase 03: event log + event bus (L2)"
```

Expected:
- All tests pass (at least 15 unit tests, probably 20+).
- Timing check: 10k appends under 1500ms; 10k reads under 200ms. If you're way off these, investigate (most likely: missing indexes, or wrapping each insert in its own transaction).
- Demo produces the expected output shape.

## Stop

**Explicit stop message:**

> L2 is in. Event log is durable and encrypted; event bus fans out with per-subscriber checkpoints. Ready for prompt 04 (typed event schemas). Please confirm by re-running the test suite and the demo before proceeding.

Do not begin schema work in this session. That is prompt 04.
