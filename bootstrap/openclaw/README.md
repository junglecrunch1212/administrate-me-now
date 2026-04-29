# bootstrap/openclaw/

Standing-order source artifacts for AdministrateMe's proactive
pipelines. Per [D1] Corollary, OpenClaw's standing-orders system is
**workspace prose paired with cron jobs**, not a programmatic
registration API ([cheatsheet Q3]).

## Layout

```
bootstrap/openclaw/
├── README.md                          # this file
├── cron.yaml                          # cron sidecar (5 scheduled entries)
└── programs/
    ├── reward_dispatch.md             # full (reactive — for documentation)
    ├── morning_digest.md              # stub — full in 10c-ii
    ├── paralysis_detection.md         # stub — full in 10c-ii
    ├── reminder_dispatch.md           # stub — full in 10c-iii
    ├── crm_surface.md                 # stub — full in 10c-iii
    └── custody_brief.md               # stub — full in 10c-iii
```

## How bootstrap §8 (prompt 16) consumes these artifacts

Per [D1] Corollary, bootstrap §8 will:

1. **Concatenate** every `programs/*.md` file into the OpenClaw
   workspace's `~/Chief/AGENTS.md`. Each program block is a
   self-contained standing-order definition (Scope / Triggers /
   Approval gate / Escalation / Execution steps / What-NOT-to-do).
   The concatenated `AGENTS.md` is a generated artifact — bootstrap §8
   regenerates it; no one hand-edits it.

2. **Read** `cron.yaml` and run, per entry:

   ```
   openclaw cron add --cron "<cron>" --message "<message>"
   ```

   Cron schedules in this YAML are placeholders. Bootstrap §8 may
   substitute per-member configured times at install time — for
   example, `morning_digest`'s `0 7 * * *` becomes the actual wake
   time the principal entered during bootstrap §3, and
   `paralysis_detection`'s `0 15,17 * * *` becomes the configured
   fog-window times for the ADHD-profile member. Per-member-cron
   substitution is bootstrap §8's responsibility, not this YAML's.

## Reactive vs. proactive — why `reward_dispatch` is not in cron.yaml

Per [§7.1-7.2] and [BUILD.md §L4 lines 1107-1116], pipelines split into
two trigger mechanisms:

- **Reactive** pipelines have `triggers.events` declared in their
  `pipeline.yaml` and run inside the AdministrateMe `PipelineRunner`.
  OpenClaw cron does NOT invoke them.
- **Proactive** pipelines run on a clock and register as OpenClaw
  standing orders so they share OpenClaw's approval, observation-mode,
  and rate-limit machinery.

`reward_dispatch` is **reactive** — it subscribes to `task.completed`
and `commitment.completed`. Its program file
(`programs/reward_dispatch.md`) ships in this directory for
documentation continuity (so a single `AGENTS.md` block describes all
eight v1 proactive behaviors per [§14]), but `reward_dispatch` is NOT
listed in `cron.yaml` because OpenClaw cron does not schedule it.

The other five files in `programs/` (`morning_digest`,
`paralysis_detection`, `reminder_dispatch`, `crm_surface`,
`custody_brief`) are genuinely proactive and DO have entries in
`cron.yaml`. Their full execution steps are TODOs filled in by
prompts 10c-ii (morning_digest, paralysis_detection) and 10c-iii
(reminder_dispatch, crm_surface, custody_brief).

## Citations

- [D1] — Standing orders registration path (CONFIRMED 2026-04-23).
- [§14] — Proactive-behavior scheduling boundary
  (`docs/SYSTEM_INVARIANTS.md` Section 14).
- [cheatsheet Q3] — Standing-orders registration format
  (`docs/openclaw-cheatsheet.md` Q3).
- [BUILD.md §L4 lines 1107-1116] — reactive vs. scheduled pipeline split.
