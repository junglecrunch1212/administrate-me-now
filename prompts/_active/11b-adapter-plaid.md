# Prompt 11b: Plaid adapter (financial inbound)

**Phase:** Phase A — L1 ingest adapters, second sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`.

**Branch:** `claude/11b-<auto>` (harness-assigned). PR base = `main`. STACKED across c1→c4 per §8.5.

**Depends on:** **11a c1-c4 merged to main.** 11a establishes the `Adapter` Protocol at `adminme/adapters/base.py`, the 3 cross-cutting schemas at `adminme/events/schemas/adapters.py`, the `adapter_supervisor.py` daemon, and the Gmail adapter pack at `packs/adapters/messaging/gmail/` that 11b clones for shape. Without 11a fully on main, 11b's executor cycle fails fast at "Verify pack template exists."

**Quality bar:** clones `packs/adapters/messaging/gmail/` pack shape with Plaid-specific substitutions. Mirrors Gmail's auth → cursor → ingest → emit flow but with Plaid's `/transactions/sync` semantics and webhook-driven invalidation pattern.

**Stop condition:** Plaid adapter pack (`packs/adapters/financial/plaid/`) implements the `Adapter` Protocol; authenticates against the Plaid sandbox via Link token flow + Item access token; performs `/transactions/sync` cursor-based pagination; handles webhooks via a Funnel endpoint receiver; tracks institution health and emits `adapter.error` (`severity: fatal`) on `ITEM_LOGIN_REQUIRED` for the operator to surface in the inbox. One new event type (`money_flow.plaid_synced`) registers at v1 in a new `adminme/events/schemas/financial.py` module. `config/channels.yaml` extended with a Plaid entry alongside Gmail. ≥18 new tests across the four commits. `scripts/verify_invariants.sh` exit 0.

**Estimated duration:** one Claude Code session, ~75-90 minutes wall clock, 4 commits. ~$80-120 API spend per split memo §2.

---

## §1 Behavioral grounding

You are building the **second L1 standalone adapter**, following the foundation that 11a established. The pattern is the same — implement the `Adapter` Protocol, emit typed events into the event log, never write projections, never call pipelines, never compose outbound. The substantive differences from Gmail (11a's worked example) are domain-shaped: Plaid is a financial-data adapter, so its sensitivity floor is `privileged` per `[§6 invariant 10]`; its auth model is Link-token-then-Item-access-token (not OAuth2 service-account); its primary ingest endpoint is webhook-driven (`/webhook` notifications trigger `/transactions/sync` calls) rather than continuous polling; and its cursor lives at the Plaid Item level (one cursor per institution per member).

**Sensitivity floor: `privileged`** [§6 invariant 10, D11]. Plaid handles bank account data. The pack's `config.schema.json` declares `sensitivity_floor: "privileged"` as a constant; the config loader rejects any member-level override that would try to set it to `normal`. Adapter base.py's Gate-4-equivalent runtime check confirms `sensitivity_floor != "normal"` before the start() call returns success.

**Auth model: Link token + Item access token** [BUILD.md PLAID DETAILED SPEC, docs/reference/plaid/_index.md §"Auth flow"]. Plaid uses a two-stage handshake: the operator (or wizard) creates a Link Token via `/link/token/create` and exchanges the user-flow result for an Item access token via `/item/public_token/exchange`. The Item access token is the long-lived secret stored in 1Password and referenced via `op://<vault>/<item>/<field>` per the `AdapterAuthenticatedV1` schema's `token_storage_ref` pattern. The Link flow itself is operator-mediated (prompt 16 wizard wires it); 11b's adapter only consumes the resulting access token. Tests mock the Plaid client; no live Plaid sandbox call is required to merge 11b.

