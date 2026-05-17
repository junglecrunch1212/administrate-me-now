# Prompt 11a: Gmail real outbound (Cohort D step 1, refreshed)

**Phase:** L1 / Cohort D step 1 — first canonical-build prompt post-Cohort-C-closure per `prompts/PROMPT_SEQUENCE.md` row 116 + `docs/08-architecture-amendment-adapter-rebase.md` §3.4. Parent draft: `prompts/11a-gmail-plaid-central.md` (PR #151 + refresh PR <this>).
**Branch:** `claude/11a-gmail-c<N>` per c-band (Executor's harness assigns)
**Stop condition:** Gmail's `send` is a real Gmail API v1 `users.messages.send` implementation (replaces 11r-a's stub), routes through `adminme.lib.observation.outbound` when seam slots populated per the 11r-c Phase-A-conditional precedent, emits the EXISTING `messaging.sent@v1` schema (already registered on main since phase 04-3 commit `2ce1cff`; NO new schema) with field-mapping from Gmail semantics, and `adminme adapters validate packs/adapters/messaging/gmail` continues to exit 0 with NO `advisory_pending_retrofit`.
**Depends-on:** Cohort B closed (PRs #118-#121 / #123-#126 / #128-#131); Cohort C closed (PRs #133-#136 / #139-#142 / #145-#148 + sidecar #150); 11a parent draft refresh (this PR)
**Quality-bar reference:** `prompts/_active/11r-c-retrofit-reminders-to-external-state-mirror-adapter.md` (PR #144, executor PRs #145-#148). 11r-c established the Phase-A-conditional observation-wrap pattern via three seam slots. 11a applies the same pattern to Cat-A `send`.
**Refresh history:** First authored 2026-05-17 in PR #152 (commit `70cb65d4`) with a c1 spec that asked to register `messaging.sent@v1` — colliding with main's existing v1 registration at `adminme/events/schemas/ingest.py:30-38` (phase 04-3, commit `2ce1cff`, 47 production consumers). Cycle #17 c1 caught the collision via the Executor's pre-flight check and halted cleanly (`<COMMIT_FAILED reason="prompt §7 c1 spec collides with main"/>`, 6m 7s, no commits pushed). Re-authored per Executor halt option (c) "Revise the prompt." This refresh: c1 becomes verify-only NO-OP; c2 maps Gmail outbound to the existing v1 field set; no new schema introduced.
**Estimated duration:** c1 ~10-15 min (no-op verify) · c2 ~60-90 min · c3 ~45-60 min · c4 ~45-60 min · total ~3-3.5 hrs wall / ~$50-80 spend

---

## §1 Behavioral grounding

Canonical 11a is the **first non-retrofit Cohort D prompt** — the pattern shift from "inheritance + manifest only" (Cohort C) to "full event-sourcing build" (Cohort D). Scope is narrow because Cohort C retrofits already brought Gmail (11r-a) and Plaid (11r-b) onto their typed ABCs AND `messaging.sent@v1` was already registered in phase 04-3. What 11a adds is the **real `send` implementation** that 11r-a deferred per its anti-vision halt. This is the **template-setting work** that 11b (Cat-B AppleScript writeback) + 11g (Cat-E HA service-call) will follow.

Three load-bearing items this prompt establishes:

1. **Phase-A-conditional observation wrap on `send`** — the seam-slot pattern 11r-c established for Cat-B's `_apply_write_impl` carries directly to Cat-A's `send`. Adapter declares optional `_observation_session: Session | None`, `_observation_manager: ObservationManager | None`, `_observation_event_log: EventLog | None` slots populated by `start(ctx)` if `ctx` carries them. When populated → `outbound(session, action="messaging.send", payload=..., action_fn=lambda: ..., *, manager=..., event_log=..., target_channel="gmail", target_identifier=message.recipient)`. When NOT populated (Phase A) → direct Gmail API call.

2. **Reuse the EXISTING `messaging.sent@v1` schema, NO new registration.** `adminme/events/schemas/ingest.py` already declares `MessagingSentV1` with `extra="forbid"` and fields `{source_channel: str, to_identifier: str, thread_id: str | None, subject: str | None, body_text: str | None, sent_at: str, delivery_status: Literal["queued","sent","failed"]}`. Per [D7] v1 schemas are immutable. Gmail outbound's emit maps to this exact set: `source_channel="gmail"`, `to_identifier=message.recipient`, `thread_id=response.get("threadId")` (or None when observation-suppressed), `subject=message.metadata.get("subject", None)`, `body_text=message.body` (FULL body, not a truncated preview), `sent_at=<UTC ISO timestamp from ctx>`, `delivery_status="sent"` on Gmail accept OR `"queued"` when observation suppresses. c1 verifies the schema is registered (no-op via `registry.get("messaging.sent", 1)`).

3. **MIME construction + Gmail API call shape** — `_build_mime_message(message: OutboundMessage) -> str` uses `email.mime.text.MIMEText` for text/plain bodies (text/html deferred). Returns `base64.urlsafe_b64encode(mime.as_bytes()).decode()` per the Gmail API contract. API call: `gmail_service.users().messages().send(userId="me", body={"raw": mime_b64}).execute()`. Imports `googleapiclient.discovery.build` + `googleapiclient.errors.HttpError`.

OUT OF SCOPE in this prompt per parent draft §"Out of scope":

- **Plaid changes** — already canonical via 11r-b. BUILD_LOG acknowledges only; zero code changes to `packs/adapters/financial/plaid/`.
- **Registering a new schema** — `messaging.sent@v1` already exists on main. Registering a colliding v1 crashes import time per `registry.py:69` `RegistryError`. Adding fields breaks [D7] immutability + 47 production consumers (thank_you_detection, commitment_extraction, identity_resolution, interactions projection, Gmail inbound poll). Gmail-specific audit fields (e.g. gmail_message_id) flow through `observation.py`'s `outbound()` which already emits `external.sent` carrying the action_fn return value — that's the audit surface.
- **Adding a `messaging.sent@v2`** — out of scope. Would require migrating the 47 consumers.
- **Real Gmail OAuth credentials** — Phase A uses mocks + `@pytest.mark.requires_live_services` gating.
- **Plaid Link OAuth flow** — product-API surface (13b).
- **Gmail HTML body / attachments** — defer. v1 `send` supports text/plain only.
- **Gmail multi-account** — Phase A is single-tenant.
- **Refactoring of `start` / `stop` / `status` / inbound poll / auth flow / cursor-advance / supervisor wiring** — preserved byte-for-byte except for the seam-population line in `start(ctx)`.
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12.
- **Populating the observation seam slots from the supervisor** — a future Phase B prompt.

Citations: [§6 invariant 10] (sensitivity floor preserved at `normal`), [§6.13-§6.20] (observation is the final outbound filter), [§7.3]/[D17] (adapters never write projections), [§8.2]/[D6] (no LLM SDK direct imports), [D7] (event schemas register at v1 ONCE, immutable; this prompt USES the existing v1, does not re-register), [D8] (correlation_id propagation), [D19] (5-category taxonomy), [PM-27] (constructor injection with no-op defaults for seams), [PM-34] (5 mutually exclusive ABCs), [PM-35] (capabilities-as-list), memo 05 §1.1 (Cat-A contract), §3.2 (5-ABC pattern), §6.20 (observation wrap), memo 08 §3.4 (Cohort D option-A: real Gmail outbound; Plaid skipped), §5 (canonical builds add real implementations).

[VISION §"Privacy by default"]: observation-mode-default-on per [§6.16] means production Gmail sends are suppressed until the operator explicitly disables observation, AND every suppressed send emits `observation.suppressed@v1` via `outbound()` for the audit log. The vision: "AdministrateMe never sends a message to the family before the family asks for it." 11a closes the gap — today the `send` stub raises `NotImplementedError`. Post-11a: the same call routes through observation; Phase A tests with no seams → direct mock; Phase B production with seams wired → observation-suppressed by default.

## §2 Read first (required)

Per c-band:

**c1 (verify-only no-op) — 3 files**

- `prompts/11a-gmail-plaid-central.md` — refreshed parent draft. The "what" and "why."
- `adminme/events/schemas/ingest.py` (on main) — **READ THE FILE CONTENTS, not just the path.** The existing `MessagingSentV1` model + `registry.register("messaging.sent", 1, MessagingSentV1)` call.
- `adminme/events/registry.py` (on main) — `registry.get(name, version)` API used by c1's verify step.

**c2 (real `send` + observation wrap) — 6 files**

- `packs/adapters/messaging/gmail/adapter.py` (on main, head + class section + the existing `send` stub from 11r-a — use `sed -n '/class GmailAdapter/,/_on_inbound/p'`)
- `adminme/lib/adapters/communication_base.py` (on main) — Cat-A ABC. `OutboundMessage(channel, recipient, body, metadata)`, `SendResult(delivered, external_id, suppressed)`.
- `adminme/lib/observation.py` (on main) — `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier) -> OutboundResult`.
- `packs/adapters/reminders/apple-eventkit/adapter.py::_apply_write_impl` (on main) — 11r-c Phase-A-conditional precedent.
- `adminme/adapters/base.py` (on main) — `AdapterContext`. Verify the `ctx` shape.
- `adminme/events/schemas/ingest.py::MessagingSentV1` — the field set Gmail outbound must populate.

**c3 (framework tests) — 5 files**

- `tests/unit/adapters/messaging/test_gmail_retrofit.py` (existing, post-sidecar-#150) — extend with new send-tests.
- `tests/unit/adapters/financial/test_plaid_retrofit.py` (precedent layout)
- `adminme/lib/observation.py` (mockable; tests monkeypatch `outbound`)
- `adminme/events/schemas/ingest.py::MessagingSentV1` (field-mapping assertion target)
- `packs/adapters/messaging/gmail/tests/` (pack-internal layout)

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `tests/integration/test_adapter_supervisor_gmail.py` (existing) — extend with send-path integration.
- `tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py` (11r-c precedent for seam-slot integration test).
- `docs/build_log.md` (append 11a entry; include Plaid acknowledgment + the schema-reconciliation note).
- The Executor's c1 halt log from cycle #17 — informs the BUILD_LOG narrative for future Partner sessions.

## §3 Reconciliation notes (carry-forward + refresh)

- **F-1 (HARD/from cycle #17 halt): `messaging.sent@v1` already exists on main.** Originally surfaced by the Executor at cycle #17 c1 via the pre-flight `<COMMIT_FAILED reason="prompt §7 c1 spec collides with main"/>`. The pre-authorship gate v4.3 Gate 0 listed `ingest.py` in the read-first but did not verify its CONTENTS. Lesson: when citing a schema-file, READ THE FILE and grep for any `registry.register("<event_name>", ...)` calls before authoring a c1 that registers same-name events. This refresh: c1 becomes verify-only NO-OP; c2 maps Gmail outbound to the existing v1 fields.
- **F-2 (HARD/from 11r-a): `send` stub replacement.** Replace whatever shape the 11r-a stub takes; preserve all other Gmail methods byte-for-byte.
- **F-3 (HARD/from 11r-c): Phase-A-conditional seam-slot pattern.** Three optional slots populated by `start(ctx)` when ctx carries them.
- **F-4 (HARD/from 10c-iii + retrofits + sidecar #150): pack-internal test basename uniqueness.** New pack-internal test in c3/c4 MUST use a unique basename.
- **F-5 (SOFT/from 11r-c): adapter.py diff size.** Anti-vision halt: adapter.py diff ≤200 lines added in c2.
- **F-6 (HARD/new for this refresh): MAP DON'T REGISTER.** Gmail outbound emits the existing `MessagingSentV1` schema's exact fields. Do NOT add fields. Do NOT register a new schema. Do NOT touch `adminme/events/schemas/ingest.py`. If Gmail-specific audit fields are wanted, they flow through `observation.py`'s `outbound()` → `external.sent` event (already carries action_fn return per `observation.py:285-303`).

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: GmailAdapter class name.** Verify `class GmailAdapter:` in `packs/adapters/messaging/gmail/adapter.py`.
- **A-2: 11r-a `send` stub shape.** Verify the current `send` body.
- **A-3: `OutboundMessage` fields.** `channel: str`, `recipient: str`, `body: str`, `metadata: dict[str, Any]`. `subject` lives in `metadata`.
- **A-4: `AdapterContext` shape.** Does `ctx` carry `session` / `observation_manager` / `event_log`? Likely NOT today.
- **A-5: `MessagingSentV1` field set.** Verify `{source_channel, to_identifier, thread_id, subject, body_text, sent_at, delivery_status}` matches the on-main schema. The Gmail emit body uses these EXACT field names.
- **A-6: `googleapiclient` import.** Already imported by Gmail's inbound poll.

## §5 Objective

Implement Gmail's real `send` in `packs/adapters/messaging/gmail/adapter.py`, replacing 11r-a's stub. Three surface changes per memo 08 §5's "canonical build" pattern:

1. **Constructor extension**: three optional seam slots defaulting to `None`; populated in `start(ctx)` if `ctx` carries them.
2. **Real `send` method body**: `_build_mime_message` helper + `send(message: OutboundMessage) -> SendResult` body that calls Gmail API v1 `users.messages.send`, routes through `adminme.lib.observation.outbound` when seams populated (else direct call), emits the EXISTING `messaging.sent@v1` schema with field-mapping on success or `adapter.error@v1` on `HttpError`.
3. **Verify-only c1**: `registry.get("messaging.sent", 1)` returns `MessagingSentV1` (no-op; no schema changes).

All other Gmail adapter methods preserved byte-for-byte.

## §6 Out of scope

Same as parent draft §"Out of scope":
- Plaid changes
- New schema registration (`messaging.sent@v1` already exists)
- Adding fields to the existing v1 schema
- `messaging.sent@v2` migration
- Real Gmail OAuth credentials (Phase B)
- Plaid Link OAuth (13b)
- Gmail HTML body / attachments
- Gmail multi-account
- Refactoring other adapter methods
- OpenClaw plugin variants (12)
- Populating seam slots from the supervisor (Phase B)

## §7 Deliverables

**c1: VERIFY-ONLY NO-OP** — `adminme/events/schemas/ingest.py` is NOT modified. c1's job is to confirm the existing schema is registered:

```python
from adminme.events.registry import registry
from adminme.events.schemas.ingest import MessagingSentV1
assert registry.get("messaging.sent", 1) is MessagingSentV1
```

Net diff: 0 lines to production code. c1 ships an empty commit (or, equivalently, a commit message documenting the no-op). The Executor's c1 step asserts `registry.get(...) is not None` and exits 0.

**c2: `packs/adapters/messaging/gmail/adapter.py`** — three changes (≤200 lines added; F-5 anti-vision halt):

1. **Imports**: add `base64`, `email.mime.text.MIMEText`, `email.utils.formatdate`, `googleapiclient.errors.HttpError`, `adminme.lib.observation.outbound` (lazy or top-level), `adminme.events.schemas.ingest.MessagingSentV1` (NOT for re-registration, just for type/field reference).
2. **`__init__` extension**: three new keyword-only params `observation_session`, `observation_manager`, `observation_event_log` with `None` defaults. Store as `self._observation_*`.
3. **`_build_mime_message(message: OutboundMessage) -> str`**: RFC 2822 MIME via `MIMEText(message.body, "plain", "utf-8")`; set From/To/Subject/Date; return `base64.urlsafe_b64encode(mime.as_bytes()).decode()`.
4. **`async def send(self, message: OutboundMessage) -> SendResult`** body — emits `messaging.sent@v1` with EXISTING field-mapping:

```python
sent_at_iso = ctx.now_factory().isoformat() + "Z"  # or however Gmail's adapter already formats
payload = {
    "source_channel": "gmail",
    "to_identifier": message.recipient,
    "thread_id": response.get("threadId"),  # from Gmail API; None on observation-suppress
    "subject": str(message.metadata.get("subject")) if message.metadata.get("subject") else None,
    "body_text": message.body,  # FULL body (existing schema field is `body_text: str | None`)
    "sent_at": sent_at_iso,
    "delivery_status": "sent",  # or "queued" when observation-suppress
}
```

On observation-suppressed: `delivery_status="queued"`, `thread_id=None`, otherwise same payload (still useful audit). On `HttpError`: classify per `AdapterErrorV1` taxonomy (4xx auth → fatal `"auth_failed"`; 5xx → transient `"network"` with retry-after; 429 → transient `"rate_limited"` with retry-after); emit `adapter.error@v1` via existing `_emit_error`; raise (supervisor retry).

**c3: `tests/unit/adapters/messaging/test_gmail_send.py`** (NEW, F-4 unique basename, ≥12 tests):

1. `test_build_mime_message_text_plain_happy_path` — recipient + subject + body present; returns base64-encoded; decoded MIME has expected headers.
2. `test_build_mime_message_missing_subject_uses_placeholder` — no `subject` in metadata; result has `Subject: (no subject)` or empty (verify A-3).
3. `test_send_happy_path_with_seams_populated` — monkeypatch `adminme.lib.observation.outbound` to record the call; assert called with `action="messaging.send"`, payload has `source_channel="gmail"` + `to_identifier=message.recipient` + `body_text=message.body` + `delivery_status="sent"` + the existing schema's field set ONLY (no extras); `SendResult(delivered=True, external_id=<gmail id>, suppressed=False)`.
4. `test_send_observation_suppressed_path` — `outbound` returns `OutboundResult(suppressed=True)`; assert emitted `messaging.sent@v1` payload has `delivery_status="queued"`, `thread_id=None`; `SendResult(delivered=False, external_id=None, suppressed=True)`.
5. `test_send_direct_call_when_seams_not_populated` — no seam slots; adapter calls Gmail service directly; emits `messaging.sent@v1` with `delivery_status="sent"`.
6. `test_send_emits_only_existing_messaging_sent_fields` — Pydantic `MessagingSentV1.model_validate(payload)` succeeds; `extra="forbid"` means any extra field would fail.
7. `test_send_validates_outbound_message_recipient` — empty recipient raises `ValueError`.
8. `test_send_4xx_auth_failure_classifies_fatal` — `HttpError(status=401)` → fatal `adapter.error`.
9. `test_send_5xx_classifies_transient` — `HttpError(status=503)` → transient.
10. `test_send_429_classifies_rate_limited_transient` — `HttpError(status=429)`.
11. `test_send_full_body_text_not_truncated` — body is 5000 chars; payload `body_text` is exactly 5000 chars (matches existing schema's `body_text: str | None`).
12. `test_messaging_sent_v1_registry_lookup_returns_existing_model` — `registry.get("messaging.sent", 1) is MessagingSentV1` (c1 contract).

Plus 2 extensions to `tests/unit/adapters/messaging/test_gmail_retrofit.py`:
- `test_send_no_longer_raises_not_implemented`
- `test_send_routes_through_observation_when_seams_populated`

**c4: pack-internal + integration tests + BUILD_LOG**:

1. `packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py` (NEW, F-4 unique basename, ~3 tests):
   - `test_send_imports_match_gmail_api_client_surface`
   - `test_mime_message_round_trips_through_base64_decode`
   - `test_messaging_sent_v1_emit_validates_against_existing_schema`

2. `tests/integration/test_adapter_supervisor_gmail.py` (EXTEND, ~3 new tests):
   - `test_supervisor_loads_gmail_send_path_works_through_loader`
   - `test_supervisor_seam_slots_propagate_through_loader` (or `@pytest.mark.skip` if loader doesn't propagate today — Phase B work)
   - `test_supervisor_observation_off_send_calls_gmail_directly`

3. `docs/build_log.md` — append the 11a entry with TWO subsections:
   - **Plaid acknowledgment**: Plaid is canonical-11a-compliant via 11r-b; zero code changes.
   - **Schema reconciliation**: cycle #17 c1 Executor halt + the refresh disposition. Documents the Gate 0 strengthening lesson (read schema-file CONTENTS, not just verify paths exist).

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11a commit <N>: <imperative summary>`.

- **c1: `phase 11a commit 1: verify messaging.sent@v1 already registered (no-op)`** — 0 production-code lines added; commit body documents that the schema exists on main since phase 04-3 commit `2ce1cff`.
- **c2: `phase 11a commit 2: GmailAdapter real send + observation wrap + MIME builder`** — `packs/adapters/messaging/gmail/adapter.py` (≤200 lines added; F-5 HARD anti-vision halt).
- **c3: `phase 11a commit 3: framework-level Gmail send tests`** — `tests/unit/adapters/messaging/test_gmail_send.py` (NEW, ~12 tests) + `test_gmail_retrofit.py` extension (2 tests).
- **c4: `phase 11a commit 4: integration + pack-internal send tests + BUILD_LOG`** — pack-internal (NEW) + integration extension + BUILD_LOG with Plaid acknowledgment + schema reconciliation note.

## §8.5 Branch strategy — STACKED

Each c-band commits onto the prior c-band's branch; PRs open with `base=main` per PR #106 sidecar. Ghost-conflict risk: LOW across all c-bands (c1 doesn't modify production code at all; c2-c4 file scopes are non-overlapping). 11r-c precedent: zero ghost conflicts.

## §9 Verification

**c1**:
```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.ingest import MessagingSentV1; assert registry.get('messaging.sent', 1) is MessagingSentV1; print('messaging.sent@v1 verified registered ✓')"
```

**c2**:
```bash
poetry run python -c "from packs.adapters.messaging.gmail.adapter import GmailAdapter; assert hasattr(GmailAdapter, 'send'); print('send method present ✓')"
poetry run mypy adminme/ packs/adapters/messaging/gmail/ 2>&1 | tail -5
```

**c3**:
```bash
poetry run pytest tests/unit/adapters/messaging/test_gmail_send.py tests/unit/adapters/messaging/test_gmail_retrofit.py -v
```

**c4**:
```bash
poetry run pytest packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py tests/integration/test_adapter_supervisor_gmail.py -v
poetry run adminme adapters validate packs/adapters/messaging/gmail
# Expected: exit 0; NO advisory_pending_retrofit; NO errors
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When `adminme adapters validate packs/adapters/messaging/gmail` exits 0 with NO `advisory_pending_retrofit` AND `pytest tests/unit/adapters/messaging/test_gmail_send.py -v` is green AND the existing `messaging.sent@v1` schema is UNCHANGED on main, c4 is done. **Cohort D step 1 complete.** Open the PR per the standard four-commit structure. Do NOT proceed to canonical 11b without operator review.

After merge: Partner runs three-job QC, opens the partner-state-snapshot PR (PROMPT_SEQUENCE.md Pause rule update; partner_handoff.md new paragraph; build_log.md placeholder patch with the Plaid acknowledgment + schema reconciliation note).
