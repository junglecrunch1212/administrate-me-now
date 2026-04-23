"""
L2 Event Log — append-only SQLCipher-backed event storage.

Implemented in prompt 03 per ADMINISTRATEME_BUILD.md §L2 and SYSTEM_INVARIANTS.md §1.

This module will expose:
- `EventStore`: owns the only writable connection to `events`; exposes
  `.append(event, *, correlation_id, causation_id)` that validates against the
  registered Pydantic model, inserts, commits, and publishes transactionally
  per §1 invariant 4.
- `.read_since(cursor)` and `.replay()` for projection rebuilds.
- Partitioning by `owner_scope` — indexed column, not a separate physical
  table (§1 invariant 6).
- SQLCipher encryption with key resolved via `adminme.lib.crypto`; paths
  resolved via `adminme.lib.instance_config` (never hardcoded per §15/D15).

Do not implement in this scaffolding prompt. Prompt 03 will fill in.
"""