**Webhook-driven invalidation** [BUILD.md PLAID DETAILED SPEC §"Webhooks", docs/reference/plaid/link-update-mode.md]. Plaid emits webhooks to a Funnel endpoint when transactions are available or when an Item requires re-authentication. The adapter's `webhook_handler` route receives these, verifies the JWT signature per `docs/reference/plaid/_index.md`, and either (a) enqueues a `/transactions/sync` cycle if `webhook_code == SYNC_UPDATES_AVAILABLE`, or (b) emits `adapter.error` `severity: fatal` `error_kind: auth_failed` if `webhook_code == ITEM_LOGIN_REQUIRED` (operator must re-Link via prompt 16's wizard).

**Cursor: per-Item per-member** [BUILD.md PLAID DETAILED SPEC §"Sync cursor", docs/reference/plaid/python-sdk-README.md]. Plaid's `/transactions/sync` returns a `next_cursor`. The adapter persists one cursor per `(member_id, plaid_item_id)` tuple. Cursor advance emission per the established milestone discipline from 11a — every 100 transactions OR every 5 minutes per Item, whichever first.

**Anti-vision halt** [VISION §"Privacy by default", DRIFT-CONTROL Job 4]. If 11b's default scan window or per-tick fetch count surfaces high-volume transaction noise (e.g., every Plaid transaction emits a separate event with full account number / card details visible to the household's inbox), stop and report. The pattern is: emit a single `money_flow.plaid_synced` per sync cycle carrying `(item_id, transactions_added_count, transactions_modified_count, transactions_removed_count)` at the SUMMARY level; individual transaction details land in the `money_flow` projection (out of scope for 11b) via the existing event consumers.

