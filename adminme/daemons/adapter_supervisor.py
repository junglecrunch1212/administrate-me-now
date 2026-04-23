"""
Adapter supervisor — lifecycle manager for L1 adapters.

Implemented in prompt 11 per ADMINISTRATEME_BUILD.md §L1 and SYSTEM_INVARIANTS.md §7.

Starts, restarts, and monitors adapter processes (Gmail, Google Calendar,
Plaid, Apple Reminders, CalDAV, iOS Shortcuts webhooks, etc.). Each adapter
runs as a standalone Python process; messaging-family adapters are OpenClaw
plugins, not supervised here (§8 invariant 3).

APScheduler drives adapter poll cadences at the adapter level — these are
internal, non-user-facing schedules and fall within the allowed APScheduler
use per SYSTEM_INVARIANTS.md §14 invariant 3.

Do not implement in this scaffolding prompt. Prompt 11 will fill in.
"""
