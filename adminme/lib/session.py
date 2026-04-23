"""
Session — (current_user, requested_scopes) wrapper for every read and write.

Implemented in prompt 08 per ADMINISTRATEME_BUILD.md §L3-continued and SYSTEM_INVARIANTS.md §6.

There is no global DB connection. Every read and every write happens under a
`Session(current_user, requested_scopes)` object; no code imports
`sqlalchemy.orm.Session` directly (§6 invariants 1-2).

Sessions carry BOTH an `authMember` (governs what you can do) and a
`viewMember` (governs whose data you are reading):
- Only principals may set view-as; ambient entities cannot be viewed-as;
  children cannot view-as (§6 invariant 2, CONSOLE_PATTERNS.md §2).
- Writes ALWAYS use authMember; viewMember never authorizes a write — a
  principal viewing-as another principal still writes under their own
  identity (§6 invariant 3).
- Two-member commitments record both ids separately (`approved_by=A`,
  `owner=B`) — do not collapse.

Do not implement in this scaffolding prompt. Prompt 08 will fill in.
"""