**Mode:** Phase A. No live Plaid API calls during tests; recorded fixtures + mocked Plaid client. No `anthropic` / `openai` imports anywhere under `packs/adapters/`. The 2 MB `docs/reference/plaid/python-sdk-plaid_api.py` and 2.9 MB `docs/reference/plaid/openapi.yaml` are reference sources; the Executor greps them for specific endpoint signatures (do NOT full-read; use `sed -n` with section anchors).

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md` / `REFERENCE_EXAMPLES.md` ranges; do NOT full-read those files. The Plaid Python SDK file is 2 MB and `openapi.yaml` is 2.9 MB — grep for specific endpoint signatures only.

1. **`docs/06-split-memo-11.md`** — full read. The split memo defines 11b's scope (Plaid adapter only, per main's canonical sequence per Option C disposition). Read §2 (scope table for 11b), §3 (dependency on 11a), §5 (constitutional citations).
2. **`docs/07-deferred-adapter-framework-expansion.md`** — full read. Context: the workspace pinned `PROMPT_SEQUENCE.md` described 11b as "Cat-B adapters" (multi-adapter, Apple Reminders + Apple Calendar + Google Calendar + Google Tasks). Operator chose Option C: main is canon (simple Plaid-only 11b). This memo preserves the alternative for future revisit. Cite as `[deferred-memo §When to revisit]` if a Cat-B-style decision surfaces during c1 grounding.
3. **`adminme/adapters/base.py`** — full read. The `Adapter` Protocol from 11a c1. Confirm `name`, `family`, `version`, `sensitivity_floor`, `start(ctx)`, `stop()`, `status()` shape. `AdapterContext` and `AdapterStatus` dataclasses.
4. **`adminme/events/schemas/adapters.py`** — full read. The 3 cross-cutting schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) from 11a c1. The Plaid adapter emits all three.
5. **`packs/adapters/messaging/gmail/pack.yaml`** — full read. The Gmail manifest from 11a c2 is the clone template for `packs/adapters/financial/plaid/pack.yaml`. Substitutions: `kind: adapter` unchanged; `family: messaging` → `family: financial`; `triggers.kind: external_poll` → `triggers.kind: webhook_driven`; events emitted include `money_flow.plaid_synced` plus the 3 cross-cutting adapter events.
6. **`packs/adapters/messaging/gmail/adapter.py`** — full read. The Gmail adapter implementation from 11a c2-c3 is the structural reference. Substitutions: Gmail OAuth2 service-account → Plaid Link/Item flow; `users.history.list` polling → webhook-driven `/transactions/sync`; `messaging.received` emit → `money_flow.plaid_synced` emit.
7. **`packs/adapters/messaging/gmail/config.schema.json`** — full read. Config-schema reference for the new `packs/adapters/financial/plaid/config.schema.json`. Substitutions per `[§6 invariant 10]`: `sensitivity_floor` constant `"privileged"` (not adjustable from `"normal"`).
8. **`adminme/daemons/adapter_supervisor.py`** — full read. The supervisor from 11a c1 will discover the Plaid pack at startup and register it; confirm the discovery pattern handles `packs/adapters/<family>/<name>/` for any `<family>` (not just `messaging`).
9. **`config/channels.yaml`** — full read. The Gmail-only example from 11a c1. Extend (or create a new sibling entry) with a Plaid adapter entry.
10. **`ADMINISTRATEME_BUILD.md`** section **"PLAID — DETAILED SPEC"** — targeted via `grep -n '^### PLAID' ADMINISTRATEME_BUILD.md` first to find line number, then `sed -n '<start>,<+100>p'`. Read for: Link flow + Item access pattern, webhook-code taxonomy (`SYNC_UPDATES_AVAILABLE`, `ITEM_LOGIN_REQUIRED`), cursor semantics, error-handling escalation, sensitivity floor `privileged`.
11. **`docs/reference/plaid/_index.md`** — full read (~3 KB). The quickstart + section index.
12. **`docs/reference/plaid/python-sdk-README.md`** — full read (~12 KB). Python SDK installation + auth + common-call patterns. The adapter uses `plaid-python`.
13. **`docs/reference/plaid/institutions-api.md`** — full read (~14 KB). Institution-health endpoints (`/institutions/get_by_id`, `/institutions/status/get`) used for the health-tracking deliverable.
14. **`docs/reference/plaid/link-update-mode.md`** — full read (~27 KB). Update-mode flow for re-authentication on `ITEM_LOGIN_REQUIRED`.
15. **`docs/reference/plaid/errors-taxonomy.md`** — full read (~13 KB). Error code → severity mapping used in the adapter's `adapter.error` emission. The 7-tuple of exception types from `compose_morning_digest` (10c-ii) is the catch pattern.
16. **`docs/reference/plaid/python-sdk-plaid_api.py`** — DO NOT full-read (2 MB). Use `grep -nE '(TransactionsSync|LinkTokenCreate|ItemPublicTokenExchange|InstitutionsGetById|InstitutionsStatusGet)' docs/reference/plaid/python-sdk-plaid_api.py` to find specific call signatures, then `sed -n '<line>,<+30>p'`. Cite line ranges in the adapter's docstring.
17. **`docs/reference/plaid/openapi.yaml`** — DO NOT full-read (2.9 MB). Use `grep -nE '(/transactions/sync|/link/token/create|/webhook_verification_key/get)' docs/reference/plaid/openapi.yaml` for the request/response shapes referenced.
18. **`docs/SYSTEM_INVARIANTS.md`** — targeted reads: §1 (event log append-only), §6 invariant 10 (sensitivity floor), §7.3 (no projection direct writes), §8 (no LLM SDKs).
19. **`docs/DECISIONS.md`** — read each: D6 (no LLM SDK), D7 (schemas at v1), D8 (correlation_id propagation), D11 (privileged sensitivity for financial / law adapters), D17 (adapters don't write projections).
20. **`docs/build_log.md`** — read **Prompt 11a entry** end-to-end. Carry-forwards into 11b. Particularly: the F-3 pack-internal-test rename pattern, the `:8081` supervisor `/status` port, the milestone-only cursor-emission discipline.

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad note confirming: (a) the Plaid adapter implements `Adapter` Protocol exactly per `base.py`; (b) `sensitivity_floor = "privileged"` is a constant, not config-overrideable; (c) webhook-driven invalidation triggers `/transactions/sync` (not the other way around); (d) `money_flow.plaid_synced` is the single SUMMARY event per sync cycle (not per transaction); (e) cursor lives at `(member_id, plaid_item_id)` granularity with milestone-only `adapter.cursor_advanced` emission; (f) F-3 pack-internal-test rename applies to the new pack.

---

## §3 Reconciliation notes (carry-forwards from 11a merge)

- **F-3 HARD (10c-iii, reinforced by 11a):** pack-internal pack-load test uses unique basename. Pattern: `packs/adapters/financial/plaid/tests/test_plaid_adapter_pack_internal.py`. Single instance for this pack.
- **PM-27 SOFT (graduated to convention):** Plaid adapter's optional loaders (institution-health-fetcher, webhook-jwt-verifier) take callable injection with no-op defaults so Phase A tests substitute fakes.
- **Stacked-merge anti-pattern (operator carry-forward from 10c-iii + 11a recovery):** when merging 11b's c-band PRs, change each PR's `base` to `main` before clicking Merge. If the executor-cycle workflow has been updated to use `base=main` for all c-bands (recommended sidecar PR after 11a stabilizes), this step goes away.
- **Constitution-sync rule (NEW from PR #97):** this PR adds a new module `adminme/events/schemas/financial.py` and a new event type `money_flow.plaid_synced`. NOT a constitutional doc change — the rule doesn't fire. CI check should pass without an exemption block.
- **Surface drift carry-forward (Gate 0):** workspace pinned `PROMPT_SEQUENCE.md` still shows the expanded 11b "Cat-B adapters" scope. Per operator Option C disposition, main is canon. Operator's standing decision applies to subsequent prompts in the 11 cohort too (11c, 11d). If this changes, restart Gate 0 disposition.

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `adminme/adapters/base.py` has the full `Adapter` Protocol (post-11a-c1 merge). Verified at authorship time (2026-05-16); confirm at c1 start.

A2. `adminme/events/schemas/adapters.py` registers the 3 cross-cutting schemas at v1. Verified at authorship.

A3. `packs/adapters/messaging/gmail/` directory and its 5 files (pack.yaml, adapter.py, config.schema.json, config.example.yaml, tests/test_gmail_adapter_pack_internal.py) exist on main. **DEPENDENCY:** if not yet merged from 11a c2-c4 PRs, halt c1 and report — 11b cannot proceed without 11a's Gmail pack as the clone template.

A4. `config/channels.yaml` exists with the Gmail adapter entry. If absent, c1's first deliverable extends/creates it.

A5. `adminme/daemons/adapter_supervisor.py` exists (from 11a c1). The supervisor's pack-discovery mechanism handles `packs/adapters/<family>/<name>/` for arbitrary `<family>`. Confirm at c1 start.

A6. No prior `adminme/events/schemas/financial.py` module exists. The Plaid adapter introduces it. Verify with `mcp__github__get_file_contents(path="adminme/events/schemas/financial.py", ref="main")` returning 404. If it exists already, halt and reconcile — the scope assumption changes.

A7. The `docs/reference/plaid/openapi.yaml` file's relevant endpoint paths (`/transactions/sync`, `/link/token/create`, `/item/public_token/exchange`, `/institutions/status/get`, `/webhook_verification_key/get`) all resolve to documented schemas. Verify via the grep pattern in §2.17 before authoring adapter code.

---

## §5 Objective

Ship the Plaid adapter pack at `packs/adapters/financial/plaid/`, a new `adminme/events/schemas/financial.py` module registering `money_flow.plaid_synced` at v1, an extension to `config/channels.yaml` with a Plaid entry, and ≥18 new tests across the four commits. The pack clones the Gmail adapter's structural pattern from 11a with Plaid-specific substitutions: Link/Item auth model, webhook-driven cursor sync, institution health tracking, `sensitivity_floor: privileged`. Establishes the financial-adapter family for future entries (none currently planned, but the directory structure is set).

---

## §6 Out of scope

- **Apple Reminders / Apple Calendar / Google Calendar / Google Tasks adapters** — these are 11c/11d in the simple sequence on main. (Workspace's expanded "Cat-B adapters" framing of 11b is the deferred-vision alternative per `docs/07-deferred-adapter-framework-expansion.md`; if/when revisited, the Plaid adapter shipped here remains as Cat-C in that taxonomy.)
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12.
- **`money_flow` projection** — Plaid adapter EMITS `money_flow.plaid_synced` events. The projection that consumes them and materializes transaction state ships later (per the `money` projection slice of 07a + future prompts). For 11b, only the event-emit half is in scope.
- **Live Plaid sandbox / production credentials** — tests use recorded fixtures + mocked Plaid client. Lab-mode integration test against a real Plaid sandbox is OPTIONAL (skipped in CI; manual operator-run, deferred to Phase B smoke test in prompt 19).
- **Bootstrap-wizard Link flow integration** — prompt 16 (`16-bootstrap-wizard.md` §"Plaid pairing") wires the operator-mediated Link flow at install time. Out of scope here; 11b consumes the Item access token via `config/channels.yaml` 1Password reference.
- **`bootstrap/adapter_install_order.yaml`** — deferred to prompt 16 per the 11a carry-forward. Plaid adapter pack is NOT added to `bootstrap/pack_install_order.yaml` (that's skill packs).
- **Webhook receiver as a separate FastAPI service** — the Plaid webhook handler lives INSIDE the adapter (handled by the adapter supervisor's webhook-route registration). Standalone webhook services are out of scope.
- **Multi-institution per member at scale** — the cursor design supports `(member_id, plaid_item_id)` tuples, but UI surfaces for institution management ship later (prompt 14b CRM/Finance view).
- **PII redaction / log-scrubbing for Plaid responses** — adapter emits the `money_flow.plaid_synced` SUMMARY event only; full transaction details flow into the `money` projection where downstream privileged-filter logic handles redaction per `[§6.9, D11]`.

---

## §7 Deliverables

### New event type (Pydantic model in NEW module `adminme/events/schemas/financial.py`)

| Type | Class | Required fields | Notes |
|---|---|---|---|
| `money_flow.plaid_synced` | `MoneyFlowPlaidSyncedV1` | `member_id: str`, `plaid_item_id: str`, `institution_id: str`, `institution_name: str`, `sync_cursor: str`, `transactions_added: int` (≥0), `transactions_modified: int` (≥0), `transactions_removed: int` (≥0), `accounts_modified_count: int` (≥0), `synced_at: str` (datetime), `webhook_triggered: bool` | One event per sync cycle (NOT per transaction). `webhook_triggered=True` means the sync was triggered by a webhook (`SYNC_UPDATES_AVAILABLE`); `False` means a periodic poll. All count fields ≥0; zero is valid. |

`model_config = {"extra": "forbid"}`. `registry.register("money_flow.plaid_synced", 1, MoneyFlowPlaidSyncedV1)` at module end.

### Plaid adapter pack at `packs/adapters/financial/plaid/`

- **`pack.yaml`** — manifest. `kind: adapter`, `family: financial`, `triggers: {kind: webhook_driven, schedule: "0 4 * * *"}` (the schedule is a backstop daily-poll if no webhooks arrived; webhook is primary trigger), `depends_on.event_types: [adapter.error, adapter.authenticated, adapter.cursor_advanced, money_flow.plaid_synced]`, `config.schema: config.schema.json`.

- **`adapter.py`** — implements `Adapter` Protocol. ~280 lines.
  - `__init__(*, plaid_client_factory=<no-op default>, webhook_verifier_factory=<no-op default>)` per PM-27.
  - `start(ctx)`: load Item access token from `op://` reference; instantiate Plaid client; emit `adapter.authenticated`; register webhook route via `ctx.config["webhook_route"]`; begin backstop poll loop (daily).
  - `webhook_handler(payload)`: verify JWT signature via `webhook_verifier_factory`; on `SYNC_UPDATES_AVAILABLE`, enqueue `_sync_transactions(item_id)`; on `ITEM_LOGIN_REQUIRED`, emit `adapter.error` `severity: fatal`.
  - `_sync_transactions(item_id)`: call `/transactions/sync` with persisted cursor; receive `added`/`modified`/`removed`/`next_cursor`; persist new cursor; emit `money_flow.plaid_synced` with counts; emit `adapter.cursor_advanced` on milestone.
  - `_fetch_institution_health(institution_id)`: call `/institutions/status/get`; if any non-HEALTHY status, emit `adapter.error` `severity: transient` `error_kind: api_changed`.
  - Defensive error catch on the same 7-tuple pattern from `compose_morning_digest` (TransientError, RateLimitError, NetworkError, APIChangedError, QuotaExceededError, AuthError, UnknownError per Plaid SDK exception hierarchy).
  - `stop()`: cancel pending sync tasks; release HTTP session.
  - `status()`: return current `AdapterStatus` snapshot.
  - Class attributes: `name = "financial-plaid"`, `family = "financial"`, `version = "1.0.0"`, `sensitivity_floor = "privileged"`.

