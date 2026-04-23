"""
PipelineRunner — in-process reactive pipeline executor.

Implemented in prompt 10a per ADMINISTRATEME_BUILD.md §L4 and SYSTEM_INVARIANTS.md §7.

Reactive pipelines subscribe via `triggers.events` in their `pipeline.yaml`
manifest and run inside this runner (§7 invariant 1). Proactive pipelines
register as OpenClaw standing orders at product boot — NOT here — so they
share OpenClaw's approval, observation-mode, and rate-limit machinery
(§7 invariant 2, §14 invariant 1, DECISIONS.md §D1).

Key rules:
- No pipeline writes directly to a projection or an xlsx file; pipelines emit
  events, projections consume them (§7 invariant 3).
- Pipelines invoke skills ONLY through `await run_skill(skill_id, inputs, ctx)`,
  which wraps POST http://127.0.0.1:18789/skills/invoke; pipelines NEVER
  import anthropic / openai / any provider SDK (§7 invariant 4, §8 invariant 2).
- A pipeline failure on one event does not halt the bus — the runner records,
  retries per policy, and continues (§7 invariant 7).

Pipeline packs are installed at runtime under InstanceConfig-resolved paths
(§15/D15); this runner discovers them via the resolved packs_dir and never
hardcodes a path.

Do not implement in this scaffolding prompt. Prompt 10a will fill in.
"""
