# Checkpoint 14e: Console ↔ Python API contract audit

**Phase:** Phase A checkpoint. After prompt 14d, before prompt 15.
**Depends on:** Console (14a-d) and Python product APIs (13a, 13b) all exist.
**Estimated duration:** 45-60 minutes.
**Stop condition:** `docs/checkpoint-14e-report.md` produced; every endpoint the console calls actually exists on a Python API with the expected shape.

---

## Read first (required)

1. `docs/SYSTEM_INVARIANTS.md` — Section 6 (security), Section 9 (console).
2. Every file in `console/routes/`.
3. Every file in `adminme/products/*/routers/`.
4. `ADMINISTRATEME_CONSOLE_PATTERNS.md` §10 (HTTP bridge).

## Operating context

The console is a rendering + authorization layer that reaches Python APIs over a loopback HTTP bridge. The console's routes declare endpoints they call; the Python APIs declare endpoints they serve. If these two sets don't match, the console will 404 or return the wrong data at runtime — and because Python APIs are loopback-only, this failure mode is invisible in normal testing until someone clicks the affected view.

This checkpoint catches that mismatch before prompt 15 integration work starts.

## Objective

Produce `docs/checkpoint-14e-report.md`. For every endpoint the console calls, verify:
- The endpoint exists on the named Python API.
- The HTTP method matches.
- The request payload shape matches (console's send vs. Python's Pydantic request model).
- The response shape matches (Python's Pydantic response vs. console's expected fields).
- The auth + session semantics agree (both sides understand the shared-secret header, correlation-ID propagation).
- Observation mode is respected on the API side for write endpoints.

## Out of scope

- Do NOT test any endpoints live. This is a static analysis + schema-matching check.
- Do NOT fix mismatches in this prompt; record them for a follow-up.

## Deliverables

### The checks

For each console route in `console/routes/*.js`:
1. Enumerate every HTTP call the route makes to a Python API (via the `bridge.js` helper). Produce a list of (method, path, payload shape, response shape).
2. For each such call, find the Python API that should serve it. Grep `adminme/products/*/routers/` for the matching path. Confirm:
   - Method matches.
   - Path matches exactly.
   - Python's request model accepts what the console sends.
   - Python's response model provides what the console consumes.

For each Python API endpoint:
3. Is any endpoint on a Python API that no console route calls? Flag as "orphan endpoint" — might be called from a CLI subcommand or by OpenClaw (slash command handler), in which case fine; or might be dead code.

Cross-cutting:
4. **Correlation ID propagation.** Every console → Python call forwards `correlation_id` in a header. Every Python → emitted event includes `correlation_id` in the envelope. Flag any Python endpoints that don't preserve correlation.
5. **Shared secret.** Every Python endpoint that serves the console validates the shared-secret header. Flag any that don't.
6. **Observation mode on writes.** Every Python write endpoint (POST, PATCH, PUT, DELETE) calls `outbound()` or `guardedWrite` appropriately per the write semantics. Flag any that directly emit external.* or mutate projections.

### The report

```markdown
# Checkpoint 14e report — Console ↔ Python API contracts

Generated: <date>

## Console → Python API call map

| Console route | HTTP method | Path | Target Python API | Endpoint exists? | Payload match? | Response match? |
|---------------|-------------|------|-------------------|------------------|----------------|-----------------|
| /today (GET) | GET | /api/core/today-stream | core (:3333) | ✓ | ✓ | ✓ |
| /inbox/approve (POST) | POST | /api/comms/inbox/approve | comms (:3334) | ✓ | ✓ | ✓ |
| ... |

## Orphan endpoints (served by Python, not called by console)

These are probably slash command handlers or CLI targets — expected.

- POST /api/core/standing-orders/trigger → slash command `/standing`
- ...

Or these are dead code — remove:

- GET /api/core/deprecated/foo → no caller found

## Cross-cutting

### Correlation ID propagation
- Console forwards X-Correlation-ID: ✓ on N/M endpoints
- Python preserves X-Correlation-ID in event envelopes: ✓ on N/M

### Shared-secret validation
- Every Python endpoint validates: ✓ / ✗ (list offenders)

### Observation-mode enforcement on writes
- Every write endpoint calls outbound() or guardedWrite(): ✓ / ✗

## Critical issues

(list or "none")

## Deferred findings

(list)
```

## Verification

```bash
poetry run python scripts/checkpoint_14e_audit.py > docs/checkpoint-14e-report.md
cat docs/checkpoint-14e-report.md

# Quick runtime sanity: start services, exercise one call per console route
poetry run uvicorn adminme.products.core.main:app --port 3333 &
poetry run uvicorn adminme.products.comms.main:app --port 3334 &
poetry run uvicorn adminme.products.capture.main:app --port 3335 &
poetry run uvicorn adminme.products.automation.main:app --port 3336 &
node console/server.js &
sleep 3

# Hit each console route once; verify 200 (or intentional 403 for auth)
# Use a test Tailscale header
for path in /today /inbox /crm /capture /finance /calendar /scoreboard /settings; do
  curl -sI -H "X-Tailscale-User-Login: test@test.ts.net" "http://127.0.0.1:3330$path" | head -1
done

kill %1 %2 %3 %4 %5

git add docs/checkpoint-14e-report.md scripts/checkpoint_14e_audit.py
git commit -m "checkpoint 14e: console <-> Python API contract audit"
git push
```

## Stop

**Explicit stop message:**

> Console/API contract checkpoint complete. Report at `docs/checkpoint-14e-report.md`.
>
> Critical issues (if any) must be fixed before prompt 15 (OpenClaw integration). Orphan endpoints are usually fine (slash commands / CLI) but worth reviewing.
