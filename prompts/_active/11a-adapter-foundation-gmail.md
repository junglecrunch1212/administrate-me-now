# Prompt 11a: Adapter foundation + Gmail adapter

**Phase:** Phase A — L1 ingest adapters, foundation sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`.

**Branch:** `claude/11a-<auto>` (harness-assigned). PR base = `main`. STACKED across c1→c4 per §8.5.

**Depends on:** Prompt 10c (event-log + projection patterns established); prompt 02 (which produced the docstring-only stub at `adminme/adapters/base.py`).

**Quality bar:** clones `packs/pipelines/morning_digest/`'s pack shape with adapter-specific substitutions. The BlueBubbles adapter pattern from `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §1 is the conceptual reference (note: BlueBubbles is a **plugin** adapter, prompt 12; the shape is similar but the runtime is different).

**Stop condition:** Adapter supervisor (`adminme/daemons/adapter_supervisor.py`) is a runnable daemon that reads `config/channels.yaml`, spawns each configured adapter as an async task, restarts on crash with exponential backoff. Adapter protocol (`adminme/adapters/base.py`) is the load-bearing `Protocol` class every adapter satisfies. Gmail adapter pack (`packs/adapters/messaging/gmail/`) authenticates against a sandbox Gmail, polls the history API, emits `messaging.received` events. Three cross-cutting adapter event schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) register at v1 in a new `adminme/events/schemas/adapters.py` module. ≥18 new tests across the four commits. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~75-90 minutes wall clock, 4 commits.

---

## §1 Behavioral grounding

You are building **the foundation** for all five L1 standalone adapters. Three of the adapters (Plaid, Apple Reminders, Google Calendar + CalDAV) ship in subsequent sub-prompts (11b, 11c, 11d) per `docs/06-split-memo-11.md`. Your job is the foundation that those three will clone from, **plus** the simplest of the five — Gmail — as the worked example that validates the foundation.

