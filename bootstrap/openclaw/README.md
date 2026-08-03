# OpenClaw standing-order sources

AdministrateMe owns the domain programs in `programs/*.md`; OpenClaw owns their
clock, execution, channel delivery, and generic assistant runtime. Five
proactive programs are declared in `cron.yaml`. `reward_dispatch` is reactive
and therefore has documentation here but no cron entry.

Bootstrap Section 8 compiles the program files into the pinned plugin
workspace and reconciles `cron.yaml` into OpenClaw automations. Morning-digest
and paralysis-detection schedules are data defaults: bootstrap substitutes the
activated member profile values before installation. The other three schedules
are exact built-in defaults unless the instance configuration overrides them.

The in-process `PipelineRunner` loads only packs with `triggers.events`.
AdministrateMe contains no product cron executor, APScheduler dependency, model
router, channel gateway, or generic workflow runtime.

`openclaw/administrateme/scripts/compile-workspace.mjs` is the deterministic
compiler. It also compiles the activated data-only persona into `SOUL.md`; a
persona cannot change authority or model routing.
