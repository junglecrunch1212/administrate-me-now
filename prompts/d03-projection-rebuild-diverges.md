# Diagnostic d03: Projection rebuild produces different state than live projection

**Symptom.** You run `adminme projection rebuild parties`. The rebuild completes, but afterward the projection's row count, row contents, or query results differ from what they were before. This should never happen — rebuild replays the exact same events, so the result must be byte-identical.

**When to use.** During prompt 05, 06, 07, or after any schema migration. Also: if you suspect data corruption and want to verify the projection is a pure function of events.

---

## Read first

1. The projection's `handlers.py` and `schema.sql`.
2. `ADMINISTRATEME_BUILD.md` L3 section on projection invariants.
3. `tests/integration/test_projection_rebuild.py` if this projection has a rebuild test.

## Likely causes (ranked)

1. **Non-deterministic handler.** The handler uses `datetime.now()`, `random`, or `uuid.uuid4()` to fill a row. This is almost always the bug. Handlers must be pure functions of `(event, current_state)`.
2. **Order-dependent logic relying on wall clock.** Two events with the same millisecond timestamp but different processing order. If the handler assumes clock-strict ordering, it can produce different results.
3. **Merged state from prior projection life.** The projection DB wasn't actually dropped before rebuild. Some rows from the old state survive.
4. **Schema changed without migration.** The new schema drops a column the live projection had populated. After rebuild, that column is null/absent.
5. **Handler depends on side effects.** Handler calls out to another projection, reads from the event log, or queries external state. This is forbidden — handlers take (event, local_state) only.

## Procedure

1. Compare: dump pre-rebuild state vs post-rebuild state.
   ```bash
   adminme projection query parties --json > /tmp/pre.json
   adminme projection rebuild parties
   adminme projection query parties --json > /tmp/post.json
   diff <(jq -S . /tmp/pre.json) <(jq -S . /tmp/post.json)
   ```
2. For each divergent row, trace the events that produced it via `correlation_id` or `source`.
3. Read the handler for the event type that created that row. Look for:
   - `datetime.now()` / `time.time()` / any wall-clock call.
   - `random.*` / `uuid.uuid4()`.
   - HTTP calls, file I/O, other projection queries.
   - Sorting by a timestamp column that has ties.
4. If handler is clean, check whether the projection DB was truly dropped. Run `adminme projection rebuild parties --clean`. If the `--clean` flag doesn't exist, it's needed — add it.

## Fix pattern

**A.** Handlers must be pure. All timestamps and IDs come from the event, not wall clock. If a handler needs a fresh ID, derive it deterministically: `hashlib.sha256(f"{event_id}:{purpose}").hexdigest()[:12]`.

**B.** Rebuild drops the projection DB fully (delete file; recreate from `schema.sql`; replay events from event_at_ms=0 through the tip).

**C.** Add a property-based test:

```python
@given(event_list=events_strategy())
def test_projection_determinism(event_list):
    state1 = apply_events(event_list)
    state2 = apply_events(event_list)
    assert state1 == state2  # Must be byte-identical
```

## Verify fix

```bash
# Three rebuilds in a row must produce identical output
adminme projection rebuild parties
adminme projection query parties --json | md5sum
adminme projection rebuild parties
adminme projection query parties --json | md5sum
adminme projection rebuild parties
adminme projection query parties --json | md5sum
# All three md5 sums must match.

poetry run pytest tests/integration/test_projection_rebuild.py -v
```

## Escalate if

The handler looks pure but rebuilds still diverge. Suspect SQLite itself: JSON ordering, float serialization drift, index-dependent query results. Normalize all JSON storage to `sort_keys=True`, compare via `json.loads` not string compare, and explicitly `ORDER BY` every query.
