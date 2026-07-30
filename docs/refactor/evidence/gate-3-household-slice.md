# Gate 3 household-slice evidence

Status: **PASS** (local verification evidence is finalized in `../HANDOFF.md`).

## Fixture contract

- Cat-A adapter: `fixture-messaging` (`messaging.received` v2).
- Principal: `member_fixture_principal`; external Party: `party_fixture_sender`.
- Identifiers: `principal@fixture.invalid` and `sender@fixture.invalid`.
- OpenClaw seam: the normal `run_skill` wrapper posts `tool: llm-task` through an in-memory HTTP transport. Recorded actual provider/model is `fixture/deterministic-v1`; no provider SDK or network is used.
- Golden message: `Could you reply today with the fixture household update?`
- Golden extraction: `kind=reply`, summary `Reply to the household message`, due `2026-08-01T17:00:00Z`, confidence `0.96`, urgency `today`.
- Initial output: one normalized Interaction, Party-resolved sender, and one unconfirmed `proposal_only` commitment. No commitment is confirmed until a principal calls the approve route.

The nonprogrammer entry point is:

```powershell
$env:PYTHONPATH = "bridge"
python -m adminme.cli.main demo canonical-message
```

The CLI invokes the same `POST /api/comms/v1/demo/canonical-message` route as process acceptance. It is not a parallel ingestion implementation.

## Versioned routes exercised

- `POST /api/comms/v1/demo/canonical-message`
- `GET /api/comms/v1/interactions`
- `GET /api/comms/v1/interactions/{id}/evidence`
- `GET /api/comms/v1/commitments/pending`
- `POST /api/comms/v1/commitments/{id}/edit|approve|dismiss`
- `GET /api/core/v1/inbox`
- `GET /api/core/v1/commitments/active`
- `POST /api/core/v1/commitments/{id}/complete|defer|snooze|block|unblock`
- `GET /api/core/v1/what-now`

Requests are Pydantic `extra=forbid` contracts. Identity comes from the trusted loopback/Tailscale ingress mapping. `authMember` authorizes commands; `viewMember` only selects readable data. Commands carry request idempotency, a durable request-body fingerprint, and optimistic `expected_last_event_id` values. Inbox merges only scope-filtered proposals and tasks at read time; their projections remain separate.

## Adversarial matrix

| Case | Expected result |
|---|---|
| Same provider-native message/request repeated | Original event ids returned; one semantic Interaction/proposal and no repeated skill call. |
| Same idempotency key with different event identity/payload | `IdempotencyConflict`; no second append. |
| Same command request id with a changed body | 409; the first immutable command remains authoritative. |
| Missing identity or malformed API body | 401 or 422; no product write. |
| Stale lifecycle edit | 409 with current event id; no projection mutation. |
| Child, wrong principal, or principal view-as another member | Private proposal absent; child command denied. |
| Privileged communication | Interaction remains owner-scoped; all downstream LLM intelligence is bypassed and audited as privileged suppression. |
| Malformed, unavailable, or timed-out fixture response | Input remains durable; pipeline records failure/defensive suppression; no proposal. |
| Projection read temporarily unavailable | Typed 503 response; the durable log remains authoritative. |
| Old proposal redelivered after completion | `ON CONFLICT DO NOTHING` plus monotonic event guards preserve completed state. |
| Old Interaction input redelivered after participant resolution | Monotonic guards preserve the Party id and latest provenance event. |
| Crash after append and before notify | Restarted durable bus discovers the event from its checkpoint and projects it. |
| Projection rebuild and workbook regeneration | Commitment semantics and protected provenance columns remain equivalent. |
| Observation mode active on approved delivery intent | `observation.suppressed` is appended and fixture transport is not called. |

## What-now policy

`deterministic-ranker-v1` returns exactly six ordered horizons: `right_now`, `next`, `today`, `this_week`, `this_month`, and `someday`. A confirmed active interpersonal obligation outranks the seeded household task. Blocked, snoozed, deferred, dismissed, and completed commitments are not actionable; after completion the seeded task becomes `right_now`. Each recommendation includes obligation, urgency, owner, dependency, energy, and effort reasons.

At the Gate 3 handoff, the next product slice was member-private knowledge. The subsequently issued Prompt 4 inserted the current OpenClaw seam as Gate 4; that unchanged member-knowledge slice is now Phase 5 and remains limited to `test_member_bridge_knowledge_vertical_slice.py`.
