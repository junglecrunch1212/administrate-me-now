"""
L2 Event Bus — in-process asyncio pub/sub layered on the event log.

Implemented in prompt 03 per ADMINISTRATEME_BUILD.md §L2 and SYSTEM_INVARIANTS.md §1.

This module will expose:
- `EventBus` Protocol — the contract both bus implementations satisfy.
- `InProcessBus` — default: asyncio queues + durable per-subscriber offsets in
  a `bus_consumer_offsets` table (§1 invariant 10).
- `RedisStreamsBus` — spec'd alternate for future scale-out; full pipeline
  integration suite runs against both.

`EventStore.append()` publishes to the bus inside the same transaction
boundary so a crash between insert and publish cannot silently lose an event
(§1 invariant 4).

Do not implement in this scaffolding prompt. Prompt 03 will fill in.
"""
