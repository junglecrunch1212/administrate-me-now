# Bootstrap wizard sections

One Python file per wizard section. The nine sections per
ADMINISTRATEME_BUILD.md §BOOTSTRAP WIZARD and SYSTEM_INVARIANTS.md §11:

1. Environment preflight (aborts on failure per §11 invariant 5)
2. Name your assistant
3. Household composition
4. Assign profiles
5. Assistant credentials
6. Plaid
7. Seed household data
8. Channel pairing (installs skill packs + plugins + standing orders +
   writes standing-order prose to `AGENTS.md` per DECISIONS.md §D1)
9. Observation briefing (observation mode enabled by default per
   SYSTEM_INVARIANTS.md §6 invariant 16 / §11 invariant 4)

Resumable via encrypted `bootstrap-answers.yaml.enc` + the event log
(§11 invariant 2). Idempotent on re-run (§11 invariant 3).

Filled in by prompt 16.
