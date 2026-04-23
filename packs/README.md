# Built-in packs

Built-in packs live here in the repo and are installed to the instance's
`packs/` subdirectory (resolved via InstanceConfig, never a hardcoded
`~/.adminme/packs/` path per SYSTEM_INVARIANTS.md §15 / DECISIONS.md §D15)
during bootstrap.

Per architecture-summary.md §7 and ADMINISTRATEME_BUILD.md §PACK REGISTRY,
six pack kinds — each with its own subdirectory here:

- `adapters/` — L1 translators from external sources to typed events.
- `pipelines/` — L4 event subscribers that emit derived events / proposals /
  skill calls.
- `skills/` — L4 SKILL.md + schemas + optional `handler.py`. Installed into
  OpenClaw via the skill-loader path or ClawHub. Shape is fixed per
  DECISIONS.md §D11.
- `profiles/` — L5 bundle of JSX views + engines + tuning + prompts, assigned
  per member. 5 built-in: `adhd_executive`, `minimalist_parent`, `power_user`,
  `kid_scoreboard`, `ambient_entity`.
- `personas/` — agent identity (one per instance). Compiled into SOUL.md at
  activation. 4 built-in: `poopsy`, `butler_classic`, `friendly_robot`,
  `quiet_assistant`.

Filled in by subsequent prompts.
