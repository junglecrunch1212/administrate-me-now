"""
Crypto utilities — SQLCipher key derivation and secret handling.

Implemented in prompt 03 per ADMINISTRATEME_BUILD.md §L2 and SYSTEM_INVARIANTS.md §1.

This module will expose:
- SQLCipher master-key derivation from the instance's secret store (1Password
  service account reference resolved at service-start time, never a raw
  credential in config).
- Fernet key derivation for at-rest artifact encryption at
  `~/.adminme/data/artifacts/<yyyy>/<mm>/<sha256>.<ext>` (§1 invariant 7),
  keyed from the SQLCipher master key.
- Path resolution for the artifact and raw-event sidecar directories
  resolves through `adminme.lib.instance_config` (never hardcoded per
  §15/D15).

Do not implement in this scaffolding prompt. Prompt 03 will fill in.
"""
