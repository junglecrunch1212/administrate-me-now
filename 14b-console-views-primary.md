# Prompt 14b: Primary console views (Today, Inbox, CRM, Capture, Finance)

**Phase:** BUILD.md L5 — views. Implements CONSOLE_PATTERNS.md §5 partial, §6, §7, §8.
**Depends on:** Prompt 14a.
**Estimated duration:** 5-6 hours.
**Stop condition:** Five views render with real projection data; compiled JSX profile views load per member; calendar privacy filtering works.

## Read first

1. `ADMINISTRATEME_CONSOLE_REFERENCE.html` — open in browser, click through Today, Inbox, CRM, Capture, Finance. These are your design reference; match tone.
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md` §6 (privacy filter for privileged), §7 (HIDDEN_FOR_CHILD nav).
3. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §6 (profile pack with compiled JSX views).

## Objective

Implement routes + compiled-JSX-loading for five views. Hand off data-loading to Python product APIs; the Node console is a rendering layer.

## Out of scope

- Calendar view and Scoreboard — prompt 14c.
- Reward toast UX — prompt 14d.

## Deliverables

### Profile JSX compilation

`console/scripts/compile-profiles.js` — esbuild-based. Takes `~/.adminme/packs/profiles/<id>/views/*.jsx` and produces `~/.adminme/packs/profiles/<id>/compiled/*.ssr.js` + `*.client.js` + CSS.

### Routes

- `console/routes/today.js` — loads compiled today.ssr.js for viewMember's profile; renders with data from `/core/today-stream`.
- `console/routes/inbox.js` — renders unified inbox; approval action POSTs back through guardedWrite to `/comms/inbox/*/approve`.
- `console/routes/crm.js` — Party detail page; lists of parties; search.
- `console/routes/capture.js` — quick-capture form + recent captures.
- `console/routes/finance.js` — dashboard widget (data from `/automation/...` and `/core/...`).

### Privacy filter application

Every data-loading path that might include privileged content runs results through the privacy-filter middleware (§6) BEFORE sending to the client. Privileged events that aren't in the viewer's scope → redacted to busy-block placeholders for calendar; hidden entirely for inbox/CRM depending on sensitivity.

### HIDDEN_FOR_CHILD nav

Middleware (§7): for child sessions, route dispatch only allows today + scoreboard; other routes return 403. Also suppresses nav links in rendered views.

### Tests

Per route, integration tests with mocked Python APIs.

## Verification

```bash
cd console && npm test
cd ..

# Live check
poetry run uvicorn platform.products.core.main:app --port 3333 &
poetry run uvicorn platform.products.comms.main:app --port 3334 &
node console/server.js &
sleep 3
open http://localhost:3330/today
open http://localhost:3330/inbox
# etc.
kill %1 %2 %3

git commit -m "phase 14b: primary console views"
```

## Stop

> Today, Inbox, CRM, Capture, Finance all render. Privacy filters apply. Child sessions see only today + scoreboard. Ready for 14c.

