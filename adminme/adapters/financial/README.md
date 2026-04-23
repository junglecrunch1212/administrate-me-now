# Financial adapters (L1)

Financial-source translators: Plaid (primary), Privacy.com. Each emits
`financial.*`, `plaid.*`, and `money_flow.*` events feeding the `money` and
`places_assets_accounts` projections.

Per ADMINISTRATEME_BUILD.md §PLAID — DETAILED SPEC, Plaid amounts are stored
as `amount_minor` (smallest currency unit) + ISO 4217 currency; credentials
are `op://` / `1password://` references, never raw.

Filled in by prompt 11 and the Plaid-specific prompt.