- **`config.schema.json`** — JSON schema. Required: `item_access_token_ref` (1Password ref pattern), `plaid_client_id_ref`, `plaid_secret_ref`, `webhook_route` (e.g., `"/webhooks/plaid"`), `member_id`, `plaid_item_id`, `institution_id`. Optional: `dedupe_window_minutes` (default 90), `backstop_poll_cron` (default `"0 4 * * *"`). **`sensitivity_floor` is a constant `"privileged"`**, NOT user-settable; the schema's `"sensitivity_floor"` property has `"const": "privileged"`.

- **`config.example.yaml`** — example with placeholder 1Password references.

- **`tests/test_plaid_adapter_pack_internal.py`** — pack-load canary (F-3 rename) + 4 handler-direct tests: (1) auth-stub success path with mocked Plaid client; (2) `/transactions/sync` happy-path with recorded fixture; (3) webhook `SYNC_UPDATES_AVAILABLE` triggers sync; (4) `sensitivity_floor` cannot be lowered via config. ≥5 tests total.

### Extension to `config/channels.yaml`

Append a Plaid entry alongside the Gmail entry from 11a c1:
```yaml
plaid_financial:
  adapter_pack: "packs/adapters/financial/plaid"
  config_ref: "op://vault/plaid-config/config"
  enabled: true
```

