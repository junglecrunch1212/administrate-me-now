"""
Event schema registry — `event_type` → Pydantic model mapping.

Implemented in prompt 04 per ADMINISTRATEME_BUILD.md §L2 and SYSTEM_INVARIANTS.md §1.

Every `event_type` ships a Pydantic model under `adminme/events/schemas/`,
a `schema_version` integer, and upcasters on schema change
(§1 invariant 9, DECISIONS.md §D7).

This module will expose:
- `register(event_type: str, model: type[BaseModel], *, version: int)` — called
  at import time by each schema module.
- `get_model(event_type, version)` and `get_current_version(event_type)`.
- `upcast(payload, event_type, from_version, to_version)` — composes upcasters.

Plugin-introduced event types register via the `hearth.event_types` entry
point in their own dotted namespace (DECISIONS.md §D9).

Do not implement in this scaffolding prompt. Prompt 04 will fill in.
"""
