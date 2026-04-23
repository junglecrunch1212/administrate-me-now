"""
Event payload schemas — one module per event family.

Per SYSTEM_INVARIANTS.md §1 invariant 9 and DECISIONS.md §D7. Each module
defines one or more Pydantic payload models and calls
``adminme.events.registry.registry.register(...)`` at import time. Modules
here are discovered by ``SchemaRegistry.autoload()`` at registry bootstrap.

Layout (prompt 04):
- ingest.py    — L1 adapter emissions (messaging, telephony, calendar, artifact)
- crm.py       — CRM spine (parties, identifiers, memberships, relationships)
- domain.py    — commitments, tasks, skill calls
- governance.py — observation-mode suppression log

Plugin-introduced event types register via the ``hearth.event_types`` entry
point in their own dotted namespace (DECISIONS.md §D9); they do not live
here.

Later prompts (05 through 14) add additional schema modules as the sections
they build are fleshed out.
"""
