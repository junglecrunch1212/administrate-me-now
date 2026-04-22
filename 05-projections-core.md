# Prompt 05: Projections core (parties, interactions, artifacts)

**Phase:** BUILD.md PHASE 2 (projections half) — CRM primitives.
**Depends on:** Prompt 04 passed.
**Estimated duration:** 4-5 hours.
**Stop condition:** Three projections consume events, expose query functions, and can be rebuilt from the event log deterministically.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md`:
   - **"L3: PROJECTIONS"** section — projection contract, rebuild semantics.
   - **"3.1 parties"** through **"3.3 artifacts"** subsections.
   - **"THE CRM IS THE SPINE OF THIS SYSTEM"** — why parties matter.
2. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §4 — the canonical parties projection example (schema, handlers, queries).
3. `ADMINISTRATEME_DIAGRAMS.md` §1 and §2 — where projections sit, how events flow through them.
4. `docs/architecture-summary.md` §4 (your own from prompt 01).

## Operating context

Projections are read models. Each subscribes to certain event types via the bus (prompt 03), writes rows into its own SQLite database, and exposes query functions for L4 pipelines and L5 surfaces to consume. Projections NEVER write back to the event log. They are derived state; the event log is truth.

Three projections in this prompt:
- **parties** — the CRM spine. People, organizations, households.
- **interactions** — timestamped touchpoints between parties (derived from messaging, calls, meetings).
- **artifacts** — documents, images, structured records.

Each projection lives in `platform/projections/<name>/` and follows the pattern in REFERENCE_EXAMPLES.md §4.

## Objective

Build three projections. Each has:
- `schema.sql` — SQLite DDL.
- `handlers.py` — event handlers that `UPSERT` rows.
- `queries.py` — read functions for L4/L5 callers.
- `tests/` — at least one test per handler per event type, plus rebuild correctness tests.

Plus a **projection runner** (`platform/projections/runner.py`) that:
- Discovers registered projections.
- Subscribes each to the bus for its declared event types.
- Dispatches events to handlers.
- Manages a per-projection checkpoint (integrates with bus checkpointing).
- Exposes `rebuild(projection_name)` — drops the projection DB and replays the whole event log through the handlers.

## Out of scope

- Do NOT build projections not listed above (commitments, tasks, etc. are prompt 06; money, xlsx, etc. prompt 07).
- Do NOT build L4 pipelines that would write to these projections (prompts 10a-c).
- Do NOT add schemas for events not yet registered in prompt 04 — just handle the ones that exist. Later prompts that add event types will also add their projection handling if relevant.

## Deliverables

### `platform/projections/base.py`

Projection protocol: class with `schema_path`, `subscribes_to`, `apply(event, conn)`, optional `after_batch(conn)`. Standard interface for the runner.

### `platform/projections/runner.py`

```python
class ProjectionRunner:
    def __init__(self, bus: EventBus, projections_root: Path): ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def rebuild(self, name: str) -> None:
        """Drop projection db; replay entire event log; reset checkpoint."""
    async def status(self) -> dict:
        """Per-projection: row counts, checkpoint, lag, last_event_applied."""
```

### The three projections

For each, follow REFERENCE_EXAMPLES.md §4's full worked example. Key events each handles:

**parties:**
- `party.created` v1 → INSERT party row
- `identifier.added` v1 → INSERT identifier row
- `membership.added` v1 → INSERT membership row
- `relationship.added` v1 → INSERT relationship row

Queries: `get_party(id)`, `find_by_identifier(kind, value)`, `list_household_members()`, `relationships_of(party_id)`, `merge_parties(a, b)` (emits `party.merged` event — that's a TODO for prompt 10b).

**interactions:**
- `messaging.received` v1 → append interaction row
- `messaging.sent` v1 → append interaction row  
- `telephony.sms_received` v1 → append interaction row

Queries: `recent_with(party_id, days)`, `thread(thread_id)`, `closeness_signals(party_id, since)`.

**artifacts:**
- `artifact.received` v1 → INSERT artifact row (mime_type, extracted_text, links to parties)

Queries: `get(id)`, `search_by_party(party_id)`, `search_by_text(query)`.

### Tests

Per projection:
- Apply event; assert row exists in correct shape.
- Apply same event twice; assert idempotent (no duplicate rows).
- Replay test: start fresh DB, replay 100 fixture events, assert final state matches expected.
- Query tests with small fixture data.

Integration test `tests/integration/test_projection_rebuild.py`:
- Populate event log with 500 mixed events.
- Start runner; let projections catch up.
- Call `rebuild("parties")`.
- Assert post-rebuild state matches pre-rebuild state (byte-for-byte on row data).

### Also: add schemas

This prompt emits new event types: `party.merged` v1 (stub — the pipeline that emits it is later, but the schema can be defined here so projections have something to react to when prompt 10b arrives). Add to `platform/events/schemas/crm.py`.

## Verification

```bash
poetry run pytest tests/unit/test_projection_*.py tests/integration/test_projection_rebuild.py -v
poetry run python scripts/demo_event_log.py  # still passes
poetry run python -c "
# Quick smoke: start a bus+runner with just parties projection, emit 10 events, read back.
# (write as a mini script or inline here)
"

git add platform/projections/ tests/
git commit -m "phase 05: projections core (parties, interactions, artifacts)"
```

## Stop

**Explicit stop message:**

> Three CRM projections in. Runner handles discovery, dispatch, and rebuild. Ready for prompt 06 (domain projections: commitments, tasks, recurrences, calendars).

