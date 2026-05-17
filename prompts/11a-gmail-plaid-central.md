# Prompt 11a: Gmail real outbound + Plaid central acknowledgment (Cohort D step 1)

**Phase:** Phase A — L1 Cohort D step 1 (first canonical-build prompt after Cohort C retrofits closed). Per `prompts/PROMPT_SEQUENCE.md` row 116 + `docs/08-architecture-amendment-adapter-rebase.md` §3.4.

**Depends on:** Cohort B closed (PRs #118-#121 / #123-#126 / #128-#131). Cohort C closed (PRs #133-#136 / #139-#142 / #145-#148 + sidecar #150). All three shipped v1 packs (Gmail / Plaid / Reminders) framework-conformant.

**Estimated duration:** 3-4 hours. **NOT a pre-split candidate** — scope is narrow post-Cohort-C (real Gmail `send` + observation wrap + tests; Plaid already done via 11r-b; `messaging.sent@v1` schema already exists on main).

**Stop condition:** Gmail's `send` method is a real implementation (no longer the 11r-a stub), routes through `adminme.lib.observation.outbound` per [§6.20], emits the existing `messaging.sent@v1` schema (registered in phase 04-3 commit `2ce1cff`) on success, and `adminme adapters validate packs/adapters/messaging/gmail` continues to exit 0 with NO `advisory_pending_retrofit` finding.

**Refresh history:** This parent draft was first authored in PR #151 (`0493a683`) with a schema-registration plan that COLLIDED with the existing `messaging.sent@v1` on main. Cycle #17 c1 caught the collision via the Executor's pre-flight check and halted cleanly with `<COMMIT_FAILED reason="prompt §7 c1 spec collides with main: messaging.sent@v1 is already registered at adminme/events/schemas/ingest.py:30-38 (phase 04-3, commit 2ce1cff) with a different field shape"/>`. Re-authored under refresh PR to reconcile: use the existing v1 schema, NO-OP c1, map Gmail send to existing fields. Refresh disposition path: Executor halt option (c) "Revise the prompt — partner refresh that reconciles c1 with main's current state."

---

## Read first

1. `docs/08-architecture-amendment-adapter-rebase.md` §3.4 — full read. Defines what canonical 11a is and isn't, including the explicit option-A/B decision deferred to author-time (resolved here: option A, Gmail real outbound; Plaid skipped since it's already done via 11r-b).
2. `prompts/PROMPT_SEQUENCE.md` row 116 — the canonical 11a spec. Note: "Gmail + Plaid emit into event log via the new framework" is the Verifies column. **Plaid already meets this post-11r-b.** Gmail's inbound poll already emits `messaging.received` events via the post-retrofit framework. What's NEW in canonical 11a is the **outbound surface** — real Gmail API `send` implementation establishing the Cat-A real-outbound template for subsequent prompts (11b Cat-B AppleScript writeback; 11g Cat-E HA service-call).
3. `adminme/events/schemas/ingest.py` (on main) — **READ THE FILE CONTENTS, not just the path.** The existing `MessagingSentV1` Pydantic model has fields `{source_channel: str, to_identifier: str, thread_id: str | None, subject: str | None, body_text: str | None, sent_at: str, delivery_status: Literal["queued", "sent", "failed"]}`. Registered at v1 alongside `MessagingReceivedV1`, `TelephonySmsReceivedV1`, the calendar event family, `ArtifactReceivedV1`, and `MessagingClassifiedV1`. The Gmail outbound's emit MUST map to this exact field set.
4. `docs/05-architecture-amendment-personal-data-layer.md` — sections §1.1 (Cat-A definition) + §3.2 (`CommunicationAdapter` ABC contract — `send(message) -> SendResult`) + §6.20 (observation-mode wrapping rule).
5. `prompts/_active/11r-a-retrofit-gmail-to-communication-adapter.md` (audit trail) — the retrofit that added the `send` stub this prompt replaces.
6. `packs/adapters/messaging/gmail/adapter.py` (on main) — the post-retrofit Gmail adapter. `send` is currently a stub.
7. `adminme/lib/adapters/communication_base.py` (on main) — the Cat-A ABC.
8. `adminme/lib/observation.py` (on main) — `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier)`. The real `send` MUST call this when seam slots populated.
9. `packs/adapters/reminders/apple-eventkit/adapter.py::_apply_write_impl` (on main) — Cat-B precedent for the Phase-A-conditional observation-wrap pattern.

## Operating context

Canonical 11a is the **first non-retrofit Cohort D prompt** — the pattern shift from "inheritance + manifest only" (Cohort C) to "full event-sourcing build" (Cohort D). For 11a, the scope is intentionally narrow because the Cohort C retrofits already accomplished most of canonical 11a's stated targets:

