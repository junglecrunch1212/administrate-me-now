# Prompt 06: Projections domain (commitments, tasks, recurrences, calendars)

**Phase:** BUILD.md PHASE 2 continued.
**Depends on:** Prompt 05.
**Estimated duration:** 3-4 hours.
**Stop condition:** Four projections + full domain queries + rebuild correctness.

## Read first

1. `ADMINISTRATEME_BUILD.md` sections **"3.4 commitments"** through **"3.7 calendars"**.
2. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §5 (commitment.proposed event, shows what commitments projection consumes).
3. Results of prompt 05 — same pattern repeats.

## Objective

Four more projections in the same shape as prompt 05.

## Deliverables

- `platform/projections/commitments/` — consumes `commitment.proposed`, `commitment.confirmed`, `commitment.completed`, `commitment.dismissed`, `commitment.updated`. Queries: `open_for_member(member_id)`, `get(id)`, `pending_approval()`, `by_party(party_id)`.
- `platform/projections/tasks/` — consumes `task.created`, `task.completed`, `task.updated`, `task.deleted`. Queries: `today_for_member(member_id)`, `open_for_member(member_id)`, `by_context(ctx)`.
- `platform/projections/recurrences/` — consumes `recurrence.added`, `recurrence.completed`, `recurrence.updated`. Queries: `due_within(days)`, `for_member(member_id)`, `all_active()`.
- `platform/projections/calendars/` — consumes `calendar.event_added`, `calendar.event_updated`, `calendar.event_deleted`. Queries: `today(member_id)`, `week(member_id, start_date)`, `busy_slots(member_id, range)`.

Add schemas for any events not yet registered: `task.updated`, `task.deleted`, `commitment.completed`, `commitment.dismissed`, `commitment.updated`, `recurrence.added`, `recurrence.completed`, `recurrence.updated`, `calendar.event_updated`, `calendar.event_deleted`.

Tests per projection as in prompt 05. Rebuild correctness test.

## Verification

Same shape as prompt 05. All previous tests still pass.

```bash
poetry run pytest -v
git commit -m "phase 06: domain projections"
```

## Stop

**Explicit stop message:**

> Four more projections. Domain state is rebuildable. Ready for prompt 07 (ops projections including bidirectional xlsx).