### Tests (total ≥18 across the four commits)

- Unit (c1): 4 schema-registration canaries (`money_flow.plaid_synced` registers at v1, accepts required fields, rejects extra keys, count fields validate ≥0).
- Unit (c2 Plaid auth): 3 auth tests (Link/Item flow mocked, sensitivity_floor `"privileged"` enforced at config-load time, `adapter.authenticated` event emission with `op://` reference shape per the existing schema).
- Unit (c3 Plaid sync): 6 ingest tests (cursor-based sync with fixture, webhook handler dispatches to sync, `ITEM_LOGIN_REQUIRED` emits `adapter.error` fatal, cursor advance at milestone, `money_flow.plaid_synced` emit shape, defensive error catch on TransientError).
- Unit (c4 institution health): 2 tests (institution-status query, non-HEALTHY status emits `adapter.error` transient).
- Pack-internal (c4): 1 pack-load canary (F-3 rename).
- Integration (c4): 2 round-trips — (a) supervisor starts → spawns Plaid adapter → webhook arrives → sync fires → `money_flow.plaid_synced` in log; (b) `ITEM_LOGIN_REQUIRED` path emits `adapter.error` and supervisor preserves backoff state. `@pytest.mark.skip_in_ci` since both touch supervisor lifecycle.

Floor: ≥18 new tests across 11b. Overshoot welcome.

