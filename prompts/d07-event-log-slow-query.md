# Diagnostic d07: Event log queries are slow

**Symptom.** A `read_since` or `get_by_correlation` call takes more than a few hundred milliseconds even on a moderately-sized log (say, 10k events). The console's today view takes visibly long to load. Pipelines lag behind new events.

**When to use.** Anytime. The event log is on the hot path of every read; slowness here cascades everywhere.

---

## Read first

1. `adminme/events/log.py` (the implementation).
2. The schema migration files in `adminme/events/migrations/` — specifically indexes.
3. The failing query. What does the caller pass? Are they filtering by `owner_scope`, `type`, or `correlation_id`?

## Likely causes (ranked)

1. **Missing index.** Query filters by a column that isn't indexed. SQLite falls back to full table scan.
2. **JSON column queries.** If you're filtering or sorting on a field inside the `payload` or `source` JSON blob, SQLite must parse every row's JSON. Slow.
3. **Autocommit per insert.** 10k single-insert transactions is 10k fsync calls. Most of the "append" cost is actually commit cost. Batching is critical.
4. **SQLCipher overhead.** SQLCipher adds ~10-30% overhead on every query due to encryption. Fine normally, but compounds with other issues.
5. **No connection pooling / opening a new connection per query.** Each SQLCipher open involves key derivation (thousands of PBKDF2 iterations by design). Slow. The log should hold a single long-lived connection (or small pool).

## Procedure

1. **Measure.** Add a timing harness around the slow call:
   ```python
   import time
   t0 = time.perf_counter()
   result = await log.read_since(owner_scope="household", limit=1000)
   print(f"elapsed: {(time.perf_counter() - t0)*1000:.0f}ms")
   ```
2. **Get SQLite's query plan.** Connect to the DB (after unlocking with the key):
   ```sql
   EXPLAIN QUERY PLAN
   SELECT * FROM events WHERE owner_scope = ? AND event_at_ms > ? ORDER BY event_at_ms LIMIT 1000;
   ```
   If you see "SCAN TABLE events" → missing index.
3. **Check index existence:**
   ```sql
   SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'events';
   ```
   Expected indexes per prompt 03: `idx_owner_scope_time`, `idx_type_time`, `idx_correlation`.
4. **If index is present but query still slow:** run `ANALYZE;` to refresh statistics.
5. **Connection pooling.** Log should hold one persistent connection in async-safe usage. If connections are being opened per-call, fix that.

## Fix pattern

**A.** Any WHERE clause that will see regular use needs an index. When a new query pattern is introduced in a later prompt, add the matching index in a migration:

```sql
-- migrations/0012_add_source_index.sql
CREATE INDEX idx_events_source_adapter
  ON events(json_extract(source, '$.adapter_id'))
  WHERE json_extract(source, '$.adapter_id') IS NOT NULL;
```

**B.** Avoid JSON-column queries on the hot path. If a field is filtered regularly, promote it to a proper column (via migration + handler change).

**C.** Batch appends. `append_batch` should wrap in a single transaction with fsync at end. The 10k-events-in-1.5s target from prompt 03 requires this.

**D.** Persistent connection. Open once at `EventLog.__init__`; re-use for the log's lifetime; close at `EventLog.close()`. SQLite's python bindings are thread-safe with the default check_same_thread setting; for async use, wrap in `asyncio.to_thread` where blocking.

## Verify fix

```bash
# Benchmark script from prompt 03
poetry run python scripts/benchmark_event_log.py
# Expect: 10k appends under 1500ms; 10k reads under 200ms; specific queries under 50ms.

# Query plan for hot queries should show index usage
sqlite3 ~/.adminme/events.db "EXPLAIN QUERY PLAN SELECT ..."
```

Targets for a 100k-event log on lab MacBook hardware:
- `read_since(cursor=X, limit=1000, owner_scope=Y)`: < 50ms
- `get_by_correlation(id)`: < 10ms
- `count()`: < 20ms
- `append(one event)`: < 5ms
- `append_batch(1000 events)`: < 200ms

## Escalate if

After A-D, queries are still slow. Suspect:
- Disk is full or near-full (SQLite degrades on low-disk).
- Hardware issue (SSD failing). Check with `diskutil verifyDisk` on macOS.
- A process is holding a long-lived transaction (preventing checkpoint). Check with:
  ```sql
  PRAGMA wal_checkpoint(FULL);
  ```
  If this returns a large "checkpointed" number, something was blocking.

Final fallback: if the log has grown so large (say, 10M+ events over years) that vanilla SQLite struggles, partitioning becomes relevant. That's a v2 consideration and should be scoped with Anthropic-scale migration planning; don't do it ad-hoc.
