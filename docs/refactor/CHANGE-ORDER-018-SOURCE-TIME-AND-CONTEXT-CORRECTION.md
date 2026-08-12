# Change Order 018 — Source-time and conservative context correction

Status: implemented and locally validated candidate; not reviewed, merged, or
deployed.

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
  a member enrichment, dismissal, confirmation, or materialization remains
  permanent.

## Classification

| Change | Classification | Boundary preserved |
|---|---|---|
| Gmail received/sent source-time envelopes and RFC continuity schemas | MODERNIZE | Provider fact remains immutable; `recorded_at` remains append truth. |
| `message-context-pack-v2` strong-edge admission and episode identity | SUPERSEDE | Privacy coordinates and provider sources remain code-owned; the model cannot widen them. |
| Canonical authored-text v2 | SUPERSEDE | Exact provider text remains the source; derived evidence uses one normalized view. |
| Exact relationship/text correction and reversal events | MODERNIZE | Correction is append-only member evidence, not deletion or mutation of the source. |
| Feedback aggregation narrowing | SUPERSEDE | No automatic suppression, confirmation, routing, canonical write, or provider effect. |
| Pipeline v21 full replay | MODERNIZE | Existing events and member decisions are retained; only untouched stale proposals retire. |

No required function is deferred. The change adds no provider SDK, model
router, central runtime, projection authority, canonical object, or provider
write.

## Rollback and compatibility

Rollback restores Gmail adapter v1.1.1, message-context v1, pipeline v20, and
skill v6 behavior. It does not delete v4 received events, v3 sent events, v4
Triage proposals, correction/reversal events, feedback, or supersession
receipts already appended. Older schema versions remain registered and
readable. The older consumer ignores new correction events; a later v21 replay
reapplies them. Because rollback can reintroduce weak context assembly, it is
operationally compatible but not a correctness-equivalent steady state.

## Acceptance evidence

AC-43 is implemented by `tests/acceptance/test_context_correctness_vertical_slice.py`,
`tests/unit/test_message_context.py`, `tests/unit/test_pipeline_noise_filtering.py`,
`tests/unit/test_projection_context_fabric.py`, Gmail adapter/supervisor tests,
and the ordinary replay, privacy, governance, browser, package, and release
gates. The process slice uses the actual Gmail adapter mapping, seeds a v20
durable checkpoint, starts the production v21 runner, proves full replay,
retires the untouched crossed-wire proposal, keeps a valid sibling reviewable,
and creates no canonical or provider effect. Candidate evidence is recorded in
`docs/refactor/evidence/co-018-source-time-and-context-correction.md`.
