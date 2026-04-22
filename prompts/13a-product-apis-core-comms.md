# Prompt 13a: Python product APIs — Core (:3333) + Comms (:3334)

**Phase:** BUILD.md "L5 CONTINUED: PYTHON PRODUCT APIS" — Products A and B.
**Depends on:** Prompt 12. Adapters ingest; projections update; pipelines react.
**Estimated duration:** 4-5 hours.
**Stop condition:** Core and Comms FastAPI services start, serve all documented endpoints, pass auth checks, register their slash commands with OpenClaw.

---

## Read first

1. `ADMINISTRATEME_BUILD.md` **"L5 CONTINUED: PYTHON PRODUCT APIS"** — Product A (core) and Product B (comms) sections in full.
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md` §10 (HTTP bridge to Python APIs) — the console's expectations of these services.
3. `docs/openclaw-cheatsheet.md` question 2 (slash command registration).
4. `docs/reference/openclaw/` — any files covering slash command handlers, invocation shape, and approval-gates. Mirror only.

## Operating context

The four Python product APIs are FastAPI services on loopback ports 3333-3336. Each hosts:
- HTTP endpoints for the Node console.
- Slash-command handlers registered with OpenClaw.
- Internal schedules via APScheduler (only for non-user-facing schedules; user-facing proactive behaviors live in pipelines as standing orders).

Each service shares state with the other services via the event log + projections only. No direct RPC between services.

## Objective

Build Core (:3333) and Comms (:3334). Each has a full FastAPI app with all documented routers, uses `Session` from prompt 08 for authorization, registers its slash commands with OpenClaw.

## Out of scope

- Capture and Automation APIs — prompt 13b.
- Node console — prompt 14a/b/c/d.

## Deliverables

### Product A: core (:3333)

`adminme/products/core/main.py` — FastAPI app, CORS for loopback only, authenticates via a simple shared-secret header set by the Node console (which in turn authenticated the caller via Tailscale).

Routers (per BUILD.md Product A section):
- `/api/core/tasks`
- `/api/core/commitments`
- `/api/core/recurrences`
- `/api/core/whatnow`
- `/api/core/digest`
- `/api/core/scoreboard`
- `/api/core/energy`
- `/api/core/today-stream`
- `/api/core/observation-mode`
- `/api/core/emergency`

Each route:
- Validates request against Pydantic model.
- Builds a Session from the shared-secret + request headers.
- For reads: calls projection query functions with session.
- For writes: calls `guardedWrite.check()` first; on pass, emits event; returns 200.

Slash-command handlers (register with OpenClaw at boot): `/whatnow`, `/digest`, `/bill`, `/remind`, `/done`, `/skip`, `/standing`, `/observation`.

Internal scheduled tasks (APScheduler): `scoreboard_rollover` at midnight; log-rotation; projection-compaction.

### Product B: comms (:3334)

`adminme/products/comms/main.py`.

Routers:
- `/api/comms/inbox` — the unified inbox (read + approve/dismiss)
- `/api/comms/threads/{thread_id}`
- `/api/comms/drafts`
- `/api/comms/outbound`
- `/api/comms/search`

Slash commands: `/comms`, `/approve`, `/reply`.

Internal scheduled tasks: inbox compaction, dead-letter review.

### Shared boot code

`adminme/products/_shared/boot.py` — loads config, opens event log, projections, bus, skill runner, pipeline runner; starts them; sets up health endpoint, graceful shutdown.

Each product's main.py imports from boot and adds its own routers.

### Tests

- `tests/integration/test_core_api.py` — spin up core in a test fixture, hit each endpoint with a test client, assert status codes and shapes.
- `tests/integration/test_comms_api.py` — same for comms.
- `tests/integration/test_slash_command_registration.py` — mocks OpenClaw registration API, asserts each product registers its verbs on boot.

## Verification

```bash
poetry run pytest tests/integration/test_core_api.py tests/integration/test_comms_api.py tests/integration/test_slash_command_registration.py -v

# Manual: start both services in background, curl health
poetry run uvicorn adminme.products.core.main:app --port 3333 &
poetry run uvicorn adminme.products.comms.main:app --port 3334 &
sleep 2
curl http://127.0.0.1:3333/health
curl http://127.0.0.1:3334/health
kill %1 %2

git commit -m "phase 13a: core + comms product APIs"
```

## Stop

> Core + Comms live on loopback. Slash commands registered with OpenClaw. Ready for 13b (capture + automation).

