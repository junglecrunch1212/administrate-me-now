# Change Order 016 — Triage queue continuity

Status: PR #334 candidate `40f1c27a1259b35c263cf980fbbb9cf3e4f5d239`
merged as `2d739ad2e89a49bf330a854317326482907b956d` and is an ancestor
of active observation release `d181fad24ef488a71ce5568cc40fb81309cb3817`.

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
  it cannot describe the newly selected lens. Progress, success, and failure
  messages carry their own presentation kind and are never cleared as stale
  continuation feedback.
- Reselecting the active lens preserves its continuation feedback. If the
  decision write succeeds but refresh fails, the warning and stale controls
  remain latched until a successful reload.
- Beginning a decision or deliberately changing lenses invalidates older
  in-flight pagination. A late page response, successful or failed, cannot
  overwrite the authoritative post-decision surface, resurrect a resolved
  candidate, replace feedback, or restore stale action controls.
- A single serialized Triage operation begins before enrichment in a dirty
  **Save & confirm** flow and remains held through the decision and authoritative
  refresh. Every Triage control—including the shared-shell **Add for review**
  button and any retained Add-for-review dialog—is visibly disabled; lens,
  matter, enrichment, intent, learning-policy, decision, and load-more handlers
  reject programmatic work; native Escape cannot dismiss a pending intent form;
  and any operation-time render inherits the same lock. Invalidating an already-loading page first restores the load-more
  control's semantic state, so a no-render failure cannot strand transient
  “Loading…” state. No earlier Triage callback may overwrite feedback or
  refresh. The generations rotate again before ordinary interaction reopens.
- If enrichment succeeds and the following decision fails, the saved values and
  current revision are authoritatively refreshed, remain visible, and are
  immediately retryable. Enrichment responses include the computed review
  state; if an edit makes a candidate unready, the client moves it to the
  appropriate review lens and does not attempt confirmation. A recorded-write
  refresh failure can be cleared only by a new page load, never by an older
  callback or shared-shell intent.
- Reconciliation always requests the exact candidate independently of the
  ordinary cursor, retains that identity in its current deterministic review
  lens—including All candidates after an evidence-only/attach-existing
  transition—and never substitutes a first-page matter. HTTP class alone is
  never settlement proof: every confirmation requires the exact immutable
  confirmation event and request ID; local confirmation also requires its materialization
  receipt; an external route requires its durable pending-action receipt; and
  provider completion/failure requires its outcome receipt. A definite
  client-visible rejection may say no decision was recorded only while the
  exact candidate remains pending. An interrupted or post-confirmation failure
  retries once with the original request ID when continuation is required,
  then reports proven action-specific truth or fails closed until reload.
- A confirmation or dismissal is reconciled only from the exact immutable
  member-action event and request ID. A different request, system supersession,
  or unrelated resolved state cannot masquerade as the requested action.
- Evidence-only and attach-existing candidates never advertise or accept
  confirmation. They remain reviewable evidence and cannot create a duplicate
  canonical object even if a stale or forged client calls the approval route.
- Add for review owns a surface-independent operation token. Cancel, Close,
  Escape, repeat submission, and a stale callback cannot discard or replace
  the form on Today or any other member surface. Ambiguous capture retries once
  with the original client request ID under a server-side per-intent lock; a
  still-ambiguous result retains the exact visible words and presents an enabled
  focused action inside the dialog. When the server proves durable intent and
  features, that action opens the exact saved Needs interpretation item from
  any source surface; when the transport provides no coordinates, it opens
  All candidates so the member checks Triage before resubmitting. A definite
  unprovenanced client 4xx is handled as pre-capture and is
  not retried. A server response that explicitly attests
  `intent_and_features_recorded` is treated as post-capture because proposal
  governance can fail after both immutable events were appended.
- Once intent features are recorded, retries rehydrate proposals only from
  that durable feature event; the language seam is not invoked again and
  cannot produce a second interpretation or proposal family. All missing
  children are governance-preflighted before one atomic batch append, so a
  denial cannot strand a partial family or prematurely dismiss its holding row.
