"""
Scope enforcement — defense-in-depth around every projection query.

Implemented in prompt 08 per ADMINISTRATEME_BUILD.md §L3-continued and SYSTEM_INVARIANTS.md §6.

Scope predicates auto-append to every projection query:
`WHERE visibility_scope IN (allowed_scopes)
   AND (sensitivity != 'privileged' OR owner_scope = current_user)`

Every projection test ships a canary that expects `ScopeViolation` on
out-of-scope reads (§6 invariant 4).

Do not implement in this scaffolding prompt. Prompt 08 will fill in.
"""
