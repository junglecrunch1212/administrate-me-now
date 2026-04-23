# OpenClaw plugins

OpenClaw plugins written in Python; installed via `openclaw plugin install <path>`.
Plugins live on the OpenClaw side of the seam — they run inside the OpenClaw
gateway process and bridge OpenClaw's concepts into AdministrateMe's event
log via the HTTP seam described in DECISIONS.md §D6.

Two plugins in scope for v1:

- `memory_bridge/` — emits `messaging.received` and `conversation.turn.recorded`
  events into AdministrateMe via POST http://127.0.0.1:3334/api/comms/ingest/
  conversation-turn. One-way (OpenClaw → AdministrateMe) per
  SYSTEM_INVARIANTS.md §8 invariant 4.
- `channel_bridge_bluebubbles/` — OpenClaw's channel plugin for iMessage via
  BlueBubbles. Outbound sends go through OpenClaw, not through an
  AdministrateMe-owned transport (SYSTEM_INVARIANTS.md §8 invariant 5).

Filled in by prompt 12.