- **Gmail framework conformance:** done via 11r-a (PRs #133-#136)
- **Plaid framework conformance:** done via 11r-b (PRs #139-#142)
- **`adapter.*` cross-cutting schemas:** done via 11-α
- **Adapter supervisor wiring:** done via 11-γ
- **`adminme adapters` CLI:** done via 11-γ
- **`messaging.sent@v1` schema:** ALREADY EXISTS — registered in phase 04-3 commit `2ce1cff` per `adminme/events/schemas/ingest.py`. Per [D7] schemas are immutable at v1 once registered.

What canonical 11a adds is the **real outbound implementation** for Gmail — replacing the `send` stub the 11r-a retrofit deferred per its anti-vision halt (memo 08 §5: "inheritance + manifest only, not a rewrite"). The new `send` body MAPS Gmail outbound semantics onto the existing `messaging.sent@v1` field set; it does NOT register a new schema. This is the **template-setting work** that 11b (Cat-B AppleScript writeback) and 11g (Cat-E HA service-call) will follow when they build real outbound paths.

**Plaid is explicitly out of scope.** Cat-C has no write surface, and the post-11r-b Plaid is already fully event-sourced (existing webhook + sync flow preserved byte-for-byte). The "Plaid central acknowledgment" in this prompt's title is a documentation-only acknowledgment in BUILD_LOG that Plaid's canonical 11a obligations were met via the Cohort C step 2 retrofit.

## Objective

Implement Gmail's real `send` method in `packs/adapters/messaging/gmail/adapter.py`, replacing the 11r-a stub. The implementation:

- Calls Google's Gmail API v1 `users.messages.send` endpoint via the `google-api-python-client` library.
- Routes the actual API call through `adminme.lib.observation.outbound(session, action="messaging.send", payload=..., action_fn=...)` per [§6.20] when the three Phase-A-conditional seam slots (`_observation_session`, `_observation_manager`, `_observation_event_log`) are populated. When not populated (Phase A test default), calls the Gmail API directly to preserve byte-for-byte production behavior.
- On success: emits the existing `messaging.sent@v1` event (NOT a new schema) with fields mapped from Gmail outbound semantics — `source_channel="gmail"`, `to_identifier=<recipient>`, `thread_id=<gmail_thread_id_or_none>`, `subject=<subject>`, `body_text=<full body>`, `sent_at=<iso timestamp>`, `delivery_status="sent"`.
- On observation-suppressed: emits `messaging.sent@v1` with `delivery_status="queued"` (matches the existing Literal enum) per [§6.16] (observation default-on records the audit trail).
- On `HttpError`: classifies into the `AdapterErrorV1` taxonomy (4xx auth → fatal; 5xx + 429 → transient with retry-after); emits `adapter.error@v1` via the existing `_emit_error` helper.
- Preserves all existing Gmail adapter behavior byte-for-byte: inbound poll + auth flow + cursor-advance + supervisor lifecycle.

## Out of scope

- **Plaid changes** — already canonical-compliant via 11r-b. BUILD_LOG entry acknowledges this; no code changes to `packs/adapters/financial/plaid/`.
- **New schema registration** — `messaging.sent@v1` already exists per [D7]. NO new schema is registered in this prompt. Gmail outbound emits the existing v1 schema with field-mapping in adapter.py.
- **Field additions to `messaging.sent@v1`** — would break [D7] immutability + 47 production consumers (thank_you_detection, commitment_extraction, identity_resolution, interactions projection, Gmail inbound poll). If Gmail-specific fields like `gmail_message_id` are wanted for audit, they belong in a separate event (e.g. `external.sent` from `observation.py`'s `outbound()` already carries the result of the Gmail API response — sufficient for audit).
- **Real Gmail OAuth credentials** — Phase A uses mocks + `@pytest.mark.requires_live_services` gating for any live-Gmail assertion. Real credentials wire in at Phase B per prompt 16's §5 Assistant credentials section.
- **Plaid Link OAuth flow** — operator-side initial account linking is product-API surface (prompt 13b), not adapter-pack surface.
- **Gmail attachment handling** — defer to a future prompt. v1 `send` covers text/plain bodies via RFC 2822 MIME.
- **Gmail multi-account / multi-tenant** — Phase A is single-tenant; multi-account is Phase B.
- **Schema migration of existing events** — both `messaging.received@v1` and `messaging.sent@v1` are immutable per [D7].
- **Refactoring of any other adapter** — Cat-B and Cat-C adapters keep their post-retrofit shape.
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12.

## Approach (architectural sketch)

The real `send` lifts from a stub to a real implementation by:

1. **Imports**: `base64`, `email.mime.text.MIMEText`, `email.utils.formatdate`, `googleapiclient.errors.HttpError`, observation wrap symbols from `adminme.lib.observation`. Gmail's inbound poll already imports `googleapiclient` so the dependency is already in `pyproject.toml`.
2. **Constructor seam slots**: three new optional kwargs (`observation_session`, `observation_manager`, `observation_event_log`) defaulting to `None`; stored as `self._observation_*` attributes. Phase B supervisor populates them; Phase A tests leave None.
3. **New helper**: `_build_mime_message(message: OutboundMessage) -> str` — constructs an RFC 2822 message + returns the base64url-encoded string per the Gmail API contract.
4. **New `send` method body**:
   - Validate the `OutboundMessage` shape (`recipient`, `body` non-empty; `metadata.get("subject")` optional with placeholder default).
   - Build MIME bytes via `_build_mime_message`.
   - When seam slots populated: call `outbound(session, action="messaging.send", payload={...}, action_fn=lambda: gmail_service.users().messages().send(userId="me", body={"raw": mime_b64}).execute(), manager=..., event_log=..., target_channel="gmail", target_identifier=message.recipient)`.
   - When seam slots NOT populated (Phase A): call the Gmail service directly.
   - On success: extract `gmail_thread_id` from response; emit `messaging.sent@v1` with the existing field set mapped from Gmail semantics; return `SendResult(delivered=True, external_id=response["id"], suppressed=False)`.
   - On observation-suppressed: emit `messaging.sent@v1` with `delivery_status="queued"`; return `SendResult(delivered=False, external_id=None, suppressed=True)`.
   - On `HttpError`: classify per `AdapterErrorV1` taxonomy; emit `adapter.error@v1`; raise (supervisor retry takes over).
5. **No new schema**: c1 is a VERIFY-ONLY no-op (`registry.get("messaging.sent", 1)` returns non-None).
6. **Tests**: framework-level + pack-internal + integration covering MIME builder, send happy path, observation-suppressed path, error classification, schema field-mapping correctness.

## Deliverables

### Real Gmail `send` implementation

- `packs/adapters/messaging/gmail/adapter.py` — `send` method body replaces 11r-a's stub. `_build_mime_message` helper added. Three seam slots added to constructor. `send` routes through `adminme.lib.observation.outbound` when populated. Maps Gmail outbound to existing `messaging.sent@v1` field set.
- Net diff target: ~120-180 lines added (the bulk is the MIME builder + send body + observation wrap + error classification).

### NO new schema

- `adminme/events/schemas/ingest.py` is NOT modified. The existing `messaging.sent@v1` is used as-is.
- c1 verifies the existing schema is registered (`registry.get("messaging.sent", 1)` returns the `MessagingSentV1` model); on a positive result c1 is a NO-OP.

### Tests

- `tests/unit/adapters/messaging/test_gmail_send.py` (NEW, ~12 tests): MIME builder happy path; `send` happy path with seams populated; `send` observation-suppressed; `send` without seams (Phase A direct call); validation errors; 4xx/5xx/429 classification; field-mapping correctness for the existing v1 schema; `delivery_status` enum coverage.
- `tests/unit/adapters/messaging/test_gmail_retrofit.py` (existing post-11r-a + post-sidecar-#150) — extend with `test_send_no_longer_raises_not_implemented` + `test_send_routes_through_observation_when_seams_populated`.
- `packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py` (NEW, F-3 unique basename, ~3 tests): MIME body round-trips; send-API-call body shape; `messaging.sent@v1` schema accessible via registry post-send.
- `tests/integration/test_adapter_supervisor_gmail.py` (existing) — extend with send-path integration test.

Test floor: ≥15 new tests + ≥2 existing-test extensions.

### Documentation

- `docs/build_log.md` — append the 11a entry. Include the "Plaid acknowledgment" subsection AND the "schema reconciliation" subsection (the F-1 / Executor-pre-flight diagnosis + the option-(a) revision).

## Verification

```bash
poetry run pytest \
  tests/unit/adapters/messaging/test_gmail_send.py \
  tests/unit/adapters/messaging/test_gmail_retrofit.py \
  packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py \
  tests/integration/test_adapter_supervisor_gmail.py \
  -v

poetry run pytest -v 2>&1 | tail -5

poetry run mypy adminme/ packs/adapters/messaging/gmail/ 2>&1 | tail -5
poetry run ruff check adminme/ packs/adapters/messaging/gmail/ 2>&1 | tail -5

poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.ingest import MessagingSentV1; m = registry.get('messaging.sent', 1); assert m is MessagingSentV1; print('messaging.sent@v1 already registered; no change needed')"

poetry run adminme adapters validate packs/adapters/messaging/gmail
# Expected: exit 0, NO advisory_pending_retrofit, NO errors

bash scripts/verify_invariants.sh
# Expected: exit 0
```

## Stop

Gmail's real outbound is wired through observation-mode suppression. The existing `messaging.sent@v1` schema is reused (no new registration). The Cat-A real-outbound template is established for subsequent Cohort D prompts. Plaid is acknowledged as canonical-compliant via 11r-b's retrofit. Open the PR per the standard four-commit structure. Do NOT proceed to canonical 11b without operator review of the 11a merge.

After merge: Partner runs three-job QC, opens the partner-state-snapshot PR.
