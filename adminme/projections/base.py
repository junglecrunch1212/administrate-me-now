"""
Projection protocol — the contract every L3 projection satisfies.

Implemented in prompt 05 per ADMINISTRATEME_BUILD.md §L3 and SYSTEM_INVARIANTS.md §2.

Every projection has a `name`, a `version` integer (bumped to trigger rebuild),
an event-type subscription list, a durable cursor, an idempotent `apply(event)`,
and a `rebuild()` that truncates and replays from event 0 producing state
equivalent to the live cursor (§2 invariants 1, 4).

Projection handlers are deterministic pure functions over `(state, event)` —
no wall-clock reads, no random, no UUIDs, no network, no calls to other
projections, no calls back to the event log beyond the cursor advance
(§2 invariant 3). Projections never write back to the event log (§2 invariant 2).

Do not implement in this scaffolding prompt. Prompt 05 will fill in.
"""
