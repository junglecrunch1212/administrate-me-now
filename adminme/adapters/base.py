"""
Adapter Protocol — the L1 contract every adapter satisfies.

Implemented in prompt 11 per ADMINISTRATEME_BUILD.md §L1.

Adapters translate external sources (Gmail, Google Calendar, Plaid, Apple
Reminders, CalDAV, etc.) into typed events on the event log. Messaging-family
adapters (iMessage via BlueBubbles, Telegram, Discord) live as OpenClaw
plugins, not as standalone Python adapters, because OpenClaw owns channel
transport (SYSTEM_INVARIANTS.md §8 invariant 3).

Key rules:
- Adapters never write projections, never call pipelines, and never compose
  outbound messages — those paths are owned by OpenClaw's channel plugins or
  by adapter-specific write surfaces (architecture-summary.md §1 "L1").
- Adapters set `correlation_id` on entry if the inbound request does not
  carry one (DECISIONS.md §D8 addition 1).
- Privileged adapters (e.g. law-practice email) have a hardcoded sensitivity
  floor at the adapter level; the config loader rejects any configuration
  that would lower it (§6 invariant 10).

Do not implement in this scaffolding prompt. Prompt 11 will fill in.
"""
