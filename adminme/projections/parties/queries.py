"""
Parties projection read queries — the CRM read surface.

Implemented in prompt 05 per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3.

All reads go through a `Session(current_user, requested_scopes)` object that
auto-appends the scope predicate (§6 invariant 4). No code in this module
imports `sqlalchemy.orm.Session` directly (§6 invariant 1).

Per DECISIONS.md §D4: the CRM spine is a shared L3 concern, not a product
concern. Any Python product (Core, Comms, Capture, Automation) may read
parties directly via its local Session connection; Capture owns the
human-facing CRM surfaces (`/api/capture/parties`) but not the data.

Do not implement in this scaffolding prompt. Prompt 05 will fill in.
"""