- Approval is authorized by the projection's acceptance of the exact
  confirmation request, not event append alone. If matching canonical truth
  arrives between the API read and confirmation, the candidate becomes
  attach-existing, the confirmation transition fails closed, and no duplicate
  materialization or provider-action receipt is created.
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
| Normalize invalidated pagination before capturing semantic disabled state | HARDEN | Ephemeral browser state only; a no-render error cannot preserve a transient loading lock. |
| Serialize the full Triage save/intent/learning/decision interval and rotate pagination before reopening | HARDEN | Ephemeral browser concurrency only; no queue, event, projection, or provider authority changes. |
| Return and honor computed review state after enrichment | HARDEN | Additive member API response metadata derived from the current projection; no new truth, model decision, or automatic confirmation. |
| Preserve successful enrichment across a following decision failure | HARDEN | The current immutable enrichment event/revision remains authoritative; no failed decision is invented or retried automatically. |
| Reconcile the exact candidate after rejection or an ambiguous response | HARDEN | Current projection truth and immutable receipts decide what the browser may claim; ordinary pagination cannot substitute another matter. |
| Require exact member confirmation/dismissal event and request receipts | HARDEN | Another request, system supersession, and unrelated resolved state remain immutable but cannot settle the member's action. |
| Block approval of evidence-only/attach-existing candidates | HARDEN | Evidence remains linked and editable; only a decision disposition may create canonical household truth. |
| Project the durable pending provider-action receipt into Triage v8 | MODERNIZE | Additive deterministic read-model linkage only; immutable domain approval, OpenClaw approval, provider command, and outcome events retain separate authority. |
| Serialize Add-for-review under a stable client request ID on every surface | HARDEN | Exact member text remains one idempotent intent; ambiguous transport cannot mint a second identity or permit stale dialog callbacks. |
| Replay recorded intent features and append each recorded proposal family atomically after post-capture failure | HARDEN | The immutable extraction event fixes interpretation identity; retries never ask the language seam to decide again, and partial families cannot escape review. |
| Prevent native dialog cancellation during an Add-for-review write | HARDEN | Ephemeral input remains visible until a definite pre-write failure or authoritative recorded result is known. |
| Opt Triage v8 into reviewed version-change replay | HARDEN | Only an explicitly reviewed projection may receive deterministic event-zero replay; its candidate version is stamped only after replay succeeds, legacy rows are preserved, and no source fact is rewritten. |
| Re-read exact projected confirmation before materialization | HARDEN | Concurrent canonical truth wins the race; append success alone never authorizes duplicate domain truth or a provider action. |
| Type non-continuation banners before display | HARDEN | Presentation provenance only; lens changes cannot erase a real progress, success, or error state. |
| Relabel eligibility supersession receipts without inferring provenance | HARDEN | Copy changes; event reason and projection state are not rewritten. |
| Keep resolved deep links read-only and exact | PRESERVE | CO-015 scope checks and no-substitution behavior remain authoritative. |

No event schema, dependency/runtime version, adapter, pipeline, model,
canonical object, provider object, projection count, or external-write
authority changes. The reviewed behavior change is confined to Triage's
deterministic read model and the projection runner's opt-in version-change
replay/stamping path described below.
Triage advances from projection v7 to v8 so replay links existing immutable
`member.provider_action.confirmed` receipts to their originating approved
candidate and exact member confirmation/dismissal receipts to their requests.
Triage explicitly opts into the runner's reviewed version-change recovery.
The runner detects v7 before metadata stamping, migrates in place, replays
retained events from event zero before live delivery, and stamps v8 only after
successful replay. It backfills exact confirmation receipts even for
historical rows whose later local/provider outcome already advanced
`last_event_id`. An interrupted replay therefore leaves v7 stamped and is
retried on the next start. No other projection is opted in, and no source
event, legacy read-model row, or provider state is rewritten.

## Acceptance

AC-41 requires production-composition browser proof for confirm continuity,
typed spam-dismiss continuity, next-matter selection, candidate/matter URL
cleanup, a final-Ready-item caught-up state, explicit member navigation to Recent,
actual-change-only continuation cleanup, a forced post-write refresh failure
that keeps stale surface and shared-shell controls locked through lens clicks,
Add-for-review opening, and retained-form submission until reload,
a load-more held from before confirmation and consumed only after authoritative
refresh; an invalidated load-more followed by a no-render enrichment failure;
a dirty confirmation held before enrichment and through decision; lens, form,
intent, and pagination attempts proving all new work is handler-rejected; a
readiness-changing enrichment proving no decision request is made; and
successful enrichment followed by a failed decision, authoritative refresh,
and immediate current-revision retry; exact off-page inclusion during both
reconciliation paths; a real post-confirmation guard refusal resumed with one
request ID through local materialization; commit followed by an interrupted
response and exact-request terminal-receipt recovery, including refusal to
accept a different request's receipt; a concurrent non-decision
transition retaining exact focus in All candidates; native Escape on Today
during a post-capture Add-for-review failure, with one client request ID across
idempotent retry and one recorded interpretation; a definite pre-capture 4xx
with no retry; exact saved-item recovery from Triage and Today after a
still-ambiguous response; atomic
two-candidate recovery after proposal-governance denial;
evidence-only/attach-existing confirmation denial, including commitment
proposals; exact member-action receipt reconciliation; a canonical-truth race
that cannot materialize a duplicate; Triage-only v7-to-v8 restart replay of
historical provider-action evidence with interrupted-replay retry; native Escape during a definite failing Triage capture,
provenance-bounded lifecycle-receipt copy, and preserved destination receipts.
Existing Triage projection/API, feedback, learning-policy, replay,
privacy/isolation, release, and observation gates must remain green.
