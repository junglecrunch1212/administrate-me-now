# Unit tests

Fast, isolated tests. Includes canary tests:

- `test_no_hardcoded_identity.py` — enforces SYSTEM_INVARIANTS.md §12 /
  DECISIONS.md §D11 / BUILD.md rule 4.
- `test_no_hardcoded_instance_path.py` — enforces SYSTEM_INVARIANTS.md §15 /
  DECISIONS.md §D15.

Both canaries are stubs in this scaffolding phase and are filled in by later
prompts (prompt 03 for the instance-path canary alongside InstanceConfig;
prompt 08 or 17 for the identity canary).

All further unit tests added by phase prompts as implementation lands.
