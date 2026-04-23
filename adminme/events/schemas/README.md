# Event schemas

One file per event family (e.g. `messaging.py`, `commitment.py`, `task.py`,
`calendar.py`, `financial.py`, `plaid.py`). Each file defines Pydantic models
for the event types in that family and registers them via
`adminme.events.registry:register` at import time.

Per SYSTEM_INVARIANTS.md §1 invariant 9, every `event_type` ships a Pydantic
model, a `schema_version` integer, and an upcaster on schema change. Per
DECISIONS.md §D7, `schema_version` is a monotonically increasing integer per
`event_type`; upcasters are pure functions named
`upcast_v{N}_to_v{N+1}(payload: dict) -> dict`.

Plugin-introduced event types register via the `hearth.event_types` entry
point in their own dotted namespace (DECISIONS.md §D9).

Filled in by prompt 04.