---

## §8 Commit structure (exactly four commits; messages exact)

### Commit 1 — Schema + program + config foundation

**Files:** `adminme/events/schemas/financial.py` (NEW), `tests/unit/events/test_financial_schemas.py` (NEW), `config/channels.yaml` (extend), `packs/adapters/financial/plaid/pack.yaml` (NEW), `packs/adapters/financial/plaid/config.schema.json` (NEW), `packs/adapters/financial/plaid/config.example.yaml` (NEW).

**Files-to-read:** 6 (`adapters.py` for schema template, `gmail/pack.yaml` for manifest template, `gmail/config.schema.json` for config template, BUILD.md PLAID DETAILED SPEC, plaid/_index.md, the split memo §2).

**Verification:**
```
poetry run python -c "from adminme.events.schemas.financial import MoneyFlowPlaidSyncedV1; print('OK')"
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/events/test_financial_schemas.py -v
grep -F 'sensitivity_floor' packs/adapters/financial/plaid/config.schema.json | grep -F '"const": "privileged"'
git add adminme/events/schemas/financial.py tests/unit/events/test_financial_schemas.py config/channels.yaml packs/adapters/financial/plaid/pack.yaml packs/adapters/financial/plaid/config.schema.json packs/adapters/financial/plaid/config.example.yaml
git commit -m "phase 11b commit 1: money_flow.plaid_synced schema + pack manifest + privileged sensitivity floor"
```

