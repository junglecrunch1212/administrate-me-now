# Prompt 13b: Python product APIs — Capture (:3335) + Automation (:3336)

**Phase:** BUILD.md L5 — Products C and D.
**Depends on:** Prompt 13a.
**Estimated duration:** 3-4 hours.
**Stop condition:** Capture and Automation services running; Plaid webhooks route to Automation; capture endpoint accepts Shortcuts webhooks.

## Read first

1. `ADMINISTRATEME_BUILD.md` Product C (capture) and Product D (automation) sections.
2. Plaid webhook notes from BUILD.md PLAID DETAILED SPEC (the Funnel public path).
3. `docs/reference/plaid/` — specifically webhook events reference and signature verification. Mirror only; no WebFetch.
4. `docs/reference/tailscale/` — Funnel setup for the Plaid webhook endpoint. Mirror only.
5. `docs/reference/apple-shortcuts/` — webhook integration for the Shortcuts callback endpoint. Mirror only.

## Objective

Capture and Automation. Same shape as 13a.

## Deliverables

### Product C: capture (:3335)

Routers:
- `/api/capture/quick` — accepts text, infers intent, routes
- `/api/capture/voice` — accepts audio
- `/api/capture/crm` — CRM surface read/write
- `/api/capture/artifacts` — upload/search
- `/api/capture/relationships`

Slash commands: `/capture`, `/crm`, `/commit`, `/review`.

### Product D: automation (:3336)

Routers:
- `/api/automation/plaid/webhook` — Plaid webhook handler (signature verified)
- `/api/automation/sensors` — home assistant, other ambient feeds
- `/api/automation/webhooks/shortcuts` — iOS Shortcuts callback
- `/api/automation/standing-orders` — query/administer OpenClaw standing orders via proxy

Slash commands: `/scoreboard`.

Internal scheduled: Plaid cursor advancement polling (as fallback to webhook).

### Plaid webhook specifically

The webhook endpoint receives Plaid events via Tailscale Funnel → internal :3337 router → automation :3336. Validates signature. Translates to `plaid.webhook.received` event. Pipeline later acts on it.

### Tests

Integration tests per route. Fixture Plaid webhook signature verification. Capture endpoint accepts text and returns intent classification.

## Verification

```bash
poetry run pytest tests/integration/test_capture_api.py tests/integration/test_automation_api.py -v
# All previous tests still pass
poetry run pytest -v

poetry run uvicorn platform.products.capture.main:app --port 3335 &
poetry run uvicorn platform.products.automation.main:app --port 3336 &
sleep 2
curl http://127.0.0.1:3335/health
curl http://127.0.0.1:3336/health
kill %1 %2

git commit -m "phase 13b: capture + automation product APIs"
```

## Stop

> All four Python product APIs live. Ready for prompt 14a (Node console framework).
