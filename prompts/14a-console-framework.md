# Prompt 14a: Node console framework

**Phase:** BUILD.md "L5: THE NODE CONSOLE SHELL". Implements CONSOLE_PATTERNS.md §1-§4, §10, §12.
**Depends on:** Prompt 13b.
**Estimated duration:** 3-4 hours.
**Stop condition:** Express server at :3330 with Tailscale identity, Session, guardedWrite, RateLimiter, HTTP bridge to Python APIs; routes authenticate; writes go through three-layer check.

---

## Read first

1. `ADMINISTRATEME_BUILD.md` **"L5: THE NODE CONSOLE SHELL"** section.
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md` §1 (Tailscale identity resolution), §2 (Session), §3 (guardedWrite three-layer), §4 (RateLimiter), §10 (HTTP bridge), §12 (error handling).
3. `docs/reference/tailscale/` — specifically Serve (HTTPS termination) and identity-header documentation. Mirror only; do NOT WebFetch tailscale.com/kb live.
4. `ADMINISTRATEME_DIAGRAMS.md` §3, §4.

## Operating context

The Node console at :3330 is the only thing the tailnet reaches. Python APIs are loopback only. The console is the identity and authorization enforcement layer. Its job:
1. Terminate tailnet HTTPS via Tailscale Serve.
2. Read `X-Tailscale-User-Login` header → resolve to authMember.
3. Build Session (authMember + viewMember + dm scope etc.).
4. For writes: run guardedWrite three-layer check.
5. For reads: proxy to the appropriate Python product API over HTTP bridge.
6. Render views (prompts 14b/c implement individual views).

This prompt builds the framework. No views yet.

## Objective

Express server + all foundational middleware/patterns from CONSOLE_PATTERNS.md §1-§4, §10, §12. Integration tests against a mock Python API.

## Out of scope

- Views themselves (14b/c).
- Reward toast + observation banner + degraded mode UX (14d).
- SSE chat proxy (14c).

## Deliverables

### `console/server.js`

Express app. Listens on 127.0.0.1:3330 (Tailscale Serve terminates TLS externally). Middleware chain:

1. Request logger (structured, includes correlation_id).
2. Tailscale identity resolution (from header → `req.authMember`).
3. Session builder (`req.authMember` + optional `?view_as=` + OpenClaw dm scope if applicable → `req.session`).
4. Router dispatch.
5. Error handler.

### `console/lib/session.js`

Implements CONSOLE_PATTERNS.md §1 + §2. Exported helpers: `resolveAuthMember(req, config)`, `buildSession(req, config)`, `assertPrincipal(session)`.

### `console/lib/guardedWrite.js`

Implements §3. Three-layer: allowlist → governance → rate limit. Each failure emits a distinct event (`write.denied` with `layer` field) and returns the appropriate status code + payload.

### `console/lib/rateLimiter.js`

Implements §4. Sliding-window, in-memory; per (tenant, scope, action) key.

### `console/lib/bridge.js`

Implements §10. HTTP client to Python APIs. Routes:
- `/core/...` → `http://127.0.0.1:3333/api/core/...`
- `/comms/...` → `http://127.0.0.1:3334/api/comms/...`
- `/capture/...` → `http://127.0.0.1:3335/api/capture/...`
- `/automation/...` → `http://127.0.0.1:3336/api/automation/...`

Error mapping: Python BridgeError shape → HTTP status for the tailnet caller.

### `console/lib/errors.js`

Implements §12. Correlation-ID-stamped error responses. 4xx for user errors; 5xx only for actual bugs.

### Routes (empty shells)

`console/routes/today.js`, `inbox.js`, `crm.js`, `capture.js`, `finance.js`, `calendar.js`, `scoreboard.js`, `settings.js`, `chat.js` — each imports the middleware, sets up the router, exports. All return 501 Not Implemented for now. Prompt 14b/c fills them in.

### Tests

`console/tests/lib/*.test.js` for each lib.

`console/tests/integration/test_auth_flow.test.js`:
- Unauthenticated request → 401.
- Valid Tailscale header → session built correctly.
- Principal view-as valid target → viewMember differs from authMember.
- Child session view-as → 403.
- Ambient or off-household view-as target → 403.

`console/tests/integration/test_guarded_write.test.js`:
- All three layers: allowlist deny, governance deny (review / hard_refuse), rate limit exhaustion. Each produces correct status + event.

## Verification

```bash
cd console
npm test
# Integration against a mock Python API:
npm run test:integration
cd ..

# Start end-to-end
poetry run uvicorn adminme.products.core.main:app --port 3333 &
node console/server.js &
sleep 2
curl -H "X-Tailscale-User-Login: test@test.ts.net" http://127.0.0.1:3330/api/core/tasks
kill %1 %2

git add console/
git commit -m "phase 14a: node console framework"
```

## Stop

> Console framework in. Auth works; writes gate through three layers; bridge proxies to Python. Ready for prompt 14b (primary views).

