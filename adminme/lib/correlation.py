"""
correlation_id and causation_id helpers.

Implemented in prompt 03 per DECISIONS.md §D8.

Base rule: the originating adapter or L5 surface sets `correlation_id`;
it is preserved unchanged through every derived event. `causation_id` is set
to the `event_id` of the immediate parent. Neither is ever overwritten
downstream (§D8 base rule, SYSTEM_INVARIANTS.md §16 item 8).

Additions:
- Every adapter and every L5 surface endpoint generates a `correlation_id`
  on entry if the inbound request does not carry one. The originating event
  always has a correlation_id (D8 addition 1).
- Every `EventStore.append()` call site must pass both `correlation_id` and
  `causation_id` as explicit keyword arguments. Passing `None` is allowed
  when genuinely unknown; defaulting them via function signature is not.
  A lint rule (ruff custom check or grep-based test) enforces this
  (D8 addition 2).

Do not implement in this scaffolding prompt. Prompt 03 will fill in.
"""
