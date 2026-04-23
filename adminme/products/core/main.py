"""
Core product — Chief of Staff FastAPI service at loopback :3333.

Implemented in prompt 13a per ADMINISTRATEME_BUILD.md §L5-continued and
architecture-summary.md §9.

Owns tasks, commitments, recurrences, scoreboard, what-now, rewards,
paralysis, digests, custody brief, calendar playbook, emergency protocols.

Routers: /api/core/{tasks, commitments, recurrences, whatnow, digest,
scoreboard, energy, today-stream, observation-mode, emergency}.

Slash commands (handlers live here, dispatched by OpenClaw):
/whatnow, /digest, /bill, /remind, /done, /skip, /standing, /observation.

Proactive jobs register as OpenClaw standing orders at boot — morning_digest,
paralysis_detection (15:00 + 17:00 per ADHD member), reminder_dispatch
(every 15 min), weekly_review (Sun 16:00), velocity_celebration,
overdue_nudge, custody_brief (20:00), scoreboard_rollover (midnight).
APScheduler is used ONLY for internal schedules (SYSTEM_INVARIANTS.md §14).

Binds to LOOPBACK only (§9 invariant 1) — only the Node console at :3330 is
tailnet-facing.

Do not implement in this scaffolding prompt. Prompt 13a will fill in.
"""
