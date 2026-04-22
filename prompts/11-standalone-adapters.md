# Prompt 11: Standalone adapters (Gmail, Plaid, Apple Reminders, Google Calendar, CalDAV)

**Phase:** BUILD.md L1 + dedicated Plaid + Reminders sections.
**Depends on:** Prompt 10c.
**Estimated duration:** 5-6 hours.
**Stop condition:** Each adapter: installs config via a setup script, authenticates, ingests events into the event log, handles errors as events, survives process restart.

---

## Read first

1. `ADMINISTRATEME_BUILD.md`:
   - **"L1: ADAPTERS"** — the two-runtime distinction (plugin vs. standalone).
   - **"PLAID — DETAILED SPEC"** — Plaid is its own subsection with detailed handling.
   - **"APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC"** — ditto.
2. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §1 — the BlueBubbles adapter as example (but note: BlueBubbles is a **plugin** adapter, prompt 12; the pattern is similar).
3. `docs/architecture-summary.md` §2 (integration seams) — specifically, which adapters are plugins vs. standalone.
4. **Mirrored external API docs** (in `docs/reference/`). READ FROM MIRROR ONLY — do NOT use WebFetch or rely on memory for any external API. Each adapter below names the directory to consult:
   - Gmail adapter → `docs/reference/google-gmail/` (messages.list, history API, push notifications)
   - Plaid adapter → `docs/reference/plaid/` (link token, transactions-sync, webhooks, institution status, update mode)
   - Apple Reminders adapter → `docs/reference/apple-eventkit/` (EKEventStore, EKReminder, list access)
   - Google Calendar adapter → `docs/reference/google-calendar/` (events.list, events.watch, sync guide)
   - CalDAV adapter → `docs/reference/caldav/` (RFC 4791 summary, iCloud server addresses)
   If any of these directories is empty or sparse, stop — prompt 00.5 is incomplete and needs operator clipping before this adapter can be built correctly.

## Operating context

Standalone adapters live as Python processes supervised by `adminme/daemons/adapter_supervisor.py`. Each is a pack in `packs/adapters/<family>/<n>/`. Each follows the `Adapter` protocol defined in `adminme/adapters/base.py` (stub from prompt 02). Each emits events only; never writes projections; never calls pipelines; never composes outbound messaging directly (outbound messaging is OpenClaw's job via channels, prompt 12).

## Objective

Build five standalone adapters as packs, plus the adapter supervisor.

## Out of scope

- Do NOT build OpenClaw-plugin adapters (prompt 12).
- Do NOT wire adapters to the bootstrap wizard's credential prompts (prompt 16).
- Do NOT go live against real accounts in this prompt. Use sandbox everywhere (Plaid sandbox, a test Gmail, Apple Reminders against a scratch iCloud list).

## Deliverables

### Adapter supervisor

`adminme/daemons/adapter_supervisor.py` — reads `config/channels.yaml`, spawns each configured adapter as an async task, restarts on crash with exponential backoff, exposes status endpoint.

### Adapters

- **`packs/adapters/messaging-gmail-api/`** — OAuth2 flow (service account), Gmail API polling + PubSub webhook for low-latency inbound, emits `messaging.received`. Sensitivity floor per member's config (normal / privileged). Cursor on Gmail history ID.
- **`packs/adapters/financial-plaid/`** — per PLAID DETAILED SPEC: Link flow, institution management, cursor-based /transactions/sync, webhook handler (Funnel endpoint), institution health tracking, emit `money_flow.plaid_synced` events.
- **`packs/adapters/reminders-apple-eventkit/`** — per APPLE REMINDERS DETAILED SPEC: bidirectional sync with iCloud Reminders via EventKit, outbound loop every 30s, inbound polling with dedup by remind_id, respects never-sync rules from config.
- **`packs/adapters/calendaring-google-api/`** — Google Calendar API; OAuth; webhook for push notifications; emit `calendar.event_*` events.
- **`packs/adapters/calendaring-caldav/`** — generic CalDAV (iCloud, Fastmail, Nextcloud); polling since server supports incremental sync poorly; emit same events as google adapter for consistent consumption.

Each adapter pack has:
- `pack.yaml` — manifest, required config schema, capabilities, sensitivity floor.
- `adapter.py` — implements Adapter protocol.
- `tests/` — at minimum: auth stub test, one ingest test with recorded response, one error-emission test.

### Tests

Per adapter:
- Auth flow (mocked): config in → token obtained → stored via 1Password reference.
- Ingest (recorded fixture): fake inbound → adapter normalizes → event emitted with correct shape.
- Error path: transient failure → `adapter.error` event, retry with backoff.
- Cursor persistence: adapter stops, restarts, resumes from last cursor.

Integration test `tests/integration/test_gmail_adapter_end_to_end.py` (skipped in CI; run manually against a test Gmail during lab work).

### Reminders bidirectional specifically

Inbound: iCloud → adapter → events. Outbound: AdministrateMe `task.created` with `destination: apple_reminders` → adapter writes to iCloud. Both directions must respect the never-sync rules (list name substrings like "Work", "Case", "Client"; item tags like `#private`; items owned by child/ambient). See BUILD.md detailed spec.

### Schemas

Add event schemas: `adapter.error` v1, `adapter.authenticated` v1, `adapter.cursor_advanced` v1, `money_flow.plaid_synced` v1, `calendar.event_*` already exist, `reminder.list_item.synced` v1.

## Verification

```bash
poetry run pytest packs/adapters/*/tests/ tests/integration/test_*adapter* -v
# Previous tests still pass
poetry run pytest -v

# Start supervisor against a fixture config, observe events in log
poetry run python scripts/demo_adapter_supervisor.py

git commit -m "phase 11: standalone adapters"
```

## Stop

> Five standalone adapters + supervisor in. Data ingest from Gmail, Google Calendar, CalDAV, Plaid, Apple Reminders works. Ready for prompt 12 (OpenClaw plugin adapters).

