# Bootstrap wizard sections

Gate 5 implements the ten constitutional sections in `bootstrap/wizard.py`.
Each section records `not_started`, `in_progress`, `complete`, `failed`, or
`rolled_back` in an AES-GCM encrypted journal. A completed section is a no-op
when its version and input digest have not changed.

1. Environment and mutation-free preflight
2. Assistant identity and persona/SOUL compilation
3. Parties, Memberships, Relationships, and member roles
4. Profile assignment and behavior preview
5. Keychain/secure-provider credential references
6. Plaid sandbox selection and Link state
7. Household and CRM seed data, with Inbox follow-ups for skips
8. Channels, shared tools, adapters, permissions, and launchd definitions
9. Observation briefing, trust-period contract, and canonical proof
10. Per-member Apple bridge enrollment, or an explicit non-Apple skip

The credential-free `two-adult-one-child` fixture is reproducible process
evidence; it is not a claim that any live provider or family Mac was mutated.
Interactive use never asks for a secret: OAuth and macOS grants happen in
their own provider/system windows, and the wizard stores references only.