Expected: mypy clean; ≥4 schema tests pass; grep confirms `sensitivity_floor` is a constant in the JSON schema.

### Commit 2 — Plaid Link/auth + institution management

**Files:** `packs/adapters/financial/plaid/adapter.py` (NEW; auth path only — sync/webhook stubs raise `NotImplementedError`), `packs/adapters/financial/plaid/tests/test_plaid_adapter_pack_internal.py` (NEW, F-3 rename; pack-load canary + auth tests).

**Files-to-read:** 5 (Gmail adapter.py from c1, base.py for Protocol, adapters.py for `AdapterAuthenticatedV1`, plaid/python-sdk-README.md, plaid/_index.md auth section).

**Verification:**
```
poetry run mypy packs/adapters/financial/plaid/ 2>&1 | tail -5
poetry run pytest packs/adapters/financial/plaid/tests/ -v
git add packs/adapters/financial/plaid/adapter.py packs/adapters/financial/plaid/tests/test_plaid_adapter_pack_internal.py
git commit -m "phase 11b commit 2: Plaid Link/Item auth + sensitivity_floor enforcement"
```

Expected: ≥4 tests pass (pack-load canary + 3 auth tests).

### Commit 3 — /transactions/sync + webhook handler

**Files:** Update `packs/adapters/financial/plaid/adapter.py` (replace NotImplementedError stubs with full `_sync_transactions` + `webhook_handler` + cursor persistence + defensive error catch). Add recorded fixtures at `packs/adapters/financial/plaid/tests/fixtures/`. Add ingest tests to the existing test file.

**Files-to-read:** 6 (adapter.py from c2, plaid/python-sdk-plaid_api.py targeted grep for TransactionsSync, plaid/openapi.yaml targeted grep for /transactions/sync, plaid/link-update-mode.md for ITEM_LOGIN_REQUIRED path, plaid/errors-taxonomy.md, Gmail adapter.py from c3 for defensive-catch pattern).

**Verification:**
```
poetry run mypy packs/adapters/financial/plaid/ 2>&1 | tail -5
poetry run pytest packs/adapters/financial/plaid/tests/ -v
git add packs/adapters/financial/plaid/adapter.py packs/adapters/financial/plaid/tests/
git commit -m "phase 11b commit 3: /transactions/sync + webhook handler + cursor"
```

Expected: ≥6 additional tests pass.

### Commit 4 — Institution health + integration + BUILD_LOG

**Files:** Update `packs/adapters/financial/plaid/adapter.py` with `_fetch_institution_health` method + invocation from start() + daily backstop poll. Add 2 institution-health tests. Add `tests/integration/test_adapter_plaid_supervisor.py` (2 round-trips, `@pytest.mark.skip_in_ci`). Append to `docs/build_log.md`.

