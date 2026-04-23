"""
Pipeline supervisor — lifecycle manager for the in-process PipelineRunner.

Implemented in prompt 10a per ADMINISTRATEME_BUILD.md §L4 and SYSTEM_INVARIANTS.md §7.

Supervises `adminme.pipelines.runner:PipelineRunner`. Reactive pipelines run
here; proactive pipelines register as OpenClaw standing orders elsewhere
(SYSTEM_INVARIANTS.md §14, DECISIONS.md §D1) and do NOT run through this
supervisor.

A pipeline failure on one event does not halt the bus; the runner records,
retries per policy, and continues (§7 invariant 7).

Do not implement in this scaffolding prompt. Prompt 10a will fill in.
"""
