# Prompt 11d: Google Calendar + CalDAV adapters

**Phase:** Phase A — L1 ingest adapters, fourth and final sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`.

**Branch:** `claude/11d-<auto>` (harness-assigned). PR base = `main` for all four c-bands (per PR #106 sidecar). STACKED across c1→c4 per §8.5.

**Depends on:** **11a c1-c4 merged to main** (Adapter Protocol + supervisor + 3 cross-cutting schemas + Gmail pack at `packs/adapters/messaging/gmail/`). **11b c1-c4** and **11c c1-c4** are not hard deps but provide additional clone-template references (Plaid for webhook handler shape; Reminders for sibling-adapter inbound discipline). All three confirmed on main at authorship time (2026-05-16).

**Quality bar:** clones the Gmail OAuth pack shape for the Google Calendar adapter; clones Plaid's webhook-handler pattern for Google push notifications; ships CalDAV as the simplest adapter in the cohort (HTTP basic-auth + polling-only, no webhook). Both pack manifests share a calendar-family config-schema fragment so future calendar adapters (Fastmail, Nextcloud) clone consistently.

**Stop condition:** Two adapter packs (`packs/adapters/calendaring/google/` and `packs/adapters/calendaring/caldav/`) implement the `Adapter` Protocol; Google authenticates via OAuth2 service-account (Gmail clone) AND receives push notifications via a Funnel webhook; CalDAV authenticates via HTTP basic-auth (1Password reference) AND polls on a configurable interval; both emit the existing `calendar.event_added` / `calendar.event_updated` / `calendar.event_deleted` v1 schemas (NO new event types). `config/channels.yaml` extended with both calendar entries. ≥18 new tests across the four commits. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~70-85 minutes wall clock, 4 commits. ~$60-100 API spend per split memo §2 (lightest in the 11 cohort because no new event types and no bidirectional complexity).

---

## §1 Behavioral grounding

You are building the **fourth and fifth L1 standalone adapters** — both in the same prompt because the split memo §2 designates them as clone-shape siblings: after 11a/11b/11c establish the patterns, calendar adapters are structurally simpler. Both Google Calendar and CalDAV are **inbound-only** (no event-log → external write surface like 11c had for Reminders); both emit the **existing** `calendar.event_*` schemas registered at v1 in `adminme/events/schemas/ingest.py` (from prompt 04 c1); neither carries a privileged-floor lift like 11b did.

**Two adapters, shared shape, different transport** [BUILD.md L1 §"Calendar adapters", split memo §2 (11d row)]. The substantive differences:

- **Google Calendar**: OAuth2 service-account auth (clone the Gmail pattern from 11a c2 verbatim — same `op://` ref shape, same `_default_credentials_resolver` factory); push notifications via Funnel webhook (clone the Plaid webhook-handler pattern from 11b c3 — JWT verification, dispatch by `resourceState` field instead of `webhook_code`); cursor on Google's per-calendar `syncToken`.