**Files-to-read:** 3 (plaid/institutions-api.md for endpoint signatures, existing `tests/integration/test_adapter_supervisor_gmail.py` from 11a c4 for integration template, build_log tail for append point).

**Build_log Evidence template:** Append AFTER 11a entry. Header: `### Prompt 11b — Plaid adapter (financial inbound)`. Mirror the 11a Evidence-section density. Cite: 1 new event type at v1 (`money_flow.plaid_synced`); the privileged-sensitivity-floor enforcement; the webhook-driven cursor-sync pattern; the institution-health backstop; carry-forwards for 11c/11d (clone-template paths remain the Gmail pack from 11a; the Plaid pack is the second worked example); soft-watch entries for the deferred `money_flow` projection (consumer of the events 11b emits) and the deferred `bootstrap/adapter_install_order.yaml` (prompt 16).

**Verification:**
```
poetry run ruff check adminme/ packs/ tests/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_adapter_plaid_supervisor.py packs/adapters/financial/plaid/
git commit -m "phase 11b commit 4: institution health + integration test + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean; mypy clean; suite ≥+18; `verify_invariants.sh` exit 0.

**PR open:** Title `phase 11b: Plaid adapter (financial inbound)`. Body: top line "Second sub-prompt of the prompt-11 Tier C split per `docs/06-split-memo-11.md`. Ships Plaid as the financial-family worked example, cloning 11a's Gmail-adapter-pack pattern with Plaid-specific substitutions: Link/Item auth, webhook-driven cursor sync, institution health tracking, `sensitivity_floor: privileged`." Then BUILD_LOG `Evidence` body.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies: c1 → c2 → c3 → c4. Each c-band's branch is created from the prior c-band's tip. Each PR's `base` should be `main` (per the 11a-recovery lesson — avoids the stacked-merge anti-pattern); the executor-cycle workflow handles this via base-arg in `gh pr create`. If the workflow hasn't been updated to default base=main for all c-bands by the time 11b's executor cycle fires, the operator manually changes each PR's base to `main` before merging.

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; ≥18 new tests passing; ruff clean on `adminme/` / `packs/` / `tests/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`.

Cross-cutting [§N] / [DN] checks (enforced by `verify_invariants.sh`):

- [§7.3, D17]: zero projection writes from the Plaid adapter (`grep -rEn 'INSERT INTO|UPDATE|DELETE FROM' packs/adapters/financial/plaid/` returns 0 matches).
- [§8.2, D6]: no LLM SDK import anywhere under `packs/adapters/financial/`.
- [§6 invariant 10, D11]: `sensitivity_floor` is `"privileged"` and cannot be lowered. Test asserts; config-schema `const` enforces.
- [D7]: `money_flow.plaid_synced` registers at v1; `registry.latest_version("money_flow.plaid_synced")` returns 1.
- [D8]: `correlation_id` set on entry. `AdapterContext.correlation_id_factory` provides it.
- [Causation]: every emitted `money_flow.plaid_synced` event carries `correlation_id`; `causation_id` is set to either the inbound webhook event_id (when webhook-triggered) or the supervisor's backstop-poll tick event_id (when scheduled).
- F-3 (10c-iii): pack-internal pack-load test uses unique basename (`test_plaid_adapter_pack_internal.py`).

---

## §10 Stop

Plaid adapter operational against the Plaid sandbox (mocked in tests; real-Plaid integration optional for lab-mode). One new event type at v1 in a new `financial.py` schema module. Privileged sensitivity floor enforced. Webhook-driven cursor sync working. Institution health tracking emits `adapter.error` on degradation. Ready for prompt 11c (Apple Reminders bidirectional — though per Option C disposition, this is the simple-sequence 11c; the deferred Cat-B framework alternative remains preserved in `docs/07-deferred-adapter-framework-expansion.md`).
