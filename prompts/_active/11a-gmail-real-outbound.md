# Prompt 11a: Gmail real outbound + messaging.sent schema (Cohort D step 1)

**Phase:** L1 / Cohort D step 1 — first canonical-build prompt post-Cohort-C-closure per `prompts/PROMPT_SEQUENCE.md` row 116 + `docs/08-architecture-amendment-adapter-rebase.md` §3.4. Parent draft: `prompts/11a-gmail-plaid-central.md` (PR #151, merged at main `0493a683`).
**Branch:** `claude/11a-c<N>` per c-band (Executor's harness assigns)
**Stop condition:** Gmail's `send` is a real Gmail API v1 `users.messages.send` implementation (replaces 11r-a's stub), routes through `adminme.lib.observation.outbound` when seam slots populated per the 11r-c Phase-A-conditional precedent, emits `messaging.sent@v1` on success, and `adminme adapters validate packs/adapters/messaging/gmail` continues to exit 0 with NO `advisory_pending_retrofit`.
**Depends-on:** Cohort B closed (PRs #118-#121 / #123-#126 / #128-#131); Cohort C closed (PRs #133-#136 / #139-#142 / #145-#148 + sidecar #150); 11a parent draft (PR #151)
**Quality-bar reference:** `prompts/_active/11r-c-retrofit-reminders-to-external-state-mirror-adapter.md` (PR #144, executor PRs #145-#148). 11r-c established the Phase-A-conditional observation-wrap pattern via three seam slots (`_observation_session` / `_observation_manager` / `_observation_event_log`). 11a applies the same pattern to Cat-A `send`. Test floor and §2.9 limits mirror 11r-c (246-line parent, ~300-line c2 ceiling).
**Estimated duration:** c1 ~30-40 min · c2 ~60-90 min · c3 ~45-60 min · c4 ~45-60 min · total ~3-4 hrs wall / ~$60-90 spend

---

## §1 Behavioral grounding

Canonical 11a is the **first non-retrofit Cohort D prompt** — the pattern shift from "inheritance + manifest only" (Cohort C) to "full event-sourcing build" (Cohort D). The scope is narrow because the Cohort C retrofits already brought Gmail (11r-a) and Plaid (11r-b) onto their typed ABCs. What 11a adds is the **real `send` implementation** that 11r-a deferred per its anti-vision halt (memo 08 §5: "inheritance + manifest only, not a rewrite"). This is the **template-setting work** that 11b Cat-B AppleScript writeback + 11g Cat-E HA service-call will follow.

Three load-bearing items this prompt establishes:

1. **Phase-A-conditional observation wrap on `send`** — the seam-slot pattern 11r-c established for Cat-B's `_apply_write_impl` carries directly to Cat-A's `send`. Adapter declares optional `_observation_session: Session | None`, `_observation_manager: ObservationManager | None`, `_observation_event_log: EventLog | None` slots populated by `start(ctx)` if `ctx` carries them. When populated, `send` routes the Gmail API call through `outbound(session, action="messaging.send", payload=..., action_fn=lambda: ...)` per `adminme.lib.observation.outbound`'s actual signature (positional `session/action/payload/action_fn` + keyword-only `manager/event_log/target_channel/target_identifier`). When NOT populated (Phase A test default), `send` calls the Gmail API directly to preserve byte-for-byte production behavior per memo 08 §5.

2. **`messaging.sent@v1` schema (ADDITIVE)** — `messaging.received@v1` is immutable per [D7]. The new `messaging.sent@v1` is a separate schema with payload `{member_id, recipient_email, subject, body_preview, sent_at, gmail_message_id, gmail_thread_id, correlation_id}`. Registered at v1 at module import time per [D7]. Body preview truncated to 200 chars per per-item-privacy heuristic (matches `reminder.list_item.synced`'s `notes` truncation pattern).

3. **MIME construction + Gmail API call shape** — `_build_mime_message(message: OutboundMessage) -> str` uses `email.mime.text.MIMEText` for text/plain bodies (text/html deferred to a future prompt). Returns `base64.urlsafe_b64encode(mime.as_bytes()).decode()` per the Gmail API contract. The API call is `gmail_service.users().messages().send(userId="me", body={"raw": mime_b64}).execute()`. Imports `googleapiclient.discovery.build` + `googleapiclient.errors.HttpError` (Gmail's inbound poll already uses `googleapiclient` per existing pack-internal tests; verify at orientation).

OUT OF SCOPE in this prompt per parent draft `prompts/11a-gmail-plaid-central.md` §"Out of scope":

- **Plaid changes** — already canonical via 11r-b. BUILD_LOG acknowledges only; zero code changes to `packs/adapters/financial/plaid/`.
- **Real Gmail OAuth credentials** — Phase A uses mocks. Any live-credential check sits behind `@pytest.mark.requires_live_services`.
- **Plaid Link OAuth flow** — product-API surface (13b), not adapter-pack surface.
- **Gmail HTML body / attachments** — defer. v1 `send` supports text/plain only.
- **Gmail multi-account** — Phase A is single-tenant.
- **Schema migration** — `messaging.received@v1` immutable; only ADDITION (`messaging.sent@v1`) per [D7].
- **Refactoring of `start` / `stop` / `status` / inbound poll / auth flow / cursor-advance / supervisor wiring** — preserved byte-for-byte.
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12.
- **Populating the observation seam slots from the supervisor** — a future Phase B prompt; this prompt only DECLARES the slots and uses them when populated.

Citations: [§6 invariant 10] (sensitivity floor preserved at `normal` for Gmail per memo 05 §1.1), [§6.13-§6.20] (observation-mode is the final outbound filter; outbound side effects route through `adminme.lib.observation.outbound`), [§7.3]/[D17] (adapters never write projections), [§8.2]/[D6] (no LLM SDK direct imports; Gmail uses `googleapiclient`, NOT an Anthropic SDK), [D7] (new schema at v1; `messaging.received@v1` immutable), [D8] (correlation_id on emission), [D19] (5-category taxonomy — Cat-A bidirectional messaging), [PM-27] (constructor injection with no-op defaults for the seam slots), [PM-34] (5 mutually exclusive ABCs), [PM-35] (capabilities-as-list — Gmail's existing Cat-A capability stays), memo 05 §1.1 (Cat-A contract — `send(message) -> SendResult` + `_on_inbound(envelope)`), §3.2 (5-ABC pattern + ABC's `send` is the abstract method this prompt implements), §6.20 (observation wrap is the final outbound filter), memo 08 §3.4 (Cohort D option-A: real Gmail outbound; Plaid skipped), §5 (inheritance + manifest only for retrofits; canonical builds add real implementations).

[VISION §"Privacy by default"]: observation-mode-default-on per [§6.16] means **production Gmail sends are suppressed until the operator explicitly disables observation**, AND every suppressed send emits `observation.suppressed@v1` with the full would-have-sent payload for the audit log. The vision is: "AdministrateMe never sends a message to the family before the family asks for it." 11a closes a real gap: today the `send` stub raises `NotImplementedError`, which would crash any pipeline trying to send a Gmail message. Post-11a, the same call routes through observation: in Phase A tests (no seams) → direct mock; in Phase B production (seams wired by supervisor) → observation-suppressed by default.

## §2 Read first (required)

Per c-band:

**c1 (schema registration) — 4 files**

- `prompts/11a-gmail-plaid-central.md` — parent draft (just merged, PR #151). The "what" and "why."
- `prompts/_active/11r-c-retrofit-reminders-to-external-state-mirror-adapter.md` — quality-bar reference. c1 schema-registration precedent + Phase-A-conditional observation-wrap pattern (referenced in c2).
- `adminme/events/schemas/ingest.py` (on main) — existing `messaging.received@v1` lives here. The new `messaging.sent@v1` is its sibling per `adminme/events/schemas/__init__.py` autoload contract.
- `adminme/events/registry.py` (on main) — `registry.register(name, version, model)` API.

**c2 (real `send` + observation wrap) — 6 files**

- `packs/adapters/messaging/gmail/adapter.py` (on main, head + class section + the existing `send` stub from 11r-a — use `sed -n '/class GmailAdapter/,/_on_inbound/p'` for the class region; do NOT full-read the file)
- `adminme/lib/adapters/communication_base.py` (on main) — Cat-A ABC. `OutboundMessage` / `SendResult` dataclass shapes the new `send` body returns. `_on_inbound` already correctly satisfied by Gmail's inbound poll.
- `adminme/lib/observation.py` (on main) — `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier)` signature. The new `send` body MUST call this with these exact parameters when seams populated.
- `packs/adapters/reminders/apple-eventkit/adapter.py::_apply_write_impl` (on main) — the 11r-c Phase-A-conditional precedent. Cat-A's real `send` lifts this exact pattern: check seam slots, route through `outbound(...)` when populated, else direct call.
- `adminme/adapters/base.py` (on main) — `AdapterContext`. Verify the `ctx` shape and whether it carries Session / ObservationManager / EventLog references (likely NOT today; the supervisor populates the seams from a Phase B prompt extending the loader path).
- `docs/05-architecture-amendment-personal-data-layer.md` §1.1 (Cat-A contract) + §3.2 (5-ABC pattern) + §6.20 (observation wrap rule).

**c3 (framework tests) — 5 files**

- `tests/unit/adapters/messaging/test_gmail_retrofit.py` (existing post-11r-a + post-sidecar-#150) — extend with new tests for `send`. F-3 unique basename for any NEW file in this commit.
- `tests/unit/adapters/financial/test_plaid_retrofit.py` (precedent layout — same `tests/unit/adapters/<family>/` location)
- `adminme/lib/observation.py` (mockable surface — tests monkeypatch `outbound` to verify call shape)
- `adminme/events/registry.py` (registry mock pattern for the new schema)
- `packs/adapters/messaging/gmail/tests/` (existing pack-internal test layout — F-3 unique basename for the new pack-internal test)

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `tests/integration/test_adapter_supervisor_gmail.py` (existing post-11r-a) — extend with send-path integration test.
- `tests/integration/test_adapter_plaid_supervisor_retrofit.py` (precedent layout — 11r-b retrofit integration sibling)
- `tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py` (precedent layout — 11r-c retrofit integration sibling; includes the write-capabilities gate test that's the closest Cat-A analogue)
- `docs/build_log.md` (append the 11a entry; include the Plaid acknowledgment subsection)

## §3 Reconciliation notes (carry-forward from 11r-a + 11r-b + 11r-c + sidecar #150)

- **F-1 (HARD/new for 11a): `outbound()` actual signature.** Parent draft cited `outbound(observation_mode_required=True, would_have_side_effect=..., perform=...)`. The actual signature on main `0493a683` at `adminme/lib/observation.py` is `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier) -> OutboundResult`. The v4 refactor reflects the correct signature; the parent draft's approximation is documentation-level (it captures intent, not API shape).
- **F-2 (HARD/from 11r-c): Phase-A-conditional seam-slot pattern.** The adapter declares three optional slots populated by `start(ctx)`; `send` routes through `outbound()` when populated, else direct call. The Phase B supervisor extension that populates the slots is OUT OF SCOPE per parent draft §"Out of scope."
- **F-3 (HARD/from 10c-iii + retrofits + sidecar #150): pack-internal test basename uniqueness.** New pack-internal test in c3 or c4 MUST use a unique basename (e.g. `test_gmail_send_pack_internal.py`). The sidecar #150 update already verified the legacy "_pack_internal" basename pattern is safe; no rename needed for existing tests.
- **F-4 (SOFT/from 11r-c): adapter.py diff size.** 11r-c's c2 was +279/-30 (within ≤300 anti-vision ceiling). 11a's c2 is comparable: ~120-180 lines added (MIME builder + `send` body + helpers). Anti-vision halt: adapter.py diff ≤200 lines for c2 (tighter than 11r-c because no inheritance flip, no ClassVar narrowing).
- **F-5 (SOFT/new for 11a): `messaging.sent@v1` body_preview truncation.** Truncate to 200 chars to match the `reminder.list_item.synced` `notes` field's `max_length=2000` pattern (lighter for messaging — body previews stay short to keep the event log scannable). Mirror Plaid's privacy-default discipline.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: GmailAdapter class name.** On main `0493a683`, the class is `GmailAdapter` in `packs/adapters/messaging/gmail/adapter.py` (verify via `grep -n "^class " packs/adapters/messaging/gmail/adapter.py`). 11a does NOT rename the class.
- **A-2: 11r-a `send` stub shape.** Verify the current `send` body: does it `raise NotImplementedError`, return a sentinel `SendResult(delivered=False)`, or no-op? The new `send` REPLACES whatever shape is there. The 11r-a c2 BUILD_LOG entry should specify; cross-check on main.
- **A-3: `OutboundMessage` fields.** Per `adminme/lib/adapters/communication_base.py`: `channel: str`, `recipient: str`, `body: str`, `metadata: dict[str, Any]`. The new `send` reads `recipient` for `To:`, `body` for the message body, and pulls `subject` from `metadata["subject"]` (or raises if absent). Verify at orientation.
- **A-4: `AdapterContext` shape.** Does `ctx` carry `session` / `observation_manager` / `event_log` references? Likely NOT today (the supervisor's loader populates the adapter's seam slots from a future Phase B prompt). Verify at orientation; if `ctx` already carries them, populate the seams in `start(ctx)`. If not, `start(ctx)` leaves them as the constructor's `None` default.
- **A-5: `messaging.received@v1` schema location.** Per `adminme/events/schemas/ingest.py` (the Gmail inbound poll emits via this module). The new `messaging.sent@v1` is its sibling. Verify location at orientation.
- **A-6: `googleapiclient` import.** Per Gmail's existing inbound poll path, `googleapiclient.discovery.build` is already imported / available. Verify by grepping for `googleapiclient` in `adapter.py` head; if not yet imported, add it (lives in `pyproject.toml` per Gmail's inbound dependency).

## §5 Objective

Implement Gmail's real `send` in `packs/adapters/messaging/gmail/adapter.py`, replacing 11r-a's stub. Three surface changes per memo 08 §5's "canonical build" pattern (vs Cohort C's "inheritance + manifest only"):

1. **Constructor extension**: three new optional seam slots (`_observation_session`, `_observation_manager`, `_observation_event_log`) defaulting to `None`; populated in `start(ctx)` if `ctx` carries them.
2. **Real `send` method body**: `_build_mime_message` helper + `send(message: OutboundMessage) -> SendResult` body that calls Gmail API v1 `users.messages.send`, routes through `adminme.lib.observation.outbound` when seams populated (else direct call), emits `messaging.sent@v1` on success or `adapter.error@v1` on `HttpError` (4xx auth → fatal; 5xx + 429 → transient with retry-after).
3. **New event schema**: `MessagingSentV1` registered at v1 in `adminme/events/schemas/ingest.py`. Payload per §1 paragraph 2.

All other Gmail adapter methods preserved byte-for-byte: `start` (except for the seam-population addition; `stop`, `status`, inbound poll path, cursor-advance, `_emit_authenticated`, `_emit_error`, `_append`, the dedup cache.

## §6 Out of scope

Same as parent draft §"Out of scope":
- Plaid changes (BUILD_LOG acknowledgment only)
- Real Gmail OAuth credentials (Phase B per prompt 16 §5)
- Plaid Link OAuth flow (13b)
- Gmail HTML body / attachments (future prompt)
- Gmail multi-account / multi-tenant (Phase B)
- Schema migration (`messaging.received@v1` immutable per [D7])
- Refactoring `start` / `stop` / `status` / inbound poll / cursor-advance / supervisor wiring beyond the seam-population line in `start`
- OpenClaw plugin messaging adapters (prompt 12)
- Populating the observation seam slots from the supervisor (future Phase B prompt)
- Changes to other adapter packs (Plaid / Reminders / Apple Notes-checklists)
- Changes to the supervisor at `adminme/daemons/adapter_supervisor.py`
- Changes to upstream pipeline subscribers (the `outbound` contract is preserved)
- Changes to `pack.yaml` or `config.schema.json` or `config.example.yaml`

## §7 Deliverables

**c1: `adminme/events/schemas/ingest.py`** — append `MessagingSentV1` Pydantic model + `registry.register("messaging.sent", 1, MessagingSentV1)` call. Fields: `member_id: str`, `recipient_email: str`, `subject: str | None`, `body_preview: str = Field(max_length=200)`, `sent_at: datetime`, `gmail_message_id: str | None` (None when observation-suppressed), `gmail_thread_id: str | None`, `correlation_id: str`. Module-import-time registration per [D7].

**c2: `packs/adapters/messaging/gmail/adapter.py`** — three changes (≤200 lines added; anti-vision halt per F-4):

1. **Imports**: add `base64`, `email.mime.text.MIMEText`, `email.utils.formatdate`, `googleapiclient.errors.HttpError`, `adminme.lib.observation.outbound` (lazy-imported inside `send` to avoid coupling at import time, or top-level — verify the existing imports' shape).
2. **`__init__` extension**: three new keyword-only params with `None` defaults:
   ```python
   def __init__(
       self,
       *,
       config: dict[str, Any],
       ... existing params ...,
       observation_session: Optional[Session] = None,
       observation_manager: Optional[ObservationManager] = None,
       observation_event_log: Optional[EventLog] = None,
   ) -> None:
       ...
       self._observation_session = observation_session
       self._observation_manager = observation_manager
       self._observation_event_log = observation_event_log
   ```
3. **`_build_mime_message(message: OutboundMessage) -> str`** — RFC 2822 + base64url-encoded:
   ```python
   def _build_mime_message(self, message: OutboundMessage) -> str:
       subject = str(message.metadata.get("subject") or "(no subject)")
       mime = MIMEText(message.body, "plain", "utf-8")
       mime["From"] = self._sender_email or "me"
       mime["To"] = message.recipient
       mime["Subject"] = subject
       mime["Date"] = formatdate(localtime=True)
       return base64.urlsafe_b64encode(mime.as_bytes()).decode()
   ```
4. **`async def send(self, message: OutboundMessage) -> SendResult`** body: validate fields → build MIME → call observation wrap when seams populated, else direct → on success emit `messaging.sent` → on `HttpError` classify + emit `adapter.error` + raise. ~80-120 lines.

**c3: `tests/unit/adapters/messaging/test_gmail_send.py`** (NEW, F-3 unique basename, ≥15 tests):

1. `test_build_mime_message_text_plain_happy_path` — recipient + subject + body present; returns base64-encoded bytes; decoded MIME has `From:`, `To:`, `Subject:`, `Date:`.
2. `test_build_mime_message_missing_subject_defaults_to_placeholder` — `metadata` has no `subject`; resulting MIME has `Subject: (no subject)`.
3. `test_send_happy_path_with_seams_populated` — monkeypatch `adminme.lib.observation.outbound` to record the call; assert called with `action="messaging.send"`, the correct payload + target_identifier; assert `SendResult(delivered=True, external_id=<gmail_message_id>, suppressed=False)`.
4. `test_send_observation_suppressed_path` — `outbound` returns `OutboundResult(suppressed=True)`; assert `SendResult(delivered=False, external_id=None, suppressed=True)`; assert NO `messaging.sent` emit (monkeypatched event log records no append).
5. `test_send_direct_call_when_seams_not_populated` — no seam slots; adapter calls Gmail service directly; emits `messaging.sent` on success.
6. `test_send_validates_outbound_message_recipient` — empty recipient raises `ValueError`; observation wrap NOT reached.
7. `test_send_validates_outbound_message_body` — empty body raises `ValueError`.
8. `test_send_4xx_auth_failure_classifies_fatal` — `HttpError(status=401)`; `adapter.error` emitted with `severity="fatal"`, `error_kind="auth_failed"`; `send` raises.
9. `test_send_5xx_classifies_transient_with_retry_after` — `HttpError(status=503, retry_after=30)`; `adapter.error` emitted with `severity="transient"`, `retry_after_s=30`.
10. `test_send_429_classifies_transient_with_retry_after` — `HttpError(status=429, retry_after=60)`; same as 5xx but distinguished by status.
11. `test_send_emits_messaging_sent_with_correlation_id` — verify `correlation_id` carried from `ctx.correlation_id_factory()` (or equivalent) into the emit.
12. `test_send_truncates_body_preview_to_200_chars` — body is 5000 chars; `messaging.sent.payload.body_preview` is exactly 200 chars.
13. `test_send_html_body_falls_back_to_plain` — body contains HTML tags; MIME is still text/plain (HTML deferred per §6); no error.
14. `test_messaging_sent_v1_schema_registers_at_v1` — `registry.get("messaging.sent", 1)` returns the Pydantic model.
15. `test_messaging_sent_v1_rejects_payload_missing_member_id` — Pydantic raises `ValidationError`.

Plus 2 NEW assertions into the existing `tests/unit/adapters/messaging/test_gmail_retrofit.py` (post-11r-a + post-sidecar-#150):
- `test_send_no_longer_raises_not_implemented` — verifies the stub is replaced.
- `test_send_routes_through_observation_when_seams_populated` — verifies the wrap shape.

Load `GmailAdapter` via the file-path importlib pattern (mirrors 11r-a + 11r-b + 11r-c precedents).

**c4: pack-internal + integration tests + BUILD_LOG**:

1. `packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py` (NEW, F-3 unique basename, ~3 tests):
   - `test_send_imports_match_gmail_api_client_surface` — assert `googleapiclient.errors.HttpError` is importable + `googleapiclient.discovery.build` is importable.
   - `test_mime_message_round_trips_through_base64_decode` — pack-internal MIME round-trip.
   - `test_messaging_sent_v1_schema_round_trips_via_registry` — pack-internal schema round-trip.

2. `tests/integration/test_adapter_supervisor_gmail.py` (EXTEND, ~3 new tests):
   - `test_supervisor_loads_gmail_send_path_works_through_loader` — full lifecycle including a synthetic outbound queued through the supervisor's send path.
   - `test_supervisor_seam_slots_propagate_through_loader` — verifies if the loader carries Session/Manager/EventLog, they reach the adapter's `_observation_session`/`_observation_manager`/`_observation_event_log`. **NOTE: if the supervisor loader does NOT propagate them today, this test is `@pytest.mark.skip(reason="Phase B supervisor extension")` until the Phase B prompt lands.**
   - `test_supervisor_observation_off_send_calls_gmail_directly` — toggle observation OFF; verify the Gmail mock receives the API call.

3. `docs/build_log.md` — append the 11a entry. Include the "Plaid acknowledgment" subsection: explicit note that Plaid's canonical 11a obligations were met via 11r-b's Cohort C step 2 retrofit; no code changes to `packs/adapters/financial/plaid/` in this prompt.

**Demo command (post-implementation):**

```bash
adminme adapters validate packs/adapters/messaging/gmail
# Expected exit code: 0; NO advisory_pending_retrofit; NO errors
```

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11a commit <N>: <imperative summary>` exactly.

- **c1: `phase 11a commit 1: register messaging.sent@v1 schema`** — `adminme/events/schemas/ingest.py` extension (≤40 lines added: Pydantic model + register call).
- **c2: `phase 11a commit 2: GmailAdapter real send + observation wrap + MIME builder`** — `packs/adapters/messaging/gmail/adapter.py` (≤200 lines added; F-4 HARD anti-vision halt).
- **c3: `phase 11a commit 3: framework-level Gmail send tests`** — `tests/unit/adapters/messaging/test_gmail_send.py` (NEW, ~15 tests) + `test_gmail_retrofit.py` extension (2 new assertions).
- **c4: `phase 11a commit 4: integration test refresh + pack-internal send tests + BUILD_LOG`** — pack-internal (NEW, F-3 unique basename) + integration extension (3 new tests) + BUILD_LOG append with Plaid acknowledgment.

## §8.5 Branch strategy — STACKED

Each c-band commits onto the prior c-band's branch; PRs open with `base=main` (per PR #106 sidecar policy). Ghost-conflict risk: LOW for c1+c2+c3 (non-overlapping file scopes), MEDIUM for c4 (touches existing `test_adapter_supervisor_gmail.py` which c2's `send` body may interact with — but c4's added tests are new tests, not modifications to existing tests, so the conflict surface stays low). If a c-band lands dirty, fall through to Chrome MCP JS-regex ghost-conflict resolution per 11r-b/11r-c precedent.

## §9 Verification

Per c-band:

**c1**:
```bash
poetry run python -c "from adminme.events.registry import registry; m = registry.get('messaging.sent', 1); assert m is not None; print('messaging.sent@v1 registered ✓')"
```

**c2**:
```bash
poetry run python -c "from packs.adapters.messaging.gmail.adapter import GmailAdapter; assert hasattr(GmailAdapter, 'send'); import asyncio; print('send method present ✓')"
poetry run mypy adminme/ packs/adapters/messaging/gmail/ 2>&1 | tail -5
```

**c3**:
```bash
poetry run pytest tests/unit/adapters/messaging/test_gmail_send.py tests/unit/adapters/messaging/test_gmail_retrofit.py -v
# Expected: 15+ passed
```

**c4**:
```bash
poetry run pytest packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py tests/integration/test_adapter_supervisor_gmail.py -v
# Expected: all pass (or skipped with documented Phase B reason)
poetry run adminme adapters validate packs/adapters/messaging/gmail
# Expected: exit 0; NO advisory_pending_retrofit; NO errors
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When `adminme adapters validate packs/adapters/messaging/gmail` exits 0 with NO `advisory_pending_retrofit` finding AND no errors, AND `pytest tests/unit/adapters/messaging/test_gmail_send.py -v` is all green, c4 is done. **Cohort D step 1 complete.** Open the PR per the standard four-commit structure. Do NOT proceed to canonical 11b (Cat-B cohort — Apple Reminders dual-deployment + Apple Calendar dual + Google Calendar + Google Tasks) without operator review of the 11a merge — 11b is a pre-split candidate per PROMPT_SEQUENCE.md row 117 + memo 05 §6.6 and warrants a separate split-memo cycle.

After merge: Partner runs three-job QC on the merged state, then opens the partner-state-snapshot PR (PROMPT_SEQUENCE.md Pause rule updated; partner_handoff.md new Last-updated paragraph; build_log.md placeholder patch on the 11a entry's PR-numbers + commit shas).
