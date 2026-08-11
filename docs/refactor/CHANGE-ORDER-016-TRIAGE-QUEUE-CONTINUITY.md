# Change Order 016 — Triage queue continuity

Status: implementation candidate; not merged or deployed.

Governing sources: product thesis; CO-014 and CO-015; C097, C101, C107,
C110–C112; UX-49–UX-59; AC-39–AC-41.

## Incident

After a member selected **Spam** and **Dismiss & teach** for one message,
Triage retained the resolved candidate as exact focus. The subsequent API read
correctly reported that one item as resolved, but browser code interpreted the
response as an instruction to change the entire work surface to **Recent**.
That lane contained many legitimate historical `eligibility_rule_replaced`
supersession receipts. The same reason code covers both reviewed eligibility
evolution and ordinary replacement by newer conversation context; it does not
prove migration provenance. The member therefore saw unrelated read-only
receipts instead of the next manageable candidate and reasonably understood
them as an effect of the dismissal.

The feedback system itself remained bounded: no shadow rule was created, no
other candidate was dismissed, and the unresolved inventory remained intact.
The defect was presentation state and authority legibility.

## Required behavior

- A successful confirm or dismiss consumes the resolved item as browser focus.
- Candidate and matter query parameters are cleared before reload.
- The active working lens remains selected and the next visible matter becomes
  manageable when one remains. If none remains, feedback truthfully says that
  the refreshed lens is caught up; a decision never implicitly opens Recent.
- An exact external link to a resolved candidate still opens its immutable
  Recent receipt.
- Recent is plainly read-only audit history.
- A deliberate lens change clears prior lens-relative continuation feedback so
  it cannot describe the newly selected lens.
- Reselecting the active lens preserves its continuation feedback. If the
  decision write succeeds but refresh fails, the warning and stale controls
  remain latched until a successful reload.
- Beginning a decision or deliberately changing lenses invalidates older
  in-flight pagination. A late page response, successful or failed, cannot
  overwrite the authoritative post-decision surface, resurrect a resolved
  candidate, replace feedback, or restore stale action controls.
- For the entire unresolved decision interval, load-more is visibly disabled
  and its handler rejects even a programmatic request. The generation rotates
  again before ordinary interaction reopens, so pagination attempted after the
  decision began cannot share the generation of the authoritative refresh.
- `eligibility_rule_replaced` is rendered as context-or-eligibility
  replacement, not as a proven migration or rule created by the current
  dismissal.
- Reversible feedback, rule suggestions, shadow-rule approval/revocation,
  events, projections, member isolation, and provider governance are unchanged.

## Classification and boundary

| Change | Classification | Boundary preserved |
|---|---|---|
| Consume exact focus after successful decision | HARDEN | Client presentation only; the audited result remains immutable and exactly linkable. |
| Preserve the active working lens and choose the next visible matter | MODERNIZE | Deterministic existing ordering; no model rank, hidden suppression, or new queue store. |
| Clear lens-relative feedback on deliberate lens change | HARDEN | Browser presentation only; the decision and feedback receipts remain immutable. |
| Latch a recorded-decision refresh failure until successful reload | HARDEN | Stale browser data cannot regain decision controls; the already-recorded decision is not retried or rewritten. |
| Invalidate pre-decision pagination before a decision or lens change | HARDEN | Browser request ordering only; late transport results cannot supersede a newer projection read or mutate durable truth. |
| Close pagination for the full decision interval and rotate before reopening | HARDEN | Ephemeral browser concurrency only; no queue, event, projection, or provider authority changes. |
| Relabel eligibility supersession receipts without inferring provenance | HARDEN | Copy changes; event reason and projection state are not rewritten. |
| Keep resolved deep links read-only and exact | PRESERVE | CO-015 scope checks and no-substitution behavior remain authoritative. |

No schema, migration, dependency, runtime, adapter, pipeline, model, canonical
object, provider object, projection count, or external-write path changes.

## Acceptance

AC-41 requires production-composition browser proof for confirm continuity,
typed spam-dismiss continuity, next-matter selection, candidate/matter URL
cleanup, a final-Ready-item caught-up state, explicit member navigation to Recent,
actual-change-only continuation cleanup, a forced post-write refresh failure
that keeps stale controls locked through lens clicks until reload,
a held confirmation followed by an attempted load-more proving that pagination
is disabled and transport-rejected until the authoritative refresh settles,
provenance-bounded lifecycle-receipt copy, and preserved destination receipts.
Existing Triage projection/API, feedback, learning-policy, replay,
privacy/isolation, release, and observation gates must remain green.
