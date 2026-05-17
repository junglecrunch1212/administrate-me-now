# Prompt 11a: Gmail real outbound + Plaid central acknowledgment (Cohort D step 1)

**Phase:** Phase A — L1 Cohort D step 1 (first canonical-build prompt after Cohort C retrofits closed). Per `prompts/PROMPT_SEQUENCE.md` row 116 + `docs/08-architecture-amendment-adapter-rebase.md` §3.4.

**Depends on:** Cohort B closed (PRs #118-#121 / #123-#126 / #128-#131). Cohort C closed (PRs #133-#136 / #139-#142 / #145-#148 + sidecar #150). All three shipped v1 packs (Gmail / Plaid / Reminders) framework-conformant.

**Estimated duration:** 3-4 hours. **NOT a pre-split candidate** — scope is narrow post-Cohort-C (real Gmail `send` + observation wrap + tests; Plaid already done via 11r-b).

**Stop condition:** Gmail's `send` method is a real implementation (no longer the 11r-a stub), routes through `adminme.lib.observation.outbound` per [§6.20], passes observation-mode suppression tests, and `adminme adapters validate packs/adapters/messaging/gmail` continues to exit 0 with NO `advisory_pending_retrofit` finding.

---

## Read first

1. `docs/08-architecture-amendment-adapter-rebase.md` §3.4 — full read. Defines what canonical 11a is and isn't, including the explicit option-A/B decision deferred to author-time (resolved here: option A, Gmail real outbound; Plaid skipped since it's already done via 11r-b).
2. `prompts/PROMPT_SEQUENCE.md` row 116 — the canonical 11a spec. Note: "Gmail + Plaid emit into event log via the new framework" is the Verifies column. **Plaid already meets this post-11r-b.** Gmail's inbound poll already emits `messaging.received` events via the post-retrofit framework. What's NEW in canonical 11a is the **outbound surface** — real Gmail API `send` implementation establishing the Cat-A real-outbound template for subsequent prompts (11b Cat-B AppleScript writeback; 11g Cat-E HA service-call).
3. `docs/05-architecture-amendment-personal-data-layer.md` — sections:
   - §1.1 (Cat-A definition — bidirectional messaging)
   - §3.2 (`CommunicationAdapter` ABC contract — `send(message) -> SendResult` is the outbound surface)
   - §6.20 (observation-mode wrapping rule — outbound side effects route through `adminme.lib.observation.outbound`)