**Adapter contract** [§L1, architecture-summary §1]. Adapters translate external sources into typed events on the event log. They are the only place in `adminme/` that talks to external APIs. They emit events; they NEVER write projections [§7.3, D17]; they NEVER call pipelines; they NEVER compose outbound messages [§8 invariant 3 — channel transport is OpenClaw's job]. They set `correlation_id` on entry if the inbound request lacks one [D8 addition 1]. They observe sensitivity-floor rules: privileged adapters (e.g., law-practice email) hardcode a `sensitivity_floor` at the adapter level; the config loader rejects any configuration that would lower it [§6 invariant 10].

**Mode:** Phase A. No live external API calls during tests; use sandbox credentials at most for integration smoke tests scoped to lab work [§Out of scope of this prompt]. No `anthropic` / `openai` imports anywhere under `packs/adapters/` [§8.2, D6]. Mirrored reference docs at `docs/reference/google-gmail/` are the SOURCE OF TRUTH for Gmail API shape — read from the mirror only; do NOT WebFetch or rely on memory for endpoint signatures.

**Adapter supervisor** [§L1 supervisor]. The supervisor is a long-running async daemon. On startup it reads `config/channels.yaml`, spawns each configured adapter as an asyncio task, watches for unhandled exceptions, and restarts on crash with exponential backoff capped at 5 minutes. It exposes a `/status` JSON endpoint at `http://127.0.0.1:<port>/status` (port from config; default 8081 distinct from the runner's 8080). The supervisor is NOT a pipeline; NOT a projection subscriber; NOT an OpenClaw plugin. It's a process-management primitive.

**Adapter-pack shape** [10c-iii precedent + this prompt establishes for adapters]. Each adapter is a pack at `packs/adapters/<family>/<name>/` per operator decision in `docs/06-split-memo-11.md` §4. The pack contains `pack.yaml` (manifest), `adapter.py` (implements `Adapter` protocol from `adminme.adapters.base`), `config.schema.json`, `config.example.yaml`, `tests/test_<name>_adapter_pack_internal.py` (F-3 rename pattern per 10b-ii build_log). Existing pre-scaffolded `adminme/adapters/<family>/` subdirectories from prompt 02 are dead code under the operator's decision (§4 in the split memo); c1 includes a cleanup step removing them.

**The three cross-cutting schemas** [D7]. `adapter.error` v1 — emitted when an adapter encounters a transient or fatal failure (auth error, rate limit, network blip). `adapter.authenticated` v1 — emitted once per startup-auth success per adapter, carrying token-storage reference (1Password reference style — NEVER the secret itself). `adapter.cursor_advanced` v1 — emitted when an adapter persists a new cursor position (e.g., Gmail history ID, Plaid sync cursor). All three register at v1 in a NEW module `adminme/events/schemas/adapters.py` (the precedent of cross-cutting per-domain modules: `domain.py`, `crm.py`, `ingest.py`, `governance.py` — adapters is a peer).

**Anti-vision halt** [VISION §"Quiet by default", DRIFT-CONTROL Job 4]. If the foundation you build encourages adapter implementations to emit noisy stream-of-everything events (every successful Gmail poll = N+1 events of any kind, every cursor tick a separate event), stop and report — the design pattern below limits the cursor-advanced emission to milestone advances (every 100 messages or every 5 minutes, whichever first), not per-message.

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md` / `REFERENCE_EXAMPLES.md` ranges; do NOT full-read those files. Verify section anchors via `grep -n '^### L1' ADMINISTRATEME_BUILD.md` before reading.

1. **`docs/06-split-memo-11.md`** — full read. This is the split decision; it scopes 11a precisely and flags the dependencies between 11a and 11b/c/d. Read §2 (scope), §4 (code-location decision), §6 (next-move sequencing).
2. **`adminme/adapters/base.py`** — full read. Currently a docstring-only stub from prompt 02. C1 firms it up to a complete `Protocol` class.
3. **`adminme/daemons/__init__.py`** and a `ls adminme/daemons/` — confirm directory exists; identify any sibling daemons whose patterns inform `adapter_supervisor.py` shape.
4. **`packs/pipelines/morning_digest/pack.yaml`** — full read. Manifest shape reference for `packs/adapters/messaging/gmail/pack.yaml`. Substitutions: `kind: pipeline` → `kind: adapter`; `triggers.{events,schedule,proactive}` → `triggers.{kind: external_poll, schedule}` (adapters poll or react to webhooks; they don't trigger on internal events).
5. **`ADMINISTRATEME_BUILD.md`** section **"L1: ADAPTERS"** — targeted via `grep -n '^### L1' ADMINISTRATEME_BUILD.md` first to find line number, then `sed -n '<start>,<+150>p'`. Read for the two-runtime distinction (plugin vs. standalone) and the adapter-supervisor lifecycle.
6. **`ADMINISTRATEME_REFERENCE_EXAMPLES.md` §1 (BlueBubbles)** — targeted via `grep -n '^## §1' ADMINISTRATEME_REFERENCE_EXAMPLES.md`. Read the BlueBubbles example end-to-end. It's a plugin adapter (prompt 12), NOT a standalone, so the runtime details differ — but the shape (cursor persistence, error emission, auth-on-startup) is the conceptual template Gmail clones.
7. **`docs/reference/google-gmail/_index.md`** and **`docs/reference/google-gmail/gmail-README.md`** — full read both. These are the mirrored API docs and the quickstart. They tell you which Gmail API endpoints the adapter calls (`users.messages.list`, `users.history.list`, `users.messages.get`, `users.watch` for push notifications) and the OAuth2 service-account flow.
8. **`docs/reference/google-gmail/gmail-v1.ts`** — DO NOT full-read (500 KB). Use `grep -nE '(users\.history|users\.messages|users\.watch)' docs/reference/google-gmail/gmail-v1.ts` to find the endpoint type signatures, then `sed -n '<line>,<+30>p'` for the specific signatures the adapter uses. Cite the line ranges in the adapter's docstring.
9. **`adminme/events/schemas/governance.py`** — full read. Pattern template for the new `adminme/events/schemas/adapters.py` module — schema BaseModel shape, `registry.register(...)` at module end, file-level docstring style.
10. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads via `grep -n '^## §[1678]' docs/SYSTEM_INVARIANTS.md`: §1 (event log append-only), §6 (observation mode + governance — adapter sensitivity_floor falls under this), §7 (pipelines reactive/proactive — adapter contrast: NOT a pipeline), §8 (no LLM SDKs in adminme; channel transport is OpenClaw's territory).
11. **`docs/DECISIONS.md`** — read each: D6 (no LLM SDK), D7 (schemas at v1), D8 (correlation_id propagation), D9 (plugin event-type namespace — adapter-introduced events live in `adminme.event_types` core namespace per prompt 04 since they're first-party L1).
12. **`docs/build_log.md`** — read **Prompt 10c-iii entry** end-to-end. Carry-forwards into 11a. F-3 (pack-internal pack-load test rename) applies to the adapter pack-load canary in c4.
13. **`docs/qc_rubric.md`** — full read. Job 1 (contract match), Job 2 (invariant audit), Job 4 (vision-drift) shape post-merge QC.

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad note confirming: (a) the `Adapter` Protocol you'll write requires `name`, `family`, `version`, `sensitivity_floor`, `start()`, `stop()`, `status()`; (b) `adapter_supervisor.py` is async and watches each adapter as a Task; (c) three new event types register at v1 in a NEW module `adminme/events/schemas/adapters.py`; (d) Gmail adapter uses service-account OAuth2 (NOT user-OAuth flow) per BUILD.md L1's "supervised account" model; (e) cursor advances are milestone-only, not per-message; (f) F-3 rename applies to the pack-internal pack-load test; (g) cleanup step removes the dead `adminme/adapters/<family>/` pre-scaffold subdirs from prompt 02.

---

## §3 Reconciliation notes (carry-forwards from 10c-iii and the split memo)

- **Split-memo carry-forward (`06-split-memo-11.md` §6):** when merging c-band PRs, operator must change each PR's `base` to `main` before clicking Merge (stacked-merge anti-pattern from 10c-iii / PR #86 recovery). This is a process note for the operator; the prompt cannot enforce it.
- **F-3 HARD (10c-iii):** pack-internal pack-load tests use unique basenames. Pattern: `packs/adapters/messaging/gmail/tests/test_gmail_adapter_pack_internal.py`. Applies to this pack and to 11b/c/d's packs.
- **PM-27 SOFT (graduated to convention):** any module that needs callable loaders takes them with no-op defaults. Adapter supervisor's adapter-loader callable defaults to a stub that returns an empty list; tests inject the real one.
- **F-PB-1 from 10d audit (`docs/checkpoint-10d-report.md`):** `bootstrap/pack_install_order.yaml` is incomplete (1 of 10 skill packs listed). This prompt does NOT add the Gmail adapter pack to that file — adapter packs probably want their own `bootstrap/adapter_install_order.yaml` (decision deferred to prompt 16). Document this carry-forward in the c4 BUILD_LOG.
- **Pre-scaffold cleanup (from split memo §4):** c1 removes the dead `adminme/adapters/{calendaring,contacts,documents,financial,iot,messaging,reminders,telephony,webhook}/` subdirs (placeholder `__init__.py` files from prompt 02). Verify they contain only `__init__.py` before deleting; if any other content surfaces, halt and report.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `adminme/adapters/base.py` is docstring-only (no class definitions, no Protocol). Verified at authorship time (2026-05-15); confirm again at c1 start.

A2. `adminme/daemons/` has an `__init__.py` and likely a sibling daemon or two; the supervisor pattern should align with whatever's there. If `adminme/daemons/` is empty except for `__init__.py`, the supervisor is the first daemon and sets the precedent for future daemons.

A3. `config/channels.yaml` may or may not exist on main. If absent, c1 creates a minimal example with the Gmail adapter as the single entry. If present, c1 extends it.

A4. The three pre-scaffolded `adminme/adapters/<family>/` subdirectories contain only placeholder `__init__.py` files. Confirm before deleting in c1's cleanup step.

A5. The mirrored `docs/reference/google-gmail/gmail-v1.ts` (500 KB) is the canonical Gmail API type source. If the version is stale (e.g., predates Gmail API v1.x current shape), the adapter still targets what's mirrored; the prompt 00.5 mirror-refresh process owns currency.

---

## §5 Objective

Ship the adapter foundation: a complete `Adapter` Protocol in `adminme/adapters/base.py`, an async supervisor at `adminme/daemons/adapter_supervisor.py` that spawns + restarts adapter tasks per `config/channels.yaml`, three cross-cutting event schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) registered at v1 in a new `adminme/events/schemas/adapters.py` module, and one worked-example adapter pack at `packs/adapters/messaging/gmail/` that authenticates against a sandbox Gmail and emits `messaging.received` events via the Gmail history API. Clean up the dead `adminme/adapters/<family>/` pre-scaffold subdirs from prompt 02. Establish the patterns that 11b (Plaid), 11c (Apple Reminders), and 11d (Calendars) will clone.

---

## §6 Out of scope

- **Plaid adapter** — prompt 11b ships it. Do NOT add a `packs/adapters/financial/plaid/` directory in this prompt.
- **Apple Reminders bidirectional** — prompt 11c. Do NOT add `packs/adapters/reminders/apple-eventkit/`.
- **Google Calendar + CalDAV adapters** — prompt 11d. Do NOT add `packs/adapters/calendaring/*/`.
- **OpenClaw plugin adapters** (BlueBubbles, Telegram, Discord) — prompt 12. They have a different runtime (OpenClaw plugin, not standalone Python daemon).
- **Bootstrap wizard integration** — prompt 16 (wizard) wires adapter credentials into 1Password and the config file at install time. Out of scope here; ADD `config/channels.yaml` directly as a configured-by-hand example.
- **Live Gmail account ingest** — sandbox only. Recorded fixtures for unit tests; lab-mode integration test against a real test Gmail is OPTIONAL (skipped in CI per BUILD.md L1 testing conventions).
- **Adapter authentication UI** — adapters consume credentials from `config/channels.yaml` + 1Password references; no UI in scope here.
- **`bootstrap/adapter_install_order.yaml`** — deferred to prompt 16. Adapter packs are NOT added to `bootstrap/pack_install_order.yaml` (that's for SKILL packs only per its docstring).
- **Webhook receiver for Gmail push notifications** — the Gmail adapter implements polling (history API) in this prompt. The Pub/Sub push receiver path is a deferred optimization; can be added in a future sidecar after the polling baseline is proven.

---

## §7 Deliverables

### New event types (Pydantic models in NEW module `adminme/events/schemas/adapters.py`)

| Type | Class | Required fields | Notes |
|---|---|---|---|
| `adapter.error` | `AdapterErrorV1` | `adapter_name: str`, `adapter_family: str`, `severity: Literal["transient","fatal"]`, `error_kind: str`, `error_message: str`, `retry_after_s: int \| None`, `occurred_at: str` | `error_kind` is a short category (`auth_failed`, `rate_limited`, `network`, `api_changed`, `quota_exceeded`, `unknown`). `retry_after_s` informs the supervisor's backoff. |
| `adapter.authenticated` | `AdapterAuthenticatedV1` | `adapter_name: str`, `adapter_family: str`, `token_storage_ref: str`, `authenticated_at: str`, `scopes: list[str]` | `token_storage_ref` is a 1Password reference (`op://<vault>/<item>/<field>`), NEVER the secret value itself. `scopes` is the OAuth scope list (Gmail: `["https://www.googleapis.com/auth/gmail.readonly"]` for read-only). |
| `adapter.cursor_advanced` | `AdapterCursorAdvancedV1` | `adapter_name: str`, `adapter_family: str`, `cursor_kind: str`, `cursor_value: str`, `events_since_last: int`, `advanced_at: str` | Milestone-only emission per §1 (every 100 messages OR every 5 minutes, whichever first). `cursor_kind` is `history_id` for Gmail, `transactions_cursor` for Plaid, etc. |

All three: `model_config = {"extra": "forbid"}`. Append three `registry.register("<type>", 1, <Model>)` calls at the bottom of `adminme/events/schemas/adapters.py`. Per [D7] schemas at v1.

### `adminme/adapters/base.py` — full Protocol replacement

Replace the docstring-only stub with:

```python
class Adapter(Protocol):
    """L1 adapter protocol. Implemented by every standalone adapter pack."""
    name: str                            # e.g., "messaging-gmail-api"
    family: str                          # e.g., "messaging"
    version: str                         # SemVer of the adapter pack
    sensitivity_floor: Literal["normal", "privileged"]
    async def start(self, *, ctx: AdapterContext) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> AdapterStatus: ...
```

Plus `AdapterContext` (dataclass) and `AdapterStatus` (dataclass) supporting types. `AdapterContext` carries `event_log: EventLog`, `config: dict`, `correlation_id_factory: Callable[[], str]`, `now_factory: Callable[[], datetime]` (for testability). `AdapterStatus` carries `state: Literal["starting","running","backoff","stopped","crashed"]`, `last_error: dict | None`, `last_cursor_advance_at: datetime | None`. ≥80 lines including docstrings.

### `adminme/daemons/adapter_supervisor.py` — async supervisor

Reads `config/channels.yaml` at startup, instantiates each configured adapter via a loader callable (PM-27 — constructor-injection with no-op default), spawns each as an `asyncio.Task`, watches for crashes with `asyncio.gather(..., return_exceptions=True)` style, restarts crashed adapters with exponential backoff (1s, 2s, 4s, ..., max 300s; resets to 1s on 5+ minutes uptime). Exposes a `/status` JSON endpoint via `aiohttp` (port from config, default 8081). Emits `adapter.error` on each adapter crash before restart. ~200 lines.

### `packs/adapters/messaging/gmail/` — Gmail adapter pack

- `pack.yaml` — manifest. `kind: adapter`, `family: messaging`, declares `triggers: {kind: external_poll, schedule: "*/2 * * * *"}` (every 2 min), `depends_on.event_types: [adapter.error, adapter.authenticated, adapter.cursor_advanced, messaging.received]`, `config.schema: config.schema.json`.
- `adapter.py` — implements `Adapter` Protocol. Service-account OAuth2 auth, `users.history.list` for incremental polling (uses `cursor_kind: history_id`), `users.messages.get` to fetch full message bodies, emits `messaging.received` per the existing schema in `ingest.py`. Defensive error catch on the same exception 7-tuple pattern from `morning_digest`. ~250 lines.
- `config.schema.json` — `service_account_key_ref` (1Password ref), `user_email` (the Gmail address being polled), `sensitivity_floor` (defaults `normal`; cannot be lowered from `privileged` if config sets it).
- `config.example.yaml` — example config with placeholder references.
- `tests/test_gmail_adapter_pack_internal.py` — pack-load canary (F-3 rename) + 2 handler-direct tests (auth-stub success path, recorded-fixture ingest emits correct `messaging.received` shape). ≥3 tests.

### `config/channels.yaml`

Minimal example with the Gmail adapter as the single entry. Includes the `adapter_supervisor_port` field (defaults 8081). 

### Tests (total ≥18 across the four commits)

- Unit (c1): 3 schema-registration canaries (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced` each register at v1, accept required fields, reject extra keys). +3 base.py tests (Adapter Protocol shape compliance, AdapterContext frozen-dataclass, AdapterStatus default values).
- Unit (c1 supervisor): 3 supervisor tests (parses minimal channels.yaml, spawns + stops adapter tasks cleanly, restart-on-crash with backoff).
- Unit (c2 Gmail auth): 2 auth tests (service-account flow mocked, sensitivity_floor cannot be lowered).
- Unit (c3 Gmail ingest): 4 ingest tests (recorded-fixture history sync, messages.get fetch, cursor advances at milestone, error emission on rate-limit).
- Pack-internal (c4): 1 pack-load canary (F-3 rename).
- Integration (c4): 1 round-trip — supervisor starts, spawns Gmail adapter, fakes one polling cycle, verifies `adapter.authenticated` + `messaging.received` + `adapter.cursor_advanced` events in log. Marked `@pytest.mark.skip_in_ci` since it touches the supervisor lifecycle; runs manually.

Floor: ≥18 new tests across 11a. Overshoot welcome.

---

## §8 Commit structure (exactly four commits; messages exact)

### Commit 1 — Foundation: protocol + 3 schemas + supervisor + cleanup

**Files:** `adminme/adapters/base.py` (replace stub), `adminme/events/schemas/adapters.py` (NEW), `adminme/daemons/adapter_supervisor.py` (NEW), `config/channels.yaml` (NEW), `tests/unit/events/test_adapter_schemas.py` (NEW), `tests/unit/adapters/test_adapter_base.py` (NEW), `tests/unit/daemons/test_adapter_supervisor.py` (NEW). Cleanup: delete `adminme/adapters/{calendaring,contacts,documents,financial,iot,messaging,reminders,telephony,webhook}/__init__.py` (the empty pre-scaffold from prompt 02) AND their containing directories if empty.

**Files-to-read:** 6 (base.py stub, governance.py for schema template, morning_digest pipeline.yaml for pack-shape template, REFERENCE_EXAMPLES §1 BlueBubbles, SYSTEM_INVARIANTS §1+§6+§8, the split memo §2).

**Verification:**
```
grep -F 'class Adapter(Protocol)' adminme/adapters/base.py
poetry run python -c "from adminme.events.schemas.adapters import AdapterErrorV1, AdapterAuthenticatedV1, AdapterCursorAdvancedV1; print('OK')"
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/events/test_adapter_schemas.py tests/unit/adapters/test_adapter_base.py tests/unit/daemons/test_adapter_supervisor.py -v
ls adminme/adapters/ | wc -l  # expect: 2 (__init__.py, base.py) — no family subdirs
git add adminme/adapters/base.py adminme/events/schemas/adapters.py adminme/daemons/adapter_supervisor.py config/channels.yaml tests/unit/events/test_adapter_schemas.py tests/unit/adapters/test_adapter_base.py tests/unit/daemons/test_adapter_supervisor.py
git rm -r adminme/adapters/calendaring adminme/adapters/contacts adminme/adapters/documents adminme/adapters/financial adminme/adapters/iot adminme/adapters/messaging adminme/adapters/reminders adminme/adapters/telephony adminme/adapters/webhook
git commit -m "phase 11a commit 1: adapter protocol + 3 schemas + supervisor + cleanup"
```

Expected: mypy clean; ≥9 new tests pass.

### Commit 2 — Gmail adapter pack: manifest + auth

**Files:** `packs/adapters/messaging/gmail/pack.yaml` (NEW), `packs/adapters/messaging/gmail/adapter.py` (NEW — auth path only, ingest/cursor stubs raising `NotImplementedError` in c2), `packs/adapters/messaging/gmail/config.schema.json` (NEW), `packs/adapters/messaging/gmail/config.example.yaml` (NEW), `packs/adapters/messaging/gmail/tests/test_gmail_adapter_pack_internal.py` (NEW, F-3 rename).

**Files-to-read:** 5 (10c-iii pack.yaml as manifest template, base.py from c1 for Protocol, governance.py for sensitivity_floor pattern, google-gmail/_index.md + gmail-README.md).

**Verification:**
```
poetry run mypy packs/adapters/messaging/gmail/ 2>&1 | tail -5
poetry run pytest packs/adapters/messaging/gmail/tests/ -v
git add packs/adapters/messaging/gmail/
git commit -m "phase 11a commit 2: Gmail adapter pack (manifest + service-account auth)"
```

Expected: ≥3 tests pass (1 pack-load canary + 2 auth tests).

### Commit 3 — Gmail adapter ingest + cursor + error path

**Files:** Update `packs/adapters/messaging/gmail/adapter.py` (replace `NotImplementedError` stubs with full history-poll + messages.get + cursor-advance + defensive-error-catch). Recorded fixture(s) at `packs/adapters/messaging/gmail/tests/fixtures/`. Update `packs/adapters/messaging/gmail/tests/test_gmail_adapter_pack_internal.py` to add ingest tests.

**Files-to-read:** 4 (adapter.py from c2, gmail-v1.ts targeted grep for `users.history`/`users.messages` types, ingest.py for `MessagingReceivedV1` shape, morning_digest handler.py for defensive-catch pattern).

**Verification:**
```
poetry run mypy packs/adapters/messaging/gmail/ 2>&1 | tail -5
poetry run pytest packs/adapters/messaging/gmail/tests/ -v
git add packs/adapters/messaging/gmail/
git commit -m "phase 11a commit 3: Gmail ingest + cursor + error path"
```

Expected: ≥4 additional tests pass (ingest with fixture, cursor milestone advance, error emission, full happy path).

### Commit 4 — Integration + BUILD_LOG + push

**Files:** `tests/integration/test_adapter_supervisor_gmail.py` (NEW), `docs/build_log.md` (append).

**Files-to-read:** 3 (existing `test_pipeline_10c_iii_integration.py` for integration-shape template; the new Gmail adapter from c3 for under-test surface; build_log tail for append point).

**Build_log Evidence template:** Append AFTER prompt 10c-iii entry under `## Build prompts`. Header: `### Prompt 11a — adapter foundation + Gmail`. Mirror the 10c-iii Evidence-section density. Cite: 3 new event types at v1 in `adapters.py`; the Adapter Protocol contract; the supervisor's restart-with-backoff pattern; the cleanup of 9 pre-scaffold subdirs; carry-forwards for 11b/11c/11d (clone-template paths); soft-watch entries for the deferred `bootstrap/adapter_install_order.yaml` (prompt 16) and Pub/Sub push-notification receiver (future sidecar).

**Verification:**
```
poetry run ruff check adminme/ packs/ tests/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_adapter_supervisor_gmail.py
git commit -m "phase 11a commit 4: integration test + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean on `adminme/`/`packs/`/`tests/`; mypy clean; suite >=+18; `verify_invariants.sh` exit 0.

**PR open:** Title `phase 11a: adapter foundation + Gmail adapter`. Body: top line "First sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`. Establishes the adapter foundation (Protocol + supervisor + 3 cross-cutting schemas) and ships Gmail as the worked example. 11b (Plaid), 11c (Apple Reminders), 11d (Calendars) clone this pattern." Then BUILD_LOG `Evidence` body. After PR opens, run one round of `mcp__github__pull_request_read` (status + reviews + comments). Report. **Stop.** Do not poll.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies:
- c1 (protocol + 3 schemas + supervisor + cleanup) branches from `main`. Self-contained foundation; ships first.
- c2 (Gmail manifest + auth) branches from c1's branch. Depends on the `Adapter` Protocol from c1.
- c3 (Gmail ingest + cursor + error path) branches from c2's branch. Depends on c2's auth scaffolding.
- c4 (integration + BUILD_LOG) branches from c3's branch. Depends on all three preceding c's.

If the harness re-assigns the branch name (per universal preamble), accept and continue; the dependency order is preserved by sequential PR opens.

**Critical operator note (from `06-split-memo-11.md` §6):** when merging the four c-band PRs into `main`, change each PR's `base` to `main` before clicking Merge. Otherwise the merge goes into the previous c-band's branch (the stacked-merge anti-pattern that bit 10c-iii). PR #80 has a deferred workflow change ("Option B") that would automate this; if not yet shipped, the manual step is required.

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥18 new tests passing; ruff clean on `adminme/`/`packs/`/`tests/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`.

Cross-cutting [§N] / [DN] checks (enforced by `verify_invariants.sh`):

- [§7.3, D17]: zero projection writes from any adapter or supervisor (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' adminme/adapters/ adminme/daemons/ packs/adapters/` returns 0 matches — adapters write to event log only, no direct projection writes).
- [§8.2, D6]: no LLM SDK import anywhere under `packs/adapters/` or `adminme/adapters/` or `adminme/daemons/` (`grep -rEn '^(from|import)\s+(anthropic|openai)' packs/adapters/ adminme/adapters/ adminme/daemons/` returns 0 matches).
- [§8 invariant 3]: no adapter calls a messaging-channel transport (Telegram client, BlueBubbles, etc.) — adapters emit events; OpenClaw plugins own outbound transport. Verify by reading adapter.py imports.
- [§6 invariant 10]: sensitivity_floor cannot be lowered from `privileged` to `normal`. Test asserts. Adapter config loader checks.
- [D7]: three new event types register at v1 in `adminme/events/schemas/adapters.py`; `registry.latest_version("adapter.<*>")` returns 1 for each.
- [D8]: `correlation_id` set on entry if absent. Adapter base.py `AdapterContext.correlation_id_factory` callable provides it.
- [§Causation]: every emitted event from an adapter carries `correlation_id`; `causation_id` is set to the triggering external-poll tick (the adapter's own internal poll timer) — distinct from pipeline-causation since adapters are not pipeline-triggered.
- F-3 (10c-iii): pack-internal pack-load test uses unique basename (`test_gmail_adapter_pack_internal.py`).

---

## §10 Stop

Adapter foundation ready. Gmail adapter operational against sandbox. Three new event types at v1. Supervisor spawns + restarts adapter tasks. Pre-scaffold cleanup removes dead code. Ready for prompt 11b (Plaid adapter), which clones this foundation's pack pattern.
