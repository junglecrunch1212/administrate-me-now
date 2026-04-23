"""
Identifier normalization helpers — emails, phones, etc.

Implemented in prompt 05 per ADMINISTRATEME_BUILD.md §3.1 and architecture-summary.md §4.

`identifiers.value_normalized` is canonicalized for exact-match merge in the
parties projection: E.164 phones, lowercased emails (§3.1 table row).

Cross-tenant identity resolution is explicitly forbidden: the same email or
phone across two tenants produces two different `party_id`s
(SYSTEM_INVARIANTS.md §3 invariant 2, §12 tenant isolation).

Do not implement in this scaffolding prompt. Prompt 05 will fill in.
"""