4. `prompts/_active/11r-a-retrofit-gmail-to-communication-adapter.md` (audit trail) — the retrofit that added the `send` stub this prompt replaces.
5. `packs/adapters/messaging/gmail/adapter.py` (on main) — the post-retrofit Gmail adapter. `send` is currently a stub raising `NotImplementedError` or returning a deferred sentinel — verify exact shape at orientation. The inbound poll + auth + supervisor wiring stays unchanged.
6. `packs/adapters/messaging/gmail/pack.yaml` (on main) — Cat-A capabilities + `observation_mode_required: true` + `sharing_model_discriminator: owner_member_id` + `dedup_strategy: {kind: external_id}`. NO change needed in this prompt — the manifest already supports outbound.
7. `adminme/lib/adapters/communication_base.py` (on main) — the Cat-A ABC. `send(message: OutboundMessage) -> SendResult` is the abstract method to implement; `_on_inbound(envelope: InboundEnvelope)` is the inbound hook (already correctly satisfied by the post-retrofit Gmail's inbound poll path).
8. `adminme/lib/observation.py` (on main) — `outbound(*, observation_mode_required, would_have_side_effect, perform)`. The real `send` MUST call this BEFORE invoking the Gmail API client.
9. `packs/adapters/reminders/apple-eventkit/adapter.py::_apply_write_impl` (on main) — Cat-B precedent for the observation-wrap pattern. Cat-A's real `send` uses the same wrap shape applied to a Cat-A `send(message)` call instead of a `_apply_write_impl(operation)` call.
10. `docs/reference/gmail-api/` (on main, if it exists post-prompt-00.5) — Gmail API reference documentation mirror. The real `send` calls `users.messages.send` per Gmail API v1.

## Operating context

Canonical 11a is the **first non-retrofit Cohort D prompt** — the pattern shift from "inheritance + manifest only" (Cohort C) to "full event-sourcing build" (Cohort D). For 11a, the scope is intentionally narrow because the Cohort C retrofits already accomplished most of canonical 11a's stated targets:

- **Gmail framework conformance:** done via 11r-a (PRs #133-#136)
- **Plaid framework conformance:** done via 11r-b (PRs #139-#142)
- **`adapter.*` cross-cutting schemas:** done via 11-α
- **Adapter supervisor wiring:** done via 11-γ
- **`adminme adapters` CLI:** done via 11-γ

What canonical 11a adds is the **real outbound implementation** for Gmail — replacing the `send` stub the 11r-a retrofit deferred per its anti-vision halt (memo 08 §5: "inheritance + manifest only, not a rewrite"). This is the **template-setting work** that 11b (Cat-B AppleScript writeback) and 11g (Cat-E HA service-call) will follow when they build real outbound paths.

**Plaid is explicitly out of scope.** Cat-C has no write surface, and the post-11r-b Plaid is already fully event-sourced (existing webhook + sync flow preserved byte-for-byte). The "Plaid central acknowledgment" in this prompt's title is a documentation-only acknowledgment in BUILD_LOG that Plaid's canonical 11a obligations were met via the Cohort C step 2 retrofit.

## Objective

Implement Gmail's real `send` method in `packs/adapters/messaging/gmail/adapter.py`, replacing the 11r-a stub. The implementation:

- Calls Google's Gmail API v1 `users.messages.send` endpoint via the `google-api-python-client` library (or equivalent — verify the import surface in `pyproject.toml` at orientation).
- Routes the actual API call through `adminme.lib.observation.outbound(observation_mode_required=True, would_have_side_effect=...)` per [§6.20]. When observation mode is active, the wrap suppresses the API call and emits `observation.suppressed@v1`; when observation mode is off, the wrap calls the Gmail API and the adapter emits `messaging.sent@v1` on success.
- Handles auth failures, rate-limit responses, and transient network errors per the `AdapterErrorV1` classification taxonomy (same shape Plaid/Gmail/Reminders use for `adapter.error`).
- Adds a `messaging.sent@v1` event schema if one doesn't already exist (verify at orientation; Gmail's inbound emits `messaging.received@v1` which is registered).
- Preserves all existing Gmail adapter behavior byte-for-byte: inbound poll + auth flow + cursor-advance + supervisor lifecycle.

## Out of scope

- **Plaid changes** — already canonical-compliant via 11r-b. BUILD_LOG entry acknowledges this; no code changes to `packs/adapters/financial/plaid/`.
- **Real Gmail OAuth credentials** — Phase A uses mocks + `@pytest.mark.requires_live_services` gating for any live-Gmail assertion. Real credentials wire in at Phase B per prompt 16's `:5 Assistant credentials` section.
- **Plaid Link OAuth flow** — operator-side initial account linking is product-API surface (prompt 13b), not adapter-pack surface.
- **Gmail attachment handling** — defer to a future prompt. v1 `send` covers text/plain + text/html bodies via RFC 2822 MIME.
- **Gmail multi-account / multi-tenant** — Phase A is single-tenant; multi-account is Phase B.
- **Schema migration of existing events** — `messaging.received@v1` is immutable per [D7]. The new `messaging.sent@v1` is ADDITIVE (separate schema name).
- **Refactoring of any other adapter** — Cat-B and Cat-C adapters keep their post-retrofit shape.
- **OpenClaw plugin messaging adapters** (BlueBubbles, Telegram, Discord) — prompt 12.

## Approach (architectural sketch)

The real `send` lifts from a stub to a real implementation by:

1. **Imports**: `googleapiclient.discovery` for the Gmail API client, `googleapiclient.errors` for `HttpError`, `email.mime` for MIME construction. Verify these are in `pyproject.toml` at orientation (Gmail's inbound poll already imports `googleapiclient`).
2. **New helper**: `_build_mime_message(message: OutboundMessage) -> bytes` — constructs an RFC 2822 message with `From: <member_email>`, `To: <recipient>`, `Subject: <subject>`, `Date:`, and the body. Returns base64-encoded bytes per the Gmail API contract.
3. **New `send` method body**:
   - Validate the `OutboundMessage` shape (recipient + subject + body present; otherwise raise `ValueError` before observation wrap).
   - Build the MIME bytes via `_build_mime_message`.
   - Call `outbound(observation_mode_required=True, would_have_side_effect={"adapter": "messaging-gmail-api", "recipient": message.recipient, ...}, perform=lambda: gmail_service.users().messages().send(userId="me", body={"raw": mime_bytes}).execute())`.
   - On the returned dict from the Gmail API, extract the message ID (`response["id"]`); construct a `SendResult(applied=True, external_id=response["id"], suppressed=False)`.
   - On observation-suppressed: return `SendResult(applied=False, external_id=None, suppressed=True)` and DO NOT emit `messaging.sent`.
   - On `HttpError`: classify into the `AdapterErrorV1` taxonomy (4xx auth → fatal; 5xx + 429 → transient with retry-after); emit `adapter.error@v1` via the existing `_emit_error` helper; raise so the supervisor's retry logic engages.
4. **New schema**: `messaging.sent@v1` in `adminme/events/schemas/ingest.py` (or a new `messaging.py` if cleaner — verify at orientation). Payload: `{member_id, recipient, subject, body_preview, sent_at, external_id, correlation_id}`. Register at v1 per [D7].
5. **Tests**: pack-internal tests for the MIME builder + send happy path + send observation-suppressed path + send 4xx/5xx classification. Framework-level test exercising `apply_write(operation)`-style flow through the Cat-A ABC's `send` gate (analogous to the Reminders Cat-B `apply_write` test pattern from 11r-c).

## Deliverables

### Real Gmail `send` implementation

- `packs/adapters/messaging/gmail/adapter.py` — `send` method body replaces 11r-a's stub. `_build_mime_message` helper added. `send` routes through `adminme.lib.observation.outbound` per [§6.20]. Existing methods preserved byte-for-byte.
- Net diff target: ~120-180 lines added (the bulk is the MIME builder + send body + observation wrap + error classification).

### Schema registration

- `adminme/events/schemas/ingest.py` (or sibling module) — `MessagingSentV1` registered at v1 per [D7]. Payload contains `member_id`, `recipient`, `subject`, `body_preview` (truncated to 200 chars per `[§6.13]`-style privacy heuristic), `sent_at`, `external_id` (Gmail message ID), `correlation_id`. Registry registration at module import time.

### Tests

- `tests/unit/adapters/messaging/test_gmail_send.py` (NEW, ~10 tests): MIME builder happy path; MIME builder with HTML body; `send` happy path (returns `SendResult(applied=True, external_id=...)`); `send` observation-suppressed path (returns `SendResult(applied=False, suppressed=True)`, NO `messaging.sent` emit); `send` 4xx auth failure → fatal `adapter.error`; `send` 5xx → transient `adapter.error` with retry-after; `send` 429 → transient with retry-after; `send` validates `OutboundMessage` shape; `send` truncates `body_preview` to 200 chars; `send` includes correlation_id in emit.
- `tests/unit/adapters/messaging/test_gmail_retrofit.py` (existing post-11r-a) — extend with 2 new tests: `test_send_routes_through_observation_outbound`; `test_send_no_longer_raises_not_implemented` (the stub is replaced).
- `packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py` (NEW, F-3 unique basename, ~3 tests): MIME body matches RFC 2822 shape; send-API-call body matches `{"raw": base64-bytes}` shape; new `messaging.sent@v1` schema registers without conflict.
- `tests/integration/test_adapter_supervisor_gmail.py` (existing) — extend: integration test exercising the full send path through the supervisor (mock Gmail service + observation-mode toggle).

Test floor: ≥15 new tests + ≥2 existing-test extensions.

### Documentation

- `docs/build_log.md` — append the 11a entry. Include the "Plaid acknowledgment" section: explicit note that Plaid is canonical-11a-compliant via 11r-b's Cohort C step 2 retrofit; no code changes to Plaid in this prompt.

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

poetry run adminme adapters validate packs/adapters/messaging/gmail
# Expected: exit 0, NO advisory_pending_retrofit, NO errors

bash scripts/verify_invariants.sh
# Expected: exit 0
```

## Stop

Gmail's real outbound is wired through observation-mode suppression. The Cat-A real-outbound template is established for subsequent Cohort D prompts to follow. Plaid is acknowledged as canonical-compliant via 11r-b's retrofit. Open the PR per the standard four-commit structure. Do NOT proceed to canonical 11b (Cat-B cohort — Apple Reminders dual-deployment + Apple Calendar dual + Google Calendar + Google Tasks) without operator review of the 11a merge — 11b is a pre-split candidate per PROMPT_SEQUENCE.md row 117 + memo 05 §6.6 and warrants a separate split-memo cycle.

After merge: Partner runs three-job QC on the merged state, then opens the partner-state-snapshot PR (PROMPT_SEQUENCE.md Pause rule updated; partner_handoff.md new Last-updated paragraph; build_log.md placeholder patch on the 11a entry's PR-numbers + commit shas).
