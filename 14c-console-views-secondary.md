# Prompt 14c: Secondary console views (Calendar, Scoreboard, Settings) + SSE chat

**Phase:** BUILD.md L5 — remaining views + chat proxy.
**Depends on:** Prompt 14b.
**Estimated duration:** 4-5 hours.
**Stop condition:** Calendar with privacy filter, Scoreboard with kid view mode, Settings with 8 panes, SSE chat streams from OpenClaw.

## Read first

1. `ADMINISTRATEME_CONSOLE_PATTERNS.md` §5 (SSE chat — OpenClaw upstream at :18789/agent/chat/stream), §6 (privacy filter for calendar specifically).
2. `docs/reference/openclaw/` — any files covering the `/agent/chat/stream` SSE endpoint, event shapes, and streaming protocol details. Mirror only.
3. `ADMINISTRATEME_CONSOLE_REFERENCE.html` — Calendar, Scoreboard, Settings tabs.

## Objective

Three more views + SSE chat proxy.

## Deliverables

- **Calendar route** (`console/routes/calendar.js`): renders member's calendar; privileged events from other members shown as `[busy]` blocks (time + duration only; no title, description, location). Implements §6 privacy filter rules.
- **Scoreboard route** (`console/routes/scoreboard.js`): two view modes — wall display (kiosk, no auth required from kitchen iPad) and child view (star accumulation, streak, weekly goal). Data from `/core/scoreboard`.
- **Settings route** (`console/routes/settings.js`): eight panes per CONSOLE_REFERENCE.html:
  1. Household members
  2. Your profile (edit tuning knobs)
  3. Channels (pair / unpair)
  4. Plaid (institutions, link flow)
  5. Reminders (list mapping)
  6. Packs (install/uninstall/list)
  7. Observation (toggle + suppressed log review)
  8. LLM usage (cost ledger from skill.call.recorded events)
- **SSE chat** (`console/routes/chat.js`): per CONSOLE_PATTERNS.md §5. Upstream is OpenClaw `http://127.0.0.1:18789/agent/chat/stream`. Pass-through SSE stream; insert AdministrateMe's correlation ID; apply rate limit before opening upstream connection.

## Tests

Per route; plus SSE-specific test (mock OpenClaw returns chunks, assert client receives them in order with correlation header).

## Verification

```bash
cd console && npm test
# Live chat check
# (start all services, open /chat, type a message, observe streaming)
git commit -m "phase 14c: secondary views + SSE chat"
```

## Stop

> All eight console tabs functional. SSE chat proxies to OpenClaw. Ready for 14d.

