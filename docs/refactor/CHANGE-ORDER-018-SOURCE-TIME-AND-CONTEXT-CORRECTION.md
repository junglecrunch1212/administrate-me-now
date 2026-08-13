# Change Order 018 — Source-time and conservative context correction

Status: PR #338 merged as `5b6c140`, but guarded activation rejected v21 and
automatically restored `d181fad`. The release exposed a concurrent exact-skill-
result append race that produced four terminal `language_seam_IdempotencyConflict`
assessments. While that forward repair was being validated, the restored v20
consumer reached another retained replay collision, poisoned at exact source
`ev_01KZV4FY0B0000`, and accumulated a bounded post-checkpoint message
backlog; one older allowlisted `language_seam_OpenClawUnreachable` assessment
also remains current. A forward-only repair candidate is in progress;
deployment remains incomplete and requires fresh independent review and
exact-head authority.

Governing sources: event-source authority; D19, D26, and D28; ADR-0008 and
ADR-0010; C002, C025, C034, C097, C106–C110, C114; AC-08, AC-09, AC-18,
AC-24, AC-33–AC-39, and AC-43.

## Incident class

A newly ingested historical message could display its ingestion timestamp as
if it were the message date. Context assembly could then connect a different
provider thread through weak participant, address, subject, filename, or
authored-name overlap. Quoted or forwarded history could also survive when its
boundary began at byte zero. The resulting Triage item remained a proposal,
but its explanation and evidence chain could falsely imply that an old,
unrelated exchange initialized a current matter.

This is a provenance and context-admission defect. It is not evidence that the
source fact, sender identity, provider account, or household name is wrong.

## Required behavior

- A provider message's envelope `occurred_at` is the provider message time;
  append time remains `recorded_at`. Triage labels both when they differ. A
  missing or invalid Gmail `internalDate` is quarantined rather than replaced
  with ingestion wall-clock time.
- Matter and provider-thread episodes use source time, span at most 30 elapsed
  days and 100 sources, and split conservatively when a provider reuses a
  thread identifier.
- A cross-thread source requires exact RFC reply/reference continuity, exact
  attachment identity/digest, or an explicit member confirmation. Participant,
  email, repeated household/business names, subject tokens, and attachment
  filenames are corroboration only and can never admit a source alone.
  Every admitted related source records the exact primary source event(s) that
  supplied its edge, and dismissal rejects those pairs even after a later
  same-thread anchor arrives. Gmail message-local attachment IDs are not stable
  identities; Gmail attachment admission remains unavailable without a digest
  or genuinely stable provider file identity.
- `authored-message-text-v2` is the one canonical body representation for
  model input, deterministic fallback, exact-quote admission, and read-time
  evidence. Recognized reply/forward boundaries at offset zero produce empty
  authored content and therefore no candidate. HTML-only content is
  canonicalized before the empty-body check. For multipart messages, an
  explicit HTML quote boundary constrains the plain alternative; mismatched
  alternatives fail closed. An unrecognized quote signal is terminally
  classified as uncertain and never sent to the language seam.
  Retained Gmail `messaging.sent@v1-v3` bodies lack the HTML alternative and
  therefore fail closed with the explicit reason
  `legacy_gmail_sent_quote_boundary_unavailable`; replay neither migrates the
  fact nor admits its body as authored evidence. Any untouched system proposal
  previously rooted in that unreliable body receives an append-only
  `eligibility_rule_replaced` supersession; member enrichment and decisions
  remain permanent. Additive `triage.item.superseded@v3` carries the exact
  proposal revision inspected by replay, and the Triage projection applies it
  only when that revision is still current.
- A wrong-context dismissal may atomically reject selected exact source pairs
  and/or mark one exact proposal/candidate evidence span as quoted/forwarded
  history. Other authored asks in the same source remain available.
  Corrections are private, member-owned, provider/account/workspace checked,
  guarded, audited, replayable, stamped with their context algorithm, and
  reversible through explicit confirmation or reinstatement events.
  Triage offers each correction control only when the server declares that its
  exact relationship provenance or uniquely grounded authored evidence is
  sufficient for the corresponding command. Exact admitting sources outside
  the displayed model window are still independently authorized and hydrated.
- The 12-message / 24,000-character language payload reserves space for the
  latest anchor and retains the source currently being assessed. Full-replay
  proposal reconciliation uses every source in the bounded 30-day/100-source
  episode, so an old untouched proposal cannot survive merely because it fell
  outside the model window.
- A subject displayed beside `Provider Thread Id` is only a readable source
  label; it is not the opaque provider thread identifier or relationship
  authority.
- Wrong-context feedback does not create a broad sender, account, workspace,
  keyword, or household-name suppression rule. Repeated generic feedback
  remains an explicit, previewed, reversible, shadow-only rule path.