- **CalDAV**: HTTP basic-auth with username + password 1Password reference (Plaid's `op://` ref pattern but for a literal password rather than an item access token); NO webhook (CalDAV's RFC 4791 protocol does not specify push; the adapter polls every `poll_interval_s` per `docs/reference/caldav/python-caldav-README.md`); cursor on per-calendar CTag (the CalDAV server's collection-modification etag).

**Schema reuse: NO new event types** [split memo §2 (11d row), BUILD.md L1 §"Calendar adapters"]. Both adapters emit:

- `calendar.event_added` v1 — when a new event appears in a watched calendar
- `calendar.event_updated` v1 — when a watched event's fields change
- `calendar.event_deleted` v1 — when a watched event disappears

These are already registered at v1 in `adminme/events/schemas/ingest.py` (from prompt 04). The adapter's c1 deliverable is **zero new schemas**; instead, c1 ships the calendar-family config-schema fragment that both packs reference.

**Shared config-schema fragment** [split memo §2 (11d row), §"c1: shared calendar adapter config schema"]. The fragment at `packs/adapters/calendaring/_shared/config.schema.json` defines the calendar-family-common fields: `member_id`, `target_calendar_ids` (array of external calendar identifiers to watch), `poll_interval_s`, `sensitivity_floor: const "normal"`. Both Google and CalDAV pack schemas `$ref` this fragment via JSON Schema's allOf composition, then add their pack-specific fields (Google: `service_account_key_ref`, `webhook_route`; CalDAV: `server_url`, `username`, `password_ref`).

**Sensitivity floor: `normal` for both** [§6 invariant 10]. Calendar metadata is personal but not privileged (no financial / legal / health data flows). Same posture as Gmail (`normal`) and Reminders (`normal`); distinct from Plaid (`privileged`). The pack-level constant is enforced via class attribute + config-loader runtime check + JSON-schema `"const": "normal"` mirror — the triple-defense pattern 11c established.

**Cursor model** [BUILD.md L1 §"Calendar adapters", `docs/reference/google-calendar/calendar-README.md`, `docs/reference/caldav/_index.md`]. Google's `events.list` accepts `syncToken` for incremental fetch — adapter persists `(calendar_id → syncToken)`. CalDAV's `PROPFIND` returns a `getctag` property per collection — adapter persists `(calendar_url → ctag)`. `adapter.cursor_advanced` emission gated on the 11a milestone discipline (every 100 events OR every 5 minutes per calendar, whichever first).

**Anti-vision halt** [VISION §"Quiet by default", DRIFT-CONTROL Job 4]. If the inbound poll emits one `calendar.event_*` event per calendar entry per poll cycle (instead of per genuinely-new-or-changed entry), stop and report. Both adapters use the upstream-provided incremental cursor as the gate — Google's syncToken returns ONLY changes since the prior token; CalDAV's ctag comparison short-circuits the full `REPORT` query when nothing changed at the collection level.

**Mode:** Phase A. No live Google Calendar API / iCloud CalDAV calls during tests; recorded fixtures + mocked SDK clients. The Google adapter uses `google-api-python-client` (already pinned for Gmail per 11a); CalDAV uses `caldav` (per `docs/reference/caldav/python-caldav-README.md`). Tests substitute fakes via constructor injection per PM-27.

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md` ranges. The `calendar-v3.ts` (337 KB) is reference-only; grep for specific operation signatures, never full-read.

1. **`docs/06-split-memo-11.md`** — full read. §2 (11d row defines scope: 2 packs, shared schema fragment, clone-shape siblings, $60-100 budget), §3 (depends on 11a; 11b/c are siblings), §5 (constitutional citations).
2. **`docs/07-deferred-adapter-framework-expansion.md`** — full read. Same Gate 0 disposition as 11b/11c: workspace's expanded framework is deferred; main's simple-sequence 11d (this prompt) is canon.
3. **`adminme/adapters/base.py`** — full read. Adapter Protocol from 11a c1.
4. **`adminme/events/schemas/adapters.py`** — full read. The 3 cross-cutting schemas; both calendar adapters emit all three.
5. **`adminme/events/schemas/ingest.py`** — full read. **Critical**: `CalendarEventAddedV1`, `CalendarEventUpdatedV1`, `CalendarEventDeletedV1` already exist at v1 (lines 50-77). 11d does NOT register new schemas; it emits these. Confirm the field shapes before writing the adapter's emit helpers.
6. **`packs/adapters/messaging/gmail/pack.yaml`** — full read. The Google Calendar `pack.yaml` mirrors this almost verbatim — same `family` shape, same `triggers.kind: external_poll`, same events_emitted list pattern. Substitutions: `family: messaging` → `family: calendaring`; events_emitted swaps `messaging.received` for `calendar.event_added` / `calendar.event_updated` / `calendar.event_deleted`.
7. **`packs/adapters/messaging/gmail/adapter.py`** — full read. The Google Calendar `adapter.py` clones the OAuth2 service-account auth + cursor-based ingest structure verbatim. Substitutions: Gmail API client → Google Calendar API client (`build("calendar", "v3", credentials=...)`); `users.history.list` → `events.list(syncToken=...)`; `messaging.received` emit → `calendar.event_added` / `calendar.event_updated` / `calendar.event_deleted` emit (one per `events.list` response item's `status` field: `"confirmed"` new → added, `"confirmed"` updated → updated, `"cancelled"` → deleted).
8. **`packs/adapters/financial/plaid/adapter.py`** — targeted read of `webhook_handler` (Google Calendar adapter's push-notification path clones this shape; substitutions: Plaid's JWT verifier → Google's `X-Goog-Channel-*` header verification; Plaid's `webhook_code` dispatch → Google's `resourceState` dispatch). The 7-tuple defensive-catch pattern carries forward unchanged.
9. **`packs/adapters/reminders/apple-eventkit/adapter.py`** — sibling reference for the inbound-poll loop shape + the milestone-cursor-advance pattern. Calendar adapters are inbound-only (no outbound loop), so the c2/c3 deliverables are simpler than 11c's bidirectional version.
10. **`adminme/daemons/adapter_supervisor.py`** — full read. Confirm pack-discovery handles `packs/adapters/calendaring/google/` AND `packs/adapters/calendaring/caldav/` — both must register under the same supervisor instance.
11. **`config/channels.yaml`** — full read. Extend with TWO new entries (`calendaring-google` + `calendaring-caldav`) alongside the existing Gmail / Plaid / Reminders entries.
12. **`ADMINISTRATEME_BUILD.md`** section **"L1: ADAPTERS"** + section **"Calendar adapters"** — targeted via `grep -n '^### L1\|^### Calendar' ADMINISTRATEME_BUILD.md` first, then `sed -n '<start>,<+100>p'` for each. Read for: emission contract (existing `calendar.event_*` v1 schemas), Google push-notification flow (`channels.watch` API + Funnel-backed webhook), CalDAV polling cadence default (5 min reasonable; configurable), sensitivity_floor `normal`.
13. **`docs/reference/google-calendar/_index.md`** — full read (~1.2 KB). Section index.
14. **`docs/reference/google-calendar/calendar-README.md`** — full read (~1.6 KB). Quickstart + sync-token semantics. Cite for the c2 `events.list(syncToken=...)` choice.
15. **`docs/reference/google-calendar/calendar-v3.ts`** — **DO NOT full-read (337 KB)**. Use `grep -nE '(eventsList\|channelsWatch\|EventResource\|SyncToken)' docs/reference/google-calendar/calendar-v3.ts` to find specific operation signatures, then `sed -n '<line>,<+30>p'`. Cite line ranges in the adapter docstring.
16. **`docs/reference/caldav/_index.md`** — full read (~1.8 KB). Section index + library overview.
17. **`docs/reference/caldav/python-caldav-README.md`** — full read (~1.9 KB). The `caldav` Python library quickstart. Cite for the c3 `caldav.DAVClient` instantiation pattern + `principal.calendars()` enumeration.
18. **`docs/reference/caldav/CHANGELOG.md`** — **DO NOT full-read (66 KB)**. Targeted `grep -nE '(version 2|breaking)' docs/reference/caldav/CHANGELOG.md` if the c3 adapter hits an API-changed exception during testing. Reference-only.
19. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads: §1 (event log append-only), §6 invariant 10 (sensitivity floor), §7.3 (no projection direct writes), §8 (no LLM SDKs).
20. **`docs/DECISIONS.md`** — read: D6 (no LLM SDK), D7 (schemas at v1 — but 11d does NOT register new ones), D8 (correlation_id), D17 (adapters don't write projections).
21. **`docs/build_log.md`** — read **Prompt 11a, 11b, 11c entries** end-to-end. Carry-forwards into 11d. Particularly: F-3 pack-internal-test rename (applies to BOTH new pack tests), the milestone-cursor discipline, the stacked-merge anti-pattern fix from PR #106 (no manual base-change dance expected).
22. **`prompts/_active/11c-adapter-reminders-bidirectional.md`** — quality-bar reference. Cite shape only; do not copy content.

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad confirming: (a) both adapters implement `Adapter` Protocol per `base.py`; (b) `sensitivity_floor = "normal"` is a constant for both, JSON-schema-enforced via `$ref` to the shared fragment; (c) NO new event types — both adapters emit existing `calendar.event_added/updated/deleted` v1; (d) Google cursor is `(calendar_id → syncToken)`; CalDAV cursor is `(calendar_url → ctag)`; (e) Google has webhook; CalDAV does not; (f) F-3 pack-internal-test rename applies to both new pack tests.

---

## §3 Reconciliation notes (carry-forwards from 11a + 11b + 11c merges)

- **F-3 HARD (10c-iii heritage, reinforced by 11a + 11b + 11c):** pack-internal tests use unique basenames. Patterns: `packs/adapters/calendaring/google/tests/test_google_calendar_adapter_pack_internal.py` and `packs/adapters/calendaring/caldav/tests/test_caldav_adapter_pack_internal.py`. Both required.
- **PM-27 SOFT (convention):** both adapter classes accept constructor-injected callables for their SDK clients (Google's `googleapiclient.discovery.build` and CalDAV's `caldav.DAVClient`). No-op defaults so the classes construct cleanly without live credentials; tests substitute fakes.
- **Stacked-merge anti-pattern fix (PR #106 — on main):** c2/c3/c4 PRs from this cycle open with `base=main` automatically. If ghost conflicts surface on c2/c3/c4 after a prior c-band merges, apply the regex pattern proven on 11c (one JS execution per file replaces dozens of button clicks).
- **Constitution-sync rule (PR #97):** this PR adds NO new event types, NO new schema modules, and modifies NO constitutional docs. The CI cascade check should pass without an exemption block.
- **Surface drift carry-forward (Gate 0):** workspace pinned `PROMPT_SEQUENCE.md` still shows the expanded "Cat-D adapters" framing. Per operator's standing Option C disposition (4th application), main is canon.
- **NEW deferred-watch:** the bootstrap-wizard's calendar-pairing flow (prompt 16's Google OAuth UX + CalDAV server URL collection) is deferred — 11d's adapters consume `op://` references from `config/channels.yaml` set manually until prompt 16 lands.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `adminme/adapters/base.py` has the full `Adapter` Protocol (post-11a). Verified at authorship; confirm at c1 start.

A2. `adminme/events/schemas/adapters.py` registers the 3 cross-cutting schemas at v1. Verified at authorship.

A3. `adminme/events/schemas/ingest.py` has `CalendarEventAddedV1`, `CalendarEventUpdatedV1`, `CalendarEventDeletedV1` registered at v1. **Verified at authorship via direct fetch** (2026-05-16). If any is absent, halt — 11d's emission contract collapses without them and `verify_invariants.sh` will fail.

A4. `packs/adapters/messaging/gmail/` and `packs/adapters/financial/plaid/` directories exist on main with their full file complements. Verified at authorship.

A5. `adminme/daemons/adapter_supervisor.py` exists (from 11a c1). Confirm the pack-discovery handles `packs/adapters/calendaring/google/` AND `packs/adapters/calendaring/caldav/` — the `<family>/<name>/` pattern should be agnostic to `<family>` value (`calendaring` is new but the pattern is established).

A6. No prior `packs/adapters/calendaring/` directory exists. Verify with `mcp__github__get_file_contents(path="packs/adapters/calendaring/", ref="main")` returning 404. If it exists already, halt and reconcile.

A7. `docs/reference/google-calendar/` and `docs/reference/caldav/` both exist with their reference files. Verified at authorship (3 files + 4 entries respectively).

A8. The `google-api-python-client` package (already pinned for Gmail) covers Calendar API too. The `caldav` package is NOT yet pinned. c3 will add it to `pyproject.toml` — or, if the operator's poetry environment forbids new dependencies in a phase A prompt, fall back to a shim path like 11c's `_eventkit_shim.py` (a Protocol-shaped `DAVClientLike` interface).

---

## §5 Objective

Ship two calendar-family adapter packs at `packs/adapters/calendaring/google/` and `packs/adapters/calendaring/caldav/`, a shared config-schema fragment at `packs/adapters/calendaring/_shared/config.schema.json`, an extension to `config/channels.yaml` with both entries, and ≥18 new tests across the four commits. Both adapters reuse the existing `calendar.event_*` v1 schemas from `adminme/events/schemas/ingest.py` — no new event types. Google clones Gmail's OAuth + Plaid's webhook patterns; CalDAV is the simplest adapter in the cohort (basic-auth + polling-only, no webhook). Establishes the calendar-adapter family for any future entries (Fastmail, Nextcloud) — both will clone these two.

---

## §6 Out of scope

- **No new event types.** `calendar.event_*` v1 already exist; 11d emits them.
- **Bidirectional calendar sync** (AdministrateMe → external calendar event writes). Per BUILD.md L1, calendar adapters are inbound-only in Phase A; bidirectional is a future-prompt concern.
- **Outlook / Microsoft Exchange / Office 365 calendar adapters** — not in the 11 cohort; future prompt.
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12.
- **The `calendar` projection's writer pipeline** — adapters EMIT; projection consumes in a later prompt. Adapter dedupe relies on the upstream cursor (Google's `syncToken`, CalDAV's `ctag`) — NOT on the projection.
- **Live Google Calendar API / iCloud CalDAV sandbox** — tests use recorded fixtures + mocked clients. Lab-mode against a real Google or iCloud account is OPTIONAL (skipped in CI; manual operator-run, deferred to Phase B smoke test in prompt 19).
- **Bootstrap-wizard credential collection** — prompt 16. Until then, operators wire `op://` refs in `config/channels.yaml` by hand.
- **Calendar invite RSVP / decline / accept actions** — adapters only INGEST events; outbound RSVP composition is OpenClaw's job (channels, prompt 12).
- **Recurring-event expansion** — adapters emit the raw `events.list` / CalDAV `REPORT` response as-is per event resource. Per-occurrence expansion (e.g., a weekly meeting → 52 events per year) is the consuming projection's responsibility.
- **`/freebusy` queries / busy-status indication** — projections + console consume `calendar.event_*` events to derive this; not the adapter's job.

---

## §7 Deliverables

### No new event types (deliberate)

Per §1 grounding + split memo §2: `CalendarEventAddedV1`, `CalendarEventUpdatedV1`, `CalendarEventDeletedV1` already register at v1 in `adminme/events/schemas/ingest.py`. The 11d adapters emit these.

### Calendar-family shared config-schema fragment (NEW)

`packs/adapters/calendaring/_shared/config.schema.json` — JSON Schema fragment defining the calendar-family-common fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `member_id` | string | yes | The household member whose calendar this adapter watches |
| `target_calendar_ids` | array of string | yes | External calendar identifiers (Google: `primary` or `<calendar>@group.calendar.google.com`; CalDAV: full collection URLs) |
| `poll_interval_s` | int (≥30) | no | Default 300 (5 min). Google's webhook is primary; this is a backstop poll |
| `sensitivity_floor` | const `"normal"` | yes | JSON-schema rejects any other value |

Both pack-specific `config.schema.json` files `$ref` this fragment via `allOf` and add their pack-specific fields.

### Google Calendar adapter pack at `packs/adapters/calendaring/google/`

- **`pack.yaml`** — `kind: adapter`, `family: calendaring`, `triggers.kind: webhook_driven`, `events_emitted: [adapter.error, adapter.authenticated, adapter.cursor_advanced, calendar.event_added, calendar.event_updated, calendar.event_deleted]`, `config.schema: config.schema.json`.

- **`adapter.py`** — implements `Adapter` Protocol. ~280 lines.
  - Class attributes: `name = "calendaring-google"`, `family = "calendaring"`, `version = "1.0.0"`, `sensitivity_floor = "normal"`.
  - `start(ctx)`: assert sensitivity_floor; instantiate `googleapiclient.discovery.build("calendar", "v3", credentials=...)` via factory; emit `adapter.authenticated`; for each target calendar, register a `channels.watch` push channel pointing at `config.webhook_route`; begin backstop poll loop.
  - `webhook_handler(headers, payload)`: verify `X-Goog-Channel-Token` against config; on `X-Goog-Resource-State: exists`, enqueue a sync cycle for the affected calendar; ignore `sync` / `not_exists` codes.
  - `_sync_calendar(calendar_id)`: call `events.list(calendarId=..., syncToken=<persisted>)`; for each event in response, dispatch on `status` field: `confirmed + new` → emit `calendar.event_added`; `confirmed + updated` → emit `calendar.event_updated`; `cancelled` → emit `calendar.event_deleted`. Persist new `nextSyncToken`. Milestone `adapter.cursor_advanced` per 11a discipline.
  - Defensive catch on the 7-tuple pattern from 11b's Plaid adapter (Google API SDK exceptions classify into `AdapterErrorV1` severity + `error_kind`).
  - `stop()`: cancel poll task; unregister webhook channels via `channels.stop`.

- **`config.schema.json`** — `$ref` `../_shared/config.schema.json` + add: `service_account_key_ref` (1Password ref), `webhook_route` (e.g., `"/webhooks/google-calendar"`), `oauth_subject` (the user-email the service-account impersonates).

- **`config.example.yaml`** — placeholder `op://` references.

- **`tests/test_google_calendar_adapter_pack_internal.py`** — pack-load canary (F-3 rename) + handler-direct tests.

### CalDAV adapter pack at `packs/adapters/calendaring/caldav/`

- **`pack.yaml`** — `kind: adapter`, `family: calendaring`, `triggers.kind: external_poll`, `triggers.schedule: "*/5 * * * *"` (5-min cron heartbeat; in-process `poll_interval_s` is the active rate), `events_emitted` mirrors Google's list.

- **`adapter.py`** — implements `Adapter` Protocol. ~240 lines (simpler than Google — no webhook).
  - Class attributes: `name = "calendaring-caldav"`, `family = "calendaring"`, `version = "1.0.0"`, `sensitivity_floor = "normal"`.
  - `start(ctx)`: assert sensitivity_floor; instantiate `caldav.DAVClient(url=..., username=..., password=<resolved-from-op-ref>)` via factory; for each target calendar URL, fetch initial `getctag` and seed cursor; emit `adapter.authenticated`; begin poll loop.
  - `_poll_calendar(calendar_url)`: `PROPFIND` for current `getctag`; compare to persisted; if unchanged, return early (the dedupe gate). If changed, run `REPORT` calendar-query for all events; diff against the projected-prior state via injected resolver (permissive default if projection absent); emit `calendar.event_added` / `_updated` / `_deleted` per delta entry. Persist new `getctag`. Milestone `adapter.cursor_advanced` per 11a discipline.
  - Defensive catch on the same 7-tuple pattern.
  - `stop()`: cancel poll task; release the DAVClient session.

- **`config.schema.json`** — `$ref` `../_shared/config.schema.json` + add: `server_url`, `username`, `password_ref` (1Password ref).

- **`config.example.yaml`** — placeholder for iCloud (`https://caldav.icloud.com/`), Fastmail, Nextcloud server URLs.

- **`tests/test_caldav_adapter_pack_internal.py`** — pack-load canary (F-3 rename) + handler-direct tests.

### Extension to `config/channels.yaml`

Append two new entries alongside Gmail + Plaid + Reminders. Both `sensitivity_floor: normal`, both reference real-shape `op://` paths as placeholders.

### Tests (total ≥18 across the four commits)

- **Unit (c1) — 5+ schema + manifest canaries:** shared config schema validates a minimal calendar config; rejects `sensitivity_floor` ≠ `"normal"`; rejects empty `target_calendar_ids`; Google pack manifest loads + declares `family: calendaring`; CalDAV pack manifest loads + declares the 5-min cron.
- **Unit (c2 Google) — 5+ adapter tests:** OAuth-stub success path; `events.list` happy path with recorded fixture; webhook handler dispatches on `X-Goog-Resource-State: exists`; sync persists `nextSyncToken`; `calendar.event_added` emission shape; `sensitivity_floor` cannot be raised via config.
- **Unit (c3 CalDAV) — 5+ adapter tests:** basic-auth stub; `PROPFIND` returns unchanged ctag → early-return (zero events emitted); `PROPFIND` returns changed ctag → `REPORT` runs + emits deltas; emits all 3 event types from a multi-entry fixture; `calendar.event_deleted` on a removed entry; defensive error catch on a 401 response.
- **Pack-internal (c2 + c3):** 2 pack-load canaries (F-3 rename for each pack).
- **Integration (c4):** 1 round-trip per pack — (Google) supervisor starts → spawns adapter → webhook arrives → sync fires → `calendar.event_added` in log; (CalDAV) supervisor starts → spawns adapter → poll fires → events emitted. Both `@pytest.mark.skip_in_ci`.

Floor: ≥18 new tests across 11d. Overshoot welcome.

---

## §8 Commit structure (exactly four commits; messages exact)

Every commit message follows `phase 11d commit <N>: <imperative summary>` exactly. Branch strategy STACKED per §8.5; PR base = `main` for ALL FOUR c-bands (PR #106 sidecar default).

### Commit 1 — Shared schema fragment + pack manifests

**Files:** `packs/adapters/calendaring/_shared/config.schema.json` (NEW), `packs/adapters/calendaring/google/pack.yaml` (NEW), `packs/adapters/calendaring/google/config.schema.json` (NEW, `$ref` _shared), `packs/adapters/calendaring/google/config.example.yaml` (NEW), `packs/adapters/calendaring/caldav/pack.yaml` (NEW), `packs/adapters/calendaring/caldav/config.schema.json` (NEW, `$ref` _shared), `packs/adapters/calendaring/caldav/config.example.yaml` (NEW), `tests/unit/adapters/test_calendar_pack_manifests.py` (NEW).

**Files-to-read:** 6 (`gmail/pack.yaml` for manifest template, `gmail/config.schema.json` for $ref pattern, `plaid/pack.yaml` for webhook_driven triggers shape, BUILD.md L1 Calendar adapters section, split memo §2 (11d row), `ingest.py` to confirm `calendar.event_*` shapes for the `events_emitted` declarations).

**Verification:**
```
poetry run python -c "import json; s=json.load(open('packs/adapters/calendaring/_shared/config.schema.json')); print('OK', s.get('title'))"
poetry run pytest tests/unit/adapters/test_calendar_pack_manifests.py -v
git add packs/adapters/calendaring/ tests/unit/adapters/test_calendar_pack_manifests.py
git commit -m "phase 11d commit 1: shared calendar config schema + Google + CalDAV pack manifests"
```

Expected: ≥5 manifest tests pass; JSON schemas load cleanly; both pack.yaml files declare `family: calendaring`.

### Commit 2 — Google Calendar adapter

**Files:** `packs/adapters/calendaring/google/adapter.py` (NEW, full), `packs/adapters/calendaring/google/tests/test_google_calendar_adapter_pack_internal.py` (NEW, F-3 rename).

**Files-to-read:** 5 (`gmail/adapter.py` for OAuth + cursor pattern, `plaid/adapter.py` for webhook_handler pattern, `base.py` for Protocol, `google-calendar/calendar-README.md`, targeted `calendar-v3.ts` grep for `eventsList` + `channelsWatch` signatures).

**Verification:**
```
poetry run mypy packs/adapters/calendaring/google/ 2>&1 | tail -5
poetry run pytest packs/adapters/calendaring/google/tests/ -v
git add packs/adapters/calendaring/google/adapter.py packs/adapters/calendaring/google/tests/test_google_calendar_adapter_pack_internal.py
git commit -m "phase 11d commit 2: Google Calendar adapter (OAuth + push notifications)"
```

Expected: ≥6 tests pass (1 pack-load canary + 5 adapter tests).

### Commit 3 — CalDAV adapter

**Files:** `packs/adapters/calendaring/caldav/adapter.py` (NEW, full), `packs/adapters/calendaring/caldav/tests/test_caldav_adapter_pack_internal.py` (NEW, F-3 rename). Add `caldav` to `pyproject.toml` (or implement a `_caldav_shim.py` Protocol per A8 fallback if poetry environment rejects new deps).

**Files-to-read:** 5 (`google/adapter.py` from c2 for parallel structure, `caldav/_index.md`, `caldav/python-caldav-README.md`, `reminders/apple-eventkit/adapter.py` for the inbound-only poll-loop shape, `base.py` for Protocol).

**Verification:**
```
poetry run mypy packs/adapters/calendaring/caldav/ 2>&1 | tail -5
poetry run pytest packs/adapters/calendaring/caldav/tests/ -v
git add packs/adapters/calendaring/caldav/adapter.py packs/adapters/calendaring/caldav/tests/test_caldav_adapter_pack_internal.py pyproject.toml
git commit -m "phase 11d commit 3: CalDAV adapter (basic-auth + polling + ctag dedupe)"
```

Expected: ≥6 tests pass (1 pack-load canary + 5 adapter tests).

### Commit 4 — Integration tests + BUILD_LOG

**Files:** `tests/integration/test_adapter_google_calendar_supervisor.py` (NEW, 1 round-trip, `@pytest.mark.skip_in_ci`), `tests/integration/test_adapter_caldav_supervisor.py` (NEW, 1 round-trip, `@pytest.mark.skip_in_ci`), `config/channels.yaml` (extend with both new entries), `docs/build_log.md` (append 11d entry).

**Files-to-read:** 3 (existing `tests/integration/test_adapter_supervisor_gmail.py` from 11a c4 for template, build_log tail for append point, BUILD.md L1 Calendar section for the 11d Evidence-section talking points).

**Build_log Evidence template:** Append AFTER 11c entry. Header: `### Prompt 11d — Google Calendar + CalDAV adapters`. Mirror 11a/11b/11c Evidence-section density. Cite: ZERO new event types (schema reuse from prompt 04); the shared config-schema fragment as the new calendar-family pattern; Google's OAuth + push-notification clone of Gmail+Plaid; CalDAV's polling + ctag dedupe; carry-forwards for future calendar adapters (Outlook etc. clone from here); soft-watch entries for the deferred bootstrap-wizard calendar-pairing flow and the deferred bidirectional-calendar-write surface.

**Verification:**
```
poetry run ruff check adminme/ packs/ tests/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/ config/channels.yaml
git commit -m "phase 11d commit 4: integration tests + channels.yaml + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+18; `verify_invariants.sh` exit 0.

**PR open:** Title `phase 11d: Google Calendar + CalDAV adapters`. Body: top line "Fourth and final sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`. Ships two clone-shape calendar adapters: Google (OAuth + push notifications) cloning Gmail+Plaid patterns, and CalDAV (basic-auth + polling) as the cohort's simplest adapter. Both emit existing `calendar.event_*` v1 schemas — no new event types." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` is `main` (per PR #106 sidecar). Pre-flight stack-integrity check on c4 expects 3 prior commits above `origin/main`.

Ghost conflicts on c2/c3/c4 (squash-merge artifacts) are expected per the 11c lesson — resolve via the regex pattern: in the GitHub web editor for each conflicted file, run JS to replace `<<<<<<< claude/11d-cN ... ======= ... >>>>>>> main` blocks with the current-change side. One execution per file.

---

## §9 Verification (final)

Across the four-commit sequence: mypy clean on all changes; ≥18 new tests passing; ruff clean on `adminme/` / `packs/` / `tests/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`.

Cross-cutting [§N] / [DN] checks (enforced by `verify_invariants.sh`):

- [§7.3, D17]: zero projection writes from either calendar adapter (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' packs/adapters/calendaring/` returns 0 matches).
- [§8.2, D6]: no LLM SDK import anywhere under `packs/adapters/calendaring/`.
- [§6 invariant 10]: `sensitivity_floor` is `"normal"` and JSON-schema enforced via the shared fragment's `"const": "normal"`. Tests assert; config-schema enforces.
- [D7]: NO new event types registered. `registry.latest_version("calendar.event_added")` remains 1 (unchanged from prompt 04). Test asserts this stays at 1 post-merge.
- [D8]: `correlation_id` set on entry. `AdapterContext.correlation_id_factory` provides it.
- [Causation]: webhook-triggered events have `causation_id=<inbound webhook event_id>`; poll-triggered events have `causation_id=<supervisor cron tick event_id>`; the upstream-cursor-only-changed case carries `causation_id=None`.
- F-3 (10c-iii heritage): both pack-internal tests use unique basenames (`test_google_calendar_adapter_pack_internal.py`, `test_caldav_adapter_pack_internal.py`).
- **VISION §"Quiet by default":** assert that a Google sync with an unchanged `syncToken` returns zero new events emitted, and a CalDAV poll with an unchanged `ctag` short-circuits before the `REPORT` query.

---

## §10 Stop

Google Calendar adapter operational with mocked OAuth + push-notification path; CalDAV adapter operational with mocked basic-auth + polling. Both reuse the existing `calendar.event_*` v1 schemas. Shared calendar-family config-schema fragment established for future adapters. Both `sensitivity_floor: normal`. `config/channels.yaml` extended with both entries.

**11 cohort complete.** All five adapters now standalone: Gmail (11a), Plaid (11b), Apple Reminders (11c bidirectional), Google Calendar + CalDAV (11d). Ready for prompt 12 (OpenClaw plugin messaging adapters: BlueBubbles, Telegram, Discord).
