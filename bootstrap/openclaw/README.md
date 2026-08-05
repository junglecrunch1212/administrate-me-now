# OpenClaw standing-order sources

AdministrateMe owns the domain programs in `programs/*.md`; OpenClaw owns their
clock, execution, channel delivery, and generic assistant runtime. Five
proactive programs are declared in `cron.yaml`. `reward_dispatch` is reactive
and therefore has documentation here but no cron entry.

Bootstrap compiles the program files into the per-instance OpenClaw workspace,
and activation selects that workspace. `bootstrap/openclaw_cron.py` reconciles
the five `administrateme-*` jobs from both `cron.yaml` and
`openclaw/administrateme/automations.json`, refusing declaration drift and
duplicates. It uses the explicit household IANA timezone, isolated sessions,
and `no-deliver`; it leaves foreign jobs untouched and rolls back a partial
change. Bootstrap, activation, and a safe service start all run the same
idempotent reconciler. Do not create or rename these jobs manually.

The in-process `PipelineRunner` loads only packs with `triggers.events`.
AdministrateMe contains no product cron executor, APScheduler dependency, model
router, channel gateway, or generic workflow runtime.

`openclaw/administrateme/scripts/compile-workspace.mjs` is the deterministic
compiler. It also compiles the activated data-only persona into `SOUL.md`; a
persona cannot change authority or model routing.
