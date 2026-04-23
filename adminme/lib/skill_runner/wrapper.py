"""
Skill runner wrapper — thin client for OpenClaw's skill runner.

Implemented in prompt 09a per ADMINISTRATEME_BUILD.md §L4-continued and SYSTEM_INVARIANTS.md §7-§8.

AdministrateMe does NOT run its own LLM loop. Every skill call flows through
OpenClaw's skill runner at `POST http://127.0.0.1:18789/skills/invoke`
(§7 invariant 4, §8 invariant 2).

`await run_skill(skill_id, inputs, ctx)` does:
1. Validate inputs against `input.schema.json`.
2. Check `sensitivity_required` is satisfied (refuse privileged inputs unless
   the skill declares it).
3. Check `context_scopes_required ⊆ Session.requested_scopes`.
4. POST to OpenClaw's skill runner with `{skill_name, inputs, correlation_id,
   session_context, dmScope}`.
5. Optional `handler.py` `post_process`.
6. Validate output against `output.schema.json`.
7. Emit `skill.call.recorded` with full provenance (skill name, version,
   `openclaw_invocation_id`, inputs, outputs, provider, token counts, cost,
   duration, `correlation_id`) so every LLM-derived piece of state is
   traceable to a replayable call (§7 invariant 5).

AdministrateMe NEVER imports `anthropic` / `openai` / any provider SDK
(§7 invariant 9, §8 invariant 2). The `httpx` client is sufficient.

Skill calls are replayable: `adminme skill replay <skill_name> --since <ts>`
re-runs and emits a new `skill.call.recorded` with `causation_id` pointing to
the original (§7 invariant 8).

Do not implement in this scaffolding prompt. Prompt 09a will fill in.
"""
