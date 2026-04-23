"""
Parties projection handlers — CRM spine.

Implemented in prompt 05 per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3.

Subscribes to `contacts.*`, `messaging.received/sent`, `telephony.*`,
`identity.*`, `party.created`, `relationship.added`. Builds tables:
`parties`, `identifiers`, `memberships`, `relationships`.

Key rules:
- Every addressable entity is a Party with a stable ULID that never changes
  (§3 invariant 1).
- `(tenant_id, party_id)` uniquely identifies — same email across tenants
  produces two different ids (§3 invariant 2, tenant isolation §12).
- Identity merges emit `party.merged`; collapsed ids still resolve via the
  identity index so no link dangles (§3 invariant 3).
- `identifiers.value_normalized` is canonicalized (E.164 phones, lowercased
  emails) for exact-match merge (architecture-summary.md §4 table row 3.1).

Do not implement in this scaffolding prompt. Prompt 05 will fill in.
"""