- Noise filtering generation 21 performs a full reviewed replay. It appends
  newer assessments/proposals and retires only untouched stale interpretations;
  an unchanged context-free deterministic fallback that remains eligible under
  the current high-signal rule retains its existing pending proposal instead
  of being superseded and suppressed by its historical candidate key;
  if an otherwise identical fallback carries stale contextual interpretation,
  replay supersedes that old proposal and emits a distinct context-free
  replacement rather than allowing historical dedupe to erase the valid ask;
  projection admits that replacement only if the revision-bound supersession
  actually retired the predecessor with the exact system
  `eligibility_rule_replaced` disposition, so enrichment, dismissal,
  confirmation, or a later terminal state that won append order cannot be
  followed by duplicate or reopened review work;
  a member enrichment, dismissal, confirmation, or materialization remains
  permanent.
- Concurrent calls with the same reviewed skill invocation identity converge
  on the append-only log's first recorded result. A losing exact-result append
  re-reads and returns that canonical result instead of converting a successful
  replay into a terminal language failure.
- The guarded source-repair exception is rebound to the exact rolled-back
  `d181fad` baseline, the four content-free release-created
  `language_seam_IdempotencyConflict` targets, and one exact allowlisted
  `language_seam_OpenClawUnreachable` target. It also binds the restored
  v20 consumer's exact version, checkpoint, poison event, and five-failure
  threshold. Up to 100 ordinary `messaging.received` obligations may remain
  behind that exact poison while review proceeds; every such missing record
  must be later than the poison and carry only the canonical
  after-checkpoint/grace reason. It requires observation mode, zero pending,
  no unrelated degradation, stopped-consumer and Gateway quiescence proof, and
  expires after activation. Doctor may additionally report
  `required_reactive_pipeline_delivery` and
  `launchd_and_process_state` only when they match that exact paused-consumer
  and source-assurance state; any other failed Doctor check or degraded
  capability still rejects activation.

## Classification

| Change | Classification | Boundary preserved |
|---|---|---|
| Gmail received/sent source-time envelopes and RFC continuity schemas | MODERNIZE | Provider fact remains immutable; `recorded_at` remains append truth. |
| `message-context-pack-v2` strong-edge admission and episode identity | SUPERSEDE | Privacy coordinates and provider sources remain code-owned; the model cannot widen them. |
| Canonical authored-text v2 | SUPERSEDE | Exact provider text remains the source; derived evidence uses one normalized view. |
| Exact relationship/text correction and reversal events | MODERNIZE | Correction is append-only member evidence, not deletion or mutation of the source. |
| Revision-bound system supersession v3 | HARDEN | Projection compare-and-apply makes append order authoritative when replay races a member enrichment or decision. |
| Feedback aggregation narrowing | SUPERSEDE | No automatic suppression, confirmation, routing, canonical write, or provider effect. |
| Pipeline v21 full replay | MODERNIZE | Existing events and member decisions are retained; only untouched stale proposals retire. An unchanged context-free fallback stays pending; a contextualized fallback with stale context is replaced under a distinct replay-versioned proposal identity so its deterministic core remains reviewable. |
| Exact skill-result append race and release-scoped recovery | HARDEN | One durable result wins for an exact invocation; failed assessment history remains immutable and only the five declared targets receive linked recovery requests. The exact restored-v20 poison and bounded later message backlog are admitted only for candidate v21 settlement. |

No required function is deferred. The change adds no provider SDK, model
router, central runtime, projection authority, canonical object, or provider
write.

## Rollback and compatibility

Rollback restores Gmail adapter v1.1.1, message-context v1, pipeline v20, and
skill v6 behavior. It does not delete v4 received events, v4 sent events, v4
Triage proposals, correction/reversal events, feedback, or supersession
receipts already appended. Older schema versions remain registered and
readable. The older consumer ignores new correction events; a later v21 replay
reapplies them. Because rollback can reintroduce weak context assembly, it is
operationally compatible but not a correctness-equivalent steady state.
The first guarded activation proved that selector rollback does not erase
events already appended by a candidate. Its four failed v21 assessments remain
immutable under `d181fad`; the restored v20 replay subsequently reached the
exact declared poison and the older transient target remains failed. The
follow-up must repair all five forward through the typed recovery contract and
settle the bounded later backlog rather than restoring an older event-log
snapshot or editing a checkpoint.

## Acceptance evidence

AC-43 is implemented by `tests/acceptance/test_context_correctness_vertical_slice.py`,
`tests/unit/test_message_context.py`, `tests/unit/test_pipeline_noise_filtering.py`,
`tests/unit/test_projection_context_fabric.py`, Gmail adapter/supervisor tests,
and the ordinary replay, privacy, governance, browser, package, and release
gates. The process slice uses the actual Gmail adapter mapping, seeds a v20
durable checkpoint, starts the production v21 runner, proves full replay,
retires both the untouched crossed-wire proposal and an untouched false legacy-
Sent proposal, keeps a newly derived valid sibling reviewable, and replaces a
still-eligible v20 same-source fallback's stale contextual interpretation with
a distinct context-free pending proposal. It preserves member-decided history
and creates no canonical or provider effect. A second process case
appends member enrichment after system inspection but before its v3
supersession and proves the item remains reviewable while the subsequent
replacement event is projection-suppressed. Parallel dismissal and confirmation
cases prove terminal member decisions remain terminal through the same append
window. Candidate evidence is recorded in
`docs/refactor/evidence/co-018-source-time-and-context-correction.md`.
